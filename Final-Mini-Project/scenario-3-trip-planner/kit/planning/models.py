"""The trip plan as Pydantic models: Activity, DayPlan, Itinerary.

The plan is the crew's shared state: the planner builds it, the verifier checks it, a revision changes it,
and Itinerary.to_trip_store() turns it into the itinerary dict that save_trip accepts (the schema checked
by check_itinerary in services/travel_api.py).

Two layers of checks:
  these models   shape: types, formats, ranges, and the internal consistency of one object
  the verifier   meaning: opening hours, overlaps, walking times, budget fit (a later milestone)
A draft with an overlap is a valid model with a verifier issue, not a crash.

Money is for the whole party, in the itinerary's currency. A missing price is None ("unknown"), never 0.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

# The same weekday labels as services.travel_data.DAYS: the Trip Store's by_weekday keys.
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _iso_date(value):
    """Accept a date or a 'YYYY-MM-DD' string, nothing else (no timestamps, no '20261102')."""
    if isinstance(value, dt.datetime):
        raise ValueError("must be a date (YYYY-MM-DD), not a date and time")
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"{value!r} is not a real calendar date") from None
    raise ValueError("must be a date like 2026-11-02")


IsoDate = Annotated[dt.date, BeforeValidator(_iso_date)]
# Stricter than the Trip Store's \d{2}:\d{2}: it also rejects 24:00 and 12:60.
Time = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
# strict: no True/False and no "28" strings; allow_inf_nan=False: no NaN or infinity.
Money = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]

# estimated  one sourced price (an estimate: listing prices can be years old)
# range      sources disagree: cost_min..cost_max, estimated_cost is the conservative value we budget with
# free       the source says it is free: estimated_cost is 0
# unknown    no price in the sources: estimated_cost is None
CostStatus = Literal["estimated", "range", "free", "unknown"]


def _fmt(amount: float) -> str:
    return f"{amount:.0f}" if float(amount).is_integer() else f"{amount:.2f}"


class Activity(BaseModel):
    """One stop in a day: a sight, a meal, a market. Its facts come from a tool result (source_url)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # --- sent to the Trip Store
    name: str = Field(min_length=1, max_length=200)  # the listing name exactly as the tool returned it
    start_time: Time | None = None
    end_time: Time | None = None
    estimated_cost: Money | None = None  # whole party, itinerary currency; None = unknown
    source_url: str | None = None
    notes: str | None = None
    # --- internal (kept in session state, not sent to the Trip Store)
    cost_status: CostStatus | None = None  # derived from the cost fields when left out
    cost_min: Money | None = None
    cost_max: Money | None = None
    place_id: str | None = None  # the listing id, e.g. wv-istanbul-topkapi-palace
    kind: Literal["see", "do", "buy", "eat", "drink"] | None = None  # the Wikivoyage listing kind
    duration_is_estimate: bool = True  # no tool gives visit durations: they are planning estimates
    travel_minutes_from_previous: Annotated[int, Field(ge=0, strict=True)] | None = None
    travel_mode_from_previous: Literal["foot", "driving"] | None = None  # how those minutes travel (measured only)

    @field_validator("source_url")
    @classmethod
    def _web_url(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"https?://\S+$", v):
            raise ValueError("source_url must be an http(s) link from a tool result")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Activity:
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("give both start_time and end_time, or neither")
        if self.start_time is not None and self.end_time <= self.start_time:
            raise ValueError(f"end_time {self.end_time} must be after start_time {self.start_time}")

        has_range = self.cost_min is not None or self.cost_max is not None
        if has_range and (self.cost_min is None or self.cost_max is None):
            raise ValueError("a price range needs both cost_min and cost_max")
        if self.cost_status is None:  # derive it, but never guess "free"
            if self.estimated_cost is None:
                self.cost_status = "unknown"
            elif has_range:
                self.cost_status = "range"
            elif self.estimated_cost == 0:
                raise ValueError("estimated_cost 0: set cost_status='free' only if the source says it is free")
            else:
                self.cost_status = "estimated"

        status, cost = self.cost_status, self.estimated_cost
        if status == "unknown" and (cost is not None or has_range):
            raise ValueError("cost_status 'unknown' means no estimated_cost and no range")
        if status == "free" and (cost != 0 or has_range):
            raise ValueError("cost_status 'free' means estimated_cost 0 and no range")
        if status == "estimated" and (cost is None or has_range):
            raise ValueError("cost_status 'estimated' needs an estimated_cost and no range (use 'range')")
        if status == "range":
            if cost is None or not has_range:
                raise ValueError("cost_status 'range' needs estimated_cost, cost_min and cost_max")
            if not self.cost_min < self.cost_max:
                raise ValueError("cost_min must be lower than cost_max (equal prices are not a range)")
            if not self.cost_min <= cost <= self.cost_max:
                raise ValueError("estimated_cost must lie inside cost_min..cost_max")
        return self

    @property
    def duration_minutes(self) -> int | None:
        if self.start_time is None:
            return None
        h0, m0 = map(int, self.start_time.split(":"))
        h1, m1 = map(int, self.end_time.split(":"))
        return (h1 * 60 + m1) - (h0 * 60 + m0)

    def store_notes(self, currency: str) -> str | None:
        """The traveller's notes plus honest labels for the cost and the timing."""
        labels = {"estimated": "Estimated cost from the listed price",
                  "free": "Listed as free",
                  "unknown": "Cost unknown: no price in the sources"}
        parts = [self.notes] if self.notes else []
        if self.cost_status == "range":
            parts.append(f"Sources disagree: {_fmt(self.cost_min)}-{_fmt(self.cost_max)} {currency}; "
                         f"{_fmt(self.estimated_cost)} {currency} used for the budget (estimate)")
        elif labels[self.cost_status] not in (self.notes or ""):  # the cost note may already say it
            parts.append(labels[self.cost_status])
        if self.start_time is not None and self.duration_is_estimate:
            parts.append("Duration is a planning estimate")
        return "; ".join(parts) or None

    def to_trip_store(self, currency: str) -> dict:
        out: dict = {"name": self.name}
        if self.start_time is not None:
            out.update(start_time=self.start_time, end_time=self.end_time)
        out["estimated_cost"] = self.estimated_cost  # always present: null says "unknown", 0 would say "free"
        if self.source_url is not None:
            out["source_url"] = self.source_url
        notes = self.store_notes(currency)
        if notes:
            out["notes"] = notes
        return out


