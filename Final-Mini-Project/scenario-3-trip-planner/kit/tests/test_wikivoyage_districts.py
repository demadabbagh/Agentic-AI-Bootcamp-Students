"""District discovery for live Wikivoyage cities, offline. Wikivoyage is mocked at http_get, so the real
wv_page -> page_record -> cached() path runs; no request leaves the machine. The pages are HAND-WRITTEN wikitext
that mimics real page structures (Paris groups its districts in regionNitems; London nests them)."""
import pytest
from fastapi.testclient import TestClient

from services import travel_data as td


def listing(kind, name, lat=None, lon=None, **params):
    coords = f"| lat={lat} | long={lon} " if lat is not None else ""
    extra = "".join(f"| {k}={v} " for k, v in params.items())
    return f"{{{{{kind} | name={name} {coords}{extra}| content=A test listing. }}}}"


def page(*sections, regionlist="", text=""):
    body = [regionlist, text]
    for heading, items in sections:
        body.append(f"=={heading}==")
        body += items
    return "\n".join(body)


PARIS_MAIN = page(
    ("See", [listing("see", "Louvre Museum"), listing("see", "Eiffel Tower", 48.8584, 2.2945)]),
    regionlist="""{{Regionlist
| regionmap=Wikivoyage_paris_map.svg
| region1name=Central Paris
| region1items=[[Paris/1st arrondissement|1st arr.]], [[Paris/4th arrondissement|4th arr.]]
| region1description=The ''Musée du Louvre'' and [[Île-de-France]] day trips.
| region2name=Left Bank
| region2items=[[Paris/5th arrondissement|5th arr.]], [[Paris/7th arrondissement|7th arr.]]
| region3name=[[Paris/La Défense|La Défense]]
| region3items=
}}""",
    text="See also [[File:Paris map.png]] and [[Category:Paris]] and [[Paris/1st arrondissement]] again.")
PARIS_PAGES = {
    "Paris": PARIS_MAIN,
    "Paris/1st arrondissement": page(
        ("See", [listing("see", "Louvre Museum", 48.8606, 2.3376, hours="9AM-6PM, closed Tue", price="€22"),
                 listing("see", "Sainte-Chapelle", 48.8554, 2.3450)]),
        ("Eat", [listing("eat", "Bistro Un", 48.861, 2.34)])),
    "Paris/4th arrondissement": page(
        ("See", [listing("see", "Notre-Dame de Paris", 48.853, 2.3499), listing("see", "Musée Picasso", 48.8598, 2.3625)]),
        ("Eat", [listing("eat", "Café Quatre", 48.855, 2.355)])),
    "Paris/5th arrondissement": page(
        ("See", [listing("see", "Panthéon", 48.8462, 2.3464)]), ("Eat", [listing("eat", "Bistro Cinq", 48.846, 2.347)])),
    "Paris/7th arrondissement": page(
        ("See", [listing("see", "Musée d'Orsay", 48.86, 2.3266), listing("see", "Eiffel Tower", 48.8584, 2.2945)])),
    "Paris/La Défense": page(
        ("See", [listing("see", "Grande Arche", 48.8925, 2.2358), listing("see", "Défense Gallery One", 48.89, 2.24),
                 listing("see", "Défense Gallery Two", 48.891, 2.241), listing("see", "Défense Gallery Three", 48.892, 2.242)]),
        ("Eat", [listing("eat", "Tower Canteen", 48.89, 2.238)])),
}

LONDON_PAGES = {
    "London": page(regionlist="{{Regionlist\n| region1name=[[London/Central|Central London]]\n"
                              "| region2name=[[London/East|East London]]\n}}"),
    "London/Central": page(("See", [listing("see", "British Museum", 51.5194, -0.127)]),
                           regionlist="{{Regionlist\n| region1name=[[London/Central/Westminster|Westminster]]\n}}"),
    "London/East": page(("See", [listing("see", "Tower of London", 51.5081, -0.0759)])),
    "London/Central/Westminster": page(
        ("See", [listing("see", "Westminster Abbey", 51.4993, -0.1273)]),
        regionlist="{{Regionlist\n| region1name=[[London/Central/Westminster/Deep|Too deep]]\n}}"),
    "London/Central/Westminster/Deep": page(("See", [listing("see", "Never Fetched", 51.5, -0.1)])),
}

