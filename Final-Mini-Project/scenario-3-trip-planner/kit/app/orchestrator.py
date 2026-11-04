import os
import time
from copy import deepcopy

from app import refinement
from planning.budget_analyst import not_plannable, prepare_candidates
from app.coordinator import understand_trip_request
from app.memory import get_session, update_trip_details
from app.mock_agents import research_trip, plan_trip, verify_trip
from app.planning_adapter import run_planning_pipeline, create_planning_llm
from app.mcp_lookup_adapter import create_hours_lookup, create_travel_lookup, max_walk_minutes
from app.verification_adapter import run_verification_pipeline
from app.revision import plan_state_from, run_trip_revision
from app.verification_loop import verify_with_retries
from planning.research import ResearchBundle
from app.research_adapter import ResearchInputError, run_research_pipeline
from app.traveller_memory import apply_preferences
from planning.evidence import checkable_bundle
from researcher.mcp_client import stage
from app.diagnostics import budget_decisions, performance_summary, research_summary
from app.research_errors import attraction_failure_message
from app.coverage import (
    assess_coverage,
    assess_density,
    density_summary,
    assess_store_coverage,
    coverage_context,
    coverage_summary,
    duration_problem,
    refreshed_duration,
)

MAX_VERIFICATION_ATTEMPTS = 3

# USD per million tokens, for an ESTIMATE from CrewAI's token counts (the provider's bill is the authority).
MODEL_PRICES = {"openai/gpt-4.1-mini": (0.40, 1.60)}


# The fast path's time budget, shared by its steps: a slow source costs a checked fact (it stays unknown and says so),
# never the answer. What is left of the 10 seconds is for the network and the page.
FAST_TOTAL_SECONDS = 7.0
FAST_EVIDENCE_SECONDS = 3.0
FAST_VERIFY_SECONDS = 1.0  # kept back from the planner, so the plan is always checked


def fast_planning() -> bool:
    return os.getenv("RIHLA_FAST_PLANNING", "on").strip().lower() not in {"0", "off", "false", "no"}


def _form_details(form: dict) -> dict:
    """Trip details from the structured trip form, exactly as entered."""
    start, end = form["start_date"], form["end_date"]
    import datetime as dt
    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
    return {
        "destination": form["destination"],
        "start_date": start,
        "end_date": end,
        "duration_days": days,
        "travellers": form["travellers"],
        "budget": form["budget"],
        "currency": form.get("currency", "USD").upper(),
        "interests": list(form.get("interests") or []),
    }


