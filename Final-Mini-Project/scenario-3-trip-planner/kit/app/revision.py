"""The Coordinator's revision step: a traveller's change request applied to the plan kept in session memory.

    request -> read_request: the Revision Agent's interpretation step (planning/revision_interpreter.py, one LLM
               call, validated by Python against the plan), or Python's rules when there is no usable LLM answer:
               structured operations, or one question, or why not; a budget in another currency converted with a
               convert_currency rate (never assumed)
            -> apply_revision (Python edits; the Revision Agent, a CrewAI crew, chooses among feasible options)
            -> run_verification_pipeline (the Verifier agent, and targeted repair: the same as a new plan)
            -> coverage, density and summaries -> the response, and the session's plan replaced

The session keeps the whole plan (research bundle, Budget Analyst report, Planner report, itinerary, rules) when
it is created (orchestrator.save_plan_state), so a revision needs no new research and no new plan. A request
that names an older version of the plan than the session's (`base_version`) is refused: it would revise a plan
the traveller is no longer looking at. Nothing here marks a plan verified: the status is the verification's.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
import uuid
from typing import Any

from app.contract import record_agent_tools
from planning.revision_agent import _Run, make_decide
from planning.user_revision import (Interpretation, PlanState, RevisionInfeasible, apply_revision,
                                    convert_budget_currency, interpret)

_INDEFINITE = re.compile(r"\b(?:an?|one|some|any)\s+(?:activity|activities|place|places|stop|thing|attraction|"
                         r"sight)\b|\bsomething\b|\bone of (?:them|those|these)\b", re.I)
MAX_DIALOGUE = 8  # turns of the revision chat kept in the session for follow-ups ("do the same for Day 3")


class RevisionNotAvailableError(RuntimeError):
    """No plan to revise in this session (kept for callers that still catch it)."""


MAX_HISTORY = 20


def plan_state_from(bundle, budget_report, planner_report, itinerary_model, rules=None,
                    history: list[dict] | None = None) -> PlanState:
    from planning.schedule import ScheduleRules
    return PlanState(bundle=bundle, budget_report=budget_report, planner_report=planner_report,
                     itinerary=itinerary_model, rules=rules or ScheduleRules.from_trip(bundle.trip),
                     version=uuid.uuid4().hex[:12], history=list(history or [])[-MAX_HISTORY:])


def _names_report(budget_report):
    """The Budget Analyst report with every researched alternative listed, so the Revision Agent's prompt can
    name an excluded place it is offered (names only: the plan is not changed by this copy)."""
    return budget_report.model_copy(update={"optional": list(budget_report.optional) + list(budget_report.excluded)
                                            + list(budget_report.dropped_for_budget)})


def _no_change(status: str, message: str, kind: str, state: PlanState | None,
               interpretation: Interpretation | None = None) -> dict[str, Any]:
    revision = {"outcome": kind, "message": message, "plan_version": state.version if state else None}
    if interpretation is not None:
        revision["interpreted_by"] = interpretation.source
        if interpretation.understood:
            revision["understood"] = list(interpretation.understood)
    return {"status": status, "message": message, "revision": revision,
            "plan_version": state.version if state else None}


def _fetch_rate(from_currency: str, to_currency: str):
    """One convert_currency result (the read-only MCP tool) as an ExchangeRate, or None when it failed."""
    from planning.budget import BudgetError, ExchangeRate
    from researcher.mcp_client import call_read_tool
    result = call_read_tool("convert_currency", {"amount": 1, "from_currency": from_currency,
                                                 "to_currency": to_currency})
    try:
        return ExchangeRate.from_tool_result(result)
    except BudgetError:
        return None


def _agent_reads() -> bool:
    """RIHLA_REVISION_INTERPRETER=rules: requests are read by Python's rules only (no LLM call)."""
    return os.getenv("RIHLA_REVISION_INTERPRETER", "agent").strip().lower() != "rules"


def _interpreter_llm(llm):
    """The LLM for reading the request: the caller's, else the planning LLM when one is configured."""
    if llm is not None or not _agent_reads():
        return llm
    try:
        from app.planning_adapter import create_planning_llm
        return create_planning_llm()
    except Exception:  # no key configured: the rules read the request
        return None


