from copy import deepcopy

from app.coordinator import understand_trip_request
from app.memory import get_session, update_trip_details
from app.mock_agents import research_trip, plan_trip, verify_trip
from app.revision import RevisionNotAvailableError, revise_itinerary
from app.verification_loop import verify_with_retries


MAX_VERIFICATION_ATTEMPTS = 3


def _verify_itinerary(
    itinerary: dict,
    repair_agent=None,
) -> dict:
    """
    Verify an itinerary using a bounded retry loop.

    The repair agent is optional until the real Planning Agent
    supports repairing a plan based on verification feedback.
    """
    return verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_trip,
        repair_agent=repair_agent,
        max_attempts=MAX_VERIFICATION_ATTEMPTS,
    )


def orchestrate_trip(
    user_request: str,
    session_id: str | None = None,
) -> dict:
    """
    Coordinate the trip planning workflow.

    Workflow:
    1. Load session memory.
    2. Understand the user's intent.
    3. Handle revisions and approvals.
    4. Merge trip details.
    5. Research the destination.
    6. Plan the itinerary.
    7. Verify with bounded retries.
    8. Request user approval.
    """

    # STEP 1: Load session memory
    previous_trip_details = {}
    session = None

    if session_id is not None:
        session = get_session(session_id)
        previous_trip_details = session.get("trip_details", {}).copy()

    # STEP 2: Coordinator Agent
    trip_info = understand_trip_request(
        user_request,
        previous_trip_details=previous_trip_details,
    )

    intent = trip_info["intent"]
    print(f"1. Coordinator detected intent: {intent}")

    # STEP 3: Handle revision requests
    if intent == "revise_trip":
        if session is None or session.get("itinerary") is None:
            return {
                "status": "needs_clarification",
                "message": "There is no existing itinerary to revise.",
            }

        try:
            revised_itinerary = revise_itinerary(
                current_itinerary=deepcopy(session["itinerary"]),
                user_request=user_request,
            )
        except RevisionNotAvailableError:
            return {
                "status": "revision_not_supported",
                "message": (
                    "Revision was requested, but the real revision "
                    "agent is not connected yet. "
                    "Your existing itinerary has not been changed."
                ),
            }

        verification = _verify_itinerary(revised_itinerary)

        if not verification["approved"]:
            return {
                "status": "needs_revision",
                "message": "The revised itinerary did not pass verification.",
                "issues": verification["issues"],
            }

        return {
            "status": "pending_approval",
            "itinerary": verification["itinerary"],
        }

    # STEP 4: Approval must use /api/approve
    if intent == "approve_trip":
        return {
            "status": "approval_required",
            "message": (
                "Trip approval must be completed using "
                "the /api/approve endpoint and a valid action ID."
            ),
        }

    # STEP 5: Unsupported requests
    if intent == "other":
        return {
            "status": "unsupported_request",
            "message": "Please provide a trip planning request.",
        }

    # STEP 6: Merge trip details
    if session_id is not None:
        trip_info = {
            **trip_info,
            **update_trip_details(session_id, trip_info),
        }

    # STEP 7: Validate required fields
    required_fields = (
        "destination",
        "duration_days",
        "budget",
        "currency",
    )

    missing_fields = [
        field
        for field in required_fields
        if trip_info.get(field) is None or trip_info.get(field) == ""
    ]

    if missing_fields:
        return {
            "status": "needs_clarification",
            "message": (
                "Please provide the following information: "
                + ", ".join(missing_fields)
            ),
        }

    # STEP 8: Research Agent
    research = research_trip(user_request)
    print("2. Research Agent finished")

    # STEP 9: Planning Agent
    itinerary = plan_trip(research)
    print("3. Planning Agent finished")

    # STEP 10: Verification Loop
    verification = _verify_itinerary(itinerary)
    print(
        f"4. Verification finished after "
        f"{verification['attempts']} attempt(s)"
    )

    if not verification["approved"]:
        return {
            "status": "needs_revision",
            "message": "The proposed itinerary did not pass verification.",
            "issues": verification["issues"],
        }

    # STEP 11: Request user approval
    return {
        "status": "pending_approval",
        "itinerary": verification["itinerary"],
    }