def traveller_summary(trip_info: dict, budget_report, planner_report, final_model) -> dict:
    """What the dashboard shows beside the itinerary, in Python's words: how each interest the traveller selected
    is represented by the places actually scheduled, and the plan's limitations. Nothing here is the LLM's."""
    from app.research_adapter import normalize_interests
    from planning.budget_analyst import SCOPE_NOTE

    scheduled = set()
    model = final_model if final_model is not None else getattr(planner_report, "itinerary", None)
    for day in getattr(model, "days", None) or []:
        scheduled |= {a.place_id for a in day.activities if a.place_id}
    items = {i.place_id: i for i in budget_report.selected} if budget_report else {}
    coverage = []
    for label in trip_info.get("interests") or []:
        tags = set(normalize_interests([label]).tags)
        places = [items[i].name for i in items if i in scheduled and tags & set(items[i].serves)
                  and items[i].category == "attraction"]
        # A planned restaurant serves food (and only food): counted for that interest, said to be a meal.
        meals = [items[i].name for i in items if i in scheduled and "food" in tags and "food" in items[i].serves
                 and items[i].category == "restaurant"]
        coverage.append({"interest": label, "supported": bool(tags), "scheduled": len(places) + len(meals),
                         "places": places, "meals": meals})
    limitations = []
    for line in (budget_report.warnings if budget_report else []):
        if line.startswith(("No planned place serves", "No usable price")) or line == SCOPE_NOTE:
            limitations.append(line)
    if planner_report is not None:
        limitations += [f"Not scheduled: {u.name} ({u.reason})." for u in planner_report.unscheduled]
        long_walks = [q.message for q in planner_report.quality if q.code == "long_walk"]
        if long_walks:  # walking is the only travel mode planned: say where it is unrealistic
            limitations.append(f"{len(long_walks)} walk(s) between stops take longer than 30 minutes (walking is the "
                               "only travel mode this plan measures; public transport may be faster): "
                               + " ".join(long_walks[:4]))
        if planner_report.decided_by == "python_fallback":
            limitations.append("The planning agent gave no usable answer; this day plan was built by Python's own "
                               "deterministic planner and checked the same way.")
    return {"interest_coverage": coverage, "limitations": limitations,
            "planned_by": getattr(planner_report, "decided_by", None),
            "days": [{"date": str(d.date), "activities": [
                {"name": a.name, "kind": a.kind, "start": a.start_time, "end": a.end_time,
                 "meal": a.kind in ("eat", "drink"), "travel_minutes": a.travel_minutes_from_previous,
                 "travel_mode": a.travel_mode_from_previous,
                 "walk_minutes": a.travel_minutes_from_previous if a.travel_mode_from_previous != "driving" else None}
                for a in d.activities]} for d in getattr(model, "days", None) or []],
            "sightseeing_per_day": [sum(a.kind not in ("eat", "drink") for a in d.activities)
                                    for d in getattr(model, "days", None) or []],
            "meals_per_day": [sum(a.kind in ("eat", "drink") for a in d.activities)
                              for d in getattr(model, "days", None) or []]}


VERIFICATION_STATES = {
    "error": "confirmed_problem",  # an actual contradiction or an invalid plan: must be repaired
    "unknown": "unverifiable",  # evidence is missing: disclosed, never guessed
    "warning": "advice",  # true, but no change is needed
}


def verification_summary(verification_result: dict | None) -> dict | None:
    """The final verification as data the dashboard can show: Python's verdict, never an LLM's.

    `state` is confirmed_valid only for PASS on fresh evidence; PASS on recorded data is supported_by_records;
    UNKNOWN is unverifiable; FAIL is confirmed_problem. Each finding keeps its code, check, date, place and the
    recommended action, and says whether it is a confirmed problem, unverifiable or advice."""
    if not isinstance(verification_result, dict):
        return None
    revision = verification_result.get("revision_report")
    verifier = verification_result.get("verifier_report")
    report = (revision.result.final if revision is not None and getattr(revision.result, "final", None) is not None
              else getattr(verifier, "verification", None))
    if report is None:
        return None
    state = ("confirmed_valid" if report.status == "PASS" and report.verified else
             "supported_by_records" if report.status == "PASS" else
             "confirmed_problem" if report.status == "FAIL" else "unverifiable")
    findings = [{"code": i.code, "state": VERIFICATION_STATES[i.severity], "check": i.check,
                 "date": str(i.date) if i.date else None, "place_id": i.place_id,
                 "related_place_id": i.related_place_id, "explanation": i.explanation,
                 "recommended_action": i.recommended_action}
                for i in report.issues + report.unknowns + report.warnings]
    return {
        "schema_version": report.schema_version, "status": report.status, "verified": report.verified,
        "state": state, "evidence_basis": report.evidence_basis,
        "counts": {"confirmed_problems": len(report.issues), "unverifiable": len(report.unknowns),
                   "advice": len(report.warnings)},
        "checks": [{"check": c.check, "status": c.status} for c in report.checks],
        "findings": findings,
        "explained_by": getattr(verifier, "decided_by", None),
        "revision": None if revision is None else {
            "decided_by": revision.result.decided_by, "attempts": len(revision.result.attempts)
            if hasattr(revision.result, "attempts") else None},
        "fresh_lookups": len(report.tool_evidence),
    }


