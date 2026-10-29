from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.verification_loop import verify_with_retries


def test_verification_passes_first_attempt():
    """An approved itinerary should not require repairs."""

    itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Amman Citadel"]}],
    }

    verify_agent = Mock(
        return_value={"approved": True, "issues": []}
    )
    repair_agent = Mock()

    result = verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_agent,
        repair_agent=repair_agent,
    )

    assert result["approved"] is True
    assert result["attempts"] == 1
    assert result["itinerary"] == itinerary

    verify_agent.assert_called_once()
    repair_agent.assert_not_called()


def test_verification_passes_after_repair():
    """A rejected itinerary should be repaired and checked again."""

    itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Closed Museum"]}],
    }

    repaired_itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Amman Citadel"]}],
    }

    verify_agent = Mock(
        side_effect=[
            {
                "approved": False,
                "issues": ["Closed Museum is unavailable"],
            },
            {
                "approved": True,
                "issues": [],
            },
        ]
    )

    repair_agent = Mock(return_value=repaired_itinerary)

    result = verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_agent,
        repair_agent=repair_agent,
        max_attempts=3,
    )

    assert result["approved"] is True
    assert result["attempts"] == 2
    assert result["itinerary"] == repaired_itinerary

    assert verify_agent.call_count == 2
    repair_agent.assert_called_once()


def test_verification_stops_after_max_attempts():
    """The verification loop must never retry indefinitely."""

    itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Closed Museum"]}],
    }

    verify_agent = Mock(
        return_value={
            "approved": False,
            "issues": ["Activity is unavailable"],
        }
    )

    repair_agent = Mock(
        side_effect=lambda plan, issues: deepcopy(plan)
    )

    result = verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_agent,
        repair_agent=repair_agent,
        max_attempts=3,
    )

    assert result["approved"] is False
    assert result["attempts"] == 3

    assert verify_agent.call_count == 3
    assert repair_agent.call_count == 2


def test_verification_without_repair_agent():
    """Without a repair agent, verification stops after rejection."""

    itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Closed Museum"]}],
    }

    verify_agent = Mock(
        return_value={
            "approved": False,
            "issues": ["Activity is unavailable"],
        }
    )

    result = verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_agent,
        repair_agent=None,
        max_attempts=3,
    )

    assert result["approved"] is False
    assert result["attempts"] == 1
    assert result["issues"] == ["Activity is unavailable"]

    verify_agent.assert_called_once()


def test_invalid_max_attempts():
    """The retry limit must be a positive integer."""

    with pytest.raises(ValueError, match="max_attempts"):
        verify_with_retries(
            itinerary={"destination": "Amman"},
            verify_agent=Mock(),
            max_attempts=0,
        )


def test_original_itinerary_is_not_mutated():
    """Verification and repair must not modify the original plan."""

    itinerary = {
        "destination": "Amman",
        "days": [{"day": 1, "activities": ["Closed Museum"]}],
    }

    original = deepcopy(itinerary)

    def verify_agent(plan):
        if "Closed Museum" in plan["days"][0]["activities"]:
            return {
                "approved": False,
                "issues": ["Closed Museum is unavailable"],
            }

        return {"approved": True, "issues": []}

    def repair_agent(plan, issues):
        plan["days"][0]["activities"] = ["Amman Citadel"]
        return plan

    result = verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_agent,
        repair_agent=repair_agent,
    )

    assert result["approved"] is True
    assert itinerary == original
    assert result["itinerary"] != original