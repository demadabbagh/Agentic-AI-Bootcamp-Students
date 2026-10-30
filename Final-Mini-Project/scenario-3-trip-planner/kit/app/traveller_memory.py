"""Persistent traveller memory through MCP: get_preferences at the start of a session, save_preferences when the
traveller states a lasting preference.

Rules (kit README, "MCP tools"; CONTRACT §5: the cases send "user": {"traveller_id", "name"} with every message):
- The traveller is the request's user.traveller_id. Preferences are loaded only for that traveller, once per
  session, and kept in that session only: another traveller never sees them.
- A preference is saved only when the traveller says it should be kept ("remember that I am vegetarian",
  "from now on..."). One-off details of this trip are not saved. Parsing is deterministic and conservative: only
  the fields the Trip Store knows, only values said explicitly.
- Saved lists are merged with what is stored (the store replaces a list it is sent), and an update never
  contains an empty value, so nothing stored is erased by accident.
- When the memory service is unavailable, planning continues without it and the reply says so.
"""
from __future__ import annotations

import re
from typing import Any

from app.mcp_app_client import call_app_tool
from app.research_adapter import INTEREST_WORDS

TRAVELLER_ID = re.compile(r"[A-Za-z0-9_.-]{2,40}")  # what the Trip Store accepts

# Saying that something should be kept beyond this trip: required before anything is saved.
_LASTING = re.compile(r"\b(remember|from now on|for (all )?future trips|always|save (this|that|it) "
                      r"(as|to|in) my preferences?|update my preferences?)\b", re.IGNORECASE)

_DIETARY = {
    "vegetarian": r"vegetarian", "vegan": r"vegan", "halal": r"halal", "kosher": r"kosher",
    "pescatarian": r"pescatarian", "gluten-free": r"gluten[- ]free|coeliac|celiac",
    "nut allergy": r"nut allerg(y|ic)|allergic to nuts", "lactose-free": r"lactose[- ]intolerant|lactose[- ]free",
    "dairy-free": r"dairy[- ]free",
}
_WALK = re.compile(r"walk(?:ing)?\s+(?:up to|at most|no more than|max(?:imum)?(?: of)?|for at most)\s+(\d{1,3})\s*"
                   r"(?:min|minutes)\b", re.IGNORECASE)
_BUDGET_STYLE = re.compile(r"\b(budget|mid-range|midrange|splurge|luxury)\s+(?:traveller|traveler|style|trips?)\b",
                           re.IGNORECASE)
_CURRENCY = re.compile(r"\b(?:my )?(?:home )?currency (?:is|should be|=)\s*([A-Za-z]{3})\b", re.IGNORECASE)
_INTERESTS = re.compile(r"\b(?:i (?:love|like|enjoy|am interested in|'m interested in)|my interests are)\s+"
                        r"([^.;!?]+)", re.IGNORECASE)


