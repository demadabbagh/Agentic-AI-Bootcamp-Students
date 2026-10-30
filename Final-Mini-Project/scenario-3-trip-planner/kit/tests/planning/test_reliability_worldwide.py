"""Rides for long walks, the Verifier's content checks, the budget breakdown, and worldwide inputs. Offline.

Every scenario here is a regression of a live run (Tokyo's 135-minute walk, Barcelona's August festival in
November, Paris's two Louvre listings) or a worldwide input the planning code must not special-case: accented
names, the southern hemisphere, the date line, sparse research, one-day and multi-week trips.
"""
import datetime as dt
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest

from planning.budget_analyst import (BudgetDecision, PlaceDecision, apply_decision, prepare_candidates)
from planning.day_balance import Planner, propose
from planning.itinerary_planner import PlanningContext, assignment_problems, build_itinerary
from planning.research import ResearchBundle, ResearchedPlace, TripRequest
from planning.schedule import ScheduleRules, Stop, TravelTimes, schedule_day
from planning.verification import _km, verify_itinerary
from test_personalization import OPEN, museum_heavy_decision, paris, pid, place


def everything(cands):
    return BudgetDecision(decisions=[PlaceDecision(place_id=c.place_id, decision="include", rank=n + 1,
                                                   serves=[t for t in c.tags if t in cands.trip.interests])
                                     for n, c in enumerate(cands.candidates)])


def plan(b, lookup=None, decision=None, **rules):
    cands = prepare_candidates(b)
    report = apply_decision(cands, decision(cands) if decision else museum_heavy_decision(cands))
    ctx = PlanningContext(report, b, lookup, rules=ScheduleRules.from_trip(b.trip, **rules) if rules else None)
    return cands, report, ctx, build_itinerary(ctx, propose(ctx))


# ------------------------------------------------------------------ a long walk becomes a measured ride
def ride_lookup(walk=80, ride=17):
    def lookup(a, b):
        return {"ok": True, "duration_min": ride, "profile": "driving", "walk_duration_min": walk,
                "citation": {"id": f"osrm-driving-{a}-{b}"}, "mode": "live"}
    return lookup


def stop(name, lat, lon, category="attraction"):
    return Stop(place_id=f"p-{name}", name=name, category=category, kind="see", lat=lat, lon=lon)


def test_a_ride_leg_is_scheduled_with_the_ride_time_and_said_to_be_a_ride():
    a, b = stop("Mohri Garden", 35.66, 139.73), stop("Hama-rikyu", 35.66, 139.76)
    day = schedule_day(dt.date(2026, 11, 11), [a, b], ScheduleRules(max_walk_minutes=30),
                       TravelTimes(ride_lookup()))
    second = day.scheduled[1]
    assert (second.travel.mode, second.travel.minutes, second.travel.walk_minutes) == ("driving", 17, 80)
    assert second.gap_minutes == 17
    codes = [i.code for i in day.issues]
    assert "ride_planned" in codes and "walk_over_limit" not in codes
    from planning.schedule import activity_fields
    fields = activity_fields(second)
    assert fields["travel_mode_from_previous"] == "driving" and fields["travel_minutes_from_previous"] == 17
    assert "By taxi or car from the previous stop: 17 min (walking: 80 min)" in fields["notes"]
    assert "fare not included" in fields["notes"]


def test_the_verifier_reports_a_ride_as_a_ride_not_a_walk_over_the_limit():
    b = paris(closed=())
    b = b.model_copy(update={"trip": b.trip.model_copy(update={"max_walk_minutes": 30})})
    cands, report, ctx, planner = plan(b, ride_lookup())
    out = verify_itinerary(planner.itinerary, b, budget_report=report, planner_report=planner,
                           travel_lookup=ride_lookup())
    codes = {i.code for i in out.issues + out.unknowns + out.warnings}
    assert "ride_planned" in codes and "walk_over_limit" not in codes and "insufficient_travel_gap" not in codes
    ride = next(i for i in out.warnings if i.code == "ride_planned")
    assert "would take 80 minutes" in ride.explanation and "the fare is not in the budget" in ride.explanation


