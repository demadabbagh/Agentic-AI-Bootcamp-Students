"""Interest-based retrieval in the Travel API, and the structured trip form. No network, no LLM.

The Paris live run asked for history, shopping and art and got only museums: the API ranked listings by how many
interests they matched, so a history-and-art museum outranked every shop, and "street"/"avenue" made almost any
listing a "market". Shopping destinations (department stores, covered passages) were never parsed at all.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory import sessions
from app.orchestrator import _form_details
from researcher.researcher import attraction_limit
from services import travel_data as td

client = TestClient(app)


def item(name, tags, district=None, lat=1.0, description=""):
    return {"id": f"wv-x-{td.slug(name)}", "name": name, "kind": "see", "tags": tags, "district": district,
            "lat": lat, "description": description}


# ------------------------------------------------------------------ balanced retrieval
def test_each_interest_takes_turns_so_multi_match_museums_cannot_fill_every_slot():
    museums = [item(f"Museum {n}", ["history", "art", "museum"]) for n in range(6)]
    shops = [item(f"Department Store {n}", ["markets"]) for n in range(3)]
    ranked = td.distribute(museums + shops, ["history", "markets", "art", "museum"])
    first_four = [i["name"] for i in ranked[:4]]
    assert sum(n.startswith("Department Store") for n in first_four) == 1  # shopping gets its turn
    assert [i["name"] for i in ranked[:8]].count("Department Store 0") == 1  # no place twice
    assert len(ranked) == 9


def test_one_interest_keeps_the_relevance_order():
    items = [item("A", ["history"]), item("B", ["history", "art"])]
    assert [i["name"] for i in td.distribute(items, ["history"])] == ["A", "B"]  # one tag: score ties keep order


def test_a_tagged_interest_never_matches_a_word_inside_another_word():
    assert td.interest_score(item("Start Point Party", []), ["art"]) == 0


# ------------------------------------------------------------------ what the tags mean
@pytest.mark.parametrize("text, kind, has, lacks", [
    ("Rainbow Street. An area to walk around, with cafes and bars", "see", [], ["markets"]),
    ("Avenue des Champs-Elysees. A wide avenue", "see", [], ["markets"]),
    ("Galeries Lafayette. A department store", "buy", ["markets"], []),
    ("Roman Theater. An ancient roman theatre", "see", ["history"], ["entertainment"]),
    ("Theatre du Chatelet. Musicals and concerts", "do", ["entertainment"], []),
    ("Moulin Rouge. Cabaret shows nightly", "do", ["entertainment"], []),
    ("Cagaloglu Hamami. A historic turkish bath", "do", ["relaxation", "history"], []),
    ("Luxembourg Gardens. A peaceful park", "see", ["nature", "relaxation"], []),
    ("Mount Takao. Hiking trails", "do", ["adventure"], []),
    ("Spice Bazaar. Spices and sweets", "buy", ["markets", "food"], []),
])
def test_tags_follow_what_the_place_is(text, kind, has, lacks):
    tags = td.tags_for(text, kind)
    assert set(has) <= set(tags), tags
    assert not set(lacks) & set(tags), tags


def test_shopping_destinations_are_listed_and_single_shops_are_not():
    page = ("== Buy ==\n"
            "* {{buy | name=Galeries Lafayette | content=Grand department store.}}\n"
            "* {{buy | name=Passage des Panoramas | content=A covered passage.}}\n"
            "* {{buy | name=Printemps | content=Another famous department store.}}\n"
            "* {{buy | name=Le Bon Marché | content=Luxury.}}\n"
            "* {{buy | name=Chez Marie | content=A small hat shop.}}\n")
    names = [l["name"] for l in td.parse_listings("Paris", page, "paris")]
    assert names == ["Galeries Lafayette", "Passage des Panoramas", "Printemps", "Le Bon Marché"]


def test_recorded_pages_are_tagged_by_the_current_rules():
    listings, _ = td.city_listings("Amman")
    rainbow = next(l for l in listings if l["name"] == "Rainbow Street")
    assert "markets" not in rainbow["tags"]  # the recorded tags said "markets" because of the word "street"


# ------------------------------------------------------------------ research size
def test_a_longer_trip_researches_more_places():
    assert attraction_limit(3) == 15 and attraction_limit(6) == 24 and attraction_limit(14) == 30


# ------------------------------------------------------------------ the structured trip form
FORM = {"destination": "Paris", "start_date": "2026-11-02", "end_date": "2026-11-07", "travellers": 2,
        "budget": 2000, "currency": "USD", "interests": ["History & Culture", "Shopping", "Art & Museums"]}


def setup_function():
    sessions.clear()


def test_the_form_is_used_exactly_and_no_coordinator_llm_call_is_made_without_free_text():
    with patch("app.orchestrator.understand_trip_request", side_effect=AssertionError("no LLM call")), \
            patch("app.orchestrator.run_research_pipeline", side_effect=RuntimeError("stop here")) as research:
        response = client.post("/api/chat", json={"session_id": "s1", "message": "Plan a trip", "trip": FORM})
    assert response.status_code == 200 and response.json()["status"] == "research_unavailable"
    trip_info = research.call_args.args[0]
    assert trip_info["interests"] == FORM["interests"]  # the exact selection, not an LLM paraphrase
    assert (trip_info["start_date"], trip_info["end_date"], trip_info["duration_days"]) == (
        "2026-11-02", "2026-11-07", 6)


def test_free_text_preferences_still_go_to_the_coordinator_but_the_form_fields_win():
    parsed = {"intent": "create_trip", "destination": "Pariss", "interests": ["history", "photography"],
              "budget_style": "mid-range", "start_date": "2026-11-03", "end_date": "2026-11-04"}
    with patch("app.orchestrator.understand_trip_request", return_value=parsed) as coordinator, \
            patch("app.orchestrator.run_research_pipeline", side_effect=RuntimeError("stop here")) as research:
        client.post("/api/chat", json={"session_id": "s2", "message": "Plan a trip",
                                       "trip": {**FORM, "preferences": "quiet photography spots"}})
    assert coordinator.called
    trip_info = research.call_args.args[0]
    assert trip_info["destination"] == "Paris" and trip_info["start_date"] == "2026-11-02"
    assert trip_info["interests"] == FORM["interests"] + ["history", "photography"]
    assert trip_info["budget_style"] == "mid-range"  # what only the free text says is kept


@pytest.mark.parametrize("change, message", [
    ({"end_date": "2026-11-01"}, "before the departure date"),
    ({"end_date": "2026-12-30"}, "up to 21 days"),
    ({"travellers": 0}, "greater than or equal to 1"),
])
def test_invalid_form_values_are_refused_before_any_work(change, message):
    with patch("app.orchestrator.orchestrate_trip") as orchestrate:
        response = client.post("/api/chat", json={"session_id": "s3", "message": "x", "trip": {**FORM, **change}})
    assert response.status_code == 422 and message in response.text
    orchestrate.assert_not_called()


def test_form_details():
    assert _form_details(FORM)["duration_days"] == 6 and _form_details(FORM)["currency"] == "USD"


def test_a_city_page_summary_of_a_district_listing_is_kept_once():
    district = {"name": "Musée du Louvre", "kind": "see", "district": "1st", "lat": 48.861089, "lon": 2.33575}
    nearby_shop = {"name": "Le Carrousel du Louvre", "kind": "buy", "district": "1st", "lat": 48.86252,
                   "lon": 2.33516}
    assert td._city_copy_of({"name": "The Louvre", "kind": "see", "district": None, "lat": 48.861089,
                             "lon": 2.33575}, [district, nearby_shop])
    assert not td._city_copy_of({"name": "Le Carrousel du Louvre", "kind": "buy", "district": None,
                                 "lat": 48.8640, "lon": 2.3352}, [district])  # 150 m away: another place
    assert not td._city_copy_of({"name": "Musée d'Orsay", "kind": "see", "district": None, "lat": 48.861089,
                                 "lon": 2.33575}, [district])  # only generic words shared


def test_shopping_prefers_shops_over_museums_that_mention_a_market():
    museum = item("Museum near the market", ["museum", "markets", "art"])
    shop = dict(item("Passage Choiseul", ["markets"]), kind="buy")
    ranked = td.distribute([museum, shop, item("Old Fort", ["history"])], ["history", "markets"])
    assert [i["name"] for i in ranked][:2] == ["Old Fort", "Passage Choiseul"]


def test_a_museum_whose_text_mentions_a_market_is_not_shopping():
    assert "markets" not in td.tags_for("Musee Cognacq-Jay. Near the old market of the Marais", "see",
                                        "Musee Cognacq-Jay")
    assert "markets" in td.tags_for("Grand Bazaar. A covered market", "see", "Grand Bazaar")
    assert "markets" in td.tags_for("Forum des Halles. A shopping mall", "see", "Forum des Halles")
