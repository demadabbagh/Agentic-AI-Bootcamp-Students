"""Unit tests for planning/schedule.py, the deterministic scheduler. No live service, no LLM.

Places, hours and coordinates come from the recorded research fixtures; travel times from the recorded
travel_time results (tests/fixtures/*_routes.json). Hours texts for edge cases are real Wikivoyage strings from
the kit's cache, parsed by the kit's own parse_hours. Trip dates: 2026-11-02 is a Monday.
"""
import datetime as dt

import pytest
from pydantic import ValidationError

from planning.budget import summarize
from planning.budget_analyst import BudgetDecision, PlaceDecision, apply_decision, prepare_candidates
from planning.models import DayPlan, Itinerary
from planning.research import TripRequest
from planning.schedule import (DEFAULT_DURATIONS, DaySchedule, ScheduleRules, Stop, TravelTimes, activity_fields,
                               availability, hours_on, schedule_day, schedule_trip, to_minutes, trip_dates,
                               validate_day)
from research_fixtures import bundle, raw, recorded_lookup
from services.travel_data import parse_hours

MON, TUE, WED, THU, FRI = (dt.date(2026, 11, d) for d in (2, 3, 4, 5, 6))
JUL = dt.date(2027, 7, 6)
IST, AMM = {p.name: p for p in bundle().places}, {p.name: p for p in bundle("amman").places}
TOPKAPI, HAGIA, CISTERN = IST["Topkapı Palace"], IST["Hagia Sophia"], IST["Basilica Cistern"]
SULEYMANIYE, DOLMABAHCE, ALI_USTA = IST["Süleymaniye Mosque"], IST["Dolmabahce Palace"], IST["Ali Usta Çiğ Köfte"]
OSMAN, BAZAAR = IST["Osman Gourmet Restaurant"], IST["Grand Bazaar"]
CITADEL, CAVE = AMM["Amman Citadel"], AMM["Cave of the Seven Sleepers"]


def stops(*places, required=()):
    return [Stop.from_place(p, required=p.name in required) for p in places]


def travel(name="istanbul", **kw):
    return TravelTimes(recorded_lookup(name), **kw)


def day(date, *places, rules=None, city="istanbul", **kw):
    schedule = schedule_day(date, stops(*places), rules or ScheduleRules(**kw), travel(city))
    assert validate_day(schedule) == []  # every schedule the engine builds must satisfy its own invariants
    return schedule


def times(schedule):
    return [(s.name, s.start, s.end) for s in schedule.scheduled]


def codes(schedule):
    return [i.code for i in schedule.issues]


# ------------------------------------------------------------------ opening hours
def test_topkapi_is_closed_on_tuesday_and_open_on_monday():
    assert hours_on(TOPKAPI.hours, TUE).status == "closed"
    mon = hours_on(TOPKAPI.hours, MON)
    assert (mon.status, mon.windows_text, mon.weekday) == ("open", "09:00-17:00", "Mon")


def test_topkapi_is_never_scheduled_on_tuesday():
    schedule = day(TUE, TOPKAPI)
    assert schedule.scheduled == [] and schedule.stops[0].status == "closed"
    assert codes(schedule) == ["closed_on_date"] and not schedule.complete
    assert times(day(MON, TOPKAPI)) == [("Topkapı Palace", "09:00", "10:30")]


def test_suleymaniye_has_shorter_friday_hours():
    assert hours_on(SULEYMANIYE.hours, FRI).windows_text == "08:30-13:30"
    assert hours_on(SULEYMANIYE.hours, THU).windows_text == "08:30-17:00"
    # Topkapı 09:00-10:30, 14 min, Cistern 10:44-12:14, 25 min: arrival 12:39 + 90 min > 13:30 on Friday.
    friday = day(FRI, TOPKAPI, CISTERN, SULEYMANIYE)
    assert friday.stops[2].status == "does_not_fit" and "08:30-13:30" in friday.stops[2].reason
    assert times(day(THU, TOPKAPI, CISTERN, SULEYMANIYE))[2] == ("Süleymaniye Mosque", "12:39", "14:09")


