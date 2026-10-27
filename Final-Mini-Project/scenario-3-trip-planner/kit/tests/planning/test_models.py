"""Unit tests for planning/models.py.

Places, prices and hours are from the kit's replay cache (see the "Facts the sample cases rely on" table in
kit/README.md): Topkapı Palace 2750 TL (closed Tuesdays), Galata Tower 650 TL vs 1300 TL with the
price_conflict overlay, Rüstem Pasha Mosque free, Osman Gourmet Restaurant with no listed price.
The compatibility tests run our output through the Trip Store's own check_itinerary and trip_record.
"""
import json

import pytest
from pydantic import ValidationError

from planning.models import Activity, DayPlan, Itinerary
from services.travel_api import check_itinerary, trip_record

PENINSULA_SEE = "https://en.wikivoyage.org/wiki/Istanbul/Historical_Peninsula#See"
PENINSULA_EAT = "https://en.wikivoyage.org/wiki/Istanbul/Historical_Peninsula#Eat"
GALATA_SEE = "https://en.wikivoyage.org/wiki/Istanbul/Galata#See"
STORE_ACTIVITY_KEYS = {"name", "start_time", "end_time", "estimated_cost", "source_url", "notes"}
STORE_TOP_KEYS = {"destination", "currency", "travellers", "budget_total", "estimated_total", "days"}


def topkapi(**kw):
    return Activity(name="Topkapı Palace", start_time="09:30", end_time="12:00", estimated_cost=2750,
                    source_url=PENINSULA_SEE, place_id="wv-istanbul-topkapi-palace", kind="see", **kw)


def galata_conflict():
    return Activity(name="Galata Tower", start_time="16:00", end_time="17:00", estimated_cost=1300,
                    cost_min=650, cost_max=1300, source_url=GALATA_SEE, kind="see")


def mosque():
    return Activity(name="Rüstem Pasha Mosque", start_time="14:00", end_time="14:45", estimated_cost=0,
                    cost_status="free", source_url=PENINSULA_SEE, kind="see")


def osman_lunch():
    return Activity(name="Osman Gourmet Restaurant", start_time="12:30", end_time="13:30",
                    source_url=PENINSULA_EAT, kind="eat")


def trip(days=None, budget=10000, currency="TRY"):
    days = days or [
        DayPlan(date="2026-11-02", activities=[topkapi(), osman_lunch(), mosque()]),  # Monday
        DayPlan(date="2026-11-03", activities=[galata_conflict()]),                   # Tuesday
    ]
    return Itinerary(destination="Istanbul", currency=currency, travellers=1, budget_total=budget, days=days)


# ------------------------------------------------------------------ Activity: fields
def test_minimal_activity_has_unknown_cost():
    a = Activity(name="Osman Gourmet Restaurant")
    assert a.estimated_cost is None and a.cost_status == "unknown"


def test_name_is_stripped_and_required():
    assert Activity(name="  Galata Tower ").name == "Galata Tower"
    with pytest.raises(ValidationError):
        Activity(name="   ")


@pytest.mark.parametrize("bad", ["9:00", "24:00", "12:60", "12:5", "noon", "09:00:00"])
def test_time_must_be_hh_mm_on_the_clock(bad):
    with pytest.raises(ValidationError):
        Activity(name="Galata Tower", start_time=bad, end_time="23:00")


def test_end_must_follow_start():
    with pytest.raises(ValidationError, match="after start_time"):
        Activity(name="Galata Tower", start_time="17:00", end_time="16:00")
    with pytest.raises(ValidationError, match="after start_time"):
        Activity(name="Galata Tower", start_time="17:00", end_time="17:00")


def test_times_come_in_pairs():
    with pytest.raises(ValidationError, match="both start_time and end_time"):
        Activity(name="Galata Tower", start_time="16:00")


def test_duration_minutes():
    assert topkapi().duration_minutes == 150
    assert Activity(name="Galata Tower").duration_minutes is None


@pytest.mark.parametrize("bad", [-1, True, "650", float("nan"), float("inf")])
def test_cost_must_be_a_real_non_negative_number(bad):
    with pytest.raises(ValidationError):
        Activity(name="Galata Tower", estimated_cost=bad)


def test_source_url_must_be_a_web_link():
    # the prompt_injection overlay's listing carries this kind of non-web URL
    with pytest.raises(ValidationError, match="http"):
        Activity(name="Some listing", source_url="about:synthetic-overlay/prompt_injection")


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Activity(name="Galata Tower", cost=650)  # a typo for estimated_cost must not pass silently


