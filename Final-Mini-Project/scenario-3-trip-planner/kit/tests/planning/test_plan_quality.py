"""Planning quality, balanced duplicate repair and stale notes (after the second live run). No live service.

The second live run (OpenRouter, gpt-4.1-mini) produced duplicates that the old repair kept on their earliest
date: 6 places on Monday, 2 on Tuesday, none on Wednesday, with notes claiming places were "scheduled twice"
and "excluded". LIVE_RUN_2 is an answer of that shape (the old repair turns it into exactly 6/2/0).
"""
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

from planning.itinerary_planner import (GUARDRAIL_RETRIES, PlanningContext, _placements, build_itinerary,
                                        make_guardrail, plan_itinerary, repair_duplicates)
from planning.plan_quality import assess_quality
from planning.schedule import TravelTimes
from research_fixtures import bundle, recorded_lookup
from scripted_llm import NativeScriptedLLM
from test_itinerary_planner import (ALI_USTA, BAZAAR, CISTERN, DOLMABAHCE, HAGIA, MON, PERA, SULEYMANIYE, TOPKAPI,
                                    TUE, VALID, WED, assign, budget_report, context, no_overlaps)

LIVE_RUN_2 = {MON: [HAGIA, TOPKAPI, CISTERN, SULEYMANIYE, ALI_USTA, BAZAAR],
              TUE: [DOLMABAHCE, PERA, SULEYMANIYE, BAZAAR],
              WED: [HAGIA, CISTERN, DOLMABAHCE, PERA, ALI_USTA]}
LIVE_NOTES = ["Topkapi Palace is scheduled twice for flexibility.", "Süleymaniye Mosque and Grand Bazaar are excluded."]
SIX_TWO_ZERO = {MON: [HAGIA, TOPKAPI, CISTERN, SULEYMANIYE, ALI_USTA, BAZAAR], TUE: [DOLMABAHCE, PERA], WED: []}


def counts(assignment):
    return [len(d.place_ids) for d in sorted(assignment.days, key=lambda d: d.date)]


def codes(report_or_issues):
    issues = getattr(report_or_issues, "quality", report_or_issues)
    return [q.code for q in issues]


def user_facing(report):
    return " ".join(report.warnings + report.plan_summary + report.planner_notes)


# ------------------------------------------------------------------ the 6/2/0 plan
def test_the_6_2_0_plan_is_valid_but_flagged():
    report = build_itinerary(context(), assign(SIX_TWO_ZERO))
    assert report.status == "complete"  # valid: quality never changes the status
    no_overlaps(report)
    empty = next(q for q in report.quality if q.code == "empty_day")
    assert empty.date.isoformat() == WED and empty.avoidable
    assert {DOLMABAHCE, PERA, HAGIA, TOPKAPI} <= set(empty.place_ids)  # Python schedules each alone on Wednesday
    assert {"front_loaded", "busy_day"} <= set(codes(report))
    assert any(w.startswith("Planning advice: 2026-11-04 (Wed) has no places") for w in report.warnings)


def test_an_avoidable_empty_day_is_filled_by_python_without_a_retry():
    """The 6/2/0 plan: Python moves places from the overfull Monday to the empty Wednesday itself."""
    ctx = context()
    ok, accepted = make_guardrail(ctx)(assign(SIX_TWO_ZERO).model_dump_json())
    assert ok and ctx.rejections == []  # valid as given: nothing to reject, only to improve
    wed = next(d for d in accepted.days if str(d.date) == WED)
    assert wed.place_ids and any(n.startswith("moved ") and "2026-11-04 (Wed), which had no places" in n
                                 for n in ctx.repairs)
    report = build_itinerary(ctx, accepted)
    assert not any(q.code == "empty_day" for q in report.quality)
    no_overlaps(report)


def test_any_attempt_gets_the_same_python_balance():
    ctx = context()
    ctx.attempts = GUARDRAIL_RETRIES  # the last answer CrewAI asks for is balanced the same way
    ok, accepted = make_guardrail(ctx)(assign(SIX_TWO_ZERO).model_dump_json())
    assert ok and all(d.place_ids for d in accepted.days)


