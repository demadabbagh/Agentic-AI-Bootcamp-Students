"""Interest coverage, the Budget Analyst's top-up and Python's day balance, on a six-day Paris-shaped trip.

The Paris live run (Nov 2-7, 2026: history, shopping, art) gave a museum-only plan with no restaurant, daily
sightseeing counts [1, 4, 1, 0, 2, 1] and walks of 55 minutes, and it failed with HTTP 503 when the planner's
duplicates and missing places exhausted the guardrail retries. These tests rebuild that shape offline: twelve
sights in four neighbourhoods, five restaurants, one place closed for the whole trip. No LLM, no network.
"""
import datetime as dt
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest

from planning.budget_analyst import (BudgetDecision, PlaceDecision, apply_decision, confirmed_serves, make_guardrail,
                                     meal_target, prepare_candidates)
from planning.day_balance import Planner, complete, propose, rebalance, score
from planning.itinerary_planner import (DayAssignment, DayChoice, PlanningContext, assignment_problems,
                                        build_itinerary, make_guardrail as planner_guardrail, plan_itinerary)
from planning.plan_quality import daily_targets
from planning.research import ResearchBundle, ResearchedPlace, TripRequest
from scripted_llm import NativeScriptedLLM

START, END = "2026-11-02", "2026-11-07"
DATES = [str(dt.date(2026, 11, 2) + dt.timedelta(days=n)) for n in range(6)]
OPEN = {"raw": "daily 09:00-18:00", "parsed": True, "days_stated": True, "closed_days": [],
        "weekly": {d: ["09:00-18:00"] for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}}
CLOSED = {"raw": "closed for renovation", "temporarily_closed": True}
# (id, tags, lat, lon): four neighbourhoods about 2-4 km apart, places in one a few hundred metres apart
SIGHTS = [
    ("louvre", ["art", "museum", "history"], 48.8606, 2.3376), ("orsay", ["art", "museum"], 48.8600, 2.3266),
    ("palais-royal", ["history"], 48.8638, 2.3370),
    ("notre-dame", ["history"], 48.8530, 2.3499), ("sainte-chapelle", ["history", "art"], 48.8554, 2.3450),
    ("cluny", ["museum", "history"], 48.8505, 2.3440),
    ("lafayette", ["markets"], 48.8738, 2.3320), ("printemps", ["markets"], 48.8740, 2.3283),
    ("opera-garnier", ["history", "art"], 48.8720, 2.3316),
    ("sacre-coeur", ["history"], 48.8867, 2.3431), ("puces", ["markets"], 48.8990, 2.3440),
    ("dali", ["art", "museum"], 48.8865, 2.3405),
]
MEALS = [("bistro-1", 48.8610, 2.3360), ("bistro-2", 48.8540, 2.3470), ("bistro-3", 48.8730, 2.3300),
         ("bistro-4", 48.8860, 2.3420), ("bistro-5", 48.8600, 2.3400)]


def place(pid, category, tags, lat, lon, hours=OPEN, kind=None):
    return ResearchedPlace(place_id=f"wv-paris-{pid}", name=pid.replace("-", " ").title(), category=category,
                           kind=kind or ("eat" if category == "restaurant" else "see"), tags=tags, lat=lat, lon=lon,
                           hours=hours, price={"raw": None, "parsed": False}, source_url="https://en.wikivoyage.org/x")


def paris(interests=("history", "markets", "art", "museum"), closed=("cluny",)):
    trip = TripRequest(destination="Paris", currency="USD", travellers=2, budget_total=2000, start_date=START,
                       end_date=END, interests=list(interests))
    places = [place(pid, "attraction", tags, lat, lon, hours=CLOSED if pid in closed else OPEN)
              for pid, tags, lat, lon in SIGHTS]
    places += [place(pid, "restaurant", ["food"], lat, lon) for pid, lat, lon in MEALS]
    return ResearchBundle(trip=trip, places=places)


def pid(short):
    return f"wv-paris-{short}"


