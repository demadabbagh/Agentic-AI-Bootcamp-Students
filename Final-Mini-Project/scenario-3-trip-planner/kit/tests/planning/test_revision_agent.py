"""Tests for planning/revision_agent.py. No live service and no paid LLM call.

The revisions start from real reports (test_revision's helpers: recorded research, the Planner's build_itinerary(),
verify_itinerary()). The CrewAI tests run the real Agent, Task, inspect_revision_options tool and guardrail with the
scripted test LLMs in scripted_llm.py; Python applies the choice, rebuilds and re-verifies.
"""
import json
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest
from pydantic import ValidationError

from planning.revision import revise_itinerary
from planning.revision_agent import (GUARDRAIL_RETRIES, MAX_ITER, InspectRevisionOptionsTool, RevisionAgentReport,
                                     RevisionContext, RevisionDraft, build_revision_agent, build_revision_task,
                                     inspect, make_guardrail, run_targeted_revision)
from research_fixtures import bundle, recorded_lookup
from scripted_llm import NativeScriptedLLM, ScriptedLLM, by_role, native_tool_call, react_action, text
from test_revision import (BAZAAR, HAGIA, MON, PERA, PICKS, TOPKAPI, TUE, VALID, WED, add, clean, day, edit, ids,
                           move, plan, remove, verify)
from test_verification import ALI_USTA


def points_for(it, b, report, **kw):
    """The decision points round 1 offers, from Python alone (a decide callback that keeps every default)."""
    seen = []
    revise_itinerary(it, b, report, travel_lookup=recorded_lookup(), decide=lambda pts: seen.extend(pts) or {},
                     **kw)
    return seen


def answer(**picks) -> str:
    return json.dumps({"choices": [{"decision_id": d, "alternative_id": a, "reason": "The better option for the day."}
                                   for d, a in picks.items()]})


def inspect_call(decision_id="D1"):
    return native_tool_call("inspect_revision_options", {"decision_id": decision_id})


def native(*replies):
    return NativeScriptedLLM(model="scripted-test", replies=list(replies))


def run(it, b, report, llm, **kw):
    v = verify(it, b=b, report=report, travel=recorded_lookup())
    return run_targeted_revision(it, b, report, llm, verification=v, travel_lookup=recorded_lookup(), **kw)


def closed_plan():
    b, report, planner, it = clean()
    return b, report, move(it, TOPKAPI, TUE, "11:00", "12:30")


# ------------------------------------------------------------------ wiring and the choice points
def test_the_revision_task_belongs_to_the_itinerary_planner_with_a_read_only_tool():
    ctx = RevisionContext([], {})
    agent = build_revision_agent(ScriptedLLM(model="scripted-test"), InspectRevisionOptionsTool(ctx))
    task = build_revision_task(agent, ctx)
    assert agent.role == "Itinerary Planner" and not agent.allow_delegation and agent.max_iter == MAX_ITER == 4
    assert [t.name for t in agent.tools] == ["inspect_revision_options"]
    assert task.output_pydantic is RevisionDraft and task.guardrail_max_retries == GUARDRAIL_RETRIES == 2


def test_a_closed_venue_offers_each_feasible_date_with_pythons_facts():
    b, report, bad = closed_plan()
    pts = points_for(bad, b, report)
    assert len(pts) == 1 and pts[0].kind == "placement" and pts[0].place_id == TOPKAPI
    alts = pts[0].alternatives
    assert [a.date for a in alts] == [MON, WED] and pts[0].default == alts[0].id  # never Tuesday: closed
    assert alts[0].unmeasured_legs == 0 and alts[1].unmeasured_legs == 1  # Wednesday's walk was never measured
    assert all(a.hours == "open" and a.schedule for a in alts)


# ------------------------------------------------------------------ the agent's choice, applied by Python
def test_a_valid_choice_is_applied_rebuilt_and_reverified():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), answer(D1="D1-A1"))
    out = run(bad, b, report, llm)
    assert out.llm_called and out.decided_by == "agent" and len(llm.prompts) == 2
    assert out.choice.choices[0].alternative_id == "D1-A1"
    r = out.result
    assert r.outcome == "revised" and r.status == "PASS" and r.verified is False and out.status == "PASS"
    assert TOPKAPI in ids(r.itinerary, MON) and day(r.itinerary, WED) == day(bad, WED)
    assert r.final == verify(r.itinerary, b=b, report=report, planner=r.planner_report, travel=recorded_lookup())
    assert r.original.status == "FAIL" and "closed_on_date" in {i.code for i in r.original.issues}  # kept as given
    assert out.agent_tool_calls[0] | {"ms": 0} == {"tool": "inspect_revision_options", "ok": True, "ms": 0,
                                                   "decision_id": "D1", "alternative_id": None}
    assert [s["step"] for s in out.agent_trace] == ["tool_call", "tool_result", "final_answer"]
    assert set(out.llm_usage) == {"successful_requests", "prompt_tokens", "completion_tokens", "total_tokens"}


