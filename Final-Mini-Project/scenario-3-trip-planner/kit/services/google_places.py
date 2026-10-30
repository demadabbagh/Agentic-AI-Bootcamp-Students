"""Live attraction and restaurant listings from Google Places API (New), in the kit's listing shape.

Opt-in: TRAVEL_PLACES_PROVIDER=google with TRAVEL_MODE=live (not record), and GOOGLE_PLACES_API_KEY. The cities of
the replay cache (Istanbul, Amman) keep their Wikivoyage data, and replay mode never calls Google. The listings
go through the same endpoints, enrich() and MCP tools as Wikivoyage listings, so nothing downstream changes.

Facts only, never filled in:
  hours   regularOpeningHours.periods -> the parse_hours() structure; no hours in the result = unknown
  price   priceRange with both ends in one currency -> amounts; priceLevel FREE -> free; anything else
          (a level without amounts, an open-ended range) is no price: unknown, never a guess
  place   id, name, coordinates, address, types, Google Maps link (the source_url and citation)

Limits and terms: one Text Search per category per city (attractions, museums and historic sites, restaurants),
at most TRAVEL_GOOGLE_MAX_REQUESTS calls per process (default 30), results kept in memory for at most
TRAVEL_GOOGLE_CACHE_TTL_S seconds (default 3600, 0 = off) and never written to disk: Google Maps Platform terms
do not allow storing Places content. Results more than MAX_DISTANCE_KM from the city centre are dropped.
"""
from __future__ import annotations

import math
import os
import threading
import time
from urllib.parse import quote_plus

import httpx

from . import travel_data as td

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Only the fields the kit uses. Hours and prices bill at a higher Text Search SKU than the basic fields.
FIELDS = ("id", "displayName", "formattedAddress", "location", "types", "primaryType", "businessStatus",
          "googleMapsUri", "websiteUri", "regularOpeningHours", "priceLevel", "priceRange")
FIELD_MASK = ",".join(f"places.{f}" for f in FIELDS)
PAGE_SIZE = 20
MAX_DISTANCE_KM = 30
LICENCE = ("Places data from Google Maps Platform (Places API). Show the Google Maps attribution, and do not store "
           "this content beyond the session (Google Maps Platform Terms of Service).")
SOURCE = "Google Places"

# (section, text query, includedType or None)
QUERIES = (("attractions", "top tourist attractions in {city}", None),
           ("museums and historic sites", "museums and historic sites in {city}", None),
           ("restaurants", "restaurants in {city}", "restaurant"))

EAT = {"restaurant", "food_court", "meal_takeaway", "fast_food_restaurant", "diner"}
DRINK = {"cafe", "coffee_shop", "bar", "pub", "wine_bar", "tea_house", "cafeteria"}
BUY = {"market", "farmers_market", "flea_market", "shopping_mall"}
SKIP = {"lodging", "hotel", "transit_station", "train_station", "bus_station", "subway_station", "airport",
        "parking", "car_rental", "travel_agency", "tour_agency"}
TYPE_TAGS = {
    "museum": "museum", "art_gallery": "art", "art_studio": "art", "historical_landmark": "history",
    "historical_place": "history", "monument": "history", "castle": "history", "cultural_landmark": "history",
    "church": "religious", "mosque": "religious", "synagogue": "religious", "hindu_temple": "religious",
    "buddhist_temple": "religious", "shinto_shrine": "religious", "place_of_worship": "religious",
    "park": "nature", "national_park": "nature", "garden": "nature", "botanical_garden": "nature",
    "beach": "nature", "hiking_area": "nature", "observation_deck": "views", "market": "markets",
    "farmers_market": "markets", "flea_market": "markets", "shopping_mall": "markets", "zoo": "family",
    "aquarium": "family", "amusement_park": "family", "water_park": "family",
}
PRICE_TIERS = {"PRICE_LEVEL_INEXPENSIVE": "budget", "PRICE_LEVEL_MODERATE": "mid-range",
               "PRICE_LEVEL_EXPENSIVE": "splurge", "PRICE_LEVEL_VERY_EXPENSIVE": "splurge"}

