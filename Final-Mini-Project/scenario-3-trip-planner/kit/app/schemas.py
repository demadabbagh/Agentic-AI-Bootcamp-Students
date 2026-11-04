import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.destinations.identity import DestinationPlace, check_destination, research_destination

# Longer trips are refused with a clear message, not planned badly. Measured on live Istanbul data (the city with the
# most listings): 30 days took 255 s with sightseeing on all 30 days; 60 days took 202 s but only 49 of 60 days had
# any, because the sources list about 50 places per city (find_attractions returns at most 50). Past 30 days a plan
# would have empty days, so the limit is 30. Keep rihla-frontend/src/services/tripForm.js in step.
MAX_TRIP_DAYS = 30
TOO_LONG = (f"Rihla plans trips of up to {MAX_TRIP_DAYS} days: for a longer stay the travel sources list fewer sights "
            "than days, so the plan would have empty days. Plan a longer journey as two trips.")


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
    # Optional: the city chosen in the destination box (GeoNames id, country code, coordinates). Older clients
    # send only the text. app/destinations/identity.py checks both against each other (docs/destinations.md).
    place: DestinationPlace | None = None

    @model_validator(mode="after")
    def _dates(self):
        check_destination(self.destination, self.place)
        self.destination = research_destination(self.destination, self.place)  # "Paris" -> "Paris, Texas, ..."
        if self.end_date < self.start_date:
            raise ValueError("The return date is before the departure date.")
        if (self.end_date - self.start_date).days + 1 > MAX_TRIP_DAYS:
            raise ValueError(TOO_LONG)
        return self


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    trip: TripForm | None = None  # the structured form, when the request comes from the trip form
    # A change request from the dashboard: the plan version it was made on (see ChatResponse.plan_version). A
    # request on an older version than the session's is refused rather than applied to the wrong plan.
    base_version: str | None = Field(default=None, max_length=40)
    revision: bool = False  # sent by the dashboard's "Ask for a change" box
    user: dict[str, Any] | None = None  # CONTRACT §1/§5: {"traveller_id": "TRV-101", "name": "..."}, optional


class PendingAction(BaseModel):
    """The public approval action from CONTRACT §2; implementation checks stay server-side.

    Only what is declared here leaves the server: extra="ignore" keeps itinerary_hash and anything else internal in
    the session. After the four contract fields come Rihla's own display fields, an explicit allow-list: the dashboard
    needs them to offer Approve and to say what the approval relies on (app/approval_policy.py). They are shown to
    the traveller and never read back: /api/approve decides from the session's own copy of the action."""
    model_config = ConfigDict(extra="ignore")

    action_id: str
    type: Literal["create_booking", "cancel_booking", "create_ticket", "save_trip"]
    summary: str
    payload: dict[str, Any]
    verification_status: Literal["verified", "verified_on_records"] | None = None  # approvable plans only
    evidence_basis: Literal["fresh", "recorded", "mixed"] | None = None
    approval_basis: Literal["live", "recorded"] | None = None
    disclosure: str | None = None


class ChatResponse(BaseModel):
    # The API contract (common/CONTRACT.md §2)
    session_id: str
    reply: str = ""  # always present: plain text the traveller reads
    state: str | None = None  # where the workflow is now (the same value as status)
    pending_action: PendingAction | None = None  # {action_id, type, summary, payload} only when approvable
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
    approval_eligibility: dict[str, Any] | None = None  # app/approval_policy.py: eligible, verification_status, evidence basis, why
    plan_version: str | None = None  # the session's plan this response shows: send it back as base_version
    revision: dict[str, Any] | None = None  # app/revision.py: outcome, changes, unchanged dates, who chose
    memory_notes: list[str] = Field(default_factory=list)  # app/traveller_memory.py: saved preferences used, or why not
    whatif: dict[str, Any] | None = None  # app/whatif.py: the hypothetical scenario, its proposal and outcome
    refinement: dict[str, Any] | None = None  # app/refinement.py: the agents' background review of a fast plan
    # app/approval_policy.acceptance: a plan that is not eligible for approval, which the traveller may still save as
    # their own acknowledged decision (POST /api/accept): its mode, status label, limitations and known costs
    pending_acceptance: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    """The structured part of an error response (next to FastAPI's "detail" string)."""
    code: str  # llm_timeout | llm_rate_limited | llm_unavailable | planning_failed | internal_error
    message: str
    error_id: str
    retryable: bool


class WhatIfScenario(BaseModel):
    """One WHAT IF scenario (app/whatif.py). Only the fields its type uses are read."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["budget_cut", "rain", "closure", "delay"]
    percent: float | None = Field(default=None, ge=1, le=90)  # budget_cut (default 30)
    date: dt.date | None = None  # rain (None: every day)
    place_id: str | None = Field(default=None, max_length=120)  # closure
    hours: int | None = Field(default=None, ge=1, le=12)  # delay (default 3)


class WhatIfRequest(BaseModel):
    session_id: str = Field(min_length=1)
    base_version: str | None = Field(default=None, max_length=40)  # the plan_version on screen
    scenario: WhatIfScenario


class WhatIfApplyRequest(BaseModel):
    session_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1, max_length=40)


class PrepareRequest(BaseModel):
    destination: str = Field(min_length=2, max_length=100)


class RefinementRequest(BaseModel):
    session_id: str = Field(min_length=1)
    base_version: str | None = Field(default=None, max_length=40)  # the plan_version on screen


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    approved: StrictBool


class AcceptRequest(BaseModel):
    """POST /api/accept: the traveller keeps a plan that is not eligible for approval, having read its limitations."""
    session_id: str = Field(min_length=1)
    acceptance_id: str = Field(min_length=1, max_length=64)
    acknowledged: bool  # True only from the confirmation's explicit acknowledgment; False cancels (nothing saved)
    mode: str = Field(max_length=40)  # the mode the traveller confirmed: it must be the plan's own