def read_request(session: dict, user_request: str, state: PlanState, llm=None, rate_for=None) -> Interpretation:
    """The traveller's words as operations on this plan (see the module docstring).

    The Revision Agent reads the request with the recent revision dialogue, so a follow-up or an answer to the
    question just asked is understood. When it gives no usable answer the rules read it; an answer to the rules'
    own question is read together with the request it answers."""
    from planning.revision_interpreter import InterpreterError, interpret_with_agent
    dialogue = list(session.get("revision_dialogue") or [])
    agent_error = None
    interpretation = None
    if _agent_reads():
        try:
            from researcher.mcp_client import stage
            with stage("revision_agent"):
                interpretation = interpret_with_agent(user_request, state, llm, dialogue=dialogue)
        except InterpreterError as exc:
            agent_error = str(exc)[:200]
        else:
            # "Move an activity from Day 1 to Day 3": an indefinite target is never resolved by the agent's guess;
            # the rules' question (which lists the candidates) is asked instead
            if (any(op.kind in ("move", "remove", "replace") for op in interpretation.ops)
                    and _INDEFINITE.search(user_request)):
                rules = interpret(user_request, state)
                if rules.clarification:
                    interpretation = rules.model_copy(update={"source": "rules"})
            # the agent called it impossible, but the rules know an edit for it: the edit is tried (Python then
            # says plainly if it cannot be done)
            if interpretation.infeasible:
                rules = interpret(user_request, state)
                if rules.ops:
                    interpretation = rules.model_copy(update={"source": "rules"})
    if interpretation is None:
        interpretation = interpret(user_request, state)
        pending = session.get("revision_pending")
        if interpretation.clarification and pending:
            combined = interpret(f"{pending} {user_request}", state)
            if combined.ops:
                interpretation = combined
        interpretation = interpretation.model_copy(update={"agent_error": agent_error})
    return convert_budget_currency(interpretation, state, rate_for or _fetch_rate)


def _remember_turn(session: dict, request: str, reply: str, interpretation: Interpretation | None) -> None:
    dialogue = list(session.get("revision_dialogue") or [])
    dialogue += [{"role": "traveller", "text": request[:500]}, {"role": "assistant", "text": reply[:500]}]
    session["revision_dialogue"] = dialogue[-MAX_DIALOGUE:]
    if interpretation is not None and interpretation.clarification:
        previous = session.get("revision_pending")
        session["revision_pending"] = f"{previous} {request}"[-500:] if previous else request[:500]
    else:
        session.pop("revision_pending", None)