NO_REGIONLIST = {  # a city whose districts are only linked in the text
    "Lyon": page(("See", [listing("see", "Fourvière Basilica", 45.7623, 4.8227)]),
                 text="The old town is described in [[Lyon/Vieux Lyon]]; the [[Rhône]] runs through it."),
    "Lyon/Vieux Lyon": page(("See", [listing("see", "Saint-Jean Cathedral", 45.7607, 4.8274)])),
}

MANY = {"Bigcity": page(regionlist="{{Regionlist\n" + "\n".join(
    f"| region{n}name=[[Bigcity/D{n}|D{n}]]" for n in range(1, 31)) + "\n}}")}
MANY.update({f"Bigcity/D{n}": page(("See", [listing("see", f"Sight {n}", 10 + n / 100, 20)])) for n in range(1, 31)})
ALL_PAGES = {**PARIS_PAGES, **LONDON_PAGES, **NO_REGIONLIST, **MANY}  # read at request time: tests may patch it


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def wikivoyage(monkeypatch):
    """Live mode with Wikivoyage answered from the dicts above; records every page requested."""
    pages = ALL_PAGES
    asked = []

    def http_get(url, params=None):
        assert url == td.WV_API, url  # nothing but Wikivoyage is called
        title = params["page"]
        asked.append(title)
        if title not in pages:
            return FakeResponse({"error": {"code": "missingtitle"}}), url
        return FakeResponse({"parse": {"title": title, "wikitext": pages[title], "revid": 1}}), url

    monkeypatch.setenv("TRAVEL_MODE", "live")
    monkeypatch.delenv("TRAVEL_OVERLAYS", raising=False)
    monkeypatch.setattr(td, "http_get", http_get)
    monkeypatch.setattr(td, "_MEM", {})
    return asked


# ------------------------------------------------------------------ parse_districts
def test_grouped_region_items_and_linked_names_are_both_found():
    assert td.parse_districts("Paris", PARIS_MAIN) == [
        "Paris/1st arrondissement", "Paris/4th arrondissement", "Paris/5th arrondissement",
        "Paris/7th arrondissement", "Paris/La Défense"]  # page order; no File:, Category:, or region page


def test_the_old_rule_would_have_found_only_la_defense():
    """The live bug: only regionNname links were read, and Paris names most regions in plain text."""
    import re
    old = [s.strip() for s in re.findall(r"region\d+name\s*=\s*\[\[([^\]|]+)", PARIS_MAIN)]
    assert old == ["Paris/La Défense"]


def test_subpage_links_are_the_fallback_without_a_regionlist():
    assert td.parse_districts("Lyon", NO_REGIONLIST["Lyon"]) == ["Lyon/Vieux Lyon"]  # not [[Rhône]]


def test_a_regionlist_may_link_districts_that_are_not_subpages():
    text = "{{Regionlist\n| region1name=[[Manhattan]]\n| region2items=[[Brooklyn]], [[Queens|the Queens]]\n}}"
    assert td.parse_districts("New York City", text) == ["Manhattan", "Brooklyn", "Queens"]


# ------------------------------------------------------------------ city_listings
def test_paris_loads_every_district_and_removes_the_city_pages_copies(wikivoyage):
    listings, meta = td.city_listings("Paris")

    assert wikivoyage == ["Paris", "Paris/1st arrondissement", "Paris/4th arrondissement",
                          "Paris/5th arrondissement", "Paris/7th arrondissement", "Paris/La Défense"]
    assert len(meta["pages"]) == 6 and meta["source"] == "Wikivoyage"
    names = [l["name"] for l in listings]
    assert names.count("Louvre Museum") == 1 and names.count("Eiffel Tower") == 1
    louvre = next(l for l in listings if l["name"] == "Louvre Museum")
    assert louvre["district"] == "1st arrondissement" and louvre["hours_raw"]  # the district page's full listing
    assert {l["district"] for l in listings if l["kind"] == "eat"} == {
        "1st arrondissement", "4th arrondissement", "5th arrondissement", "La Défense"}


def test_same_name_far_apart_is_two_places():
    a = {"name": "Starbucks", "lat": 48.86, "lon": 2.33, "district": "1st"}
    b = {"name": "Starbucks", "lat": 48.83, "lon": 2.40, "district": "12th"}
    assert td._same_place(a, b) is False
    assert td._same_place(a, dict(b, lat=48.8601, lon=2.3301)) is True
    assert td._same_place(dict(a, lat=None), dict(b, district=None)) is True  # city page copy without coordinates


