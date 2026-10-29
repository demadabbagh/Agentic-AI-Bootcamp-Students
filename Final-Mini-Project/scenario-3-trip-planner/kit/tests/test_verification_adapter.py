from types import SimpleNamespace
from unittest.mock import patch

from app.verification_adapter import run_verification_pipeline


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def test_verifier_pass_skips_revision():
    itinerary = obj(name="original")

    verifier = obj(
        verified=True,
        verification=obj(status="PASS"),
    )

    with patch(
        "app.verification_adapter.run_verifier_agent",
        return_value=verifier,
    ), patch(
        "app.verification_adapter.run_targeted_revision"
    ) as revision_mock:

        result = run_verification_pipeline(
            itinerary=itinerary,
            research_bundle=obj(),
            llm=obj(),
            budget_report=obj(),
        )

    assert result["status"] == "verified"
    assert result["verified"] is True
    assert result["itinerary_model"] is itinerary
    revision_mock.assert_not_called()


def test_failed_verifier_triggers_successful_revision():
    original = obj(name="original")
    corrected = obj(name="corrected")

    verifier = obj(
        verified=False,
        verification=obj(status="FAIL"),
    )

    revision = obj(
        result=obj(
            verified=True,
            itinerary=corrected,
        )
    )

    with patch(
        "app.verification_adapter.run_verifier_agent",
        return_value=verifier,
    ), patch(
        "app.verification_adapter.run_targeted_revision",
        return_value=revision,
    ) as revision_mock:

        result = run_verification_pipeline(
            itinerary=original,
            research_bundle=obj(),
            llm=obj(),
            budget_report=obj(),
        )

    assert revision_mock.call_count == 1
    assert result["status"] == "verified"
    assert result["verified"] is True
    assert result["itinerary_model"] is corrected


def test_failed_revision_blocks_approval():
    original = obj(name="original")

    verifier = obj(
        verified=False,
        verification=obj(status="FAIL"),
    )

    revision = obj(
        result=obj(
            verified=False,
            itinerary=original,
        )
    )

    with patch(
        "app.verification_adapter.run_verifier_agent",
        return_value=verifier,
    ), patch(
        "app.verification_adapter.run_targeted_revision",
        return_value=revision,
    ):

        result = run_verification_pipeline(
            itinerary=original,
            research_bundle=obj(),
            llm=obj(),
            budget_report=obj(),
        )

    assert result["status"] == "needs_revision"
    assert result["verified"] is False


def test_failed_verifier_without_budget_blocks_revision():
    itinerary = obj(name="original")

    verifier = obj(
        verified=False,
        verification=obj(status="FAIL"),
    )

    with patch(
        "app.verification_adapter.run_verifier_agent",
        return_value=verifier,
    ), patch(
        "app.verification_adapter.run_targeted_revision"
    ) as revision_mock:

        result = run_verification_pipeline(
            itinerary=itinerary,
            research_bundle=obj(),
            llm=obj(),
            budget_report=None,
        )

    assert result["status"] == "needs_revision"
    assert result["verified"] is False
    revision_mock.assert_not_called()
