from copy import deepcopy
from typing import Any, Callable


def verify_with_retries(
    itinerary: dict[str, Any],
    verify_agent: Callable[[dict[str, Any]], dict[str, Any]],
    repair_agent: Callable[
        [dict[str, Any], list[Any]], dict[str, Any]
    ] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Verify an itinerary with bounded repair attempts.

    max_attempts counts verification calls, not repairs.
    No repair is performed unless a repair agent is connected.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    current = deepcopy(itinerary)

    for attempt in range(1, max_attempts + 1):
        verification = verify_agent(deepcopy(current))

        if verification.get("approved") is True:
            return {
                "approved": True,
                "itinerary": current,
                "issues": [],
                "attempts": attempt,
            }

        issues = verification.get("issues", [])

        if repair_agent is None or attempt == max_attempts:
            return {
                "approved": False,
                "itinerary": current,
                "issues": issues,
                "attempts": attempt,
            }

        repaired = repair_agent(deepcopy(current), deepcopy(issues))

        if not isinstance(repaired, dict) or not repaired:
            raise ValueError(
                "Repair agent must return a non-empty itinerary dictionary."
            )

        current = deepcopy(repaired)

    raise RuntimeError("Unexpected verification loop state.")