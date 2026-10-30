"""Data layer of the simulated Rihla travel gateway (real open data + a record/replay cache).

Upstream sources (all keyless):
  Wikivoyage MediaWiki API   listings, opening hours, prices     CC BY-SA 4.0
  Nominatim                  geocoding                           (c) OpenStreetMap contributors, ODbL
  Open-Meteo                 forecast and archive weather         CC BY 4.0
  Frankfurter                ECB reference exchange rates
  FOSSGIS OSRM servers       walking and driving times           (c) OpenStreetMap contributors, ODbL

TRAVEL_MODE
  replay  (default) answer from services/cache only; never call the internet
  live    call the upstream services (timeouts, polite rate limits); keep results in memory
  record  answer from the cache when it has the key; otherwise call the upstream and write the result
          to services/cache (delete a cache file to refresh it)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import httpx

HERE = Path(__file__).resolve().parent
CACHE_DIR = Path(os.getenv("TRAVEL_CACHE_DIR") or HERE / "cache")
OVERLAY_DIR = CACHE_DIR / "overlays"

WV_API = "https://en.wikivoyage.org/w/api.php"
WV_LICENCE = "Text from Wikivoyage, CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)"
OSM_LICENCE = "Data (c) OpenStreetMap contributors, ODbL (https://www.openstreetmap.org/copyright)"
METEO_LICENCE = "Weather data by Open-Meteo.com, CC BY 4.0"
FX_LICENCE = "Euro foreign exchange reference rates of the European Central Bank, via Frankfurter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FX_URL = "https://api.frankfurter.dev/v1/latest"
ROUTERS = {"foot": "https://routing.openstreetmap.de/routed-foot", "driving": "https://routing.openstreetmap.de/routed-car"}
JOD_PER_USD = 0.709  # Central Bank of Jordan fixed peg, used only because the ECB does not publish JOD
JOD_PEG_SOURCE = "https://www.cbj.gov.jo/"
FORECAST_DAYS = 16
MIN_INTERVAL = {"nominatim.openstreetmap.org": 1.1, "routing.openstreetmap.de": 1.0, "en.wikivoyage.org": 0.5}
MEM_TTL_S = 3 * 3600
MAX_DISTRICTS = 24  # district pages per city (Paris has 21); each is one rate-limited, cached request
MAX_DISTRICT_DEPTH = 2  # districts of districts, for hierarchical cities
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class DataError(Exception):
    """An error the API turns into {"error": {"code", "message", ...}} with a real HTTP status."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def mode() -> str:
    m = os.getenv("TRAVEL_MODE", "replay").strip().lower()
    return m if m in ("replay", "live", "record") else "replay"


def data_mode() -> str:
    return "replay" if mode() == "replay" else "live"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fold(text: str) -> str:
    """Lower-case, accent-free text for matching ('Topkapı Sarayı' -> 'topkapi sarayi')."""
    text = (text or "").replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:80] or "x"


# ------------------------------------------------------------------ cache (record / replay)
_MEM: dict[tuple[str, str], tuple[float, dict]] = {}
_LOCK = threading.Lock()


def cache_file(kind: str, key: str) -> Path:
    return CACHE_DIR / kind / f"{key}.json"


def cache_read(kind: str, key: str) -> dict | None:
    path = cache_file(kind, key)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def cache_write(kind: str, key: str, record: dict) -> None:
    path = cache_file(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def cache_glob(kind: str, pattern: str) -> list[dict]:
    folder = CACHE_DIR / kind
    if not folder.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob(pattern + ".json"))]


def not_cached(what: str, hint: str = "") -> DataError:
    return DataError(404, "not_cached", f"{what} is not in the replay cache. "
                     f"The cache covers Istanbul and Amman (see the kit README). {hint}".strip(),
                     mode="replay")


def cached(kind: str, key: str, fetch, what: str) -> tuple[dict, str]:
    """Return (record, mode). replay: cache only. record: cache, else upstream + write.
    live: upstream (kept in memory), the cache only as a fallback when the upstream fails."""
    if mode() in ("replay", "record"):
        rec = cache_read(kind, key)
        if rec is not None:
            return rec, "replay"
        if mode() == "replay":
            raise not_cached(what)
    hit = _MEM.get((kind, key))
    if hit and time.time() - hit[0] < MEM_TTL_S:
        return hit[1], "live"
    try:
        rec = fetch()
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001 - upstream failure: fall back to the cache, say so
        rec = cache_read(kind, key)
        if rec is None:
            raise DataError(503, "upstream_unavailable", f"{what}: the live source failed ({type(exc).__name__}) "
                            "and there is no cached copy.") from exc
        rec = dict(rec, stale=True, note=f"Live source failed; cached copy from {rec.get('retrieved_at')}.")
        return rec, "replay"
    _MEM[(kind, key)] = (time.time(), rec)
    if mode() == "record":
        cache_write(kind, key, rec)
    return rec, "live"


# ------------------------------------------------------------------ polite HTTP
_client: httpx.Client | None = None
_last_call: dict[str, float] = {}


def user_agent() -> str:
    contact = os.getenv("TRAVEL_CONTACT", "").strip() or "set TRAVEL_CONTACT to your email or repo URL"
    return f"RihlaTripPlannerKit/1.0 (Agentic AI Bootcamp teaching kit; low-volume cached use; contact: {contact})"


def http_get(url: str, params: dict | None = None) -> tuple[httpx.Response, str]:
    global _client
    if _client is None:
        _client = httpx.Client(headers={"User-Agent": user_agent(), "Accept": "application/json"},
                               timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True)
    host = urlparse(url).hostname or ""
    with _LOCK:
        wait = _last_call.get(host, 0) + MIN_INTERVAL.get(host, 0.2) - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
    r = _client.get(url, params=params)
    return r, str(r.url)


# ------------------------------------------------------------------ Wikivoyage listings
LISTING_RE = re.compile(r"\{\{\s*(see|do|eat|drink|buy|listing)\s*(?=\||\}\})", re.I)
MARKET_RE = re.compile(r"bazaar|market|marche|souq|souk|carsi|arasta|mercado|mercato|markt|\bpuces\b", re.I)
# A "buy" listing is a shopping destination (not just one shop) when its name says so, or its own text calls it a
# department store, a mall, a covered passage or arcade, a flea market or a shopping street or district.
SHOP_NAME_RE = re.compile(r"galerie|galleria|department store|grands? magasins?|\bmall\b|shopping|arcade|passage|"
                          r"emporium|outlet|halles|shotengai|depachika", re.I)
SHOP_TEXT_RE = re.compile(r"department store|shopping (?:mall|cent(?:er|re)|street|district|arcade|area|precinct)|"
                          r"covered (?:passage|arcade|market)|flea market|\bmall\b|grands magasins", re.I)
