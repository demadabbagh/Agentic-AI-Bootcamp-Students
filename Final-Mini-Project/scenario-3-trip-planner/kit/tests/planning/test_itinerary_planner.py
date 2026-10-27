"""Tests for planning/itinerary_planner.py. No live service and no paid LLM call.

The Budget Analyst's report is built in Python from the recorded research (tests/fixtures); travel times replay
the recorded travel_time results. The CrewAI tests run the real Agent, Task, check_day tool and guardrail with
the scripted test LLMs in scripted_llm.py. Trip: Istanbul, Mon 2026-11-02 to Wed 2026-11-04, 2 travellers,
700 USD, Topkapı Palace required (closed on Tuesdays).
"""
import datetime as dt
import json
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")  # keep CrewAI's local files away from real runs

import pytest

from planning.budget_analyst import BudgetDecision, PlaceDecision, apply_decision, prepare_candidates
from planning.itinerary_planner import (AGENT_BACKSTORY, DEFAULT_MAX_LOOKUPS, NOT_VERIFIED, CheckDayTool,
                                        DayAssignment, DayChoice, InvalidAssignment, ItineraryPlannerError,
                                        PlanningContext, UnscheduledChoice, build_itinerary, build_planner_agent,
                                        build_planner_task, check_day, make_guardrail, parse_assignment,
                                        plan_itinerary, planner_trace, prompt_inputs, validate_assignment)
from planning.models import WEEKDAYS
from planning.schedule import ScheduleRules, to_minutes, validate_day
from research_fixtures import bundle, recorded_lookup
from scripted_llm import (NativeScriptedLLM, ScriptedLLM, by_role, native_tool_call, react_action, react_final,
                          text)


def I(slug):  # noqa: E743 - a place id of the Istanbul fixture
    return f"wv-istanbul-{slug}"


TOPKAPI, HAGIA, CISTERN, SULEYMANIYE = I("topkapi-palace"), I("hagia-sophia"), I("basilica-cistern"), I("suleymaniye-mosque")
DOLMABAHCE, PERA, ALI_USTA, BAZAAR = I("dolmabahce-palace"), I("pera-museum"), I("ali-usta-cig-kofte"), I("grand-bazaar")
GALATA, HAMAM, SYNTHETIC = I("galata-tower"), I("hurrem-sultan-hamami"), "synthetic-istanbul-rihla-test-kitchen"
MON, TUE, WED = "2026-11-02", "2026-11-03", "2026-11-04"
SELECTED = [TOPKAPI, HAGIA, CISTERN, SULEYMANIYE, DOLMABAHCE, PERA, ALI_USTA, BAZAAR]
PICKS = {HAGIA: ("include", 1), CISTERN: ("include", 2), SULEYMANIYE: ("include", 3), DOLMABAHCE: ("include", 4),
         PERA: ("include", 5), ALI_USTA: ("include", 6), BAZAAR: ("include", 7), GALATA: ("optional", 1)}
VALID = {MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA], TUE: [SULEYMANIYE, BAZAAR], WED: [DOLMABAHCE, PERA]}


def budget_report(b, picks=PICKS):
    cands = prepare_candidates(b)
    return apply_decision(cands, BudgetDecision(decisions=[
        PlaceDecision(place_id=i, decision=picks.get(i, ("exclude", None))[0], rank=picks.get(i, ("exclude", None))[1])
        for i in cands.open_ids]))


def context(name="istanbul", picks=PICKS, rules=None, max_lookups=DEFAULT_MAX_LOOKUPS, **trip):
    b = bundle(name, **trip)
    return PlanningContext(budget_report(b, picks), b, recorded_lookup(name), rules=rules, max_lookups=max_lookups)


def assign(days: dict, unscheduled: dict | None = None, notes=(), reason="Close together") -> DayAssignment:
    return DayAssignment(days=[DayChoice(date=d, place_ids=ids, reason=reason) for d, ids in days.items()],
                         unscheduled=[UnscheduledChoice(place_id=i, reason=r) for i, r in (unscheduled or {}).items()],
                         notes=list(notes))


def acts(report, date):
    day = next(d for d in report.itinerary.days if str(d.date) == date)
    return [(a.place_id, a.start_time, a.end_time) for a in day.activities]


def no_overlaps(report):
    for day in report.schedule.days:
        assert validate_day(day) == []
    for day in report.itinerary.days:
        spans = [(to_minutes(a.start_time), to_minutes(a.end_time)) for a in day.activities]
        assert all(prev[1] <= nxt[0] for prev, nxt in zip(spans, spans[1:]))


