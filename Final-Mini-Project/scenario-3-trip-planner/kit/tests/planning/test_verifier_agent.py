"""Tests for planning/verifier_agent.py. No live service and no paid LLM call.

The verification reports come from the real Python path (test_verification's helpers: recorded research, the
Planner's build_itinerary(), then one fact broken). The CrewAI tests run the real Agent, Task,
inspect_verification tool and guardrail with the scripted test LLMs in scripted_llm.py.
"""
import json
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest
from pydantic import ValidationError

from planning.verifier_agent import (AGENT_BACKSTORY, GUARDRAIL_RETRIES, MAX_ITER, DecisionDraft, InspectVerificationTool,
                                     PrioritizedFinding, VerifierAgentError, VerifierAgentReport, VerifierContext,
                                     VerifierDecision, build_verifier_agent, build_verifier_task, explain_verification,
                                     inspect, make_guardrail, prompt_inputs, run_verifier_agent, validate_decision,
                                     verifier_trace)
from research_fixtures import recorded_lookup
from scripted_llm import NativeScriptedLLM, ScriptedLLM, by_role, native_tool_call, react_action, react_final, text
from test_verification import (BAZAAR, HAGIA, MON, PERA, PICKS, TOPKAPI, TUE, VALID, WED, clean,
                               edit, hours_lookup, live_travel, move, plan, remove, verify)

EXPLAIN = {"confirmed_error": "Python found this problem in the plan; it needs the recommended change.",
           "missing_evidence": "This fact is unknown; the recommended lookup would settle it.",
           "warning": "Worth knowing, nothing to change."}


def good(report, *, warnings=False, actions=None, summary="Fix the confirmed errors first, then fill the gaps."):
    """A correct decision for a report: errors, then unknowns (then warnings), Python's actions, neutral words."""
    ctx = VerifierContext(report)
    refs = [f for f in ctx.findings.values() if warnings or f.kind != "warning"]
    return VerifierDecision(summary=summary, findings=[
        PrioritizedFinding(finding_id=f.id, code=f.issue.code, kind=f.kind, date=f.issue.date,
                           place_id=f.issue.place_id, rank=n, explanation=EXPLAIN[f.kind],
                           recommended_action=(actions or {}).get(f.id, f.issue.recommended_action))
        for n, f in enumerate(refs, 1)])


def answer(decision, **changes) -> str:
    """The decision as JSON, with fields of findings changed: changes={"E1": {"kind": ...}} or top-level keys."""
    data = json.loads(decision.model_dump_json())
    for f in data["findings"]:
        f.update(changes.pop(f["finding_id"], {}))
    data.update(changes)
    return json.dumps(data)


def closed_report():
    b, report, planner, it = clean()
    return verify(move(it, TOPKAPI, TUE, "11:00", "12:30"), b=b, report=report, planner=planner)


OVERVIEW = native_tool_call("inspect_verification", {"section": "overview"})


def native(*replies):
    """A native-calling LLM that reads the overview first (Python requires one inspect_verification call), then
    gives `replies` in order."""
    return NativeScriptedLLM(model="scripted-test", replies=[OVERVIEW, *replies])


def native_raw(*replies):
    """Exactly `replies`: for runs that script their own tool calls, or show what happens without one."""
    return NativeScriptedLLM(model="scripted-test", replies=list(replies))


def inspected(r):
    """A context in which the agent has already read the overview through the tool."""
    ctx = VerifierContext(r)
    InspectVerificationTool(ctx)._run("overview")
    return ctx


# ------------------------------------------------------------------ wiring
def test_agent_and_task_are_wired_for_structured_output_with_a_read_only_tool():
    ctx = VerifierContext(closed_report())
    tool = InspectVerificationTool(ctx)
    agent = build_verifier_agent(ScriptedLLM(model="scripted-test"), tool)
    task = build_verifier_task(agent, ctx)
    assert agent.role == "Itinerary Verifier" and not agent.allow_delegation and agent.max_iter == MAX_ITER == 4
    assert [t.name for t in agent.tools] == ["inspect_verification"]
    assert task.output_pydantic is DecisionDraft and task.guardrail is not None  # lenient: the guardrail is strict
    assert task.guardrail_max_retries == GUARDRAIL_RETRIES == 2