HEADING_RE = re.compile(r"^(={2,4})\s*(.*?)\s*\1\s*$", re.M)
TIER_RE = [("budget", re.compile(r"budget|cheap", re.I)), ("mid-range", re.compile(r"mid", re.I)),
           ("splurge", re.compile(r"splurge|expensive|upscale|fine", re.I))]
TAG_RULES = {
    "history": r"histor|ancient|byzantin|ottoman|roman|palace|fortress|castle|citadel|ruin|tomb|mausoleum|cistern|"
               r"aqueduct|obelisk|hippodrome|city walls|archaeolog|medieval|umayyad|nabat|theat(er|re)|nymphaeum|"
               r"temple|fort\b|monument|old city|caravanserai",
    "museum": r"museum|exhibit",
    "religious": r"mosque|church|cathedral|synagogue|monastery|chapel|basilica|shrine|\bcami|mevlevihane|dervish",
    "nature": r"\bpark\b|garden|forest|grove|beach|island|nature|woods|lake\b|botanic|arboretum|bird|valley|"
              r"trail|hiking|\bwadi\b|seaside|cycling",
    "views": r"\bview|panoram|tower|lookout|rooftop|observation|sunset|cable car",
    # Shopping: places to shop, not any listing on a street ("street" and "avenue" matched almost everything).
    "markets": r"bazaar|market|marche|souq|souk|shopping|department store|\bmall\b|boutiques|flea|\bpuces\b|"
               r"covered passage|arcade",
    "art": r"\bart\b|\barts\b|gallery|galleries|contemporary|painting|sculpture|creative",
    "architecture": r"architect|dome|minaret|mansion|pavilion|facade|kiosk",
    "family": r"children|kids|family|aquarium|\bzoo\b|\btoy|miniat|playground|amusement|aqua park|dolphin|vialand",
    "food": r"\bfood|cooking|culinary|tasting|gastronom|street food|food hall|\bwine\b|cheese|chocolate|"
            r"patisserie|pastr(y|ies)|bakery|\bspice",
    "entertainment": r"\bopera\b|cabaret|cinema|concert|live music|\bjazz\b|nightclub|nightlife|comedy|circus|"
                     r"musical\b|\bshows?\b|performances?|ballet|karaoke|theme park|amusement park|stadium|casino|"
                     r"music hall|puppet|kabuki|\bnoh\b|bunraku|\bfestival|\bgig\b",
    "relaxation": r"\bspa\b|hammam|turkish bath|onsen|hot spring|sauna|thermal|massage|bathhouse|"
                  r"\bgardens?\b|\bpark\b|beach|promenade|riverside|riverbank|waterfront|tea ?house|"
                  r"quiet|peaceful|tranquil|relax|cruise",
    "adventure": r"hik(e|ing)|\btrek|climb|kayak|canoe|rafting|zip ?line|paraglid|bungee|scuba|diving|snorkel|"
                 r"\bsurf|cycling|bike tour|bicycle|horse riding|camel ride|\bquad\b|\batv\b|jeep|safari|caving|"
                 r"via ferrata|sailing|\bski(ing)?\b|snowboard|escape room|rope park|adventure",
}
# A theatre is entertainment unless the listing is about an ancient one (then it is history).
_ANCIENT_THEATRE = re.compile(r"ancient|roman|greek|amphitheat|ruin|odeon|hellenistic", re.I)
_THEATRE = re.compile(r"theat(er|re)", re.I)
INTEREST_SYNONYMS = {
    "historical": "history", "historic": "history", "heritage": "history", "historical sites": "history",
    "museums": "museum", "nature": "nature", "parks": "nature", "outdoors": "nature", "gardens": "nature",
    "religion": "religious", "mosques": "religious", "churches": "religious", "shopping": "markets",
    "bazaars": "markets", "market": "markets", "galleries": "art", "views": "views", "viewpoints": "views",
    "kids": "family", "children": "family", "family": "family", "architecture": "architecture", "food": "food",
    "cuisine": "food", "entertainment": "entertainment", "nightlife": "entertainment", "shows": "entertainment",
    "relaxation": "relaxation", "relax": "relaxation", "spa": "relaxation", "wellness": "relaxation",
    "adventure": "adventure", "outdoor activities": "adventure",
}
DIET_RE = re.compile(r"vegetarian|vegan|halal|gluten|kosher|vegetable|meze|mezze|falafel|hummus", re.I)


def _template_end(text: str, start: int) -> int:
    depth, i, n = 0, start, len(text)
    while i < n:
        if text.startswith("{{", i):
            depth += 1
            i += 2
        elif text.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return n


