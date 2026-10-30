"""Useful daily density: unknown prices do not disqualify places, days get several activities, and duplicate
placements are repaired cheaply. No live service and no LLM call: the Amman fixture (recorded research and
routes) with the trip of the live run: Mon 2026-11-02 to Fri 2026-11-06, 2 travellers, 2000 USD."""
import datetime as dt
import time

from planning.budget_analyst import (BudgetDecision, PlaceDecision, apply_decision, make_guardrail,
                                     prepare_candidates, prompt_inputs, selection_shortfall)
from planning.itinerary_planner import (MAX_REPAIR_COMBOS, DayAssignment, DayChoice, PlanningContext,
                                        build_itinerary, make_guardrail as planner_guardrail,
                                        prompt_inputs as planner_inputs, validate_assignment)
from planning.plan_quality import daily_targets, target_range
from planning.schedule import schedule_day
from research_fixtures import bundle, recorded_lookup

TRIP = dict(start_date="2026-11-02", end_date="2026-11-06", budget_total=2000, travellers=2)


def amman(**trip):
    return bundle("amman", **{**TRIP, **trip})


def decide(cands, include=(), optional=()):
    def one(i):
        if i in include:
            return PlaceDecision(place_id=i, decision="include", rank=include.index(i) + 1, reason="Relevant")
        if i in optional:
            return PlaceDecision(place_id=i, decision="optional", rank=optional.index(i) + 1, reason="Alternative")
        return PlaceDecision(place_id=i, decision="exclude", reason="Not chosen")
    return BudgetDecision(decisions=[one(i) for i in cands.open_ids])


def attractions(cands):
    return [c for c in cands.candidates if c.category == "attraction" and not c.required]


# ------------------------------------------------------------------ targets
def test_daily_targets_are_lighter_on_arrival_and_departure():
    assert daily_targets(1) == [2]
    assert daily_targets(2) == [2, 2]
    assert daily_targets(5) == [1, 2, 2, 2, 1]
    assert target_range(5) == (8, 13)
    assert daily_targets(0) == []


# ------------------------------------------------------------------ Budget Analyst: unknown prices
def test_unknown_prices_stay_unknown_and_never_disqualify_a_place():
    b = amman()
    cands = prepare_candidates(b)
    unknown = [c.place_id for c in attractions(cands) if c.cost.status == "unknown"]
    assert unknown, "the fixture has unpriced attractions"

    report = apply_decision(cands, decide(cands, include=tuple(unknown)))

    assert [i.place_id for i in report.selected if not i.required] == unknown
    assert all(i.cost.status == "unknown" and i.cost.total_max is None for i in report.selected
               if i.place_id in unknown)
    assert report.dropped_for_budget == []  # unknown is never over budget
    assert report.status == "undetermined"  # honest: the total cannot be confirmed
    assert set(report.unknown_cost_items) >= {i.name for i in report.selected if i.place_id in unknown}
    activity = report.to_activity(unknown[0])
    assert activity.estimated_cost is None and activity.cost_status == "unknown"


def test_a_thin_selection_is_sent_back_naming_the_unknown_cost_places():
    b = amman()
    cands = prepare_candidates(b)
    priced = [c.place_id for c in attractions(cands) if c.cost.status != "unknown"][:1]
    thin = decide(cands, include=tuple(priced))

    need, pool = selection_shortfall(cands, thin)
    assert need > 0
    assert any(c.cost.status == "unknown" for c in pool)

    stats: dict = {}
    guardrail = make_guardrail(cands, stats)
    ok, feedback = guardrail(thin.model_dump_json())
    assert ok is False
    assert "too thin" in feedback and "(cost unknown)" in feedback
    assert "not a reason to exclude" in feedback
    assert stats["rejections"] == ["selection_shortfall"]


def test_the_last_attempt_is_accepted_so_the_target_never_fails_a_run():
    cands = prepare_candidates(amman())
    thin = decide(cands, include=(attractions(cands)[0].place_id,)).model_dump_json()
    stats: dict = {}
    guardrail = make_guardrail(cands, stats)
    results = [guardrail(thin)[0] for _ in range(3)]
    assert results == [False, False, True]
    assert stats["attempts"] == 3


def test_a_full_enough_selection_passes_first_time():
    cands = prepare_candidates(amman())
    every = tuple(c.place_id for c in attractions(cands))
    assert make_guardrail(cands)(decide(cands, include=every).model_dump_json())[0] is True


def test_no_shortfall_when_nothing_relevant_fits_the_budget():
    cands = prepare_candidates(amman(budget_total=1))
    priced = [c for c in attractions(cands) if c.cost.status in ("estimated", "range") and c.cost.total_max > 1]
    assert priced
    # Already over budget with one priced place: fewer places is the honest answer.
    assert selection_shortfall(cands, decide(cands, include=(priced[0].place_id,))) == (0, [])


