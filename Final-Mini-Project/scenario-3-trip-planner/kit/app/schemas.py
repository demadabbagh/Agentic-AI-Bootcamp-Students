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
    user: dict[str, Any] | None = None  # CONTRACT §1/§5: {"traveller_id": "TRV-101", "name": "..."}, optional


class ChatResponse(BaseModel):
    # The API contract (common/CONTRACT.md §2)
    session_id: str
    reply: str = ""  # always present: plain text the traveller reads
    state: str | None = None  # where the workflow is now (the same value as status)
    pending_action: dict[str, Any] | None = None  # {action_id, type, summary, payload} only when approvable
    result: dict[str, Any] | None = None  # {"trip_id": "TRIP-…"} only after the Trip Store confirmed a save
    citations: list[dict[str, Any]] = Field(default_factory=list)  # [{id, title, url}]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # [{tool, ok, ms, agent, via}]
    usage: dict[str, Any] | None = None  # {prompt_tokens, completion_tokens, cost_usd, ...}
    # Rihla's own fields (the React dashboard reads them)
    status: str
    itinerary: dict[str, Any] | None = None
    message: str | None = None
    coverage: dict[str, Any] | None = None  # app/coverage.py CoverageReport.as_dict()
    plan_quality: dict[str, Any] | None = None  # app/coverage.py DensityReport.as_dict(): advice, never a gate
    performance: dict[str, Any] | None = None  # per-stage timings, LLM calls and tokens: measured, for display
    summary: dict[str, Any] | None = None  # orchestrator.traveller_summary(): interest coverage and limitations
    verification: dict[str, Any] | None = None  # orchestrator.verification_summary(): Python's verdict and findings
    budget: dict[str, Any] | None = None  # orchestrator.budget_summary(): known, unknown and not-included costs


class ErrorBody(BaseModel):
    """The structured part of an error response (next to FastAPI's "detail" string)."""
    code: str  # llm_timeout | llm_rate_limited | llm_unavailable | planning_failed | internal_error
    message: str
    error_id: str
    retryable: bool


class ApproveRequest(BaseModel):
    session_id: str
    action_id: str
    approved: bool