def test_findings_get_stable_ids_and_allowed_actions():
    ctx = VerifierContext(closed_report())
    e1 = ctx.findings["E1"]
    assert (e1.issue.code, e1.kind, e1.allowed_actions) == ("closed_on_date", "confirmed_error",
                                                            ["move_activity", "remove_activity"])
    assert all(i.startswith(("E", "U", "W")) for i in ctx.findings)
    assert ctx.must_rank == [i for i in ctx.findings if not i.startswith("W")]


# ------------------------------------------------------------------ the no-LLM path
def test_a_fully_passing_report_needs_no_llm_call():
    b, report, planner, it = clean()
    llm = ScriptedLLM(model="scripted-test")  # no replies: any call would fail the run
    out = run_verifier_agent(it, b, llm, budget_report=report, planner_report=planner,
                             travel_lookup=recorded_lookup())
    assert llm.prompts == [] and out.decided_by == "python" and out.llm_usage == {}
    assert out.status == "PASS" and out.verified is False  # recorded data: never verified
    assert {p.kind for p in out.prioritized} == {"warning"}  # the price ranges, in Python's order
    assert out.summary == "Nothing needs fixing; 2 warning(s) to be aware of."
    assert "not freshly verified" in out.headline


def test_fresh_evidence_is_verified_by_python_alone():
    b, report, planner, it = clean()
    out = run_verifier_agent(it, b, ScriptedLLM(model="scripted-test"), budget_report=report,
                             planner_report=planner, travel_lookup=live_travel(), hours_lookup=hours_lookup(b))
    assert out.verified is True and out.decided_by == "python" and out.headline.startswith("PASS: every check")
    assert {c["tool"] for c in out.tool_calls} == {"get_opening_hours", "travel_time"}


def test_warnings_only_are_listed_by_python_without_an_llm_call():
    b, report, planner, it = clean()
    out = explain_verification(verify(it, b=b, report=report, hours_lookup=lambda name: {"ok": False}),
                               ScriptedLLM(model="scripted-test"))
    assert out.decided_by == "python" and out.status == "PASS"
    assert "hours_lookup_failed" in {p.code for p in out.prioritized} and "warning(s)" in out.summary
    assert [p.rank for p in out.prioritized] == list(range(1, len(out.prioritized) + 1))


# ------------------------------------------------------------------ real CrewAI runs
def test_a_closed_venue_is_ranked_first_by_the_agent():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")
    expected = verify(bad, b=b, report=report, planner=planner)
    llm = native(good(expected).model_dump_json())
    out = run_verifier_agent(bad, b, llm, budget_report=report, planner_report=planner,
                             travel_lookup=recorded_lookup())
    assert out.decided_by == "agent" and len(llm.prompts) == 2  # the overview, then the answer
    first = out.prioritized[0]
    assert (first.code, first.kind, first.place_id, str(first.date)) == ("closed_on_date", "confirmed_error",
                                                                         TOPKAPI, "2026-11-03")
    assert out.status == "FAIL" and out.verified is False  # Python's, unchanged
    assert out.verification == expected and out.revision_targets == expected.revision_targets
    assert set(out.llm_usage) == {"successful_requests", "prompt_tokens", "completion_tokens", "total_tokens"}
    assert out.guardrail_rejections == []


def test_a_travel_time_conflict_may_take_an_allowed_alternative_action():
    b, report, planner, it = clean()
    r = verify(edit(it, TOPKAPI, start_time="10:35", end_time="12:05"), b=b, report=report)
    fid = next(i for i, f in VerifierContext(r).findings.items() if f.issue.code == "insufficient_travel_gap")
    out = explain_verification(r, native(good(r, actions={fid: "reorder_day"}).model_dump_json()))
    picked = next(p for p in out.prioritized if p.finding_id == fid)
    assert picked.recommended_action == "reorder_day"
    assert out.revision_targets == r.revision_targets  # Python's targets are not rewritten by the agent's choice