# ------------------------------------------------------------------ the Python path: a valid plan
def test_valid_three_day_istanbul_plan():
    ctx = context()
    report = build_itinerary(ctx, assign(VALID))
    assert report.status == "complete" and report.unscheduled == []
    assert acts(report, MON) == [(HAGIA, "09:00", "10:30"), (TOPKAPI, "10:45", "12:15"), (CISTERN, "12:29", "13:59"),
                                 (ALI_USTA, "14:12", "15:12")]
    assert acts(report, TUE) == [(SULEYMANIYE, "09:00", "10:30"), (BAZAAR, "10:43", "12:13")]
    assert acts(report, WED) == [(DOLMABAHCE, "09:00", "10:30"), (PERA, "11:00", "12:30")]
    assert [str(d.date) for d in report.itinerary.days] == [MON, TUE, WED]
    no_overlaps(report)
    assert report.verified is False and report.warnings[-1] == NOT_VERIFIED


def test_every_selected_place_is_accounted_for_exactly_once():
    report = build_itinerary(context(), assign(VALID))
    assert sorted(report.scheduled_place_ids) == sorted(SELECTED)
    partial = build_itinerary(context(), assign({**VALID, WED: [DOLMABAHCE]}, {PERA: "Too far from the rest"}))
    accounted = partial.scheduled_place_ids + [u.place_id for u in partial.unscheduled]
    assert sorted(accounted) == sorted(SELECTED) and len(accounted) == len(set(accounted))


def test_costs_come_from_the_budget_analyst_and_the_budget_is_recalculated():
    ctx = context()
    report = build_itinerary(ctx, assign(VALID))
    for day in report.itinerary.days:
        for a in day.activities:
            item = ctx.report.item(a.place_id)
            assert a.estimated_cost == (None if item.cost.estimated is None else float(item.cost.estimated))
            assert a.source_url == item.source_url
    assert report.budget.known_total == ctx.report.known_total  # every selected place is scheduled
    assert report.budget.status == "undetermined"  # the Grand Bazaar has no price: never treated as free


def test_citations_cover_places_rates_and_measured_routes_only():
    report = build_itinerary(context(), assign({**VALID, WED: [DOLMABAHCE]}, {PERA: "Too far from the rest"}))
    ids = {c["id"] for c in report.citations}
    assert {TOPKAPI, HAGIA, "fx-ecb-2026-10-23", "osrm-foot-hagia-sophia-topkapi-palace"} <= ids
    assert PERA not in ids and GALATA not in ids  # not scheduled: not cited


def test_partial_plan_reports_the_lower_total():
    ctx = context()
    report = build_itinerary(ctx, assign({**VALID, WED: [DOLMABAHCE]}, {PERA: "Too far from the rest"}))
    assert report.status == "partial"
    assert [(u.place_id, u.decided_by) for u in report.unscheduled] == [(PERA, "agent")]
    assert report.budget.known_total == ctx.report.known_total - ctx.report.item(PERA).cost.estimated
    assert any(w.startswith("Pera Museum is not in the plan: Too far from the rest") for w in report.warnings)


# ------------------------------------------------------------------ closed days and required places
def test_topkapi_on_tuesday_is_rejected_with_the_dates_it_can_go_on():
    ctx = context()
    errors = validate_assignment(assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [TOPKAPI, SULEYMANIYE, BAZAAR],
                                         WED: [DOLMABAHCE, PERA]}), ctx)
    assert any("is closed on 2026-11-03 (Tue): delete it from 2026-11-03 and put it on exactly one of: "
               "2026-11-02, 2026-11-04" in e for e in errors)


def test_even_a_forced_tuesday_topkapi_is_never_scheduled():
    ctx = context()  # bypass the guardrail: build_itinerary validates again and refuses the closed day
    with pytest.raises(InvalidAssignment, match=r"closed on 2026-11-03 \(Tue\)"):
        build_itinerary(ctx, assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [TOPKAPI, SULEYMANIYE, BAZAAR],
                                     WED: [DOLMABAHCE, PERA]}))
    report = build_itinerary(ctx, assign(VALID))  # the valid plan puts it on Monday
    assert [WEEKDAYS[d.date.weekday()] for d in report.itinerary.days
            if TOPKAPI in [a.place_id for a in d.activities]] == ["Mon"]