def in_plan(budget: dict | None, itinerary: dict | None) -> dict | None:
    """The budget was worked out before verification: a place the repair then removed is no longer a planned item."""
    if not budget or not itinerary:
        return budget
    names = {a.get("name") for d in itinerary.get("days") or [] for a in d.get("activities") or []}
    return {**budget, "unknown_price_items": [n for n in budget.get("unknown_price_items") or [] if n in names]}


def budget_summary(budget_report) -> dict | None:
    """The Budget Analyst's money picture and interest coverage, in Python's numbers."""
    if budget_report is None:
        return None
    return {**(budget_report.breakdown or {}), "interest_coverage": dict(budget_report.interest_coverage),
            "promotions": list(budget_report.promotions), "warnings": list(budget_report.warnings)}


def _usage(report) -> dict:
    usage = getattr(report, "llm_usage", None) or {}
    return {k: int(usage.get(k) or 0) for k in ("successful_requests", "prompt_tokens", "completion_tokens")}


def _performance(timings: dict, reports: dict) -> dict:
    """Measured per-stage times and the LLM calls and tokens CrewAI counted, with an estimated cost.

    The agents share one LLM object and CrewAI's counters on it are cumulative, so each report carries the
    totals so far: a stage's own use is the difference from the stage before (in pipeline order)."""
    import os
    keys = ("successful_requests", "prompt_tokens", "completion_tokens")
    stages, before = {}, {k: 0 for k in keys}
    for name, r in reports.items():
        if r is None:
            continue
        now = _usage(r)
        if not any(now.values()):  # no LLM call in this stage (e.g. nothing to revise)
            stages[name] = {k: 0 for k in keys}
            continue
        stages[name] = {k: max(0, now[k] - before[k]) for k in keys}
        before = {k: max(before[k], now[k]) for k in keys}
    total = dict(before)
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    price = MODEL_PRICES.get(model)
    cost = (None if price is None else
            round(total["prompt_tokens"] / 1e6 * price[0] + total["completion_tokens"] / 1e6 * price[1], 5))
    rejections = {name: [k for r in (getattr(rep, "guardrail_rejections", None)
                                     or (getattr(rep, "performance", None) or {}).get("rejections") or [])
                         for k in ((r.get("problems") or {"parse_error": 1}) if isinstance(r, dict) else [r])]
                  for name, rep in reports.items() if rep is not None}
    return {"timings_ms": dict(timings), "llm": stages, "llm_total": total, "model": model,
            "guardrail_rejections": rejections,
            "estimated_cost_usd": cost,
            "note": "Estimated from CrewAI's token counts (cached prompts are billed less); the provider's "
                    "bill is the authority. The Coordinator's LLM, when used, is not counted."}


def _with_warning(message: str, warning: str | None) -> str:
    return f"{message} {warning}" if warning else message


def _verify_itinerary(
    itinerary: dict,
    repair_agent=None,
) -> dict:
    """
    Verify an itinerary using a bounded retry loop.

    The mock verifier remains available for legacy tests.
    It must not be treated as real verification.
    """
    return verify_with_retries(
        itinerary=itinerary,
        verify_agent=verify_trip,
        repair_agent=repair_agent,
        max_attempts=MAX_VERIFICATION_ATTEMPTS,
    )


def orchestrate_trip(
    user_request: str,
    session_id: str | None = None,
    research_bundle: ResearchBundle | None = None,
    trip_form: dict | None = None,
    base_version: str | None = None,
    revision_request: bool = False,
    preferences: dict | None = None,
) -> dict:
    """
    Coordinate the trip planning workflow.

    `preferences` are the identified traveller's saved preferences (app/traveller_memory.py). They fill only
    the trip details the traveller did not give; what was used is returned as "preferences_used".
    """
    used: list[str] = []
    result = _orchestrate_trip(user_request, session_id, research_bundle, trip_form, base_version,
                               revision_request, preferences, used)
    if used:
        result["preferences_used"] = used
    return result