def museum_heavy_decision(cands, *, serves=True):
    """What the live analyst did: the museums included, shopping and every restaurant left as alternatives."""
    museums = {"louvre", "orsay", "cluny", "dali", "sainte-chapelle", "notre-dame", "palais-royal",
               "opera-garnier", "sacre-coeur"}
    out = []
    for c in cands.candidates:
        short = c.place_id.removeprefix("wv-paris-")
        include = short in museums
        out.append(PlaceDecision(place_id=c.place_id, decision="include" if include else "optional",
                                 rank=len(out) + 1, reason="Fits the interests",
                                 serves=[t for t in c.tags if t in cands.trip.interests] if serves else None))
    return BudgetDecision(decisions=out)


# ------------------------------------------------------------------ semantic interest matching
def test_a_place_counts_for_an_interest_only_when_its_tags_and_the_analyst_agree():
    cands = prepare_candidates(paris())
    louvre = cands.get(pid("louvre"))
    decision = PlaceDecision(place_id=louvre.place_id, decision="include", rank=1, serves=["art", "markets"])
    assert confirmed_serves(louvre, decision, cands.trip.interests) == ["art"]  # "markets" is not in its tags
    unanswered = decision.model_copy(update={"serves": None})
    assert confirmed_serves(louvre, unanswered, cands.trip.interests) == ["history", "art", "museum"]
    keyword_only = decision.model_copy(update={"serves": []})  # the analyst says it serves none of them
    assert confirmed_serves(louvre, keyword_only, cands.trip.interests) == []


def test_an_ordinary_meal_never_counts_for_sightseeing_interests():
    cands = prepare_candidates(paris(interests=("history", "food")))
    bistro = cands.get(pid("bistro-1"))
    d = PlaceDecision(place_id=bistro.place_id, decision="include", rank=1, serves=["food", "history"])
    assert confirmed_serves(bistro, d, cands.trip.interests) == ["food"]


# ------------------------------------------------------------------ the Budget Analyst's top-up
def test_the_museum_only_selection_gets_shopping_and_meals_from_the_analysts_own_alternatives():
    cands = prepare_candidates(paris())
    report = apply_decision(cands, museum_heavy_decision(cands))
    promoted = [i.place_id.removeprefix("wv-paris-") for i in report.selected if i.promoted]
    assert "lafayette" in promoted  # shopping was asked for and no planned place served it
    assert report.interest_coverage["markets"] >= 1 and all(report.interest_coverage.values())
    restaurants = [i for i in report.selected if i.category == "restaurant"]
    assert len(restaurants) == meal_target(6) == 4  # about one meal per full day
    assert all(i.reason.startswith("Added by Python from the analyst's alternatives") for i in report.selected
               if i.promoted)
    assert len(report.promotions) == len(promoted)
    unknown = {i.name for i in report.selected if i.cost.status == "unknown"}
    assert unknown and set(report.unknown_cost_items) == unknown  # unknown, never free


def test_top_up_never_takes_an_excluded_place_or_passes_the_trips_maximum():
    cands = prepare_candidates(paris())
    decision = museum_heavy_decision(cands)
    excluded = BudgetDecision(decisions=[d.model_copy(update={"decision": "exclude", "rank": None})
                                         if d.place_id.startswith(pid("lafayette")) or "bistro" in d.place_id else d
                                         for d in decision.decisions])
    report = apply_decision(cands, excluded)
    ids = {i.place_id for i in report.selected}
    assert pid("lafayette") not in ids and not any("bistro" in i for i in ids)
    assert sum(i.category == "attraction" for i in report.selected) <= 16


def test_an_interest_no_place_serves_is_said_out_loud():
    cands = prepare_candidates(paris(interests=("history", "adventure")))
    report = apply_decision(cands, museum_heavy_decision(cands))
    assert report.interest_coverage["adventure"] == 0
    assert any(w.startswith("No planned place serves your interest 'adventure'") for w in report.warnings)
    assert report.warnings[-1].startswith("These totals cover only")  # the scope note stays last


