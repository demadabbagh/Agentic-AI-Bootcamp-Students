"""Try the Verifier on the mock research in tests/fixtures (development only).

    python scripts/try_verifier.py                       # dry run: Python's VerificationReport per case, no LLM
    python scripts/try_verifier.py --check               # build the real LLM and show its settings, no call
    python scripts/try_verifier.py --replay              # the whole Verifier flow offline: the real CrewAI agent,
                                                         # task, tool and guardrail with a scripted LLM, every
                                                         # integration check, and network access blocked
    python scripts/try_verifier.py --live --case closed  # ONE real Verifier run on OpenRouter (costs money)

Cases (Istanbul fixture, Mon 2026-11-02 to Wed 2026-11-04, 2 travellers, 700 USD, Topkapı Palace required):
    unknowns  the Planner's plan: no confirmed error; one walk never recorded and the Grand Bazaar has no price
    closed    deliberately invalid: Topkapı Palace moved to Tuesday, a day its listing says it is closed
    clean     a valid plan: every price known, every hour confirmed, every walk measured in the recorded data

Every case is built in Python: a fixed Budget Analyst decision, the Planner's build_itinerary() on a fixed day
assignment, then verify_itinerary(). The evidence is RECORDED, never fresh: listing hours from the research
fixture and travel times replayed from tests/fixtures/istanbul_routes.json. No MCP request is made in any mode,
so a report can reach PASS but never verified=True. In --live only the Verifier agent's LLM calls are real (and
none when the report has no errors and no unknowns). The LLM settings come from .env, as in try_budget_analyst.py.
"""
import argparse
import datetime as dt
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
if "--replay" in sys.argv:  # scripted runs stay out of the local record that --inspect-last reads after a live run
    os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

from planning.budget_analyst import BudgetDecision, PlaceDecision, apply_decision, prepare_candidates  # noqa: E402
from planning.itinerary_planner import DayAssignment, DayChoice, PlanningContext, build_itinerary  # noqa: E402
from planning.models import Itinerary  # noqa: E402
from planning.verification import verify_itinerary  # noqa: E402
from planning.verifier_agent import (GUARDRAIL_RETRIES, MAX_ITER, PrioritizedFinding, VerifierAgentError,  # noqa: E402
                                     VerifierAgentReport, VerifierContext, VerifierDecision, explain_verification)
from try_budget_analyst import live_llm, load, show_llm  # noqa: E402
from try_itinerary_planner import FIXED  # noqa: E402

TIME_LIMIT_S = 150  # the project's evaluation limit for a whole request


def I(slug):  # noqa: E743 - a place id of the Istanbul fixture
    return f"wv-istanbul-{slug}"


MON, TUE, WED = dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)
CLEAN_PICKS = {"hagia-sophia": ("include", 1), "basilica-cistern": ("include", 2), "suleymaniye-mosque": ("include", 3),
               "dolmabahce-palace": ("include", 4), "ali-usta-cig-kofte": ("include", 5), "galata-tower": ("optional", 1)}
CLEAN_DAYS = {MON: ["hagia-sophia", "topkapi-palace", "basilica-cistern", "ali-usta-cig-kofte"],
              TUE: ["suleymaniye-mosque"], WED: ["dolmabahce-palace"]}
PLANNER_DAYS = {MON: ["hagia-sophia", "topkapi-palace", "basilica-cistern", "ali-usta-cig-kofte"],
                TUE: ["suleymaniye-mosque", "grand-bazaar"], WED: ["dolmabahce-palace", "pera-museum"]}
CASES = {
    "unknowns": ("The Planner's Istanbul plan: no confirmed error expected; the Dolmabahçe -> Pera walk was never "
                 "recorded and the Grand Bazaar has no price", FIXED["istanbul"], PLANNER_DAYS, None),
    "closed": ("Deliberately invalid: Topkapı Palace moved to Tuesday 11:00-12:30, a day its listing says it is "
               "closed", CLEAN_PICKS, CLEAN_DAYS, ("topkapi-palace", TUE, "11:00", "12:30")),
    "clean": ("A valid plan: every price known, every hour confirmed and every walk measured (recorded data)",
              CLEAN_PICKS, CLEAN_DAYS, None),
}