def test_required_place_with_no_possible_date_is_reported_infeasible_not_invented():
    ctx = context(start_date=TUE, end_date=TUE)  # a one-day trip on Topkapı's closing day
    assert validate_assignment(assign({TUE: [TOPKAPI, SULEYMANIYE]}, {
        i: "No time" for i in SELECTED if i not in (TOPKAPI, SULEYMANIYE)}), ctx)  # closed: refused
    a = assign({TUE: [HAGIA, CISTERN, ALI_USTA]}, {TOPKAPI: "Closed on the only trip day",
                                                     **{i: "No time" for i in (SULEYMANIYE, DOLMABAHCE, PERA, BAZAAR)}})
    assert validate_assignment(a, ctx) == []
    report = build_itinerary(ctx, a)
    assert report.status == "infeasible" and TOPKAPI not in report.scheduled_place_ids
    topkapi = next(u for u in report.unscheduled if u.place_id == TOPKAPI)
    assert topkapi.required and topkapi.reason == "Closed on the only trip day (Python: closed on every trip date)"


def test_a_required_place_that_could_be_scheduled_may_not_be_left_out():
    errors = validate_assignment(assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [SULEYMANIYE, BAZAAR],
                                         WED: [DOLMABAHCE, PERA]}, {TOPKAPI: "Too expensive"}), context())
    assert any("was asked for by the traveller: delete it from unscheduled and put it on exactly one of: "
               "2026-11-02, 2026-11-04" in e for e in errors)


def test_places_that_do_not_fit_are_rejected_by_the_guardrail_and_reported_by_the_build():
    ctx = context(rules=ScheduleRules(day_end="12:00"))
    errors = validate_assignment(assign(VALID), ctx)
    assert any(e.startswith(f"{CISTERN} does not fit on 2026-11-02") for e in errors)
    report = build_itinerary(ctx, assign(VALID))  # forced: Python reports, never squeezes
    assert report.status == "infeasible"  # Topkapı (required) ends 12:15, after the 12:00 planning day
    assert TOPKAPI in [u.place_id for u in report.unscheduled]
    assert all(u.decided_by == "scheduler" and "does not fit" in u.reason for u in report.unscheduled)
    no_overlaps(report)


# ------------------------------------------------------------------ uncertain data
def test_missing_opening_hours_are_scheduled_tentatively_and_flagged():
    amman = {"wv-amman-amman-citadel": ("include", 1), "wv-amman-roman-theater": ("include", 2),
             "wv-amman-the-jordan-museum": ("include", 3)}
    ctx = context("amman", picks=amman)  # Tue 2026-11-03 and Wed; the Jordan Museum is closed on Tuesdays
    a = assign({TUE: ["wv-amman-amman-citadel", "wv-amman-roman-theater"], WED: ["wv-amman-the-jordan-museum"]})
    report = build_itinerary(ctx, a)
    assert report.status == "complete" and report.uncertain
    assert report.hours_uncertain == ["wv-amman-amman-citadel", "wv-amman-roman-theater"]
    assert sum("opening hours unknown" in w for w in report.warnings) == 2
    citadel = report.itinerary.days[0].activities[0]
    assert "Opening hours unknown: check before you go" in citadel.notes
    assert next(e for e in report.evidence if e.place_id == "wv-amman-amman-citadel").hours_status == "unknown"


def test_missing_travel_time_uses_the_unverified_buffer():
    report = build_itinerary(context(max_walk_minutes=25), assign(VALID))
    assert report.travel_uncertain == [f"{WED}: {DOLMABAHCE} -> {PERA}"]
    pera = next(e for e in report.evidence if e.place_id == PERA)
    assert (pera.travel_status, pera.travel_minutes, pera.buffer_minutes) == ("unavailable", None, 30)
    activity = report.itinerary.days[2].activities[1]
    assert activity.travel_minutes_from_previous is None and "30-minute planning buffer" in activity.notes
    assert any("cannot be checked against the 25-minute limit" in w for w in report.walking_warnings)
    assert report.status == "complete" and report.uncertain  # completeness and uncertainty stay separate


def test_walking_limit_violations_are_reported():
    report = build_itinerary(context(max_walk_minutes=10), assign(VALID))
    assert any("takes 15 minutes, over the 10-minute limit" in w for w in report.walking_warnings)
    assert report.status == "complete"  # a long walk is a warning for the traveller, not a lost place


