"""Worldwide listings from Google Places API (New), offline: Google is mocked at the HTTP layer and the city centre
is patched, so no request leaves the machine. The Paris answers below are HAND-WRITTEN TEST DATA in the Places API
response format, not real Google results."""
import datetime as dt
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from planning.budget_analyst import prepare_candidates
from planning.schedule import hours_on
from services import google_places as gp
from services import travel_data as td

PARIS = {"name": "Paris", "lat": 48.8566, "lon": 2.3522}
MON, TUE = dt.date(2026, 11, 2), dt.date(2026, 11, 3)


def period(day, open_hm, close_hm, close_day=None):
    (oh, om), (ch, cm) = open_hm, close_hm
    return {"open": {"day": day, "hour": oh, "minute": om},
            "close": {"day": day if close_day is None else close_day, "hour": ch, "minute": cm}}


def place(pid, name, types, lat=48.86, lon=2.34, **extra):
    return {"id": pid, "displayName": {"text": name, "languageCode": "en"}, "types": types,
            "location": {"latitude": lat, "longitude": lon}, "formattedAddress": f"{name}, 75001 Paris, France",
            "googleMapsUri": f"https://maps.google.com/?cid={pid}", "businessStatus": "OPERATIONAL", **extra}


# Google days: 0 = Sunday. The Louvre-like test place is closed on Tuesday (2).
LOUVRE = place("p-louvre", "Louvre Museum", ["museum", "tourist_attraction"],
               regularOpeningHours={"periods": [period(d, (9, 0), (18, 0)) for d in (0, 1, 3, 4, 5, 6)],
                                    "weekdayDescriptions": ["Monday: 9:00 AM - 6:00 PM", "Tuesday: Closed"]})
EIFFEL = place("p-eiffel", "Eiffel Tower", ["tourist_attraction", "observation_deck"], lat=48.858, lon=2.2945,
               regularOpeningHours={"periods": [period(d, (9, 30), (23, 45)) for d in range(7)]})
NOTRE_DAME = place("p-notre-dame", "Notre-Dame Cathedral", ["church", "place_of_worship", "tourist_attraction"])
CHAPELLE = place("p-chapelle", "Sainte-Chapelle", ["church", "historical_landmark"],
                 businessStatus="CLOSED_TEMPORARILY")
HOTEL = place("p-hotel", "Hotel du Louvre", ["lodging", "hotel"])
GONE = place("p-gone", "Old Panorama", ["tourist_attraction"], businessStatus="CLOSED_PERMANENTLY")
FAR = place("p-far", "Disneyland Paris", ["amusement_park", "tourist_attraction"], lat=48.8722, lon=2.7758)  # ~31 km
ORSAY = place("p-orsay", "Musee d'Orsay", ["museum", "art_gallery"])
BISTRO = place("p-bistro", "Le Bistro Test", ["restaurant", "french_restaurant"], priceLevel="PRICE_LEVEL_MODERATE",
               priceRange={"startPrice": {"currencyCode": "EUR", "units": "20"},
                           "endPrice": {"currencyCode": "EUR", "units": "30"}},
               regularOpeningHours={"periods": [period(d, (19, 0), (1, 0), (d + 1) % 7) for d in range(1, 7)]})
OPEN_ENDED = place("p-luxe", "Grand Test Restaurant", ["restaurant", "fine_dining_restaurant"],
                   priceRange={"startPrice": {"currencyCode": "EUR", "units": "100"}})