# ------------------------------------------------------------------ the recorded inputs
def recorded_travel(log: list):
    """The recorded travel_time results (tests/fixtures/istanbul_routes.json), every question logged."""
    results = json.loads((KIT / "tests" / "fixtures" / "istanbul_routes.json").read_text(encoding="utf-8"))["results"]

    def lookup(a: str, b: str) -> dict:
        log.append((a, b))
        return json.loads(json.dumps(results.get(f"{a}|{b}") or
                                     {"ok": False, "error": {"code": "not_recorded", "message": "not recorded"}}))
    return lookup


def build_case(name: str):
    """(bundle, Budget Analyst report, Planner report, the itinerary to verify, description), all in Python."""
    desc, picks, days, moved = CASES[name]
    bundle = load("istanbul", None)
    cands = prepare_candidates(bundle)
    choice = {I(slug): c for slug, c in picks.items()}
    report = apply_decision(cands, BudgetDecision(decisions=[
        PlaceDecision(place_id=i, decision=choice.get(i, ("exclude", None))[0], rank=choice.get(i, ("exclude", None))[1],
                      reason="Fixed decision for the verifier try-out") for i in cands.open_ids]))
    ctx = PlanningContext(report, bundle, recorded_travel([]))
    planner = build_itinerary(ctx, DayAssignment(days=[DayChoice(date=d, place_ids=[I(s) for s in ids])
                                                       for d, ids in days.items()]))
    itinerary = planner.itinerary
    if moved:  # break the plan after the Planner: the Verifier must find it on its own
        slug, day, start, end = moved
        data = itinerary.model_dump()
        act = next(a for d in data["days"] for a in d["activities"] if a["place_id"] == I(slug))
        for d in data["days"]:
            d["activities"] = [a for a in d["activities"] if a["place_id"] != I(slug)]
        act.update(start_time=start, end_time=end, travel_minutes_from_previous=None)
        target = next(d for d in data["days"] if d["date"] == day)
        target["activities"] = sorted(target["activities"] + [act], key=lambda a: a["start_time"])
        itinerary = Itinerary.model_validate(data)
    return bundle, report, planner, itinerary, desc


def verification(name: str, travel_log: list | None = None):
    bundle, report, planner, itinerary, desc = build_case(name)
    travel_log = [] if travel_log is None else travel_log
    verified = verify_itinerary(itinerary, bundle, budget_report=report, planner_report=planner,
                                travel_lookup=recorded_travel(travel_log))
    return verified, desc, (bundle, report, planner, itinerary)


# ------------------------------------------------------------------ the scripted LLM (replay only)
def scripted_decision(report) -> VerifierDecision:
    """What a correct agent answer looks like: Python's order (errors, then unknowns) and Python's actions. It is
    written by this script, not by a model: --replay tests the plumbing, never the model's judgement."""
    ctx = VerifierContext(report)
    words = {"confirmed_error": "Python found this problem in the plan; it needs the recommended change.",
             "missing_evidence": "This fact is unknown; the recommended lookup would settle it."}
    return VerifierDecision(summary="Fix the confirmed errors first, then settle the missing evidence.", findings=[
        PrioritizedFinding(finding_id=f.id, code=f.issue.code, kind=f.kind, date=f.issue.date,
                           place_id=f.issue.place_id, rank=n, recommended_action=f.issue.recommended_action,
                           explanation=words[f.kind])
        for n, f in enumerate([f for f in ctx.findings.values() if f.kind != "warning"], 1)])


def scripted_llm(*replies):
    sys.path.insert(0, str(KIT / "tests" / "planning"))
    from scripted_llm import NativeScriptedLLM  # the shared test double: no network, no cost
    return NativeScriptedLLM(model="scripted-replay", replies=list(replies))


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@contextmanager
def no_network(attempts: list):
    """Block every outgoing connection and record what tried: replay must not reach OpenRouter or the MCP service
    (which itself listens on localhost). The one exception is a loopback connection to a socket this process is
    listening on: asyncio's own self-pipe on Windows (socket.socketpair), which never leaves the process."""
    real_connect, real_listen, real_create = socket.socket.connect, socket.socket.listen, socket.create_connection
    own_ports: set[int] = set()

    def listen(self, *args):
        real_listen(self, *args)
        own_ports.add(self.getsockname()[1])

    def connect(self, address):
        host, port = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        if host in LOOPBACK and port in own_ports:
            return real_connect(self, address)
        attempts.append(f"{host}:{port}")
        raise ConnectionRefusedError("network blocked during --replay")

    def create_connection(address, *args, **kwargs):
        attempts.append(f"{address[0]}:{address[1]}")
        raise ConnectionRefusedError("network blocked during --replay")
    socket.socket.connect, socket.socket.listen, socket.create_connection = connect, listen, create_connection
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.listen, socket.create_connection = real_connect, real_listen, real_create


