"""What a pending action shows the browser, and that nothing the browser says can make a plan approvable.

The dashboard needs verification_status to offer Approve at all: when the response model dropped it, every eligible
plan lost its button while the unit tests of each side still passed. These tests hold the two sides together, through
the real ChatResponse: the frontend's fields arrive, internal fields do not, and /api/approve trusts only the session.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory import sessions
from app.schemas import ChatResponse

client = TestClient(app)
ITINERARY = {"destination": "Istanbul", "currency": "USD", "travellers": 1, "budget_total": 300, "estimated_total": 25,
             "days": [{"date": "2026-11-05", "activities": [{"name": "Hagia Sophia", "start_time": "09:00",
                                                             "end_time": "11:00", "estimated_cost": 25}]}]}
COVERED = {"trip_days": 1, "sufficient": True, "covered_dates": ["2026-11-05"], "meal_only_dates": [], "empty_dates": []}


def planned(status, basis, verified=False):
    final = SimpleNamespace(status=status, evidence_basis=basis, verified=verified, checks=[])
    return {"status": "verification_required", "message": "Checked.", "itinerary": deepcopy(ITINERARY),
            "coverage": COVERED, "verification_result": {"verified": verified,
                                                         "verifier_report": SimpleNamespace(verification=final)},
            "verification": {"status": status, "verified": verified, "evidence_basis": basis, "findings": []}}


def chat(session_id, result):
    sessions.clear()
    with patch("app.main.orchestrate_trip", return_value=result):
        r = client.post("/api/chat", json={"session_id": session_id, "message": "Plan a day in Istanbul",
                                           "user": {"traveller_id": "TRV-101"}})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("basis, verified, status, approval_basis", [
    ("fresh", True, "verified", "live"),
    ("recorded", False, "verified_on_records", "recorded"),
    ("mixed", False, "verified_on_records", "recorded"),
])
def test_an_eligible_plan_reaches_the_browser_with_what_the_dashboard_reads(basis, verified, status, approval_basis):
    action = chat("s-ok", planned("PASS", basis, verified))["pending_action"]
    assert action["verification_status"] == status and action["approval_basis"] == approval_basis
    assert action["evidence_basis"] == basis
    assert (action["disclosure"] is None) is (approval_basis == "live")


def test_only_the_contract_fields_and_the_declared_display_fields_leave_the_server():
    action = chat("s-public", planned("PASS", "recorded"))["pending_action"]
    assert set(action) == {"action_id", "type", "summary", "payload", "verification_status", "evidence_basis",
                           "approval_basis", "disclosure"}
    assert "itinerary_hash" in sessions["s-public"]["pending_action"]  # it stays in the session


@pytest.mark.parametrize("status", ["UNKNOWN", "FAIL"])
def test_a_plan_that_did_not_pass_gets_no_approval(status):
    body = chat("s-no", planned(status, "recorded"))
    assert body["pending_action"] is None and body["status"] != "pending_approval"
    assert sessions["s-no"]["pending_action"] is None


@pytest.mark.parametrize("claim", ["unverified", "UNKNOWN", "approved", "PASS", ""])
def test_the_response_can_never_claim_an_unapprovable_status(claim):
    with pytest.raises(ValueError):
        ChatResponse(session_id="s", status="pending_approval", pending_action={
            "action_id": "a", "type": "save_trip", "summary": "s", "payload": {}, "verification_status": claim})


def test_the_browser_cannot_vouch_for_a_plan():
    chat("s-client", planned("UNKNOWN", "recorded"))
    r = client.post("/api/approve", json={"session_id": "s-client", "action_id": "made-up", "approved": True,
                                          "verification_status": "verified"})
    assert r.status_code == 422  # ApproveRequest forbids extra fields: a client cannot send a status at all
    r = client.post("/api/approve", json={"session_id": "s-client", "action_id": "made-up", "approved": True})
    assert r.json()["status"] == "no_pending_action" and r.json()["result"] is None


def test_a_tampered_session_action_is_refused_whatever_the_response_said(monkeypatch):
    action = chat("s-tamper", planned("PASS", "recorded"))["pending_action"]
    sessions["s-tamper"]["pending_action"]["verification_status"] = "unverified"
    with patch("app.main.save_trip_to_store") as save:
        r = client.post("/api/approve", json={"session_id": "s-tamper", "action_id": action["action_id"],
                                              "approved": True})
    assert r.status_code == 403
    save.assert_not_called()