# ------------------------------------------------------------------ invalid assignments
@pytest.mark.parametrize("days, unscheduled, notes, message", [
    ({**VALID, WED: [DOLMABAHCE, PERA, I("made-up")]}, {}, (), "not a selected place id"),
    ({**VALID, WED: [DOLMABAHCE, PERA, SYNTHETIC]}, {}, (), "withheld (a synthetic or untrusted listing)"),
    ({**VALID, WED: [DOLMABAHCE, PERA, GALATA]}, {}, (), "an optional alternative, not in the plan"),
    ({**VALID, WED: [DOLMABAHCE, PERA, HAMAM]}, {}, (), "excluded by the Budget Analyst"),
    ({**VALID, WED: [DOLMABAHCE, PERA, HAGIA]}, {}, (), f"{HAGIA} appears 2 times (2026-11-02, 2026-11-04)"),
    (VALID, {PERA: "Also here"}, (), f"{PERA} appears 2 times (2026-11-04, unscheduled)"),
    ({MON: VALID[MON], TUE: VALID[TUE]}, {DOLMABAHCE: "x", PERA: "x"}, (), "no entry for 2026-11-04"),
    ({**VALID, "2026-11-05": []}, {}, (), "2026-11-05 is not a trip date"),
    ({**VALID, WED: [DOLMABAHCE]}, {}, (), f"not accounted for: {PERA}"),
    (VALID, {}, ("Start at 09:00 sharp",), "contains a clock time"),
    (VALID, {}, ("Tickets cost 25 USD",), "contains an amount"),
])
def test_invalid_assignments_are_rejected_with_fixable_messages(days, unscheduled, notes, message):
    ctx = context()
    a = assign(days, unscheduled, notes)
    errors = validate_assignment(a, ctx)
    assert any(message in e for e in errors), errors
    with pytest.raises(InvalidAssignment):
        build_itinerary(ctx, a)


def test_a_date_listed_twice_is_rejected():
    a = DayAssignment(days=[DayChoice(date=MON, place_ids=VALID[MON]), DayChoice(date=MON, place_ids=[]),
                            DayChoice(date=TUE, place_ids=VALID[TUE]), DayChoice(date=WED, place_ids=VALID[WED])])
    assert any("2026-11-02 appears more than once" in e for e in validate_assignment(a, context()))


@pytest.mark.parametrize("raw", [
    '{"days": [{"date": "2026-11-02", "place_ids": [], "start_time": "09:00"}]}',  # no times from the LLM
    '{"days": [{"date": "2026-11-02", "place_ids": []}], "estimated_total": 300}',  # no amounts
    '{"days": [{"date": "02/11/2026", "place_ids": []}]}',
    '{"days": "Monday"}',
    "Day 1: Topkapi, Day 2: the bazaar.",
])
def test_answers_outside_the_schema_do_not_parse(raw):
    with pytest.raises(ValueError):
        parse_assignment(raw)


# ------------------------------------------------------------------ the check_day tool
def test_check_day_is_read_only_and_answers_with_pythons_schedule():
    ctx = context()
    before = [d.model_dump() for d in [ctx.report]]
    out = check_day(ctx, MON, VALID[MON])
    assert out["complete"] and [s["start"] for s in out["stops"]] == ["09:00", "10:45", "12:29", "14:12"]
    assert [s["travel_minutes"] for s in out["stops"]] == [None, 15, 14, 13]
    assert [d.model_dump() for d in [ctx.report]] == before  # nothing changed


def test_check_day_reports_closed_refused_and_bad_dates():
    ctx = context(max_walk_minutes=10)
    out = check_day(ctx, TUE, [TOPKAPI, GALATA, HAGIA])
    assert out["errors"] == [{"code": "closed_on_date", "place_id": TOPKAPI}] and not out["complete"]
    assert out["not_planned"] == {GALATA: "an optional alternative, not in the plan"}
    walk = check_day(ctx, MON, [HAGIA, TOPKAPI])["warnings"]
    assert {"code": "walk_over_limit", "place_id": TOPKAPI, "walk_minutes": 15, "limit": 10} in walk
    assert check_day(ctx, "2026-11-09", [HAGIA])["error"] == "not a trip date"
    assert check_day(ctx, "Monday", [HAGIA])["error"] == "not a date: use YYYY-MM-DD"
    assert "Hagia" not in json.dumps(out)  # ids and codes only: no listing text goes back to the model


def test_travel_lookups_are_cached_and_capped_per_request():
    ctx = context(max_lookups=3)
    check_day(ctx, MON, VALID[MON])
    check_day(ctx, MON, VALID[MON])  # the same legs again: from the cache
    assert len(ctx.travel.calls) == 3
    report = build_itinerary(ctx, assign(VALID))
    assert len(report.travel_lookups) == 3  # the cap held: later legs were not looked up
    skipped = [e for e in report.evidence if e.travel_status == "not_looked_up"]
    assert skipped and all(e.travel_minutes is None and e.buffer_minutes == 30 for e in skipped)
    assert DEFAULT_MAX_LOOKUPS == 20


# ------------------------------------------------------------------ prompt hygiene
def test_prompt_lists_only_selected_places_without_descriptions():
    ctx = context()
    data = prompt_inputs(ctx)["research_data"]
    rows = json.loads(data)
    assert [r["id"] for r in rows] == SELECTED
    assert GALATA not in data and HAMAM not in data and SYNTHETIC not in data
    descriptions = [p.description for p in bundle().places if p.description]
    assert not any(d[:60] in data for d in descriptions)
    topkapi = next(r for r in rows if r["id"] == TOPKAPI)
    assert topkapi["required"] and topkapi["hours_by_date"] == {MON: "open 09:00-17:00", TUE: "closed",
                                                                WED: "open 09:00-17:00"}


