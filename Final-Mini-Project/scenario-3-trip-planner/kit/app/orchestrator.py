import time
from copy import deepcopy

from app.coordinator import understand_trip_request
from app.memory import get_session, update_trip_details
from app.mock_agents import research_trip, plan_trip, verify_trip
from app.planning_adapter import run_planning_pipeline, create_planning_llm
from app.mcp_lookup_adapter import create_hours_lookup, create_travel_lookup
from app.verification_adapter import run_verification_pipeline
from app.revision import RevisionNotAvailableError, revise_itinerary
from app.verification_loop import verify_with_retries
from planning.research import ResearchBundle
from app.research_adapter import ResearchInputError, run_research_pipeline
from app.diagnostics import budget_decisions, performance_summary, research_summary
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
        coverage.append({"interest": label, "supported": bool(tags), "scheduled": len(places), "places": places})
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
                 "meal": a.kind in ("eat", "drink"), "walk_minutes": a.travel_minutes_from_previous}
                for a in d.activities]} for d in getattr(model, "days", None) or []],
            "sightseeing_per_day": [sum(a.kind not in ("eat", "drink") for a in d.activities)
                                    for d in getattr(model, "days", None) or []],
            "meals_per_day": [sum(a.kind in ("eat", "drink") for a in d.activities)
                              for d in getattr(model, "days", None) or []]}


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

    # STEP 3: Handle revision requests
    if intent == "revise_trip":
        if session is None or session.get("itinerary") is None:
            return {
                "status": "needs_clarification",
                "message": "There is no existing itinerary to revise.",
            }

        try:
            revised_itinerary = revise_itinerary(
                current_itinerary=deepcopy(session["itinerary"]),
                user_request=user_request,
            )
        except RevisionNotAvailableError:
            return {
                "status": "revision_not_supported",
                "message": (
                    "Revision was requested, but the real revision "
                    "agent is not connected yet. "
                    "Your existing itinerary has not been changed."
                ),
            }

        # A revision must still be a useful trip. Without the research
        # context from planning, coverage cannot be confirmed: fail closed.
        context = session.get("coverage_context")
        if context is None:
            return {
                "status": "needs_revision",
                "message": (
                    "The revised itinerary's coverage cannot be checked "
                    "for this trip. Your existing itinerary has not been changed."
                ),
            }

        coverage = assess_store_coverage(revised_itinerary, context)
        print("\n".join(coverage_summary(coverage)))

        if not coverage.sufficient:
            return {
                "status": "needs_revision",
                "message": coverage.insufficient_message(),
                "coverage": coverage.as_dict(),
            }

        # Real verification is not connected yet.
        # Never approve a revision using the mock verifier.
        return {
            "status": "verification_required",
            "message": _with_warning(
                "The revised itinerary was generated, but real "
                "verification is required before approval.",
                coverage.warning(),
            ),
            "itinerary": revised_itinerary,
            "coverage": coverage.as_dict(),
        }

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

        # STEP 8: Real Research + Planning Pipeline
    started = time.perf_counter()
    if research_bundle is None:
        try:
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

        destination = research_bundle.trip.destination
        hours_lookup = create_hours_lookup(destination)
        travel_lookup = create_travel_lookup(destination, mode="foot")
        llm = create_planning_llm()

        planning_result = run_planning_pipeline(
            research_bundle,
            llm=llm,
            travel_lookup=travel_lookup,
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

        if session is not None:
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
        )

        timings["verification_ms"] = round((time.perf_counter() - started) * 1000)
        performance = _performance(timings, {
            "budget_analyst": planning_result.get("budget_report"),
            "planner": planning_result.get("planner_report"),
            "verifier": verification_result.get("verifier_report"),
            "revision": verification_result.get("revision_report"),
        })
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
        }

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