CAFE = place("p-cafe", "Cafe Test", ["cafe", "coffee_shop"], priceLevel="PRICE_LEVEL_INEXPENSIVE",
             regularOpeningHours={"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]})

ANSWERS = {"top tourist attractions in Paris": [LOUVRE, EIFFEL, NOTRE_DAME, HOTEL, GONE, FAR, BISTRO],
           "museums and historic sites in Paris": [LOUVRE, ORSAY, CHAPELLE],
           "restaurants in Paris": [BISTRO, OPEN_ENDED, CAFE]}


class FakeGoogle:
    """A Places Text Search endpoint: records each request, answers from ANSWERS or with a given error."""

    def __init__(self, status=200, body=None, raise_exc=None):
        self.requests, self.status, self.body, self.raise_exc = [], status, body, raise_exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc:
            raise self.raise_exc
        if self.status != 200:
            return httpx.Response(self.status, json=self.body or {"error": {"message": "test error"}})
        query = json.loads(request.content)["textQuery"]
        return httpx.Response(200, json={"places": ANSWERS.get(query, [])})


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE", "live")
    monkeypatch.setenv("TRAVEL_PLACES_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-places-key")
    monkeypatch.delenv("TRAVEL_GOOGLE_MAX_REQUESTS", raising=False)
    monkeypatch.delenv("TRAVEL_GOOGLE_CACHE_TTL_S", raising=False)
    monkeypatch.delenv("TRAVEL_OVERLAYS", raising=False)
    monkeypatch.setattr(td, "city_point", lambda city: dict(PARIS, name=td.city_title(city)))
    fake = FakeGoogle()
    monkeypatch.setattr(gp, "_http", httpx.Client(transport=httpx.MockTransport(fake)))
    gp.reset()
    yield fake
    gp.reset()


def use(monkeypatch, fake):
    monkeypatch.setattr(gp, "_http", httpx.Client(transport=httpx.MockTransport(fake)))


# ------------------------------------------------------------------ hours and prices: facts only
def test_hours_become_the_kits_structure_and_the_scheduler_reads_them():
    hours = gp.to_hours(LOUVRE)
    assert hours["parsed"] and hours["days_stated"]
    assert hours["closed_days"] == ["Tue"]
    assert hours["weekly"]["Mon"] == ["09:00-18:00"]
    assert hours_on(hours, TUE).status == "closed"
    monday = hours_on(hours, MON)
    assert monday.status == "open" and monday.windows == [(540, 1080)]


def test_overnight_24h_temporarily_closed_and_missing_hours():
    assert gp.to_hours(BISTRO)["weekly"]["Mon"] == ["19:00-01:00"]
    assert "past midnight" in hours_on(gp.to_hours(BISTRO), MON).note
    assert gp.to_hours(CAFE)["open_24h"] is True
    assert hours_on(gp.to_hours(CHAPELLE), MON).status == "closed"
    assert gp.to_hours(NOTRE_DAME) is None  # no hours given: unknown, never invented
    assert hours_on(gp.to_hours(NOTRE_DAME), MON).status == "unknown"


def test_prices_only_from_a_complete_range_or_a_free_level():
    bistro = gp.to_price(BISTRO)
    assert {k: bistro[k] for k in ("parsed", "currency", "min", "max")} == {
        "parsed": True, "currency": "EUR", "min": 20, "max": 30}
    assert gp.to_price(OPEN_ENDED)["parsed"] is False and gp.to_price(OPEN_ENDED)["max"] is None
    assert gp.to_price(CAFE) is None  # a price level alone is not an amount
    assert gp.to_price(LOUVRE) is None  # Google gives no entry fee: unknown
    assert gp.to_price(place("p", "Free Park", ["park"], priceLevel="PRICE_LEVEL_FREE"))["free"] is True
    mismatched = place("p", "X", ["restaurant"], priceRange={"startPrice": {"currencyCode": "EUR", "units": "10"},
                                                             "endPrice": {"currencyCode": "USD", "units": "20"}})
    assert gp.to_price(mismatched)["parsed"] is False


# ------------------------------------------------------------------ a city's listings
def test_paris_listings_are_normalised_filtered_and_deduplicated(google):
    listings, meta = gp.city_listings("Paris")
    by_name = {l["name"]: l for l in listings}

    assert set(by_name) == {"Louvre Museum", "Eiffel Tower", "Notre-Dame Cathedral", "Musee d'Orsay",
                            "Sainte-Chapelle", "Le Bistro Test", "Grand Test Restaurant", "Cafe Test"}
    # skipped: the hotel, the permanently closed place, the place 31 km away; the bistro is listed once
    assert meta["dropped_outside_city"] == 1 and meta["source"] == "Google Places"
    louvre = by_name["Louvre Museum"]
    assert louvre["id"] == "gp-paris-louvre-museum" and louvre["google_place_id"] == "p-louvre"
    assert louvre["kind"] == "see" and "museum" in louvre["tags"]
    assert (louvre["lat"], louvre["lon"]) == (48.86, 2.34)
    assert by_name["Le Bistro Test"]["kind"] == "eat" and by_name["Le Bistro Test"]["price_tier"] == "mid-range"
    assert by_name["Cafe Test"]["kind"] == "drink"
    assert "religious" in by_name["Notre-Dame Cathedral"]["tags"]
    assert gp.requests_made() == 3


def test_the_key_is_sent_in_a_header_with_a_field_mask_and_a_city_bias(google):
    gp.city_listings("Paris")
    request = google.requests[0]
    assert request.headers["X-Goog-Api-Key"] == "test-places-key"
    assert "test-places-key" not in str(request.url)
    assert "places.regularOpeningHours" in request.headers["X-Goog-FieldMask"]
    body = json.loads(request.content)
    assert body["pageSize"] == 20 and body["locationBias"]["circle"]["center"]["latitude"] == PARIS["lat"]
    assert json.loads(google.requests[2].content)["includedType"] == "restaurant"


def test_results_are_kept_in_memory_only_and_reused(google, monkeypatch, tmp_path):
    monkeypatch.setattr(td, "CACHE_DIR", tmp_path)
    gp.city_listings("Paris")
    gp.city_listings("paris")
    assert gp.requests_made() == 3  # the second call is served from memory
    assert list(tmp_path.rglob("*")) == []  # nothing from Google is written to disk
    monkeypatch.setenv("TRAVEL_GOOGLE_CACHE_TTL_S", "0")
    gp.reset()
    gp.city_listings("Paris")
    gp.city_listings("Paris")
    assert gp.requests_made() == 6


def test_the_request_limit_stops_further_calls(google, monkeypatch):
    monkeypatch.setenv("TRAVEL_GOOGLE_MAX_REQUESTS", "2")
    with pytest.raises(td.DataError) as err:
        gp.city_listings("Paris")
    assert err.value.status == 429 and err.value.code == "places_request_limit"
    assert len(google.requests) == 2


@pytest.mark.parametrize("fake, status, code", [
    (FakeGoogle(status=403, body={"error": {"status": "PERMISSION_DENIED", "message": "API not enabled"}}),
     502, "places_permission_denied"),
    (FakeGoogle(status=429), 429, "places_quota_exceeded"),
    (FakeGoogle(status=400), 502, "places_bad_request"),
    (FakeGoogle(status=500), 503, "places_unavailable"),
    (FakeGoogle(raise_exc=httpx.ConnectTimeout("slow")), 503, "places_unavailable"),
])
def test_google_errors_are_explicit_and_never_leak_the_key(google, monkeypatch, fake, status, code):
    use(monkeypatch, fake)
    with pytest.raises(td.DataError) as err:
        gp.city_listings("Paris")
    assert (err.value.status, err.value.code) == (status, code)
    assert "test-places-key" not in err.value.message


def test_missing_key_is_reported(google, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY")
    with pytest.raises(td.DataError) as err:
        gp.city_listings("Paris")
    assert err.value.code == "places_provider_not_configured"
    assert google.requests == []


def test_unknown_city_and_empty_results_are_reported(google, monkeypatch):
    def nowhere(city):
        raise td.DataError(404, "place_not_found", "nothing")
    monkeypatch.setattr(td, "city_point", nowhere)
    with pytest.raises(td.DataError) as err:
        gp.city_listings("Atlantis")
    assert err.value.code == "city_not_found" and google.requests == []

    monkeypatch.setattr(td, "city_point", lambda city: dict(PARIS, name="Nowhere"))
    with pytest.raises(td.DataError) as err:
        gp.city_listings("Nowhere")  # Google answers with no places for these queries
    assert err.value.code == "no_places_found"


# ------------------------------------------------------------------ through the Travel API and the Researcher
@pytest.fixture
def api(google, monkeypatch):
    monkeypatch.setenv("TRAVEL_API_KEY", "test-key")
    from services.travel_api import app
    return TestClient(app)


def get(api, path, **params):
    return api.get(path, params=params, headers={"X-API-Key": "test-key"})


def test_travel_api_serves_google_listings_through_the_existing_endpoints(api):
    attractions = get(api, "/api/attractions", city="Paris", interest="museum").json()
    assert attractions["source"] == "Google Places" and "Google Maps attribution" in attractions["licence"]
    names = [a["name"] for a in attractions["attractions"]]
    assert "Louvre Museum" in names and "Musee d'Orsay" in names and "Eiffel Tower" not in names
    louvre = next(a for a in attractions["attractions"] if a["name"] == "Louvre Museum")
    assert louvre["hours"]["closed_days"] == ["Tue"] and louvre["price"] is None
    assert louvre["citation"]["title"].startswith("Louvre Museum - Google Places")

    restaurants = get(api, "/api/restaurants", city="Paris", budget="mid-range").json()
    assert [r["name"] for r in restaurants["restaurants"]] == ["Le Bistro Test"]

    hours = get(api, "/api/hours", name="Louvre", city="Paris").json()
    assert hours["known"] is True and hours["source"] == "Google Places listing"

    health = api.get("/health").json()
    assert health["places"]["google_active"] is True and health["places"]["google_configured"] is True


def test_the_researcher_builds_a_research_bundle_for_paris(api):
    from app.research_adapter import run_research_pipeline

    endpoints = {"find_attractions": ("/api/attractions", {"city": "city", "interests": "interest", "limit": "limit"}),
                 "find_restaurants": ("/api/restaurants", {"city": "city", "budget": "budget", "limit": "limit"}),
                 "get_opening_hours": ("/api/hours", {"place_name": "name", "city": "city"})}

    def caller(name, args):
        if name == "convert_currency":  # TEST RATE: live FX would call the ECB; never used outside this test
            return {"ok": True, "from": args["from_currency"], "to": args["to_currency"], "rate": 1.1,
                    "rate_date": "2026-10-29", "method": "ecb_reference_rate", "source": "test"}
        path, mapping = endpoints[name]
        r = get(api, path, **{mapping[k]: v for k, v in args.items() if v is not None})
        return {"ok": True, **r.json()} if r.status_code < 400 else {"ok": False, "error": r.json().get("error")}

    trip = {"destination": "Paris", "currency": "USD", "start_date": "2026-11-02", "end_date": "2026-11-04",
            "budget": 1500, "travellers": 2, "interests": ["History & Culture", "Art & Museums"]}
    bundle = run_research_pipeline(trip, caller=caller)

    names = {p.name: p for p in bundle.places}
    assert {"Louvre Museum", "Musee d'Orsay", "Le Bistro Test"} <= set(names)
    assert bundle.failures == []
    cands = {c.name: c for c in prepare_candidates(bundle).candidates}
    assert cands["Louvre Museum"].cost.status == "unknown"  # no entry fee from Google: unknown, not free
    bistro = cands["Le Bistro Test"].cost
    assert bistro.status == "range" and bistro.currency == "USD" and float(bistro.rate.rate) == pytest.approx(1.1)
    assert cands["Grand Test Restaurant"].cost.status == "unknown"


# ------------------------------------------------------------------ replay mode is unchanged
def test_replay_mode_never_calls_google_and_keeps_amman(google, monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE", "replay")
    monkeypatch.setenv("TRAVEL_API_KEY", "test-key")
    from services.travel_api import app
    api = TestClient(app)

    amman = get(api, "/api/attractions", city="Amman", interest="history").json()
    assert amman["source"] == "Wikivoyage" and amman["count"] > 0
    paris = get(api, "/api/attractions", city="Paris")
    assert paris.status_code == 404 and paris.json()["error"]["code"] == "not_cached"
    assert google.requests == [] and gp.requests_made() == 0


def test_replay_cities_keep_wikivoyage_even_in_live_google_mode(google):
    assert gp.enabled_for("Paris") is True
    assert gp.enabled_for("Amman") is False and gp.enabled_for("istanbul") is False


def test_record_mode_does_not_use_google(google, monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE", "record")
    assert gp.enabled_for("Paris") is False