# ------------------------------------------------------------------ the CrewAI agent and task, offline
def test_agent_and_task_are_wired_for_structured_output_with_a_read_only_tool():
    ctx = context()
    tool = CheckDayTool(ctx)
    agent = build_planner_agent(ScriptedLLM(model="scripted-test"), tool)
    task = build_planner_task(agent, ctx)
    assert agent.role == "Itinerary Planner" and not agent.allow_delegation and agent.max_iter == 8
    assert [t.name for t in agent.tools] == ["check_day"]
    assert task.output_pydantic is DayAssignment and task.guardrail is not None and task.guardrail_max_retries == 2


def test_native_function_calling_run():
    b = bundle()
    report_in = budget_report(b)
    final = assign(VALID).model_dump_json()
    llm = NativeScriptedLLM(model="scripted-test", replies=[native_tool_call("check_day", {"date": MON,
                                                                                           "place_ids": VALID[MON]}), final])
    report = plan_itinerary(report_in, b, llm, recorded_lookup())
    assert len(llm.prompts) == 2 and llm.prompts[1][-1]["role"] == "tool"
    assert '"start": "10:45"' in llm.prompts[1][-1]["content"]  # the agent saw Python's times
    assert report.status == "complete"
    assert report.agent_tool_calls[0] | {"ms": 0} == {"tool": "check_day", "ok": True, "ms": 0, "date": MON,
                                                       "place_ids": VALID[MON], "complete": True}
    assert [s["step"] for s in report.agent_trace] == ["tool_call", "tool_result", "final_answer"]
    assert report.agent_trace[1]["scheduled"] == VALID[MON]
    assert set(report.llm_usage) == {"successful_requests", "prompt_tokens", "completion_tokens", "total_tokens"}
    assert {c["tool"] for c in report.tool_calls} == {"check_day", "travel_time"}


def test_text_tool_calling_run():
    b = bundle()
    llm = ScriptedLLM(model="scripted-test", replies=[react_action("check_day", {"date": TUE, "place_ids": [TOPKAPI]}),
                                                      react_final(assign(VALID))])
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert '"code": "closed_on_date"' in text(llm.prompts[1])  # Python told the agent Topkapı is closed on Tuesday
    assert report.status == "complete" and TOPKAPI in [a for a, *_ in acts(report, MON)]
    assert [s["step"] for s in report.agent_trace] == ["tool_call", "tool_result"]  # ReAct keeps no final message


def test_the_final_assignment_is_validated_independently_of_the_checks():
    b = bundle()
    llm = NativeScriptedLLM(model="scripted-test", replies=[
        native_tool_call("check_day", {"date": MON, "place_ids": [HAGIA]}),  # checked one small day only
        assign(VALID).model_dump_json()])  # then answered a full plan it never checked
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert report.agent_tool_calls[0]["place_ids"] == [HAGIA]
    assert acts(report, MON)[1] == (TOPKAPI, "10:45", "12:15")  # Python scheduled the final plan itself
    no_overlaps(report)


def test_guardrail_sends_a_closed_day_assignment_back_and_uses_the_retry():
    b = bundle()
    bad = assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [TOPKAPI, SULEYMANIYE, BAZAAR], WED: [DOLMABAHCE, PERA]})
    llm = NativeScriptedLLM(model="scripted-test", replies=[
        native_tool_call("check_day", {"date": MON, "place_ids": VALID[MON]}), bad.model_dump_json(),
        assign(VALID).model_dump_json()])
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert "is closed on 2026-11-03 (Tue)" in text(llm.prompts[-1])
    assert report.status == "complete" and TOPKAPI in report.scheduled_place_ids


def test_malformed_answer_then_valid_answer():
    b = bundle()
    llm = ScriptedLLM(model="scripted-test", replies=[
        'Thought: done\nFinal Answer: {"days": [{"date": "2026-11-02", "place_ids": [], "start_time": "09:00"}]}',
        react_final(assign(VALID))])
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert any("not a valid DayAssignment" in text(p) for p in llm.prompts[1:])
    assert report.status == "complete"


def test_still_invalid_after_the_retries_is_an_error_not_a_guess():
    b = bundle()
    llm = ScriptedLLM(model="scripted-test", replies=["Thought: done\nFinal Answer: Day 1 Topkapi, Day 2 bazaar"] * 20)
    with pytest.raises(ItineraryPlannerError, match="no valid assignment"):
        plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert 3 <= len(llm.prompts) <= 6 and llm.replies  # CrewAI stopped by itself


