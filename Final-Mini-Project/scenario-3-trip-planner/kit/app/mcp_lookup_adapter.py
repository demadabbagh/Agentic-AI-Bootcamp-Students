"""Read-only MCP lookup adapter for trip orchestration."""

from __future__ import annotations

import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from researcher import perf
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
            perf.count("lookup_retries")
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
        perf.count("lookup_retries")
        sleep(RETRY_DELAYS[attempt])

    raise MCPLookupError(f"MCP lookup '{name}' could not be completed.")


class _RequestMemo:
    """The successful answers of one planning request's lookups, by question. A request asks the same questions
    several times (the Planner measures a leg, the Verifier checks it, a targeted revision verifies again): each
    is now asked once, and the later askers get that same real tool result, made during this request. A failure
    (an exception) is never kept, so it is asked again exactly as before. The lookups are created per request
    (app/orchestrator.py, app/revision.py), so nothing outlives the request or passes between travellers."""

    def __init__(self):
        self._answers: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, ask: Callable[[], dict]) -> dict:
        with self._lock:
            if key in self._answers:
                perf.count("lookup_cache_hits")
                return dict(self._answers[key])
        result = ask()
        with self._lock:
            self._answers[key] = dict(result)
        return result


def create_hours_lookup(city: str) -> Callable[[str], dict]:
    """Create a city-bound opening-hours lookup (each place asked once per request: _RequestMemo)."""
    memo = _RequestMemo()

    def hours_lookup(place_name: str) -> dict:
        return memo.get((place_name,), lambda: call_lookup(
            "get_opening_hours",
            {
                "place_name": place_name,
                "city": city,
            },
        ))

    return hours_lookup


PREPARE_TTL_S = 600.0  # the store keeps a city's listings this long (services/travel_data.LISTINGS_TTL_S)
MAX_PREPARING = 4  # cities being fetched or queued: a burst of clicks must not become a burst on the public sources
_PREPARE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rihla-prepare")
_prepare_lock = threading.Lock()
_prepared: dict[str, float] = {}
_preparing = 0


def prepare_city(city: str) -> str:
    """Start reading a city's guide as soon as the traveller picks it, while the form is still being filled in: reading
    a city for the first time takes 4 to 10 seconds of a plan. Read-only, and nothing depends on it: the plan reads the
    same listings itself, waiting for this read if it is still running. "started", "ready" or "busy"."""
    global _preparing
    key = city.strip().casefold()
    now = time.monotonic()
    with _prepare_lock:
        if now - _prepared.get(key, -PREPARE_TTL_S) < PREPARE_TTL_S:
            return "ready"
        if _preparing >= MAX_PREPARING:
            return "busy"
        _preparing += 1
        if len(_prepared) > 500:
            _prepared.clear()
        _prepared[key] = now

    def read() -> None:
        global _preparing
        try:
            call_read_tool("find_attractions", {"city": city, "limit": 1})
        except Exception:  # noqa: BLE001 - the plan asks again, and reports what it gets
            with _prepare_lock:
                _prepared.pop(key, None)
        finally:
            with _prepare_lock:
                _preparing -= 1

    _PREPARE_POOL.submit(read)
    return "started"


WARM_SECONDS = 3.0  # how long a plan waits for a batched routing request: a slow answer still fills the cache


def warm_routes(city: str, stops: list[str], mode: str = "foot", ride_after_minutes: int | None = None, *,
                seconds: float = WARM_SECONDS) -> None:
    """Ask for the legs between consecutive stops in one request (services/travel_data.warm_routes), so the lookups
    that follow answer at once. An optimisation only: a failure costs time, never a fact."""
    if len(stops) < 2:
        return

    def ask() -> None:
        try:
            call_read_tool("warm_routes", {"places": stops, "city": city, "mode": mode,
                                           "ride_after_minutes": ride_after_minutes})
        except Exception:  # noqa: BLE001 - the lookups that follow ask one by one, as they always did
            perf.count("warm_routes_failed")

    worker = threading.Thread(target=contextvars.copy_context().run, args=(ask,), daemon=True)
    worker.start()
    worker.join(seconds)


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
    The Planner and the Verifier share this lookup, so both see the same legs; each leg is asked once per request
    (_RequestMemo)."""
    memo = _RequestMemo()

    def ask(from_place: str, to_place: str, how: str) -> dict:
        return memo.get((from_place, to_place, how), lambda: call_lookup(
            "travel_time",
            {
                "from_place": from_place,
                "to_place": to_place,
                "city": city,
                "mode": how,
            },
        ))

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

    # planning/evidence.py and the planner send the stops they are about to ask about, in order, as one request
    travel_lookup.warm = lambda stops, rides=True, seconds=WARM_SECONDS: warm_routes(
        city, stops, mode, ride_after_minutes if rides else None, seconds=seconds)
    travel_lookup.walk = lambda from_place, to_place: ask(from_place, to_place, mode)  # the leg alone, no ride
    return travel_lookup
