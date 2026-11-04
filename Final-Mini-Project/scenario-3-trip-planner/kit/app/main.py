import os
import json
import hashlib
import logging
import threading
from copy import deepcopy
from uuid import uuid4
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app import refinement
from app.mcp_lookup_adapter import prepare_city
from researcher.mcp_client import open_session_early
from app.schemas import (AcceptRequest, ApproveRequest, ChatRequest, ChatResponse, PrepareRequest,
                         RefinementRequest,
                         SessionRequest, WhatIfApplyRequest, WhatIfRequest)
from app.memory import get_session
from app.orchestrator import orchestrate_trip
from app.trip_store import TripStoreError, get_trip_from_store, list_trips_from_store, save_trip_to_store, trip_title
from app.diagnostics import classify_error, safe_traceback
from app.contract import build_citations, build_reply, build_usage, pending_summary, usage_recording
from app.airports import install_airports
from app.auth import current_user, install_auth, traveller_id_for_request
from app.destinations import install_destinations
from app.flights import install_flights
from app.weather import install_weather
from app.approval_policy import ACCEPTANCE_MODES, APPROVABLE_STATUSES, acceptance, approval_eligibility
from app.traveller_memory import (describe_preferences, lasting_preferences, remember, session_preferences,
                                  traveller_id_from)
from researcher import perf
from researcher.mcp_client import recording


app = FastAPI(title="Rihla Trip Planner")
# Google sign-in (app/auth): the signed-in user's identity, RIHLA_AUTH_REQUIRED, CSRF and session ownership.
install_auth(app)


@app.on_event("startup")
def start_mcp_server() -> None:
    threading.Thread(target=open_session_early, name="mcp-open", daemon=True).start()

# Read-only search boxes (docs/destinations.md, docs/airports.md): GET /api/destinations, GET /api/airports.
install_destinations(app)
install_flights(app)
install_airports(app)
install_weather(app)  # GET /api/weather, GET /api/weather/status (docs/weather.md)

# uvicorn's own error logger: printed in the backend console.
logger = logging.getLogger("uvicorn.error")

_approval_lock = Lock()


