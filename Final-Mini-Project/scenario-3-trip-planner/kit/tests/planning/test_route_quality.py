"""Route-aware duplicate repair (after the third live run). No live service, no LLM cost.

The third live run's repair kept a 3/3/2 plan chosen by day counts alone: Topkapı -> Süleymaniye (32 min) ->
Grand Bazaar; Dolmabahçe -> Pera -> Hagia Sophia (45 min); Basilica Cistern -> Ali Usta at 10:43. LIVE_RUN_3 is
an answer of that shape: the old count-only ranking turns it into exactly that plan, and it also contains every
placement of the route-efficient 3/2/3 candidate. Travel times are the recorded replay routes.
"""
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import datetime as dt
import itertools

from planning import itinerary_planner as planner
from planning.itinerary_planner import (MAX_REORDER_STOPS, MAX_REPAIR_COMBOS, _placements, assignment_problems,
                                        build_itinerary, day_cost, make_guardrail, plan_itinerary, repair_duplicates)
from planning.schedule import validate_day
from research_fixtures import bundle, recorded_lookup
from scripted_llm import NativeScriptedLLM
from test_itinerary_planner import (ALI_USTA, BAZAAR, CISTERN, DOLMABAHCE, HAGIA, MON, PERA, SULEYMANIYE, TOPKAPI,
                                    TUE, WED, assign, budget_report, context, no_overlaps)

LIVE_3_3_2 = {MON: [TOPKAPI, SULEYMANIYE, BAZAAR], TUE: [DOLMABAHCE, PERA, HAGIA], WED: [CISTERN, ALI_USTA]}
CANDIDATE = {MON: [TOPKAPI, HAGIA, CISTERN], TUE: [DOLMABAHCE, PERA], WED: [SULEYMANIYE, BAZAAR, ALI_USTA]}
LIVE_RUN_3 = {MON: [TOPKAPI, SULEYMANIYE, BAZAAR, HAGIA, CISTERN], TUE: [DOLMABAHCE, PERA, HAGIA],
              WED: [SULEYMANIYE, BAZAAR, CISTERN, ALI_USTA]}
THU, FRI = "2026-11-05", "2026-11-06"


def warm(ctx):
    """Measure every walk between the selected places, as the agent's check_day calls would over a run."""
    for a, b in itertools.permutations(ctx.stops.values(), 2):
        ctx.travel.leg(a, b)
    return ctx


def measured_minutes(report):
    return sum(e.travel_minutes or 0 for e in report.evidence)


def per_day_sets(assignment):
    return {str(d.date): set(d.place_ids) for d in assignment.days}


# ------------------------------------------------------------------ the two plans, checked by the scheduler
def test_the_live_3_3_2_plan_is_valid_but_walks_far():
    ctx = context()
    report = build_itinerary(ctx, assign(LIVE_3_3_2))
    assert report.status == "complete" and validate_day_all(report)
    walks = {e.place_id: e.travel_minutes for e in report.evidence if e.travel_status == "measured"}
    assert walks[SULEYMANIYE] == 32 and walks[HAGIA] == 45 and measured_minutes(report) == 103
    assert ("long_walk", "meal_time") == tuple(sorted({q.code for q in report.quality}))
    assert report.plan_summary[2] == "2026-11-04 (Wed): 09:00-10:30 Basilica Cistern, 10:43-11:43 Ali Usta Çiğ Köfte"


def test_the_candidate_is_feasible_and_walks_less_than_half():
    ctx = context()
    report = build_itinerary(ctx, assign(CANDIDATE))
    assert assignment_problems(assign(CANDIDATE), ctx) == [] and validate_day_all(report)
    assert measured_minutes(report) == 44 and report.quality == []
    assert report.plan_summary[2].endswith("12:26-13:26 Ali Usta Çiğ Köfte")  # lunch, not 10:43
    live = build_itinerary(context(), assign(LIVE_3_3_2))
    assert report.budget.known_total == live.budget.known_total  # same places, same money
    assert report.travel_uncertain == live.travel_uncertain == [f"{TUE}: {DOLMABAHCE} -> {PERA}"]  # same unknown


def validate_day_all(report):
    return all(validate_day(d) == [] for d in report.schedule.days)


# ------------------------------------------------------------------ the repair now prefers shorter walks
def test_the_answer_offers_both_plans_and_balance_alone_cannot_tell_them_apart():
    placements = _placements(assign(LIVE_RUN_3))
    for plan in (LIVE_3_3_2, CANDIDATE):
        assert all(date in placements[i] for date, ids in plan.items() for i in ids)
    assert sorted(len(v) for v in LIVE_3_3_2.values()) == sorted(len(v) for v in CANDIDATE.values()) == [2, 3, 3]


def test_with_measured_routes_the_repair_picks_the_route_efficient_plan_without_lookups():
    ctx = warm(context(max_lookups=200))
    calls = len(ctx.travel.calls)
    fixed, notes = repair_duplicates(assign(LIVE_RUN_3), ctx)
    assert len(ctx.travel.calls) == calls  # ranking and validation used the cache only: no new tool calls
    assert per_day_sets(fixed) == {d: set(ids) for d, ids in CANDIDATE.items()}
    report = build_itinerary(ctx, fixed)
    assert measured_minutes(report) <= 44 and not any(q.code == "long_walk" for q in report.quality)
    assert notes[-1].startswith("chose the least travel among") and "planning buffer, not as measured" in notes[-1]