# ------------------------------------------------------------------ output
def metrics(name: str, mode: str, out: VerifierAgentReport, seconds: float, travel_log: list) -> dict:
    v = out.verification
    return {"case": name, "mode": mode, "evidence": "recorded fixtures (not fresh)", "duration_s": round(seconds, 2),
            "within_time_limit": seconds <= TIME_LIMIT_S, "status": out.status, "verified": out.verified,
            "evidence_basis": v.evidence_basis, "decided_by": out.decided_by,
            "counts": {"errors": len(v.issues), "unknowns": len(v.unknowns), "warnings": len(v.warnings)},
            "llm_usage": out.llm_usage, "agent_tool_calls": len(out.agent_tool_calls),
            "inspect_sections": [c.get("section") for c in out.agent_tool_calls],
            "recorded_travel_lookups": len(travel_log), "guardrail_retries": len(out.guardrail_rejections),
            "guardrail_rejections": out.guardrail_rejections,
            "ranked": [[p.rank, p.finding_id, p.code, p.kind, p.recommended_action] for p in out.prioritized],
            "revision_targets": [[t.action, t.kind, t.place_id, str(t.date) if t.date else None, t.codes]
                                 for t in out.revision_targets]}


def print_verification(v, desc: str) -> None:
    print(f"\n{desc}")
    print(f"Python: {v.status}, verified={v.verified}, evidence {v.evidence_basis} "
          f"({len(v.issues)} errors, {len(v.unknowns)} unknowns, {len(v.warnings)} warnings)")
    for c in v.checks:
        print(f"  {c.status:7} {c.check:16} {c.summary}")
    for i in v.issues + v.unknowns:
        print(f"  {i.severity:7} {i.code:26} {str(i.date or ''):10} {i.place_id or '':40} -> {i.recommended_action}")


def print_agent(out: VerifierAgentReport, m: dict) -> None:
    print(f"\n{out.headline}  [decided by {out.decided_by}]")
    for p in out.prioritized:
        print(f"  {p.rank:>2}. {p.finding_id:4} {p.kind:17} {p.code:26} -> {p.recommended_action}: {p.explanation}")
    print(f"  summary: {out.summary}")
    print("metrics: " + json.dumps(m, ensure_ascii=False))


# ------------------------------------------------------------------ the integration checks (replay)
def without_timings(report) -> dict:
    """A VerificationReport as data, without the lookup timings (ms): the only part that may differ between runs."""
    data = report.model_dump(mode="json")
    data["tool_evidence"] = [{k: v for k, v in c.items() if k != "ms"} for c in data["tool_evidence"]]
    return data


def integration_checks(name: str, out: VerifierAgentReport, travel_log: list, net: list) -> list[tuple[str, bool]]:
    fresh, _, _ = verification(name)  # the deterministic report, computed again on its own
    ctx = VerifierContext(out.verification)
    allowed = {f.id: f.allowed_actions for f in ctx.findings.values()}
    checks = [
        ("verify_itinerary() report is deterministic and is the one the agent received",
         without_timings(out.verification) == without_timings(fresh)),
        ("status and verified are Python's", (out.status, out.verified) == (fresh.status, fresh.verified)),
        ("replay evidence is never verified=True", out.verified is False),
        ("revision targets are Python's, unchanged", out.revision_targets == fresh.revision_targets),
        ("every ranked action fits its finding's revision target",
         all(p.recommended_action in allowed[p.finding_id] for p in out.prioritized)),
        ("every confirmed error and unknown is ranked", {p.finding_id for p in out.prioritized} >= set(ctx.must_rank)),
        ("the Coordinator can round-trip the report as JSON",
         VerifierAgentReport.model_validate_json(out.model_dump_json()) == out),
        ("only recorded travel results were used", all(c.get("freshness") in (None, "recorded")
                                                        for c in out.verification.tool_evidence)),
        ("no network connection was attempted", not net),
    ]
    if out.decided_by == "agent":
        checks.append(("inspect_verification was called and logged",
                       [c["section"] for c in out.agent_tool_calls] == ["overview"]))
    else:
        checks.append(("no LLM call when nothing needs ranking", out.llm_usage == {} and not out.agent_tool_calls))
    return checks