def test_a_short_selection_with_relevant_alternatives_is_not_sent_back_to_the_llm():
    cands = prepare_candidates(paris())
    thin = BudgetDecision(decisions=[d.model_copy(update={"decision": "optional"}) if i > 2 else d
                                     for i, d in enumerate(museum_heavy_decision(cands).decisions)])
    stats: dict = {}
    ok, _ = make_guardrail(cands, stats)(thin.model_dump_json())
    assert ok and stats["rejections"] == []  # Python tops up from the alternatives: no paid retry
    report = apply_decision(cands, thin)
    assert sum(i.category == "attraction" for i in report.selected) >= 10


# ------------------------------------------------------------------ Python's day balance
def planning_context(b=None):
    b = b or paris()
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    return PlanningContext(report, b, None)


def sights_per_day(a: DayAssignment, ctx) -> list[int]:
    return [sum(ctx.stops[i].category == "attraction" for i in d.place_ids)
            for d in sorted(a.days, key=lambda d: d.date)]


def test_pythons_plan_for_six_days_has_no_empty_day_and_full_days_of_two_or_three():
    ctx = planning_context()
    a = propose(ctx)
    assert assignment_problems(a, ctx) == []  # the full validation, scheduling included
    counts = sights_per_day(a, ctx)
    usable = sum(ctx.stops[i].category == "attraction" for i in ctx.stops) - 1  # all but the closed Cluny
    # every usable sight placed, no date empty, full days of two or three, at most one lighter
    assert sum(counts) == usable >= 10 and all(counts) and max(counts) <= 3 and counts[0] <= 2 and counts[-1] <= 2
    assert sum(c < 2 for c in counts[1:-1]) <= 1
    placed = [i for d in a.days for i in d.place_ids]
    assert len(placed) == len(set(placed))  # nothing twice
    assert pid("cluny") in [u.place_id for u in a.unscheduled]  # closed all trip: said, never forced in
    assert "closed on every trip date" in next(u.reason for u in a.unscheduled if u.place_id == pid("cluny"))
    meals = [sum(ctx.stops[i].category == "restaurant" for i in d.place_ids) for d in a.days]
    assert sum(meals) == 4 and max(meals) == 1


def test_pythons_plan_keeps_each_day_in_one_neighbourhood():
    ctx = planning_context()
    p = Planner(ctx)
    a = propose(ctx)
    for d in a.days:
        assert p.long_legs(list(d.place_ids)) == 0, (d.date, d.place_ids)  # no estimated walk over 30 minutes


def test_the_live_1_4_1_0_2_1_plan_is_balanced_by_python():
    ctx = planning_context()
    sel = [i for i in ctx.stops if ctx.stops[i].category == "attraction" and i != pid("cluny")]
    live = {DATES[0]: sel[:1], DATES[1]: sel[1:5], DATES[2]: sel[5:6], DATES[3]: [], DATES[4]: sel[6:8],
            DATES[5]: sel[8:9]}
    a = DayAssignment(days=[DayChoice(date=d, place_ids=ids) for d, ids in live.items()])
    fixed, notes = complete(a, ctx)
    balanced, more = rebalance(fixed, ctx)
    assert assignment_problems(balanced, ctx) == []
    counts = sights_per_day(balanced, ctx)
    assert 0 not in counts, counts
    assert any("which had no places" in n for n in more) or any("placed by Python" in n for n in notes)
    assert score(balanced, ctx) < score(a.model_copy(update={"unscheduled": fixed.unscheduled}), ctx)


