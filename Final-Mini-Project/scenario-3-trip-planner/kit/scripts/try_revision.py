"""Try the Targeted Revision agent on the closed-Topkapı fixture (development only).

    python scripts/try_revision.py                  # dry run: the Verifier's findings and Python's options, no LLM
    python scripts/try_revision.py --check          # build the real LLM and show its settings, no call
    python scripts/try_revision.py --replay         # the whole flow offline: the real CrewAI agent, task, tool and
                                                    # guardrail with scripted LLMs, every integration check, network
                                                    # blocked
    python scripts/try_revision.py --live           # ONE real revision run on OpenRouter (costs money)

The case is try_verifier.py's "closed": the Istanbul plan with Topkapı Palace moved to Tuesday, a day its listing
says it is closed. Python's revision engine finds the feasible fixes (Topkapı on Monday or on Wednesday, each
scheduled by Python), the Itinerary Planner chooses one by its id, and Python applies it, rebuilds the plan through
the Planner's own validation and verifies it again. Python keeps the agent's choice only if it is no worse than its
own: choosing Wednesday leaves a walk nobody measured (UNKNOWN), so Python's Monday stands.

The evidence is RECORDED: listing hours from the research fixture, travel times replayed from
tests/fixtures/istanbul_routes.json. No MCP request is made in any mode, so the revised plan can reach PASS but
never verified=True. In --live only the agent's LLM calls are real; every other connection is refused and listed.
The LLM settings come from .env, as in try_budget_analyst.py.
"""
import argparse
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from try_verifier import (LOOPBACK, TIME_LIMIT_S, TUE, build_case, no_network, recorded_travel,  # noqa: E402
                          scripted_llm, without_timings)
from try_budget_analyst import live_llm, show_llm  # noqa: E402

from planning.revision_agent import (GUARDRAIL_RETRIES, MAX_ITER, RevisionAgentReport,  # noqa: E402
                                     run_targeted_revision)
from planning.revision import revise_itinerary  # noqa: E402
from planning.schedule import validate_day  # noqa: E402
from planning.verification import verify_itinerary  # noqa: E402

MAX_LLM_CALLS = MAX_ITER * (1 + GUARDRAIL_RETRIES)  # one crew run, round 1 only: the structural bound (12)
OPENROUTER_HOST = "openrouter.ai"


# ------------------------------------------------------------------ the recorded case
def case():
    """(bundle, Budget Analyst report, the broken itinerary, Python's VerificationReport of it), all recorded."""
    bundle, report, _planner, itinerary, _desc = build_case("closed")
    verification = verify_itinerary(itinerary, bundle, budget_report=report, travel_lookup=recorded_travel([]))
    return bundle, report, itinerary, verification


def python_options(bundle, report, itinerary, verification):
    """The decision points Python offers in round 1, without any LLM (a decide callback that keeps every default)."""
    seen = []
    revise_itinerary(itinerary, bundle, report, verification=verification, travel_lookup=recorded_travel([]),
                     decide=lambda points: seen.extend(points) or {})
    return seen


def revise(llm, travel_log: list):
    bundle, report, itinerary, verification = case()
    started = time.perf_counter()
    out = run_targeted_revision(itinerary, bundle, report, llm, verification=verification,
                                travel_lookup=recorded_travel(travel_log))
    return out, time.perf_counter() - started, (bundle, report, itinerary, verification)


# ------------------------------------------------------------------ the live network allowlist
@contextmanager
def only_hosts(allowed: set[str], attempts: list, *, real_create=None):
    """Allow outgoing connections to `allowed` host names only (and asyncio's in-process self-pipe); refuse and
    record every other. HTTP clients connect through socket.create_connection with the host name, so the check is
    on the name; the IP-level connect inside an allowed call is let through."""
    real_connect, real_listen = socket.socket.connect, socket.socket.listen
    original_create = socket.create_connection  # restored afterwards (real_create may be a test's stand-in)
    real_create = real_create or original_create
    own_ports: set[int] = set()
    inside = threading.local()

    def listen(self, *args):
        real_listen(self, *args)
        own_ports.add(self.getsockname()[1])

    def connect(self, address):
        host, port = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        if getattr(inside, "allowed", False) or (host in LOOPBACK and port in own_ports):
            return real_connect(self, address)
        attempts.append(f"{host}:{port}")
        raise ConnectionRefusedError("blocked: only the LLM endpoint may be reached")

    def create_connection(address, *args, **kwargs):
        host = str(address[0]).lower()
        if host not in allowed and not any(host.endswith("." + a) for a in allowed):
            attempts.append(f"{host}:{address[1]}")
            raise ConnectionRefusedError("blocked: only the LLM endpoint may be reached")
        inside.allowed = True
        try:
            return real_create(address, *args, **kwargs)
        finally:
            inside.allowed = False
    socket.socket.connect, socket.socket.listen, socket.create_connection = connect, listen, create_connection
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.listen, socket.create_connection = real_connect, real_listen, original_create