# ------------------------------------------------------------------ Activity: cost status
def test_listed_price_is_an_estimate():
    assert topkapi().cost_status == "estimated"


def test_zero_is_never_assumed_free():
    with pytest.raises(ValidationError, match="free"):
        Activity(name="Rüstem Pasha Mosque", estimated_cost=0)
    assert mosque().cost_status == "free"


def test_free_must_cost_zero():
    with pytest.raises(ValidationError):
        Activity(name="Rüstem Pasha Mosque", estimated_cost=50, cost_status="free")


def test_unknown_must_have_no_cost():
    with pytest.raises(ValidationError):
        Activity(name="Osman Gourmet Restaurant", estimated_cost=300, cost_status="unknown")


def test_conflicting_prices_keep_the_range():
    a = galata_conflict()
    assert (a.cost_status, a.cost_min, a.cost_max, a.estimated_cost) == ("range", 650, 1300, 1300)


@pytest.mark.parametrize("kw", [
    dict(estimated_cost=1500, cost_min=650, cost_max=1300),  # outside the range
    dict(estimated_cost=650, cost_min=1300, cost_max=650),   # min above max
    dict(estimated_cost=650, cost_min=650, cost_max=650),    # equal: not a range
    dict(estimated_cost=650, cost_min=650),                  # half a range
    dict(cost_min=650, cost_max=1300),                       # a range without the budgeted value
    dict(estimated_cost=650, cost_status="estimated", cost_min=650, cost_max=1300),
])
def test_inconsistent_ranges_are_rejected(kw):
    with pytest.raises(ValidationError):
        Activity(name="Galata Tower", **kw)


# ------------------------------------------------------------------ DayPlan
def test_day_parses_iso_date_and_weekday():
    d = DayPlan(date="2026-11-03", activities=[galata_conflict()])
    assert d.date.isoformat() == "2026-11-03" and d.weekday == "Tue"


@pytest.mark.parametrize("bad", ["2026-02-30", "20261102", "03/11/2026", 1793664000, "2026-11-03T09:00"])
def test_day_rejects_bad_dates(bad):
    with pytest.raises(ValidationError):
        DayPlan(date=bad)


def test_day_costs():
    d = DayPlan(date="2026-11-02", activities=[topkapi(), osman_lunch(), mosque()])
    assert d.known_cost == 2750
    assert [a.name for a in d.unknown_cost_activities] == ["Osman Gourmet Restaurant"]


# ------------------------------------------------------------------ Itinerary: fields
def test_currency_is_normalised():
    assert trip(currency=" try ").currency == "TRY"
    with pytest.raises(ValidationError):
        trip(currency="TL")


@pytest.mark.parametrize("bad", [0, -2, True, 2.5])
def test_travellers_must_be_a_positive_whole_number(bad):
    with pytest.raises(ValidationError):
        Itinerary(destination="Istanbul", currency="TRY", travellers=bad,
                  days=[DayPlan(date="2026-11-02", activities=[topkapi()])])


def test_needs_at_least_one_day():
    with pytest.raises(ValidationError):
        Itinerary(destination="Istanbul", currency="TRY", days=[])


def test_days_must_be_in_order_without_repeats():
    with pytest.raises(ValidationError, match="date order"):
        trip(days=[DayPlan(date="2026-11-03", activities=[galata_conflict()]),
                   DayPlan(date="2026-11-02", activities=[topkapi()])])
    with pytest.raises(ValidationError, match="date order"):
        trip(days=[DayPlan(date="2026-11-02", activities=[topkapi()]),
                   DayPlan(date="2026-11-02", activities=[galata_conflict()])])


def test_needs_at_least_one_activity():
    with pytest.raises(ValidationError, match="no activities"):
        trip(days=[DayPlan(date="2026-11-02"), DayPlan(date="2026-11-03")])


def test_an_empty_day_inside_a_trip_is_allowed():
    it = trip(days=[DayPlan(date="2026-11-02", activities=[topkapi()]), DayPlan(date="2026-11-03")])
    assert len(it.days) == 2


# ------------------------------------------------------------------ Itinerary: totals and budget
def test_total_is_computed_from_known_costs():
    it = trip()
    assert it.estimated_total == 2750 + 0 + 1300  # Osman's unknown cost is not counted as 0 "free"
    assert not it.cost_complete
    assert [a.name for _, a in it.unknown_cost_activities] == ["Osman Gourmet Restaurant"]