def test_24_hour_opening():
    h = hours_on(HAGIA.hours, TUE)
    assert (h.status, h.windows) == ("open", [(0, 1440)])
    assert times(day(TUE, HAGIA)) == [("Hagia Sophia", "09:00", "10:30")]  # the planning day still applies


@pytest.mark.parametrize("raw_hours, window, past_midnight", [
    ("Daily 09:00-02:00", (540, 1440), True),   # Osman Gourmet Restaurant
    ("Daily 12:30-00:30", (750, 1440), True),   # Palatium Cafe & Restaurant
    ("Daily 09:00-00:00", (540, 1440), False),  # Cafe Amedros: closes at midnight
])
def test_past_midnight_hours_run_to_the_end_of_the_day(raw_hours, window, past_midnight):
    h = hours_on(parse_hours(raw_hours), MON)
    assert h.status == "open" and h.windows == [window]
    assert ("past midnight" in (h.note or "")) is past_midnight


def test_past_midnight_restaurant_is_scheduled_for_dinner():
    schedule = day(MON, OSMAN, day_start="19:00")
    assert times(schedule) == [("Osman Gourmet Restaurant", "19:00", "20:00")] and codes(schedule) == []


def test_missing_hours_are_scheduled_tentatively_and_never_called_open():
    h = hours_on(CITADEL.hours, TUE)  # Amman Citadel: the listing gives no hours
    assert (h.status, h.windows) == ("unknown", [])
    schedule = day(TUE, CITADEL, city="amman")
    assert times(schedule) == [("Amman Citadel", "09:00", "10:30")]
    assert codes(schedule) == ["hours_unknown"]
    assert schedule.complete and schedule.uncertain  # completeness and uncertainty are separate
    assert activity_fields(schedule.scheduled[0])["notes"] == "Opening hours unknown: check before you go"


@pytest.mark.parametrize("raw_hours, date, status, windows_text", [
    ("May-Oct 09:00-21:00, Nov-Apr 09:00-19:00", MON, "unconfirmed", "09:00-19:00"),  # Sultanahmet Mosque
    ("May-Oct 09:00-21:00, Nov-Apr 09:00-19:00", JUL, "unconfirmed", "09:00-21:00"),
    ("April-Sep daily 10:00-18:00", MON, "unknown", ""),  # Marina Aquapark: no November hours
    ("Daily 8:00-19:00 (summer), 8:00-17:00 (winter)", MON, "unconfirmed", "08:00-17:00"),  # narrowest
    ("Tue-Thu Sat Sun 10:00-18:00, Fri to 20:00", FRI, "unconfirmed", "00:00-20:00"),  # closing time only
    ("Tue-Thu Sat Sun 10:00-18:00, Fri to 20:00", MON, "closed", ""),
    ("Temporarily closed", WED, "closed", ""),
    ("Sat", MON, "unknown", ""),  # Beşiktaş Market: text the parser cannot read
    ("09:00-17:00", MON, "unconfirmed", "09:00-17:00"),  # times without days
    ("Daily 09:00-18:30, 19:30-22:00", MON, "open", "09:00-18:30, 19:30-22:00"),  # a real split day
])
def test_real_hours_texts(raw_hours, date, status, windows_text):
    h = hours_on(parse_hours(raw_hours), date)
    assert (h.status, h.windows_text) == (status, windows_text)
    assert h.raw == raw_hours and (status == "open" or h.note)


def test_unconfirmed_hours_are_scheduled_inside_the_listed_times_with_a_warning():
    cafe = Stop(place_id="x", name="Mock Late Museum", category="attraction", kind="see",
                hours=parse_hours("Tue-Thu Sat Sun 10:00-18:00, Fri to 20:00"))
    schedule = schedule_day(FRI, [cafe], ScheduleRules(day_start="18:45"), travel())
    assert times(schedule) == [] and schedule.stops[0].status == "does_not_fit"  # 18:45 + 90 = 20:15 > 20:00
    schedule = schedule_day(FRI, [cafe], ScheduleRules(day_start="17:00"), travel())
    assert times(schedule) == [("Mock Late Museum", "17:00", "18:30")] and codes(schedule) == ["hours_unconfirmed"]