def test_without_known_routes_unknown_walks_are_never_counted_as_zero():
    ctx = context()  # cold cache: every walk unknown while ranking
    fixed, notes = repair_duplicates(assign(LIVE_RUN_3), ctx)
    no_routes = planner.PlanningContext(budget_report(bundle()), bundle(), None)  # no travel source at all
    for d in fixed.days:
        cost = day_cost(no_routes, d.date, tuple(d.place_ids))
        assert cost.travel_minutes == 30 * cost.unknown_legs  # the labelled buffer, never 0 minutes
    assert len(ctx.travel.calls) <= 5  # only the final plan's own legs were looked up, by its validation
    report = build_itinerary(ctx, fixed)
    assert all(e.travel_minutes is None for e in report.evidence if e.travel_status != "measured")


def test_with_no_walks_known_geography_cannot_decide_but_meal_timing_still_counts():
    ctx = context()  # nothing measured: every candidate costs the same buffers, so only meals and balance differ
    fixed, _ = repair_duplicates(assign(LIVE_RUN_3), ctx)
    report = build_itinerary(ctx, fixed)
    assert "meal_time" not in {q.code for q in report.quality}  # the live 10:43 meal is not repeated
    assert report.status == "complete" and validate_day_all(report)


def test_long_walk_messages_say_what_was_examined():
    plain = build_itinerary(context(), assign(LIVE_3_3_2))
    walk = next(q for q in plain.quality if q.code == "long_walk")
    assert "no reorder of this day's places using walking times already measured" in walk.message
    assert "other day assignments were not compared" in walk.message and "optimal" not in walk.message
    ctx = warm(context(max_lookups=200, max_walk_minutes=10))  # a strict limit: some walks remain long
    ok, accepted = make_guardrail(ctx)(assign(LIVE_RUN_3).model_dump_json())
    assert ok
    repaired = build_itinerary(ctx, accepted)
    remaining = [q for q in repaired.quality if q.code == "long_walk" and not q.avoidable]
    assert remaining and all("combinations of the planner's own day assignments that Python compared" in q.message
                             for q in remaining)
    assert not any("optimal" in w for w in repaired.warnings)


# ------------------------------------------------------------------ hard constraints still hold
def test_an_order_that_breaks_opening_hours_is_never_used():
    picks = {HAGIA: ("include", 1), CISTERN: ("include", 2), SULEYMANIYE: ("include", 3)}
    ctx = warm(context(picks=picks, start_date=THU, end_date=FRI, max_lookups=200))
    friday = dt.date(2026, 11, 6)  # Süleymaniye closes at 13:30 on Fridays
    as_given = planner.schedule_day(friday, [ctx.stops[i] for i in (TOPKAPI, CISTERN, SULEYMANIYE)], ctx.rules,
                                    ctx.travel)
    assert as_given.stops[2].status == "does_not_fit"  # the agent's own order fails the Friday hours
    cost = day_cost(ctx, friday, (TOPKAPI, CISTERN, SULEYMANIYE))
    assert cost.feasible and cost.order != (TOPKAPI, CISTERN, SULEYMANIYE)  # only a valid reorder is kept
    fixed, _ = repair_duplicates(assign({THU: [HAGIA, CISTERN, SULEYMANIYE], FRI: [TOPKAPI, CISTERN, SULEYMANIYE]}),
                                 ctx)
    report = build_itinerary(ctx, fixed)
    assert validate_day_all(report) and report.status == "complete"
    for d in report.itinerary.days:
        for a in d.activities:
            if a.place_id == SULEYMANIYE and d.date == friday:
                assert a.end_time <= "13:30"


def test_closed_dates_and_required_places_are_untouched_by_the_ranking():
    fixed, _ = repair_duplicates(assign(LIVE_RUN_3), warm(context(max_lookups=200)))
    kept = {i: str(d.date) for d in fixed.days for i in d.place_ids}
    assert kept[TOPKAPI] == MON and kept[DOLMABAHCE] != MON and kept[PERA] != MON
    assert sorted(kept) == sorted(planner.PlanningContext(budget_report(bundle()), bundle()).stops)


def test_end_to_end_the_repaired_plan_keeps_costs_citations_and_has_no_overlaps():
    b = bundle()
    report_in = budget_report(b)
    llm = NativeScriptedLLM(model="scripted-test", replies=[assign(LIVE_RUN_3).model_dump_json()])
    report = plan_itinerary(report_in, b, llm, recorded_lookup())
    assert len(llm.prompts) == 1 and report.status == "complete"
    ids = [a.place_id for d in report.itinerary.days for a in d.activities]
    assert len(ids) == len(set(ids)) == 8
    no_overlaps(report)
    assert report.budget.known_total == report_in.known_total
    cited = {c["id"] for c in report.citations}
    assert set(ids) <= cited and all(e.travel_citation_id in cited for e in report.evidence
                                     if e.travel_status == "measured")
    assert len(report.travel_lookups) <= 20


def test_the_search_is_bounded():
    assert (MAX_REPAIR_COMBOS, MAX_REORDER_STOPS) == (1024, 4)
    ctx = warm(context(max_lookups=200))
    counter = {"n": 0}
    original = planner.schedule_day

    def counting(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    planner.schedule_day = counting
    try:
        repair_duplicates(assign(LIVE_RUN_3), ctx)
    finally:
        planner.schedule_day = original
    # 2 x 2 x 2 x 2 combinations, each day memoised and tried in at most 24 orders, plus the final validation
    assert counter["n"] <= 16 * 3 * 24