def test_an_unknown_travel_duration_keeps_its_lookup_action():
    b, report, planner, it = clean()
    r = verify(it, b=b, report=report, travel=None)
    assert r.issues == [] and r.status == "UNKNOWN"
    bad = answer(good(r), U1={"recommended_action": "reschedule_activity"})  # turning missing evidence into a change
    llm = native(bad, good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert "does not fit its revision target; choose one of lookup_travel_time" in text(llm.prompts[-1])
    assert out.prioritized[0].recommended_action == "lookup_travel_time"
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"action": 1}}]
    assert out.status == "UNKNOWN" and out.verified is False


def test_unknown_and_inconsistent_prices_are_ranked_errors_first():
    b, report, planner = plan(days=VALID, picks=PICKS)
    r = verify(edit(planner.itinerary, HAGIA, estimated_cost=5.0), b=b, report=report)
    assert {"activity_cost_mismatch"} <= {i.code for i in r.issues}
    assert {"cost_unknown", "budget_undetermined"} <= {i.code for i in r.unknowns}
    right = good(r)
    flipped = answer(right, **{f.finding_id: {"rank": len(right.findings) + 1 - f.rank} for f in right.findings})
    llm = native(flipped, right.model_dump_json())
    out = explain_verification(r, llm)
    assert "every confirmed_error finding must rank before" in text(llm.prompts[-1])
    kinds = [p.kind for p in out.prioritized]
    assert kinds == sorted(kinds, key=["confirmed_error", "missing_evidence", "warning"].index)
    assert out.prioritized[0].code == "activity_cost_mismatch"
    assert any(p.code == "cost_unknown" and p.place_id == BAZAAR for p in out.prioritized)


def test_a_missing_required_place_cannot_be_left_out_of_the_ranking():
    b, report, planner, it = clean()
    r = verify(remove(it, TOPKAPI), b=b, report=report)
    full = good(r)
    dropped = full.model_copy(update={"findings": [f for f in full.findings if f.code != "required_place_missing"]})
    llm = native(dropped.model_dump_json(), full.model_dump_json())
    out = explain_verification(r, llm)
    assert "not ranked: E1" in text(llm.prompts[-1])
    assert out.prioritized[0].code == "required_place_missing" and out.prioritized[0].place_id == TOPKAPI