def _itinerary_hash(itinerary: dict) -> str:
    itinerary_json = json.dumps(
        itinerary,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(itinerary_json.encode("utf-8")).hexdigest()


def _fallback_traveller() -> str | None:
    """The configured demo traveller, for anonymous calls to the open contract that send no user."""
    return os.getenv("TRAVEL_TRAVELLER_ID", "").strip() or None


def _request_traveller(request_user) -> str | None:
    """The signed-in user's own traveller (never one the body names), else the contract's request user (§5)."""
    if current_user() is not None:
        return traveller_id_for_request() or None
    return traveller_id_from(request_user)


def _describe(update: dict) -> str:
    return "; ".join(f"{k.replace('_', ' ')}: {', '.join(v) if isinstance(v, list) else v}" for k, v in update.items())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """
    Process user messages through the Coordinator
    and orchestration workflow.
    """

    session = get_session(request.session_id)

    # The traveller this conversation belongs to. A session belongs to one traveller.
    traveller_id = _request_traveller(request.user)
    if traveller_id and session.get("traveller_id") not in (None, traveller_id):
        reply = "This conversation belongs to another traveller. Please start a new conversation."
        return ChatResponse(session_id=request.session_id, status="traveller_mismatch",
                            state="traveller_mismatch", reply=reply, message=reply)
    if traveller_id:
        session["traveller_id"] = traveller_id
    traveller_id = session.get("traveller_id")

    with recording() as calls, usage_recording() as llm_usage, perf.tracing() as trace:
        # Persistent memory through MCP: loaded once per session, saved only when asked to remember.
        memory_notes: list[str] = []
        preferences, load_status = session_preferences(session, traveller_id)
        if load_status == "unavailable":
            memory_notes.append("I could not load your saved preferences right now, so this plan does not use them.")
        elif load_status == "loaded" and describe_preferences(preferences):
            memory_notes.append(f"Welcome back. Your saved preferences: {describe_preferences(preferences)}.")
        update = lasting_preferences(request.message)
        saved_update = False
        if update:
            if not traveller_id:
                memory_notes.append("I can only remember preferences for an identified traveller, so nothing was saved.")
            elif remember(session, traveller_id, update) == "saved":
                saved_update = True
                preferences = session["memory"]["preferences"]
                memory_notes.append(f"I'll remember that ({_describe(update)}).")
            else:
                memory_notes.append("I could not save that preference right now, so it was not remembered.")

        try:
            result = orchestrate_trip(
                request.message,
                session_id=request.session_id,
                trip_form=request.trip.model_dump(mode="json") if request.trip else None,
                base_version=request.base_version,
                revision_request=request.revision,
                preferences=preferences,
            )

        except Exception as exc:
            # The client gets a generic message; the server log gets the
            # cause (secrets redacted), matched by the error id.
            error_id = uuid4().hex[:8]
            logger.error(
                "POST /api/chat failed (error id %s):\n%s",
                error_id,
                safe_traceback(exc),
            )
            status_code, code, message, retryable = classify_error(exc)
            # "detail" stays a plain string (what the frontend shows); "error" adds the machine-readable part.
            return JSONResponse(
                status_code=status_code,
                content={
                    "detail": f"{message} (error id {error_id})",
                    "error": {"code": code, "message": message, "error_id": error_id, "retryable": retryable},
                },
            )

        tool_calls = [dict(call) for call in calls]
        usage = build_usage(llm_usage, result.get("performance"))
        if isinstance(result.get("performance"), dict):
            result["performance"]["trace"] = trace.summary()
            logger.info("rihla.perf %s", json.dumps({"session": request.session_id[:12], **result["performance"]["trace"]}))

    if result.get("preferences_used"):
        memory_notes.append("Used for this plan: " + "; ".join(result["preferences_used"]) + ".")

    return _plan_response(request.session_id, session, result, traveller_id=traveller_id, memory_notes=memory_notes,
                          tool_calls=tool_calls, usage=usage, saved_update=saved_update)


def _plan_response(session_id: str, session: dict, result: dict, *, traveller_id: str | None,
                   memory_notes: list[str], tool_calls: list[dict], usage: dict | None, saved_update: bool = False,
                   preview: bool = False) -> ChatResponse:
    """The ChatResponse for a pipeline or revision result, through the approval gate (app/approval_policy.py).

    `preview` (WHAT IF): the revised plan is shown with what its approval would rely on, but no action is created
    and the session's open approval is left as it is: the plan on screen is still the original one."""
    status = result["status"]
    itinerary = None
    pending_action = None
    revision_info = result.get("revision") if isinstance(result.get("revision"), dict) else None

    verification_result = result.get("verification_result")
    verifier_report = (
        verification_result.get("verifier_report")
        if isinstance(verification_result, dict)
        else None
    )
    revision_report = (
        verification_result.get("revision_report")
        if isinstance(verification_result, dict)
        else None
    )

    if revision_report is not None:
        final_verification = revision_report.result.final
    elif verifier_report is not None:
        final_verification = verifier_report.verification
    else:
        final_verification = None

    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else None
    coverage_ok = coverage is not None and coverage.get("sufficient") is True

    # A plan came back. Approval eligibility comes from Python's final verification report only
    # (app/approval_policy.py), never from an LLM or a status string: without a report there is no approval.
    eligibility = None
    produced = preview or status in {"verified", "verification_required", "pending_approval"}
    if produced and isinstance(result.get("itinerary"), dict):
        itinerary = deepcopy(result["itinerary"])
        status = "whatif_preview" if preview else "verification_required"
        eligibility = approval_eligibility(final_verification if isinstance(verification_result, dict) else None,
                                           coverage_ok)

    if eligibility is not None and eligibility["eligible"] and not preview:
        title = trip_title(itinerary)
        pending_traveller = traveller_id or _fallback_traveller()

        pending_action = {
            "action_id": str(uuid4()),
            "type": "save_trip",
            "summary": pending_summary(itinerary, title, pending_traveller, eligibility["disclosure"]),
            "verification_status": eligibility["verification_status"],
            "evidence_basis": eligibility["evidence_basis"],
            "approval_basis": eligibility["basis"],
            "disclosure": eligibility["disclosure"],
            "itinerary_hash": _itinerary_hash(itinerary),
            # Exactly what /api/approve sends to the MCP save_trip tool.
            "payload": {
                "traveller_id": pending_traveller,
                "title": title,
                "itinerary": deepcopy(itinerary),
            },
        }
        session["itinerary"] = deepcopy(itinerary)
        status = "pending_approval"

    if coverage is not None and coverage.get("sufficient") is False:
        status = "needs_revision"
        itinerary = None
        pending_action = None

    # A plan the policy did not make eligible can still be kept by the traveller, as their own acknowledged
    # decision (POST /api/accept): never an approval, and saved with its status and every limitation.
    pending_acceptance = None
    state = session.get("plan_state")  # the research behind this plan: names for the places findings refer to
    place_names = {p.place_id: p.name for p in getattr(getattr(state, "bundle", None), "places", None) or []}
    offer = None if preview or itinerary is None or pending_action is not None else acceptance(
        eligibility, itinerary, result.get("verification"), result.get("budget"), place_names)
    if offer is not None:
        record = {k: deepcopy(offer[k]) for k in ("mode", "label", "verification_status", "verified",
                                                  "evidence_basis", "limitations", "confirmed_problems", "costs")}
        # the plan version the traveller accepted (a revised plan has a new one)
        saved = {**deepcopy(itinerary), "rihla_save": {**record, "plan_version": result.get("plan_version")}}
        pending_acceptance = {**offer, "acceptance_id": str(uuid4()), "type": "save_trip_unverified",
                              "itinerary_hash": _itinerary_hash(saved),
                              "payload": {"traveller_id": traveller_id or _fallback_traveller(),
                                          "title": trip_title(itinerary), "itinerary": saved}}

    # The open approval belongs to the plan on screen. Only a response with a new plan replaces it (with the new
    # plan's action when that one is approvable, else nothing); every other reply (a question, a clarification,
    # "remember...", a revision that changed nothing or failed) keeps it, and says so.
    if preview:
        pending_action = None  # nothing to approve until the traveller applies the WHAT IF
    elif itinerary is not None:
        session["pending_action"] = pending_action
        session["pending_acceptance"] = pending_acceptance
    else:
        pending_action = deepcopy(session.get("pending_action"))
        pending_acceptance = deepcopy(session.get("pending_acceptance"))

    if saved_update and status == "unsupported_request":
        status = "preferences_saved"  # the message only asked to remember something

    plan_quality = result.get("plan_quality") if isinstance(result.get("plan_quality"), dict) else None
    summary = deepcopy(result.get("summary")) if itinerary is not None and isinstance(result.get("summary"), dict) \
        else None
    verification = deepcopy(result.get("verification")) if itinerary is not None else None
    budget = deepcopy(result.get("budget")) if itinerary is not None else None
    approval_info = None
    if eligibility is not None and itinerary is not None:
        approval_info = {k: eligibility[k] for k in ("eligible", "verification_status", "evidence_basis", "basis",
                                                "verified", "disclosure", "reason")}

    message = result.get("message")
    if offer is not None and isinstance(message, str):
        # Approval stays refused; the traveller is told the plan can still be saved, as their own decision.
        message = message.replace("No approval is allowed.", "It cannot be approved as verified, but you can save "
                                                              "it with its limitations.")
    reply = build_reply(
        status=status,
        message=None if status == "preferences_saved" else message,
        itinerary=itinerary,
        summary=summary,
        verification=verification,
        budget=budget,
        approvable=itinerary is not None and pending_action is not None,
        memory_notes=memory_notes,
        disclosure=(approval_info or {}).get("disclosure"),
    )
    if itinerary is None and pending_action is not None:
        reply += "\nThe plan you were shown earlier is still waiting for your approval."
    # A revised plan says what changed first (app/revision.py: Python's diff of the two itineraries, not an LLM's).
    changes = [c for c in (revision_info or {}).get("changes") or [] if isinstance(c, str) and c.strip()]
    if itinerary is not None and (revision_info or {}).get("outcome") == "revised" and changes:
        reply = "I revised your plan. What changed:\n" + "\n".join(f"- {c}" for c in changes[:12]) + "\n" + reply
    if preview:
        reply = ("WHAT IF preview (hypothetical): your itinerary has not been changed. Review the differences, then "
                 "apply it to make it your plan.\n" + reply)

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        state=status,
        status=status,
        itinerary=itinerary,
        pending_action=pending_action,
        result=None,
        citations=build_citations(itinerary),
        tool_calls=tool_calls,
        usage=usage,
        message=message,
        coverage=deepcopy(coverage),
        plan_quality=deepcopy(plan_quality) if itinerary is not None else None,
        performance=deepcopy(result.get("performance")) if isinstance(result.get("performance"), dict) else None,
        summary=summary,
        verification=verification,
        budget=budget,
        approval_eligibility=approval_info,
        plan_version=result.get("plan_version"),
        revision=deepcopy(revision_info),
        memory_notes=memory_notes,
        whatif=deepcopy(result.get("whatif")) if isinstance(result.get("whatif"), dict) else None,
        refinement=deepcopy(result.get("refinement")) if isinstance(result.get("refinement"), dict) else None,
        pending_acceptance=_public_acceptance(pending_acceptance),
    )


