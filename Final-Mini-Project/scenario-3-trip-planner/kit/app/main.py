import os
from copy import deepcopy
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from app.schemas import ChatRequest, ChatResponse, ApproveRequest
from app.memory import get_session
from app.orchestrator import orchestrate_trip
from app.trip_store import TripStoreError, save_trip_to_store


app = FastAPI(title="Rihla Trip Planner")


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
        )

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="The trip planning service is temporarily unavailable.",
        ) from exc

    status = result["status"]

    itinerary = session.get("itinerary")
    pending_action = session.get("pending_action")

    if status == "pending_approval":
        itinerary = deepcopy(result["itinerary"])

        pending_action = {
            "action_id": str(uuid4()),
            "type": "save_trip",
            "payload": {
                "itinerary": deepcopy(itinerary),
            },
        }

        session["itinerary"] = itinerary
        session["pending_action"] = pending_action

    return ChatResponse(
        session_id=request.session_id,
        status=status,
        itinerary=itinerary,
        pending_action=pending_action,
        message=result.get("message"),
    )


@app.post("/api/approve")
def approve(request: ApproveRequest):
    """
    Save a trip only after explicit user approval.
    """

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

    traveller_id = os.getenv("TRAVEL_TRAVELLER_ID", "").strip()

    if not traveller_id:
        raise HTTPException(
            status_code=503,
            detail="Traveller ID is not configured.",
        )

    itinerary = deepcopy(
        pending["payload"]["itinerary"]
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