def test_inspect_tool_native_call_is_logged_and_traced():
    r = closed_report()
    llm = native_raw(native_tool_call("inspect_verification", {"section": "finding", "finding_id": "E1"}),
                     good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert len(llm.prompts) == 2 and llm.prompts[1][-1]["role"] == "tool"
    seen = json.loads(llm.prompts[1][-1]["content"])
    assert seen["finding"]["code"] == "closed_on_date" and seen["finding"]["evidence"][0]["freshness"] == "recorded"
    assert out.agent_tool_calls[0] | {"ms": 0} == {"tool": "inspect_verification", "ok": True, "ms": 0,
                                                   "section": "finding", "finding_id": "E1"}
    assert [s["step"] for s in out.agent_trace] == ["tool_call", "tool_result", "final_answer"]
    assert out.agent_trace[0]["finding_id"] == "E1"


def test_inspect_tool_text_call_runs():
    r = closed_report()
    llm = ScriptedLLM(model="scripted-test", replies=[react_action("inspect_verification", {"section": "overview"}),
                                                      react_final(good(r))])
    out = explain_verification(r, llm)
    assert '"status": "FAIL"' in text(llm.prompts[1]) and out.decided_by == "agent"
    assert out.agent_tool_calls[0]["section"] == "overview"
    assert [s["step"] for s in out.agent_trace] == ["tool_call", "tool_result"]  # ReAct keeps no final message


def test_failed_retries_raise_with_pythons_report_intact():
    r = closed_report()
    llm = ScriptedLLM(model="scripted-test", replies=["Thought: done\nFinal Answer: Everything looks fine."] * 20)
    with pytest.raises(VerifierAgentError, match="no valid decision") as failure:
        explain_verification(r, llm)
    assert failure.value.verification == r and failure.value.verification.status == "FAIL"
    assert failure.value.rejections and all(x["parse_error"] for x in failure.value.rejections)
    assert 3 <= len(llm.prompts) <= 6 and llm.replies  # bounded: CrewAI stopped by itself


def test_an_llm_failure_is_an_explicit_error():
    with pytest.raises(VerifierAgentError):
        explain_verification(closed_report(), ScriptedLLM(model="scripted-test"))  # no reply: the LLM fails


def test_a_status_or_verified_field_is_rejected_and_the_retry_used():
    r = closed_report()
    llm = native(answer(good(r), verified=True), answer(good(r), status="PASS"), good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert "the status and verified flag are Python's" in text(llm.prompts[2])
    assert out.status == "FAIL" and out.verified is False and len(out.guardrail_rejections) == 2
    assert len(llm.prompts) == 4  # the overview, then one call per answer: no hidden "conversion" call


def test_a_wrongly_shaped_answer_costs_one_retry_not_a_conversion_call():
    r = closed_report()
    llm = native('{"verdict": "looks fine", "findings": [{"id": "E1"}]}', good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert len(llm.prompts) == 3 and out.decided_by == "agent"
    last = text(llm.prompts[-1])
    assert "delete the field 'verdict'" in last and "not ranked: E1" in last


# ------------------------------------------------------------------ the guardrail, one rule at a time
@pytest.mark.parametrize("change, message", [
    ({"E1": {"finding_id": "E9"}}, "E9 is not a finding of the report"),
    ({"E1": {"code": "venue_on_fire"}}, "has the code closed_on_date, not venue_on_fire"),
    ({"E1": {"kind": "missing_evidence"}}, "a finding's kind cannot change"),
    ({"E1": {"place_id": "wv-istanbul-atlantis"}}, "copy both exactly"),
    ({"E1": {"date": "2026-11-05"}}, "copy both exactly"),
    ({"E1": {"recommended_action": "lookup_price"}}, "does not fit its revision target"),
    ({"E1": {"recommended_action": "book_a_taxi"}}, "does not fit its revision target"),
    ({"E1": {"explanation": "Topkapı is open on Tuesday mornings."}}, "says a place is open"),
    ({"E1": {"explanation": "The walk takes 15 minutes."}}, "a time or a duration"),
    ({"E1": {"explanation": "Leave at 10:30 to be safe."}}, "a time or a duration"),
    ({"E1": {"explanation": "Tickets cost $20 each."}}, "contains an amount"),
    ({"E1": {"explanation": "Visit wv-istanbul-galata-tower instead."}}, "place id wv-istanbul-galata-tower"),
    ({"E1": {"explanation": "Move it to 2026-11-09."}}, "the date 2026-11-09"),
    ({"summary": "The plan is verified."}, "claims the plan passes or is verified"),
    ({"summary": "Otherwise the plan passed every check."}, "claims the plan passes or is verified"),
])
def test_guardrail_rejects_invented_or_unsupported_answers(change, message):
    r = closed_report()
    ok, feedback = make_guardrail(VerifierContext(r))(answer(good(r), **change))
    assert ok is False and message in feedback


def test_missing_evidence_cannot_be_stated_as_certain():
    b, report, planner, it = clean()
    r = verify(it, b=b, report=report, travel=None)
    ok, feedback = make_guardrail(VerifierContext(r))(
        answer(good(r), U1={"explanation": "The gap is too short for this walk."}))
    assert not ok and "states a missing-evidence finding as certain" in feedback
    ok, _ = make_guardrail(inspected(r))(
        answer(good(r), U1={"explanation": "The walking time cannot be confirmed; a lookup would settle it."}))
    assert ok


def test_guardrail_rejects_duplicates_and_shared_ranks():
    r = closed_report()
    right = good(r)
    twice = right.model_copy(update={"findings": right.findings + [right.findings[0].model_copy(update={"rank": 99})]})
    ok, feedback = make_guardrail(VerifierContext(r))(twice.model_dump_json())
    assert not ok and "E1 is listed more than once" in feedback
    same = answer(right, **{f.finding_id: {"rank": 1} for f in right.findings})
    if len(right.findings) > 1:
        assert "share a rank" in make_guardrail(VerifierContext(r))(same)[1]


def test_guardrail_accepts_the_correct_answer_from_raw_text_and_records_it():
    r = closed_report()
    ctx = inspected(r)
    ok, decision = make_guardrail(ctx)(f"Here it is: {good(r).model_dump_json()}")
    assert ok and ctx.accepted == decision and validate_decision(decision, ctx) == []


def test_the_report_cannot_change_pythons_verdict():
    r = closed_report()
    common = dict(headline="x", verification=r, decided_by="agent", summary="x", revision_targets=r.revision_targets)
    with pytest.raises(ValidationError, match="cannot change"):
        VerifierAgentReport(status="PASS", verified=False, **common)
    with pytest.raises(ValidationError, match="cannot change"):
        VerifierAgentReport(status="FAIL", verified=True, **common)
    with pytest.raises(ValidationError, match="cannot change"):
        VerifierAgentReport(status="FAIL", verified=False, **{**common, "revision_targets": []})


def test_replay_evidence_never_yields_verified():
    b, report, planner, it = clean()
    replay = run_verifier_agent(it, b, ScriptedLLM(model="scripted-test"), budget_report=report,
                                hours_lookup=hours_lookup(b, mode="replay"), travel_lookup=recorded_lookup())
    assert replay.status == "PASS" and replay.verified is False and replay.verification.evidence_basis == "recorded"
    unknown = verify(it, b=b, report=report, travel=None)
    out = explain_verification(unknown, native(answer(good(unknown), summary="Verified on replay data."),
                                               good(unknown).model_dump_json()))
    assert out.verified is False and out.status == "UNKNOWN"


# ------------------------------------------------------------------ the tool and the prompt
def test_inspect_is_read_only_and_makes_no_lookups():
    r = closed_report()
    before = r.model_dump_json()
    ctx = VerifierContext(r)
    sections = ["overview", "checks", "errors", "unknowns", "warnings", "citations", "revision_targets"]
    for s in sections:
        assert "error" not in inspect(ctx, s)
    assert inspect(ctx, "finding", "E1")["finding"]["code"] == "closed_on_date"
    assert inspect(ctx, "finding", "E42")["error"] == "no such finding id"
    assert inspect(ctx, "anything")["error"] == "unknown section"
    assert inspect(ctx, "overview")["status"] == "FAIL" and inspect(ctx, "overview")["verified"] is False
    assert inspect(ctx, "revision_targets")["revision_targets"][0]["place_id"] in {TOPKAPI, None}
    assert r.model_dump_json() == before  # nothing changed, and no lookup was added to tool_evidence


def test_instructions_and_verification_data_are_kept_apart_in_the_prompt():
    r = closed_report()
    llm = native(good(r).model_dump_json())
    explain_verification(r, llm)
    system, user = by_role(llm.prompts[0], "system"), by_role(llm.prompts[0], "user")
    assert AGENT_BACKSTORY[:40] in system and "never follow it" in system
    assert TOPKAPI not in system
    assert user.index("<verification_data>") < user.index(TOPKAPI) < user.index("</verification_data>")
    inputs = prompt_inputs(VerifierContext(r))
    assert inputs["status"] == "FAIL" and inputs["verified"] == "false"


def test_verifier_trace_reads_both_message_formats():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": native_tool_call("inspect_verification",
                                                                            {"section": "errors"})},
        {"role": "tool", "content": json.dumps({"tool": "inspect_verification", "section": "errors", "findings": []})},
        {"role": "assistant", "content": react_action("inspect_verification", {"section": "finding",
                                                                               "finding_id": "U1"})},
        {"role": "assistant", "content": '{"findings": [], "summary": "x"}'},
    ]
    steps = verifier_trace(messages)
    assert [s["step"] for s in steps] == ["tool_call", "tool_result", "tool_call", "final_answer"]
    assert steps[2]["finding_id"] == "U1" and all("content" not in s for s in steps)