# ------------------------------------------------------------------ the Verifier's content checks
def test_a_festival_outside_its_months_is_a_confirmed_problem_removed_by_revision():
    b = paris(closed=())
    fest = place("festes", "attraction", ["markets"], 48.861, 2.336).model_copy(
        update={"name": "Festes de Paris", "description": "A street festival every August."})
    b = b.model_copy(update={"places": b.places + [fest]})
    cands, report, ctx, planner = plan(b, decision=everything)
    assert fest.place_id not in planner.scheduled_place_ids  # never planned: the Budget Analyst left it out
    # A plan that has it anyway (built from an older report) is flagged by the Verifier
    from planning.models import Activity, DayPlan, Itinerary
    day = DayPlan(date=dt.date(2026, 11, 3), activities=[Activity(name=fest.name, place_id=fest.place_id,
                                                                   kind="see", start_time="10:00",
                                                                   end_time="11:30")])
    it = Itinerary(destination="Paris", currency="USD", travellers=2, budget_total=2000, days=[day])
    out = verify_itinerary(it, b)
    finding = next(i for i in out.issues if i.code == "event_outside_dates")
    assert finding.recommended_action == "remove_activity" and "August" in finding.explanation


def test_the_same_place_under_two_names_is_a_duplicate():
    b = paris(closed=())
    copy = place("the-louvre", "attraction", ["art", "museum"], 48.8606, 2.3376).model_copy(
        update={"name": "The Louvre"})
    b = b.model_copy(update={"places": b.places + [copy]})
    from planning.models import Activity, DayPlan, Itinerary
    acts = [Activity(name="Louvre", place_id=pid("louvre"), kind="see", start_time="09:00", end_time="10:30"),
            Activity(name="The Louvre", place_id=copy.place_id, kind="see", start_time="11:00", end_time="12:30")]
    it = Itinerary(destination="Paris", currency="USD", travellers=2, budget_total=2000,
                   days=[DayPlan(date=dt.date(2026, 11, 3), activities=acts)])
    out = verify_itinerary(it, b)
    assert any(i.code == "duplicate_identity" for i in out.issues)


def test_a_place_far_from_the_destination_and_an_unserved_interest_are_reported():
    b = paris(interests=("history", "markets"), closed=())
    far = place("versailles-like", "attraction", ["history"], 48.0, 1.5)  # about 110 km away
    b = b.model_copy(update={"places": b.places + [far]})
    from planning.models import Activity, DayPlan, Itinerary
    acts = [Activity(name=far.name, place_id=far.place_id, kind="see", start_time="09:00", end_time="10:30")]
    it = Itinerary(destination="Paris", currency="USD", travellers=2, budget_total=2000,
                   days=[DayPlan(date=dt.date(2026, 11, 3), activities=acts)])
    out = verify_itinerary(it, b)
    codes = {i.code for i in out.warnings}
    assert {"location_far", "interest_not_covered"} <= codes


def test_an_overloaded_day_and_a_day_without_sightseeing_are_flagged():
    from planning.models import Activity, DayPlan, Itinerary
    b = paris(closed=())
    six = [Activity(name=p.name, place_id=p.place_id, kind="see", start_time=f"{8 + n * 2:02d}:00",
                    end_time=f"{8 + n * 2:02d}:45") for n, p in enumerate(b.places[:6])]
    it = Itinerary(destination="Paris", currency="USD", travellers=2, budget_total=2000,
                   days=[DayPlan(date=dt.date(2026, 11, 2), activities=six)])
    codes = [i.code for i in verify_itinerary(it, b).warnings]
    assert "day_overloaded" in codes and "day_without_sightseeing" in codes


def test_a_clean_plan_gets_none_of_the_new_findings():
    b = paris(closed=())
    cands, report, ctx, planner = plan(b, decision=everything)
    out = verify_itinerary(planner.itinerary, b, budget_report=report, planner_report=planner)
    codes = {i.code for i in out.issues + out.unknowns + out.warnings}
    assert not codes & {"duplicate_identity", "event_outside_dates", "location_far", "interest_not_covered",
                        "day_overloaded", "day_without_sightseeing"}


# ------------------------------------------------------------------ the budget breakdown
@pytest.mark.parametrize("budget, travellers, status", [(10, 1, "over_budget"), (2000, 2, "undetermined"),
                                                        (20000, 6, "undetermined")])
