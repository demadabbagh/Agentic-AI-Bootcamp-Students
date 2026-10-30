"""Build the ResearchBundle from the tool log, then drop what cannot be trusted. Code, not the LLM."""
from __future__ import annotations

import math
import re

from pydantic import BaseModel, Field, ValidationError

from planning.research import ResearchBundle, ResearchedPlace, ToolFailure, TripRequest

# Listing text that talks to AI agents is an injection attempt, never a fact about a place.
# Each pattern needs an instruction AND what it targets (rules, plans, agents), so ordinary travel prose such as
# "locals disregard the queue" or "ignore the crowds" is not mistaken for one.
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\s+(all|any|previous|prior|your|earlier|above|these)\b"
    r"[\w\s']{0,30}?\b(instructions?|rules|prompts?|guidelines|budget|constraints|directions)\b|"
    r"\b(ignore|disregard|forget|override)\s+the\b[\w\s']{0,30}?\b(instructions?|prompts?|guidelines|"
    r"traveller'?s budget|user'?s budget|constraints)\b|"
    r"system prompt|"
    r"\byou are an? (ai|llm|language model|assistant|chatbot)\b|"
    r"\b(ai|llm|gpt)\b(\s+[\w-]+){0,2}\s+(agents?|assistants?|models?|planners?|crews?|systems?)\b|"
    r"\b(note|message|attention|instructions?)\s+(to|for)\s+(any\s+|all\s+)?(ai|llm|bots?|chatbots?|assistants?|"
    r"agents?|planners?)\b|"
    r"\b(attention|hey|dear)\s+(ai|llm|bots?|chatbots?|assistants?)\b|"
    r"\b(assistants?|agents?|planners?|chatbots?|bots?)\s+(planning|creating|building|making|writing)\s+"
    r"(trips?|itinerar(y|ies)|plans?)\b|"
    r"\b(add|include|recommend|schedule|put|place|list)\b[^.]{0,40}?\b(to|in|on|into)\s+(every|all|each|any)\s+"
    r"(plans?|itinerar(y|ies)|days?|trips?|lists?)\b|"
    r"\balways\s+(put|place|list|rank|recommend|schedule|include|add)\b|"
    r"\byou must (add|include|recommend|schedule|put|list)\b",
    re.IGNORECASE,
)


def looks_like_injection(text: str | None) -> bool:
    return bool(text and isinstance(text, str) and _INJECTION.search(text))


def _listing_texts(place: ResearchedPlace) -> list[str]:
    """Every free-text field of a listing that later steps may read: the description, the name, and the raw
    price and hours text (the Budget Analyst shows price text to its LLM)."""
    texts = [place.description, place.name]
    for parsed in (place.price, place.hours):
        if isinstance(parsed, dict) and isinstance(parsed.get("raw"), str):
            texts.append(parsed["raw"])
    return [t for t in texts if t]


