"""/api/chat returns only this request's itinerary, and approval needs verification and coverage."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.memory import sessions

client = TestClient(app)

AMMAN = {
    "destination": "Amman", "currency": "USD", "travellers": 2, "budget_total": 2000,
    "estimated_total": 16.92,
    "days": [
        {"date": "2026-11-02", "activities": [
            {"name": "Amman Citadel", "start_time": "09:00", "end_time": "10:30", "estimated_cost": 8.46},
            {"name": "Hashem", "start_time": "13:00", "end_time": "14:00", "estimated_cost": 8.46},
        ]},
        {"date": "2026-11-03", "activities": [
            {"name": "Roman Theater", "start_time": "10:00", "end_time": "11:00", "estimated_cost": None},
        ]},
    ],
}
COVERED = {"trip_days": 2, "nights": 1, "required_days": 1, "food_focused": False, "sufficient": True,
           "covered_dates": ["2026-11-02", "2026-11-03"], "meal_only_dates": [], "empty_dates": [],
           "warning": None}
THIN = {**COVERED, "sufficient": False, "covered_dates": [], "empty_dates": ["2026-11-02", "2026-11-03"]}
PREVIOUS = {"destination": "Istanbul", "days": [{"date": "2026-10-01", "activities": [{"name": "Hagia Sophia"}]}]}


def trusted_verification():
    final = SimpleNamespace(status="PASS", verified=True, evidence_basis="fresh")
    return {"verified": True, "verifier_report": SimpleNamespace(verification=final)}


@pytest.fixture(autouse=True)
def fresh_session():
    sessions.clear()
    sessions["s"] = {"itinerary": PREVIOUS, "pending_action": None, "trip_details": {}}
    yield
    sessions.clear()


def chat(result=None, *, side_effect=None):
    with patch("app.main.orchestrate_trip", return_value=result, side_effect=side_effect):
        return client.post("/api/chat", json={"session_id": "s", "message": "Plan Amman"})


def test_verified_amman_itinerary_with_coverage_awaits_approval():
    response = chat({"status": "verified", "message": "Passed.", "itinerary": AMMAN,
                     "verification_result": trusted_verification(), "coverage": COVERED})
    data = response.json()

    assert data["status"] == "pending_approval"
    assert data["itinerary"]["destination"] == "Amman"
    assert data["pending_action"]["verification_status"] == "verified"
    assert data["coverage"] == COVERED
    assert sessions["s"]["itinerary"]["destination"] == "Amman"


def test_unknown_costs_stay_null_not_zero():
    data = chat({"status": "verified", "itinerary": AMMAN,
                 "verification_result": trusted_verification(), "coverage": COVERED}).json()
    assert data["itinerary"]["days"][1]["activities"][0]["estimated_cost"] is None


@pytest.mark.parametrize("coverage", [None, {"sufficient": None}, "not a dict"])
def test_verified_without_coverage_evidence_is_not_approvable(coverage):
    data = chat({"status": "verified", "itinerary": AMMAN,
                 "verification_result": trusted_verification(), "coverage": coverage}).json()

    assert data["status"] == "verification_required"
    assert data["pending_action"] is None
    assert sessions["s"]["pending_action"] is None
    assert sessions["s"]["itinerary"] == PREVIOUS


def test_thin_coverage_never_returns_an_itinerary_even_if_verified():
    data = chat({"status": "verified", "itinerary": AMMAN,
                 "verification_result": trusted_verification(), "coverage": THIN}).json()

    assert data["status"] == "needs_revision"
    assert data["itinerary"] is None
    assert data["pending_action"] is None
    assert data["coverage"]["sufficient"] is False


@pytest.mark.parametrize("status", ["needs_revision", "needs_clarification", "research_unavailable",
                                    "unsupported_request", "revision_not_supported"])
def test_other_statuses_do_not_return_the_previous_itinerary(status):
    data = chat({"status": status, "message": "No new itinerary."}).json()

    assert data["status"] == status
    assert data["itinerary"] is None
    assert data["message"] == "No new itinerary."
    assert sessions["s"]["itinerary"] == PREVIOUS  # kept for a later revision


def test_low_coverage_needs_revision_from_the_orchestrator():
    data = chat({"status": "needs_revision", "message": "Only 0 of 8 trip days...", "coverage": THIN}).json()
    assert data["status"] == "needs_revision"
    assert data["itinerary"] is None
    assert data["coverage"] == THIN


def test_backend_failure_is_a_503_and_keeps_the_session():
    response = chat(side_effect=RuntimeError("402 Payment Required"))

    assert response.status_code == 503
    assert "402" not in response.text  # provider details stay server-side
    assert sessions["s"]["itinerary"] == PREVIOUS


QUALITY = {"level": "light", "total_non_meal": 5, "message": "Light itinerary: 5 sightseeing activities over 5 days."}


def test_plan_quality_is_returned_with_the_itinerary():
    data = chat({"status": "verified", "itinerary": AMMAN, "verification_result": trusted_verification(),
                 "coverage": COVERED, "plan_quality": QUALITY}).json()
    assert data["status"] == "pending_approval"  # light is advice, not a reason to block approval
    assert data["plan_quality"] == QUALITY


def test_plan_quality_is_dropped_when_there_is_no_itinerary():
    data = chat({"status": "verified", "itinerary": AMMAN, "verification_result": trusted_verification(),
                 "coverage": THIN, "plan_quality": QUALITY}).json()
    assert data["itinerary"] is None and data["plan_quality"] is None


def test_a_503_logs_the_cause_server_side_with_secrets_redacted(caplog, monkeypatch):
    import logging

    from planning.itinerary_planner import ItineraryPlannerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-supersecretvalue123456")
    provider = RuntimeError('OpenrouterException - {"error":{"message":"Insufficient credits","code":402}} '
                            "key=sk-or-v1-supersecretvalue123456 Authorization: Bearer abcdefghijklmnop")
    try:
        raise provider
    except RuntimeError as cause:
        failure = ItineraryPlannerError("The Itinerary Planner gave no valid assignment: boom",
                                        rejections=[{"parse_error": False, "problems": {"closed": 2}}])
        failure.__cause__ = cause

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        response = chat(side_effect=failure)

    assert response.status_code == 503
    error_id = response.json()["detail"].split("error id ")[1].rstrip(")")
    log = caplog.text
    assert f"error id {error_id}" in log
    assert "ItineraryPlannerError" in log and "Insufficient credits" in log and "402" in log
    assert "guardrail rejections: [{'parse_error': False, 'problems': {'closed': 2}}]" in log
    assert "supersecretvalue" not in log and "abcdefghijklmnop" not in log
    assert "Insufficient credits" not in response.text and "supersecret" not in response.text
