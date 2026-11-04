"""The fast path: each agent layer's deterministic counterpart, with no LLM. Offline, on the Istanbul research fixture.

The rules are the agent's own (planning/budget_analyst.TASK_DESCRIPTION); the budget fit, the schedule and the
verification are the same code as for the agents' plans, so only the ranking and the day assignment are Python's.
"""
import time
from unittest.mock import patch

from research_fixtures import bundle

from planning.budget_analyst import apply_decision, prepare_candidates, python_decision, run_budget_analyst, validate_decision
from planning.itinerary_planner import plan_itinerary
from planning.verification import FreshLookups, verify_itinerary
from planning.verifier_agent import explain_verification, run_verifier_agent


def measured(a, b):
    return {"ok": True, "duration_min": 9, "profile": "foot", "citation": {"id": "x"}, "mode": "replay"}


def no_llm(*a, **k):
    raise AssertionError("the fast path must make no LLM call")


def test_the_python_decision_is_valid_and_keeps_every_rule_of_the_agent():
    b = bundle()
    cands = prepare_candidates(b)
    decision = python_decision(cands)
    assert validate_decision(decision, cands) == []
    decided = {d.place_id: d for d in decision.decisions}
    assert set(decided) == set(cands.open_ids)
    for d in decision.decisions:
        if d.decision != "exclude":
            assert d.rank is not None
        c = cands.get(d.place_id)
        if c.closed_all_trip or c.not_during_trip:
            assert d.decision == "exclude"


def test_the_budget_report_comes_from_the_same_code_as_the_agents():
    b = bundle()
    with patch("planning.budget_analyst.decide_with_crew", side_effect=no_llm):
        report = run_budget_analyst(b, None)
    assert report.selected and {i.place_id for i in report.selected} >= set(prepare_candidates(b).required_ids)
    assert report == apply_decision(prepare_candidates(b), python_decision(prepare_candidates(b)))


def test_an_unknown_price_stays_unknown_in_a_python_plan():
    report = run_budget_analyst(bundle(), None)
    assert all(i.cost.total_max is None for i in report.selected if i.cost.status == "unknown")
    assert report.breakdown["unknown_price_items"] == [i.name for i in report.selected if i.cost.status == "unknown"]


def test_the_day_plan_needs_no_agent_and_says_it_is_pythons():
    b = bundle()
    report = run_budget_analyst(b, None)
    with patch("planning.itinerary_planner.decide_with_crew", side_effect=no_llm):
        planned = plan_itinerary(report, b, None, measured)
    assert planned.decided_by == "python" and planned.itinerary is not None
    names = [a.name for d in planned.itinerary.days for a in d.activities]
    assert names and len(names) == len(set(names))  # no place twice


def test_the_verdict_is_the_same_with_or_without_the_agent_ranking_it():
    b = bundle()
    report = run_budget_analyst(b, None)
    planned = plan_itinerary(report, b, None, measured)
    check = dict(budget_report=report, planner_report=planned, travel_lookup=measured)
    python_report = verify_itinerary(planned.itinerary, b, **check)
    with patch("planning.verifier_agent.decide_with_crew", side_effect=no_llm):
        fast = run_verifier_agent(planned.itinerary, b, None, **check)
    assert (fast.status, fast.verified, fast.decided_by) == (python_report.status, python_report.verified, "python")
    assert [p.code for p in explain_verification(python_report, None).prioritized] == [p.code for p in fast.prioritized]


def test_past_the_time_limit_no_new_lookup_starts_and_the_fact_stays_unknown():
    asked = []

    def slow(a, b):
        asked.append((a, b))
        time.sleep(1.5)
        return measured(a, b)

    lookups = FreshLookups(None, slow, seconds=0.2)
    started = time.monotonic()
    lookups.prefetch([("travel_time", ("A", "B")), ("travel_time", ("C", "D"))])
    assert time.monotonic() - started < 1.0  # the limit holds even with a slow source
    assert lookups.travel("E", "F") is None and lookups.skipped == 1  # not asked: unknown, never assumed
    assert ("E", "F") not in asked


def test_two_listings_of_one_name_are_planned_once():
    cands = prepare_candidates(bundle())
    open_sights = [c for c in cands.candidates if c.category == "attraction" and not c.required]
    first, twin = open_sights[0], open_sights[1]
    object.__setattr__(twin, "name", first.name)
    decision = python_decision(cands)
    assert validate_decision(decision, cands) == []
    kept = {d.place_id for d in decision.decisions if d.decision != "exclude"}
    assert not {first.place_id, twin.place_id} <= kept


def test_two_listings_of_one_spot_keep_the_one_the_research_ranks_first():
    b = bundle()
    cands = prepare_candidates(b)
    sights = [c for c in cands.candidates if c.category == "attraction" and not c.required and c.place_id in cands.open_ids]
    by_id = {p.place_id: p for p in b.places}
    first = by_id[sights[0].place_id]
    second = by_id[sights[1].place_id]
    assert first.lat is not None
    twin = second.model_copy(update={"name": f"{first.name} Tunnel Tour", "lat": first.lat, "lon": first.lon})
    places = [twin if p.place_id == second.place_id else p for p in b.places]
    named = cands.model_copy(update={"candidates": [
        c.model_copy(update={"name": twin.name}) if c.place_id == twin.place_id else c for c in cands.candidates]})
    decided = {d.place_id: d for d in python_decision(named, places).decisions}
    assert decided[first.place_id].decision != "exclude"
    assert decided[twin.place_id].decision == "exclude"
    assert validate_decision(python_decision(named, places), named) == []


