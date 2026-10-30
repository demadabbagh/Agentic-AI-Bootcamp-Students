import json
import hashlib
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.memory import sessions


client = TestClient(app)


def setup_function():
    sessions.clear()


def make_pending_trip(session_id="test-session"):
    """Create a pending trip for approval tests."""

    itinerary = {
        "destination": "Amman",
        "currency": "USD",
        "budget_total": 600,
        "estimated_total": 450,
        "days": [
            {
                "date": "2026-11-02",
                "activities": [
                    {
                        "name": "Amman Citadel",
                        "start_time": "09:00",
                        "end_time": "11:00",
                        "estimated_cost": 20,
                    }
                ],
            }
        ],
    }

    sessions[session_id] = {
        "itinerary": itinerary,
        "trip_details": {},
        "pending_action": {
            "action_id": "correct-id",
            "type": "save_trip",
            "verification_status": "verified",
            "itinerary_hash": hashlib.sha256(
                json.dumps(
                    itinerary,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "payload": {"itinerary": itinerary},
        },
    }

    return itinerary


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_blocks_unverified_pending_approval():
    mock_result = {
        "status": "pending_approval",
        "itinerary": {
            "destination": "Amman",
            "days": [],
        },
    }

    with patch(
        "app.main.orchestrate_trip",
        return_value=mock_result,
    ), patch("app.main.save_trip_to_store") as save_mock:

        response = client.post(
            "/api/chat",
            json={
                "session_id": "test-session",
                "message": "Plan a trip to Amman",
            },
        )

        save_mock.assert_not_called()

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "verification_required"
    assert data["itinerary"]["destination"] == "Amman"
    assert data["pending_action"] is None
    assert sessions["test-session"]["pending_action"] is None


def test_invalid_approval_is_rejected():
    make_pending_trip()

    with patch("app.main.save_trip_to_store") as save_mock:
        response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "wrong-id",
                "approved": True,
            },
        )

        save_mock.assert_not_called()

    # CONTRACT §3: an unknown action_id gets a clear reply and writes nothing.
    assert response.status_code == 200
    assert response.json()["status"] == "no_pending_action"
    assert response.json()["result"] is None
    assert "nothing" in response.json()["reply"].lower()
    assert sessions["test-session"]["pending_action"] is not None


def test_valid_approval():
    itinerary = make_pending_trip()

    saved_trip = {
        "trip_id": "TRIP-100",
        "destination": "Amman",
        "status": "saved",
    }

    with patch.dict(
        "os.environ",
        {"TRAVEL_TRAVELLER_ID": "TRV-101"},
    ), patch(
        "app.main.save_trip_to_store",
        return_value=saved_trip,
    ) as save_mock:

        response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "correct-id",
                "approved": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        assert response.json()["result"] == {"trip_id": "TRIP-100"}
        assert "TRIP-100" in response.json()["reply"]

        save_mock.assert_called_once_with(
            itinerary=itinerary,
            traveller_id="TRV-101",
            action_id="correct-id",
            title=None,
        )

        assert sessions["test-session"]["pending_action"] is None
        assert sessions["test-session"]["saved_trip"] == saved_trip

        # The same approval again (a double click) answers with the same trip and writes nothing again.
        second_response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "correct-id",
                "approved": True,
            },
        )

        assert second_response.status_code == 200
        assert second_response.json()["result"] == {"trip_id": "TRIP-100"}
        save_mock.assert_called_once()


def test_rejection_does_not_save():
    make_pending_trip()

    with patch("app.main.save_trip_to_store") as save_mock:
        response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "correct-id",
                "approved": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

        save_mock.assert_not_called()

    assert sessions["test-session"]["pending_action"] is None


def test_save_failure_preserves_pending_action():
    from app.trip_store import TripStoreError

    make_pending_trip()

    with patch.dict(
        "os.environ",
        {"TRAVEL_TRAVELLER_ID": "TRV-101"},
    ), patch(
        "app.main.save_trip_to_store",
        side_effect=TripStoreError("Travel API unavailable"),
    ) as save_mock:

        response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "correct-id",
                "approved": True,
            },
        )

        # CONTRACT §3: a failed write is an honest reply with no result; the session continues.
        assert response.status_code == 200
        assert response.json()["status"] == "save_failed"
        assert response.json()["result"] is None
        assert "TRIP-" not in response.json()["reply"]
        assert save_mock.call_count == 1

    assert sessions["test-session"]["pending_action"] is not None
    assert "saved_trip" not in sessions["test-session"]


def test_unsupported_request_preserves_itinerary():
    sessions["test-session"] = {
        "itinerary": {"destination": "Amman"},
        "pending_action": None,
    }

    with patch(
        "app.main.orchestrate_trip",
        return_value={
            "status": "unsupported_request",
            "message": "Unsupported request",
        },
    ):
        response = client.post(
            "/api/chat",
            json={
                "session_id": "test-session",
                "message": "Hello",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported_request"

    assert sessions["test-session"]["itinerary"] == {
        "destination": "Amman"
    }


def test_sessions_are_isolated():
    sessions["session-A"] = {
        "itinerary": {"destination": "Amman"},
        "pending_action": None,
    }

    sessions["session-B"] = {
        "itinerary": {"destination": "Istanbul"},
        "pending_action": None,
    }

    assert (
        sessions["session-A"]["itinerary"]["destination"]
        == "Amman"
    )

    assert (
        sessions["session-B"]["itinerary"]["destination"]
        == "Istanbul"
    )

def test_unverified_trip_cannot_be_saved():
    make_pending_trip()
    sessions["test-session"]["pending_action"]["verification_status"] = "unverified"

    with patch("app.main.save_trip_to_store") as save_mock:
        response = client.post(
            "/api/approve",
            json={
                "session_id": "test-session",
                "action_id": "correct-id",
                "approved": True,
            },
        )

    assert response.status_code == 403
    save_mock.assert_not_called()
    assert sessions["test-session"]["pending_action"] is not None


def test_approval_without_itinerary_hash_is_rejected():
    """A pending action without an itinerary hash must not be saved."""
    from unittest.mock import patch

    make_pending_trip(session_id="missing-hash-session")

    session = sessions["missing-hash-session"]
    session["pending_action"].pop("itinerary_hash", None)

    with patch(
        "app.main.save_trip_to_store"
    ) as save_mock:
        response = client.post(
            "/api/approve",
            json={
                "session_id": "missing-hash-session",
                "action_id": "correct-id",
                "approved": True,
            },
        )

    assert response.status_code == 403
    save_mock.assert_not_called()
