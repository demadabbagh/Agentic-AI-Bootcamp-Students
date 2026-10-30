"""Worldwide destination resolution, offline. Nominatim is mocked at http_get, so the real geocode -> cached()
path runs; no request leaves the machine. The hits are HAND-WRITTEN in Nominatim's jsonv2 + addressdetails shape."""
import pytest
from fastapi.testclient import TestClient

from services import travel_data as td


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def hit(name, lat, lon, country, cc, region, kind="city", category="place", importance=0.5):
    return {"name": name, "display_name": f"{name}, {region}, {country}", "lat": str(lat), "lon": str(lon),
            "osm_type": "relation", "osm_id": abs(hash(name + country)) % 10**6, "category": category,
            "type": kind, "addresstype": kind, "importance": importance,
            "address": {"country": country, "country_code": cc.lower(), "state": region}}


PARIS_FR = hit("Paris", 48.8589, 2.3200, "France", "FR", "Ile-de-France", importance=0.88)
PARIS_TX = hit("Paris", 33.6609, -95.5555, "United States", "US", "Texas", importance=0.52)
PARIS_TN = hit("Paris", 36.3020, -88.3267, "United States", "US", "Tennessee", importance=0.41)
TOKYO = hit("Tokyo", 35.6769, 139.7639, "Japan", "JP", "Tokyo", importance=0.82)
BARCELONA = hit("Barcelona", 41.3826, 2.1771, "Spain", "ES", "Catalonia", importance=0.79)
AMMAN = hit("Amman", 31.9516, 35.9239, "Jordan", "JO", "Amman", importance=0.67)
AMMAN_ADMIN = hit("Amman", 31.9500, 35.9300, "Jordan", "JO", "Amman", kind="administrative",
                  category="boundary", importance=0.6)

ANSWERS = {
    "Paris": [PARIS_FR, PARIS_TX, PARIS_TN],
    "Paris, France": [PARIS_FR],
    "Paris, Texas": [PARIS_TX],
    "Tokyo": [TOKYO],
    "東京": [TOKYO],  # Nominatim answers non-English names; accept-language=en gives the English name back
    "Barcelona, Spain": [BARCELONA],
    "Amman": [AMMAN, AMMAN_ADMIN],
    "Atlantis": [],
    "Null Island": [{"name": "Null Island", "display_name": "Null Island", "lat": "0", "lon": "0"}],
    "Bad Coords": [{"name": "Bad", "display_name": "Bad", "lat": "95.0", "lon": "10"}],
}


@pytest.fixture
def nominatim(monkeypatch):
    asked = []

    def http_get(url, params=None):
        assert url == td.NOMINATIM, url
        asked.append(params)
        return FakeResponse(ANSWERS.get(params["q"], [])), f"{url}?q={params['q']}"

    monkeypatch.setenv("TRAVEL_MODE", "live")
    monkeypatch.setattr(td, "http_get", http_get)
    monkeypatch.setattr(td, "_MEM", {})
    monkeypatch.setattr(td, "cache_read", lambda kind, key: None)  # no fallback to the recorded cache
    return asked


def test_unqualified_ambiguous_name_is_flagged_not_silently_picked(nominatim):
    rec, _ = td.geocode("Paris")
    assert (rec["lat"], rec["lon"]) == (48.8589, 2.32)  # Nominatim's top hit, as before
    assert rec["country_code"] == "FR"
    assert rec["ambiguous"] is True
    assert "Texas" in rec["note"] and "add the country" in rec["note"].lower()
    assert {c["country_code"] for c in rec["candidates"]} == {"FR", "US"}


def test_qualified_names_resolve_to_the_requested_place(nominatim):
    fr, _ = td.geocode("Paris, France")
    tx, _ = td.geocode("Paris, Texas")
    assert fr["ambiguous"] is False and fr["country"] == "France"
    assert tx["ambiguous"] is False
    assert tx["candidates"][0]["region"] == "Texas"
    assert tx["lat"] == pytest.approx(33.6609)


@pytest.mark.parametrize("query,cc", [("Tokyo", "JP"), ("Barcelona, Spain", "ES"), ("Amman", "JO"), ("東京", "JP")])
def test_worldwide_destinations_resolve_with_country(nominatim, query, cc):
    rec, _ = td.geocode(query)
    assert rec["country_code"] == cc
    assert rec["ambiguous"] is False  # Amman's city node and its boundary are the same place
    assert -90 <= rec["lat"] <= 90 and -180 <= rec["lon"] <= 180
    assert rec["source"] == "Nominatim (OpenStreetMap)"


def test_asks_nominatim_for_alternatives_and_address_details(nominatim):
    td.geocode("Tokyo")
    assert nominatim[0]["limit"] == td.GEOCODE_CANDIDATES
    assert nominatim[0]["addressdetails"] == 1


def test_no_results_is_an_error_not_an_invented_place(nominatim):
    with pytest.raises(td.DataError) as err:
        td.geocode("Atlantis")
    assert err.value.status == 404 and err.value.code == "place_not_found"


@pytest.mark.parametrize("query", ["Null Island", "Bad Coords"])
def test_invalid_coordinates_are_dropped(nominatim, query):
    with pytest.raises(td.DataError):
        td.geocode(query)


def test_upstream_failure_without_cache_is_a_503(monkeypatch):
    def broken(url, params=None):
        raise TimeoutError("Nominatim timed out")

    monkeypatch.setenv("TRAVEL_MODE", "live")
    monkeypatch.setattr(td, "http_get", broken)
    monkeypatch.setattr(td, "_MEM", {})
    monkeypatch.setattr(td, "cache_read", lambda kind, key: None)
    with pytest.raises(td.DataError) as err:
        td.geocode("Tokyo")
    assert err.value.status == 503 and err.value.code == "upstream_unavailable"


def test_is_ambiguous_trusts_a_qualified_query():
    cands = [td._candidate(PARIS_FR), td._candidate(PARIS_TX)]
    assert td.is_ambiguous("Paris", cands) is True
    assert td.is_ambiguous("Paris, France", cands) is False
    # Buildings and streets named after a city do not make the city ambiguous.
    hotel = td._candidate(hit("Paris", 36.11, -115.17, "United States", "US", "Nevada", kind="hotel", category="tourism"))
    assert td.is_ambiguous("Paris", [td._candidate(PARIS_FR), hotel]) is False


def test_geocode_endpoint_passes_candidates_through(nominatim, monkeypatch):
    from services import travel_api

    monkeypatch.setenv("TRAVEL_API_KEY", "test-key")
    monkeypatch.setattr(travel_api, "fault", lambda op: None)
    client = TestClient(travel_api.app)
    body = client.get("/api/geocode", params={"q": "Paris"}, headers={"X-API-Key": "test-key"}).json()
    assert body["ambiguous"] is True
    assert len(body["candidates"]) == 3
    assert body["resolved_by"] == "nominatim"
