"""Try the Budget Analyst on the mock research in tests/fixtures (development only).

    python scripts/try_budget_analyst.py                      # dry run: priced candidates and the agent's data, no LLM
    python scripts/try_budget_analyst.py amman --budget 30    # another fixture, another budget
    python scripts/try_budget_analyst.py --check              # build the real LLM and show its settings, no call
    python scripts/try_budget_analyst.py --live               # ONE real LLM call (costs money)
    python scripts/try_budget_analyst.py --inspect-last       # tool-use evidence of the last run, from CrewAI's
                                                              # local record (read-only, no call, no tracing)

--live builds a CrewAI LLM from OPENROUTER_API_KEY, OPENROUTER_BASE_URL and MODEL. That is a stand-in for this
script only: in the crew, the Coordinator injects the shared LLM into run_budget_analyst(). The values come from
the environment, or from the nearest .env file (kit/.env, then each parent folder up to the repository root),
read at runtime and never copied. Variables already set in the environment win.
CrewAI 1.15 sends "openai/gpt-4.1-mini" to OpenRouter as "gpt-4.1-mini"; the "openrouter/" prefix keeps the
full slug, so the script adds it when it is missing.
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

from planning.budget_analyst import prepare_candidates, prompt_inputs, run_budget_analyst  # noqa: E402
from planning.research import ResearchBundle  # noqa: E402


def load(name: str, budget: float | None) -> ResearchBundle:
    data = json.loads((KIT / "tests" / "fixtures" / f"{name}_research.json").read_text(encoding="utf-8"))
    trip = data["trip"] if budget is None else {**data["trip"], "budget_total": budget}
    results = data["tool_results"]
    return ResearchBundle.from_tool_results(trip, attractions=results["find_attractions"],
                                            restaurants=results["find_restaurants"], fx=results["convert_currency"])


def load_env() -> Path | None:
    """Load the nearest .env (kit first, then parents up to the git root) without overriding the environment."""
    from dotenv import load_dotenv
    for folder in [KIT, *KIT.parents]:
        if (folder / ".env").is_file():
            load_dotenv(folder / ".env", override=False)
            return folder / ".env"
        if (folder / ".git").exists():
            return None
    return None


def live_llm():
    from crewai import LLM
    source = load_env()
    key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("MODEL") or "openai/gpt-4.1-mini"
    if not key:
        sys.exit("No OPENROUTER_API_KEY in the environment or in a .env file (kit/.env or a parent folder).")
    print(f"OpenRouter key loaded from {source or 'the environment'} (value not shown)")
    model = model if model.startswith("openrouter/") else f"openrouter/{model}"
    return LLM(model=model, base_url=os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1", api_key=key,
               temperature=0)


def show_llm(llm) -> None:
    """What a real call would use, read from the LLM object: no request is made, the key is not shown."""
    client = getattr(llm, "_client", None) or getattr(llm, "client", None)
    print(f"LLM class      {type(llm).__name__}")
    print(f"provider       {getattr(llm, 'provider', None)}")
    print(f"model sent     {getattr(llm, 'model', None)}")
    print(f"endpoint       {getattr(llm, 'base_url', None) or getattr(client, 'base_url', None)}")
    print(f"key set        {bool(getattr(llm, 'api_key', None))}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", nargs="?", default="istanbul", choices=["istanbul", "amman"])
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--live", action="store_true", help="make one real (paid) LLM call")
    ap.add_argument("--check", action="store_true", help="build the real LLM and show its settings, no call")
    ap.add_argument("--inspect-last", action="store_true",
                    help="show tool-use evidence from CrewAI's local record of the last run, no call")
    args = ap.parse_args()
    if args.inspect_last:
        inspect_last()
        return
    if args.check:
        show_llm(live_llm())
        return
    bundle = load(args.fixture, args.budget)
    cands = prepare_candidates(bundle)
    trip = cands.trip
    print(f"{trip.destination}, {trip.days} days, {trip.travellers} travellers, budget {trip.budget_total} {trip.currency}\n")
    for c in cands.candidates:
        cost = c.cost.note() if c.cost.status != "unknown" else f"unknown ({c.cost.missing_reason})"
        print(f"{'*' if c.required else ' '} {c.place_id:48} {c.basis_status:10} {cost}")
    for w in cands.withheld:
        print(f"  withheld {w.place_id}: {w.reason}")
    if not args.live:
        print("\nWhat the agent would receive (research_data):\n" + prompt_inputs(cands)["research_data"])
        return
    print_report(run_budget_analyst(bundle, live_llm()))


def print_report(report) -> None:
    cur = report.currency
    print(f"\nstatus {report.status} (calculator: {report.calculator_status}) for the PLAN only")
    print(f"planned total (required + include)      {report.known_total} {cur}"
          f"{' + unknown costs' if report.unknown_cost_items else ''}")
    if report.optional:
        print(f"optional alternatives, not planned      {report.optional_known_total} {cur}"
              f"{' + unknown costs' if report.optional_unknown_items else ''}")
        print(f"ALL OPTIONS (plan + every alternative)  {report.all_options_known_total} {cur}: {report.all_options_status}")
    print(f"whole trip within budget                {report.whole_trip_within_budget} "
          f"(not priced: {', '.join(report.unaccounted_categories)})")
    def cost(item) -> str:  # 0.00 is free, not unknown
        return "unknown" if item.cost.estimated is None else str(item.cost.estimated)

    for item in report.selected:
        print(f"  {item.decision:9} {cost(item):>8} {item.name}: {item.reason}")
    for item in report.optional:
        print(f"  optional  {cost(item):>8} {item.name} (fits if added: {item.fits_if_added}): {item.reason}")
    for item in report.dropped_for_budget:
        print(f"  dropped   {cost(item):>8} {item.name}")
    print("\n".join(["", *report.warnings, *[f"(analyst) {n}" for n in report.analyst_notes]]))
    print("\ntool calls logged by check_selection:", json.dumps(report.agent_tool_calls))
    print("agent steps from CrewAI's messages:  ", json.dumps(report.agent_trace))
    print("LLM usage (CrewAI counters):         ", json.dumps(report.llm_usage))


def inspect_last() -> None:
    """Tool evidence from CrewAI's local record of the last kickoff (read-only, no API call, no tracing).

    CrewAI keeps the last task output, including the agent's messages, in
    <user data dir>/CrewAI/<CREWAI_STORAGE_DIR or the current folder name>/latest_kickoff_task_outputs.db.
    Only tool names, place ids, statuses and totals are printed: no prompt text.
    """
    import sqlite3

    import appdirs
    from crewai_core.paths import get_project_directory_name

    from planning.budget_analyst import tool_evidence
    db = Path(appdirs.user_data_dir(get_project_directory_name(), "CrewAI")) / "latest_kickoff_task_outputs.db"
    if not db.is_file():
        sys.exit(f"No CrewAI record at {db}. Run from the folder the live run used, or set CREWAI_STORAGE_DIR.")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select timestamp, output from latest_kickoff_task_outputs order by task_index").fetchall()
    finally:
        con.close()
    print(f"CrewAI record: {db}")
    for timestamp, output in rows:
        out = json.loads(output)
        decisions = (out.get("pydantic") or json.loads(out.get("raw") or "{}")).get("decisions", [])
        counts = {k: sum(d.get("decision") == k for d in decisions) for k in ("include", "optional", "exclude")}
        steps = tool_evidence(out.get("messages"))
        print(f"kickoff at {timestamp} UTC, {len(out.get('messages') or [])} messages, decisions {counts}")
        for step in steps:
            print("  ", json.dumps(step))
        print("check_selection was called" if any(s["step"] == "tool_call" for s in steps)
              else "no tool call in the stored messages (only the final attempt is stored)")


if __name__ == "__main__":
    main()