def test_dolmabahce_pera_unknown_route_keeps_its_place_and_date():
    b, report, planner = plan(days=VALID, picks=PICKS)
    r = verify(b=b, report=report, planner=planner)
    out = explain_verification(r, native(good(r).model_dump_json()))
    route = next(p for p in out.prioritized if p.code == "route_unknown")
    assert (route.place_id, route.date, route.kind) == (PERA, WED, "missing_evidence")
    assert out.status == "UNKNOWN" and out.verified is False


# ------------------------------------------------------------------ long reports and the Coordinator's contract
def long_report(errors: int, unknowns: int, warnings: int):
    """A report past the prompt limit, built by verification's own report builder from catalogue codes (no real
    plan has this many findings in the fixtures)."""
    from planning.verification import VerificationIssue, _report
    days = [MON, TUE, WED]
    found = [VerificationIssue.of("closed_on_date", f"Place {n} is closed.", date=days[n % 3],
                                  place_id=f"wv-istanbul-place-{n}") for n in range(errors)]
    found += [VerificationIssue.of("route_unknown", f"No route to place {n}.", date=days[n % 3],
                                   place_id=f"wv-istanbul-stop-{n}") for n in range(unknowns)]
    found += [VerificationIssue.of("cost_range", f"Range {n}.", place_id=f"wv-istanbul-range-{n}")
              for n in range(warnings)]
    return _report(found, ["opening_hours", "travel", "costs"])


