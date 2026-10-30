"""LIVE end-to-end scenarios against a running backend: real OpenRouter calls, real MCP tools and data.

SPENDS API CREDITS. Skipped unless RIHLA_LIVE_TESTS=1. Point RIHLA_LIVE_BACKEND_URL at a running backend
(default http://127.0.0.1:8000) whose Travel API runs in live mode. Each scenario writes its metrics to
RIHLA_LIVE_REPORT_DIR (default: the system temp directory) as live_<name>.json.

    RIHLA_LIVE_TESTS=1 RIHLA_LIVE_BACKEND_URL=http://127.0.0.1:8010 python -m pytest -q tests/planning/test_live_scenarios.py

The assertions are about the traveller's result, not only HTTP 200: no place twice, no unexplained empty day,
every walk over the threshold either a measured ride or reported, unknown prices never shown as zero, and
Python's verification report present (verification_required is an acceptable, honest outcome).
"""
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RIHLA_LIVE_TESTS") != "1",
                                reason="LIVE test: set RIHLA_LIVE_TESTS=1 (spends OpenRouter credits)")

BACKEND = os.getenv("RIHLA_LIVE_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
REPORT_DIR = Path(os.getenv("RIHLA_LIVE_REPORT_DIR", tempfile.gettempdir()))
WALK_LIMIT = int(os.getenv("RIHLA_MAX_WALK_MINUTES", "30"))


def form(destination, start, end, interests, *, travellers=2, budget=1500, preferences=None):
    return {"destination": destination, "start_date": start, "end_date": end, "travellers": travellers,
            "budget": budget, "currency": "USD", "interests": interests, "preferences": preferences}


SCENARIOS = {
    "A_paris": form("Paris", "2026-11-02", "2026-11-07", ["History & Culture", "Shopping", "Art & Museums"],
                    budget=2000),
    "B_tokyo": form("Tokyo", "2026-11-10", "2026-11-13", ["Nature & Outdoors", "Relaxation"]),
    "C_barcelona": form("Barcelona", "2026-11-16", "2026-11-18", ["Food & Cuisine", "Entertainment"], budget=1200),
    "D_amman": form("Amman", "2026-11-02", "2026-11-04", ["History & Culture", "Art & Museums"], budget=600),
    "E_one_day_lisbon": form("Lisbon", "2026-12-05", "2026-12-05", ["History & Culture", "Food & Cuisine"],
                             travellers=1, budget=150),
    "F_long_rome": form("Rome", "2027-03-01", "2027-03-10", ["History & Culture", "Art & Museums", "Food & Cuisine"],
                        budget=4000),
    "G_tiny_budget_prague": form("Prague", "2026-12-10", "2026-12-12", ["History & Culture"], travellers=1,
                                 budget=20),
    "H_group_high_budget_vienna": form("Vienna", "2026-12-14", "2026-12-17", ["Art & Museums", "Entertainment"],
                                       travellers=6, budget=12000),
    "I_sparse_reykjavik": form("Reykjavík", "2026-11-20", "2026-11-22", ["Nature & Outdoors", "Adventure"]),
    "J_conflicting_required": form("Istanbul", "2026-11-02", "2026-11-04", ["Shopping", "Relaxation"],
                                   preferences="I must visit Topkapi Palace and the Grand Bazaar."),
}


def metrics(name: str, request: dict, status: int, seconds: float, body: dict) -> dict:
    it = body.get("itinerary") or {}
    summary = body.get("summary") or {}
    names = [a["name"] for d in it.get("days", []) for a in d.get("activities", [])]
    walks = [(d["date"], a["name"], a.get("walk_minutes"))
             for d in summary.get("days", []) for a in d["activities"] if (a.get("walk_minutes") or 0) > WALK_LIMIT]
    findings = (body.get("verification") or {}).get("findings", [])
    return {
        "name": name, "request": request, "http_status": status, "seconds": round(seconds, 1),
        "status": body.get("status"), "performance": body.get("performance"),
        "sightseeing_per_day": summary.get("sightseeing_per_day"), "meals_per_day": summary.get("meals_per_day"),
        "interest_coverage": summary.get("interest_coverage"), "duplicates": len(names) - len(set(names)),
        "long_legs": walks, "rides": [f for f in findings if f["code"] == "ride_planned"],
        "walk_findings": [f for f in findings if f["code"] in ("walk_over_limit", "walk_unverified")],
        "budget": body.get("budget"), "verification": {k: (body.get("verification") or {}).get(k)
                                                     for k in ("status", "state", "verified", "counts")},
        "limitations": summary.get("limitations"), "message": body.get("message"),
    }


@pytest.fixture(scope="module")
def http():
    import httpx
    with httpx.Client(timeout=600) as c:
        yield c


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_live_scenario(http, name):
    request = SCENARIOS[name]
    message = (f"Plan a trip to {request['destination']} from {request['start_date']} to {request['end_date']} for "
               f"{request['travellers']} travellers with a budget of {request['budget']} USD.")
    started = time.perf_counter()
    r = http.post(f"{BACKEND}/api/chat", json={"session_id": f"live-{name}-{uuid.uuid4().hex[:6]}",
                                               "message": message, "trip": request})
    body = r.json()
    m = metrics(name, request, r.status_code, time.perf_counter() - started, body)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"live_{name}.json").write_text(json.dumps({**m, "response": body}, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
    assert r.status_code == 200, body
    if body.get("itinerary") is None:  # an honest refusal (e.g. too little research) must say why
        assert body.get("message") and body["status"] in ("needs_revision", "needs_clarification")
        return
    assert m["duplicates"] == 0
    assert body["verification"] is not None and body["verification"]["state"] in (
        "confirmed_valid", "supported_by_records", "unverifiable", "confirmed_problem")
    assert not (body["verification"]["verified"] and body["verification"]["status"] != "PASS")
    days = m["sightseeing_per_day"] or []
    assert not (0 in days and max(days) >= 3), days  # an empty day while another is full is not acceptable
    for day in (body.get("summary") or {}).get("days", []):  # a long walk is a ride, or it is reported
        for a in day["activities"]:
            if (a.get("walk_minutes") or 0) > WALK_LIMIT:
                assert any(f["place_id"] for f in m["walk_findings"]) or any(
                    "walk" in line for line in m["limitations"] or []), (day["date"], a["name"])
    for a in [a for d in body["itinerary"]["days"] for a in d["activities"]]:
        if "Cost unknown" in (a.get("notes") or ""):
            assert a.get("estimated_cost") is None  # unknown is never shown as zero
    assert body["budget"]["unknown_price_items"] is not None and "accommodation" in body["budget"]["not_included"]
