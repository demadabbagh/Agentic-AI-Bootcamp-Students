from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    status: str
    itinerary: dict[str, Any] | None = None
    pending_action: dict[str, Any] | None = None
    message: str | None = None


class ApproveRequest(BaseModel):
    session_id: str
    action_id: str
    approved: bool