_http: httpx.Client | None = None
_lock = threading.Lock()
_requests_made = 0
_memory: dict[str, tuple[float, list[dict], dict]] = {}


# ------------------------------------------------------------------ configuration
def provider() -> str:
    return os.getenv("TRAVEL_PLACES_PROVIDER", "wikivoyage").strip().lower()


def max_requests() -> int:
    return int(os.getenv("TRAVEL_GOOGLE_MAX_REQUESTS", "30"))


def cache_ttl() -> float:
    return float(os.getenv("TRAVEL_GOOGLE_CACHE_TTL_S", "3600"))


def requests_made() -> int:
    return _requests_made


def reset() -> None:
    """For tests: forget the request count and the in-memory results."""
    global _requests_made
    with _lock:
        _requests_made = 0
        _memory.clear()


def replay_city(city: str) -> bool:
    known = {td.fold(c["name"]) for c in td.manifest().get("cities", [])}
    return td.fold((city or "").split(",")[0]) in known


def enabled_for(city: str) -> bool:
    """Google serves this city: selected, live mode, and not one of the replay cache's cities. Not record mode:
    it writes routes (with Google's coordinates) to the disk cache, and Places content may not be stored."""
    return provider() == "google" and td.mode() == "live" and not replay_city(city)


def status() -> dict:
    return {"provider": provider(), "google_configured": bool(os.getenv("GOOGLE_PLACES_API_KEY")),
            "google_requests_made": _requests_made, "google_request_limit": max_requests(),
            "google_active": provider() == "google" and td.mode() == "live"}


# ------------------------------------------------------------------ one Text Search
def _client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0))
    return _http


def _error_message(response: httpx.Response) -> str:
    try:
        err = response.json().get("error") or {}
        return str(err.get("message") or err.get("status") or "")[:200]
    except ValueError:
        return ""


def search(text_query: str, included_type: str | None = None, *, bias: dict | None = None) -> list[dict]:
    """One Places Text Search. Raises td.DataError with a code the API can return as is."""
    global _requests_made
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise td.DataError(503, "places_provider_not_configured",
                           "TRAVEL_PLACES_PROVIDER=google is set but GOOGLE_PLACES_API_KEY is not.")
    with _lock:
        if _requests_made >= max_requests():
            raise td.DataError(429, "places_request_limit",
                               f"The Google Places request limit for this process ({max_requests()}) is reached. "
                               "Raise TRAVEL_GOOGLE_MAX_REQUESTS or restart the service.")
        _requests_made += 1
    body: dict = {"textQuery": text_query, "pageSize": PAGE_SIZE, "languageCode": "en"}
    if included_type:
        body["includedType"] = included_type
    if bias:
        body["locationBias"] = {"circle": {"center": {"latitude": bias["lat"], "longitude": bias["lon"]},
                                           "radius": 20000.0}}
    try:  # the key goes in a header, never in the URL or an error message
        response = _client().post(SEARCH_URL, json=body,
                                  headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK})
    except httpx.TimeoutException:
        raise td.DataError(503, "places_unavailable", "Google Places did not answer in time.") from None
    except httpx.HTTPError as exc:
        raise td.DataError(503, "places_unavailable", f"Google Places is not reachable ({type(exc).__name__}).") from None
    if response.status_code in (401, 403):
        raise td.DataError(502, "places_permission_denied",
                           "Google Places refused the key: check that Places API (New) is enabled, billing is "
                           f"active and the key's restrictions allow it. {_error_message(response)}".strip())
    if response.status_code == 429:
        raise td.DataError(429, "places_quota_exceeded", f"Google Places quota exceeded. {_error_message(response)}".strip())
    if response.status_code == 400:
        raise td.DataError(502, "places_bad_request", f"Google Places rejected the request. {_error_message(response)}".strip())
    if response.status_code >= 400:
        raise td.DataError(503, "places_unavailable", f"Google Places failed ({response.status_code}).")
    try:
        return response.json().get("places") or []
    except ValueError:
        raise td.DataError(503, "places_unavailable", "Google Places returned an unreadable answer.") from None


