from typing import Any


sessions: dict[str, dict[str, Any]] = {}


TRIP_FIELDS = {
    "destination",
    "duration_days",
    "budget",
    "currency",
    "travellers",
    "start_date",
    "end_date",
    "interests",
    "required_places",
    "budget_style",
    "max_walk_minutes",
    "day_start",
    "day_end",
}


def get_session(session_id: str) -> dict[str, Any]:
    if session_id not in sessions:
        sessions[session_id] = {
            "itinerary": None,
            "pending_action": None,
            "trip_details": {},
        }

    return sessions[session_id]


def update_trip_details(
    session_id: str,
    new_details: dict[str, Any],
) -> dict[str, Any]:
    session = get_session(session_id)
    trip_details = session.setdefault("trip_details", {})

    for field in TRIP_FIELDS:
        value = new_details.get(field)

        if value is not None:
            trip_details[field] = value

    return trip_details.copy()