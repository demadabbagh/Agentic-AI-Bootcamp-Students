"""Trip coverage: a valid schedule must also be a useful trip for its length."""
import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from app.coverage import assess_coverage, calendar_dates, duration_problem, is_food_focused
from app.orchestrator import orchestrate_trip
from planning.models import Activity, DayPlan, Itinerary
from planning.research import ResearchBundle, ResearchedPlace, TripRequest

START = dt.date(2026, 11, 2)

PLACES = [
    ResearchedPlace(place_id="citadel", name="Amman Citadel", category="attraction", kind="see", tags=["history"]),
    ResearchedPlace(place_id="theatre", name="Roman Theater", category="attraction", kind="see", tags=["history"]),
    ResearchedPlace(place_id="rainbow", name="Rainbow Street", category="attraction", kind="see",
                    tags=["views", "markets"]),
    ResearchedPlace(place_id="museum", name="The Jordan Museum", category="attraction", kind="see",
                    tags=["history", "museum"]),
    ResearchedPlace(place_id="hashem", name="Hashem", category="restaurant", kind="eat", tags=["food"]),
    ResearchedPlace(place_id="jafra", name="Jafra Restaurant & Cafe", category="restaurant", kind="eat",
                    tags=["food"]),
]
BY_ID = {p.place_id: p for p in PLACES}


def bundle(days: int, interests=("history", "markets")) -> ResearchBundle:
    trip = TripRequest(destination="Amman", currency="USD", travellers=2, budget_total=2000,
                       start_date=START, end_date=START + dt.timedelta(days=days - 1),
                       interests=list(interests))
    return ResearchBundle(trip=trip, places=PLACES)


def itinerary(plan: dict[int, list[str]]) -> Itinerary:
    """{day offset: [place ids]} -> an Itinerary with those activities on those dates."""
    days = [
        DayPlan(date=START + dt.timedelta(days=offset),
                activities=[Activity(name=BY_ID[i].name, place_id=i) for i in ids])
        for offset, ids in sorted(plan.items())
    ]
    return Itinerary(destination="Amman", currency="USD", travellers=2, days=days)


# ------------------------------------------------------------------ dates and duration
def test_calendar_dates_are_inclusive():
    dates = calendar_dates("2026-11-02", "2026-11-09")
    assert len(dates) == 8
    assert dates[0] == dt.date(2026, 11, 2) and dates[-1] == dt.date(2026, 11, 9)


@pytest.mark.parametrize("duration", [8, 7, 8.0])  # 8 calendar days, 7 nights
def test_duration_matching_days_or_nights_is_accepted(duration):
    assert duration_problem({"start_date": "2026-11-02", "end_date": "2026-11-09",
                             "duration_days": duration}) is None


def test_duration_that_disagrees_with_the_dates_is_questioned():
    message = duration_problem({"start_date": "2026-11-02", "end_date": "2026-11-09", "duration_days": 4})
    assert "8 calendar days (7 nights)" in message
    assert "given as 4 days" in message


def test_one_day_trip_duration():
    trip = {"start_date": "2026-11-02", "end_date": "2026-11-02"}
    assert duration_problem({**trip, "duration_days": 1}) is None
    assert duration_problem({**trip, "duration_days": 0}) is not None  # 0 nights is not a trip length


@pytest.mark.parametrize("trip", [
    {"start_date": "2026-11-09", "end_date": "2026-11-02", "duration_days": 8},
    {"start_date": "next monday", "end_date": "2026-11-09", "duration_days": 8},
    {"start_date": "2026-11-02", "end_date": "2026-11-09", "duration_days": "eight"},
    {"start_date": "2026-11-02", "end_date": "2026-11-09", "duration_days": True},
])
def test_bad_dates_or_duration(trip):
    assert duration_problem(trip) is not None


# ------------------------------------------------------------------ coverage
def test_amman_one_meal_in_eight_days_is_insufficient():
    report = assess_coverage(itinerary({0: ["hashem"]}), bundle(8))

    assert report.trip_days == 8 and report.required_days == 4
    assert report.covered_dates == []
    assert report.meal_only_dates == [START]
    assert len(report.empty_dates) == 7
    assert not report.sufficient
    assert "Only 0 of 8 trip days" in report.insufficient_message()
    assert "at least 4" in report.insufficient_message()


def test_half_the_days_covered_is_sufficient():
    report = assess_coverage(itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"], 3: ["museum"]}),
                             bundle(8))
    assert report.sufficient
    assert len(report.covered_dates) == 4