def test_a_shekel_price_is_read_and_stays_in_shekels():
    from services.travel_data import parse_price
    assert (parse_price("₪30 / ₪15 discount")["currency"], parse_price("₪30 / ₪15 discount")["min"]) == ("ILS", 15)
    assert parse_price("35 NIS")["amounts"] == {"ILS": [35]}
    assert parse_price("Under ₪60")["max"] == 60


def test_admission_is_free_is_free_and_a_paid_mention_is_not():
    from services.travel_data import parse_price
    assert parse_price("Admission is free")["free"] is True
    assert parse_price("Free")["free"] is True
    assert parse_price("Free for children, adults ₪20")["free"] is False
    assert parse_price("See Wohl Archaeological Museum")["parsed"] is False  # no amount: unknown, never guessed


def test_free_entry_with_a_paid_extra_stays_free():
    from services.travel_data import parse_price
    p = parse_price("Free entry; guided tours can cost about ₪30")
    assert p["free"] is True and p["min"] == 0 and p["max"] == 0
    assert parse_price("Free entry for children, adults ₪20")["free"] is False


def test_a_food_trip_keeps_a_meal_a_day_even_when_they_cannot_all_be_checked():
    from planning.evidence import checkable_bundle
    b = bundle()
    t = b.trip
    without_food = b.model_copy(update={"trip": t.model_copy(update={"interests": [i for i in t.interests if i != "food"] or ["history"]})})
    with_food = b.model_copy(update={"trip": t.model_copy(update={"interests": list(dict.fromkeys([*t.interests, "food"]))})})
    meals = lambda x: sum(p.category == "restaurant" for p in x.places)  # noqa: E731
    assert meals(checkable_bundle(with_food, measured)[0]) >= meals(checkable_bundle(without_food, measured)[0])


def test_a_place_the_repair_removed_is_not_listed_as_planned():
    from app.orchestrator import in_plan
    plan = {"days": [{"activities": [{"name": "Lifta"}]}]}
    out = in_plan({"unknown_price_items": ["Lifta", "The Western Wall"], "status": "undetermined"}, plan)
    assert out == {"unknown_price_items": ["Lifta"], "status": "undetermined"}
    assert in_plan(None, plan) is None


def test_the_repair_of_a_fast_plan_stops_looking_things_up_at_its_limit(monkeypatch):
    from app import verification_adapter as va
    asked = []
    monkeypatch.setattr(va, "REPAIR_SECONDS", 0)
    bounded = va._within(lambda a, b: asked.append((a, b)), 5.0)
    try:
        bounded("A", "B")
    except TimeoutError:
        pass
    else:
        raise AssertionError("past the limit a lookup must fail, so the fact stays unknown")
    assert not asked
    lookup = object()
    assert va._within(lookup, None) is lookup  # the agents' path has no limit


def test_without_a_time_limit_every_lookup_is_answered():
    lookups = FreshLookups(None, measured)
    lookups.prefetch([("travel_time", ("A", "B")), ("travel_time", ("C", "D"))])
    assert lookups.travel("A", "B")["duration_min"] == 9 and len(lookups.calls) == 2


def _far_and_dear():
    from decimal import Decimal
    b = bundle()
    cands = prepare_candidates(b)
    open_sight = next(c for c in cands.candidates if c.category == "attraction" and not c.required and c.place_id in cands.open_ids)
    meal = next(c for c in cands.candidates if c.category == "restaurant" and c.place_id in cands.open_ids)
    places = [p.model_copy(update={"lat": p.lat + 6}) if p.place_id == open_sight.place_id and p.lat is not None else p for p in b.places]
    dear = meal.model_copy(update={"cost": meal.cost.model_copy(update={"total_max": Decimal("99999"), "total_min": Decimal("99999")})})
    named = cands.model_copy(update={"candidates": [dear if c.place_id == meal.place_id else c for c in cands.candidates]})
    return named, places, open_sight, meal


def test_a_listing_with_another_towns_coordinates_is_not_planned():
    named, places, sight, _ = _far_and_dear()
    decided = {d.place_id: d for d in python_decision(named, places).decisions}
    assert decided[sight.place_id].decision == "exclude" and "far" in decided[sight.place_id].reason


def test_a_meal_above_a_days_budget_is_left_out_alone():
    named, places, _, meal = _far_and_dear()
    decision = python_decision(named, places)
    decided = {d.place_id: d for d in decision.decisions}
    assert decided[meal.place_id].decision == "exclude"
    others = [c for c in named.candidates if c.category == "restaurant" and c.place_id != meal.place_id]
    assert any(decided[c.place_id].decision == "include" for c in others if c.place_id in decided)
    assert validate_decision(decision, named) == []


def test_the_review_starts_from_the_same_places_as_the_plan():
    from planning.budget_analyst import not_plannable
    named, places, sight, meal = _far_and_dear()
    assert set(not_plannable(named, places)) == {sight.place_id, meal.place_id}


def test_a_place_the_traveller_asked_for_is_never_set_aside():
    from planning.budget_analyst import not_plannable
    named, places, sight, _ = _far_and_dear()
    asked = named.model_copy(update={"candidates": [
        c.model_copy(update={"required": True}) if c.place_id == sight.place_id else c for c in named.candidates]})
    assert sight.place_id not in not_plannable(asked, places)
