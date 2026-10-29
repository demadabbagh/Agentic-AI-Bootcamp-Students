"""Real verification and targeted revision integration."""

from __future__ import annotations

from typing import Any

from planning.research import ResearchBundle
from planning.verifier_agent import run_verifier_agent
from planning.revision_agent import run_targeted_revision


def run_verification_pipeline(
    itinerary: Any,
    research_bundle: ResearchBundle,
    llm: Any,
    *,
    budget_report: Any = None,
    planner_report: Any = None,
    hours_lookup=None,
    travel_lookup=None,
) -> dict:
    """Verify a planned itinerary and attempt targeted repair if needed."""

    if itinerary is None:
        return {
            "status": "verification_unavailable",
            "verified": False,
            "message": "No itinerary was provided for verification.",
            "itinerary_model": None,
        }

    verifier_report = run_verifier_agent(
        itinerary=itinerary,
        research_bundle=research_bundle,
        llm=llm,
        budget_report=budget_report,
        planner_report=planner_report,
        hours_lookup=hours_lookup,
        travel_lookup=travel_lookup,
    )

    if verifier_report.verified:
        return {
            "status": "verified",
            "verified": True,
            "message": "The itinerary passed real verification.",
            "itinerary_model": itinerary,
            "verifier_report": verifier_report,
            "revision_report": None,
        }

    if budget_report is None:
        return {
            "status": "needs_revision",
            "verified": False,
            "message": "Verification did not pass, and no budget report is available for revision.",
            "itinerary_model": itinerary,
            "verifier_report": verifier_report,
            "revision_report": None,
        }

    revision_report = run_targeted_revision(
        itinerary=itinerary,
        bundle=research_bundle,
        budget_report=budget_report,
        llm=llm,
        verification=verifier_report.verification,
        planner_report=planner_report,
        travel_lookup=travel_lookup,
        hours_lookup=hours_lookup,
    )

    result = revision_report.result

    if result.verified and result.itinerary is not None:
        return {
            "status": "verified",
            "verified": True,
            "message": "The itinerary passed verification after targeted revision.",
            "itinerary_model": result.itinerary,
            "verifier_report": verifier_report,
            "revision_report": revision_report,
        }

    return {
        "status": "needs_revision",
        "verified": False,
        "message": "The itinerary could not be fully verified. No approval is allowed.",
        "itinerary_model": result.itinerary or itinerary,
        "verifier_report": verifier_report,
        "revision_report": revision_report,
    }