def test_the_paris_503_answer_is_repaired_in_one_call_and_never_drops_a_place():
    """Duplicates, a missing place, an unknown id and a closed date in one answer: repaired by Python."""
    ctx = planning_context()
    sel = list(ctx.stops)
    bad = DayAssignment(days=[DayChoice(date=DATES[0], place_ids=[sel[0], sel[1], sel[0]]),
                              DayChoice(date=DATES[1], place_ids=[sel[1], "wv-paris-made-up", pid("cluny")]),
                              DayChoice(date="2026-11-09", place_ids=[sel[2]])] +
                             [DayChoice(date=d) for d in DATES[2:]])
    ok, accepted = planner_guardrail(ctx)(bad.model_dump_json())
    assert ok and assignment_problems(accepted, ctx) == []
    ids = [i for d in accepted.days for i in d.place_ids] + [u.place_id for u in accepted.unscheduled]
    assert sorted(ids) == sorted(sel)  # every selected place exactly once, nothing invented
    assert "wv-paris-made-up" not in ids
    assert ctx.rejections and ctx.rejections[0]["repaired_by_python"]


def test_the_agent_gets_pythons_proposal_and_a_failed_agent_falls_back_to_it():
    b = paris()
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    llm = NativeScriptedLLM(model="scripted-test", replies=["not json at all"] * 20)
    out = plan_itinerary(report, b, llm, None)
    assert '"python_proposal"' in llm.prompts[0][-1]["content"]
    assert out.decided_by == "python_fallback" and out.itinerary is not None
    counts = [sum(a.kind != "eat" for a in d.activities) for d in out.itinerary.days]
    assert 0 not in counts and out.verified is False


def test_an_agent_plan_that_is_clearly_worse_is_replaced_by_pythons_and_said_so():
    b = paris()
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    ctx = PlanningContext(report, b, None)
    ctx.proposal = propose(ctx)
    sights = [i for i in ctx.stops if ctx.stops[i].category == "attraction" and i != pid("cluny")]
    meals = [i for i in ctx.stops if ctx.stops[i].category == "restaurant"]
    # every day mixes far-apart neighbourhoods
    zigzag = {d: [] for d in DATES}
    for n, i in enumerate(sights + meals):
        zigzag[DATES[n % 6]].append(i)
    a = DayAssignment(days=[DayChoice(date=d, place_ids=ids) for d, ids in zigzag.items()],
                      unscheduled=[{"place_id": pid("cluny"), "reason": "Closed"}])
    ok, accepted = planner_guardrail(ctx)(a.model_dump_json())
    assert ok and (ctx.decided_by == "python") == (accepted == ctx.proposal)
    assert score(accepted, ctx) <= score(a, ctx)


def test_daily_targets_for_six_days():
    assert daily_targets(6) == [1, 2, 2, 2, 2, 1]


@pytest.mark.parametrize("closed", [(), ("cluny", "louvre", "orsay")])
def test_closed_places_never_leave_an_avoidable_empty_day(closed):
    """Closed places are reported, and the remaining places still use every date."""
    ctx = planning_context(paris(closed=closed))
    report = build_itinerary(ctx, propose(ctx))
    assert not any(q.code == "empty_day" and q.avoidable for q in report.quality)
    assert all(day.activities for day in report.itinerary.days)


def test_with_enough_places_every_full_day_gets_two_or_three():
    b = paris(closed=())
    cands = prepare_candidates(b)
    every = BudgetDecision(decisions=[PlaceDecision(place_id=c.place_id, decision="include", rank=n + 1,
                                                    serves=[t for t in c.tags if t in b.trip.interests])
                                      for n, c in enumerate(cands.candidates)])
    ctx = PlanningContext(apply_decision(cands, every), b, None)
    counts = sights_per_day(propose(ctx), ctx)
    assert sum(counts) == 12 and all(2 <= c <= 3 for c in counts[1:-1]) and 1 <= counts[0] <= 2, counts


def test_a_restaurant_reached_shortly_before_lunch_waits_for_lunch():
    ctx = planning_context()
    sight = next(i for i in ctx.stops if ctx.stops[i].category == "attraction" and i != pid("cluny"))
    meal = next(i for i in ctx.stops if ctx.stops[i].category == "restaurant")
    a = DayAssignment(days=[DayChoice(date=DATES[0], place_ids=[sight, meal])] +
                      [DayChoice(date=d) for d in DATES[1:]],
                      unscheduled=[{"place_id": i, "reason": "test"} for i in ctx.stops if i not in (sight, meal)])
    report = build_itinerary(ctx, a)
    lunch = next(e for e in report.evidence if e.place_id == meal)
    assert lunch.start == "12:00" and lunch.wait_minutes > 0  # 10:30 + walk, then lunch at noon