def test_within_budget_is_honest():
    complete = [DayPlan(date="2026-11-04", activities=[topkapi(), mosque()])]  # Wednesday, all costs known
    assert trip(days=complete, budget=3000).within_budget is True
    assert trip(days=complete, budget=2000).within_budget is False
    assert trip(budget=10000).within_budget is None  # fits so far, but one cost is unknown: cannot confirm
    assert trip(budget=1000).within_budget is False  # already over, whatever the unknown cost is
    assert trip(budget=None).within_budget is None


def test_estimated_total_cannot_be_typed_in():
    with pytest.raises(ValidationError):
        Itinerary(destination="Istanbul", currency="TRY", estimated_total=1,
                  days=[DayPlan(date="2026-11-02", activities=[topkapi()])])


# ------------------------------------------------------------------ to_trip_store and the real Trip Store
def test_store_payload_passes_check_itinerary():
    assert check_itinerary(trip().to_trip_store()) == []


def test_store_payload_uses_only_documented_keys():
    payload = trip().to_trip_store()
    assert set(payload) <= STORE_TOP_KEYS
    for day in payload["days"]:
        assert set(day) == {"date", "activities"}
        for a in day["activities"]:
            assert set(a) <= STORE_ACTIVITY_KEYS


def test_store_payload_keeps_unknown_cost_as_null_and_labels_it():
    acts = trip().to_trip_store()["days"][0]["activities"]
    lunch = next(a for a in acts if a["name"] == "Osman Gourmet Restaurant")
    assert lunch["estimated_cost"] is None
    assert "Cost unknown" in lunch["notes"]


def test_store_payload_labels_ranges_free_and_estimates():
    days = trip().to_trip_store()["days"]
    by_name = {a["name"]: a for d in days for a in d["activities"]}
    assert "650-1300 TRY" in by_name["Galata Tower"]["notes"]
    assert by_name["Rüstem Pasha Mosque"]["notes"].startswith("Listed as free")
    assert "Estimated cost" in by_name["Topkapı Palace"]["notes"]
    assert "planning estimate" in by_name["Topkapı Palace"]["notes"]


def test_trip_record_reads_our_payload_the_way_the_tests_do():
    body = {"traveller_id": "TRV-200", "title": "Istanbul, 2 days", "itinerary": trip().to_trip_store()}
    rec = trip_record("TRIP-3001", body, "act-test")
    assert (rec["start_date"], rec["end_date"]) == ("2026-11-02", "2026-11-03")
    assert "topkap" in rec["places"].lower()
    assert "topkap" not in rec["by_weekday"].get("Tue", "").lower()  # S3-05 style check
    assert rec["computed_total"] == rec["estimated_total"] == 4050


def test_store_budget_check_agrees_with_ours_when_costs_are_complete():
    for budget in (3000, 2000):
        it = trip(days=[DayPlan(date="2026-11-04", activities=[topkapi(), mosque()])], budget=budget)
        rec = trip_record("TRIP-3001", {"traveller_id": "TRV-200", "title": "t", "itinerary": it.to_trip_store()}, None)
        assert rec["budget_total"] == budget  # S3-04 compares budget_total for equality
        assert rec["within_budget"] is it.within_budget


def test_example_from_the_kit_readme():
    # kit/README.md: Hagia Sophia, Adult EUR 25 converted to 28 USD
    it = Itinerary(destination="Istanbul", currency="USD", budget_total=700, days=[DayPlan(
        date="2026-11-02", activities=[Activity(
            name="Hagia Sophia", start_time="09:00", end_time="10:30", estimated_cost=28,
            source_url=PENINSULA_SEE, notes="Adult EUR 25 (Wikivoyage), converted with the ECB rate: estimate")])])
    payload = it.to_trip_store()
    assert check_itinerary(payload) == []
    assert payload["estimated_total"] == 28 and payload["budget_total"] == 700
    assert payload["days"][0]["activities"][0]["notes"].startswith("Adult EUR 25")


def test_payload_is_plain_json():
    payload = trip().to_trip_store()
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_to_trip_store_is_deterministic():
    # the same plan must give the same body, or the Idempotency-Key replay would answer 409
    assert trip().to_trip_store() == trip().to_trip_store()


# ------------------------------------------------------------------ session state round trip
def test_model_round_trips_through_json():
    it = trip()
    again = Itinerary.model_validate_json(it.model_dump_json())
    assert again == it
    assert Itinerary.model_validate(it.model_dump()) == it