def test_split_day_waits_for_the_evening_window():
    late = ScheduleRules(day_start="18:00", day_end="21:30")
    schedule = schedule_day(MON, stops(CISTERN), late, travel())  # 18:00 + 90 > 18:30: wait for 19:30
    assert times(schedule) == [("Basilica Cistern", "19:30", "21:00")] and schedule.scheduled[0].wait_minutes == 90


# ------------------------------------------------------------------ travel
def test_measured_travel_spaces_the_day_with_no_overlaps():
    schedule = day(MON, HAGIA, TOPKAPI, CISTERN, ALI_USTA)
    assert times(schedule) == [("Hagia Sophia", "09:00", "10:30"), ("Topkapı Palace", "10:45", "12:15"),
                               ("Basilica Cistern", "12:29", "13:59"), ("Ali Usta Çiğ Köfte", "14:12", "15:12")]
    assert [s.gap_minutes for s in schedule.scheduled] == [0, 15, 14, 13]
    assert schedule.scheduled[1].travel.citation["id"] == "osrm-foot-hagia-sophia-topkapi-palace"
    assert codes(schedule) == [] and schedule.complete and not schedule.uncertain


def test_route_not_cached_uses_the_labelled_buffer_and_stays_unknown():
    schedule = day(WED, TOPKAPI, DOLMABAHCE, max_walk_minutes=25)  # no cached walk to Dolmabahçe
    leg = schedule.scheduled[1].travel
    assert (leg.status, leg.minutes, leg.error_code) == ("unavailable", None, "not_cached")
    assert times(schedule)[1] == ("Dolmabahce Palace", "11:00", "12:30") and schedule.scheduled[1].buffer_used
    assert codes(schedule) == ["travel_unknown", "walk_unverified"]  # never "within the walking limit"
    fields = activity_fields(schedule.scheduled[1])
    assert fields["travel_minutes_from_previous"] is None
    assert fields["notes"] == "Travel time from the previous stop unknown: 30-minute planning buffer"


def test_buffer_is_configurable():
    schedule = day(WED, TOPKAPI, DOLMABAHCE, travel_buffer_minutes=45)
    assert times(schedule)[1] == ("Dolmabahce Palace", "11:15", "12:45")


def test_walk_over_the_limit_is_flagged_with_the_measured_minutes():
    schedule = day(TUE, CITADEL, CAVE, city="amman", max_walk_minutes=25)
    assert schedule.scheduled[1].travel.minutes == 131
    over = [i for i in schedule.issues if i.code == "walk_over_limit"]
    assert len(over) == 1 and "131 minutes" in over[0].message and "25-minute" in over[0].message
    assert day(TUE, CITADEL, CAVE, city="amman").issues == [i for i in schedule.issues if i.code != "walk_over_limit"]


def test_travel_lookups_are_cached_bounded_and_logged():
    asked = []
    t = TravelTimes(recorded_lookup("istanbul", asked), max_lookups=2)
    a, b, c = stops(HAGIA, TOPKAPI, CISTERN)
    assert t.leg(a, b).minutes == 15 and t.leg(a, b).minutes == 15
    assert asked == [("Hagia Sophia", "Topkapı Palace")]  # the second ask came from the cache
    assert t.leg(b, c).status == "measured"
    capped = t.leg(a, c)
    assert capped.status == "not_looked_up" and "limit (2)" in capped.note and len(asked) == 2
    assert [(c["tool"], c["ok"]) for c in t.calls] == [("travel_time", True), ("travel_time", True)]