class DayPlan(BaseModel):
    """One calendar day of the trip and its activities, in the order they happen."""
    model_config = ConfigDict(extra="forbid")

    date: IsoDate
    activities: list[Activity] = Field(default_factory=list)

    # Plain properties, not computed fields: model_dump() then re-validates under extra="forbid".
    @property
    def weekday(self) -> str:
        return WEEKDAYS[self.date.weekday()]

    @property
    def known_cost(self) -> float:
        return round(sum(a.estimated_cost for a in self.activities if a.estimated_cost is not None), 2)

    @property
    def unknown_cost_activities(self) -> list[Activity]:
        return [a for a in self.activities if a.estimated_cost is None]


class Itinerary(BaseModel):
    """The whole plan. estimated_total is always computed from the activities, never typed in."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")  # ISO code, e.g. USD, TRY, JOD
    travellers: Annotated[int, Field(ge=1, le=50, strict=True)] = 1
    budget_total: Money | None = None
    days: list[DayPlan] = Field(min_length=1)

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _days_in_order(self) -> Itinerary:
        dates = [d.date for d in self.days]
        for earlier, later in zip(dates, dates[1:]):
            if later <= earlier:
                raise ValueError(f"days must be in date order without repeats ({earlier} then {later})")
        if not any(d.activities for d in self.days):
            raise ValueError("the itinerary has no activities")  # the Trip Store rejects it too
        return self

    @property
    def start_date(self) -> dt.date:
        return self.days[0].date

    @property
    def end_date(self) -> dt.date:
        return self.days[-1].date

    @property
    def estimated_total(self) -> float:
        """Sum of the known costs. With unknown costs it is a lower bound: see cost_complete."""
        return round(sum(d.known_cost for d in self.days), 2)

    @property
    def unknown_cost_activities(self) -> list[tuple[dt.date, Activity]]:
        return [(d.date, a) for d in self.days for a in d.unknown_cost_activities]

    @property
    def cost_complete(self) -> bool:
        return not self.unknown_cost_activities

    @property
    def within_budget(self) -> bool | None:
        """True only when every cost is known and the total fits. None = no budget, or cannot confirm."""
        if self.budget_total is None:
            return None
        if self.estimated_total > self.budget_total:
            return False  # already over, whatever the unknown costs are
        return True if self.cost_complete else None

    def to_trip_store(self) -> dict:
        """The itinerary dict for save_trip: only the keys the Trip Store schema documents."""
        out: dict = {"destination": self.destination, "currency": self.currency, "travellers": self.travellers}
        if self.budget_total is not None:
            out["budget_total"] = self.budget_total
        out["estimated_total"] = self.estimated_total
        out["days"] = [{"date": d.date.isoformat(),
                        "activities": [a.to_trip_store(self.currency) for a in d.activities]}
                       for d in self.days]
        return out
