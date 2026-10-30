"""Real verification and targeted revision integration."""

from __future__ import annotations

from typing import Any

from app.contract import record_agent_tools
from planning.research import ResearchBundle
from researcher.mcp_client import stage
from planning.verifier_agent import VerifierAgentError, python_explanation, run_verifier_agent
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

    try:
        with stage("verifier"):
            verifier_report = run_verifier_agent(
                itinerary=itinerary,
                research_bundle=research_bundle,
                llm=llm,
                budget_report=budget_report,
                planner_report=planner_report,
                hours_lookup=hours_lookup,
                travel_lookup=travel_lookup,
            )
    except VerifierAgentError as error:
        # The verdict is Python's and survives an LLM failure: only the agent's
        # ranking and wording are replaced by Python's (decided_by "python").
        print("Verifier agent failed; Python's ranking is used:", error.rejections)
        verifier_report = python_explanation(error)
    record_agent_tools("verifier", getattr(verifier_report, "agent_tool_calls", None))

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

    with stage("revision"):
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
    record_agent_tools("revision", getattr(revision_report, "agent_tool_calls", None))

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