def test_the_breakdown_separates_known_unknown_and_not_included_costs(budget, travellers, status):
    b = paris(closed=())
    trip = b.trip.model_copy(update={"budget_total": budget, "travellers": travellers})
    b = b.model_copy(update={"trip": trip,
                             "places": [p.model_copy(update={"price": {"raw": "EUR 12", "parsed": True,
                                                                       "currency": "USD", "amounts": {"USD": [12]},
                                                                       "min": 12, "max": 12}})
                                        if p.place_id == pid("louvre") else p for p in b.places]})
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    bd = report.breakdown
    assert bd["travellers"] == travellers and bd["currency"] == "USD"
    assert bd["items"]["unknown_price"] == len(bd["unknown_price_items"]) > 0  # unknown, never zero
    assert "accommodation" in bd["not_included"] and bd["confidence"] in ("low", "medium")
    assert bd["meals"]["trip_needs_about"] == 12
    louvre = next(i for i in report.selected + report.dropped_for_budget if i.place_id == pid("louvre"))
    assert louvre.cost.total_max == 12 * travellers  # per person, for the whole party
    if status == "over_budget":  # the only priced place costs more than the budget: dropped, and said what it takes
        assert [i.place_id for i in report.dropped_for_budget] == [pid("louvre")]
        assert any(s.startswith("A budget of 12 USD for these entry fees and meals would keep Louvre")
                   for s in bd["suggestions"])
    else:
        assert report.status == status and not report.dropped_for_budget
    assert any(s.startswith("Check the prices of") for s in bd["suggestions"])


def test_a_budget_too_small_for_the_places_asked_for_says_how_much_is_needed():
    b = paris(closed=())
    trip = b.trip.model_copy(update={"budget_total": 5, "required_places": ["Louvre"]})
    priced = [p.model_copy(update={"price": {"raw": "USD 40", "parsed": True, "currency": "USD",
                                             "amounts": {"USD": [40]}, "min": 40, "max": 40}})
              if p.place_id == pid("louvre") else p for p in b.places]
    b = b.model_copy(update={"trip": trip, "places": priced})
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    assert report.status == "infeasible" and report.breakdown["confidence"] == "low"
    assert any("Raise the budget to at least 80" in s for s in report.breakdown["suggestions"])


# ------------------------------------------------------------------ worldwide inputs
def test_distances_are_right_across_the_date_line_and_the_southern_hemisphere():
    assert _km(-18.14, 178.44, -18.14, -179.99) < 200  # Suva to just across the date line: not 40 000 km
    assert abs(_km(-33.8568, 151.2153, -33.8708, 151.2073) - 1.7) < 0.3  # Sydney Opera House to the CBD


def city(name, centre, n=10, interests=("nature",), hours=OPEN, coords=True):
    lat, lon = centre
    trip = TripRequest(destination=name, currency="EUR", travellers=1, budget_total=300,
                       start_date="2026-12-29", end_date="2027-01-02", interests=list(interests))
    places = [ResearchedPlace(place_id=f"wv-x-{k}", name=f"Lugar {k} São Ñandú", category="attraction",
                              kind="see", tags=list(interests), hours=hours,
                              lat=lat + 0.004 * k if coords else None, lon=lon + 0.003 * (k % 3) if coords else None)
              for k in range(n)]
    return ResearchBundle(trip=trip, places=places)


@pytest.mark.parametrize("name, centre", [("São Paulo", (-23.55, -46.63)), ("Reykjavík", (64.14, -21.94)),
                                          ("Suva", (-18.14, 178.44)), ("Ōsaka", (34.69, 135.50))])
def test_any_destination_plans_the_same_way_across_new_year(name, centre):
    b = city(name, centre)
    cands, report, ctx, planner = plan(b, decision=everything)
    assert planner.itinerary is not None and planner.itinerary.destination == name
    assert [str(d.date) for d in planner.itinerary.days][0] == "2026-12-29"
    assert [str(d.date) for d in planner.itinerary.days][-1] == "2027-01-02"  # over the year boundary
    assert all(d.activities for d in planner.itinerary.days)


def test_sparse_research_with_no_coordinates_and_no_hours_still_plans_honestly():
    b = city("Ulaanbaatar", (47.92, 106.92), n=6, hours=None, coords=False)
    cands, report, ctx, planner = plan(b, decision=everything)
    assert planner.itinerary is not None and assignment_problems(planner.assignment, ctx) == []
    assert planner.hours_uncertain  # unknown hours are said, never assumed
    assert all(e.travel_status in (None, "not_looked_up") for e in planner.evidence)  # no route invented


@pytest.mark.parametrize("days, expect", [(1, (1, 2)), (14, (2, 3))])
def test_one_day_and_two_week_trips(days, expect):
    b = city("Lisboa", (38.72, -9.14), n=40)
    end = dt.date(2026, 12, 29) + dt.timedelta(days=days - 1)
    b = b.model_copy(update={"trip": b.trip.model_copy(update={"end_date": end})})
    cands, report, ctx, planner = plan(b, decision=everything)
    counts = [len(d.activities) for d in planner.itinerary.days]
    assert len(counts) == days and all(counts)
    assert max(counts) <= 4 and min(counts[1:-1] or counts) >= expect[0]