def test_every_error_and_unknown_is_in_the_prompt_even_past_the_limit():
    r = long_report(errors=20, unknowns=30, warnings=10)  # 50 must-rank findings, limit 40
    data = json.loads(prompt_inputs(VerifierContext(r))["verification_data"])
    ids = [row["id"] for row in data["findings"]]
    assert set(VerifierContext(r).must_rank) <= set(ids) and len(ids) == 50  # all 50, no warning
    assert "10 more warning(s), optional to rank" in data["more_findings"]
    assert all(len(row["explanation"]) <= 80 for row in data["findings"])  # shortened rows, nothing dropped
    out = explain_verification(r, native(good(r).model_dump_json()))  # the real crew accepts all 50
    assert len(out.prioritized) == 50 and out.prioritized[0].kind == "confirmed_error"


def test_only_warnings_are_cut_and_the_note_says_so():
    r = long_report(errors=2, unknowns=3, warnings=60)
    data = json.loads(prompt_inputs(VerifierContext(r))["verification_data"])
    kinds = [row["kind"] for row in data["findings"]]
    assert len(kinds) == 40 and kinds[:5] == ["confirmed_error"] * 2 + ["missing_evidence"] * 3
    assert data["more_findings"].startswith("25 more warning(s)")
    assert len(inspect(VerifierContext(r), "warnings")["findings"]) == 60  # the tool still shows every one


def test_the_coordinator_can_round_trip_the_report_as_json():
    r = closed_report()
    out = explain_verification(r, native(good(r).model_dump_json()))
    back = VerifierAgentReport.model_validate_json(out.model_dump_json())
    assert back == out and back.status == "FAIL" and back.verification.revision_targets == r.revision_targets


