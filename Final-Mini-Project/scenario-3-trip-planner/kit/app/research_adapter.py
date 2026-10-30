import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from planning.research import TripRequest, ResearchBundle
from researcher.researcher import run_research


class ResearchInputError(ValueError):
    """Raised when the Coordinator has insufficient or invalid trip details."""


# Interest tags the Travel API matches on (services/travel_data.TAG_RULES).
TRAVEL_API_TAGS = frozenset({
    "history", "museum", "religious", "nature", "views",
    "markets", "art", "architecture", "family", "food",
    "entertainment", "relaxation", "adventure",
})

# Frontend interest labels (the eight chips in rihla-frontend/src/App.jsx).
# Each maps to the Travel API tags whose rules describe it. A label with no
# matching tag maps to nothing: it is reported as unsupported, never guessed
# into another category.
INTEREST_LABELS: dict[str, tuple[str, ...]] = {
    "history & culture": ("history",),
    "art & museums": ("art", "museum"),
    "shopping": ("markets",),
    "nature & outdoors": ("nature",),
    "food & cuisine": ("food",),
    "adventure": ("adventure",),
    "relaxation": ("relaxation",),
    "entertainment": ("entertainment",),
}

# Single words, e.g. from a chat request ("history and museums").
INTEREST_WORDS: dict[str, str] = {
    "history": "history", "historical": "history", "historic": "history",
    "heritage": "history", "historical sites": "history",
    "museum": "museum", "museums": "museum",
    "art": "art", "arts": "art", "gallery": "art", "galleries": "art",
    "markets": "markets", "market": "markets", "shopping": "markets",
    "bazaar": "markets", "bazaars": "markets", "souq": "markets",
    "souk": "markets",
    "nature": "nature", "outdoors": "nature", "parks": "nature",
    "gardens": "nature",
    "food": "food", "cuisine": "food",
    "religious": "religious", "religion": "religious",
    "mosques": "religious", "churches": "religious",
    "views": "views", "viewpoints": "views",
    "architecture": "architecture",
    "family": "family", "kids": "family", "children": "family",
    "adventure": "adventure", "hiking": "adventure",
    "relaxation": "relaxation", "relaxing": "relaxation", "spa": "relaxation",
    "wellness": "relaxation",
    "entertainment": "entertainment", "nightlife": "entertainment",
    "shows": "entertainment", "theatre": "entertainment", "theater": "entertainment",
}

_JOINER = re.compile(r"\s*(?:&|\+|\band\b)\s*")
_SPLITTER = re.compile(r"[,;/&+]|\band\b")


@dataclass(frozen=True)
class InterestMatch:
    """Travel API tags for the traveller's interests, and what had no tag."""
    tags: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def normalize_interests(interests: list[str] | str | None) -> InterestMatch:
    """
    Map user-facing interests ("History & Culture") to Travel API tags.

    Unsupported interests are reported, not passed on: the API would treat
    them as free-text searches that match almost nothing.
    """
    if isinstance(interests, str):
        interests = [interests]

    tags: list[str] = []
    unsupported: list[str] = []

    for raw in interests or []:
        if not isinstance(raw, str) or not raw.strip():
            continue

        text = " ".join(raw.casefold().split())
        label = _JOINER.sub(" & ", text)

        if label in INTEREST_LABELS:
            found = list(INTEREST_LABELS[label])
            if not found:
                unsupported.append(raw.strip())
        else:
            found = []
            for word in _SPLITTER.split(text):
                word = word.strip()
                if not word:
                    continue
                tag = INTEREST_WORDS.get(word)
                if tag is None:
                    unsupported.append(word)
                else:
                    found.append(tag)

        tags.extend(tag for tag in found if tag not in tags)

    return InterestMatch(tags=tags, unsupported=unsupported)


def build_research_request(trip_info: dict[str, Any]) -> TripRequest:
    """
    Convert Coordinator output into the Research Agent's TripRequest.

    Never invent missing dates, destination, currency, or budget.
    """
    required_fields = (
    "destination",
    "currency",
    "start_date",
    "end_date",
    "budget",
    "travellers",
)
    missing = [
        field
        for field in required_fields
        if trip_info.get(field) is None or trip_info.get(field) == ""
    ]

    if missing:
        raise ResearchInputError(
            "Missing required trip information: " + ", ".join(missing)
        )

    data = {
        "destination": trip_info["destination"],
        "currency": trip_info["currency"],
        "travellers": trip_info["travellers"],
        "budget_total": trip_info["budget"],
        "start_date": trip_info["start_date"],
        "end_date": trip_info["end_date"],
        # Research gets Travel API tags; trip_info keeps the
        # traveller's own wording for display and preferences.
        "interests": normalize_interests(trip_info.get("interests")).tags,
        "required_places": trip_info.get("required_places") or [],
    }

    for optional_field in (
        "budget_style",
        "max_walk_minutes",
        "day_start",
        "day_end",
    ):
        value = trip_info.get(optional_field)
        if value is not None:
            data[optional_field] = value

    try:
        return TripRequest.model_validate(data)
    except ValidationError as exc:
        raise ResearchInputError(
            f"Invalid trip information: {exc}"
        ) from exc


def run_research_pipeline(
    trip_info: dict[str, Any],
    *,
    use_llm: bool = False,
    caller=None,
) -> ResearchBundle:
    """
    Coordinator output -> TripRequest -> Research Agent -> ResearchBundle.

    The optional caller supports isolated MCP tests.
    """

    trip_request = build_research_request(trip_info)

    match = normalize_interests(trip_info.get("interests"))
    print("Research interest tags:", match.tags or "none (no filter)")
    if match.unsupported:
        print("Interests with no Travel API tag:", match.unsupported)

    kwargs = {"use_llm": use_llm}

    if caller is not None:
        kwargs["caller"] = caller

    result = run_research(trip_request, **kwargs)

    return result.bundle