def _split_params(body: str) -> dict:
    parts, cur, depth_t, depth_l, i = [], [], 0, 0, 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "}}", "[[", "]]"):
            depth_t += {"{{": 1, "}}": -1}.get(two, 0)
            depth_l += {"[[": 1, "]]": -1}.get(two, 0)
            cur.append(two)
            i += 2
            continue
        if body[i] == "|" and depth_t == 0 and depth_l == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(body[i])
        i += 1
    parts.append("".join(cur))
    out = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def clean_wiki(text: str) -> str:
    t = text or ""
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S | re.I)
    t = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", t, flags=re.I)
    t = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[(?:https?:)?//\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"\[(?:https?:)?//\S+\]", "", t)
    for _ in range(3):  # simple inline templates: keep their last parameter ({{nowrap|x}} -> x)
        t = re.sub(r"\{\{\s*(?:marker|station|metro|rint)[^{}]*\}\}", "", t, flags=re.I)
        t = re.sub(r"\{\{[^{}|]*\|([^{}]*)\}\}", lambda m: m.group(1).split("|")[-1], t)
        t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&nbsp;", " ").replace("&ndash;", "-").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def _num(text: str):
    try:
        f = float(text)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def wv_page_url(title: str, anchor: str = "") -> str:
    url = "https://en.wikivoyage.org/wiki/" + quote(title.replace(" ", "_"), safe="/_(),'")
    return url + ("#" + quote(anchor.replace(" ", "_"), safe="_") if anchor else "")


def tags_for(text: str, kind: str, name: str | None = None) -> list[str]:
    """Interest tags from a listing's own text. With `name`: a place that is not a "buy" listing is shopping only
    when its name says so (a market, a passage, a mall), never because its description mentions a market."""
    low = fold(text)
    tags = [tag for tag, rx in TAG_RULES.items() if re.search(rx, low)]
    if name is not None and kind != "buy" and "markets" in tags and not (
            re.search(TAG_RULES["markets"], fold(name)) or SHOP_NAME_RE.search(fold(name))):
        tags.remove("markets")
    if _THEATRE.search(low) and not _ANCIENT_THEATRE.search(low) and "entertainment" not in tags:
        tags.append("entertainment")
    if kind in ("eat", "drink"):
        tags = [t for t in tags if t in ("views", "family")] + ["food"]
    if kind == "buy" and "markets" not in tags:
        tags.append("markets")
    return tags


def parse_listings(title: str, wikitext: str, city_slug: str) -> list[dict]:
    """All see/do/eat/drink listings of one page (plus bazaars and markets from 'buy'), with section and price tier."""
    heads = [(m.start(), len(m.group(1)), clean_wiki(m.group(2))) for m in HEADING_RE.finditer(wikitext)]
    district = title.split("/", 1)[1] if "/" in title else None
    out = []
    for m in LISTING_RE.finditer(wikitext):
        end = _template_end(wikitext, m.start())
        params = _split_params(wikitext[m.end():end - 2])
        section = sub = ""
        for pos, level, name in heads:
            if pos > m.start():
                break
            if level == 2:
                section, sub = name, ""
            elif level == 3:
                sub = name
        kind = m.group(1).lower()
        if kind == "listing":
            kind = (params.get("type") or section).strip().lower()
        if kind not in ("see", "do", "eat", "drink", "buy"):
            continue
        name = clean_wiki(params.get("name", ""))
        if not name:
            continue
        content = clean_wiki(params.get("content", ""))
        alt = clean_wiki(params.get("alt", ""))
        if kind == "buy" and not (MARKET_RE.search(fold(f"{name} {alt}")) or SHOP_NAME_RE.search(fold(f"{name} {alt}"))
                                  or SHOP_TEXT_RE.search(fold(content))):
            continue  # a single shop is not an attraction; bazaars, markets and shopping destinations are
        tier = None
        if kind in ("eat", "drink"):
            tier = next((t for t, rx in TIER_RE if rx.search(sub)), None)
        lat, lon = _num(params.get("lat")), _num(params.get("long"))
        hours_raw = clean_wiki(params.get("hours", ""))
        price_raw = clean_wiki(params.get("price", ""))
        listing = {
            "id": f"wv-{city_slug}-{slug(name)}",
            "kind": kind,
            "name": name,
            "alt": alt or None,
            "district": district,
            "section": section + (f" / {sub}" if sub else ""),
            "price_tier": tier,
            "address": clean_wiki(params.get("address", "")) or None,
            "lat": lat if isinstance(lat, (int, float)) else None,
            "lon": lon if isinstance(lon, (int, float)) else None,
            "url": (params.get("url") or "").strip() or None,
            "hours_raw": hours_raw or None,
            "price_raw": price_raw or None,
            "last_edit": (params.get("lastedit") or "").strip() or None,
            "wikidata": (params.get("wikidata") or "").strip() or None,
            "description": content,
            "tags": tags_for(f"{name} {alt} {content}", kind, f"{name} {alt}"),
            "dietary_mentions": sorted({w.lower() for w in DIET_RE.findall(content)}) if kind in ("eat", "drink") else [],
            "source_url": wv_page_url(title, section or ""),
        }
        out.append(listing)
    return out


REGIONLIST_RE = re.compile(r"\{\{\s*regionlist\b", re.I)
REGION_FIELD_RE = re.compile(r"region\d+(?:name|items)$", re.I)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _link_target(raw: str) -> str | None:
    target = " ".join(raw.replace("_", " ").split())
    if not target or ":" in target:  # File:, Category:, w: and other namespaces are not district pages
        return None
    return target[:1].upper() + target[1:]


def parse_districts(title: str, wikitext: str) -> list[str]:
    """District pages of a city page, in the page's own order.

    1. Every link in the Regionlist's regionNname and regionNitems fields. Big cities group districts under a
       plain-text name and list the district links in "items" (Paris: region1name=Central Paris,
       region1items=[[Paris/1st arrondissement|1st arr.]], ...); smaller ones link the name itself.
    2. Then subpage links ([[City/...]]) anywhere on the page, for cities without a Regionlist.
    """
    out: list[str] = []
    for m in REGIONLIST_RE.finditer(wikitext):
        body = wikitext[m.end():_template_end(wikitext, m.start()) - 2]
        for key, value in _split_params(body).items():
            if REGION_FIELD_RE.match(key.strip()):
                out += [t for t in map(_link_target, WIKILINK_RE.findall(value)) if t]
    prefix = fold(title) + "/"
    out += [t for t in map(_link_target, WIKILINK_RE.findall(wikitext)) if t and fold(t).startswith(prefix)]
    seen, districts = {fold(title)}, []
    for t in out:
        if fold(t) not in seen:
            seen.add(fold(t))
            districts.append(t)
    return districts


def page_record(title: str, wikitext: str, retrieved_at: str, revid=None, requested: str | None = None) -> dict:
    city_slug = slug(title.split("/")[0])
    return {
        "kind": "wikivoyage_page", "title": title, "requested_title": requested or title,
        "source": "Wikivoyage", "source_url": wv_page_url(title), "licence": WV_LICENCE,
        "retrieved_at": retrieved_at, "revid": revid,
        "districts": parse_districts(title, wikitext),
        "listings": parse_listings(title, wikitext, city_slug),
    }


def wv_page(title: str) -> tuple[dict, str]:
    def fetch() -> dict:
        r, _ = http_get(WV_API, {"action": "parse", "page": title, "prop": "wikitext|revid", "format": "json",
                                 "formatversion": 2, "redirects": 1})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise DataError(404, "page_not_found", f"Wikivoyage has no page '{title}'.")
        p = data["parse"]
        return page_record(p["title"], p["wikitext"], now_iso(), p.get("revid"), requested=title)
    return cached("wikivoyage", slug(title), fetch, f"Wikivoyage page '{title}'")


def city_title(city: str) -> str:
    name = (city or "").split(",")[0].strip()
    if not name:
        raise DataError(422, "missing_city", "Give a city, e.g. city=Istanbul.")
    known = {fold(c["name"]): c["name"] for c in manifest().get("cities", [])}
    return known.get(fold(name), name[:1].upper() + name[1:])


def _district_pages(page: dict) -> list[dict]:
    """The city's district pages, breadth first: its districts, then theirs (a hierarchical city such as
    London -> London/Central -> ...), at most MAX_DISTRICT_DEPTH levels and MAX_DISTRICTS pages."""
    queue = [(d, 1) for d in page.get("districts", [])]
    seen, pages = {fold(page["title"])}, []
    while queue and len(pages) < MAX_DISTRICTS:
        title, depth = queue.pop(0)
        if fold(title) in seen:
            continue
        seen.add(fold(title))
        try:
            district = wv_page(title)[0]
        except DataError as exc:
            if exc.code not in ("not_cached", "page_not_found", "upstream_unavailable"):
                raise
            continue
        seen.add(fold(district["title"]))  # a redirect lands on its target's title
        pages.append(district)
        if depth < MAX_DISTRICT_DEPTH:
            queue += [(d, depth + 1) for d in district.get("districts", [])]
    return pages


def _same_place(a: dict, b: dict) -> bool:
    """The same listing on two pages: a city page summarises places its district pages list in full."""
    if fold(a["name"]) != fold(b["name"]):
        return False
    if None not in (a.get("lat"), b.get("lat")):
        return abs(a["lat"] - b["lat"]) < 0.005 and abs(a["lon"] - b["lon"]) < 0.005  # ~0.5 km
    return a.get("district") is None or b.get("district") is None  # the city page's copy of a district listing


GENERIC_NAME_WORDS = {"musee", "museum", "palais", "palace", "eglise", "church", "place", "parc", "park", "jardin",
                      "garden", "le", "la", "les", "du", "de", "des", "the", "of", "saint", "st", "rue", "street"}


def _city_copy_of(item: dict, kept: list[dict]) -> bool:
    """A city-page listing that summarises a district listing under another name ("The Louvre" for "Musee du
    Louvre"): at the same spot (about 60 m) and sharing a distinctive word of the name."""
    if item.get("district") is not None or item.get("lat") is None:
        return False
    words = {w for w in re.findall(r"[a-z0-9]+", fold(item["name"])) if len(w) > 3} - GENERIC_NAME_WORDS
    for other in kept:
        if other.get("district") is None or other.get("lat") is None or other["kind"] != item["kind"]:
            continue
        near = abs(other["lat"] - item["lat"]) < 0.0006 and abs(other["lon"] - item["lon"]) < 0.0008
        if near and words & set(re.findall(r"[a-z0-9]+", fold(other["name"]))):
            return True
    return False


def city_listings(city: str) -> tuple[list[dict], dict]:
    """Listings of a city page plus its district pages (districts first, in the page's own order)."""
    title = city_title(city)
    page, m = wv_page(title)
    pages = _district_pages(page)
    pages.append(page)
    seen, listings = set(), []
    by_name: dict[str, list[dict]] = {}
    for p in pages:
        for item in p["listings"]:
            if any(_same_place(item, other) for other in by_name.get(fold(item["name"]), [])):
                continue  # keep the first copy: district pages come first and carry the full listing
            if _city_copy_of(item, listings):
                continue  # the city page's summary of a district listing, under another name
            by_name.setdefault(fold(item["name"]), []).append(item)
            uid = item["id"]
            if uid in seen:  # same name in two districts: keep both, make the id unique
                uid = f"{uid}-{slug(item.get('district') or 'city')}"
            n = 2
            while uid in seen:
                uid, n = f"{item['id']}-{n}", n + 1
            seen.add(uid)
            # Tags are derived again from the listing's own text, so recorded pages use the current rules too.
            tags = tags_for(f"{item['name']} {item.get('alt') or ''} {item.get('description') or ''}", item["kind"],
                            f"{item['name']} {item.get('alt') or ''}")
            listings.append(dict(item, id=uid, city=page["title"], tags=tags))
    meta = {"city": page["title"], "mode": m, "retrieved_at": page["retrieved_at"],
            "pages": [p["source_url"] for p in pages], "source": "Wikivoyage", "licence": WV_LICENCE}
    return listings, meta


# ------------------------------------------------------------------ hours and prices (free text -> structure)
DAY_WORDS = {"mo": 0, "mon": 0, "monday": 0, "tu": 1, "tue": 1, "tues": 1, "tuesday": 1, "we": 2, "wed": 2,
             "wednesday": 2, "th": 3, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fr": 4, "fri": 4,
             "friday": 4, "sa": 5, "sat": 5, "saturday": 5, "su": 6, "sun": 6, "sunday": 6}
DAY_TOKEN = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|tu|wed(?:nesday)?|we|thu(?:r(?:s(?:day)?)?)?|th|fri(?:day)?|fr|sat(?:urday)?|sa|sun(?:day)?|su|mo)s?"
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
AMPM = r"(am|pm|a\.m\.|p\.m\.)?"
TIME_RANGE = re.compile(rf"(\d{{1,2}})(?:[:.](\d{{2}}))?\s*{AMPM}\s*(?:-|to|till|until)\s*(\d{{1,2}})(?:[:.](\d{{2}}))?\s*{AMPM}", re.I)


