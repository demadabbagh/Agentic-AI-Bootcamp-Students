import re
from typing import Any

from app.mcp_app_client import call_app_tool


class TripStoreError(RuntimeError):
    """Raised when saving a trip fails. `code` is the Trip Store's error code when it gave one."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


TRIP_ID = re.compile(r"TRIP-\d+")


def trip_title(itinerary: dict[str, Any]) -> str:
    """The title a saved trip gets: "<destination>, <n> days"."""
    days = len(itinerary.get("days") or [])
    return f"{itinerary.get('destination')}, {days} {'day' if days == 1 else 'days'}"


def save_trip_to_store(
    itinerary: dict[str, Any],
    traveller_id: str,
    action_id: str,
    title: str | None = None,
) -> dict[str, Any]:
    """
    Save an approved itinerary through the MCP server's save_trip tool.

    The action_id is the idempotency key: the Trip Store answers a repeated save of the
    same body with the same trip and saves nothing new. There is no REST fallback: if
    the MCP call fails, nothing is reported as saved.
    """

    if not traveller_id or not traveller_id.strip():
        raise TripStoreError("Traveller ID is required.")

    if not isinstance(itinerary, dict):
        raise TripStoreError("Itinerary must be a dictionary.")

    required_fields = {
        "destination",
        "currency",
        "budget_total",
        "days",
    }

    missing_fields = [
        field
        for field in required_fields
        if field not in itinerary
    ]

    if missing_fields:
        raise TripStoreError(
            "Itinerary is missing required fields: "
            + ", ".join(sorted(missing_fields))
        )

    if not itinerary["days"]:
        raise TripStoreError("Itinerary must contain at least one day.")

    try:
        result = call_app_tool("save_trip", {
            "traveller_id": traveller_id.strip(),
            "title": title or trip_title(itinerary),
            "itinerary": itinerary,
            "idempotency_key": action_id,
        })
    except Exception as exc:
        raise TripStoreError("The MCP server could not be reached to save the trip.", "mcp_unavailable") from exc

    if not isinstance(result, dict):
        raise TripStoreError("The MCP save_trip tool returned an invalid response.", "bad_result")

    if result.get("ok") is not True:
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        raise TripStoreError(
            str(error.get("message") or "The Trip Store did not save the trip."),
            str(error.get("code") or "save_failed"),
        )

    trip_id = result.get("trip_id")
    if not isinstance(trip_id, str) or not TRIP_ID.fullmatch(trip_id):
        raise TripStoreError("The Trip Store did not return a valid trip ID.", "bad_result")

    return {k: v for k, v in result.items() if k != "ok"}