def test_a_valid_choice_that_leaves_a_worse_verdict_is_not_kept():
    b, report, bad = closed_plan()
    out = run(bad, b, report, native(inspect_call(), answer(D1="D1-A2")))  # Wednesday: an unmeasured walk
    r = out.result
    assert out.decided_by == "python_fallback" and out.choice.choices[0].alternative_id == "D1-A2"
    assert "UNKNOWN where Python's own choice leaves it PASS" in r.decision_error
    assert TOPKAPI in ids(r.itinerary, MON) and r.status == "PASS"  # Python's choice stands


def test_the_agent_may_prefer_the_least_disruptive_of_equally_good_orders():
    b, report, planner, it = clean()
    tight = bundle(max_walk_minutes=14)
    pts = points_for(it, tight, report)
    assert pts[0].kind == "reorder" and len(pts[0].alternatives) == 4
    calm = min(pts[0].alternatives, key=lambda a: a.reordered_places)
    assert calm.id != pts[0].default  # Python ranks by travel; the agent weighs disruption
    out = run(it, tight, report, native(inspect_call(), answer(D1=calm.id)))
    r = out.result
    assert out.decided_by == "agent" and r.status == "PASS" and r.final.issues == []
    assert ids(r.itinerary, MON) == calm.order
    assert [day(r.itinerary, d) for d in (TUE, WED)] == [day(it, d) for d in (TUE, WED)]  # untouched dates


def test_the_agent_chooses_which_place_gives_way_for_a_required_one():
    # Monday only, 09:00-13:00: Hagia Sophia and Ali Usta fill it; Topkapı (required) needs one of them to give way.
    b, report, planner = plan(days={MON: [HAGIA, TOPKAPI]}, unscheduled={ALI_USTA: "does not fit"},
                              picks={HAGIA: ("include", 1), ALI_USTA: ("include", 2)},
                              start_date="2026-11-02", end_date="2026-11-02", day_end="13:00")
    bad = add(remove(planner.itinerary, TOPKAPI), MON, report.to_activity(ALI_USTA, start_time="10:43",
                                                                         end_time="11:43"))
    pts = points_for(bad, b, report)
    assert pts[0].kind == "make_room" and {a.gives_way for a in pts[0].alternatives} == {ALI_USTA, HAGIA}
    pick = next(a for a in pts[0].alternatives if a.gives_way == HAGIA)  # not Python's default (the least important)
    out = run(bad, b, report, native(inspect_call(), answer(D1=pick.id)))
    r = out.result
    assert out.decided_by == "agent" and r.final.issues == [] and TOPKAPI in ids(r.itinerary, MON)
    assert HAGIA not in ids(r.itinerary, MON) and "make room for Topkapı" in r.attempts[0].plan.unscheduled_after[HAGIA]
    assert "selected_place_unscheduled" in {i.code for i in r.final.warnings}  # said out loud


# ------------------------------------------------------------------ the guardrail
@pytest.mark.parametrize("reply, message", [
    (answer(D1="D1-A9"), "'D1-A9' is not one of its alternatives"),
    (answer(D7="D1-A1"), "D7 is not a decision point"),
    (json.dumps({"choices": []}), "not answered: D1"),
    (json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1", "reason": "Only 12 minutes away."}]}),
     "contains a number the option does not state (12)"),
    (json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1", "reason": "It is open on Tuesday too."}]}),
     "says it is open on 2026-11-03 (Tue), but the listing status there is closed"),
    (json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1", "reason": "x"}], "status": "PASS"}),
     "delete the field 'status'"),
    (json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1", "reason": "x", "start": "09:00"}]}),
     "D1: delete the field 'start'"),
])
def test_guardrail_rejects_invented_or_unsupported_choices(reply, message):
    b, report, bad = closed_plan()
    ctx = RevisionContext(points_for(bad, b, report), {})
    InspectRevisionOptionsTool(ctx)._run("D1")
    ok, feedback = make_guardrail(ctx)(reply)
    assert ok is False and message in feedback