def replay(names: list[str]) -> bool:
    ok = True
    for name in names:
        travel_log, net = [], []
        with no_network(net):
            v, desc, _ = verification(name, travel_log)
            print_verification(v, desc)
            llm = scripted_llm()  # no replies: a call when nothing needs ranking would fail the run
            if VerifierContext(v).needs_agent:
                from scripted_llm import native_tool_call
                llm = scripted_llm(native_tool_call("inspect_verification", {"section": "overview"}),
                                   scripted_decision(v).model_dump_json())
            started = time.perf_counter()
            out = explain_verification(v, llm)
            seconds = time.perf_counter() - started
        print_agent(out, metrics(name, "replay (scripted LLM)", out, seconds, travel_log))
        for label, passed in integration_checks(name, out, travel_log, net):
            ok &= passed
            print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    # An LLM that never gives a valid answer: the error carries Python's report, still usable.
    net = []
    with no_network(net):
        v, _, _ = verification("closed")
        try:
            explain_verification(v, scripted_llm(*["Everything looks fine to me."] * 20))
            failed = None
        except VerifierAgentError as exc:
            failed = exc
    print("\nLLM failure case (scripted invalid answers):")
    for label, passed in [("VerifierAgentError raised", failed is not None),
                          ("its .verification is Python's report", failed is not None and failed.verification == v),
                          ("guardrail rejections recorded", failed is not None and bool(failed.rejections)),
                          ("no network connection was attempted", not net)]:
        ok &= passed
        print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    print(f"\n{'all integration checks passed' if ok else 'SOME INTEGRATION CHECKS FAILED'} "
          f"(bounds: max_iter {MAX_ITER}, guardrail retries {GUARDRAIL_RETRIES})")
    return ok


def live(name: str, metrics_out: Path | None) -> None:
    llm = live_llm()
    travel_log: list = []
    v, desc, _ = verification(name, travel_log)
    print_verification(v, desc)
    print("\nVerification evidence: recorded fixtures (not fresh). LLM: live OpenRouter (paid).")
    started = time.perf_counter()
    try:
        out = explain_verification(v, llm)
    except VerifierAgentError as exc:  # Python's report is still valid: print what we kept
        seconds = time.perf_counter() - started
        print(f"\nFAILED after {seconds:.1f} s: {exc}")
        print(f"Python's verdict still stands: {exc.verification.status}, verified={exc.verification.verified}")
        print("inspect_verification calls: ", json.dumps(exc.tool_calls))
        print("guardrail rejections:       ", json.dumps(exc.rejections))
        sys.exit(1)
    seconds = time.perf_counter() - started
    m = metrics(name, "live", out, seconds, travel_log)
    print_agent(out, m)
    if seconds > TIME_LIMIT_S:
        print(f"WARNING: {seconds:.0f} s is over the {TIME_LIMIT_S} s evaluation limit")
    if metrics_out:
        metrics_out.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"metrics written to {metrics_out}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=[*CASES, "all"], default="all")
    ap.add_argument("--check", action="store_true", help="build the real LLM and show its settings, no call")
    ap.add_argument("--replay", action="store_true", help="the whole flow offline with a scripted LLM")
    ap.add_argument("--live", action="store_true", help="ONE real (paid) Verifier run; needs a single --case")
    ap.add_argument("--metrics-out", type=Path, default=None, help="with --live: write the metrics JSON here")
    args = ap.parse_args()
    if sum([args.check, args.replay, args.live]) > 1:
        ap.error("choose one of --check, --replay, --live")
    names = list(CASES) if args.case == "all" else [args.case]
    if args.check:
        show_llm(live_llm())
    elif args.replay:
        sys.exit(0 if replay(names) else 1)
    elif args.live:
        if args.case == "all":
            ap.error("--live runs one case only: give --case unknowns|closed|clean")
        live(args.case, args.metrics_out)
    else:
        for name in names:
            v, desc, _ = verification(name)
            print_verification(v, desc)
            print(f"  needs the agent: {VerifierContext(v).needs_agent}")


if __name__ == "__main__":
    main()
