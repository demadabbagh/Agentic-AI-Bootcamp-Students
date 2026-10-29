from typing import Any, Callable


class RevisionNotAvailableError(RuntimeError):
    """Raised when no real revision implementation is connected."""


def revise_itinerary(
    current_itinerary: dict[str, Any],
    user_request: str,
    revision_agent: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Request a revised itinerary without mutating the original.

    The revision_agent must accept:
        (current_itinerary, user_request)

    and return a complete revised itinerary dictionary.

    The real Planning Agent will be connected later through an adapter.
    """
    if not current_itinerary:
        raise ValueError("Cannot revise an empty itinerary.")

    if not user_request.strip():
        raise ValueError("Revision request cannot be empty.")

    if revision_agent is None:
        raise RevisionNotAvailableError(
            "No revision agent is connected yet."
        )

    from copy import deepcopy

    revised = revision_agent(
        deepcopy(current_itinerary),
        user_request,
    )

    if not isinstance(revised, dict):
        raise TypeError("Revision agent must return an itinerary dictionary.")

    if not revised:
        raise ValueError("Revision agent returned an empty itinerary.")

    return deepcopy(revised)