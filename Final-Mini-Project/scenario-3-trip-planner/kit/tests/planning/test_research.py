"""Unit tests for planning/research.py: the provisional ResearchBundle adapter. No live service, no LLM."""
import pytest
from pydantic import ValidationError

from planning.research import SCHEMA_VERSION, ResearchBundle, TripRequest
from research_fixtures import bundle, listing, raw

TRIP = raw()["trip"]
FAILED_503 = {"ok": False, "error": {"code": "error_503", "message": "Injected fault", "status": 503}}


def test_fixture_becomes_a_bundle_of_places_rates_and_no_failures():
    b = bundle()
    assert b.schema_version == SCHEMA_VERSION == "provisional-1"
    assert len(b.places) == 12 and len(b.fx_results) == 2 and b.failures == []
    assert {p.category for p in b.places} == {"attraction", "restaurant"}
    topkapi = next(p for p in b.places if p.place_id == "wv-istanbul-topkapi-palace")
    assert topkapi.price["raw"].startswith("Adult 2750 TL") and topkapi.source_url.startswith("https://en.wikivoyage.org/")
    assert topkapi.citation["id"] == "wv-istanbul-topkapi-palace"


def test_conflicting_prices_and_synthetic_flags_survive_the_adapter():
    places = {p.place_id: p for p in bundle().places}
    galata = places["wv-istanbul-galata-tower"]
    assert galata.price_conflict and len(galata.price_sources) == 2
    assert places["synthetic-istanbul-rihla-test-kitchen"].synthetic


def test_failed_attractions_call_adds_a_failure_and_no_places():
    b = ResearchBundle.from_tool_results(TRIP, attractions=[FAILED_503], restaurants=raw()["tool_results"]["find_restaurants"])
    assert [(f.tool, f.code) for f in b.failures] == [("find_attractions", "error_503")]
    assert {p.category for p in b.places} == {"restaurant"}


def test_rest_style_error_without_ok_is_a_failure_too():
    b = ResearchBundle.from_tool_results(TRIP, fx=[{"error": {"code": "unknown_currency", "message": "XYZ"}}])
    assert [(f.tool, f.code) for f in b.failures] == [("convert_currency", "unknown_currency")]
    assert b.fx_results == []


def test_the_same_listing_twice_is_kept_once():
    item = listing("istanbul", "wv-istanbul-hagia-sophia")
    b = ResearchBundle.from_tool_results(TRIP, attractions=[{"ok": True, "attractions": [item]},
                                                            {"ok": True, "attractions": [item]}])
    assert [p.place_id for p in b.places] == ["wv-istanbul-hagia-sophia"]


def test_scheduling_data_and_sources_are_preserved_from_the_listing():
    places = {p.place_id: p for p in bundle().places}
    topkapi = places["wv-istanbul-topkapi-palace"]
    assert topkapi.hours["raw"] == "Wed-Mon 09:00-17:00" and topkapi.hours["closed_days"] == ["Tue"]
    assert topkapi.lat == pytest.approx(41.0115, abs=0.01) and topkapi.lon == pytest.approx(28.9834, abs=0.01)
    assert topkapi.district and topkapi.source_url and topkapi.citation["id"] == topkapi.place_id
    assert bundle("amman").places[0].hours is None  # Amman Citadel: the listing gives no hours


def test_bundles_without_the_new_fields_still_load():
    old_place = {"place_id": "x", "name": "Old Place", "category": "attraction"}
    old = ResearchBundle.model_validate({"trip": {k: v for k, v in TRIP.items()}, "places": [old_place]})
    p = old.places[0]
    assert (p.hours, p.lat, p.lon) == (None, None, None)
    assert (old.trip.day_start, old.trip.day_end, old.trip.max_walk_minutes) == ("09:00", "21:00", None)


def test_scheduling_preferences_are_validated():
    t = TripRequest(**{**TRIP, "max_walk_minutes": 25, "day_start": "08:30", "day_end": "20:00"})
    assert (t.max_walk_minutes, t.day_start, t.day_end) == (25, "08:30", "20:00")
    for bad in ({"day_start": "21:00", "day_end": "09:00"}, {"day_end": "24:00"}, {"max_walk_minutes": 0},
                {"max_walk_minutes": "25"}):
        with pytest.raises(ValidationError):
            TripRequest(**{**TRIP, **bad})
    with pytest.raises(ValidationError):  # coordinates out of range are refused
        ResearchBundle.model_validate({"trip": TRIP, "places": [{"place_id": "x", "name": "X", "category": "attraction",
                                                                  "lat": 123.0}]})


def test_trip_request_rules():
    t = TripRequest(**{**TRIP, "currency": " usd "})
    assert t.currency == "USD" and t.days == 3
    with pytest.raises(ValidationError, match="before start_date"):
        TripRequest(**{**TRIP, "end_date": "2026-11-01"})
    with pytest.raises(ValidationError):
        TripRequest(**{**TRIP, "travellers": 0})