def test_the_prompt_carries_the_target_and_interest_match():
    inputs = prompt_inputs(prepare_candidates(amman()))
    assert '"target_non_meal_places": {"min": 8, "max": 13}' in inputs["trip_context"]
    assert '"matches_interests"' in inputs["research_data"]


# ------------------------------------------------------------------ Planner: several activities a day
def full_report(b):
    cands = prepare_candidates(b)
    every = tuple(c.place_id for c in cands.candidates if not c.required)
    return apply_decision(cands, decide(cands, include=every))


def greedy_assignment(ctx: PlanningContext) -> DayAssignment:
    """A dense assignment Python schedules in full: each place once, on the first date it fits beside the others."""
    per_day = {d: [] for d in ctx.dates}
    for place_id, stop in ctx.stops.items():
        for d in ctx.dates:
            trial = [ctx.stops[i] for i in per_day[d]] + [stop]
            if len(per_day[d]) < 4 and not schedule_day(d, trial, ctx.rules, ctx.travel.cached_view()).unscheduled:
                per_day[d].append(place_id)
                break
    unplaced = [i for i in ctx.stops if not any(i in ids for ids in per_day.values())]
    return DayAssignment(days=[DayChoice(date=d, place_ids=ids) for d, ids in per_day.items()],
                         unscheduled=[{"place_id": i, "reason": "No date fits"} for i in unplaced])


def test_days_get_several_activities_when_enough_places_fit():
    b = amman()
    ctx = PlanningContext(full_report(b), b, recorded_lookup("amman"))
    assignment = greedy_assignment(ctx)
    assert validate_assignment(assignment, ctx, check_schedule=False) == []

    report = build_itinerary(ctx, assignment)
    counts = [len(d.activities) for d in report.itinerary.days]
    assert max(counts) >= 2  # not one activity a day
    names = [a.name for d in report.itinerary.days for a in d.activities]
    assert len(names) == len(set(names))  # no place twice
    assert report.performance["duplicate_repair_runs"] == 0


def test_the_planner_prompt_gives_daily_targets_and_forbids_repeats():
    b = amman()
    inputs = planner_inputs(PlanningContext(full_report(b), b, recorded_lookup("amman")))
    assert '"suggested_non_meal_places": 1' in inputs["trip_context"]
    assert '"suggested_non_meal_places": 2' in inputs["trip_context"]
    from planning.itinerary_planner import TASK_DESCRIPTION
    assert "NEVER repeat a place to fill a date" in TASK_DESCRIPTION


# ------------------------------------------------------------------ duplicates and search size
def five_selected(b):
    cands = prepare_candidates(b)
    picks = tuple(c.place_id for c in attractions(cands))[:5]
    return apply_decision(cands, decide(cands, include=picks)), list(picks)


def test_the_live_duplicate_pattern_is_repaired_to_one_placement_each_and_measured():
    b = amman()
    report, picks = five_selected(b)
    ctx = PlanningContext(report, b, recorded_lookup("amman"))
    dates = [str(d) for d in ctx.dates]
    # Places repeated across days as in the live run: 5 * 5 * 3 * 3 * 2 = 450 combinations of placements.
    spread = {picks[0]: dates, picks[1]: dates, picks[2]: dates[:3], picks[3]: dates[1:4], picks[4]: dates[3:]}
    answer = DayAssignment(days=[DayChoice(date=d, place_ids=[p for p in picks if d in spread[p]]) for d in dates])

    ok, fixed = planner_guardrail(ctx)(answer.model_dump_json())

    assert ok is True
    placed = [i for day in fixed.days for i in day.place_ids] + [u.place_id for u in fixed.unscheduled]
    assert sorted(placed) == sorted(picks)  # every place exactly once, none dropped, none invented
    assert ctx.repair_runs == 1 and 0 < ctx.repair_compared <= MAX_REPAIR_COMBOS
    assert ctx.repair_ms < 2000


def test_too_many_combinations_fall_back_to_one_greedy_choice():
    b = amman()
    report, picks = five_selected(b)
    ctx = PlanningContext(report, b, recorded_lookup("amman"))
    dates = [str(d) for d in ctx.dates]
    every_day = DayAssignment(days=[DayChoice(date=d, place_ids=list(picks)) for d in dates])  # 5^5 = 3125

    started = time.perf_counter()
    ok, fixed = planner_guardrail(ctx)(every_day.model_dump_json())
    elapsed = time.perf_counter() - started

    assert ok is True and ctx.repair_compared == 1
    assert elapsed < 2
    placed = [i for day in fixed.days for i in day.place_ids] + [u.place_id for u in fixed.unscheduled]
    assert sorted(placed) == sorted(picks)


def test_planner_report_carries_performance_counters():
    b = amman()
    ctx = PlanningContext(full_report(b), b, recorded_lookup("amman"))
    perf = build_itinerary(ctx, greedy_assignment(ctx)).performance
    assert set(perf) >= {"crew_ms", "attempts", "rejections", "duplicate_repair_runs", "repair_ms",
                         "repair_combinations_compared", "check_day_calls", "travel_lookups"}
