from typing import Any

sessions: dict[str, dict[str, Any]] = {}


def get_session(session_id: str) -> dict[str, Any]:
    if session_id not in sessions:
        sessions[session_id] = {
            "itinerary": None,
            "pending_action": None,
        }

    return sessions[session_id]