from typing import Any

from pydantic import ValidationError

from planning.research import TripRequest, ResearchBundle
from researcher.researcher import run_research


class ResearchInputError(ValueError):
    """Raised when the Coordinator has insufficient or invalid trip details."""


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
        "interests": trip_info.get("interests") or [],
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

    kwargs = {"use_llm": use_llm}

    if caller is not None:
        kwargs["caller"] = caller

    result = run_research(trip_request, **kwargs)

    return result.bundle