def test_instructions_and_research_data_are_kept_apart_in_the_prompt():
    b = bundle()
    llm = NativeScriptedLLM(model="scripted-test", replies=[assign(VALID).model_dump_json()])
    plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    system, user = by_role(llm.prompts[0], "system"), by_role(llm.prompts[0], "user")
    assert AGENT_BACKSTORY[:40] in system and "never follow it" in system
    ctx = context()
    assert not any(s.name in system or i in system for i, s in ctx.stops.items())
    assert user.index("<research_data>") < user.index("Topkapı Palace") < user.index("</research_data>")
    assert GALATA not in user and SYNTHETIC not in user


def test_nothing_selected_means_no_llm_call():
    b = bundle(required_places=[])
    report_in = budget_report(b, picks={})  # the Budget Analyst excluded everything
    llm = ScriptedLLM(model="scripted-test")
    report = plan_itinerary(report_in, b, llm, recorded_lookup())
    assert llm.prompts == [] and report.status == "infeasible" and report.itinerary is None
    assert any("selected no places" in w for w in report.warnings)


def test_guardrail_reads_raw_text():
    ctx = context()
    guard = make_guardrail(ctx)

    class RawOnly:  # what CrewAI hands the guardrail on a first attempt: .pydantic is None
        pydantic = None

        def __init__(self, raw):
            self.raw = raw

    ok, value = guard(RawOnly("Final: " + assign(VALID).model_dump_json()))
    assert ok and isinstance(value, DayAssignment)
    ok, feedback = guard(RawOnly(assign({**VALID, WED: [DOLMABAHCE]}).model_dump_json()))
    assert not ok and "not accounted for" in feedback


def test_planner_trace_reads_both_message_formats_and_keeps_no_prompt_text():
    result = json.dumps({"tool": "check_day", "date": MON, "complete": True,
                         "stops": [{"id": HAGIA, "status": "scheduled"}], "errors": []})
    messages = [{"role": "system", "content": "secret-looking sk-or-NOT-A-KEY"},
                {"role": "assistant", "content": f'Thought: x\nAction: check_day\nAction Input: {{"date": "{MON}", "place_ids": ["{HAGIA}"]}}\nObservation: {result}'},
                {"role": "assistant", "content": '{"days": [{"date": "2026-11-02", "place_ids": []}]}'}]
    steps = planner_trace(messages)
    assert steps == [{"step": "tool_call", "tool": "check_day", "date": MON, "place_ids": [HAGIA]},
                     {"step": "tool_result", "tool": "check_day", "date": MON, "complete": True,
                      "scheduled": [HAGIA], "errors": []},
                     {"step": "final_answer"}]
    assert "sk-or-" not in json.dumps(steps) and planner_trace(None) == []


# ------------------------------------------------------------------ regression: the first live run's duplicates
# The live run (OpenRouter, gpt-4.1-mini) failed after 2 retries with exactly these duplicates. CrewAI sends the
# guardrail's message together with the previous answer, and the old messages only said where a place "can go".
LIVE_FAILURE = {MON: [HAGIA, TOPKAPI, CISTERN, SULEYMANIYE, ALI_USTA],
                TUE: [SULEYMANIYE, BAZAAR, DOLMABAHCE, PERA, ALI_USTA],
                WED: [HAGIA, CISTERN, DOLMABAHCE, PERA, ALI_USTA]}
LIVE_FAILURE_UNSCHEDULED = {TOPKAPI: "Could not fit", BAZAAR: "Could not fit"}


def live_failure(drop=()):
    days = {d: [i for i in ids if i not in drop] for d, ids in LIVE_FAILURE.items()}
    return assign(days, {i: r for i, r in LIVE_FAILURE_UNSCHEDULED.items() if i not in drop})


def test_the_live_failure_is_reported_once_per_place_with_every_placement():
    from planning.itinerary_planner import assignment_problems
    problems = assignment_problems(live_failure(), context())
    assert {p.kind for p in problems} == {"duplicate"}
    by_place = {p.place_id: p.text for p in problems}
    assert len(problems) == len(by_place) == 8  # grouped: one problem per place, not one per extra copy
    assert by_place[ALI_USTA] == (f"{ALI_USTA} appears 3 times (2026-11-02, 2026-11-03, 2026-11-04): "
                                  "keep exactly one and delete the others")
    assert by_place[TOPKAPI].startswith(f"{TOPKAPI} appears 2 times (2026-11-02, unscheduled)")
    assert by_place[DOLMABAHCE].startswith(f"{DOLMABAHCE} appears 2 times (2026-11-03, 2026-11-04)")