# ------------------------------------------------------------------ output
def metrics(mode: str, out: RevisionAgentReport, seconds: float, llm_calls: int | None, travel_log: list,
            blocked: list) -> dict:
    r = out.result
    usage = out.llm_usage or {}
    return {"mode": mode, "case": "closed", "evidence": "recorded fixtures (not fresh)",
            "duration_s": round(seconds, 2), "within_time_limit": seconds <= TIME_LIMIT_S,
            "successful_requests": usage.get("successful_requests"), "total_tokens": usage.get("total_tokens"),
            "llm_calls_observed": llm_calls, "llm_call_bound": MAX_LLM_CALLS, "llm_called": out.llm_called,
            "tool_calls": [{k: c.get(k) for k in ("tool", "ok", "decision_id", "alternative_id")}
                           for c in out.agent_tool_calls],
            "guardrail_retries": len(out.guardrail_rejections), "guardrail_rejections": out.guardrail_rejections,
            "decided_by": out.decided_by, "decision_error": r.decision_error,
            "offered_options": {p.id: [a.id for a in p.alternatives] for p in out.decision_points},
            "selected_options": {c.decision_id: c.alternative_id for c in out.choice.choices} if out.choice else {},
            "applied_options": r.attempts[0].choices if r.attempts else {},
            "outcome": r.outcome, "original_status": r.original.status, "final_status": r.status,
            "verified": r.verified, "changed_dates": [str(d) for d in r.changed_dates],
            "preserved_dates": [str(d) for d in r.preserved_dates], "resolved": r.resolved,
            "remaining_errors": r.remaining_errors, "recorded_travel_lookups": len(travel_log),
            "blocked_connections": blocked}


def print_options(points) -> None:
    for p in points:
        print(f"\n{p.id} {p.kind} {p.place_id} (Python's default {p.default})")
        for a in p.alternatives:
            print(f"  {a.id}  {a.summary}: hours {a.hours}, measured travel {a.measured_travel_minutes} min, "
                  f"unmeasured legs {a.unmeasured_legs}, walks over the limit {a.walks_over_limit}, "
                  f"reordered {a.reordered_places}")


def print_result(out: RevisionAgentReport, m: dict) -> None:
    r = out.result
    print(f"\n{r.outcome}: {r.original.status} -> {r.status}, verified={r.verified} (decided by {out.decided_by})")
    if r.decision_error:
        print(f"  {r.decision_error}")
    for d in r.itinerary.days:
        print(f"  {d.date} ({d.weekday}): " + ", ".join(f"{a.start_time}-{a.end_time} {a.name}" for a in d.activities))
    for c in (out.choice.choices if out.choice else []):
        print(f"  agent: {c.decision_id} -> {c.alternative_id}: {c.reason}")
    print("metrics: " + json.dumps(m, ensure_ascii=False))


# ------------------------------------------------------------------ the integration checks
def checks(out: RevisionAgentReport, inputs, llm_calls: int, net: list, *, expect: str) -> list[tuple[str, bool]]:
    bundle, report, itinerary, verification = inputs
    r = out.result
    offered = {a.id for p in out.decision_points for a in p.alternatives}
    default = python_options(bundle, report, itinerary, verification)
    defaults = {p.id: p.default for p in default}
    chosen = {c.decision_id: c.alternative_id for c in out.choice.choices} if out.choice else {}
    again = verify_itinerary(r.itinerary, bundle, budget_report=report, planner_report=r.planner_report,
                             travel_lookup=recorded_travel([]))
    rebuilt = r.planner_report is not None and all(not validate_day(d) for d in r.planner_report.schedule.days)
    found = [
        ("an accepted choice rests on a logged, successful inspect_revision_options call; an answer without one "
         "is sent back",
         (out.choice is None or any(c["ok"] and c["tool"] == "inspect_revision_options" for c in out.agent_tool_calls))
         and (any(c["ok"] for c in out.agent_tool_calls) or
              all(x["problems"].get("tool") for x in out.guardrail_rejections if not x["parse_error"]))),
        ("the agent's choices are Python's own alternative ids", set(chosen.values()) <= offered),
        ("Python rebuilt the plan through the Planner's validation", rebuilt),
        ("Topkapı is no longer on its closed Tuesday",
         not any(a.place_id == "wv-istanbul-topkapi-palace" for d in r.itinerary.days if d.date == TUE
                 for a in d.activities)),
        ("Python re-verified the revised plan (the same report again)",
         without_timings(again) == without_timings(r.final)),
        ("the original report is kept unchanged", without_timings(r.original) == without_timings(verification)),
        ("status and verified are the final report's", (out.status, out.verified) == (r.final.status, r.final.verified)),
        ("recorded evidence: verified stays False", out.verified is False),
        ("Wednesday is copied verbatim", [d for d in r.itinerary.days if str(d.date) == "2026-11-04"] ==
         [d for d in itinerary.days if str(d.date) == "2026-11-04"]),
        (f"at most {MAX_LLM_CALLS} LLM calls", llm_calls <= MAX_LLM_CALLS),
        ("the Coordinator can round-trip the report as JSON",
         RevisionAgentReport.model_validate_json(out.model_dump_json()) == out),
        ("no network connection was attempted", not net),
    ]
    if expect == "agent":
        found.append(("the agent's valid choice is applied", out.decided_by == "agent"
                      and r.attempts[0].choices == chosen))
    else:
        found.append(("Python's safer default is kept", out.decided_by == "python_fallback"
                      and r.attempts[0].choices == defaults))
    return found