def test_a_number_the_option_states_may_be_repeated():
    b, report, bad = closed_plan()
    ctx = RevisionContext(points_for(bad, b, report), {})
    InspectRevisionOptionsTool(ctx)._run("D1")
    minutes = ctx.points["D1"].alternatives[0].measured_travel_minutes
    ok, _ = make_guardrail(ctx)(json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1",
                                                         "reason": f"All walks measured, {minutes} minutes in all."}]}))
    assert ok


def test_an_invented_alternative_is_sent_back_and_the_retry_used():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), answer(D1="D1-A9"), answer(D1="D1-A1"))
    out = run(bad, b, report, llm)
    assert "is not one of its alternatives" in text(llm.prompts[-1])
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"unknown_alternative": 1}}]
    assert out.decided_by == "agent" and out.result.status == "PASS"


def test_a_choice_without_reading_the_options_is_sent_back_native_and_react():
    b, report, bad = closed_plan()
    out = run(bad, b, report, native(answer(D1="D1-A1"), inspect_call(), answer(D1="D1-A1")))
    assert out.guardrail_rejections == [{"parse_error": False, "problems": {"tool": 1}}]
    assert out.decided_by == "agent"
    react = ScriptedLLM(model="scripted-test", replies=[f"Thought: done\nFinal Answer: {answer(D1='D1-A1')}",
                                                        react_action("inspect_revision_options", {"decision_id": "D1"}),
                                                        f"Thought: done\nFinal Answer: {answer(D1='D1-A1')}"])
    out = run(bad, b, report, react)
    assert "inspect_revision_options tool yet" in text(react.prompts[1]) and out.decided_by == "agent"


def test_failed_retries_fall_back_to_pythons_choice_explicitly():
    b, report, bad = closed_plan()
    llm = native(*[answer(D1="D1-A9")] * 20)
    out = run(bad, b, report, llm)
    r = out.result
    assert out.decided_by == r.decided_by == "python_fallback" and out.choice is None
    assert "no valid choice from the agent" in r.decision_error
    assert len(llm.prompts) == 1 + GUARDRAIL_RETRIES and llm.replies  # bounded: one answer per attempt
    assert len(out.guardrail_rejections) == 1 + GUARDRAIL_RETRIES
    assert r.outcome == "revised" and TOPKAPI in ids(r.itinerary, MON)  # Python's own choice, still a fix


def test_an_llm_failure_falls_back_too():
    b, report, bad = closed_plan()
    out = run(bad, b, report, ScriptedLLM(model="scripted-test"))  # no reply: the LLM call fails
    assert out.decided_by == "python_fallback" and out.result.status == "PASS"


# ------------------------------------------------------------------ no LLM call where nothing is to be chosen
def test_a_valid_plan_needs_no_llm_call():
    b, report, planner, it = clean()
    llm = ScriptedLLM(model="scripted-test")  # no replies: any call would fail the run
    out = run(it, b, report, llm)
    assert llm.prompts == [] and not out.llm_called and out.decided_by == "python"
    assert out.result.outcome == "no_change_needed" and out.result.itinerary == it


def test_missing_evidence_needs_no_llm_call_and_stays_a_request():
    b, report, planner = plan(days=VALID, picks=PICKS)
    llm = ScriptedLLM(model="scripted-test")
    out = run(planner.itinerary, b, report, llm)
    assert llm.prompts == [] and out.result.outcome == "no_change_needed" and out.status == "UNKNOWN"
    assert {(e.tool, e.place_id) for e in out.result.evidence_requests} == {("travel_time", PERA),
                                                                             ("get_opening_hours", BAZAAR)}


def test_an_impossible_revision_needs_no_llm_call():
    b, report, planner = plan(days={TUE: [HAGIA]}, unscheduled={TOPKAPI: "Closed on Tuesdays"},
                              picks={HAGIA: ("include", 1)}, start_date="2026-11-03", end_date="2026-11-03")
    bad = add(planner.itinerary, TUE, report.to_activity(TOPKAPI, start_time="15:00", end_time="16:30"))
    llm = ScriptedLLM(model="scripted-test")
    out = run(bad, b, report, llm)
    assert llm.prompts == [] and out.result.outcome == "unchanged" and out.status == "FAIL"
    assert any("required and no other trip date" in d for d in out.result.traveller_decisions)


