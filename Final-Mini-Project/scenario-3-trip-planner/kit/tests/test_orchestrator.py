from unittest.mock import patch

from app.memory import get_session, sessions
from app.orchestrator import orchestrate_trip


def test_create_trip():
    """Missing trip details must block planning and approval."""

    trip_info = {
        "intent": "create_trip",
        "destination": "Amman",
        "duration_days": 4,
        "budget": 600.0,
        "currency": "USD",
    }

    with (
        patch(
            "app.orchestrator.understand_trip_request",
            return_value=trip_info,
        ),
        patch("app.orchestrator.run_research_pipeline") as research_mock,
        patch("app.orchestrator.run_planning_pipeline") as planning_mock,
        patch("app.orchestrator.research_trip") as mock_research,
        patch("app.orchestrator.plan_trip") as mock_planning,
        patch("app.orchestrator.verify_trip") as mock_verification,
    ):
        result = orchestrate_trip(
            "I want a 4-day trip to Amman with a budget of 600 USD"
        )

        assert result["status"] == "needs_clarification"
        assert "travellers" in result["message"]
        assert "start_date" in result["message"]
        assert "end_date" in result["message"]

        assert "itinerary" not in result

        research_mock.assert_not_called()
        planning_mock.assert_not_called()
        mock_research.assert_not_called()
        mock_planning.assert_not_called()
        mock_verification.assert_not_called()


def test_missing_information():
    """Missing trip details should trigger clarification."""

    trip_info = {
        "intent": "create_trip",
        "destination": "Amman",
        "duration_days": None,
        "budget": None,
        "currency": None,
    }

    with patch(
        "app.orchestrator.understand_trip_request",
        return_value=trip_info,
    ), patch(
        "app.orchestrator.research_trip",
    ) as research_mock:

        result = orchestrate_trip("I want to visit Amman")

        assert result["status"] == "needs_clarification"
        assert "duration_days" in result["message"]
        assert "budget" in result["message"]

        research_mock.assert_not_called()


def test_revision_without_existing_itinerary():
    """
    A revision request without an existing itinerary
    should ask for clarification.
    """

    with patch(
        "app.orchestrator.understand_trip_request",
        return_value={"intent": "revise_trip"},
    ), patch(
        "app.orchestrator.research_trip",
    ) as research_mock:

        result = orchestrate_trip("Change my second day")

        assert result["status"] == "needs_clarification"
        assert "no existing itinerary" in result["message"].lower()

        research_mock.assert_not_called()


def test_revision_agent_not_connected():
    """
    An existing itinerary must remain unchanged
    when the revision agent is unavailable.
    """

    sessions.clear()

    session_id = "revision-agent-test-123"

    original_itinerary = {
        "destination": "Amman",
        "days": [
            {
                "day": 1,
                "activities": ["Amman Citadel"],
            },
            {
                "day": 2,
                "activities": ["Roman Theater"],
            },
        ],
    }

    session = get_session(session_id)
    session["itinerary"] = original_itinerary.copy()

    with patch(
        "app.orchestrator.understand_trip_request",
        return_value={"intent": "revise_trip"},
    ), patch(
        "app.orchestrator.research_trip",
    ) as research_mock:

        result = orchestrate_trip(
            "Change day 2 to include museums",
            session_id=session_id,
        )

        assert result["status"] == "revision_not_supported"

        assert session["itinerary"] == original_itinerary

        research_mock.assert_not_called()


def test_approval_is_not_automatic():
    """Chat approval intent cannot bypass the approval gate."""

    with patch(
        "app.orchestrator.understand_trip_request",
        return_value={"intent": "approve_trip"},
    ), patch(
        "app.orchestrator.research_trip",
    ) as research_mock:

        result = orchestrate_trip("I approve the trip")

        assert result["status"] == "approval_required"

        research_mock.assert_not_called()


def test_trip_memory_across_messages():
    """Trip details should be remembered across messages."""

    sessions.clear()

    session_id = "memory-test-123"

    first_message = {
        "intent": "create_trip",
        "destination": "Amman",
        "duration_days": 4,
        "budget": None,
        "currency": None,
    }

    second_message = {
        "intent": "create_trip",
        "destination": None,
        "duration_days": None,
        "budget": 600,
        "currency": "USD",
    }

    with patch(
        "app.orchestrator.understand_trip_request",
        side_effect=[first_message, second_message],
    ), patch(
        "app.orchestrator.research_trip",
        return_value={"destination": "Amman"},
    ), patch(
        "app.orchestrator.plan_trip",
        return_value={"destination": "Amman", "days": []},
    ), patch(
        "app.orchestrator.verify_trip",
        return_value={"approved": True, "issues": []},
    ):

        result1 = orchestrate_trip(
            "I want a 4-day trip to Amman",
            session_id=session_id,
        )

        assert result1["status"] == "needs_clarification"

        result2 = orchestrate_trip(
            "My budget is 600 USD",
            session_id=session_id,
        )

        assert result2["status"] == "needs_clarification"
        assert "travellers" in result2["message"]
        assert "start_date" in result2["message"]
        assert "end_date" in result2["message"]

        assert sessions[session_id]["trip_details"] == {
            "destination": "Amman",
            "duration_days": 4,
            "budget": 600,
            "currency": "USD",
        }


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))