from copy import deepcopy

from app.coordinator import understand_trip_request
from app.memory import get_session, update_trip_details
from app.mock_agents import research_trip, plan_trip, verify_trip
from app.planning_adapter import run_planning_pipeline
from app.revision import RevisionNotAvailableError, revise_itinerary
from app.verification_loop import verify_with_retries
from planning.research import ResearchBundle
from app.research_adapter import ResearchInputError, run_research_pipeline

MAX_VERIFICATION_ATTEMPTS = 3


def _verify_itinerary(
    itinerary: dict,
    repair_agent=None,
) -> dict:
    """
    Verify an itinerary using a bounded retry loop.

    The mock verifier remains available for legacy tests.
    It must not be treated as real verification.
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
    research_bundle: ResearchBundle | None = None,
) -> dict:
    """
    Coordinate the trip planning workflow.

    A ResearchBundle can be supplied by the real Research Agent.

    Until a real verifier is connected, real planning results
    must not enter the approval workflow.
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

        # Real verification is not connected yet.
        # Never approve a revision using the mock verifier.
        return {
            "status": "verification_required",
            "message": (
                "The revised itinerary was generated, but real "
                "verification is required before approval."
            ),
            "itinerary": revised_itinerary,
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
        "travellers",
        "start_date",
        "end_date",
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

        # STEP 8: Real Research + Planning Pipeline
    if research_bundle is None:
        try:
            research_bundle = run_research_pipeline(trip_info)
        except ResearchInputError as error:
            return {
                "status": "needs_clarification",
                "message": str(error),
            }
        except Exception:
            return {
                "status": "research_unavailable",
                "message": (
                    "The research service is currently unavailable. "
                    "No itinerary has been approved or saved."
                ),
            }

    if research_bundle is not None:
        requested_destination = trip_info["destination"]
        researched_destination = research_bundle.trip.destination

        if (
            requested_destination.strip().casefold()
            != researched_destination.strip().casefold()
        ):
            return {
                "status": "needs_clarification",
                "message": (
                    "The research destination does not match "
                    "the requested destination."
                ),
            }

        print("2. ResearchBundle received")

        planning_result = run_planning_pipeline(research_bundle)

        print("3. Real Planning Pipeline finished")

        itinerary = planning_result["itinerary"]

        if itinerary is None:
            return {
                "status": "needs_revision",
                "message": (
                    "The Planning Agent could not produce "
                    "a complete itinerary."
                ),
            }

        # The real verifier is not connected yet.
        # Never approve a real itinerary using the mock verifier.
        return {
            "status": "verification_required",
            "message": (
                "The itinerary was generated successfully, "
                "but real verification is required before approval."
            ),
            "itinerary": itinerary,
        }

       # STEP 9: Safety fallback
    # Never approve an itinerary through mock agents.

    return {
        "status": "research_unavailable",
        "message": (
            "The real research and planning pipeline "
            "could not be completed. "
            "No itinerary has been approved or saved."
        ),
    }