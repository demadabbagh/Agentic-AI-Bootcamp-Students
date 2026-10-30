import os
import json
import hashlib
import logging
from copy import deepcopy
from uuid import uuid4
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.schemas import ChatRequest, ChatResponse, ApproveRequest
from app.memory import get_session
from app.orchestrator import orchestrate_trip
from app.trip_store import TripStoreError, save_trip_to_store, trip_title
from app.diagnostics import classify_error, safe_traceback
from app.contract import build_citations, build_reply, build_usage, pending_summary, usage_recording
from app.traveller_memory import (describe_preferences, lasting_preferences, remember, session_preferences,
                                  traveller_id_from)
from researcher.mcp_client import recording


app = FastAPI(title="Rihla Trip Planner")

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
    """The configured traveller, for clients that send no user (the React page has no accounts yet)."""
    return os.getenv("TRAVEL_TRAVELLER_ID", "").strip() or None


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

    # The traveller is the request's user (CONTRACT §5). A session belongs to one traveller.
    traveller_id = traveller_id_from(request.user)
    if traveller_id and session.get("traveller_id") not in (None, traveller_id):
        reply = "This conversation belongs to another traveller. Please start a new conversation."
        return ChatResponse(session_id=request.session_id, status="traveller_mismatch",
                            state="traveller_mismatch", reply=reply, message=reply)
    if traveller_id:
        session["traveller_id"] = traveller_id
    traveller_id = session.get("traveller_id")

    with recording() as calls, usage_recording() as llm_usage:
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

    if result.get("preferences_used"):
        memory_notes.append("Used for this plan: " + "; ".join(result["preferences_used"]) + ".")

    status = result["status"]
    itinerary = None
    pending_action = None

    # Invalidate any previous approval request when processing
    # a new trip result.
    session["pending_action"] = None

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

    trusted_verification = (
        status == "verified"
        and isinstance(verification_result, dict)
        and verification_result.get("verified") is True
        and final_verification is not None
        and final_verification.status == "PASS"
        and final_verification.verified is True
        and final_verification.evidence_basis == "fresh"
        and coverage_ok
    )

    if trusted_verification:
        itinerary = deepcopy(result["itinerary"])
        title = trip_title(itinerary)
        pending_traveller = traveller_id or _fallback_traveller()

        pending_action = {
            "action_id": str(uuid4()),
            "type": "save_trip",
            "summary": pending_summary(itinerary, title, pending_traveller),
            "verification_status": "verified",
            "itinerary_hash": _itinerary_hash(itinerary),
            # Exactly what /api/approve sends to the MCP save_trip tool.
            "payload": {
                "traveller_id": pending_traveller,
                "title": title,
                "itinerary": deepcopy(itinerary),
            },
        }

        session["itinerary"] = deepcopy(itinerary)
        session["pending_action"] = pending_action
        status = "pending_approval"

    elif status in {"verified", "pending_approval"}:
        # A status string alone is never sufficient proof.
        status = "verification_required"
        itinerary = deepcopy(result.get("itinerary"))

    elif status == "verification_required":
        itinerary = deepcopy(result.get("itinerary"))

    # Any other status (needs_revision, needs_clarification, errors...)
    # carries no itinerary: the session's previous one is kept for later
    # revision, but never returned as if it were this request's result.

    if coverage is not None and coverage.get("sufficient") is False:
        status = "needs_revision"
        itinerary = None

    if saved_update and status == "unsupported_request":
        status = "preferences_saved"  # the message only asked to remember something

    plan_quality = result.get("plan_quality") if isinstance(result.get("plan_quality"), dict) else None
    summary = deepcopy(result.get("summary")) if itinerary is not None and isinstance(result.get("summary"), dict) \
        else None
    verification = deepcopy(result.get("verification")) if itinerary is not None else None
    budget = deepcopy(result.get("budget")) if itinerary is not None else None

    reply = build_reply(
        status=status,
        message=None if status == "preferences_saved" else result.get("message"),
        itinerary=itinerary,
        summary=summary,
        verification=verification,
        budget=budget,
        approvable=pending_action is not None,
        memory_notes=memory_notes,
    )

    return ChatResponse(
        session_id=request.session_id,
        reply=reply,
        state=status,
        status=status,
        itinerary=itinerary,
        pending_action=pending_action,
        result=None,
        citations=build_citations(itinerary),
        tool_calls=tool_calls,
        usage=usage,
        message=result.get("message"),
        coverage=deepcopy(coverage),
        plan_quality=deepcopy(plan_quality) if itinerary is not None else None,
        performance=deepcopy(result.get("performance")) if isinstance(result.get("performance"), dict) else None,
        summary=summary,
        verification=verification,
        budget=budget,
    )


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

        def respond(status: str, reply: str, result: dict | None = None) -> ChatResponse:
            return ChatResponse(session_id=request.session_id, status=status, state=status, reply=reply,
                                message=reply, result=result, tool_calls=[dict(c) for c in calls])

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

        # Only a trusted, verified pending action may be saved.
        if pending.get("verification_status") != "verified":
            raise HTTPException(
                status_code=403,
                detail="Trip has not passed real verification.",
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

        traveller_id = payload.get("traveller_id") or _fallback_traveller()
        if not traveller_id:
            # Keep the pending action: the plan is still approvable once a traveller is known.
            return respond("save_failed", "I can't save this plan: no traveller is identified for it. "
                                          "Nothing was saved.")

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
                           "approving again.")

        # Clear pending action only after confirmed save.
        trip_id = saved_trip["trip_id"]
        session["pending_action"] = None
        session["saved_trip"] = saved_trip
        decided[request.action_id] = {"approved": True, "trip_id": trip_id}

        return respond("approved", f"Saved as {trip_id}. Nothing was booked or bought: prices are estimates "
                                   "from the sources shown.", {"trip_id": trip_id})