def test_an_answer_cut_short_is_completed_by_python_instead_of_a_paid_retry():
    """The live Paris failure: 34 places, the answer stopped before the last ten decisions, three attempts."""
    from planning.budget_analyst import MISSING_REASON
    cands = prepare_candidates(paris())
    full = museum_heavy_decision(cands)
    cut = BudgetDecision(decisions=full.decisions[:12])
    stats: dict = {}
    ok, decision = make_guardrail(cands, stats)(cut.model_dump_json())
    assert ok and stats["rejections"] == [] and len(stats["filled_missing"]) == len(full.decisions) - 12
    added = decision.decisions[12:]
    assert all(d.decision == "optional" and d.reason == MISSING_REASON for d in added)
    report = apply_decision(cands, decision)
    assert {i.place_id for i in report.selected + report.optional} >= {d.place_id for d in added}


def test_an_answer_with_most_places_missing_still_goes_back_to_the_analyst():
    cands = prepare_candidates(paris())
    ok, message = make_guardrail(cands, {})(BudgetDecision(decisions=museum_heavy_decision(cands).decisions[:3])
                                            .model_dump_json())
    assert not ok and "no decision for" in message


def test_a_garbled_id_is_dropped_and_its_place_kept_as_an_alternative_without_a_retry():
    """Live Paris run 4: "wv-paris-pasteur" for "wv-paris-musee-pasteur" cost a full paid retry."""
    cands = prepare_candidates(paris())
    decisions = museum_heavy_decision(cands).decisions
    garbled = [d.model_copy(update={"place_id": "wv-paris-dal"}) if d.place_id == pid("dali") else d
               for d in decisions]
    stats: dict = {}
    ok, decision = make_guardrail(cands, stats)(BudgetDecision(decisions=garbled).model_dump_json())
    assert ok and stats["rejections"] == [] and stats["dropped_unknown_ids"] == ["wv-paris-dal"]
    assert next(d for d in decision.decisions if d.place_id == pid("dali")).decision == "optional"


def test_a_required_place_excluded_still_goes_back_to_the_analyst():
    b = paris()
    trip = b.trip.model_copy(update={"required_places": ["Louvre"]})
    cands = prepare_candidates(b.model_copy(update={"trip": trip}))
    decisions = [d.model_copy(update={"decision": "exclude", "rank": None}) if d.place_id == pid("louvre") else d
                 for d in museum_heavy_decision(cands).decisions]
    ok, message = make_guardrail(cands, {})(BudgetDecision(decisions=decisions).model_dump_json())
    assert not ok and "always included" in message


def test_a_long_walk_on_a_full_day_is_avoided_by_a_swap():
    """Live Paris run 4: an 81-minute walk on a full day; every other full day was full too, so no move fit."""
    from planning.day_balance import refine_walks
    ctx = planning_context(paris(closed=()))
    far = [pid("louvre"), pid("orsay"), pid("puces")]  # Montmartre's flea market among the Louvre's museums
    near = [pid("sacre-coeur"), pid("dali"), pid("palais-royal")]
    rest = [i for i in ctx.stops if i not in far + near and ctx.stops[i].category == "attraction"]
    a = DayAssignment(days=[DayChoice(date=DATES[1], place_ids=far), DayChoice(date=DATES[2], place_ids=near)] +
                      [DayChoice(date=d, place_ids=rest[n * 2:n * 2 + 2]) for n, d in enumerate([DATES[0]] +
                                                                                                DATES[3:])],
                      unscheduled=[{"place_id": i, "reason": "test"} for i in ctx.stops
                                   if i not in far + near + rest])
    p = Planner(ctx)
    before = sum(p.long_legs(list(d.place_ids)) for d in a.days)
    fixed, notes = refine_walks(a, ctx)
    after = sum(p.long_legs(list(d.place_ids)) for d in fixed.days)
    assert before and after < before and any("the other way" in n for n in notes)
    counts_before, counts_after = sights_per_day(a, ctx), sights_per_day(fixed, ctx)
    assert sum(counts_after) == sum(counts_before)  # the same places, none dropped
    assert all(after_n for before_n, after_n in zip(counts_before, counts_after) if before_n)  # no date emptied
    assert assignment_problems(fixed, ctx) == [] or all(p.kind != "does_not_fit" for p in assignment_problems(fixed, ctx))