def traveller_id_from(user: Any) -> str | None:
    """The traveller id from the request's user object, if it is one the Trip Store would accept."""
    if not isinstance(user, dict):
        return None
    value = user.get("traveller_id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if TRAVELLER_ID.fullmatch(value) else None


def _interest_words(text: str) -> list[str]:
    found = []
    for word in re.split(r"[\s,/&+]+|\band\b", text.casefold()):
        tag = INTEREST_WORDS.get(word.strip())
        if tag and tag not in found:
            found.append(tag)
    return found


def lasting_preferences(message: str) -> dict[str, Any]:
    """The preferences the traveller explicitly asked to be remembered in this message ({} for none)."""
    if not isinstance(message, str) or not _LASTING.search(message):
        return {}
    text = message.casefold()
    prefs: dict[str, Any] = {}
    dietary = [label for label, pattern in _DIETARY.items() if re.search(rf"\b({pattern})\b", text)]
    if dietary:
        prefs["dietary"] = dietary
    if m := _WALK.search(message):
        minutes = int(m.group(1))
        if 1 <= minutes <= 240:
            prefs["max_walk_minutes"] = minutes
    if m := _BUDGET_STYLE.search(message):
        style = m.group(1).casefold()
        prefs["budget_style"] = {"midrange": "mid-range", "luxury": "splurge"}.get(style, style)
    if m := _CURRENCY.search(message):
        prefs["home_currency"] = m.group(1).upper()
    interests = [w for m in _INTERESTS.finditer(message) for w in _interest_words(m.group(1))]
    if interests:
        prefs["interests"] = list(dict.fromkeys(interests))
    return prefs


def merged_update(stored: dict[str, Any] | None, update: dict[str, Any]) -> dict[str, Any]:
    """What to send to save_preferences: the new values, lists merged with the stored ones, nothing empty."""
    stored = stored or {}
    out: dict[str, Any] = {}
    for key, value in update.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            previous = stored.get(key) if isinstance(stored.get(key), list) else []
            value = list(dict.fromkeys([*previous, *value]))
        out[key] = value
    return out


def load_preferences(traveller_id: str) -> tuple[dict[str, Any] | None, str]:
    """(preferences, status) with status "loaded", "not_found" (nothing saved yet) or "unavailable"."""
    try:
        result = call_app_tool("get_preferences", {"traveller_id": traveller_id})
    except Exception:
        return None, "unavailable"
    if isinstance(result, dict) and result.get("ok") is True:
        prefs = result.get("preferences")
        return (dict(prefs) if isinstance(prefs, dict) else {}), "loaded"
    code = (result.get("error") or {}).get("code") if isinstance(result, dict) and \
        isinstance(result.get("error"), dict) else None
    return None, "not_found" if code == "traveller_not_found" else "unavailable"


def save_preferences(traveller_id: str, update: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """(stored preferences after the save, status "saved" | "unavailable" | "rejected"). Sends nothing for an
    empty update."""
    if not update:
        return None, "nothing_to_save"
    try:
        result = call_app_tool("save_preferences", {"traveller_id": traveller_id, "preferences": update})
    except Exception:
        return None, "unavailable"
    if isinstance(result, dict) and result.get("ok") is True and isinstance(result.get("preferences"), dict):
        return dict(result["preferences"]), "saved"
    status = (result.get("error") or {}).get("status") if isinstance(result, dict) and \
        isinstance(result.get("error"), dict) else None
    return None, "rejected" if status == 422 else "unavailable"


def session_preferences(session: dict, traveller_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The traveller's preferences for this session: loaded through MCP the first time, then reused.
    Returns (preferences or None, the status of a load made now, or None when nothing was loaded now)."""
    if not traveller_id:
        return None, None
    memory = session.get("memory")
    if isinstance(memory, dict) and memory.get("traveller_id") == traveller_id and memory.get("status") == "loaded":
        return memory.get("preferences"), None
    prefs, status = load_preferences(traveller_id)
    session["memory"] = {"traveller_id": traveller_id, "preferences": prefs, "status": status}
    return prefs, status


def remember(session: dict, traveller_id: str, update: dict[str, Any]) -> str:
    """Save a lasting preference for this traveller and keep the stored result in the session. Returns the
    save status."""
    memory = session.get("memory") if isinstance(session.get("memory"), dict) else {}
    stored = memory.get("preferences") if memory.get("traveller_id") == traveller_id else None
    saved, status = save_preferences(traveller_id, merged_update(stored, update))
    if status == "saved":
        session["memory"] = {"traveller_id": traveller_id, "preferences": saved, "status": "loaded"}
    return status


def describe_preferences(prefs: dict[str, Any] | None) -> str | None:
    """The stored preferences in a few words, for the reply when they were just loaded (None when empty)."""
    if not isinstance(prefs, dict):
        return None
    parts = []
    for key, label in (("dietary", "diet"), ("interests", "interests"), ("budget_style", "budget style"),
                       ("max_walk_minutes", "walks up to (minutes)"), ("home_currency", "currency"),
                       ("travel_party", "travelling as")):
        value = prefs.get(key)
        if isinstance(value, list):
            value = ", ".join(v for v in value if isinstance(v, str) and v.strip())
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    return "; ".join(parts) or None


def _party_size(text: Any) -> int | None:
    """'1 adult' -> 1, '2 adults and 1 child (age 7)' -> 3; None when it does not say."""
    if not isinstance(text, str):
        return None
    counts = [int(n) for n in re.findall(r"(\d+)\s+(?:adults?|child(?:ren)?|kids?|people|persons?|travell?ers?)",
                                         text, re.IGNORECASE)]
    total = sum(counts)
    return total if 1 <= total <= 50 else None


def apply_preferences(trip_info: dict[str, Any], prefs: dict[str, Any] | None) -> list[str]:
    """Fill the trip details the traveller did not give from their saved preferences. Never overrides what was
    said for this trip. Returns what was used, in words, for the reply."""
    if not isinstance(prefs, dict) or not prefs:
        return []
    used = []
    if not trip_info.get("interests") and isinstance(prefs.get("interests"), list):
        words = [w for i in prefs["interests"] if isinstance(i, str) for w in _interest_words(i)]
        if words:
            trip_info["interests"] = list(dict.fromkeys(words))
            used.append("interests: " + ", ".join(prefs["interests"]))
    if trip_info.get("budget_style") is None and prefs.get("budget_style") in ("budget", "mid-range", "splurge"):
        trip_info["budget_style"] = prefs["budget_style"]
        used.append(f"budget style: {prefs['budget_style']}")
    walk = prefs.get("max_walk_minutes")
    if trip_info.get("max_walk_minutes") is None and isinstance(walk, int) and not isinstance(walk, bool) \
            and 1 <= walk <= 240:
        trip_info["max_walk_minutes"] = walk
        used.append(f"walks of up to {walk} minutes")
    currency = prefs.get("home_currency")
    if not trip_info.get("currency") and isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency):
        trip_info["currency"] = currency.upper()
        used.append(f"currency: {currency.upper()}")
    party = _party_size(prefs.get("travel_party"))
    if trip_info.get("travellers") is None and party:
        trip_info["travellers"] = party
        used.append(f"travelling as {prefs['travel_party']}")
    dietary = [d for d in prefs.get("dietary") or [] if isinstance(d, str) and d.strip()]
    if dietary:
        # Not a planning filter: listings only mention diets, they never certify them (kit README).
        used.append("dietary needs: " + ", ".join(dietary) + " (restaurant listings do not certify diets: check "
                    "menus before you go)")
    return used
