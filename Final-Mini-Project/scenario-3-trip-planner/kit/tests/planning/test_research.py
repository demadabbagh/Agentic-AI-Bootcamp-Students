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


def test_trip_request_rules():
    t = TripRequest(**{**TRIP, "currency": " usd "})
    assert t.currency == "USD" and t.days == 3
    with pytest.raises(ValidationError, match="before start_date"):
        TripRequest(**{**TRIP, "end_date": "2026-11-01"})
    with pytest.raises(ValidationError):
        TripRequest(**{**TRIP, "travellers": 0})