SCENARIOS = [  # (label, replies, who should decide)
    ("agent reads the options and picks Monday (Python's default)", ["inspect", "D1-A1"], "agent"),
    ("agent states Monday's listed hours (the first live run's wording, now grounded)",
     ["inspect", ("D1-A1", "Topkapı Palace is open on Monday and every walk is measured.")], "agent"),
    ("agent picks Wednesday: valid, but leaves an unmeasured walk", ["inspect", "D1-A2"], "python"),
    ("agent invents an alternative id, then corrects it", ["inspect", "D1-A9", "D1-A1"], "agent"),
    ("agent answers without reading the options, then reads them", ["D1-A1", "inspect", "D1-A1"], "agent"),
    ("agent never gives a valid answer", ["D1-A9"] * 20, "python"),
]


def scripted(replies):
    scripted_llm()  # puts tests/planning on the path for native_tool_call
    from scripted_llm import native_tool_call
    out = []
    for r in replies:
        if r == "inspect":
            out.append(native_tool_call("inspect_revision_options", {"decision_id": "D1"}))
        else:
            alt, reason = r if isinstance(r, tuple) else (r, "The better option for the day.")
            out.append(json.dumps({"choices": [{"decision_id": "D1", "alternative_id": alt, "reason": reason}]}))
    return scripted_llm(*out)


def replay() -> bool:
    ok = True
    for label, replies, expect in SCENARIOS:
        travel_log, net = [], []
        with no_network(net):
            llm = scripted(replies)
            out, seconds, inputs = revise(llm, travel_log)
            found = checks(out, inputs, len(llm.prompts), net, expect=expect)
        print(f"\n=== {label}")
        print_result(out, metrics("replay (scripted LLM)", out, seconds, len(llm.prompts), travel_log, net))
        for name, passed in found:
            ok &= passed
            print(f"  [{'ok' if passed else 'FAILED'}] {name}")
    print(f"\n{'all integration checks passed' if ok else 'SOME INTEGRATION CHECKS FAILED'} "
          f"(bound: {MAX_LLM_CALLS} LLM calls in one crew run)")
    return ok


def live(metrics_out: Path | None) -> None:
    llm = live_llm()
    host = OPENROUTER_HOST
    travel_log, blocked = [], []
    print("Evidence: recorded fixtures (not fresh). LLM: live OpenRouter (paid). Network: only "
          f"{host} is allowed; every other connection is refused and listed.")
    with only_hosts({host}, blocked):
        out, seconds, _ = revise(llm, travel_log)
    m = metrics("live", out, seconds, None, travel_log, blocked)
    print_result(out, m)
    if seconds > TIME_LIMIT_S:
        print(f"WARNING: {seconds:.0f} s is over the {TIME_LIMIT_S} s evaluation limit")
    if (out.llm_usage or {}).get("successful_requests", 0) > MAX_LLM_CALLS:
        print(f"WARNING: more than {MAX_LLM_CALLS} LLM requests")
    if metrics_out:
        metrics_out.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"metrics written to {metrics_out}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="build the real LLM and show its settings, no call")
    ap.add_argument("--replay", action="store_true", help="the whole flow offline with scripted LLMs")
    ap.add_argument("--live", action="store_true", help="ONE real (paid) revision run")
    ap.add_argument("--metrics-out", type=Path, default=None, help="with --live: write the metrics JSON here")
    args = ap.parse_args()
    if sum([args.check, args.replay, args.live]) > 1:
        ap.error("choose one of --check, --replay, --live")
    if args.check:
        show_llm(live_llm())
    elif args.replay:
        sys.exit(0 if replay() else 1)
    elif args.live:
        live(args.metrics_out)
    else:
        bundle, report, itinerary, verification = case()
        print(f"Python's verification: {verification.status}, verified={verification.verified}")
        for i in verification.issues:
            print(f"  error {i.code} {i.place_id} {i.date} -> {i.recommended_action}")
        print_options(python_options(bundle, report, itinerary, verification))


if __name__ == "__main__":
    main()
