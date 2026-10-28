from uuid import uuid4

from fastapi import FastAPI, HTTPException

from app.schemas import ChatRequest, ChatResponse, ApproveRequest
from app.memory import get_session
from app.orchestrator import orchestrate_trip


app = FastAPI(title="Rihla Trip Planner")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session = get_session(request.session_id)

    result = orchestrate_trip(request.message)

    itinerary = result.get("itinerary")

    pending_action = None

    if result["status"] == "pending_approval":
        pending_action = {
            "action_id": str(uuid4()),
            "type": "save_trip",
            "payload": {
                "itinerary": itinerary,
            },
        }

    session["itinerary"] = itinerary
    session["pending_action"] = pending_action

    return ChatResponse(
        session_id=request.session_id,
        status=result["status"],
        itinerary=itinerary,
        pending_action=pending_action,
    )




@app.post("/api/approve")
def approve(request: ApproveRequest):
    session = get_session(request.session_id)
    pending = session.get("pending_action")

    if pending is None:
        raise HTTPException(
            status_code=404,
            detail="No pending action for this session"
        )

    if pending["action_id"] != request.action_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid action ID"
        )

    if not request.approved:
        session["pending_action"] = None

        return {
            "status": "rejected",
            "message": "Trip was not saved"
        }

    session["pending_action"] = None

    return {
        "status": "approved",
        "message": "Approval recorded. Mock mode: trip was not saved."
    }