def test_just_under_half_is_insufficient():
    report = assess_coverage(itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"]}), bundle(8))
    assert not report.sufficient


def test_odd_length_rounds_up():
    assert assess_coverage(itinerary({0: ["citadel"]}), bundle(3)).required_days == 2


def test_partially_empty_schedule_passes_with_a_warning():
    report = assess_coverage(itinerary({0: ["citadel", "hashem"], 1: ["theatre"], 2: ["jafra"]}), bundle(4))

    assert report.sufficient
    assert report.meal_only_dates == [START + dt.timedelta(days=2)]
    assert report.empty_dates == [START + dt.timedelta(days=3)]
    assert report.warning() == ("2 of 4 trip days have no sightseeing or other non-meal activities "
                                "planned: 2026-11-04, 2026-11-05.")


def test_fully_covered_plan_has_no_warning():
    report = assess_coverage(itinerary({0: ["citadel"], 1: ["theatre"]}), bundle(2))
    assert report.sufficient and report.warning() is None


def test_one_day_trip():
    assert assess_coverage(itinerary({0: ["citadel"]}), bundle(1)).sufficient
    meal_only = assess_coverage(itinerary({0: ["hashem"]}), bundle(1))
    assert meal_only.required_days == 1 and not meal_only.sufficient


def test_food_focused_trip_counts_meals():
    report = assess_coverage(itinerary({0: ["hashem"], 1: ["jafra"]}), bundle(4, interests=["food"]))
    assert report.food_focused and report.sufficient
    assert "planned activities" in report.warning()


def test_food_as_one_interest_among_several_is_not_food_focused():
    assert not is_food_focused(["history", "food"])
    assert not is_food_focused([])
    report = assess_coverage(itinerary({0: ["hashem"], 1: ["jafra"]}), bundle(4, interests=["history", "food"]))
    assert not report.sufficient


def test_kind_is_used_when_the_place_is_not_in_the_bundle():
    plan = Itinerary(destination="Amman", currency="USD", travellers=2, days=[
        DayPlan(date=START, activities=[Activity(name="Unlisted sight", kind="see")]),
        DayPlan(date=START + dt.timedelta(days=1), activities=[Activity(name="Unlisted cafe", kind="drink")]),
        DayPlan(date=START + dt.timedelta(days=2), activities=[Activity(name="Unknown")]),
    ])
    report = assess_coverage(plan, bundle(3))
    assert report.covered_dates == [START]
    assert report.meal_only_dates == [START + dt.timedelta(days=1)]
    assert report.empty_dates == [START + dt.timedelta(days=2)]  # says neither: not counted


# ------------------------------------------------------------------ the orchestrator
TRIP_INFO = {"intent": "create_trip", "destination": "Amman", "duration_days": 8, "budget": 2000,
             "currency": "USD", "travellers": 2, "start_date": "2026-11-02", "end_date": "2026-11-09",
             "interests": ["History & Culture", "Shopping"]}


def run(plan: Itinerary, research: ResearchBundle, trip_info=TRIP_INFO, verified=True):
    report = MagicMock(selected=[], optional=[], excluded=[], withheld=[], dropped_for_budget=[], warnings=[])
    planning = {"status": "complete", "itinerary": plan.to_trip_store(), "itinerary_model": plan,
                "budget_report": report, "planner_report": MagicMock(warnings=[])}
    verification = {"verified": verified, "itinerary_model": plan, "message": "Not verified."}
    with (
        patch("app.orchestrator.understand_trip_request", return_value=dict(trip_info)),
        patch("app.orchestrator.run_research_pipeline", return_value=research) as research_mock,
        patch("app.orchestrator.create_hours_lookup"),
        patch("app.orchestrator.create_travel_lookup"),
        patch("app.orchestrator.create_planning_llm"),
        patch("app.orchestrator.run_planning_pipeline", return_value=planning),
        patch("app.orchestrator.run_verification_pipeline", return_value=verification) as verify_mock,
    ):
        return orchestrate_trip("plan Amman"), research_mock, verify_mock


def test_orchestrator_stops_a_thin_plan_before_verification():
    result, _, verify_mock = run(itinerary({0: ["hashem"]}), bundle(8))

    assert result["status"] == "needs_revision"
    assert "Only 0 of 8 trip days" in result["message"]
    assert "itinerary" not in result
    assert result["coverage"]["sufficient"] is False
    verify_mock.assert_not_called()


def test_orchestrator_passes_a_covered_plan_and_warns_about_empty_days():
    plan = itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"], 3: ["museum"]})
    result, _, verify_mock = run(plan, bundle(8))

    verify_mock.assert_called_once()
    assert result["status"] == "verified"
    assert result["message"].startswith("The itinerary passed real verification")
    assert "4 of 8 trip days have no sightseeing" in result["message"]
    assert result["coverage"]["empty_dates"] == ["2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"]


def test_orchestrator_warning_also_reaches_unverified_plans():
    plan = itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"], 3: ["museum"]})
    result, _, _ = run(plan, bundle(8), verified=False)

    assert result["status"] == "verification_required"
    assert result["message"].startswith("Not verified.")
    assert "4 of 8 trip days" in result["message"]


def test_orchestrator_rechecks_coverage_after_a_revision():
    planned = itinerary({0: ["citadel"], 1: ["theatre"]})
    revised = itinerary({0: ["hashem"]})
    research = bundle(2)
    report = MagicMock(selected=[], optional=[], excluded=[], withheld=[], dropped_for_budget=[], warnings=[])
    planning = {"status": "complete", "itinerary": planned.to_trip_store(), "itinerary_model": planned,
                "budget_report": report, "planner_report": MagicMock(warnings=[])}
    with (
        patch("app.orchestrator.understand_trip_request",
              return_value={**TRIP_INFO, "duration_days": 2, "end_date": "2026-11-03"}),
        patch("app.orchestrator.run_research_pipeline", return_value=research),
        patch("app.orchestrator.create_hours_lookup"),
        patch("app.orchestrator.create_travel_lookup"),
        patch("app.orchestrator.create_planning_llm"),
        patch("app.orchestrator.run_planning_pipeline", return_value=planning),
        patch("app.orchestrator.run_verification_pipeline",
              return_value={"verified": True, "itinerary_model": revised}),
    ):
        result = orchestrate_trip("plan Amman")

    assert result["status"] == "needs_revision"
    assert "itinerary" not in result


def test_orchestrator_asks_about_a_duration_that_disagrees_with_the_dates():
    result, research_mock, _ = run(itinerary({0: ["citadel"]}), bundle(8),
                                   trip_info={**TRIP_INFO, "duration_days": 5})

    assert result["status"] == "needs_clarification"
    assert "8 calendar days (7 nights)" in result["message"]
    research_mock.assert_not_called()


def test_changed_dates_refresh_a_remembered_duration():
    from app.coverage import refreshed_duration

    before = {"start_date": "2026-11-02", "end_date": "2026-11-09", "duration_days": 8}
    moved = {**before, "end_date": "2026-11-05"}
    assert refreshed_duration(before, moved) == 4
    assert refreshed_duration(before, before) is None  # dates unchanged
    assert refreshed_duration(before, {**moved, "duration_days": 6}) is None  # restated: checked instead
    assert refreshed_duration({"duration_days": 5}, {"start_date": "2026-11-02", "end_date": "2026-11-09",
                                                     "duration_days": 5}) is None  # no earlier dates


def test_orchestrator_uses_the_refreshed_duration_in_a_session():
    from app.memory import sessions

    sessions["dates-session"] = {"itinerary": None, "pending_action": None,
                                 "trip_details": {k: v for k, v in TRIP_INFO.items() if k != "intent"}}
    moved = {**TRIP_INFO, "end_date": "2026-11-05"}  # Coordinator echoes the old duration_days: 8
    plan = itinerary({n: ["citadel"] for n in range(4)})
    report = MagicMock(selected=[], optional=[], excluded=[], withheld=[], dropped_for_budget=[], warnings=[])
    planning = {"status": "complete", "itinerary": plan.to_trip_store(), "itinerary_model": plan,
                "budget_report": report, "planner_report": MagicMock(warnings=[])}
    try:
        with (
            patch("app.orchestrator.understand_trip_request", return_value=moved),
            patch("app.orchestrator.run_research_pipeline", return_value=bundle(4)) as research_mock,
            patch("app.orchestrator.create_hours_lookup"),
            patch("app.orchestrator.create_travel_lookup"),
            patch("app.orchestrator.create_planning_llm"),
            patch("app.orchestrator.run_planning_pipeline", return_value=planning),
            patch("app.orchestrator.run_verification_pipeline",
                  return_value={"verified": True, "itinerary_model": plan}),
        ):
            result = orchestrate_trip("Actually end on the 5th", session_id="dates-session")

        assert result["status"] == "verified"
        assert research_mock.call_args.args[0]["duration_days"] == 4
        assert sessions["dates-session"]["trip_details"]["duration_days"] == 4
        assert sessions["dates-session"]["coverage_context"]["end_date"] == "2026-11-05"
    finally:
        sessions.pop("dates-session", None)


# ------------------------------------------------------------------ the revise_trip path
def _revise(session: dict, revised: dict):
    from app.memory import sessions

    sessions["revise-session"] = session
    try:
        with (
            patch("app.orchestrator.understand_trip_request", return_value={"intent": "revise_trip"}),
            patch("app.orchestrator.revise_itinerary", return_value=revised),
        ):
            return orchestrate_trip("Swap day two", session_id="revise-session")
    finally:
        sessions.pop("revise-session", None)


def _store(plan: dict[int, list[str]]) -> dict:
    return itinerary(plan).to_trip_store()


def test_revision_is_checked_against_the_stored_coverage_context():
    from app.coverage import coverage_context

    session = {"itinerary": _store({0: ["citadel"], 1: ["theatre"]}), "pending_action": None,
               "trip_details": {}, "coverage_context": coverage_context(bundle(2))}

    thin = _revise(session, _store({0: ["hashem"]}))
    assert thin["status"] == "needs_revision"
    assert "itinerary" not in thin

    ok = _revise(session, _store({0: ["citadel"]}))
    assert ok["status"] == "verification_required"  # still never approvable from here
    assert ok["coverage"]["sufficient"] is True
    assert "1 of 2 trip days have no sightseeing" in ok["message"]


def test_revision_without_coverage_context_fails_closed():
    session = {"itinerary": _store({0: ["citadel"]}), "pending_action": None, "trip_details": {}}
    result = _revise(session, _store({0: ["citadel"]}))
    assert result["status"] == "needs_revision"
    assert "itinerary" not in result


def test_orchestrator_accepts_nights_as_the_duration():
    plan = itinerary({n: ["citadel"] for n in range(8)})
    result, _, _ = run(plan, bundle(8), trip_info={**TRIP_INFO, "duration_days": 7})
    assert result["status"] == "verified"


# ------------------------------------------------------------------ density: valid and covered is not full
from types import SimpleNamespace

from app.coverage import assess_density


def _budget(selected=4, excluded_matching=0, dropped=0):
    att = lambda tags: SimpleNamespace(category="attraction", tags=tags)
    return SimpleNamespace(selected=[att(["history"])] * selected,
                           excluded=[att(["history"])] * excluded_matching,
                           dropped_for_budget=[object()] * dropped)


def test_one_activity_a_day_for_five_days_is_light_and_says_why():
    plan = itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"], 3: ["museum"], 4: ["citadel"]})
    research = bundle(5).model_copy(update={"places": PLACES + [  # 13 matching attractions: supply is enough
        ResearchedPlace(place_id=f"extra{n}", name=f"Extra {n}", category="attraction", kind="see",
                        tags=["history"]) for n in range(9)]})
    report = assess_density(plan, research, _budget(selected=4, excluded_matching=9))

    assert report.targets == [1, 2, 2, 2, 1]
    assert report.non_meal_per_day == [1, 1, 1, 1, 1]
    assert report.level == "light"
    assert [d.isoformat() for d in report.light_dates] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    message = report.message()
    assert "5 sightseeing activities over 5 days" in message
    assert "about 8 to 13" in message
    assert "the Budget Analyst selected 4 of 13 matching attractions (9 excluded)" in message


def test_meeting_every_daily_target_is_full():
    plan = itinerary({0: ["citadel"], 1: ["theatre", "rainbow"], 2: ["museum", "citadel"], 3: ["theatre", "rainbow"],
                      4: ["museum"]})
    report = assess_density(plan, bundle(5), _budget(selected=8))
    assert report.level == "full" and report.message() is None
    assert report.as_dict()["light_dates"] == []


def test_meals_never_count_as_sightseeing():
    plan = itinerary({0: ["citadel", "hashem"], 1: ["hashem", "jafra"]})
    report = assess_density(plan, bundle(2), _budget(selected=1))
    assert report.non_meal_per_day == [1, 0]
    assert report.meals == 3


def test_density_explains_a_research_shortage():
    plan = itinerary({0: ["citadel"], 1: ["theatre"], 2: ["rainbow"]})
    report = assess_density(plan, bundle(5), _budget(selected=3))
    assert report.matching_attractions == 4 < report.target_min == 8
    assert "not enough feasible activities" in report.message()


def test_density_explains_places_that_could_not_be_scheduled():
    research = bundle(1)
    plan = itinerary({0: ["citadel"]})
    planner = SimpleNamespace(unscheduled=[object()])
    research = research.model_copy(update={"places": PLACES + [
        ResearchedPlace(place_id=f"extra{n}", name=f"Extra {n}", category="attraction", kind="see",
                        tags=["history"]) for n in range(8)]})
    report = assess_density(plan, research, _budget(selected=8), planner)
    assert "could not be scheduled" in report.message()


def test_orchestrator_labels_a_light_plan_but_keeps_it_verified():
    plan = itinerary({n: ["citadel"] for n in range(8)})
    result, _, _ = run(plan, bundle(8))

    assert result["status"] == "verified"  # density is advice, never a gate
    assert "Light itinerary: 8 sightseeing activities over 8 days" in result["message"]
    assert result["plan_quality"]["level"] == "light"
    assert result["coverage"]["sufficient"] is True