def _public_acceptance(pending: dict | None) -> dict | None:
    """What the dashboard shows before the traveller confirms: the limitations, not the stored payload."""
    if pending is None:
        return None
    return {k: deepcopy(v) for k, v in pending.items() if k not in ("payload", "itinerary_hash")}


@app.post("/api/approve", response_model=ChatResponse)
def approve(request: ApproveRequest):
    """
    Save a trip only after explicit user approval.

    The save is the MCP save_trip tool, called by this code (never by an agent) with exactly the
    approved payload and the action_id as the idempotency key. Answers follow the API contract: a
    ChatResponse whose result holds the trip_id only when the Trip Store confirmed the save.
    """

    with _approval_lock, recording() as calls:
        session = get_session(request.session_id)
        decided = session.setdefault("decided_actions", {})
        pending = session.get("pending_action")

        def respond(status: str, reply: str, result: dict | None = None, *,
                    pending_action: dict | None = None) -> ChatResponse:
            return ChatResponse(session_id=request.session_id, status=status, state=status, reply=reply,
                                message=reply, result=result, pending_action=pending_action,
                                tool_calls=[dict(c) for c in calls])

        is_pending = pending is not None and pending.get("action_id") == request.action_id
        earlier = decided.get(request.action_id)

        if not is_pending and earlier is not None:
            # The same decision again (a double click): answer it, write nothing again.
            if earlier.get("trip_id") and request.approved:
                return respond("approved", f"This plan is already saved as {earlier['trip_id']}. Nothing new was "
                                           "saved, booked or bought.", {"trip_id": earlier["trip_id"]})
            return respond("already_decided", "This plan was already "
                           + (f"saved as {earlier['trip_id']}" if earlier.get("trip_id") else "rejected")
                           + ". Nothing was changed.")

        if not is_pending:
            return respond("no_pending_action", "There is no plan waiting for approval with this ID, so nothing "
                                                "was saved. Plan the trip again to get a plan you can approve.")

        if pending.get("type") != "save_trip":
            raise HTTPException(
                status_code=400,
                detail="Unsupported pending action",
            )

        if not request.approved:
            session["pending_action"] = None
            decided[request.action_id] = {"approved": False}
            return respond("rejected", "OK, I did not save this plan. Nothing was saved, booked or bought. "
                                       "You can ask for changes or plan another trip.")

        # Only a pending action the approval policy made eligible may be saved (app/approval_policy.py).
        if pending.get("verification_status") not in APPROVABLE_STATUSES:
            raise HTTPException(
                status_code=403,
                detail="Trip has not passed verification.",
            )

        payload = pending.get("payload") or {}
        itinerary = deepcopy(payload.get("itinerary"))

        if (
            pending.get("itinerary_hash") is None
            or not isinstance(itinerary, dict)
            or _itinerary_hash(itinerary) != pending["itinerary_hash"]
        ):
            raise HTTPException(
                status_code=403,
                detail="Itinerary changed after verification.",
            )

        # The signed-in user's own traveller, never one from the browser; for an anonymous call to the open
        # contract, the traveller the plan was made for (the request's user) or the configured demo traveller.
        if current_user() is not None:
            traveller_id = traveller_id_for_request()
            if payload.get("traveller_id") not in (None, traveller_id):
                raise HTTPException(status_code=403, detail="This plan belongs to another traveller.")
        else:
            traveller_id = payload.get("traveller_id") or _fallback_traveller()
        if not traveller_id:
            # Keep the pending action: the plan is still approvable once a traveller is known.
            return respond("save_failed", "I can't save this plan: no traveller is identified for it (sign in, or "
                                          "set TRAVEL_TRAVELLER_ID on the server). Nothing was saved.",
                           pending_action=deepcopy(pending))

        try:
            saved_trip = save_trip_to_store(
                itinerary=itinerary,
                traveller_id=traveller_id,
                action_id=request.action_id,
                title=payload.get("title"),
            )

        except TripStoreError as exc:
            # Keep the pending action so the user can retry (same action_id: the store will not save it twice).
            logger.warning("save_trip over MCP failed for action %s: %s", request.action_id, exc.code)
            return respond("save_failed", "The Trip Store did not save this plan"
                           + (f" ({exc.code})" if exc.code else "") + ". Nothing was saved; you can try "
                           "approving again.", pending_action=deepcopy(pending))

        # Clear pending action only after confirmed save.
        trip_id = saved_trip["trip_id"]
        session["pending_action"] = None
        session["saved_trip"] = saved_trip
        decided[request.action_id] = {"approved": True, "trip_id": trip_id}

        reply = f"Saved as {trip_id}. Nothing was booked or bought: prices are estimates from the sources shown."
        if pending.get("approval_basis") == "recorded":
            reply += " This plan was checked against recorded data, not live sources."
        return respond("approved", reply, {"trip_id": trip_id})


