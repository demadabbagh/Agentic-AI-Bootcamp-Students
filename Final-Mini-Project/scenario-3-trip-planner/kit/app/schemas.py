import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TRIP_DAYS = 21  # longer trips are refused with a clear message, not planned badly


class TripForm(BaseModel):
    """The trip form's own fields, exactly as the traveller entered them. When given, they are used as they
    are: the Coordinator LLM only reads the free-text preferences (and is not called when there are none)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field(min_length=2, max_length=100)
    start_date: dt.date
    end_date: dt.date
    travellers: int = Field(ge=1, le=50)
    budget: float = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")
    interests: list[str] = Field(default_factory=list, max_length=8)
    preferences: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date < self.start_date:
            raise ValueError("The return date is before the departure date.")
        if (self.end_date - self.start_date).days + 1 > MAX_TRIP_DAYS:
            raise ValueError(f"Rihla plans trips of up to {MAX_TRIP_DAYS} days. Please choose shorter dates.")
        return self


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    trip: TripForm | None = None  # the structured form, when the request comes from the trip form


class ChatResponse(BaseModel):
    session_id: str
    status: str
    itinerary: dict[str, Any] | None = None
    pending_action: dict[str, Any] | None = None
    message: str | None = None
    coverage: dict[str, Any] | None = None  # app/coverage.py CoverageReport.as_dict()
    plan_quality: dict[str, Any] | None = None  # app/coverage.py DensityReport.as_dict(): advice, never a gate
    performance: dict[str, Any] | None = None  # per-stage timings, LLM calls and tokens: measured, for display
    summary: dict[str, Any] | None = None  # orchestrator.traveller_summary(): interest coverage and limitations


class ApproveRequest(BaseModel):
    session_id: str
    action_id: str
    approved: bool