# ------------------------------------------------------------------ scripts/try_verifier.py
@pytest.fixture(scope="module")
def try_verifier():
    import importlib.util
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("try_verifier", scripts / "try_verifier.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_replay_cases_are_real_reports(try_verifier):
    statuses = {name: try_verifier.verification(name)[0] for name in try_verifier.CASES}
    assert (statuses["unknowns"].status, statuses["unknowns"].issues) == ("UNKNOWN", [])
    assert "closed_on_date" in {i.code for i in statuses["closed"].issues}
    assert statuses["clean"].status == "PASS" and statuses["clean"].unknowns == []
    assert all(v.evidence_basis == "recorded" and not v.verified for v in statuses.values())


def test_replay_runs_every_integration_check_offline(try_verifier, capsys):
    assert try_verifier.replay(list(try_verifier.CASES)) is True
    printed = capsys.readouterr().out
    assert "FAILED" not in printed and "all integration checks passed" in printed


def test_the_replay_network_guard_blocks_the_mcp_service_and_openrouter(try_verifier):
    import socket
    attempts = []
    with try_verifier.no_network(attempts):
        for address in [("127.0.0.1", 8103), ("openrouter.ai", 443)]:
            with pytest.raises(ConnectionRefusedError):
                socket.create_connection(address, timeout=1)
            s = socket.socket()
            with pytest.raises(ConnectionRefusedError):
                s.connect(address)
            s.close()
        a, b = socket.socketpair()  # asyncio's in-process self-pipe stays allowed
        a.close(), b.close()
    assert attempts == ["127.0.0.1:8103", "127.0.0.1:8103", "openrouter.ai:443", "openrouter.ai:443"]
    assert socket.socket.connect.__qualname__ == "socket.connect"  # restored afterwards


# ------------------------------------------------------------------ the inspect_verification requirement
def test_native_final_answer_without_the_tool_is_sent_back_and_the_retry_uses_it():
    r = closed_report()
    llm = native_raw(good(r).model_dump_json(), OVERVIEW, good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert "you have not read the report through the inspect_verification tool yet" in text(llm.prompts[1])
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"tool": 1}}]
    assert [c["section"] for c in out.agent_tool_calls] == ["overview"] and len(llm.prompts) == 3
    assert out.decided_by == "agent" and out.status == "FAIL"


def test_react_final_answer_without_the_tool_is_sent_back_and_the_retry_uses_it():
    r = closed_report()
    llm = ScriptedLLM(model="scripted-test", replies=[react_final(good(r)),
                                                      react_action("inspect_verification", {"section": "overview"}),
                                                      react_final(good(r))])
    out = explain_verification(r, llm)
    assert "inspect_verification tool yet" in text(llm.prompts[1])
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"tool": 1}}]
    assert [c["section"] for c in out.agent_tool_calls] == ["overview"]


def test_an_agent_that_never_uses_the_tool_ends_in_an_explicit_bounded_error():
    r = closed_report()
    llm = native_raw(*[good(r).model_dump_json()] * 20)
    with pytest.raises(VerifierAgentError, match="no valid decision") as failure:
        explain_verification(r, llm)
    assert len(llm.prompts) == 1 + GUARDRAIL_RETRIES and llm.replies  # one answer per attempt, then stop
    assert [x["problems"] for x in failure.value.rejections] == [{"tool": 1}] * (1 + GUARDRAIL_RETRIES)
    assert failure.value.verification == r and failure.value.tool_calls == []


def test_a_failed_tool_call_does_not_count():
    r = closed_report()
    bad_call = native_tool_call("inspect_verification", {"section": "finding", "finding_id": "E42"})
    llm = native_raw(bad_call, good(r).model_dump_json(), OVERVIEW, good(r).model_dump_json())
    out = explain_verification(r, llm)
    assert [c["ok"] for c in out.agent_tool_calls] == [False, True]
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"tool": 1}}]


def test_the_report_refuses_an_agent_decision_without_a_successful_tool_call():
    r = closed_report()
    common = dict(status=r.status, verified=r.verified, headline="x", verification=r, decided_by="agent",
                  summary="x", revision_targets=r.revision_targets)
    with pytest.raises(ValidationError, match="at least one successful inspect_verification"):
        VerifierAgentReport(**common)
    with pytest.raises(ValidationError, match="at least one successful inspect_verification"):
        VerifierAgentReport(**common, agent_tool_calls=[{"tool": "inspect_verification", "ok": False}])
    VerifierAgentReport(**common, agent_tool_calls=[{"tool": "inspect_verification", "ok": True}])