def test_a_place_repeated_within_one_date_is_a_duplicate():
    errors = validate_assignment(assign({**VALID, MON: [HAGIA, HAGIA, TOPKAPI, CISTERN, ALI_USTA]}), context())
    assert f"{HAGIA} appears 2 times (2026-11-02, 2026-11-02): keep exactly one and delete the others" in errors


def test_feedback_states_the_rule_and_what_to_delete():
    from planning.itinerary_planner import ONE_TIME_RULE, assignment_problems, feedback
    message = feedback(assignment_problems(live_failure(drop=(BAZAAR,)), context()))
    lines = message.splitlines()
    assert lines[0] == ONE_TIME_RULE and "deleting it from where it was" in lines[0]
    assert lines[1] == "Fix these problems, then return the complete corrected JSON:"
    assert lines[-1] == "Before answering, check that every selected id appears exactly once."
    duplicates = [line for line in lines if line.startswith("- ") and " appears " in line]
    assert len(duplicates) == 7 and all("delete the others" in line for line in duplicates)
    assert any(line.startswith(f"- not accounted for: {BAZAAR}: add each exactly once") for line in lines)
    assert lines.index(duplicates[0]) < lines.index(next(line for line in lines if "not accounted for" in line))


def test_closed_day_feedback_says_delete_and_exactly_one_date():
    errors = validate_assignment(assign({MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA, DOLMABAHCE, PERA],
                                         TUE: [SULEYMANIYE, BAZAAR], WED: []}), context())
    assert (f"{DOLMABAHCE} is closed on 2026-11-02 (Mon): delete it from 2026-11-02 and put it on exactly one of: "
            "2026-11-03, 2026-11-04") in errors


def test_duplicates_only_are_repaired_from_the_agents_own_placements_and_validated():
    from planning.itinerary_planner import _placements, assignment_problems, repair_duplicates
    ctx = context()
    original = live_failure()
    fixed, notes = repair_duplicates(original, ctx)
    assert assignment_problems(fixed, ctx) == []  # the full validation, scheduling included
    kept = {i: str(d.date) for d in fixed.days for i in d.place_ids}
    assert kept == {HAGIA: MON, TOPKAPI: MON, CISTERN: MON, SULEYMANIYE: TUE, ALI_USTA: TUE,
                    BAZAAR: TUE, DOLMABAHCE: WED, PERA: WED}  # 3/3/2: the best-balanced of the agent's options
    assert fixed.unscheduled == []  # Topkapı and the bazaar kept on their dates, not dropped
    originals = _placements(original)
    assert all(kept[i] in originals[i] for i in kept)  # nothing invented: every kept date was the agent's
    assert sum(": kept " in n for n in notes) == 8
    assert f"{ALI_USTA}: kept on 2026-11-03, deleted the extra placement(s) (2026-11-02, 2026-11-04)" in notes
    assert notes[-1].startswith("chose the least travel among")  # says what was compared, never "optimal"


def test_repair_never_keeps_a_closed_date():
    from planning.itinerary_planner import repair_duplicates
    ctx = context()
    a = assign({MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA, DOLMABAHCE], TUE: [SULEYMANIYE, BAZAAR, DOLMABAHCE],
                WED: [PERA]})  # Dolmabahçe on its closed Monday and on Tuesday
    guard = make_guardrail(ctx)
    ok, accepted = guard(a.model_dump_json())
    assert ok and DOLMABAHCE not in accepted.days[0].place_ids and DOLMABAHCE in accepted.days[1].place_ids
    assert ctx.repairs[0] == f"{DOLMABAHCE}: kept on 2026-11-03, deleted the extra placement(s) (2026-11-02)"
    # Monday was also reordered: the same places, with Ali Usta at lunch instead of mid-afternoon (reported).
    reorder = next(n for n in ctx.repairs if n.startswith("reordered 2026-11-02"))
    assert sorted(accepted.days[0].place_ids) == sorted([HAGIA, TOPKAPI, CISTERN, ALI_USTA]) and "less travel" in reorder
    # Listed twice, but only on its closed Tuesday: none of the agent's placements may be kept, so no repair.
    only_closed = assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [TOPKAPI, TOPKAPI, SULEYMANIYE, BAZAAR],
                          WED: [DOLMABAHCE, PERA]})
    assert repair_duplicates(only_closed, ctx) is None


