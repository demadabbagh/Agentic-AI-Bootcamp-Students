"""Worldwide city search for the trip form's destination box (search as you type).

Provider: the Open-Meteo Geocoding API (https://open-meteo.com/en/docs/geocoding-api), free and without a key,
built on GeoNames. It is made for search as you type: three or more characters match by prefix and
fuzzily, two match exactly, one returns nothing. Results carry a stable GeoNames id, coordinates, country,
region, population and time zone. (The kit's geocode tool is Nominatim, an exact single-place lookup whose
usage policy forbids autocomplete, and in replay mode it answers only recorded queries, so it is not used here.)

Short queries (one or two letters) are answered from a bundled list of popular destinations
(data/popular_destinations.json: GeoNames records as the same provider serves them, built by build_popular.py),
matched by the start of the name and ranked by population, without calling the provider: the provider cannot
suggest "Amman" for "Am" (two letters match exactly), and it is never asked about a single letter.

A country or a region is not a destination here: the trip planner plans a city. When the query is the whole name
of a country or a first-level region ("Italy", "Georgia", "Texas"; areas.py, GeoNames), the answer lists them in
`areas`, each with suggested cities there (the bundled popular destinations, and the places found in that
region), so the box asks for a city instead of offering a namesake town ("Italy, Texas") as if it were the
country. The provider's own country records (GeoNames PCLI) are never returned as places.

Only populated places are returned (GeoNames feature codes PPL*, without historical, abandoned, destroyed or
"section of" places). Nothing is invented: no result is a valid answer, and a provider failure is reported
as one, never filled in.

Limits that keep the free provider usable for everyone: results are cached (shorter for an empty answer),
and the number of provider calls per minute is capped for the whole server; a cached query never counts.

Licence: Open-Meteo's free API is for non-commercial use only. In production the provider is called only after
the team's explicit decision (app/open_meteo.py: a paid key, sent to the customer- server, or
RIHLA_OPEN_METEO_NONCOMMERCIAL=true); otherwise every search answers 503 destinations_unavailable and the
traveller can still type the destination.
"""
from __future__ import annotations

import math
import os
import re
import threading
import json
import time
import unicodedata
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

import httpx

from app.deployment_env import deployment_env
from app.destinations.areas import area_matches
from app.open_meteo import api_key as open_meteo_key, licence_allows, protect_httpx_logs

PROVIDER_URL = "https://geocoding-api.open-meteo.com/v1/search"
CUSTOMER_URL = "https://customer-geocoding-api.open-meteo.com/v1/search"  # paid plan ("prefix customer-")
SOURCE = {
    "provider": "Open-Meteo Geocoding API (GeoNames)",
    "url": "https://open-meteo.com/en/docs/geocoding-api",
    "licence": "CC BY 4.0",
    "attribution": "City data from GeoNames (geonames.org), CC BY 4.0, via Open-Meteo.com",
}

MIN_QUERY_LENGTH = 1
PROVIDER_MIN_LENGTH = 3  # shorter queries are answered from the popular destinations list only
POPULAR_PATH = Path(__file__).parent / "data" / "popular_destinations.json"
MAX_QUERY_LENGTH = 80
DEFAULT_LIMIT = 8
MAX_LIMIT = 10
OVERFETCH = 3  # ask the provider for more, so that non-city matches can be dropped and the list stays full
PROVIDER_MAX_COUNT = 30

# GeoNames populated-place codes that are not a destination today.
EXCLUDED_CODES = {"PPLH", "PPLQ", "PPLW", "PPLX", "PPLCH"}
# GeoNames codes of a country or territory (independent, dependent, freely associated, section, ...).
COUNTRY_CODES = {"PCL", "PCLD", "PCLF", "PCLI", "PCLIX", "PCLS"}
AREA_SUGGESTIONS = 8