def _hhmm(h: str, m: str | None, ampm: str | None) -> str:
    hour = int(h)
    ampm = (ampm or "").replace(".", "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{int(m or 0):02d}"


def _day_index(token: str) -> int | None:
    t = token.lower().rstrip(".")
    if t.endswith("s") and t[:-1] in DAY_WORDS:
        t = t[:-1]
    return DAY_WORDS.get(t)


def _days_in(spec: str) -> set[int]:
    spec = spec.lower()
    if re.search(r"daily|every ?day|7 days|all week", spec):
        return set(range(7))
    days: set[int] = set()
    for a, b in re.findall(rf"\b({DAY_TOKEN})\.?\s*-\s*({DAY_TOKEN})\b", spec):
        i, j = _day_index(a), _day_index(b)
        if i is None or j is None:
            continue
        k = i
        while True:
            days.add(k)
            if k == j:
                break
            k = (k + 1) % 7
    spec = re.sub(rf"\b({DAY_TOKEN})\.?\s*-\s*({DAY_TOKEN})\b", " ", spec)
    for tok in re.findall(rf"\b({DAY_TOKEN})\b", spec):
        i = _day_index(tok)
        if i is not None:
            days.add(i)
    return days


def parse_hours(raw: str | None) -> dict | None:
    """Turn a free-text Wikivoyage 'hours' field into closed days and daily times (best effort)."""
    if not raw:
        return None
    text = raw.replace("–", "-").replace("—", "-")
    low = text.lower()
    out = {"raw": raw, "parsed": False, "days_stated": False, "open_days": [], "closed_days": [],
           "weekly": {}, "seasonal": [], "open_24h": False, "temporarily_closed": False}
    if re.search(r"temporar\w* closed|closed for (restoration|renovation)|currently closed", low):
        out.update(parsed=True, temporarily_closed=True, closed_days=DAYS[:], note="The listing says it is closed.")
        return out
    if re.search(r"24 ?hours|24/7|open all day", low):
        out.update(parsed=True, open_24h=True, open_days=DAYS[:], weekly={d: ["00:00-24:00"] for d in DAYS})
        return out
    explicit_closed: set[int] = set()
    day_list = rf"(\b(?:{DAY_TOKEN}\.?(?:\s*(?:,|and|&|-)\s*)?)+)"
    for m in re.finditer(rf"(?:closed|except)\s*(?:on\s*)?:?\s*{day_list}", low):
        explicit_closed |= _days_in(m.group(1))
    for m in re.finditer(rf"{day_list}\s*(?:day\s+)?(?:off|closed)\b", low):
        explicit_closed |= _days_in(m.group(1))
    weekly: dict[int, list[str]] = {}
    prev_days: set[int] = set()
    pending: set[int] = set()
    for seg in re.split(r"[;,]|(?<!a\.m)(?<!p\.m)\.\s", text):
        seg_low = seg.lower()
        found = list(TIME_RANGE.finditer(seg))
        times = [m.groups() for m in found]
        month = re.match(rf"\s*(({MONTHS})\w*\s*-\s*({MONTHS})\w*)", seg_low)
        if month and times:
            out["seasonal"].append({"months": month.group(1).title(),
                                    "hours": [f"{_hhmm(a, b, c)}-{_hhmm(d, e, f)}" for a, b, c, d, e, f in times]})
            continue
        strip = lambda s: re.sub(r"(closed|except)\b.*|\b(off)\b.*", "", s.lower())  # noqa: E731
        if not found:
            spec_days = _days_in(strip(seg))
            until = re.search(r"(?:to|till|until)\s*(\d{1,2})[:.](\d{2})", seg_low)
            if spec_days and until:  # "Fri to 20:00": open that day, closing time only
                out["days_stated"] = True
                for day in spec_days | pending:
                    weekly[day] = [f"?-{_hhmm(until.group(1), until.group(2), None)}"]
                pending = set()
            elif spec_days and not re.search(r"closed|except|\boff\b", seg_low):
                pending |= spec_days  # "Tue, Thu & Sat 10:00-12:30": days split by a comma
            continue
        spec_days = _days_in(strip(seg[:found[0].start()])) or _days_in(strip(seg[found[-1].end():]))
        spec_days |= pending
        pending = set()
        if spec_days:
            out["days_stated"] = True
            days = spec_days
            for day in days:  # a later, more specific segment replaces earlier times for that day
                weekly[day] = []
        else:
            days = prev_days or set(range(7))
        prev_days = days
        for a, b, c, d, e, f in times:
            rng = f"{_hhmm(a, b, c)}-{_hhmm(d, e, f)}"
            for day in days:
                weekly.setdefault(day, [])
                if rng not in weekly[day]:
                    weekly[day].append(rng)
    if out["seasonal"] and not weekly:
        out["parsed"] = True
        out["open_days"] = DAYS[:]
        out["closed_days"] = [DAYS[d] for d in sorted(explicit_closed)]
        out["note"] = "Seasonal hours; no closing day is stated."
        if explicit_closed:
            out["open_days"] = [DAYS[d] for d in range(7) if d not in explicit_closed]
        return out
    if not weekly and not explicit_closed:
        out["note"] = "Could not parse the hours; read the raw text."
        return out
    open_days = set(weekly) if out["days_stated"] else set(range(7))
    open_days -= explicit_closed
    out["parsed"] = True
    out["days_stated"] = out["days_stated"] or bool(explicit_closed)
    out["open_days"] = [DAYS[d] for d in sorted(open_days)]
    out["closed_days"] = [DAYS[d] for d in range(7) if d not in open_days]
    out["weekly"] = {DAYS[d]: weekly[d] for d in sorted(weekly) if d in open_days}
    if not out["days_stated"]:
        out["note"] = "No days are stated in the listing; times may apply every day."
    return out


CURRENCY_TOKENS = [("EUR", r"€|eur(?:os?)?\b"), ("USD", r"us\$|usd\b|\$|dollars?\b"),
                   ("TRY", r"₺|\btl\b|\btry\b|lira\b|tl(?=\W|$)"), ("JOD", r"\bjod\b|\bjd\b|dinars?\b"),
                   ("GBP", r"£|gbp\b")]
NUM = r"(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)"


def _amount(text: str) -> float | None:
    t = text
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", t):
        t = re.sub(r"[,.]", "", t)
    return _num(t)


def parse_price(raw: str | None) -> dict | None:
    """Turn a free-text price into amounts per currency (best effort; the raw text stays authoritative)."""
    if not raw:
        return None
    low = raw.lower().replace("–", "-")
    found: dict[str, list] = {}
    order: list[str] = []
    for code, cur in CURRENCY_TOKENS:
        pats = [rf"(?:{cur})\s?{NUM}(?:\s?-\s?(?:{cur})?\s?{NUM})?", rf"{NUM}(?:\s?-\s?{NUM})?\s?(?:{cur})"]
        for pat in pats:
            for m in re.finditer(pat, low):
                nums = [_amount(g) for g in m.groups() if g]
                nums = [n for n in nums if n is not None]
                if nums:
                    found.setdefault(code, [])
                    found[code] += [n for n in nums if n not in found[code]]
                    order.append((m.start(), code))
    order_codes = [c for _, c in sorted(order)]
    currency = order_codes[0] if order_codes else None
    years = [a or b for a, b in re.findall(rf"(?:(?:{MONTHS})[a-z]*\.?\s+|\b(?:in|as of|since)\s+)(20[0-3]\d)\b|\((20[0-3]\d)\)", low)]
    free = bool(re.match(r"\s*free\b", low)) and not found
    out = {"raw": raw, "parsed": bool(found) or free, "free": free, "mentions_free": "free" in low,
           "currency": currency, "amounts": {k: sorted(v) for k, v in found.items()},
           "min": min(found[currency]) if currency else (0 if free else None),
           "max": max(found[currency]) if currency else (0 if free else None),
           "as_of_year": int(years[-1]) if years else None}
    if not out["parsed"]:
        out["note"] = "No amount found; read the raw text."
    return out


def enrich(listing: dict, overlays: dict | None = None) -> dict:
    """Public shape of a listing: structured hours/price, citation, overlays applied."""
    item = {k: v for k, v in listing.items() if k not in ("hours_raw", "price_raw", "hours_parsed", "price_parsed")}
    # A provider with structured data (Google Places) gives hours and prices already in the parsed shape.
    item["hours"] = listing["hours_parsed"] if "hours_parsed" in listing else parse_hours(listing.get("hours_raw"))
    price = listing["price_parsed"] if "price_parsed" in listing else parse_price(listing.get("price_raw"))
    item["price"] = price
    source = listing.get("source") or "Wikivoyage"
    item["citation"] = {"id": listing["id"], "title": f"{listing['name']} - {source} ({listing.get('district') or listing.get('city')})",
                        "url": listing["source_url"]}
    item["synthetic"] = bool(listing.get("synthetic"))
    for patch in (overlays or {}).get("patches", []):
        if patch.get("id") == listing["id"] and patch.get("extra_price_sources"):
            sources = [{"source": "Wikivoyage", "price": price, "source_url": listing["source_url"], "synthetic": False}]
            for extra in patch["extra_price_sources"]:
                sources.append({"source": extra["source"], "price": parse_price(extra["price_raw"]),
                                "source_url": extra.get("source_url"), "synthetic": True,
                                "overlay": patch.get("overlay")})
            item["price_sources"] = sources
            item["price_conflict"] = True
            item["note"] = "SYNTHETIC TEST DATA: a second price source disagrees with Wikivoyage (overlay)."
    return item


# ------------------------------------------------------------------ overlays (synthetic, opt-in)
def load_overlays(names: list[str]) -> dict:
    merged = {"names": [], "patches": [], "add_listings": []}
    for name in names:
        path = OVERLAY_DIR / f"{name}.json"
        if not path.is_file():
            raise DataError(404, "overlay_not_found", f"No overlay '{name}'. Available: {available_overlays()}")
        data = json.loads(path.read_text(encoding="utf-8"))
        merged["names"].append(name)
        for p in data.get("patches", []):
            merged["patches"].append(dict(p, overlay=name))
        for a in data.get("add_listings", []):
            merged["add_listings"].append(dict(a, overlay=name))
    return merged


def available_overlays() -> list[str]:
    return sorted(p.stem for p in OVERLAY_DIR.glob("*.json")) if OVERLAY_DIR.is_dir() else []


def with_overlays(listings: list[dict], city: str, overlays: dict) -> list[dict]:
    out = list(listings)
    for add in overlays.get("add_listings", []):
        if slug(add["city"]) == slug(city):
            item = dict(add["listing"], city=city, synthetic=True, overlay=add["overlay"])
            out.insert(min(int(add.get("position", 0)), len(out)), item)
    return out


# ------------------------------------------------------------------ search helpers
def interest_tags(interests: str | None) -> list[str]:
    if not interests:
        return []
    out = []
    for word in re.split(r"[,;/]| and ", interests.lower()):
        w = word.strip()
        if w:
            out.append(INTEREST_SYNONYMS.get(w, INTEREST_SYNONYMS.get(w.rstrip("s"), w)))
    return out


def matches_interest(item: dict, wanted: list[str]) -> bool:
    return interest_score(item, wanted) > 0 or not wanted


KIND_FOR_INTEREST = {"markets": {"buy"}, "adventure": {"do"}, "entertainment": {"do", "drink"}}


def _matches_one(item: dict, w: str) -> bool:
    """One wanted interest: a tag of the listing, or (for words with no tag rule) in its own text."""
    if w in item.get("tags", []):
        return True
    if w in TAG_RULES:  # a tagged interest is matched by its rule only: "art" must not match "start" or "party"
        return False
    return fold(w) in fold(f"{item['name']} {item.get('alt') or ''} {item.get('description') or ''}")


def interest_score(item: dict, wanted: list[str]) -> int:
    """How many of the wanted interests a listing matches (its tags or its own text): 0 = none."""
    return sum(_matches_one(item, w) for w in wanted)


def distribute(items: list[dict], wanted: list[str]) -> list[dict]:
    """Order listings by relevance, then spread them over the city's districts.

    Only facts from the listings: how many interests a place matches, and whether it can be mapped. Within the
    same relevance, the districts take turns (each district's best first, in page order), so one district does
    not fill the first page of results. No ratings or popularity are invented.

    With two or more tagged interests, the interests then take turns: each picks its next most relevant place
    not taken yet. Ranking by the number of matches alone let places matching several interests (a history and
    art museum) fill every slot, so a traveller who asked for history, shopping and art got only museums.
    """
    def own_key(item):
        return -interest_score(item, wanted), item.get("lat") is None

    districts: dict = {}
    for item in items:
        districts.setdefault(item.get("district"), []).append(item)
    keyed = []
    for d_order, members in enumerate(districts.values()):
        for turn, item in enumerate(sorted(members, key=own_key)):  # stable: page order within a district
            keyed.append((own_key(item), turn, d_order, item))
    ranked = [item for *_, item in sorted(keyed, key=lambda k: k[:3])]
    tagged = [w for w in dict.fromkeys(wanted) if w in TAG_RULES]
    if len(tagged) < 2:
        return ranked
    # Within an interest, listings of the kind that interest is about come first (a shop for shopping, an
    # activity for adventure): a museum whose text mentions a market is not a shopping trip.
    queues = {w: sorted((i for i in ranked if _matches_one(i, w)),
                        key=lambda i: i.get("kind") not in KIND_FOR_INTEREST.get(w, {i.get("kind")}))
              for w in tagged}
    out, taken = [], set()
    while any(queues.values()):
        for w in tagged:
            queue = queues[w]
            while queue and id(queue[0]) in taken:
                queue.pop(0)
            if queue:
                taken.add(id(queue[0]))
                out.append(queue.pop(0))
    return out + [i for i in ranked if id(i) not in taken]


STOPWORDS = {"the", "of", "and", "in", "at", "a", "an", "de", "di", "el", "al", "to"}


def _tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", fold(text)):
        if t in STOPWORDS:
            continue
        t = {"theatre": "theater", "centre": "center", "mosques": "mosque"}.get(t, t)
        out.add(t[:-1] if len(t) > 4 and t.endswith("s") else t)
    return out


def find_listing(listings: list[dict], name: str) -> tuple[dict | None, list[str]]:
    """Best listing for a free-text name: exact, then substring, then a strict token match. Ambiguous -> None."""
    q = re.sub(r",.*$", "", fold(name)).strip() or fold(name)
    if len(q) < 2:
        return None, []
    labels = [(l, fold(lab)) for l in listings for lab in (l["name"], l.get("alt")) if lab]
    for l, lab in labels:
        if q == lab:
            return l, []
    for l, lab in labels:
        if len(q) >= 4 and q in lab:
            return l, []
    for l, lab in labels:
        if len(lab) > 4 and re.search(rf"\b{re.escape(lab)}\b", q):
            return l, []
    qt = _tokens(q)
    scored: dict[str, tuple[float, dict]] = {}
    for l, lab in labels:
        ct = _tokens(lab)
        if not ct or not qt:
            continue
        subset = ct <= qt and (len(ct) >= 2 or len(next(iter(ct))) >= 5)
        score = 1.0 if subset else len(qt & ct) / len(qt | ct)
        if score >= 0.6 and score > scored.get(l["id"], (0, None))[0]:
            scored[l["id"]] = (score, l)
    if scored:
        ranked = sorted(scored.values(), key=lambda x: -x[0])
        top = [l for s, l in ranked if s == ranked[0][0]]
        if len(top) == 1:
            return top[0], []
        return None, [l["name"] for l in top[:5]]
    hints = [l["name"] for l in listings if {t for t in qt if len(t) >= 4} & _tokens(l["name"] + " " + (l.get("alt") or ""))]
    return None, hints[:5]


# ------------------------------------------------------------------ geocoding
def geocode(query: str) -> tuple[dict, str]:
    q = (query or "").strip()
    if not q:
        raise DataError(422, "missing_query", "Give a place to geocode, e.g. q=Galata Tower, Istanbul.")

    def fetch() -> dict:
        r, url = http_get(NOMINATIM, {"q": q, "format": "jsonv2", "limit": 1, "accept-language": "en"})
        r.raise_for_status()
        hits = r.json()
        if not hits:
            raise DataError(404, "place_not_found", f"Nominatim found nothing for '{q}'.")
        h = hits[0]
        return {"kind": "geocode", "query": q, "name": h.get("name") or q, "display_name": h.get("display_name"),
                "lat": float(h["lat"]), "lon": float(h["lon"]), "osm_type": h.get("osm_type"),
                "osm_id": h.get("osm_id"), "category": h.get("category"), "type": h.get("type"),
                "source": "Nominatim (OpenStreetMap)", "source_url": url.replace(" ", "+"),
                "licence": OSM_LICENCE, "retrieved_at": now_iso()}
    return cached("geocode", slug(q), fetch, f"Geocoding '{q}'")


def resolve_place(place: str, city: str, listings: list[dict] | None = None) -> dict:
    """A place name -> coordinates: a 'lat,lon' pair, a Wikivoyage listing of the city, or Nominatim."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", place or "")
    if m:
        return {"name": place.strip(), "lat": float(m.group(1)), "lon": float(m.group(2)), "resolved_by": "coordinates"}
    if listings is None:
        try:
            listings = city_listings(city)[0]
        except DataError:
            listings = []
    item, _ = find_listing([l for l in listings if l.get("lat") is not None], place)
    if item:
        return {"name": item["name"], "id": item["id"], "lat": item["lat"], "lon": item["lon"],
                "resolved_by": f"{(item.get('source') or 'Wikivoyage').lower()} listing", "source_url": item["source_url"]}
    query = place if fold(city) in fold(place) else f"{place}, {city}"
    rec, _ = geocode(query)
    return {"name": rec.get("name") or place, "lat": rec["lat"], "lon": rec["lon"], "resolved_by": "nominatim",
            "source_url": rec["source_url"]}


# ------------------------------------------------------------------ routing
def route_profile(m: str) -> str:
    m = (m or "foot").lower()
    if m in ("foot", "walk", "walking", "on foot"):
        return "foot"
    if m in ("driving", "drive", "car", "taxi"):
        return "driving"
    raise DataError(422, "bad_mode", "mode must be 'foot' or 'driving'.")


def _route_url(profile: str, a: dict, b: dict) -> str:
    return f"{ROUTERS[profile]}/route/v1/driving/{a['lon']:.5f},{a['lat']:.5f};{b['lon']:.5f},{b['lat']:.5f}?overview=false"


def _same(p: dict, q: dict) -> bool:
    if p.get("id") and q.get("id"):
        return p["id"] == q["id"]
    return abs(p["lat"] - q["lat"]) < 2e-5 and abs(p["lon"] - q["lon"]) < 2e-5


def route_from_tables(city: str, profile: str, a: dict, b: dict) -> dict | None:
    for table in cache_glob("route", f"table__{slug(city)}__{profile}"):
        pts = table["points"]
        ia = next((i for i, p in enumerate(pts) if _same(p, a)), None)
        ib = next((i for i, p in enumerate(pts) if _same(p, b)), None)
        if ia is not None and ib is not None and table["durations"][ia][ib] is not None:
            return {"distance_m": table["distances"][ia][ib], "duration_s": table["durations"][ia][ib],
                    "retrieved_at": table["retrieved_at"], "via": "cached duration matrix (OSRM table service)"}
    return None


def route(a: dict, b: dict, city: str, profile: str) -> tuple[dict, str]:
    hit = route_from_tables(city, profile, a, b)
    if hit:
        return hit, "replay"
    key = f"{profile}__{a['lat']:.5f},{a['lon']:.5f}__{b['lat']:.5f},{b['lon']:.5f}"

    def fetch() -> dict:
        r, url = http_get(_route_url(profile, a, b))
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise DataError(404, "no_route", f"No {profile} route found.")
        rt = data["routes"][0]
        return {"kind": "route", "profile": profile, "from": a, "to": b, "distance_m": round(rt["distance"], 1),
                "duration_s": round(rt["duration"], 1), "retrieved_at": now_iso(), "via": "OSRM route service"}
    if mode() == "replay":
        rec = cache_read("route", key)
        if rec is None and profile == "foot":
            back = cache_read("route", f"{profile}__{b['lat']:.5f},{b['lon']:.5f}__{a['lat']:.5f},{a['lon']:.5f}")
            if back:
                return dict(back, via="cached route in the opposite direction (walking)"), "replay"
        if rec is None:
            raise not_cached(f"A {profile} route from '{a['name']}' to '{b['name']}'",
                             "Cached routes connect the top attractions and restaurants of each city.")
        return rec, "replay"
    return cached("route", key, fetch, f"Route {a['name']} -> {b['name']}")


def record_table(city: str, profile: str, points: list[dict]) -> dict:
    """Record mode helper: OSRM table requests for many points (much lighter than N*N route calls).
    The server allows sources x destinations <= 100 x 100 per request, so sources are sent in chunks."""
    coords = ";".join(f"{p['lon']:.5f},{p['lat']:.5f}" for p in points)
    n, durations, distances, url = len(points), [], [], ""
    chunk = max(1, 10000 // n)
    for start in range(0, n, chunk):
        sources = ";".join(str(i) for i in range(start, min(n, start + chunk)))
        r, url = http_get(f"{ROUTERS[profile]}/table/v1/driving/{coords}",
                          {"annotations": "duration,distance", "sources": sources})
        r.raise_for_status()
        data = r.json()
        durations += data["durations"]
        distances += data["distances"]
    rnd = lambda rows: [[None if v is None else round(v) for v in row] for row in rows]  # noqa: E731
    rec = {"kind": "route_table", "city": city, "profile": profile,
           "points": [{k: p.get(k) for k in ("id", "name", "lat", "lon")} for p in points],
           "durations": rnd(durations), "distances": rnd(distances),
           "source": f"FOSSGIS OSRM server ({profile} profile)", "source_url": f"{ROUTERS[profile]}/table/v1/driving/",
           "licence": OSM_LICENCE, "retrieved_at": now_iso()}
    cache_write("route", f"table__{slug(city)}__{profile}", rec)
    return rec


# ------------------------------------------------------------------ weather
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
       51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers",
       85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail",
       99: "thunderstorm with hail"}


def travel_today() -> dt.date:
    env = os.getenv("TRAVEL_TODAY", "").strip()
    if env:
        return dt.date.fromisoformat(env)
    if mode() == "replay" and manifest().get("recorded_today"):
        return dt.date.fromisoformat(manifest()["recorded_today"])
    return dt.date.today()


def city_point(city: str) -> dict:
    rec, _ = geocode(city_title(city))
    return {"name": city_title(city), "lat": round(rec["lat"], 4), "lon": round(rec["lon"], 4)}


def forecast(city: str) -> tuple[dict, str]:
    pt = city_point(city)
    today = travel_today()
    if mode() == "replay":
        recs = cache_glob("weather", f"forecast__{slug(pt['name'])}__*")
        if not recs:
            raise not_cached(f"The forecast for {pt['name']}")
        return recs[-1], "replay"

    def fetch() -> dict:
        r, url = http_get(FORECAST, {"latitude": pt["lat"], "longitude": pt["lon"], "timezone": "auto",
                                     "forecast_days": FORECAST_DAYS,
                                     "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                                              "precipitation_probability_max,precipitation_sum"})
        r.raise_for_status()
        d = r.json()["daily"]
        rows = [{"date": d["time"][i], "weather_code": d["weather_code"][i],
                 "summary": WMO.get(d["weather_code"][i], "unknown"),
                 "temp_max_c": d["temperature_2m_max"][i], "temp_min_c": d["temperature_2m_min"][i],
                 "precip_probability_pct": d["precipitation_probability_max"][i],
                 "precip_mm": d["precipitation_sum"][i]} for i in range(len(d["time"]))]
        return {"kind": "forecast", "city": pt["name"], "lat": pt["lat"], "lon": pt["lon"], "issued_for": today.isoformat(),
                "days": rows, "source": "Open-Meteo forecast API", "source_url": url, "licence": METEO_LICENCE,
                "retrieved_at": now_iso()}
    return cached("weather", f"forecast__{slug(pt['name'])}__{today.isoformat()}", fetch, f"Forecast for {pt['name']}")


def archive(city: str, start: dt.date, end: dt.date) -> tuple[dict, str]:
    pt = city_point(city)
    if mode() == "replay":
        rows, src = {}, None
        for rec in cache_glob("weather", f"archive__{slug(pt['name'])}__*"):
            src = src or rec
            for row in rec["days"]:
                rows[row["date"]] = row
        need = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        if not src or any(d not in rows for d in need):
            raise not_cached(f"Last year's weather for {pt['name']} {start}..{end}")
        return dict(src, days=[rows[d] for d in need]), "replay"

    def fetch() -> dict:
        r, url = http_get(ARCHIVE, {"latitude": pt["lat"], "longitude": pt["lon"], "timezone": "auto",
                                    "start_date": start.isoformat(), "end_date": end.isoformat(),
                                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"})
        r.raise_for_status()
        d = r.json()["daily"]
        rows = [{"date": d["time"][i], "weather_code": d["weather_code"][i],
                 "summary": WMO.get(d["weather_code"][i], "unknown"), "temp_max_c": d["temperature_2m_max"][i],
                 "temp_min_c": d["temperature_2m_min"][i], "precip_mm": d["precipitation_sum"][i]}
                for i in range(len(d["time"]))]
        return {"kind": "archive", "city": pt["name"], "lat": pt["lat"], "lon": pt["lon"], "days": rows,
                "source": "Open-Meteo historical weather API (archive)", "source_url": url,
                "licence": METEO_LICENCE, "retrieved_at": now_iso()}
    return cached("weather", f"archive__{slug(pt['name'])}__{start}__{end}", fetch, f"Archive weather for {pt['name']}")


def one_year_back(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


# ------------------------------------------------------------------ currency
def fx_rates() -> tuple[dict, str]:
    def fetch() -> dict:
        r, url = http_get(FX_URL)
        r.raise_for_status()
        d = r.json()
        return {"kind": "fx", "base": d["base"], "date": d["date"], "rates": d["rates"],
                "source": "Frankfurter (ECB reference rates)", "source_url": url, "licence": FX_LICENCE,
                "retrieved_at": now_iso()}
    return cached("fx", "latest__EUR", fetch, "Exchange rates")


def convert(amount: float, src: str, dst: str) -> dict:
    src, dst = src.strip().upper(), dst.strip().upper()
    rec, m = fx_rates()
    rates = dict(rec["rates"], **{rec["base"]: 1.0})
    peg = None
    for code in (src, dst):
        if code not in rates:
            if code == "JOD" and "USD" in rates:
                rates["JOD"] = rates["USD"] * JOD_PER_USD
                peg = code
            else:
                raise DataError(422, "unknown_currency", f"{code} is not in the ECB reference rates.",
                                supported=sorted(rates))
    rate = rates[dst] / rates[src]
    out = {"amount": amount, "from": src, "to": dst, "rate": round(rate, 6), "converted": round(amount * rate, 2),
           "rate_date": rec["date"], "method": "ecb_reference_rate", "source": rec["source"],
           "source_url": rec["source_url"], "licence": rec["licence"], "mode": m, "retrieved_at": rec["retrieved_at"],
           "note": "Reference rate; banks and exchange offices charge a margin. Use it for estimates only.",
           "citation": {"id": f"fx-ecb-{rec['date']}", "title": f"ECB reference rates {rec['date']} (Frankfurter)",
                        "url": rec["source_url"]}}
    if peg:
        out.update(method="fixed_peg_via_usd", peg={"JOD_per_USD": JOD_PER_USD, "source_url": JOD_PEG_SOURCE},
                   note="JOD is not in the ECB reference rates. Converted through USD with the Central Bank of "
                        "Jordan peg (1 USD = 0.709 JOD). Estimate only.")
        out["citation"] = {"id": "fx-jod-peg", "title": "JOD fixed peg 1 USD = 0.709 JOD (Central Bank of Jordan) "
                           f"+ ECB USD rate {rec['date']}", "url": JOD_PEG_SOURCE}
    return out


# ------------------------------------------------------------------ manifest
_MANIFEST: dict | None = None


def manifest() -> dict:
    global _MANIFEST
    if _MANIFEST is None:
        path = CACHE_DIR / "manifest.json"
        _MANIFEST = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return _MANIFEST


def page_query(params: dict) -> str:
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})