@app.post("/api/accept", response_model=ChatResponse)
def accept(request: AcceptRequest):
    """Save a plan that is not eligible for approval, only because the traveller explicitly accepted it with its
    limitations (app/approval_policy.acceptance). It is not an approval and nothing is marked verified: the saved
    itinerary carries rihla_save (its mode, the verifier's status and every limitation). The save is the same MCP
    save_trip tool, with the acceptance_id as the idempotency key; a trip_id is returned only when the store confirms."""
    with _approval_lock, recording() as calls:
        session = get_session(request.session_id)
        decided = session.setdefault("decided_actions", {})
        pending = session.get("pending_acceptance")

        def respond(status: str, reply: str, result: dict | None = None) -> ChatResponse:
            return ChatResponse(session_id=request.session_id, status=status, state=status, reply=reply,
                                message=reply, result=result, tool_calls=[dict(c) for c in calls])

        is_pending = pending is not None and pending.get("acceptance_id") == request.acceptance_id
        earlier = decided.get(request.acceptance_id)
        if not is_pending and earlier is not None and earlier.get("trip_id"):
            # The same confirmation again (a double click): answer it, write nothing again.
            return respond("saved_unverified", f"This plan is already saved as {earlier['trip_id']}. Nothing new "
                                               "was saved.", {"trip_id": earlier["trip_id"], "mode": earlier["mode"]})
        if not is_pending:
            return respond("no_pending_action", "There is no plan waiting to be saved with this ID, so nothing was "
                                                "saved. Plan the trip again.")
        if not request.acknowledged:
            # A cancelled confirmation saves nothing; the plan can still be accepted later.
            return respond("not_saved", "OK, nothing was saved. The plan is still on screen if you change your mind.")
        if pending.get("mode") not in ACCEPTANCE_MODES or request.mode != pending.get("mode"):
            raise HTTPException(status_code=409, detail="The confirmation does not match this plan's status. "
                                                        "Nothing was saved.")

        payload = pending.get("payload") or {}
        itinerary = deepcopy(payload.get("itinerary"))
        if not isinstance(itinerary, dict) or _itinerary_hash(itinerary) != pending.get("itinerary_hash"):
            raise HTTPException(status_code=403, detail="Itinerary changed after it was offered. Nothing was saved.")

        # As for approval: the signed-in user's own traveller, never one from the browser.
        if current_user() is not None:
            traveller_id = traveller_id_for_request()
            if payload.get("traveller_id") not in (None, traveller_id):
                raise HTTPException(status_code=403, detail="This plan belongs to another traveller.")
        else:
            traveller_id = payload.get("traveller_id") or _fallback_traveller()
        if not traveller_id:
            return respond("save_failed", "I can't save this plan: no traveller is identified for it (sign in, or "
                                          "set TRAVEL_TRAVELLER_ID on the server). Nothing was saved.")
        try:
            saved_trip = save_trip_to_store(itinerary=itinerary, traveller_id=traveller_id,
                                            action_id=request.acceptance_id, title=payload.get("title"))
        except TripStoreError as exc:
            # Keep the offer so the traveller can retry (same acceptance_id: the store will not save it twice).
            logger.warning("save_trip over MCP failed for acceptance %s: %s", request.acceptance_id, exc.code)
            return respond("save_failed", "The Trip Store did not save this plan"
                           + (f" ({exc.code})" if exc.code else "") + ". Nothing was saved; you can try again.")

        trip_id = saved_trip["trip_id"]
        session["pending_acceptance"] = None
        decided[request.acceptance_id] = {"accepted": True, "trip_id": trip_id, "mode": pending["mode"]}
        return respond("saved_unverified", f"Saved as {trip_id} ({pending['label']}). This is your decision, not a "
                       "verification: the limitations you accepted are saved with the trip. Nothing was booked or "
                       "bought.", {"trip_id": trip_id, "mode": pending["mode"]})