def test_nested_districts_are_followed_two_levels_deep_only(wikivoyage):
    listings, _ = td.city_listings("London")
    assert "London/Central/Westminster" in wikivoyage
    assert "London/Central/Westminster/Deep" not in wikivoyage
    assert {l["name"] for l in listings} == {"British Museum", "Tower of London", "Westminster Abbey"}


def test_district_pages_are_capped(wikivoyage):
    listings, meta = td.city_listings("Bigcity")
    assert len(wikivoyage) == 1 + td.MAX_DISTRICTS
    assert len(meta["pages"]) == td.MAX_DISTRICTS + 1 and len(listings) == td.MAX_DISTRICTS


def test_pages_are_cached_for_the_session(wikivoyage):
    td.city_listings("Paris")
    td.city_listings("Paris")
    assert len(wikivoyage) == 6  # the second call is served from the live in-memory cache


def test_a_missing_district_page_is_skipped(wikivoyage, monkeypatch):
    monkeypatch.setitem(ALL_PAGES, "Paris", PARIS_MAIN.replace("[[Paris/5th arrondissement|5th arr.]]",
                                                                 "[[Paris/Missing|gone]]"))
    listings, meta = td.city_listings("Paris")
    assert "Paris/Missing" in wikivoyage and all("Missing" not in p for p in meta["pages"])
    assert "Panthéon" not in {l["name"] for l in listings}


# ------------------------------------------------------------------ ranking and distribution
def test_relevance_first_then_districts_take_turns():
    items = [{"name": f"{d}{i}", "district": d, "lat": 1.0, "tags": ["history"] if i == 0 else [],
              "description": ""} for d in ("A", "B", "C") for i in range(3)]
    order = [i["name"] for i in td.distribute(items, ["history"])]
    assert order[:3] == ["A0", "B0", "C0"]  # the relevant ones, one per district
    assert order[3:6] == ["A1", "B1", "C1"]  # then the districts take turns


def test_more_matched_interests_rank_higher_and_unmappable_places_last():
    items = [{"name": "one", "district": "A", "lat": 1.0, "tags": ["history"], "description": ""},
             {"name": "two", "district": "B", "lat": 1.0, "tags": ["history", "museum"], "description": ""},
             {"name": "nomap", "district": "C", "lat": None, "tags": ["history", "museum"], "description": ""}]
    assert [i["name"] for i in td.distribute(items, ["history", "museum"])] == ["two", "nomap", "one"]
    assert [i["name"] for i in td.distribute(items, [])][-1] == "nomap"


@pytest.fixture
def api(wikivoyage, monkeypatch):
    monkeypatch.setenv("TRAVEL_API_KEY", "test-key")
    from services.travel_api import app
    return TestClient(app)


def test_attractions_are_not_dominated_by_the_first_district(api):
    r = api.get("/api/attractions", params={"city": "Paris", "limit": 4}, headers={"X-API-Key": "test-key"})
    districts = [a["district"] for a in r.json()["attractions"]]
    assert len(set(districts)) == 4  # four results from four different districts


def test_restaurants_come_from_the_district_pages(api):
    data = api.get("/api/restaurants", params={"city": "Paris"}, headers={"X-API-Key": "test-key"}).json()
    assert data["count"] == 4 and data["total_matching"] == 4
    assert len(data["source_pages"]) == 6


def test_responses_declare_utf8_and_keep_accents(api):
    r = api.get("/api/attractions", params={"city": "Paris", "limit": 50}, headers={"X-API-Key": "test-key"})
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    assert "Musée Picasso" in {a["name"] for a in r.json()["attractions"]}
    assert "Musée".encode("utf-8") in r.content  # real UTF-8 bytes, not escapes or Latin-1
    error = api.get("/api/attractions", params={"city": "Paris"})  # no key: the error path too
    assert error.status_code == 401 and error.headers["content-type"] == "application/json; charset=utf-8"


# ------------------------------------------------------------------ replay mode is unchanged
def test_replay_never_calls_wikivoyage_and_keeps_its_listings(monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE", "replay")
    monkeypatch.delenv("TRAVEL_OVERLAYS", raising=False)

    def no_network(url, params=None):
        raise AssertionError("replay mode must not call the network")
    monkeypatch.setattr(td, "http_get", no_network)

    assert len(td.city_listings("Istanbul")[0]) == 304
    assert len(td.city_listings("Amman")[0]) == 44
    with pytest.raises(td.DataError) as err:
        td.city_listings("Paris")
    assert err.value.code == "not_cached"