# ------------------------------------------------------------------ normalisation (facts only)
def _kind(types: list[str], section: str) -> str | None:
    t = set(types)
    if t & SKIP and not t & set(TYPE_TAGS):
        return None
    if section == "restaurants":
        return "drink" if t & DRINK and not t & EAT else ("eat" if t & (EAT | DRINK) or any(
            x.endswith("_restaurant") for x in t) else None)
    if t & EAT or any(x.endswith("_restaurant") for x in t):
        return None  # a restaurant in an attraction search: the restaurant search lists restaurants
    return "buy" if t & BUY else "see"


def _amount(money: dict | None) -> float | None:
    if not money or money.get("units") is None and money.get("nanos") is None:
        return None
    try:
        return round(int(money.get("units") or 0) + int(money.get("nanos") or 0) / 1e9, 2)
    except (TypeError, ValueError):
        return None


def to_price(place: dict) -> dict | None:
    """A parse_price()-shaped price, or None (unknown). Never derived from a price level alone."""
    if place.get("priceLevel") == "PRICE_LEVEL_FREE":
        return {"raw": "Free (Google Places price level)", "parsed": True, "free": True, "mentions_free": True,
                "currency": None, "amounts": {}, "min": 0, "max": 0, "as_of_year": None, "source": SOURCE}
    rng = place.get("priceRange") or {}
    start, end = rng.get("startPrice"), rng.get("endPrice")
    low, high = _amount(start), _amount(end)
    currency = (start or {}).get("currencyCode")
    if low is None or not currency:
        return None
    if high is None or (end or {}).get("currencyCode") != currency or high < low:
        return {"raw": f"{currency} {low:g}+ (Google Places price range, no upper end)", "parsed": False,
                "free": False, "mentions_free": False, "currency": None, "amounts": {}, "min": None, "max": None,
                "as_of_year": None, "source": SOURCE,
                "note": "An open-ended price range cannot be budgeted; treat the cost as unknown."}
    return {"raw": f"{currency} {low:g}-{high:g} (Google Places price range)", "parsed": True, "free": False,
            "mentions_free": False, "currency": currency, "amounts": {currency: sorted({low, high})},
            "min": low, "max": high, "as_of_year": None, "source": SOURCE}


def _hhmm(point: dict) -> str:
    return f"{int(point.get('hour', 0)):02d}:{int(point.get('minute', 0)):02d}"


def to_hours(place: dict) -> dict | None:
    """A parse_hours()-shaped structure from regularOpeningHours, or None (unknown)."""
    base = {"parsed": True, "days_stated": True, "seasonal": [], "open_24h": False, "temporarily_closed": False,
            "source": SOURCE, "note": "Regular hours from Google Places; holidays and special days may differ."}
    if place.get("businessStatus") == "CLOSED_TEMPORARILY":
        return {**base, "raw": "Temporarily closed (Google Places)", "temporarily_closed": True, "open_days": [],
                "closed_days": td.DAYS[:], "weekly": {}}
    opening = place.get("regularOpeningHours") or {}
    periods = opening.get("periods") or []
    if not periods:
        return None
    raw = "; ".join(opening.get("weekdayDescriptions") or []) or "Opening hours from Google Places"
    if len(periods) == 1 and "close" not in periods[0]:  # Google's way of saying open around the clock
        return {**base, "raw": raw, "open_24h": True, "open_days": td.DAYS[:], "closed_days": [],
                "weekly": {d: ["00:00-24:00"] for d in td.DAYS}}
    weekly: dict[str, list[str]] = {}
    for period in periods:
        start, end = period.get("open"), period.get("close")
        if not start or not end or start.get("day") is None:
            continue
        day = td.DAYS[(int(start["day"]) + 6) % 7]  # Google: 0 = Sunday; the kit: 0 = Monday
        close = _hhmm(end)
        if close == "00:00" and end.get("day") != start.get("day"):
            close = "24:00"
        rng = f"{_hhmm(start)}-{close}"
        if rng not in weekly.setdefault(day, []):
            weekly[day].append(rng)
    if not weekly:
        return {"raw": raw, "parsed": False, "note": "Google Places hours could not be read; treat them as unknown.",
                "days_stated": False, "open_days": [], "closed_days": [], "weekly": {}, "seasonal": [],
                "open_24h": False, "temporarily_closed": False, "source": SOURCE}
    order = {d: i for i, d in enumerate(td.DAYS)}
    weekly = {d: weekly[d] for d in sorted(weekly, key=order.get)}
    return {**base, "raw": raw, "open_days": list(weekly), "closed_days": [d for d in td.DAYS if d not in weekly],
            "weekly": weekly}