# ------------------------------------------------------------------ WHAT IF (app/whatif.py)
def _whatif_session(session_id: str) -> dict:
    """The session's plan, for its own traveller only (the middleware already bound the session to its user)."""
    return get_session(session_id)


@app.post("/api/plan/prepare", status_code=202)
def plan_prepare(request: PrepareRequest):
    """The traveller picked a destination: start reading its guide while they fill in the form. Saves time, changes
    no answer, returns at once."""
    return {"status": prepare_city(request.destination)}


# ------------------------------------------------------------------ the AI review of a fast plan (app/refinement.py)
@app.post("/api/plan/refinement")
def plan_refinement(request: RefinementRequest):
    """Where the agents' review of the plan on screen stands: running, no_change, ready (with what it would change),
    failed, or stale. The plan is not changed by asking."""
    return refinement.status(get_session(request.session_id), request.base_version)


@app.post("/api/plan/refinement/apply", response_model=ChatResponse)
def plan_refinement_apply(request: RefinementRequest):
    """Make the offered plan the session's plan. It then goes through the same approval gate as any plan: a new
    pending action when the policy makes it eligible, and the previous action stops working."""
    from app.revision import _no_change
    session = get_session(request.session_id)
    with _approval_lock:  # never at the same moment as an approval of the plan it replaces
        result = refinement.take(session, request.base_version) or _no_change(
            "needs_clarification", "This suggestion is no longer available: your plan changed, or was saved.",
            "stale_plan", session.get("plan_state"))
        return _plan_response(request.session_id, session, result, traveller_id=session.get("traveller_id"),
                              memory_notes=[], tool_calls=[], usage=None)


