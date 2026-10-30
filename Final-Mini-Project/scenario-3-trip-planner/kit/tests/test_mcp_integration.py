"""The real MCP connection: our client starts the kit's services.travel_mcp over stdio, which calls the kit's
travel API. Both are unmodified kit code; the API runs here in a subprocess, in replay mode (offline, from the
recorded cache), on a free port with its own key, so it never touches a running instance or the internet.

Nothing calls an LLM: /api/chat's pipeline is replaced where a test needs a chat turn. Skipped (with the reason)
when the travel API cannot be started, e.g. without uvicorn. Run alone:  python -m pytest -q tests/test_mcp_integration.py
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import researcher.mcp_client as mcp_client
from app.main import app
from app.memory import sessions
from researcher.mcp_client import ToolNotAllowed, call_read_tool, recording, reset_shared_session

KIT = Path(__file__).resolve().parents[1]
KEY = "integration-test-key"
ADMIN = {"Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def travel_api():
    port = free_port()
    env = {**os.environ, "TRAVEL_MODE": "replay", "TRAVEL_API_KEY": KEY, "TRAVEL_OVERLAYS": "", "TRAVEL_TODAY": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "services.travel_api:app", "--port", str(port),
                             "--log-level", "warning"], cwd=str(KIT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.skip("the kit's travel API could not be started for the MCP integration tests")
    saved = {k: os.environ.get(k) for k in ("TRAVEL_API_URL", "TRAVEL_API_KEY", "MCP_SHARED_SESSION")}
    os.environ.update(TRAVEL_API_URL=url, TRAVEL_API_KEY=KEY, MCP_SHARED_SESSION="1")
    reset_shared_session()  # the next call starts an MCP server that talks to this API
    yield url
    reset_shared_session()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    proc.kill()
    proc.wait(timeout=10)


@pytest.fixture
def api(travel_api):
    httpx.post(f"{travel_api}/admin/reset", headers=ADMIN, timeout=10).raise_for_status()  # test-only endpoint
    sessions.clear()
    yield travel_api
    httpx.delete(f"{travel_api}/admin/faults", timeout=10)
    sessions.clear()


def admin(api, path):
    return httpx.get(f"{api}/admin/{path}", timeout=10).json()


# ------------------------------------------------------------------ the connection
def test_the_stdio_server_starts_and_lists_the_kit_tools(api):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async def names():
        async with stdio_client(mcp_client._server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return {t.name for t in (await session.list_tools()).tools}

    assert asyncio.run(names()) == {"geocode", "find_attractions", "find_restaurants", "get_opening_hours",
                                    "get_weather", "convert_currency", "travel_time", "save_trip", "list_trips",
                                    "get_preferences", "save_preferences"}


def test_a_read_tool_answers_through_mcp_and_is_recorded(api):
    with recording() as calls:
        result = call_read_tool("get_opening_hours", {"place_name": "Topkapi Palace", "city": "Istanbul"})
    assert result["ok"] is True and result["mode"] == "replay"
    assert "Tue" in str(result["hours"].get("closed_days"))  # the kit README's fact: closed on Tuesdays
    assert [(c["tool"], c["ok"], c["via"]) for c in calls] == [("get_opening_hours", True, "mcp")]
    assert any(r["path"].startswith("/api/hours") for r in admin(api, "log")["log"])  # it reached the API


def test_an_error_from_the_api_comes_back_as_a_failed_call(api):
    httpx.post(f"{api}/admin/faults", json={"target": "attractions", "mode": "error_503", "count": 1}, timeout=10)
    with recording() as calls:
        result = call_read_tool("find_attractions", {"city": "Istanbul"})
    assert result["ok"] is False and result["error"]["status"] == 503
    assert calls[0]["ok"] is False and calls[0]["error"]


def test_a_server_that_cannot_start_is_an_error_not_an_answer(api, monkeypatch):
    real = mcp_client._server_params

    def broken():
        params = real()
        params.args = ["-m", "services.no_such_mcp_server"]
        return params

    monkeypatch.setattr(mcp_client, "_server_params", broken)
    monkeypatch.setattr(mcp_client, "CALL_TIMEOUT", 15)
    reset_shared_session()
    try:
        with recording() as calls, pytest.raises(Exception):
            call_read_tool("geocode", {"place": "Amman"})
        assert calls[0]["ok"] is False and calls[0]["error"].startswith("mcp_unavailable")
    finally:
        monkeypatch.setattr(mcp_client, "_server_params", real)
        reset_shared_session()


def test_the_researcher_cannot_write_even_with_a_live_server(api):
    for tool in ("save_trip", "save_preferences"):
        with pytest.raises(ToolNotAllowed):
            call_read_tool(tool, {"traveller_id": "TRV-101", "title": "x", "itinerary": {}, "idempotency_key": "k",
                                  "preferences": {"dietary": ["vegan"]}})
    assert admin(api, "trips")["trips"] == []
    assert admin(api, "travellers")["travellers"][0]["preferences"]["dietary"] == ["vegetarian"]


# ------------------------------------------------------------------ approval through MCP, end to end
ITINERARY = {"destination": "Istanbul", "currency": "USD", "travellers": 1, "budget_total": 300, "estimated_total": 28,
             "days": [{"date": "2026-11-05", "activities": [
                 {"name": "Hagia Sophia", "start_time": "09:00", "end_time": "10:30", "estimated_cost": 28,
                  "source_url": "https://en.wikivoyage.org/wiki/Istanbul/Historical_Peninsula#See"}]}]}


def verified_result():
    final = SimpleNamespace(status="PASS", verified=True, evidence_basis="fresh")
    return {"status": "verified", "message": "Passed.", "itinerary": ITINERARY,
            "coverage": {"sufficient": True, "covered_dates": ["2026-11-05"], "meal_only_dates": [], "empty_dates": []},
            "verification_result": {"verified": True, "verifier_report": SimpleNamespace(verification=final)}}


def plan(client, session_id, traveller="TRV-200"):
    with patch("app.main.orchestrate_trip", return_value=verified_result()):
        data = client.post("/api/chat", json={"session_id": session_id, "message": "Plan Istanbul",
                                              "user": {"traveller_id": traveller}}).json()
    return data["pending_action"]


def approve(client, session_id, action_id, approved=True):
    return client.post("/api/approve", json={"session_id": session_id, "action_id": action_id,
                                             "approved": approved}).json()


def test_approval_saves_through_mcp_once_with_the_store_trip_id(api):
    client = TestClient(app)
    action = plan(client, "e2e")
    first = approve(client, "e2e", action["action_id"])
    trips = admin(api, "trips")["trips"]
    assert first["result"] == {"trip_id": trips[0]["trip_id"]} and len(trips) == 1
    assert trips[0]["traveller_id"] == "TRV-200" and trips[0]["title"] == "Istanbul, 1 day"
    assert trips[0]["start_date"] == "2026-11-05" and trips[0]["places"] == "Hagia Sophia"
    assert ("save_trip", True, "mcp") in [(c["tool"], c["ok"], c["via"]) for c in first["tool_calls"]]
    assert any(r["path"] == "/api/trips" for r in admin(api, "log")["log"])  # the MCP server made the write
    second = approve(client, "e2e", action["action_id"])  # the double click
    assert second["result"] == first["result"] and len(admin(api, "trips")["trips"]) == 1


def test_the_idempotency_key_protects_a_retry_that_reaches_the_store_twice(api):
    from app.trip_store import save_trip_to_store
    one = save_trip_to_store(ITINERARY, "TRV-200", "act-retry", title="Istanbul, 1 day")
    two = save_trip_to_store(ITINERARY, "TRV-200", "act-retry", title="Istanbul, 1 day")
    assert one["trip_id"] == two["trip_id"] and len(admin(api, "trips")["trips"]) == 1


def test_rejection_saves_nothing(api):
    client = TestClient(app)
    action = plan(client, "reject")
    data = approve(client, "reject", action["action_id"], approved=False)
    assert data["status"] == "rejected" and data["result"] is None and admin(api, "trips")["trips"] == []


def test_a_store_failure_is_reported_and_the_retry_succeeds(api):
    client = TestClient(app)
    action = plan(client, "fault")
    httpx.post(f"{api}/admin/faults", json={"target": "save_trip", "mode": "error_503", "count": 1}, timeout=10)
    failed = approve(client, "fault", action["action_id"])
    assert failed["status"] == "save_failed" and failed["result"] is None and admin(api, "trips")["trips"] == []
    retry = approve(client, "fault", action["action_id"])
    assert retry["status"] == "approved" and len(admin(api, "trips")["trips"]) == 1


# ------------------------------------------------------------------ memory through MCP, end to end
def chat_as(client, traveller, message, session_id):
    seen = {}

    def fake(message, session_id=None, trip_form=None, preferences=None):
        seen["preferences"] = preferences
        return {"status": "needs_clarification", "message": "When do you travel?"}

    with patch("app.main.orchestrate_trip", side_effect=fake):
        data = client.post("/api/chat", json={"session_id": session_id, "message": message,
                                              "user": {"traveller_id": traveller}}).json()
    return data, seen["preferences"]


def test_saved_preferences_are_read_from_the_store(api):
    data, prefs = chat_as(TestClient(app), "TRV-101", "Plan one day in Istanbul", "mem-1")
    assert prefs["dietary"] == ["vegetarian"] and prefs["max_walk_minutes"] == 25
    assert ("get_preferences", True) in [(c["tool"], c["ok"]) for c in data["tool_calls"]]


def test_a_lasting_preference_is_stored_merged_and_scoped_to_its_traveller(api):
    client = TestClient(app)
    chat_as(client, "TRV-102", "Remember that I'm vegan, please.", "mem-2")
    travellers = {t["traveller_id"]: t["preferences"] for t in admin(api, "travellers")["travellers"]}
    assert travellers["TRV-102"]["dietary"] == ["halal", "nut allergy", "vegan"]  # merged, nothing erased
    assert travellers["TRV-102"]["max_walk_minutes"] == 15  # untouched field kept
    assert travellers["TRV-101"]["dietary"] == ["vegetarian"]  # another traveller untouched
    _, prefs = chat_as(client, "TRV-102", "Plan Amman", "mem-3")  # a new session reads it back
    assert "vegan" in prefs["dietary"]


def test_an_unknown_traveller_has_no_memory_until_they_ask_to_be_remembered(api):
    client = TestClient(app)
    _, prefs = chat_as(client, "TRV-900", "Plan Amman", "mem-4")
    assert prefs is None
    chat_as(client, "TRV-900", "From now on remember I am a vegetarian.", "mem-4")
    _, prefs = chat_as(client, "TRV-900", "Plan Amman", "mem-5")
    assert prefs == {"dietary": ["vegetarian"]}
