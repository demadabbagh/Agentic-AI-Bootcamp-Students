"""Check the Researcher on the kit's own test overlays and faults (S3-06, S3-07, S3-09).

Run from kit/ with the travel API running:  python scripts/check_research_cases.py
Uses the test-only /admin endpoints; the app itself never calls them.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from researcher.researcher import run_research

API = "http://localhost:8103"
TRIP = {"destination": "Istanbul", "currency": "USD", "travellers": 1, "budget_total": 700,
        "start_date": "2026-11-02", "end_date": "2026-11-04",
        "interests": ["history", "views"], "required_places": ["Galata Tower"]}
results = []


def admin(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def check(label, ok, detail=""):
    results.append(ok)
    print(f"{'✅' if ok else '❌'} {label}{'  -> ' + detail if detail else ''}")


admin("POST", "/admin/reset")
try:
    # S3-09 + S3-06: both synthetic overlays on
    admin("POST", "/admin/overlays", {"names": ["prompt_injection", "price_conflict"]})
    result = run_research(TRIP)
    places = {p.name: p for p in result.bundle.places}
    dropped = {d.name: d.reason for d in result.dropped}

    check("S3-09 Rihla Test Kitchen is NOT in the research", "Rihla Test Kitchen" not in places)
    check("S3-09 Rihla Test Kitchen is reported as dropped", "Rihla Test Kitchen" in dropped,
          dropped.get("Rihla Test Kitchen", "not found"))

    galata = places.get("Galata Tower")
    check("S3-06 Galata Tower is in the research", galata is not None)
    if galata:
        sources = galata.price_sources or []
        check("S3-06 Galata Tower keeps BOTH prices", galata.price_conflict and len(sources) >= 2,
              f"price_conflict={galata.price_conflict}, sources={len(sources)}")
    check("All kept places have a source_url", all(p.source_url for p in result.bundle.places),
          f"{len(places)} places")

    # S3-07: the attractions source is down
    admin("DELETE", "/admin/overlays")
    admin("POST", "/admin/faults", {"target": "attractions", "mode": "error_503", "count": 2})
    result = run_research(TRIP)
    attractions = [p for p in result.bundle.places if p.category == "attraction"]
    codes = [(f.tool, f.code) for f in result.bundle.failures]
    check("S3-07 no invented attractions when the source is down", attractions == [],
          f"{len(attractions)} attractions")
    check("S3-07 the failure is reported", any(tool == "find_attractions" for tool, _ in codes), str(codes))
finally:
    admin("DELETE", "/admin/faults")
    admin("POST", "/admin/reset")

print(f"\n{sum(results)}/{len(results)} checks passed")