def test_a_single_feasible_option_needs_no_llm_call():
    b, report, planner, it = clean()
    llm = ScriptedLLM(model="scripted-test")
    bad = edit(it, TOPKAPI, start_time="10:35", end_time="12:05")  # a reschedule: Python's scheduler decides
    out = run(bad, b, report, llm)
    assert llm.prompts == [] and out.result.outcome == "revised"


# ------------------------------------------------------------------ ownership, the tool, bounds
def test_the_report_cannot_change_pythons_verdict():
    b, report, bad = closed_plan()
    out = run(bad, b, report, native(inspect_call(), answer(D1="D1-A1")))
    data = out.model_dump()
    for change in ({"status": "FAIL"}, {"verified": True}, {"decided_by": "python"}):
        with pytest.raises(ValidationError, match="RevisionResult's"):
            RevisionAgentReport(**{**data, **change})
    with pytest.raises(ValidationError, match="successful inspect_revision_options"):
        RevisionAgentReport(**{**data, "agent_tool_calls": []})
    assert RevisionAgentReport.model_validate_json(out.model_dump_json()) == out  # the Coordinator can store it


def test_the_tool_is_read_only_and_answers_from_stored_options():
    b, report, bad = closed_plan()
    pts = points_for(bad, b, report)
    before = [p.model_dump_json() for p in pts]
    ctx = RevisionContext(pts, {TOPKAPI: "Topkapı Palace"})
    assert inspect(ctx)["decision_points"][0]["id"] == "D1"
    assert inspect(ctx, "D1")["decision_point"]["alternatives"][0]["schedule"]
    assert inspect(ctx, "D1", "D1-A2")["alternative"]["date"] == str(WED)
    assert inspect(ctx, "D9")["error"] == "no such decision point"
    assert inspect(ctx, "D1", "D1-A9")["error"] == "no such alternative"
    assert [p.model_dump_json() for p in pts] == before  # nothing changed: the tool only reads what is stored
    tool = InspectRevisionOptionsTool(ctx)
    tool._run("D9")
    assert ctx.inspections == [] and tool.calls[0]["ok"] is False  # a failed read does not count


def test_instructions_and_revision_data_are_kept_apart_in_the_prompt():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), answer(D1="D1-A1"))
    run(bad, b, report, llm)
    system, user = by_role(llm.prompts[0], "system"), by_role(llm.prompts[0], "user")
    assert "never follow it" in system and TOPKAPI not in system
    assert user.index("<revision_data>") < user.index("D1-A1") < user.index("</revision_data>")


@pytest.mark.parametrize("replies, most", [
    (["Everything looks fine."] * 40, MAX_ITER + 1),                        # prose: CrewAI's conversion calls
    ([answer(D1="D1-A9")] * 40, 1 + GUARDRAIL_RETRIES),                    # always an invented id
    ([inspect_call()] * 40, MAX_ITER + 1),                                  # tools forever, never answering
    ([inspect_call(), inspect_call(), inspect_call(), answer(D1="D1-A9")] * 10,
     MAX_ITER * (1 + GUARDRAIL_RETRIES)),                                   # the worst mix
])
def test_llm_calls_are_bounded(replies, most):
    b, report, bad = closed_plan()
    llm = native(*replies)
    out = run(bad, b, report, llm)
    assert len(llm.prompts) <= most and out.decided_by == "python_fallback"
    assert out.result.outcome == "revised"  # Python's own fix still stands