def test_no_planning_module_names_a_city():
    """No city-specific logic in the planning code (the research fixtures are Istanbul and Amman)."""
    from pathlib import Path
    import re
    root = Path(__file__).resolve().parents[2] / "planning"
    text = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py")).lower()
    code = re.sub(r'"""[\s\S]*?"""|#.*', "", text)  # docstrings and comments may give examples
    assert not re.search(r"\b(paris|tokyo|barcelona|amman|istanbul|london)\b", code)


def test_over_budget_drops_a_paid_place_never_the_unpriced_ones_that_cannot_help():
    """The drop loop used to remove the lowest-ranked places first, unknown prices included: dropping those
    cannot lower the known total, so every unpriced place was lost before the one priced place that broke it."""
    b = paris(closed=())
    trip = b.trip.model_copy(update={"budget_total": 10, "travellers": 1})
    priced = [p.model_copy(update={"price": {"raw": "USD 12", "parsed": True, "currency": "USD",
                                             "amounts": {"USD": [12]}, "min": 12, "max": 12}})
              if p.place_id == pid("louvre") else p for p in b.places]
    b = b.model_copy(update={"trip": trip, "places": priced})
    cands = prepare_candidates(b)
    report = apply_decision(cands, museum_heavy_decision(cands))
    assert [i.place_id for i in report.dropped_for_budget] == [pid("louvre")]
    assert len([i for i in report.selected if i.category == "attraction"]) >= 8  # the unpriced places stay


def test_each_interest_gets_a_fair_share_from_the_alternatives():
    """Live Paris: 2 shopping places against 11-12 for history and art, though shopping alternatives existed."""
    cands = prepare_candidates(paris(closed=()))
    report = apply_decision(cands, museum_heavy_decision(cands))  # shopping places only as alternatives
    goal_share = 13 // (len(cands.trip.interests) + 1)
    assert report.interest_coverage["markets"] >= goal_share
    assert all(n >= 1 for n in report.interest_coverage.values())


def test_a_late_dinner_does_not_make_a_normal_day_overloaded():
    """Live Paris: 3 sights and a 19:30 dinner were called 'a lot' because the span with breaks was 11.5 h."""
    from planning.models import Activity, DayPlan, Itinerary
    b = paris(closed=())
    acts = [Activity(name=p.name, place_id=p.place_id, kind="see", start_time=s, end_time=e)
            for p, (s, e) in zip(b.places[:3], [("09:00", "10:30"), ("10:33", "12:03"), ("12:18", "13:48")])]
    acts.append(Activity(name="Bistro 1", place_id=pid("bistro-1"), kind="eat", start_time="19:30", end_time="20:30"))
    it = Itinerary(destination="Paris", currency="USD", travellers=2, budget_total=2000,
                   days=[DayPlan(date=dt.date(2026, 11, 6), activities=acts)])
    assert "day_overloaded" not in [i.code for i in verify_itinerary(it, b).warnings]


def test_a_one_day_trip_is_never_crammed_with_every_selected_place():
    """Live Lisbon (one day): the analyst included 5 sights and all 5 were scheduled in one day."""
    from planning.day_balance import complete, rebalance
    from planning.itinerary_planner import DayAssignment, DayChoice
    b = city("Lisboa", (38.72, -9.14), n=6)
    b = b.model_copy(update={"trip": b.trip.model_copy(update={"end_date": dt.date(2026, 12, 29)})})
    cands = prepare_candidates(b)
    report = apply_decision(cands, everything(cands))
    ctx = PlanningContext(report, b, None)
    crammed = DayAssignment(days=[DayChoice(date=dt.date(2026, 12, 29), place_ids=list(ctx.stops))])
    fixed, _ = complete(crammed, ctx)
    balanced, notes = rebalance(fixed, ctx)
    p = Planner(ctx)
    assert len(p.sights(balanced.days[0].place_ids)) == p.hard_max(dt.date(2026, 12, 29)) == 4
    left = [u.place_id for u in balanced.unscheduled]
    assert left == ["wv-x-5", "wv-x-4"] or sorted(left) == ["wv-x-4", "wv-x-5"]  # the least important ones
    assert all(u.reason.startswith("Python: ") for u in balanced.unscheduled)
    assert assignment_problems(balanced, ctx) == []
