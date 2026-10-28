from app.mock_agents import (
    research_trip,
    plan_trip,
    verify_trip,
)


def orchestrate_trip(user_request: str) -> dict:
    """
    Run the first mock version of the trip planning workflow.
    """

    print("1. Coordinator received the request")

    research = research_trip(user_request)
    print("2. Research Agent finished")

    itinerary = plan_trip(research)
    print("3. Planning Agent finished")

    verification = verify_trip(itinerary)
    print("4. Verification Agent finished")

    if not verification["approved"]:
        return {
            "status": "needs_revision",
            "issues": verification["issues"],
        }

    return {
        "status": "pending_approval",
        "itinerary": itinerary,
    }