def test_lookup_failures_are_unknown_never_invented():
    def broken(a, b):
        raise ConnectionError("travel API down")
    a, b = stops(HAGIA, TOPKAPI)
    assert TravelTimes(broken).leg(a, b).error_code == "lookup_error"
    assert TravelTimes(lambda x, y: {"ok": True, "duration_min": "15"}).leg(a, b).error_code == "bad_result"
    assert TravelTimes(None).leg(a, b).status == "not_looked_up"
    fault = {"ok": False, "error": {"code": "error_503", "message": "Injected fault", "status": 503}}
    leg = TravelTimes(lambda x, y: fault).leg(a, b)
    assert (leg.status, leg.minutes, leg.error_code) == ("unavailable", None, "error_503")


# ------------------------------------------------------------------ impossible days, duplicates, meals
def test_impossible_schedule_reports_what_does_not_fit():
    schedule = day(MON, HAGIA, TOPKAPI, CISTERN, day_end="12:00")
    assert times(schedule) == [("Hagia Sophia", "09:00", "10:30")]
    assert [s.status for s in schedule.stops] == ["scheduled", "does_not_fit", "does_not_fit"]
    assert "the planning day ends at 12:00" in schedule.stops[1].reason
    assert not schedule.complete and codes(schedule) == ["does_not_fit", "does_not_fit"]


def test_a_place_listed_twice_on_a_day_is_kept_once():
    schedule = day(MON, HAGIA, HAGIA)
    assert len(schedule.scheduled) == 1 and codes(schedule) == ["duplicate_place"] and not schedule.complete


def test_meal_windows_are_soft():
    schedule = day(MON, ALI_USTA)  # a restaurant at 09:00
    assert times(schedule) == [("Ali Usta Çiğ Köfte", "09:00", "10:00")]
    assert codes(schedule) == ["outside_meal_time"] and schedule.complete


def test_validate_day_catches_overlaps_and_closed_days():
    good = day(MON, HAGIA, TOPKAPI)
    overlapping = good.model_copy(update={"stops": [good.stops[0], good.stops[1].model_copy(
        update={"start": "10:00", "end": "11:30"})]})
    assert any("before Hagia Sophia ends" in e for e in validate_day(overlapping))
    tue = hours_on(TOPKAPI.hours, TUE)
    on_closed_day = good.model_copy(update={"stops": [good.stops[1].model_copy(update={"hours": tue, "travel": None})]})
    assert any("closed" in e for e in validate_day(on_closed_day))
    stretched = good.model_copy(update={"stops": [good.stops[0].model_copy(update={"end": "11:00"})]})
    assert any("is not 90 minutes" in e for e in validate_day(stretched))


# ------------------------------------------------------------------ rules and durations
def test_rules_come_from_the_trip_request_and_durations_are_configurable():
    trip = TripRequest(**{**raw()["trip"], "day_start": "10:00", "day_end": "18:00", "max_walk_minutes": 15})
    rules = ScheduleRules.from_trip(trip, durations={**DEFAULT_DURATIONS, "see": 60})
    assert (rules.day_start, rules.day_end, rules.max_walk_minutes) == ("10:00", "18:00", 15)
    assert times(schedule_day(MON, stops(HAGIA), rules, travel())) == [("Hagia Sophia", "10:00", "11:00")]
    assert ScheduleRules().duration_for(stops(ALI_USTA)[0]) == 60  # eat
    assert ScheduleRules().duration_for(Stop(place_id="x", name="X", category="restaurant")) == 60  # no kind
    with pytest.raises(ValidationError):
        ScheduleRules(day_start="21:00", day_end="09:00")
    with pytest.raises(ValidationError):
        ScheduleRules(durations={"see": 0})


def test_every_scheduled_duration_is_labelled_an_estimate():
    schedule = day(MON, HAGIA, TOPKAPI)
    assert all(s.duration_is_estimate for s in schedule.scheduled)
    assert all(activity_fields(s)["duration_is_estimate"] for s in schedule.scheduled)