def run_trip_revision(session: dict, user_request: str, trip_info: dict, *, base_version: str | None = None,
                      llm=None, lookups=None, interpretation: Interpretation | None = None,
                      commit: bool = True) -> dict[str, Any]:
    """Revise the session's plan for `user_request`. Returns the same shape as a new plan's result, with a
    "revision" block (what changed, what stayed, who chose). The session's plan is replaced only on success.

    `interpretation`: structured operations built by the caller (app/whatif.py) instead of reading the request's
    words. `commit=False` previews: the session's plan is left as it is, and the new plan state is returned as
    "_plan_state" for the caller to keep until the traveller applies it (app/whatif.py apply_proposal)."""
    from app import orchestrator as o
    from app.coverage import assess_coverage, assess_density, density_summary
    from app.mcp_lookup_adapter import create_hours_lookup, create_travel_lookup, max_walk_minutes
    from app.planning_adapter import create_planning_llm
    from app.verification_adapter import run_verification_pipeline

    state: PlanState | None = session.get("plan_state")
    if state is None:
        return _no_change("needs_clarification", (
            "There is no plan in this session to revise: it was created in an earlier session or the server was "
            "restarted. Plan the trip again, then ask for changes."), "no_plan", None)
    if base_version and base_version != state.version:
        return _no_change("needs_clarification", (
            "This itinerary is not the latest version of your plan (it was revised since). Reload the latest "
            "version, then ask for the change again."), "stale_plan", state)

    started = time.perf_counter()
    from_words = interpretation is None  # a WHAT IF hands its own operations: no dialogue to remember
    if from_words:
        llm = _interpreter_llm(llm)
        interpretation = read_request(session, user_request, state, llm)
    timings = {"revision_interpret_ms": round((time.perf_counter() - started) * 1000)}

    def unchanged(status: str, message: str, kind: str) -> dict[str, Any]:
        if from_words and commit:
            _remember_turn(session, user_request, message, interpretation)
        return _no_change(status, message, kind, state, interpretation)

    if interpretation.clarification:
        return unchanged("needs_clarification", interpretation.clarification, "clarification")
    if interpretation.infeasible:
        return unchanged("revision_not_feasible", interpretation.infeasible, "not_feasible")
    started = time.perf_counter()

    destination = state.bundle.trip.destination
    new_limit = next((op.minutes for op in interpretation.ops if op.kind in ("walk_limit", "family_friendly")),
                     None)
    walk_limit = new_limit or state.bundle.trip.max_walk_minutes or max_walk_minutes()
    hours_lookup, travel_lookup = lookups or (create_hours_lookup(destination),
                                              create_travel_lookup(destination, mode="foot",
                                                                   ride_after_minutes=walk_limit))
    llm = llm or create_planning_llm()
    run = _Run()
    decide = make_decide(llm, state.bundle, _names_report(state.budget_report), run)
    try:
        revised = apply_revision(user_request, state, interpretation, travel_lookup=travel_lookup, decide=decide)
    except RevisionInfeasible as exc:
        record_agent_tools("revision", run.tool_calls)
        return unchanged("revision_not_feasible", str(exc), "not_feasible")
    record_agent_tools("revision", run.tool_calls)
    timings["revision_edit_ms"] = round((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    verification_result = run_verification_pipeline(
        itinerary=revised.itinerary, research_bundle=revised.bundle, llm=llm, budget_report=revised.budget_report,
        planner_report=revised.planner_report, hours_lookup=hours_lookup, travel_lookup=travel_lookup)
    timings["verification_ms"] = round((time.perf_counter() - started) * 1000)
    final_model = verification_result.get("itinerary_model") or revised.itinerary
    repair = verification_result.get("revision_report")
    final_planner = (repair.result.planner_report if repair is not None and repair.result.planner_report is not None
                     else revised.planner_report)

    coverage = assess_coverage(final_model, revised.bundle)
    if not coverage.sufficient:
        return unchanged("revision_not_feasible",
                         f"That change would leave the trip too thin: {coverage.insufficient_message()} Your "
                         "itinerary has not been changed.", "not_feasible")
    density = assess_density(final_model, revised.bundle, revised.budget_report, final_planner)
    print("\n".join(density_summary(density)))
    summary = o.traveller_summary({**trip_info, "interests": session.get("trip_details", {}).get("interests")
                                   or trip_info.get("interests")}, revised.budget_report, final_planner, final_model)
    from types import SimpleNamespace
    performance = o._performance(timings, {"revision_agent": SimpleNamespace(llm_usage=run.usage) if run.called else None,
                                           "verifier": verification_result.get("verifier_report"),
                                           "revision": repair})
    entry = {"request": user_request, "changes": revised.changes, "at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "previous_version": state.version}
    new_state = plan_state_from(revised.bundle, revised.budget_report, final_planner, final_model, revised.rules,
                                history=state.history + [entry])
    if commit:
        session["plan_state"] = new_state
        session["itinerary"] = final_model.to_trip_store()
        if from_words:
            _remember_turn(session, user_request, "Done: " + " ".join(revised.changes[:6]), interpretation)
    revision = {
        "outcome": "revised", "request": user_request, "understood": interpretation.understood,
        "interpreted_by": interpretation.source, "interpreter_error": interpretation.agent_error,
        "changes": revised.changes, "notes": revised.notes,
        "changed_dates": [str(d) for d in revised.changed_dates],
        "unchanged_dates": [str(d) for d in revised.unchanged_dates],
        "decided_by": revised.decided_by, "decision_points": len(revised.decision_points),
        "agent_error": revised.agent_error,
        "budget_before": None if state.budget_report.budget_total is None else str(state.budget_report.budget_total),
        "budget_after": None if revised.budget_report.budget_total is None else str(revised.budget_report.budget_total),
        "verification_repairs": None if repair is None else repair.result.outcome,
        "previous_version": state.version, "plan_version": new_state.version, "revision_number": len(new_state.history),
    }
    notes = " ".join(filter(None, [coverage.warning(), density.message()])) or None
    verified = verification_result.get("verified") is True
    message = ("Your itinerary has been updated and it passed verification: you can approve it."
               if verified else "Your itinerary has been updated. " + verification_result.get(
                   "message", "It could not be fully verified."))
    preview = {} if commit else {"_plan_state": new_state}
    return {
        **preview,
        "status": "verified" if verified else "verification_required",
        "message": o._with_warning(message, notes),
        "itinerary": final_model.to_trip_store(),
        "verification_result": verification_result,
        "coverage": coverage.as_dict(),
        "plan_quality": density.as_dict(),
        "performance": performance,
        "summary": summary,
        "verification": o.verification_summary(verification_result),
        "budget": o.budget_summary(revised.budget_report),
        "revision": revision,
        "plan_version": new_state.version,
    }