def test_an_unavoidable_empty_day_is_accepted_and_said_to_be_unavoidable():
    picks = {DOLMABAHCE: ("include", 1), PERA: ("include", 2)}  # both closed on Mondays
    ctx = context(picks=picks, required_places=[], start_date=MON, end_date=TUE)
    a = assign({MON: [], TUE: [DOLMABAHCE, PERA]})
    ok, accepted = make_guardrail(ctx)(a.model_dump_json())
    assert ok and ctx.rejections == []  # nothing to ask for: no planned place can go on Monday
    report = build_itinerary(ctx, accepted)
    empty = next(q for q in report.quality if q.code == "empty_day")
    assert not empty.avoidable and "none of the planned places can go on it" in empty.message


def test_unequal_days_are_not_a_problem_in_themselves():
    report = build_itinerary(context(), assign(VALID))  # 4/2/2
    assert "empty_day" not in codes(report) and "front_loaded" not in codes(report)


# ------------------------------------------------------------------ balanced repair
def test_duplicate_repair_prefers_a_balanced_plan_from_the_agents_own_placements():
    ctx = context()
    original = assign(LIVE_RUN_2, notes=LIVE_NOTES)
    fixed, notes = repair_duplicates(original, ctx)
    assert counts(fixed) == [3, 3, 2]  # the old earliest-date repair gave 6/2/0
    kept = {i: str(d.date) for d in fixed.days for i in d.place_ids}
    assert all(kept[i] in _placements(original)[i] for i in kept)  # only the agent's own choices
    assert kept[TOPKAPI] == MON and kept[DOLMABAHCE] != MON and kept[PERA] != MON  # required kept, closed avoided
    assert fixed.notes == [] and all(d.reason == "" for d in fixed.days)  # stale words cleared
    assert sum(": kept " in n for n in notes) == 7  # every place but Topkapı was listed twice in this answer


def test_a_date_the_agent_never_chose_is_filled_from_the_planned_places_only():
    ctx = context()
    a = assign({MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA, SULEYMANIYE],
                TUE: [SULEYMANIYE, BAZAAR, DOLMABAHCE, PERA], WED: []})
    ok, accepted = make_guardrail(ctx)(a.model_dump_json())
    assert ok
    wed = next(d for d in accepted.days if str(d.date) == WED).place_ids
    assert wed and set(wed) <= set(ctx.stops)  # nothing invented: only selected places moved there
    ids = [i for d in accepted.days for i in d.place_ids]
    assert len(ids) == len(set(ids))  # Süleymaniye kept once


def test_live_run_2_is_repaired_balanced_and_explained_consistently():
    b = bundle()
    llm = NativeScriptedLLM(model="scripted-test", replies=[assign(LIVE_RUN_2, notes=LIVE_NOTES).model_dump_json()])
    report = plan_itinerary(budget_report(b), b, llm, recorded_lookup())
    assert len(llm.prompts) == 1  # repaired on the first answer: no extra paid call
    assert report.status == "complete" and counts(report.assignment) == [3, 3, 2]
    assert not any(q.code == "empty_day" for q in report.quality)
    no_overlaps(report)
    # The stale notes are gone; what the user reads comes from the final schedule.
    assert report.planner_notes == [] and report.notes_suppressed == 2
    text = user_facing(report)
    assert "scheduled twice" not in text and "are excluded" not in text
    assert any("The planner's own notes are not shown" in w for w in report.warnings)
    for line, day in zip(report.plan_summary, report.itinerary.days):
        assert line.startswith(f"{day.date} ({day.weekday}): ")
        assert [a.name for a in day.activities] == [n.split(" ", 1)[1] for n in line.split(": ", 1)[1].split(", ")]


def test_budget_and_citations_hold_after_a_repair():
    b = bundle()
    llm = NativeScriptedLLM(model="scripted-test", replies=[assign(LIVE_RUN_2).model_dump_json()])
    report_in = budget_report(b)
    report = plan_itinerary(report_in, b, llm, recorded_lookup())
    assert report.budget.known_total == report_in.known_total  # every selected place scheduled once
    assert set(report.scheduled_place_ids) <= {c["id"] for c in report.citations}
    for e in report.evidence:  # no route fabricated: unmeasured legs have no minutes, measured ones a citation
        if e.travel_status == "measured":
            assert e.travel_minutes is not None and e.travel_citation_id
        else:
            assert e.travel_minutes is None
    assert len(report.travel_lookups) <= 20