# ------------------------------------------------------------------ numbers: only as Python states them
def live_answer(r):
    """The first live OpenRouter answer, reconstructed: every explanation copied from Python's own text, in the
    live run's order (U1, U3, U2). Its U2 copy carries Python's '315.32 USD'."""
    ctx = VerifierContext(r)
    order = {"route_unknown": 1, "cost_unknown": 2, "budget_undetermined": 3}
    return VerifierDecision(summary="First, look up the travel time between Dolmabahce Palace and Pera Museum, then "
                                    "find the missing cost for Grand Bazaar, and finally confirm the overall budget.",
                            findings=[PrioritizedFinding(finding_id=i, code=f.issue.code, kind=f.kind, date=f.issue.date,
                                                         place_id=f.issue.place_id, rank=order[f.issue.code],
                                                         recommended_action=f.issue.recommended_action,
                                                         explanation=f.issue.explanation)
                                      for i, f in ctx.findings.items() if f.issue.code in order])


def test_pythons_own_numbers_may_be_repeated():
    b, report, planner = plan(days=VALID, picks=PICKS)
    r = verify(b=b, report=report, planner=planner)
    answer_ = live_answer(r)
    assert any("315.32 USD" in f.explanation for f in answer_.findings)
    assert validate_decision(answer_, inspected(r)) == []  # the live run's only rejection, now accepted
    out = explain_verification(r, native(answer_.model_dump_json()))
    assert out.guardrail_rejections == []


@pytest.mark.parametrize("explanation, message", [
    ("The known costs come to 400 USD.", "does not state (400 USD"),          # an invented amount
    ("The known costs come to 315.32 EUR.", "does not state (315.32 EUR"),    # Python's number, another currency
    ("The known costs are about 315 USD.", "does not state (315 USD"),        # rounded is not Python's
    ("The gap is 30 minutes.", "does not state (30)"),                         # U1's number, borrowed into U2
])
def test_numbers_python_did_not_state_for_that_finding_are_rejected(explanation, message):
    b, report, planner = plan(days=VALID, picks=PICKS)
    r = verify(b=b, report=report, planner=planner)
    ctx = inspected(r)
    u2 = next(i for i, f in ctx.findings.items() if f.issue.code == "budget_undetermined")
    ok, feedback = make_guardrail(ctx)(answer(live_answer(r), **{u2: {"explanation": explanation}}))
    assert not ok and message in feedback


# ------------------------------------------------------------------ the tool stays read-only and offline
def test_the_tool_is_read_only_and_makes_no_network_call(try_verifier):
    r = closed_report()
    before = r.model_dump_json()
    ctx = VerifierContext(r)
    tool = InspectVerificationTool(ctx)
    attempts = []
    with try_verifier.no_network(attempts):
        for section in ["overview", "checks", "errors", "unknowns", "warnings", "citations", "revision_targets"]:
            assert "error" not in json.loads(tool._run(section))
        assert "error" not in json.loads(tool._run("finding", "E1"))
    assert attempts == [] and r.model_dump_json() == before
    assert ctx.inspections == ["overview", "checks", "errors", "unknowns", "warnings", "citations",
                               "revision_targets", "finding"]
    assert len(tool.calls) == 8 and all(c["ok"] for c in tool.calls)


# ------------------------------------------------------------------ hedged wording, and the Python fallback
@pytest.mark.parametrize("text, certain", [
    ("Opening hours are not yet confirmed; a fresh get_opening_hours lookup would settle it.", False),
    ("The travel_time lookup failed, so the walk is unknown; a new lookup would settle it.", False),
    ("Its hours could not be confirmed by the listing.", False),
    ("It is definitely closed that day.", True),
    ("The listing is wrong and the visit fails.", True),
])
def test_missing_evidence_wording_that_hedges_is_not_called_certain(text, certain):
    from planning.verifier_agent import _states_certainty
    assert _states_certainty(text) is certain


def test_a_failed_verifier_agent_leaves_pythons_verdict_and_ranking():
    from planning.verifier_agent import AGENT_FAILED_SUMMARY, VerifierAgentError, python_explanation
    report = closed_report()
    out = python_explanation(VerifierAgentError("x", verification=report, rejections=[{"problems": {"prose": 2}}]))
    assert (out.status, out.verified, out.decided_by) == (report.status, report.verified, "python")
    assert out.summary == AGENT_FAILED_SUMMARY and out.guardrail_rejections == [{"problems": {"prose": 2}}]
    assert out.revision_targets == report.revision_targets
