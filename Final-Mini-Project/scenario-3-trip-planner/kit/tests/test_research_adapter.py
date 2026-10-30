"""Interest normalization between the traveller's wording and the Travel API tags."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.research_adapter import (
    INTEREST_LABELS,
    INTEREST_WORDS,
    TRAVEL_API_TAGS,
    build_research_request,
    normalize_interests,
    run_research_pipeline,
)
from services.travel_data import TAG_RULES

AMMAN_TRIP = {
    "destination": "Amman",
    "currency": "USD",
    "start_date": "2026-11-02",
    "end_date": "2026-11-09",
    "budget": 2000,
    "travellers": 2,
    "interests": ["History & Culture", "Art & Museums", "Shopping"],
}


# ------------------------------------------------------------------ the tables
def test_every_tag_is_one_the_travel_api_matches():
    assert TRAVEL_API_TAGS == set(TAG_RULES)
    produced = {t for tags in INTEREST_LABELS.values() for t in tags} | set(INTEREST_WORDS.values())
    assert produced <= TRAVEL_API_TAGS


# Every chip in rihla-frontend/src/App.jsx.
@pytest.mark.parametrize("label, tags", [
    ("History & Culture", ["history"]),
    ("Nature & Outdoors", ["nature"]),
    ("Food & Cuisine", ["food"]),
    ("Shopping", ["markets"]),
    ("Art & Museums", ["art", "museum"]),
    ("Adventure", ["adventure"]),
    ("Relaxation", ["relaxation"]),
    ("Entertainment", ["entertainment"]),
])
def test_supported_frontend_labels(label, tags):
    """All eight chips have a tag the Travel API matches; none is dropped as unsupported."""
    assert normalize_interests([label]).tags == tags
    assert normalize_interests([label]).unsupported == []


def test_unknown_labels_are_reported_not_guessed():
    match = normalize_interests(["Space Tourism"])
    assert match.tags == []
    assert match.unsupported == ["space tourism"]


@pytest.mark.parametrize("label", [
    "history & culture", "HISTORY & CULTURE", "History&Culture",
    "  History   &   Culture ", "History and Culture", "History + Culture",
])
def test_equivalent_spellings_of_a_label(label):
    assert normalize_interests([label]).tags == ["history"]


# ------------------------------------------------------------------ chat wording
@pytest.mark.parametrize("interests, tags", [
    (["history", "museums"], ["history", "museum"]),
    (["Art/Museums"], ["art", "museum"]),
    (["history and art, markets"], ["history", "art", "markets"]),
    ("historical sites; bazaars", ["history", "markets"]),
    (["Bazaars", "SHOPPING", "market"], ["markets"]),
])
def test_chat_words(interests, tags):
    assert normalize_interests(interests).tags == tags


def test_unknown_words_are_reported_alongside_known_ones():
    match = normalize_interests(["culture", "history", "nightlife"])
    assert match.tags == ["history", "entertainment"]
    assert match.unsupported == ["culture"]


def test_duplicates_are_removed_in_order():
    match = normalize_interests(["Art & Museums", "museums", "art", "History & Culture"])
    assert match.tags == ["art", "museum", "history"]


@pytest.mark.parametrize("interests", [None, [], [""], ["   "], "", [None]])
def test_empty_interests(interests):
    match = normalize_interests(interests)
    assert match.tags == []
    assert match.unsupported == []


# ------------------------------------------------------------------ the research request
def test_request_uses_tags_and_keeps_the_travellers_wording():
    trip_info = deepcopy(AMMAN_TRIP)
    request = build_research_request(trip_info)

    assert request.interests == ["history", "art", "museum", "markets"]
    assert trip_info == AMMAN_TRIP  # the session's copy keeps the original labels


def test_request_with_only_unsupported_interests_has_no_filter():
    request = build_research_request({**AMMAN_TRIP, "interests": ["Space Tourism"]})
    assert request.interests == []


def test_request_without_interests():
    request = build_research_request({k: v for k, v in AMMAN_TRIP.items() if k != "interests"})
    assert request.interests == []


# ------------------------------------------------------------------ Amman, offline end to end
ENDPOINTS = {
    "find_attractions": ("/api/attractions", {"city": "city", "interests": "interest", "limit": "limit"}),
    "find_restaurants": ("/api/restaurants", {"city": "city", "budget": "budget", "limit": "limit"}),
    "get_opening_hours": ("/api/hours", {"place_name": "name", "city": "city"}),
    "convert_currency": ("/api/fx", {"amount": "amount", "from_currency": "from", "to_currency": "to"}),
}


@pytest.fixture
def travel_api_caller(monkeypatch):
    """The Researcher's tool calls, answered by the real Travel API in-process from the replay cache."""
    monkeypatch.setenv("TRAVEL_MODE", "replay")
    monkeypatch.setenv("TRAVEL_API_KEY", "test-key")
    monkeypatch.delenv("TRAVEL_OVERLAYS", raising=False)
    from services.travel_api import app

    client = TestClient(app)
    calls = []

    def caller(name, args):
        path, params = ENDPOINTS[name]
        query = {params[k]: v for k, v in args.items() if v is not None}
        calls.append((name, query))
        response = client.get(path, params=query, headers={"X-API-Key": "test-key"})
        data = response.json()
        if response.status_code >= 400:
            return {"ok": False, "error": data.get("error") or {"code": "http_error"}}
        return {"ok": True, **data}

    caller.calls = calls
    return caller


def test_amman_frontend_labels_reach_the_real_attractions(travel_api_caller):
    bundle = run_research_pipeline(AMMAN_TRIP, caller=travel_api_caller)

    attractions = [p.name for p in bundle.places if p.category == "attraction"]
    restaurants = [p.name for p in bundle.places if p.category == "restaurant"]

    first = travel_api_caller.calls[0]
    assert first == ("find_attractions", {"city": "Amman", "interest": "history,art,museum,markets", "limit": 30})  # 8 days
    assert {"Amman Citadel", "Roman Theater", "The Jordan Museum", "Darat al Funun"} <= set(attractions)
    # Rainbow Street used to count as shopping only because "street" was a market keyword; its listing describes
    # a walking area with cafes. The recorded Amman page has no shopping listing, so shopping is not served.
    assert "Rainbow Street" not in attractions
    assert len(attractions) == 13  # was 1 (Rainbow Street) with the raw labels
    assert len(restaurants) == 10
    assert bundle.failures == []
