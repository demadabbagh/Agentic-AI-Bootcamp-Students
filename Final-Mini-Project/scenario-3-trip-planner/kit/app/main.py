import os
import json
import hashlib
import logging
from copy import deepcopy
from uuid import uuid4
from threading import Lock

from fastapi import FastAPI, HTTPException

from app.schemas import ChatRequest, ChatResponse, ApproveRequest
from app.memory import get_session
from app.orchestrator import orchestrate_trip
from app.trip_store import TripStoreError, save_trip_to_store
from app.diagnostics import safe_traceback


app = FastAPI(title="Rihla Trip Planner")

# uvicorn's own error logger: printed in the backend console.
logger = logging.getLogger("uvicorn.error")

_approval_lock = Lock()


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

    try:
        result = orchestrate_trip(
            request.message,
            session_id=request.session_id,
            trip_form=request.trip.model_dump(mode="json") if request.trip else None,
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
        raise HTTPException(
            status_code=503,
            detail=(
                "The trip planning service is temporarily unavailable. "
                f"(error id {error_id})"
            ),
        ) from exc

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

        action_id = str(uuid4())

        itinerary_json = json.dumps(
            itinerary,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        itinerary_hash = hashlib.sha256(
            itinerary_json.encode("utf-8")
        ).hexdigest()

        pending_action = {
            "action_id": action_id,
            "type": "save_trip",
            "verification_status": "verified",
            "itinerary_hash": itinerary_hash,
            "payload": {
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

    plan_quality = result.get("plan_quality") if isinstance(result.get("plan_quality"), dict) else None

    return ChatResponse(
        session_id=request.session_id,
        status=status,
        itinerary=itinerary,
        pending_action=pending_action,
        message=result.get("message"),
        coverage=deepcopy(coverage),
        plan_quality=deepcopy(plan_quality) if itinerary is not None else None,
        performance=deepcopy(result.get("performance")) if isinstance(result.get("performance"), dict) else None,
        summary=deepcopy(result.get("summary")) if itinerary is not None and isinstance(result.get("summary"), dict)
        else None,
    )


@app.post("/api/approve")
def approve(request: ApproveRequest):
    """
    Save a trip only after explicit user approval.
    """

    with _approval_lock:
        session = get_session(request.session_id)
        pending = session.get("pending_action")

        if pending is None:
            raise HTTPException(
                status_code=404,
                detail="No pending action for this session",
            )

        if pending["action_id"] != request.action_id:
            raise HTTPException(
                status_code=400,
                detail="Invalid action ID",
            )

        if pending.get("type") != "save_trip":
            raise HTTPException(
                status_code=400,
                detail="Unsupported pending action",
            )

        if not request.approved:
            session["pending_action"] = None

            return {
                "status": "rejected",
                "message": "Trip was not saved",
            }

        # Only a trusted, verified pending action may be saved.
        if pending.get("verification_status") != "verified":
            raise HTTPException(
                status_code=403,
                detail="Trip has not passed real verification.",
            )

        itinerary = deepcopy(
            pending["payload"]["itinerary"]
        )

        itinerary_json = json.dumps(
            itinerary,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        current_hash = hashlib.sha256(
            itinerary_json.encode("utf-8")
        ).hexdigest()

        if (
            pending.get("itinerary_hash") is None
            or current_hash != pending["itinerary_hash"]
        ):
            raise HTTPException(
                status_code=403,
                detail="Itinerary changed after verification.",
            )

        # Configuration is checked after the integrity checks: a tampered or
        # unverified action is refused (403) whatever the server's settings.
        traveller_id = os.getenv("TRAVEL_TRAVELLER_ID", "").strip()

        if not traveller_id:
            raise HTTPException(
                status_code=503,
                detail="Traveller ID is not configured.",
            )

        try:
            saved_trip = save_trip_to_store(
                itinerary=itinerary,
                traveller_id=traveller_id,
                action_id=request.action_id,
            )

        except TripStoreError as exc:
            # Keep the pending action so the user can retry.
            raise HTTPException(
                status_code=503,
                detail="Trip could not be saved. Please try again.",
            ) from exc

        # Clear pending action only after confirmed save.
        session["pending_action"] = None
        session["saved_trip"] = saved_trip

        return {
            "status": "approved",
            "message": "Trip saved successfully.",
            "trip": saved_trip,
        }