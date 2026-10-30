"""The /api/chat contract: structured errors, the machine-readable verification report and budget. No LLM."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.diagnostics import classify_error
from app.main import app
from app.memory import sessions
from app.orchestrator import verification_summary

client = TestClient(app)


def setup_function():
    sessions.clear()


class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APIStatusError(Exception):
    def __init__(self, status_code):
        super().__init__("provider")
        self.status_code = status_code


def wrapped(inner):
    """CrewAI wraps the provider's error: the classification must look through the cause chain."""
    try:
        try:
            raise inner
        except Exception as exc:
            raise RuntimeError("Task failed") from exc
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize("exc, status, code, retryable", [
    (wrapped(APITimeoutError()), 504, "llm_timeout", True),
    (wrapped(RateLimitError()), 429, "llm_rate_limited", True),
    (wrapped(APIStatusError(402)), 402, "llm_payment_required", False),
    (ValueError("boom"), 503, "internal_error", True),
])
def test_errors_are_classified(exc, status, code, retryable):
    assert classify_error(exc)[0::1][:2] == (status, code) and classify_error(exc)[3] is retryable


def test_an_error_response_keeps_the_detail_string_and_adds_the_structured_error():
    with patch("app.main.orchestrate_trip", side_effect=wrapped(APITimeoutError())):
        r = client.post("/api/chat", json={"session_id": "e1", "message": "Plan a trip"})
    body = r.json()
    assert r.status_code == 504 and isinstance(body["detail"], str) and "error id" in body["detail"]
    assert body["error"]["code"] == "llm_timeout" and body["error"]["retryable"] is True
    assert "sk-" not in r.text  # never a key, never a traceback


def issue(code, severity, check="opening_hours"):
    return SimpleNamespace(code=code, severity=severity, check=check, date=None, place_id="p", related_place_id=None,
                           explanation="x", recommended_action="none")


def report(status, verified, issues=(), unknowns=(), warnings=()):
    return SimpleNamespace(schema_version="v", status=status, verified=verified, evidence_basis="recorded",
                           checks=[SimpleNamespace(check="travel", status=status)], issues=list(issues),
                           unknowns=list(unknowns), warnings=list(warnings), tool_evidence=[{}, {}])


@pytest.mark.parametrize("status, verified, state", [("PASS", True, "confirmed_valid"),
                                                     ("PASS", False, "supported_by_records"),
                                                     ("UNKNOWN", False, "unverifiable"),
                                                     ("FAIL", False, "confirmed_problem")])
def test_the_verification_report_names_the_three_situations(status, verified, state):
    r = report(status, verified, issues=[issue("closed_on_date", "error")], unknowns=[issue("hours_unknown", "unknown")],
               warnings=[issue("ride_planned", "warning", "travel")])
    out = verification_summary({"verifier_report": SimpleNamespace(verification=r, decided_by="agent"),
                                "revision_report": None})
    assert out["state"] == state and out["verified"] is verified
    assert out["counts"] == {"confirmed_problems": 1, "unverifiable": 1, "advice": 1}
    assert [f["state"] for f in out["findings"]] == ["confirmed_problem", "unverifiable", "advice"]
    assert out["fresh_lookups"] == 2


def test_the_final_report_after_a_revision_is_the_one_shown():
    first, final = report("FAIL", False, issues=[issue("closed_on_date", "error")]), report("UNKNOWN", False)
    revision = SimpleNamespace(result=SimpleNamespace(final=final, decided_by="python", attempts=[1, 2]))
    out = verification_summary({"verifier_report": SimpleNamespace(verification=first, decided_by="agent"),
                                "revision_report": revision})
    assert out["status"] == "UNKNOWN" and out["revision"] == {"decided_by": "python", "attempts": 2}


def test_no_verification_no_report():
    assert verification_summary(None) is None and verification_summary({"verifier_report": None}) is None
