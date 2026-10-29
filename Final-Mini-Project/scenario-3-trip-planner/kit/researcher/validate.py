"""Build the ResearchBundle from the tool log, then drop what cannot be trusted. Code, not the LLM."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from planning.research import ResearchBundle, ToolFailure, TripRequest

# Listing text that talks to AI agents is an injection attempt, never a fact about a place.
_INJECTION = re.compile(
    r"ignore (all|any|the|previous|prior|your)\b|disregard|system prompt|"
    r"\b(ai|llm) (agent|assistant|model)s?\b|"
    r"(add|include|recommend) (this|it|us|our \w+) (to|in) (every|all|each|your)|you must (add|include|recommend)",
    re.IGNORECASE,
)


def looks_like_injection(text: str | None) -> bool:
    return bool(text and _INJECTION.search(text))


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


def _ok(result) -> bool:
    return isinstance(result, dict) and result.get("ok") is not False and not isinstance(result.get("error"), dict)


def build_result(trip: TripRequest, log: list[dict]) -> ResearchResult:
    def results(tool: str) -> list:
        return [entry["result"] for entry in log if entry["tool"] == tool]

    bundle = ResearchBundle.from_tool_results(
        trip,
        attractions=results("find_attractions"),
        restaurants=results("find_restaurants"),
        fx=results("convert_currency"),
    )

    kept, dropped = [], []
    for place in bundle.places:
        if place.synthetic:
            reason = "synthetic test listing, not a real place"
        elif not place.source_url:
            reason = "no source_url from a tool"
        elif looks_like_injection(place.description):
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
    )