# ------------------------------------------------------------------ the trip: dates and required places
def test_trip_dates_and_weekdays():
    trip = TripRequest(**raw()["trip"])  # 2026-11-02 .. 2026-11-04
    dates = trip_dates(trip)
    assert dates == [MON, TUE, WED]
    result = schedule_trip({TUE: stops(HAGIA)}, dates, ScheduleRules(), travel())
    assert [(d.date, d.weekday, len(d.scheduled)) for d in result.days] == [(MON, "Mon", 0), (TUE, "Tue", 1),
                                                                            (WED, "Wed", 0)]


def test_dates_outside_the_trip_are_refused():
    result = schedule_trip({THU: stops(HAGIA)}, [MON, TUE, WED], ScheduleRules(), travel())
    assert [i.code for i in result.issues] == ["date_outside_trip"] and result.scheduled_ids == []


def test_a_place_is_scheduled_on_one_day_only():
    result = schedule_trip({MON: stops(HAGIA), WED: stops(HAGIA, CISTERN)}, [MON, TUE, WED], ScheduleRules(), travel())
    assert result.scheduled_ids == [HAGIA.place_id, CISTERN.place_id]
    assert [i.code for i in result.issues] == ["duplicate_place"] and not result.complete


def test_required_place_that_cannot_be_scheduled_is_reported_with_its_reason():
    required = stops(TOPKAPI, BAZAAR, required=("Topkapı Palace", "Grand Bazaar"))
    result = schedule_trip({TUE: required[:1]}, [MON, TUE, WED], ScheduleRules(), travel(), required=required)
    reasons = result.unscheduled_required()
    assert set(reasons) == {TOPKAPI.place_id, BAZAAR.place_id}
    assert "closed on 2026-11-03 (Tue)" in reasons[TOPKAPI.place_id]
    assert "not assigned to any trip date" in reasons[BAZAAR.place_id]
    assert not result.complete


def test_availability_shows_where_a_place_may_go():
    assert availability(Stop.from_place(TOPKAPI), [MON, TUE, WED]) == {MON: "open", TUE: "closed", WED: "open"}
    assert availability(Stop.from_place(CITADEL), [MON]) == {MON: "unknown"}


# ------------------------------------------------------------------ into the itinerary: costs from the Budget Analyst
def test_zero_cost_and_priced_activities_keep_the_budget_analysts_costs_and_sources():
    cands = prepare_candidates(bundle())  # Topkapı is the traveller's required place
    decision = BudgetDecision(decisions=[
        PlaceDecision(place_id=i, decision="include" if i in (HAGIA.place_id, SULEYMANIYE.place_id) else "exclude",
                      rank=1 if i == HAGIA.place_id else (2 if i == SULEYMANIYE.place_id else None))
        for i in cands.open_ids])
    report = apply_decision(cands, decision)
    schedule = day(MON, TOPKAPI, HAGIA, SULEYMANIYE)
    activities = [report.to_activity(s.place_id, **activity_fields(s)) for s in schedule.scheduled]
    suleymaniye = activities[2]
    assert (suleymaniye.estimated_cost, suleymaniye.cost_status) == (0.0, "free")
    assert activities[1].travel_minutes_from_previous == schedule.scheduled[1].travel.minutes
    assert all(a.source_url.startswith("https://en.wikivoyage.org/") for a in activities)
    itinerary = Itinerary(destination="Istanbul", currency="USD", travellers=2, budget_total=float(report.budget_total),
                          days=[DayPlan(date=MON, activities=activities)])
    summary = summarize(itinerary)
    assert summary.known_total == report.known_total and summary.status == report.status == "within_budget"
    assert "Duration is a planning estimate" in itinerary.to_trip_store()["days"][0]["activities"][0]["notes"]


def test_activity_fields_refuse_an_unscheduled_stop():
    with pytest.raises(ValueError):
        activity_fields(day(TUE, TOPKAPI).stops[0])


def test_empty_day_is_complete_and_certain():
    schedule = schedule_day(MON, [], ScheduleRules(), travel())
    assert isinstance(schedule, DaySchedule) and schedule.complete and not schedule.uncertain
    assert to_minutes("24:00") == 1440