def _distance_km(a: dict, lat: float, lon: float) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lon - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def to_listing(place: dict, section: str, city: str, city_slug: str) -> dict | None:
    """The kit's internal listing (as parse_listings() builds it), or None for a place the kit does not list."""
    name = ((place.get("displayName") or {}).get("text") or "").strip()
    location = place.get("location") or {}
    if not name or place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return None
    types = list(place.get("types") or [])
    kind = _kind(types, section)
    if kind is None:
        return None
    lat, lon = location.get("latitude"), location.get("longitude")
    tags = sorted({TYPE_TAGS[t] for t in types if t in TYPE_TAGS} | set(td.tags_for(name, kind))
                  | ({"food"} if kind in ("eat", "drink") else set()))
    return {
        "id": f"gp-{city_slug}-{td.slug(name)}",
        "google_place_id": place.get("id"),
        "kind": kind,
        "name": name,
        "alt": None,
        "district": None,
        "section": section,
        "price_tier": PRICE_TIERS.get(place.get("priceLevel")) if kind in ("eat", "drink") else None,
        "address": place.get("formattedAddress"),
        "lat": lat if isinstance(lat, (int, float)) else None,
        "lon": lon if isinstance(lon, (int, float)) else None,
        "url": place.get("websiteUri"),
        "hours_raw": None,
        "price_raw": None,
        "hours_parsed": to_hours(place),
        "price_parsed": to_price(place),
        "last_edit": None,
        "wikidata": None,
        "description": "",
        "tags": tags,
        "dietary_mentions": [],
        "source": SOURCE,
        "source_url": place.get("googleMapsUri"),
        "types": types,
        "city": city,
    }


# ------------------------------------------------------------------ a city's listings
def city_listings(city: str) -> tuple[list[dict], dict]:
    """Attractions and restaurants of a city from Google Places: the same (listings, meta) as td.city_listings."""
    title = td.city_title(city)
    key = td.fold(title)
    hit = _memory.get(key)
    if hit and time.time() - hit[0] < cache_ttl():
        return [dict(item) for item in hit[1]], dict(hit[2])
    try:
        centre = td.city_point(title)
    except td.DataError as exc:
        raise td.DataError(404, "city_not_found", f"'{title}' could not be located, so its places cannot be "
                           f"searched ({exc.code}).") from None
    city_slug = td.slug(title)
    listings, seen_places, seen_ids, dropped = [], set(), set(), 0
    for section, query, included_type in QUERIES:
        for place in search(query.format(city=title), included_type, bias=centre):
            if place.get("id") in seen_places:
                continue
            listing = to_listing(place, section, title, city_slug)
            if listing is None:
                continue
            if listing["lat"] is not None and _distance_km(centre, listing["lat"], listing["lon"]) > MAX_DISTANCE_KM:
                dropped += 1
                continue
            seen_places.add(place.get("id"))
            uid, n = listing["id"], 2
            while uid in seen_ids:
                uid, n = f"{listing['id']}-{n}", n + 1
            seen_ids.add(uid)
            listings.append(dict(listing, id=uid))
    if not listings:
        raise td.DataError(404, "no_places_found", f"Google Places found no attractions or restaurants in '{title}'.")
    meta = {"city": title, "mode": "live", "retrieved_at": td.now_iso(), "source": SOURCE, "licence": LICENCE,
            "pages": [f"https://www.google.com/maps/search/?api=1&query={quote_plus(q.format(city=title))}"
                      for _, q, _ in QUERIES],
            "dropped_outside_city": dropped}
    if cache_ttl() > 0:
        _memory[key] = (time.time(), [dict(item) for item in listings], dict(meta))
    return listings, meta