class DestinationError(Exception):
    """A search that could not be answered: an HTTP status, a stable code, a message for the traveller."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 retry_after: int | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable, self.retry_after = retryable, retry_after

    def as_dict(self) -> dict:
        error = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.retry_after is not None:
            error["retry_after"] = self.retry_after
        return {"error": error}


SPELLINGS = {"marrakech": "Marrakesh"}


def _env_number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    return value if low <= value <= high else default


def normalize_query(raw: Any) -> str:
    """The query as searched: Unicode NFC, whitespace collapsed. Raises DestinationError(422) when unusable."""
    if not isinstance(raw, str) or not raw.strip():
        raise DestinationError(422, "missing_query", "Type the first letters of a city name.")
    text = " ".join(unicodedata.normalize("NFC", raw).split())
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        raise DestinationError(422, "invalid_query", "The search text contains characters that are not allowed.")
    if not any(ch.isalpha() for ch in text):
        raise DestinationError(422, "invalid_query", "Type a city name (letters, not only numbers or symbols).")
    if len(text) > MAX_QUERY_LENGTH:
        raise DestinationError(422, "query_too_long", f"The search text is limited to {MAX_QUERY_LENGTH} characters.")
    return text


def _fold(text: str) -> str:
    """Case- and accent-insensitive form, to compare names ("São Paulo" == "sao paulo")."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_result(item: Any) -> dict | None:
    """One provider result as Rihla's destination, or None when it is not a usable populated place."""
    if not isinstance(item, dict):
        return None
    code = _text(item.get("feature_code"))
    if not code or not code.startswith("PPL") or code in EXCLUDED_CODES:
        return None
    pid, name = item.get("id"), _text(item.get("name"))
    lat, lon = _number(item.get("latitude")), _number(item.get("longitude"))
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not name:
        return None
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    country, region = _text(item.get("country")), _text(item.get("admin1"))
    country_code = _text(item.get("country_code"))
    population = item.get("population")
    return {
        "id": f"geonames:{pid}",
        "provider_id": pid,
        "name": name,
        "region": region,
        "country": country,
        "country_code": country_code.upper() if country_code and re.fullmatch(r"[A-Za-z]{2}", country_code) else None,
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "population": population if isinstance(population, int) and not isinstance(population, bool)
        and population >= 0 else None,
        "timezone": _text(item.get("timezone")),
        "feature_code": code,
        "label": ", ".join(part for part in (name, region if region != name else None, country) if part),
    }


def load_popular(path: Path = POPULAR_PATH) -> list[dict]:
    """The bundled popular destinations, normalized like provider results; [] when the file is missing."""
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("places") or []
    except (OSError, ValueError, AttributeError):
        return []
    seen, places = set(), []
    for item in records:
        place = normalize_result(item)
        if place and place["id"] not in seen:
            seen.add(place["id"])
            places.append(place)
    return places


def popular_matches(places: list[dict], query: str, limit: int) -> list[dict]:
    """Places whose name starts with the query, then places with a later word that does ("Tel" -> "Tel Aviv",
    "Yo" -> "New York"); each group by population, largest first. Case and accent insensitive."""
    wanted = _fold(query)
    starts, words = [], []
    for place in places:
        name = _fold(place["name"])
        if name.startswith(wanted):
            starts.append(place)
        elif any(word.startswith(wanted) for word in re.split(r"[\s\-']+", name)[1:]):
            words.append(place)

    def rank(place: dict) -> tuple:
        return -(place["population"] or 0), _fold(place["name"])

    return (sorted(starts, key=rank) + sorted(words, key=rank))[:limit]


def provider_country_names(items: list) -> dict[str, str]:
    """The provider's own country records in an answer, as {name: ISO code}: other spellings of a country name
    for area_matches (GeoNames says "Turkey", a provider may say "Republic of Türkiye")."""
    names = {}
    for item in items:
        if isinstance(item, dict) and _text(item.get("feature_code")) in COUNTRY_CODES:
            name, code = _text(item.get("name")), _text(item.get("country_code"))
            if name and code and re.fullmatch(r"[A-Za-z]{2}", code):
                names[name] = code.upper()
    return names