def _coordinate(value, low: float, high: float) -> float | None:
    """A usable coordinate or None. A listing with a typo in its coordinates keeps its other facts."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def _clean_listing(item: dict) -> tuple[dict, list[str]]:
    """Repair what a single listing gets wrong without inventing anything: an impossible coordinate is dropped
    (it was never a location), a price or hours that is not the parsed object is dropped (unknown, not guessed).
    Returns the cleaned copy and what was removed."""
    clean, removed = dict(item), []
    lat, lon = item.get("lat"), item.get("lon")
    if lat is not None or lon is not None:
        clean["lat"], clean["lon"] = _coordinate(lat, -90, 90), _coordinate(lon, -180, 180)
        if clean["lat"] is None or clean["lon"] is None:
            clean["lat"] = clean["lon"] = None  # half a coordinate is no location
            removed.append("coordinates")
    for field in ("price", "hours", "citation"):
        if clean.get(field) is not None and not isinstance(clean[field], dict):
            clean[field] = None
            removed.append(field)
    if clean.get("price_sources") is not None and not isinstance(clean["price_sources"], list):
        clean["price_sources"] = None
        clean["price_conflict"] = False
        removed.append("price_sources")
    if clean.get("tags") is not None and not isinstance(clean["tags"], list):
        clean["tags"] = []
        removed.append("tags")
    return clean, removed


def _sanitize_results(results: list, key: str, category: str, dropped: list[DroppedPlace]) -> list:
    """Each listing is checked on its own, so one malformed listing never costs the trip all its research."""
    out = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get(key), list):
            out.append(result)  # a failure (or no list): from_tool_results reports it
            continue
        items = []
        for item in result[key]:
            if not isinstance(item, dict):
                continue
            clean, _ = _clean_listing(item)
            if not (clean.get("id") and clean.get("name")):
                items.append(clean)  # from_tool_results skips it, as before
                continue
            try:
                ResearchedPlace.from_listing(clean, category)
            except (ValidationError, TypeError, ValueError) as exc:
                dropped.append(DroppedPlace(place_id=str(clean["id"]), name=str(clean["name"]),
                                            reason=f"malformed listing data ({type(exc).__name__})",
                                            source_url=clean.get("source_url")
                                            if isinstance(clean.get("source_url"), str) else None))
                continue
            items.append(clean)
        out.append({**result, key: items})
    return out


class DroppedPlace(BaseModel):
    place_id: str
    name: str
    reason: str
    source_url: str | None = None


class ResearchResult(BaseModel):
    bundle: ResearchBundle                                   # what the Budget Analyst reads
    dropped: list[DroppedPlace] = Field(default_factory=list)
    opening_hours: list[dict] = Field(default_factory=list)  # successful get_opening_hours results, verbatim
    weather: list[dict] = Field(default_factory=list)        # successful get_weather results, verbatim
    tool_calls: int = 0
    # One entry per MCP call, in order, shaped like the API contract's tool_calls: {"tool", "ok", "ms"}, plus the
    # error code of a failed call. No arguments or results: safe to return to a client as they are.
    calls: list[dict] = Field(default_factory=list)


def _ok(result) -> bool:
    return isinstance(result, dict) and result.get("ok") is not False and not isinstance(result.get("error"), dict)


def build_result(trip: TripRequest, log: list[dict]) -> ResearchResult:
    def results(tool: str) -> list:
        return [entry["result"] for entry in log if entry["tool"] == tool]

    dropped: list[DroppedPlace] = []
    bundle = ResearchBundle.from_tool_results(
        trip,
        attractions=_sanitize_results(results("find_attractions"), "attractions", "attraction", dropped),
        restaurants=_sanitize_results(results("find_restaurants"), "restaurants", "restaurant", dropped),
        fx=results("convert_currency"),
    )

    kept = []
    for place in bundle.places:
        if place.synthetic:
            reason = "synthetic test listing, not a real place"
        elif not place.source_url:
            reason = "no source_url from a tool"
        elif any(looks_like_injection(text) for text in _listing_texts(place)):
            reason = "listing text contains instructions to AI agents (ignored)"
        else:
            kept.append(place)
            continue
        dropped.append(DroppedPlace(place_id=place.place_id, name=place.name, reason=reason,
                                    source_url=place.source_url))

    failures = list(bundle.failures)
    for tool in ("get_opening_hours", "get_weather"):
        for result in results(tool):
            if not _ok(result):
                err = result.get("error") if isinstance(result, dict) else {}
                err = err if isinstance(err, dict) else {}
                failures.append(ToolFailure(tool=tool, code=str(err.get("code", "error")),
                                            message=str(err.get("message", ""))))

    return ResearchResult(
        bundle=bundle.model_copy(update={"places": kept, "failures": failures}),
        dropped=dropped,
        opening_hours=[r for r in results("get_opening_hours") if _ok(r)],
        weather=[r for r in results("get_weather") if _ok(r)],
        tool_calls=len(log),
        calls=[_call_record(entry) for entry in log],
    )


def _call_record(entry: dict) -> dict:
    result = entry.get("result")
    record = {"tool": entry["tool"], "ok": _ok(result), "ms": entry.get("ms")}
    if not record["ok"]:
        err = result.get("error") if isinstance(result, dict) else None
        record["error"] = str(err.get("code", "error")) if isinstance(err, dict) else "bad_result"
    return record