def test_repair_never_drops_a_required_place():
    ctx = context()  # Topkapı on its closed Tuesday and in unscheduled: keeping "unscheduled" would drop it
    a = assign({MON: [HAGIA, CISTERN, ALI_USTA], TUE: [TOPKAPI, SULEYMANIYE, BAZAAR], WED: [DOLMABAHCE, PERA]},
               {TOPKAPI: "Closed"})
    ok, message = make_guardrail(ctx)(a.model_dump_json())
    assert not ok and ctx.repairs == []
    assert f"{TOPKAPI} appears 2 times (2026-11-03, unscheduled)" in message
    assert "delete it from 2026-11-03 and put it on exactly one of: 2026-11-02, 2026-11-04" in message


def test_a_repair_that_fails_scheduling_is_not_accepted():
    ctx = context(rules=ScheduleRules(day_end="13:00"))  # the repaired Monday would not fit
    ok, message = make_guardrail(ctx)(live_failure().model_dump_json())
    assert not ok and ctx.repairs == [] and message.startswith("ONE-TIME RULE")


def test_build_itinerary_itself_never_repairs():
    with pytest.raises(InvalidAssignment, match="appears 3 times"):
        build_itinerary(context(), live_failure())


def test_the_prompt_states_the_rule_with_a_valid_example():
    from planning.itinerary_planner import TASK_DESCRIPTION
    assert "ONE-TIME RULE" in TASK_DESCRIPTION and "Moving a place means deleting it" in TASK_DESCRIPTION
    example = next(line for line in TASK_DESCRIPTION.splitlines() if line.startswith('{"days"'))
    parsed = DayAssignment.model_validate_json(example)
    ids = [i for d in parsed.days for i in d.place_ids] + [u.place_id for u in parsed.unscheduled]
    assert len(ids) == len(set(ids)) == 4 and not any(i.startswith("wv-") for i in ids)  # made-up ids
    inputs = prompt_inputs(context())
    assert inputs["place_count"] == "8" and '"hours_by_date"' in inputs["research_data"]


@pytest.mark.parametrize("native", [True, False])
def test_live_failure_pattern_is_repaired_on_the_first_answer(native):
    b = bundle()
    first = live_failure().model_dump_json()
    llm = (NativeScriptedLLM(model="scripted-test", replies=[first]) if native else
           ScriptedLLM(model="scripted-test", replies=[f"Thought: done\nFinal Answer: {first}"]))
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert len(llm.prompts) == 1  # no retry needed, no extra cost
    assert report.status == "complete" and sum(": kept " in n for n in report.repairs) == 8
    assert any(w.startswith("The planner listed some places more than once") for w in report.warnings)
    ids = [i for d in report.assignment.days for i in d.place_ids]
    assert len(ids) == len(set(ids)) == 8 and TOPKAPI in ids  # the report keeps the repaired assignment
    no_overlaps(report)


@pytest.mark.parametrize("native", [True, False])
def test_unrepairable_answer_gets_grouped_feedback_and_the_corrected_answer_is_used(native):
    b = bundle()
    bad, good = live_failure(drop=(BAZAAR,)).model_dump_json(), assign(VALID).model_dump_json()
    llm = (NativeScriptedLLM(model="scripted-test", replies=[bad, good]) if native else
           ScriptedLLM(model="scripted-test", replies=[f"Final Answer: {bad}", f"Final Answer: {good}"]))
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    retry = text(llm.prompts[1])
    assert "ONE-TIME RULE" in retry and f"{ALI_USTA} appears 3 times" in retry and "### Previous result" in retry
    assert f"not accounted for: {BAZAAR}" in retry
    assert report.status == "complete" and report.repairs == []
    assert acts(report, MON)[1] == (TOPKAPI, "10:45", "12:15")  # Python scheduled the corrected answer itself


def test_repeated_duplicates_still_end_in_an_explicit_error():
    b = bundle()
    bad = f"Final Answer: {live_failure(drop=(BAZAAR,)).model_dump_json()}"
    llm = ScriptedLLM(model="scripted-test", replies=[bad] * 20)
    with pytest.raises(ItineraryPlannerError, match="no valid assignment") as failure:
        plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert 3 <= len(llm.prompts) <= 6
    # The error keeps the evidence CrewAI does not: every rejection, by problem kind, with the duplicated ids.
    assert len(failure.value.rejections) == 3
    assert failure.value.rejections[0]["problems"] == {"duplicate": 7, "missing": 1}
    assert ALI_USTA in failure.value.rejections[0]["duplicated_ids"] and failure.value.tool_calls == []


def test_dates_are_trip_dates_in_order():
    report = build_itinerary(context(), assign({WED: VALID[WED], MON: VALID[MON], TUE: VALID[TUE]}))
    assert report.dates == [dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)]
    assert [str(d.date) for d in report.itinerary.days] == [MON, TUE, WED]
