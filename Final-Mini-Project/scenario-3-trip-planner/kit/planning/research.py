"""ResearchBundle: what the Budget Analyst reads from the research step. A PROVISIONAL adapter.

The planning package owns this schema until the Researcher's output contract is agreed. The Researcher hands
over the raw MCP tool results it collected, and ResearchBundle.from_tool_results() turns them into
ResearchedPlace rows. When the contract is final, only this module changes: the Budget Analyst reads
ResearchBundle, never a tool result directly.

Every text field below comes from outside (Wikivoyage, a price sheet, the traveller). It is data about a
place, never an instruction to the crew.
"""
from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import IsoDate, Money

SCHEMA_VERSION = "provisional-1"


class TripRequest(BaseModel):
    """The traveller's request, as far as the budget is concerned."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")  # the budget currency
    travellers: int = Field(ge=1, le=50, strict=True)
    budget_total: Money | None = None  # whole party, in currency
    start_date: IsoDate
    end_date: IsoDate
    interests: list[str] = Field(default_factory=list)
    budget_style: str | None = None  # budget | mid-range | splurge (saved preferences)
    required_places: list[str] = Field(default_factory=list)  # places the traveller explicitly asked for

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _dates(self) -> TripRequest:
        if self.end_date < self.start_date:
            raise ValueError(f"end_date {self.end_date} is before start_date {self.start_date}")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class ResearchedPlace(BaseModel):
    """One listing from find_attractions / find_restaurants, with the fields that matter for money."""
    model_config = ConfigDict(extra="forbid")

    place_id: str
    name: str
    category: Literal["attraction", "restaurant"]
    kind: str | None = None  # see | do | buy | eat | drink
    tags: list[str] = Field(default_factory=list)
    district: str | None = None
    price: dict | None = None  # the listing's parsed price (travel_data.parse_price)
    price_sources: list[dict] | None = None  # only with price_conflict: every source, each with its own price
    price_conflict: bool = False
    price_tier: str | None = None  # restaurants: the Wikivoyage section (budget | mid-range | splurge)
    source_url: str | None = None
    citation: dict | None = None
    synthetic: bool = False  # a synthetic test listing (overlay), not a real place
    description: str | None = None  # untrusted text: kept for the record, never sent to an LLM by planning

    @classmethod
    def from_listing(cls, item: dict, category: Literal["attraction", "restaurant"]) -> ResearchedPlace:
        return cls(place_id=item["id"], name=item["name"], category=category, kind=item.get("kind"),
                   tags=list(item.get("tags") or []), district=item.get("district"), price=item.get("price"),
                   price_sources=item.get("price_sources"), price_conflict=bool(item.get("price_conflict")),
                   price_tier=item.get("price_tier"), source_url=item.get("source_url"),
                   citation=item.get("citation"), synthetic=bool(item.get("synthetic")),
                   description=item.get("description"))


class ToolFailure(BaseModel):
    """A tool result the research step got back as an error: said out loud, never filled in."""
    model_config = ConfigDict(extra="forbid")

    tool: str
    code: str
    message: str = ""


def _failure(tool: str, result) -> ToolFailure | None:
    if not isinstance(result, dict):
        return ToolFailure(tool=tool, code="bad_result", message="the tool result is not a JSON object")
    if result.get("ok") is False or isinstance(result.get("error"), dict):
        err = result.get("error") or {}
        return ToolFailure(tool=tool, code=str(err.get("code", "error")), message=str(err.get("message", "")))
    return None


class ResearchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["provisional-1"] = SCHEMA_VERSION
    trip: TripRequest
    places: list[ResearchedPlace] = Field(default_factory=list)
    fx_results: list[dict] = Field(default_factory=list)  # successful convert_currency results, verbatim
    failures: list[ToolFailure] = Field(default_factory=list)

    @classmethod
    def from_tool_results(cls, trip: TripRequest | dict, *, attractions: Iterable[dict] = (),
                          restaurants: Iterable[dict] = (), fx: Iterable[dict] = ()) -> ResearchBundle:
        """Build from raw MCP results ({"ok": true, ...} or {"ok": false, "error": ...}).

        A failed result becomes a ToolFailure and adds no places. The same listing twice is kept once.
        """
        places: dict[str, ResearchedPlace] = {}
        failures: list[ToolFailure] = []
        for tool, results, key, category in (("find_attractions", attractions, "attractions", "attraction"),
                                             ("find_restaurants", restaurants, "restaurants", "restaurant")):
            for result in results:
                if failed := _failure(tool, result):
                    failures.append(failed)
                    continue
                for item in result.get(key) or []:
                    if item.get("id") and item.get("name") and item["id"] not in places:
                        places[item["id"]] = ResearchedPlace.from_listing(item, category)
        rates = []
        for result in fx:
            if failed := _failure("convert_currency", result):
                failures.append(failed)
            else:
                rates.append(result)
        return cls(trip=trip, places=list(places.values()), fx_results=rates, failures=failures)
