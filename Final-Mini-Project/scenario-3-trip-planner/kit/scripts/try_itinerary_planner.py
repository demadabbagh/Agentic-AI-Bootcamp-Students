"""Try the Itinerary Planner on the mock research in tests/fixtures (development only).

    python scripts/try_itinerary_planner.py                   # dry run: selected places, hours per date and the
                                                              # agent's input; no LLM call
    python scripts/try_itinerary_planner.py --check           # build the real LLM and show its settings, no call
    python scripts/try_itinerary_planner.py --live            # ONE real Planner run (costs money)
    python scripts/try_itinerary_planner.py --live --live-budget   # the real Budget Analyst first (costs more)

By default the Budget Analyst's decision is a fixed one (below), applied in Python, so --live pays for the
Planner only. Travel times replay the recorded travel_time results (tests/fixtures/<city>_routes.json): real
OpenStreetMap routing data, recorded offline. In the crew, the Coordinator injects the shared LLM and a lookup
that wraps the travel_time MCP tool instead. The LLM settings come from .env, as in try_budget_analyst.py.
"""
import argparse
import json
import os
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from planning.budget_analyst import (BudgetDecision, PlaceDecision, apply_decision, prepare_candidates,  # noqa: E402
                                     run_budget_analyst)
from planning.itinerary_planner import (ItineraryPlannerError, PlanningContext, plan_itinerary,  # noqa: E402
                                        prompt_inputs)
from planning.schedule import hours_on  # noqa: E402
from try_budget_analyst import live_llm, load, show_llm  # noqa: E402

# A fixed Budget Analyst decision per fixture: place id slug -> (decision, rank). Everything else is excluded.
FIXED = {"istanbul": {"hagia-sophia": ("include", 1), "basilica-cistern": ("include", 2),
                      "suleymaniye-mosque": ("include", 3), "dolmabahce-palace": ("include", 4),
                      "pera-museum": ("include", 5), "ali-usta-cig-kofte": ("include", 6),
                      "grand-bazaar": ("include", 7), "galata-tower": ("optional", 1)},
         "amman": {"amman-citadel": ("include", 1), "roman-theater": ("include", 2),
                   "the-jordan-museum": ("include", 3), "cave-of-the-seven-sleepers": ("optional", 1)}}


def fixed_report(name: str, bundle):
    cands = prepare_candidates(bundle)
    picks = {f"wv-{name}-{slug}": choice for slug, choice in FIXED[name].items()}
    return apply_decision(cands, BudgetDecision(decisions=[
        PlaceDecision(place_id=i, decision=picks.get(i, ("exclude", None))[0], rank=picks.get(i, ("exclude", None))[1],
                      reason="Fixed decision for the planner try-out") for i in cands.open_ids]))


def recorded_lookup(name: str):
    results = json.loads((KIT / "tests" / "fixtures" / f"{name}_routes.json").read_text(encoding="utf-8"))["results"]
    return lambda a, b: results.get(f"{a}|{b}") or {"ok": False, "error": {"code": "not_recorded",
                                                                          "message": "not in the recorded routes"}}


def print_report(report) -> None:
    print(f"\nstatus {report.status} (verified: {report.verified}); uncertain: {report.uncertain}")
    if report.itinerary:
        for day in report.itinerary.days:
            print(f"\n{day.date} ({day.weekday})")
            for a in day.activities:
                travel = "" if a.travel_minutes_from_previous is None else f" [{a.travel_minutes_from_previous} min walk]"
                cost = "unknown" if a.estimated_cost is None else a.estimated_cost
                print(f"  {a.start_time}-{a.end_time}{travel} {a.name} ({cost} {report.currency})")
    for u in report.unscheduled:
        print(f"  NOT PLANNED ({u.decided_by}{', required' if u.required else ''}) {u.name}: {u.reason}")
    if report.budget:
        print(f"\nbudget for the scheduled places: {report.budget.known_total} {report.currency}, {report.budget.status}")
    print("\n".join(["", *report.warnings, *[f"(planner) {n}" for n in report.planner_notes]]))
    if report.notes_suppressed:
        print(f"({report.notes_suppressed} planner note(s) not shown: stale after a repair or contradicting the plan)")
    print("\nplan (from the final schedule):\n  " + "\n  ".join(report.plan_summary))
    print("quality:             ", json.dumps([(q.code, str(q.date) if q.date else None, q.avoidable)
                                               for q in report.quality]))
    print("repairs:             ", len(report.repairs))
    print("\ncheck_day calls:     ", json.dumps(report.agent_tool_calls))
    print("travel_time lookups: ", len(report.travel_lookups))
    print("agent steps:         ", json.dumps(report.agent_trace))
    print("LLM usage:           ", json.dumps(report.llm_usage))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", nargs="?", default="istanbul", choices=["istanbul", "amman"])
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--max-walk", type=int, default=None, help="the traveller's walking limit in minutes")
    ap.add_argument("--check", action="store_true", help="build the real LLM and show its settings, no call")
    ap.add_argument("--live", action="store_true", help="one real (paid) Planner run")
    ap.add_argument("--live-budget", action="store_true", help="with --live: run the real Budget Analyst first")
    args = ap.parse_args()
    if args.check:
        show_llm(live_llm())
        return
    bundle = load(args.fixture, args.budget)
    if args.max_walk:
        bundle = bundle.model_copy(update={"trip": bundle.trip.model_copy(update={"max_walk_minutes": args.max_walk})})
    llm = live_llm() if args.live else None
    report = run_budget_analyst(bundle, llm) if args.live and args.live_budget else fixed_report(args.fixture, bundle)
    ctx = PlanningContext(report, bundle, recorded_lookup(args.fixture))
    print(f"{bundle.trip.destination}: {', '.join(f'{d} {d:%a}' for d in ctx.dates)}; planning day "
          f"{ctx.rules.day_start}-{ctx.rules.day_end}; walking limit {ctx.rules.max_walk_minutes}")
    for pid, stop in ctx.stops.items():
        hours = "; ".join(f"{d:%a} {hours_on(stop.hours, d).status}" for d in ctx.dates)
        print(f"{'*' if stop.required else ' '} {pid:42} {ctx.rules.duration_for(stop):>4} min  {hours}")
    if not args.live:
        print("\nWhat the agent would receive (research_data):\n" + prompt_inputs(ctx)["research_data"])
        return
    try:
        print_report(plan_itinerary(report, bundle, llm, recorded_lookup(args.fixture)))
    except ItineraryPlannerError as exc:  # CrewAI keeps no record of a failed task: print what we kept
        print(f"\nFAILED: {exc}")
        print("check_day calls:      ", json.dumps(exc.tool_calls))
        print("travel_time lookups:  ", len(exc.travel_lookups))
        print("guardrail rejections: ", json.dumps(exc.rejections))
        sys.exit(1)


if __name__ == "__main__":
    main()