@app.post("/api/whatif/options")
def whatif_options(request: SessionRequest):
    """What the WHAT IF panel can offer for the plan on screen: its dates and its attractions (for a closure), each
    with whether its listing shows it is indoors or outdoors (for rain)."""
    from planning.setting import setting
    state = _whatif_session(request.session_id).get("plan_state")
    if state is None:
        return {"available": False, "reason": "There is no plan in this session to try a WHAT IF on: plan the trip "
                                              "again (a saved trip opened from My Trips is not kept in memory)."}
    places = {p.place_id: p for p in state.bundle.places}
    attractions = [{"place_id": a.place_id, "name": a.name, "date": str(d.date),
                    "setting": setting(places[a.place_id]) if a.place_id in places else "unclear"}
                   for d in state.itinerary.days for a in d.activities
                   if a.place_id and a.kind not in ("eat", "drink")
                   and (places.get(a.place_id) is None or places[a.place_id].category != "restaurant")]
    return {"available": True, "plan_version": state.version, "dates": [str(d.date) for d in state.itinerary.days],
            "budget_total": None if state.budget_report.budget_total is None else str(state.budget_report.budget_total),
            "currency": state.budget_report.currency, "attractions": attractions}


@app.post("/api/whatif", response_model=ChatResponse)
def whatif_preview(request: WhatIfRequest):
    """Preview a hypothetical scenario on the session's plan. The itinerary is NOT changed: the answer is a proposal
    (status whatif_preview) the traveller can apply with POST /api/whatif/apply, or a reason why it is not possible."""
    from app.whatif import preview
    session = _whatif_session(request.session_id)
    with recording() as calls, usage_recording() as llm_usage:
        try:
            result = preview(session, request.scenario.model_dump(exclude_none=True, mode="json"),
                             session.get("trip_details", {}), base_version=request.base_version)
        except Exception as exc:
            error_id = uuid4().hex[:8]
            logger.error("POST /api/whatif failed (error id %s):\n%s", error_id, safe_traceback(exc))
            status_code, code, message, retryable = classify_error(exc)
            return JSONResponse(status_code=status_code, content={
                "detail": f"{message} Your itinerary was not changed. (error id {error_id})",
                "error": {"code": code, "message": message, "error_id": error_id, "retryable": retryable}})
        tool_calls = [dict(call) for call in calls]
        usage = build_usage(llm_usage, result.get("performance"))
    return _plan_response(request.session_id, session, result, traveller_id=session.get("traveller_id"),
                          memory_notes=[], tool_calls=tool_calls, usage=usage,
                          preview=result.get("status") == "whatif_preview")