def _plan_and_verify(trip_info: dict, research_bundle: ResearchBundle, evidence_notes: list[str], session: dict | None,
                     timings: dict, llm, hours_lookup, travel_lookup, *, use_agents: bool = True,
                     commit: bool = True, deadline: float | None = None) -> dict:
    """Plan from the research, verify, and build the result. The one path every plan takes.

    use_agents=False: Python's selection and day plan, no LLM; the verification is the same, with lookups bounded in
    time, up to `deadline` (time.monotonic()). commit=False: the session is left as it is and the plan state is returned
    as "_plan_state" (the AI review)."""
    def left() -> float:
        return max(0.0, deadline - time.monotonic()) if deadline is not None else FAST_VERIFY_SECONDS

    planning_result = run_planning_pipeline(
        research_bundle,
        llm=llm,
        travel_lookup=travel_lookup,
        **({} if use_agents else {"use_agents": False, "lookup_seconds": max(0.0, left() - FAST_VERIFY_SECONDS)}),
    )

    print("3. Real Planning Pipeline finished")
    timings["budget_analyst_ms"] = (planning_result.get("timings") or {}).get("budget_analyst_ms")
    timings["planner_ms"] = (planning_result.get("timings") or {}).get("planner_ms")

    budget_report = planning_result["budget_report"]
    print("=== BUDGET DIAGNOSTICS ===")
    print("Selected:", len(budget_report.selected))
    print("Optional:", len(budget_report.optional))
    print("Excluded:", len(budget_report.excluded))
    print("Withheld:", len(budget_report.withheld))
    print("Withheld reasons:", [(x.name, x.reason) for x in budget_report.withheld])
    print("Budget warnings:", budget_report.warnings)
    print("Budget guardrail rejections:", (budget_report.performance or {}).get("rejection_details"),
          "| filled by Python:", (budget_report.performance or {}).get("filled_missing"),
          "| promotions:", budget_report.promotions)
    print("==========================")
    print("\n".join(budget_decisions(budget_report)))
    print("\n".join(performance_summary(
        budget_report,
        planning_result.get("planner_report"),
        planning_result.get("timings"),
    )))


    planner_report = planning_result["planner_report"]

    print("=== PLANNING DIAGNOSTICS ===")
    print("Planner status:", planner_report.status)
    print("Scheduled places:", len(planner_report.scheduled_place_ids))
    print("Unscheduled places:", len(planner_report.unscheduled))
    print("Warnings:", planner_report.warnings)
    print("============================")

    itinerary = planning_result["itinerary"]

    if itinerary is None:
        return {
            "status": "needs_revision",
            "message": (
                "The Planning Agent could not produce "
                "a complete itinerary."
            ),
        }

    # A valid schedule can still be too thin for the trip's length:
    # stop before verification rather than offer it for approval.
    coverage = assess_coverage(
        planning_result["itinerary_model"], research_bundle
    )
    print("\n".join(coverage_summary(coverage)))

    if not coverage.sufficient:
        return {
            "status": "needs_revision",
            "message": coverage.insufficient_message(),
            "coverage": coverage.as_dict(),
        }

    if session is not None and commit:
        session["coverage_context"] = coverage_context(research_bundle)

    started = time.perf_counter()
    verification_result = run_verification_pipeline(
        itinerary=planning_result["itinerary_model"],
        research_bundle=research_bundle,
        llm=llm,
        budget_report=planning_result["budget_report"],
        planner_report=planning_result["planner_report"],
        hours_lookup=hours_lookup,
        travel_lookup=travel_lookup,
        **({} if use_agents else {"lookup_seconds": max(0.5, left())}),
    )

    timings["verification_ms"] = round((time.perf_counter() - started) * 1000)
    performance = _performance(timings, {
        "budget_analyst": planning_result.get("budget_report"),
        "planner": planning_result.get("planner_report"),
        "verifier": verification_result.get("verifier_report"),
        "revision": verification_result.get("revision_report"),
    })
    performance["planned_by"] = "agents" if use_agents else "python"
    print("=== STAGE PERFORMANCE ===")
    print(performance)
    print("=========================")
    final_model = verification_result.get("itinerary_model")
    final_itinerary = (
        final_model.to_trip_store()
        if final_model is not None
        else itinerary
    )

    # A revision during verification may have changed the plan.
    if final_model is not None:
        coverage = assess_coverage(final_model, research_bundle)

        if not coverage.sufficient:
            return {
                "status": "needs_revision",
                "message": coverage.insufficient_message(),
                "coverage": coverage.as_dict(),
            }

    # Valid and covered is not the same as full: label a light plan
    # and say why. Advice only; it never changes the status.
    density = assess_density(
        final_model or planning_result["itinerary_model"],
        research_bundle,
        planning_result["budget_report"],
        planning_result.get("planner_report"),
    )
    print("\n".join(density_summary(density)))

    notes = " ".join(filter(None, [coverage.warning(), density.message()])) or None
    summary = traveller_summary(trip_info, planning_result["budget_report"],
                                planning_result.get("planner_report"), final_model)
    summary["limitations"] = list(summary.get("limitations") or []) + evidence_notes

    # Session memory for later revisions: the whole plan, so a change needs no new research or plan.
    plan_version = None
    state = None
    if session is not None:
        repair = verification_result.get("revision_report")
        kept_planner = (repair.result.planner_report
                        if repair is not None and repair.result.planner_report is not None
                        else planning_result["planner_report"])
        try:
            state = plan_state_from(research_bundle, planning_result["budget_report"], kept_planner,
                                    final_model or planning_result["itinerary_model"])
        except Exception as exc:  # the plan stands; only later revisions are unavailable (said when asked)
            print("Plan not kept for revisions:", type(exc).__name__)
            state = None
            if commit:
                session.pop("plan_state", None)
        else:
            if commit:
                session["plan_state"] = state
            plan_version = state.version
    print("=== TRAVELLER SUMMARY ===")
    print(summary)
    print("=========================")

    if verification_result.get("verified") is True:
        return {
            "status": "verified",
            "message": _with_warning(
                "The itinerary passed real verification "
                "and is awaiting user approval.",
                notes,
            ),
            "itinerary": final_itinerary,
            "verification_result": verification_result,
            "coverage": coverage.as_dict(),
            "plan_quality": density.as_dict(),
            "performance": performance,
            "summary": summary,
            "verification": verification_summary(verification_result),
            "budget": budget_summary(planning_result["budget_report"]),
            "plan_version": plan_version,
            **({} if commit else {"_plan_state": state}),
        }

    return {
        "status": "verification_required",
        "message": _with_warning(
            verification_result.get(
                "message",
                "The itinerary could not be fully verified.",
            ),
            notes,
        ),
        "itinerary": final_itinerary,
        "verification_result": verification_result,
        "coverage": coverage.as_dict(),
        "plan_quality": density.as_dict(),
        "performance": performance,
        "summary": summary,
        "verification": verification_summary(verification_result),
        "budget": in_plan(budget_summary(planning_result["budget_report"]), final_itinerary),
        "plan_version": plan_version,
        **({} if commit else {"_plan_state": state}),
    }