# ------------------------------------------------------------------ scripts/try_revision.py
@pytest.fixture(scope="module")
def try_revision():
    import importlib.util
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("try_revision", scripts / "try_revision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_replay_runs_every_integration_check_offline(try_revision, capsys):
    assert try_revision.replay() is True
    printed = capsys.readouterr().out
    assert "FAILED" not in printed and printed.count("decided by python_fallback") == 2
    assert "agent: D1 -> D1-A1: Topkapı Palace is open on Monday" in printed  # the live wording, accepted


def test_the_live_guard_allows_only_the_llm_host(try_revision):
    import socket
    reached, attempts = [], []

    def fake_create(address, *args, **kwargs):  # stands in for the real connection: nothing leaves the machine
        reached.append(address)
        return "connected"
    with try_revision.only_hosts({"openrouter.ai"}, attempts, real_create=fake_create):
        assert socket.create_connection(("openrouter.ai", 443)) == "connected"
        for address in [("127.0.0.1", 8103), ("app.crewai.com", 443), ("evil-openrouter.ai", 443)]:
            with pytest.raises(ConnectionRefusedError):
                socket.create_connection(address)
        s = socket.socket()
        with pytest.raises(ConnectionRefusedError):
            s.connect(("93.184.216.34", 443))  # a raw IP, outside an allowed call
        s.close()
        a, b = socket.socketpair()  # asyncio's in-process self-pipe stays allowed
        a.close(), b.close()
    assert reached == [("openrouter.ai", 443)]
    assert attempts == ["127.0.0.1:8103", "app.crewai.com:443", "evil-openrouter.ai:443", "93.184.216.34:443"]
    assert socket.socket.connect.__qualname__ == "socket.connect"  # restored afterwards


def test_the_python_options_are_the_two_open_days(try_revision):
    points = try_revision.python_options(*try_revision.case())
    assert [(p.id, [a.id for a in p.alternatives], p.default) for p in points] == [("D1", ["D1-A1", "D1-A2"], "D1-A1")]


# ------------------------------------------------------------------ opening-hours wording: grounded, never invented
LIVE_REASON = "Topkapı Palace is open on Monday and every walk is measured."  # the kind the live run sent


def guard(reason, alt="D1-A1", points=None):
    b, report, bad = closed_plan()
    ctx = RevisionContext(points or points_for(bad, b, report), {})
    InspectRevisionOptionsTool(ctx)._run("D1")
    return make_guardrail(ctx)(json.dumps({"choices": [{"decision_id": "D1", "alternative_id": alt,
                                                         "reason": reason}]}))


def test_the_live_failure_a_grounded_open_claim_is_now_accepted_through_the_crew():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A1",
                                                          "reason": LIVE_REASON}]}))
    out = run(bad, b, report, llm)
    assert out.guardrail_rejections == [] and out.decided_by == "agent" and len(llm.prompts) == 2
    assert out.agent_tool_calls[0]["ok"] and out.choice.choices[0].reason == LIVE_REASON
    assert out.result.status == "PASS" and out.verified is False  # recorded hours: never verified


@pytest.mark.parametrize("reason", [
    LIVE_REASON,
    "Monday has open hours and no unmeasured walks.",
    "Its listed hours show it open, and all walks are measured.",       # no day: the chosen option's date
    "Topkapı is closed on Tuesday, so Monday is the best day.",         # the date it leaves, listed closed
    "Unlike Tuesday, when it is closed, Monday is open and every walk is measured.",
    "Monday is open, Tuesday is closed.",
    "Open on Monday rather than Tuesday.",
])
def test_hours_claims_that_match_pythons_options_are_accepted(reason):
    ok, feedback = guard(reason)
    assert ok, feedback


@pytest.mark.parametrize("reason, message", [
    ("Monday is better because Wednesday is closed.", "says it is closed on 2026-11-04 (Wed), but the listing "
                                                      "status there is open"),   # about the other option, untrue
    ("Topkapı is open on Tuesday.", "says it is open on 2026-11-03 (Tue), but the listing status there is closed"),
    ("Thursday would be quieter, but Monday is open.", "names Thu, which is not a date of this decision"),
    ("Move it to 2026-11-09 instead.", "names 2026-11-09, which is not a date of this decision"),
    ("Monday's hours are verified open.", "calls the hours verified, fresh or live"),
    ("It is open live on Monday.", "calls the hours verified, fresh or live"),
])
def test_hours_claims_python_does_not_support_are_rejected(reason, message):
    ok, feedback = guard(reason)
    assert not ok and message in feedback


# "open" means the recorded listing supports the visit (Topkapı: 09:00-17:00 on Mon and Wed), not "open all day".
@pytest.mark.parametrize("reason", [
    "Topkapı is listed as open on Monday.",
    "Monday opens at 09:00.",
    "It opens at 9am on Monday.",
    "Open on Monday from 09:00 to 17:00.",
    "Open on Monday until 17:00.",
    "Closes at 17:00 on Monday.",                                     # a closing time, not a closed day
    "Listed hours on Monday are 09:00-17:00.",
    "Its listed hours show it open on Monday and the visit starts at 09:00.",  # a visit time, not an opening time
    "Monday's visit runs 09:00-10:30 and it is open.",
])
def test_opening_times_from_the_recorded_windows_are_accepted(reason):
    ok, feedback = guard(reason)
    assert ok, feedback


