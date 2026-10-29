"""Run the Researcher on Amman. From kit/:  python scripts/try_researcher.py   (add --llm to run the agent too)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from researcher.researcher import run_research

TRIP = {"destination": "Amman", "currency": "USD", "travellers": 1, "budget_total": 400,
        "start_date": "2026-11-02", "end_date": "2026-11-04",
        "interests": ["history", "museum"], "budget_style": "budget",
        "required_places": ["The Jordan Museum"]}

result = run_research(TRIP, use_llm="--llm" in sys.argv)
bundle = result.bundle

print(f"Tool calls: {result.tool_calls}")
print(f"\nPlaces kept: {len(bundle.places)}")
for p in bundle.places:
    hours = (p.hours or {}).get("raw") or "hours unknown"
    price = (p.price or {}).get("raw") or "price unknown"
    print(f" - [{p.category}] {p.name} | {hours} | {price}\n      {p.source_url}")

print(f"\nFX results: {len(bundle.fx_results)}")
for r in bundle.fx_results:
    print("  ", json.dumps(r, ensure_ascii=False)[:200])

print(f"\nOpening-hours lookups: {[r.get('name') for r in result.opening_hours]}")
print(f"Dropped: {[(d.name, d.reason) for d in result.dropped] or 'none'}")
print(f"Failures: {[(f.tool, f.code) for f in bundle.failures] or 'none'}")