# ------------------------------------------------------------------ walks and meals: advice from known data only
def test_long_walk_advice_spends_no_lookups_and_writes_nothing_to_the_cache():
    ctx = context(max_walk_minutes=10)
    report = build_itinerary(ctx, assign(VALID))
    calls = len(ctx.travel.calls)
    cache = dict(ctx.travel._cache)
    walks = [q for q in assess_quality(report.schedule, ctx.stops, ctx.rules, ctx.travel) if q.code == "long_walk"]
    assert walks and all("measured" in q.message for q in walks)
    assert len(ctx.travel.calls) == calls and ctx.travel._cache == cache  # read-only
    assert not any(leg.status == "not_looked_up" for leg in ctx.travel._cache.values())


def test_a_reorder_is_suggested_only_when_every_leg_is_already_measured():
    minutes = {("Hagia Sophia", "Topkapı Palace"): 40, ("Hagia Sophia", "Basilica Cistern"): 5,  # MOCK minutes
               ("Basilica Cistern", "Topkapı Palace"): 5, ("Topkapı Palace", "Ali Usta Çiğ Köfte"): 5}

    def mock_lookup(a, b):
        return {"ok": True, "duration_min": minutes[(a, b)]} if (a, b) in minutes else {
            "ok": False, "error": {"code": "not_cached", "message": "MOCK"}}

    b = bundle()
    ctx = PlanningContext(budget_report(b), b, mock_lookup)
    report = build_itinerary(ctx, assign({**VALID, MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA]}))
    walk = next(q for q in report.quality if q.code == "long_walk")
    assert not walk.avoidable  # Hagia -> Cistern was never measured: no suggestion
    for a, b in ((HAGIA, CISTERN), (CISTERN, TOPKAPI), (TOPKAPI, ALI_USTA)):  # now every leg of the reorder is
        ctx.travel.leg(ctx.stops[a], ctx.stops[b])                             # measured
    walk = next(q for q in assess_quality(report.schedule, ctx.stops, ctx.rules, ctx.travel) if q.code == "long_walk")
    assert walk.avoidable and walk.place_ids == [HAGIA, CISTERN, TOPKAPI, ALI_USTA]


def test_meal_outside_the_usual_times_is_advice_shown_once():
    report = build_itinerary(context(), assign({**VALID, MON: [ALI_USTA, HAGIA, TOPKAPI, CISTERN]}))
    assert "meal_time" in codes(report)
    assert sum("outside the usual meal times" in w for w in report.warnings) == 1


# ------------------------------------------------------------------ notes against the final plan
def test_notes_that_contradict_the_plan_are_not_shown():
    notes = ["Süleymaniye Mosque and Grand Bazaar are excluded.", "Topkapi Palace is scheduled twice for flexibility.",
             "Museums are grouped in the mornings."]
    report = build_itinerary(context(), assign(VALID, notes=notes))
    assert report.planner_notes == ["Museums are grouped in the mornings."] and report.notes_suppressed == 2
    assert any("2 of the planner's notes contradicted the final plan" in w for w in report.warnings)


def test_a_note_about_an_unscheduled_place_as_if_planned_is_not_shown():
    a = assign({**VALID, WED: [DOLMABAHCE]}, {PERA: "Too far from the rest"},
               notes=["Pera Museum suits the art interest.", "Pera Museum is left out: too far."])
    report = build_itinerary(context(), a)
    assert report.planner_notes == ["Pera Museum is left out: too far."]


def test_plan_summary_is_built_from_the_final_schedule():
    report = build_itinerary(context(), assign({**VALID, WED: [DOLMABAHCE]}, {PERA: "Too far from the rest"}))
    assert report.plan_summary[0] == ("2026-11-02 (Mon): 09:00-10:30 Hagia Sophia, 10:45-12:15 Topkapı Palace, "
                                      "12:29-13:59 Basilica Cistern, 14:12-15:12 Ali Usta Çiğ Köfte")
    assert report.plan_summary[-1] == "Not planned: Pera Museum (Too far from the rest)"


def test_quality_reads_only_the_cache_even_on_a_fresh_travel_source():
    travel = TravelTimes(recorded_lookup())
    ctx = context()
    report = build_itinerary(ctx, assign(VALID))
    assess_quality(report.schedule, ctx.stops, ctx.rules, travel)  # an empty cache: nothing to suggest, no lookup
    assert travel.calls == []