@app.post("/api/whatif/apply", response_model=ChatResponse)
def whatif_apply(request: WhatIfApplyRequest):
    """Make a previewed WHAT IF the session's plan. It then goes through the same approval gate as any revised plan:
    a new pending action when the policy makes it eligible, none otherwise."""
    from app.whatif import apply_proposal
    session = _whatif_session(request.session_id)
    result = apply_proposal(session, request.proposal_id)
    return _plan_response(request.session_id, session, result, traveller_id=session.get("traveller_id"),
                          memory_notes=[], tool_calls=[], usage=None)


MAX_LISTED_TRIPS = 50  # newest first


def _store_traveller() -> str:
    traveller_id = traveller_id_for_request()  # the signed-in user's traveller, never one the browser sends
    if not traveller_id:
        raise HTTPException(
            status_code=503,
            detail=("Saved trips are not available: TRAVEL_TRAVELLER_ID is not configured on the server."),
        )
    return traveller_id


@app.get("/api/trips")
def list_trips():
    """The approved trips saved in the Rihla Trip Store for the signed-in user's traveller (summaries only)."""
    traveller_id = _store_traveller()
    try:
        trips = list_trips_from_store(traveller_id)[:MAX_LISTED_TRIPS]
    except TripStoreError as exc:
        raise HTTPException(status_code=503, detail="The Trip Store could not be reached. Please try again.") from exc
    # The store's summaries carry no traveller count: it comes from each trip's own itinerary (None if unreadable).
    for trip in trips:
        try:
            record = get_trip_from_store(str(trip.get("trip_id")))
        except TripStoreError:
            record = None
        itinerary = (record or {}).get("itinerary") or {}
        trip["travellers"] = itinerary.get("travellers")
        # How it was saved: approved (the only way into the store without rihla_save), or accepted by the traveller
        # with limitations / as an unverified draft (app/approval_policy.acceptance).
        mode = itinerary.get("rihla_save", {}).get("mode") if isinstance(itinerary.get("rihla_save"), dict) else None
        trip["save_mode"] = mode if mode in ACCEPTANCE_MODES else ("approved" if record else None)
    return {"count": len(trips), "trips": trips}


@app.get("/api/trips/{trip_id}")
def get_trip(trip_id: str):
    """One approved trip with its full itinerary, only if it belongs to the signed-in user's traveller."""
    traveller_id = _store_traveller()
    try:
        trip = get_trip_from_store(trip_id)
    except TripStoreError as exc:
        raise HTTPException(status_code=503, detail="The Trip Store could not be reached. Please try again.") from exc
    if trip is None or str(trip.get("traveller_id", "")).lower() != traveller_id.lower():
        raise HTTPException(status_code=404, detail=f"No saved trip {trip_id}.")
    return trip