def _orchestrate_trip(
    user_request: str,
    session_id: str | None,
    research_bundle: ResearchBundle | None,
    trip_form: dict | None,
    base_version: str | None,
    revision_request: bool,
    preferences: dict | None,
    preferences_used: list[str],
) -> dict:
    """
    Coordinate the trip planning workflow.

    A ResearchBundle can be supplied by the real Research Agent.

    Until a real verifier is connected, real planning results
    must not enter the approval workflow.
    """

    # STEP 1: Load session memory
    previous_trip_details = {}
    session = None

    if session_id is not None:
        session = get_session(session_id)
        previous_trip_details = session.get("trip_details", {}).copy()

    # The dashboard's change box always edits this session's current plan. Do not spend a
    # Coordinator call classifying it or let a missing plan fall through to new-trip planning.
    if revision_request:
        if session is None:
            return {
                "status": "needs_clarification",
                "message": "There is no existing itinerary to revise in this session. Plan a trip first, then ask "
                           "for changes.",
                "revision": {"outcome": "no_plan"},
            }
        return run_trip_revision(session, user_request, previous_trip_details, base_version=base_version)

    # STEP 2: Coordinator Agent. The structured form's own fields are used as
    # entered (the exact interests the traveller selected); the LLM reads only
    # free-text preferences, and is not called when there are none.
    timings: dict[str, int] = {}
    started = time.perf_counter()
    if trip_form is not None and not (trip_form.get("preferences") or "").strip():
        trip_info = {"intent": "create_trip", **_form_details(trip_form)}
        print("1. Coordinator: structured trip form, no free-text preferences (no LLM call)")
    else:
        trip_info = understand_trip_request(
            user_request,
            previous_trip_details=previous_trip_details,
        )
        if trip_form is not None:
            form = _form_details(trip_form)
            extra = [i for i in (trip_info.get("interests") or []) if i not in form["interests"]]
            trip_info = {**trip_info, **form, "intent": "create_trip",
                         "interests": form["interests"] + extra}
    timings["coordinator_ms"] = round((time.perf_counter() - started) * 1000)

    intent = trip_info["intent"]
    print(f"1. Coordinator detected intent: {intent}")
    # STEP 3: Handle revision requests: the plan kept in this session is revised in place (targeted changes,
    # the Revision Agent for the choices, then the same verification as a new plan). See app/revision.py.
    if intent == "revise_trip":
        if session is None or session.get("plan_state") is None:
            return {
                "status": "needs_clarification",
                "message": (
                    "There is no existing itinerary to revise in this session. Plan a trip first, then ask "
                    "for changes."
                ),
                "revision": {"outcome": "no_plan"},
            }
        return run_trip_revision(session, user_request, previous_trip_details, base_version=base_version)

    # STEP 4: Approval must use /api/approve
    if intent == "approve_trip":
        return {
            "status": "approval_required",
            "message": (
                "Trip approval must be completed using "
                "the /api/approve endpoint and a valid action ID."
            ),
        }

    # STEP 5: Unsupported requests
    if intent == "other":
        return {
            "status": "unsupported_request",
            "message": "Please provide a trip planning request.",
        }

    # STEP 6: Merge trip details
    if session_id is not None:
        trip_info = {
            **trip_info,
            **update_trip_details(session_id, trip_info),
        }

        # New dates make a remembered duration_days stale.
        duration = refreshed_duration(previous_trip_details, trip_info)
        if duration is not None:
            trip_info["duration_days"] = duration
            update_trip_details(session_id, {"duration_days": duration})

    # STEP 6b: a returning traveller's saved preferences fill what this request left out
    preferences_used.extend(apply_preferences(trip_info, preferences))

    # STEP 7: Validate required fields
    required_fields = (
        "destination",
        "duration_days",
        "budget",
        "currency",
        "travellers",
        "start_date",
        "end_date",
    )

    missing_fields = [
        field
        for field in required_fields
        if trip_info.get(field) is None or trip_info.get(field) == ""
    ]

    if missing_fields:
        return {
            "status": "needs_clarification",
            "message": (
                "Please provide the following information: "
                + ", ".join(missing_fields)
            ),
        }

    # STEP 7b: duration_days must describe the same trip as the dates
    duration_message = duration_problem(trip_info)
    if duration_message:
        return {
            "status": "needs_clarification",
            "message": duration_message,
        }

        # The walking threshold the plan and its checks use, unless the traveller gave one.
    if trip_info.get("max_walk_minutes") is None:
        trip_info["max_walk_minutes"] = max_walk_minutes()

        # STEP 8: Real Research + Planning Pipeline
    started = time.perf_counter()
    if research_bundle is None:
        try:
            with stage("researcher"):
                research_bundle = run_research_pipeline(trip_info)
        except ResearchInputError as error:
            return {
                "status": "needs_clarification",
                "message": str(error),
            }
        except Exception:
            return {
                "status": "research_unavailable",
                "message": (
                    "The research service is currently unavailable. "
                    "No itinerary has been approved or saved."
                ),
            }

    if research_bundle is not None:
        requested_destination = trip_info["destination"]
        researched_destination = research_bundle.trip.destination

        if (
            requested_destination.strip().casefold()
            != researched_destination.strip().casefold()
        ):
            return {
                "status": "needs_clarification",
                "message": (
                    "The research destination does not match "
                    "the requested destination."
                ),
            }

        timings["research_ms"] = round((time.perf_counter() - started) * 1000)
        print("2. ResearchBundle received")
        print("\n".join(research_summary(research_bundle)))

        # The attractions source failed and returned nothing: say so, propose nothing, never fill in sights.
        attraction_failures = [f for f in research_bundle.failures if f.tool == "find_attractions"]
        if attraction_failures and not any(p.category == "attraction" for p in research_bundle.places):
            return {
                "status": "research_unavailable",
                # the reason decides the advice: recorded data, no guide, or a source to retry (app/research_errors.py)
                "message": attraction_failure_message(research_bundle.trip.destination, attraction_failures[0]),
            }

        destination = research_bundle.trip.destination
        hours_lookup = create_hours_lookup(destination)
        # Walks over the threshold (the traveller's, else RIHLA_MAX_WALK_MINUTES, 30) are measured by car too.
        walk_limit = research_bundle.trip.max_walk_minutes or max_walk_minutes()
        travel_lookup = create_travel_lookup(destination, mode="foot", ride_after_minutes=walk_limit)
        # The trip form gets the fast path: an answer in seconds, then the agents' review in the background. Chat
        # requests (the official contract and its evals) keep the agents in line.
        fast = trip_form is not None and fast_planning()
        llm = None if fast else create_planning_llm()
        deadline = time.monotonic() - (time.perf_counter() - started) + FAST_TOTAL_SECONDS if fast else None

        # Plan from places whose hours, price and walking legs can be checked, when there are enough of them
        # (planning/evidence.py): nothing is filled in; what is set aside is said in the plan's limitations.
        started_evidence = time.perf_counter()
        with stage("evidence"):
            research_bundle, evidence_notes = checkable_bundle(
                research_bundle, travel_lookup,
                **({"seconds": min(FAST_EVIDENCE_SECONDS, max(0.0, deadline - time.monotonic() - 2 * FAST_VERIFY_SECONDS))}
                   if fast else {}))
        timings["evidence_ms"] = round((time.perf_counter() - started_evidence) * 1000)
        for note in evidence_notes:
            print("   evidence:", note)

        started_plan = time.perf_counter()
        result = _plan_and_verify(trip_info, research_bundle, evidence_notes, session, timings, llm, hours_lookup,
                                  travel_lookup, use_agents=not fast, deadline=deadline)
        print(f"Plan answered in {round((time.perf_counter() - started_plan) * 1000)} ms after research")
        # The agents review a fast plan in the background; what they find is an offer (app/refinement.py).
        if fast and session is not None and result.get("plan_version") and refinement.enabled():
            # the agents start from the same places: left to choose, they offered back a 1,479 USD meal and a monument
            # 678 km away, the two listings the fast plan had set aside
            left_out = not_plannable(prepare_candidates(research_bundle), research_bundle.places)
            planned_from = research_bundle.model_copy(
                update={"places": [p for p in research_bundle.places if p.place_id not in left_out]})

            def review(bundle=planned_from, notes=list(evidence_notes)) -> dict:
                return _plan_and_verify(trip_info, bundle, notes, session, {}, create_planning_llm(), hours_lookup,
                                        travel_lookup, commit=False)
            result["refinement"] = refinement.start(session, base_version=result["plan_version"], base_result=result,
                                                    review=review)
        return result

       # STEP 9: Safety fallback
    # Never approve an itinerary through mock agents.

    return {
        "status": "research_unavailable",
        "message": (
            "The real research and planning pipeline "
            "could not be completed. "
            "No itinerary has been approved or saved."
        ),
    }