"""Read-only MCP lookup adapter for trip orchestration."""

from __future__ import annotations

from typing import Any, Callable

from researcher.mcp_client import call_read_tool


class MCPLookupError(RuntimeError):
    """Raised when a read-only MCP lookup fails."""


def call_lookup(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an approved read-only MCP tool without fabricating results."""

    if name not in {"get_opening_hours", "travel_time"}:
        raise ValueError(f"Unsupported lookup: {name}")

    try:
        result = call_read_tool(name, arguments)
    except Exception as exc:
        raise MCPLookupError(
            f"MCP lookup '{name}' could not be completed."
        ) from exc

    if not isinstance(result, dict):
        raise MCPLookupError(
            f"MCP lookup '{name}' returned an invalid response."
        )

    if result.get("ok") is not True:
        raise MCPLookupError(
            f"MCP lookup '{name}' did not return successful evidence."
        )

    return result


def create_hours_lookup(city: str) -> Callable[[str], dict]:
    """Create a city-bound opening-hours lookup."""

    def hours_lookup(place_name: str) -> dict:
        return call_lookup(
            "get_opening_hours",
            {
                "place_name": place_name,
                "city": city,
            },
        )

    return hours_lookup


def create_travel_lookup(
    city: str,
    mode: str = "driving",
) -> Callable[[str, str], dict]:
    """Create a city- and transport-mode-bound travel lookup."""

    def travel_lookup(from_place: str, to_place: str) -> dict:
        return call_lookup(
            "travel_time",
            {
                "from_place": from_place,
                "to_place": to_place,
                "city": city,
                "mode": mode,
            },
        )

    return travel_lookup
