"""Read-only MCP lookup adapter for trip orchestration."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from researcher.mcp_client import call_read_tool


class MCPLookupError(RuntimeError):
    """Raised when a read-only MCP lookup fails."""


# Failures worth one more try: the source was briefly unavailable or busy. A "not found" or a bad request is an
# answer, never retried; and a retry never turns a failure into a value.
TRANSIENT_CODES = frozenset({"upstream_unavailable", "rate_limited", "timeout", "temporarily_unavailable"})
RETRY_DELAYS = (0.7,)  # seconds before each retry: one retry, so a failing source costs about a second


def call_lookup(name: str, arguments: dict[str, Any], *, sleep=time.sleep) -> dict[str, Any]:
    """Call an approved read-only MCP tool without fabricating results."""

    if name not in {"get_opening_hours", "travel_time"}:
        raise ValueError(f"Unsupported lookup: {name}")

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            result = call_read_tool(name, arguments)
        except Exception as exc:
            if attempt == len(RETRY_DELAYS):
                raise MCPLookupError(
                    f"MCP lookup '{name}' could not be completed."
                ) from exc
            sleep(RETRY_DELAYS[attempt])
            continue

        if not isinstance(result, dict):
            raise MCPLookupError(
                f"MCP lookup '{name}' returned an invalid response."
            )

        if result.get("ok") is True:
            return result

        code = (result.get("error") or {}).get("code") if isinstance(result.get("error"), dict) else None
        if code not in TRANSIENT_CODES or attempt == len(RETRY_DELAYS):
            raise MCPLookupError(
                f"MCP lookup '{name}' did not return successful evidence"
                + (f" ({code})." if code else ".")
            )
        sleep(RETRY_DELAYS[attempt])

    raise MCPLookupError(f"MCP lookup '{name}' could not be completed.")


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


DEFAULT_MAX_WALK_MINUTES = 30


def max_walk_minutes() -> int:
    """The walking threshold: RIHLA_MAX_WALK_MINUTES, else 30 minutes."""
    try:
        value = int(os.getenv("RIHLA_MAX_WALK_MINUTES", DEFAULT_MAX_WALK_MINUTES))
    except ValueError:
        return DEFAULT_MAX_WALK_MINUTES
    return value if 1 <= value <= 240 else DEFAULT_MAX_WALK_MINUTES


def create_travel_lookup(
    city: str,
    mode: str = "driving",
    *,
    ride_after_minutes: int | None = None,
) -> Callable[[str, str], dict]:
    """Create a city- and transport-mode-bound travel lookup.

    With mode "foot" and `ride_after_minutes`: a measured walk longer than that is looked up again by car
    (OSRM driving, the only other mode the travel_time tool measures) and, when the ride is shorter, the ride is
    returned: profile "driving", its own minutes, distance and citation, plus the walk's measured minutes
    (walk_duration_min). Nothing is estimated: a failed ride lookup returns the measured walk unchanged, which
    the walking checks then report. There is no public-transport data, so a transit time is never given.
    The Planner and the Verifier share this lookup, so both see the same legs."""

    def ask(from_place: str, to_place: str, how: str) -> dict:
        return call_lookup(
            "travel_time",
            {
                "from_place": from_place,
                "to_place": to_place,
                "city": city,
                "mode": how,
            },
        )

    def travel_lookup(from_place: str, to_place: str) -> dict:
        result = ask(from_place, to_place, mode)
        walk = result.get("duration_min")
        if (mode != "foot" or not ride_after_minutes or not isinstance(walk, int) or isinstance(walk, bool)
                or walk <= ride_after_minutes):
            return result
        try:
            ride = ask(from_place, to_place, "driving")
        except MCPLookupError:
            return {**result, "ride_unavailable": True}
        minutes = ride.get("duration_min")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes >= walk:
            return result
        return {**ride, "profile": "driving", "walk_duration_min": walk,
                "walk_distance_m": result.get("distance_m"), "walk_citation": result.get("citation")}

    return travel_lookup