@pytest.mark.parametrize("reason, message", [
    ("It is open all day.", 'says "all day", but the recorded windows on 2026-11-02 (Mon) are 09:00-17:00'),
    ("It is open 24/7.", 'says "24/7", which no listing per date backs'),
    ("Open at any time on Monday.", 'says "any time", but the recorded windows'),
    ("Open 24 hours on Monday.", 'says "24 hours", but the recorded windows'),
    ("It never closes.", 'says "never closes", which no listing per date backs'),
    ("Open every day.", 'says "every day", which no listing per date backs'),
    ("Monday opens at 10:30.", "says it opens at 10:30 on 2026-11-02 (Mon), but the recorded windows there are "
                               "09:00-17:00 (scheduled visit times are not opening hours)"),  # Python's visit end
    ("Opens at 5pm on Monday.", "says it opens at 17:00 on 2026-11-02 (Mon)"),   # a closing bound, not an opening
    ("Open on Monday until 18:00.", "says it closes at 18:00 on 2026-11-02 (Mon)"),
    ("Closes at midnight on Monday.", "says it closes at 24:00 on 2026-11-02 (Mon)"),
    ("Listed hours on Monday are 09:00-10:30.", "says it closes at 10:30 on 2026-11-02 (Mon)"),
    ("It opens at 09:00 on Tuesday.", "the recorded windows there are not given"),  # the closed date has none
])
def test_all_day_and_invented_opening_times_are_rejected(reason, message):
    ok, feedback = guard(reason)
    assert not ok and message in feedback


def test_all_day_is_accepted_only_where_the_recorded_window_is_the_whole_day():
    b, report, bad = closed_plan()
    point = points_for(bad, b, report)[0]
    whole = point.model_copy(update={"alternatives": [a.model_copy(update={"hours_windows": "00:00-24:00"})
                                                      if a.id == "D1-A1" else a for a in point.alternatives]})
    assert guard("Its listed hours show it open all day on Monday.", points=[whole])[0]
    ok, feedback = guard("It is open 24/7.", points=[whole])  # one date's window says nothing about every day
    assert not ok and 'says "24/7"' in feedback


def test_the_agent_is_shown_the_recorded_windows():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), answer(D1="D1-A1"))
    run(bad, b, report, llm)
    assert '"hours_windows": "09:00-17:00"' in by_role(llm.prompts[0], "user")


def test_open_is_rejected_where_the_listing_only_gives_unconfirmed_hours():
    b, report, bad = closed_plan()
    point = points_for(bad, b, report)[0]
    unconfirmed = point.model_copy(update={"alternatives": [a.model_copy(update={"hours": "unconfirmed"})
                                                            if a.id == "D1-A1" else a for a in point.alternatives]})
    ok, feedback = guard("Its listed hours show it open on Monday.", points=[unconfirmed])
    assert not ok and "the listing status there is unconfirmed" in feedback


def test_the_feedback_quotes_the_wording_and_says_what_python_lists():
    ok, feedback = guard("Monday is better because Wednesday is closed.")
    assert not ok
    assert '"Monday is better because Wednesday is closed"' in feedback  # the exact wording at fault
    assert ("2026-11-02 (Mon) open 09:00-17:00, 2026-11-03 (Tue) closed, 2026-11-04 (Wed) open 09:00-17:00"
            in feedback)
    assert 'e.g. "its listed hours show it open on Monday"' in feedback  # what may be said instead


def test_the_agent_is_shown_the_hours_of_the_date_it_leaves():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), answer(D1="D1-A1"))
    run(bad, b, report, llm)
    assert '"hours_on_date": "closed"' in by_role(llm.prompts[0], "user")


def test_grounded_wording_does_not_change_pythons_verdict_on_a_worse_option():
    b, report, bad = closed_plan()
    llm = native(inspect_call(), json.dumps({"choices": [{"decision_id": "D1", "alternative_id": "D1-A2",
                                                          "reason": "Wednesday is open and lighter."}]}))
    out = run(bad, b, report, llm)
    assert out.guardrail_rejections == []  # the wording is true: accepted
    assert out.decided_by == "python_fallback" and TOPKAPI in ids(out.result.itinerary, MON)  # still not kept