def test_avoiding_a_walk_never_leaves_a_full_day_below_its_target():
    """Live Paris final run: a move to avoid a walk left Nov 5 with one place while two days had three."""
    from planning.day_balance import refine_walks
    ctx = planning_context(paris(closed=()))
    p = Planner(ctx)
    a = propose(ctx)
    targets = daily_targets(6)
    fixed, _ = refine_walks(a, ctx)
    for before, after, target in zip(sights_per_day(a, ctx), sights_per_day(fixed, ctx), targets):
        assert after >= min(before, target)


def test_a_place_closed_all_trip_is_flagged_never_counted_and_replaced_from_the_alternatives():
    """Live Barcelona run: the only planned comedy club was closed on every trip date, so entertainment and the
    trip's place count fell short although Teatre Lliure was a ranked alternative."""
    import json as _json
    from planning.budget_analyst import prompt_inputs
    cands = prepare_candidates(paris())  # Cluny: temporarily closed
    cluny = cands.get(pid("cluny"))
    assert cluny.closed_all_trip
    row = next(r for r in _json.loads(prompt_inputs(cands)["research_data"]) if r["id"] == pid("cluny"))
    assert row["closed_on_every_trip_date"] is True
    report = apply_decision(cands, museum_heavy_decision(cands))  # the analyst included Cluny anyway
    open_sights = [i for i in report.selected if i.category == "attraction" and not i.closed_all_trip]
    # the closed place did not count towards the goal: every open sightseeing alternative was used instead
    assert len(open_sights) == 11 and not [i for i in report.optional if i.category == "attraction"]
    assert not any(i.promoted and i.closed_all_trip for i in report.selected)
    assert any("closed on every trip date, so it cannot be visited" in w for w in report.warnings)


@pytest.mark.parametrize("name, description, expected", [
    ("Festes de Gràcia", "A week-long street festival every August in Gràcia.", "August"),
    ("Sónar", "Electronic music festival held each June.", "June"),
    ("Christmas Fair", "Stalls in November and December.", None),  # a trip month named: kept
    ("Jazz Festival", "Big names, great music.", None),  # no month named: not evidence
    ("Museum of August Rodin", "Sculptures from August onwards.", None),  # not an event
])
def test_an_event_named_for_other_months_is_not_during_the_trip(name, description, expected):
    from planning.budget_analyst import event_outside_trip
    b = paris()
    found = event_outside_trip(place("x", "attraction", ["entertainment"], 48.85, 2.35).model_copy(
        update={"name": name, "description": description}), b.trip)
    assert (found is None) if expected is None else (expected in found)


def test_an_event_outside_the_trip_is_left_out_by_python_even_if_the_analyst_includes_it():
    b = paris()
    fest = place("festes", "attraction", ["markets"], 48.86, 2.34).model_copy(
        update={"name": "Festes de Paris", "description": "A street festival every August."})
    b = b.model_copy(update={"places": b.places + [fest]})
    cands = prepare_candidates(b)
    decision = museum_heavy_decision(cands)
    decision = BudgetDecision(decisions=[d.model_copy(update={"decision": "include", "rank": 1})
                                         if d.place_id == fest.place_id else d for d in decision.decisions])
    report = apply_decision(cands, decision)
    assert fest.place_id not in {i.place_id for i in report.selected + report.optional}
    left = next(i for i in report.excluded if i.place_id == fest.place_id)
    assert left.reason == "Left out by Python: its listing places it in August, not during the trip"