class _TTLCache:
    def __init__(self, max_entries: int):
        self._data: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self._max = max_entries

    def get(self, key: tuple, now: float) -> dict | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires, value = hit
        if now >= expires:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: tuple, value: dict, expires: float) -> None:
        self._data[key] = (expires, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)


class DestinationSearch:
    """Searches the provider, with a cache and a cap on provider calls per minute. Thread-safe."""

    def __init__(self, *, client: httpx.Client | None = None, url: str | None = None,
                 timeout_s: float | None = None, max_calls_per_minute: int | None = None,
                 cache_ttl_s: float = 6 * 3600, empty_ttl_s: float = 600, cache_entries: int = 512,
                 clock: Callable[[], float] = time.monotonic, environment: str | None = None,
                 api_key: str | None = None, popular: list[dict] | None = None):
        self.environment = environment or deployment_env()
        self._key = (api_key if api_key is not None else open_meteo_key()) or None
        self.allowed, self.reason = licence_allows(self.environment)
        if self._key:  # a paid plan's key: commercial use is licensed
            self.allowed, self.reason = True, None
            protect_httpx_logs()
        self.url = url or os.getenv("RIHLA_DESTINATIONS_URL") or (CUSTOMER_URL if self._key else PROVIDER_URL)
        timeout = timeout_s if timeout_s is not None else _env_number("RIHLA_DESTINATIONS_TIMEOUT_S", 4, 0.5, 30)
        contact = (os.getenv("TRAVEL_CONTACT") or "").strip()
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "Rihla-trip-planner" + (f" ({contact})" if contact else "")})
        self._max_calls = int(max_calls_per_minute if max_calls_per_minute is not None
                              else _env_number("RIHLA_DESTINATIONS_MAX_PER_MINUTE", 60, 1, 600))
        self._ttl, self._empty_ttl = cache_ttl_s, empty_ttl_s
        self._cache = _TTLCache(cache_entries)
        self._calls: deque[float] = deque()
        self._clock = clock
        self._lock = threading.Lock()
        self._popular = popular  # loaded on the first short query when not given

    def _popular_places(self) -> list[dict]:
        if self._popular is None:
            self._popular = load_popular()
        return self._popular

    def _take_call_slot(self, now: float) -> None:
        with self._lock:
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self._max_calls:
                wait = max(1, math.ceil(60 - (now - self._calls[0])))
                raise DestinationError(429, "rate_limited", "Too many city searches right now. Please try again "
                                       "in a moment.", retryable=True, retry_after=wait)
            self._calls.append(now)

    def __repr__(self) -> str:  # never show the key
        return f"DestinationSearch(url={self.url!r}, environment={self.environment!r}, allowed={self.allowed})"

    def status(self) -> dict:
        """Whether city search can run here, without searching."""
        return {"environment": self.environment, "available": self.allowed,
                "notice": None if self.allowed else self.reason, **({"source": dict(SOURCE)} if self.allowed else {})}

    def _fetch(self, query: str, count: int, language: str) -> list:
        params = {"name": query, "count": count, "language": language, "format": "json"}
        if self._key:
            params["apikey"] = self._key
        try:
            response = self._client.get(self.url, params=params)
        except httpx.TimeoutException as exc:
            raise DestinationError(504, "provider_timeout", "The city search service did not answer in time. "
                                   "Please try again.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise DestinationError(503, "provider_unavailable", "The city search service cannot be reached "
                                   "right now. Please try again.", retryable=True) from exc
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            raise DestinationError(503, "provider_rate_limited", "The city search service is busy. Please try "
                                   "again in a minute.", retryable=True,
                                   retry_after=int(retry) if retry.isdigit() else 60)
        if response.status_code >= 500:
            raise DestinationError(502, "provider_error", "The city search service had a problem. Please try "
                                   "again.", retryable=True)
        try:
            body = response.json()
        except ValueError as exc:
            raise DestinationError(502, "provider_bad_response", "The city search service sent an unreadable "
                                   "answer.") from exc
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("error"):
            raise DestinationError(502, "provider_rejected", "The city search service could not run this search.")
        results = body.get("results", [])  # the provider leaves "results" out when nothing matches
        if not isinstance(results, list):
            raise DestinationError(502, "provider_bad_response", "The city search service sent an unexpected answer.")
        return results

    def search(self, raw_query: Any, *, limit: int = DEFAULT_LIMIT, language: str = "en") -> dict:
        if not self.allowed:  # before anything else: no input makes an unlicensed call acceptable
            raise DestinationError(503, "destinations_unavailable", "City search is not available on this service "
                                   "right now. You can still type the destination yourself.")
        query = normalize_query(raw_query)
        limit = max(1, min(MAX_LIMIT, int(limit)))
        language = language.lower() if isinstance(language, str) and re.fullmatch(r"[A-Za-z]{2}", language) else "en"
        if len(query) < PROVIDER_MIN_LENGTH:  # never sent to the provider
            return self._answer(query, popular_matches(self._popular_places(), query, limit), False, "popular")
        count = min(PROVIDER_MAX_COUNT, limit * OVERFETCH)
        # the usual spelling of a city the provider knows only under another: the answer is the provider's own place
        name, comma, rest = query.partition(",")
        lookup = SPELLINGS.get(_fold(name).strip(), name) + comma + rest
        key = (_fold(lookup), count, language)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key, now)
        if cached is None:
            self._take_call_slot(now)
            raw = self._fetch(lookup, count, language)
            seen, places = set(), []
            for item in raw:
                place = normalize_result(item)
                if place and place["id"] not in seen:
                    seen.add(place["id"])
                    places.append(place)
            cached = {"places": places, "countries": provider_country_names(raw)}
            with self._lock:
                self._cache.put(key, cached, now + (self._ttl if places else self._empty_ttl))
            from_cache = False
        else:
            from_cache = True
        return self._answer(query, cached["places"][:limit], from_cache, "worldwide",
                            self._areas(query, cached.get("countries"), cached["places"]))

    def _areas(self, query: str, provider_countries: dict | None, places: list[dict]) -> list[dict]:
        """The countries and regions the query names, each with suggested cities there: the bundled popular
        destinations in it, then the places found that lie in it (a region), largest first, at most 8."""
        areas = []
        for area in area_matches(query, provider_countries):
            def inside(place: dict) -> bool:
                return place["country_code"] == area["country_code"] and (
                    area["kind"] == "country" or _fold(place.get("region") or "") == _fold(area["name"]))
            cities, seen = [], set()
            for place in [*self._popular_places(), *places]:
                if inside(place) and place["id"] not in seen:
                    seen.add(place["id"])
                    cities.append(place)
            cities.sort(key=lambda p: (-(p["population"] or 0), _fold(p["name"])))
            areas.append({**area, "suggestions": cities[:AREA_SUGGESTIONS]})
        return areas

    @staticmethod
    def _answer(query: str, places: list[dict], cached: bool, scope: str, areas: list[dict] | None = None) -> dict:
        names: dict[str, int] = {}
        for place in places:
            names[_fold(place["name"])] = names.get(_fold(place["name"]), 0) + 1
        results = [{**place, "ambiguous_name": names[_fold(place["name"])] > 1} for place in places]
        return {
            "query": query,
            "count": len(results),
            "results": results,
            # The best match shares its name with another result: show region and country before choosing.
            "ambiguous": bool(results) and results[0]["ambiguous_name"],
            "cached": cached,
            # "popular": a one- or two-letter query, matched in the bundled list; "worldwide": the provider's search
            "scope": scope,
            # The query is the whole name of countries or regions: [{kind: "country" | "region", name, country_code,
            # country, suggestions}]. Ask which city; never pick one for the traveller.
            "areas": areas or [],
            "source": dict(SOURCE),
        }
