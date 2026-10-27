"""Deterministic budget arithmetic for the trip plan: no LLM maths, no invented numbers.

Two jobs:
  pricing      PriceQuote (a price as the evidence states it) -> price_activity() -> ActivityCost
               (whole party, trip currency), written into an Activity with apply_to_activity()
  summarising  summarize(itinerary) -> BudgetSummary: per-day and trip totals, within / over / undetermined
               (budget_verdict() is that verdict alone, for costs not in an itinerary yet: the Budget Analyst)

Rules:
  - Decimal for every amount; one rounding step, to cents (ROUND_HALF_UP), at the end of each calculation.
  - A per-person price is multiplied by the travellers; a per-group price never is.
  - Exchange rates come only from convert_currency tool results (ExchangeRate.from_tool_result).
    A missing rate makes the cost unknown (with the reason), never a guessed number.
  - Unknown is not free. A range stays a range; the higher end is the one we budget with.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Activity, Itinerary

CENT = Decimal("0.01")
PriceBasis = Literal["per_person", "per_group", "free", "unknown"]
CostStatus = Literal["estimated", "range", "free", "unknown"]  # the same values as Activity.cost_status
BudgetStatus = Literal["within_budget", "over_budget", "undetermined", "no_budget"]


class BudgetError(ValueError):
    """Evidence the calculator cannot use: a failed tool result, a bad rate, mixed currencies."""


class MissingExchangeRate(BudgetError):
    def __init__(self, from_currency: str, to_currency: str):
        self.from_currency, self.to_currency = from_currency, to_currency
        super().__init__(f"No exchange rate {from_currency}->{to_currency} from a convert_currency result.")


def to_decimal(value) -> Decimal:
    """An exact Decimal from int, float, str or Decimal. Floats go through str: 0.1 -> Decimal('0.1')."""
    if isinstance(value, bool) or value is None:
        raise BudgetError(f"not an amount: {value!r}")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BudgetError(f"not an amount: {value!r}") from None
    if not d.is_finite():
        raise BudgetError(f"not a finite amount: {value!r}")
    return d


def money(value) -> Decimal:
    """Round to cents, half up (2.675 -> 2.68), the way people expect money to round."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _fmt(d: Decimal) -> str:
    """5500.00 -> '5500', 55.29 -> '55.29', 0.020105 -> '0.020105'."""
    s = f"{d:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _currency(code) -> str:
    if not isinstance(code, str) or len(code.strip()) != 3 or not code.strip().isalpha():
        raise BudgetError(f"not a 3-letter currency code: {code!r}")
    return code.strip().upper()


# ------------------------------------------------------------------ exchange rates (from tool results only)
class ExchangeRate(BaseModel):
    """One rate the convert_currency tool returned, with where it came from."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_currency: str
    to_currency: str
    rate: Decimal = Field(gt=0)
    rate_date: str | None = None
    method: str | None = None  # ecb_reference_rate | fixed_peg_via_usd
    source: str | None = None
    source_url: str | None = None
    citation_id: str | None = None

    @field_validator("from_currency", "to_currency", mode="before")
    @classmethod
    def _code(cls, v):
        return _currency(v)

    @classmethod
    def from_tool_result(cls, result: dict) -> ExchangeRate:
        """Build from a convert_currency result ({"ok": true, "from", "to", "rate", ...}); refuse a failed one."""
        if not isinstance(result, dict):
            raise BudgetError("convert_currency result must be a dict")
        if result.get("ok") is False:
            err = result.get("error") or {}
            raise BudgetError(f"convert_currency failed ({err.get('code', 'error')}): {err.get('message', '')}".strip())
        missing = [k for k in ("from", "to", "rate") if result.get(k) is None]
        if missing:
            raise BudgetError(f"convert_currency result is missing {missing}")
        return cls(from_currency=result["from"], to_currency=result["to"], rate=to_decimal(result["rate"]),
                   rate_date=result.get("rate_date"), method=result.get("method"), source=result.get("source"),
                   source_url=result.get("source_url"), citation_id=(result.get("citation") or {}).get("id"))

    def describe(self) -> str:
        how = "Central Bank of Jordan peg via USD" if self.method == "fixed_peg_via_usd" else "ECB reference rate"
        return f"{self.from_currency}->{self.to_currency} {_fmt(self.rate)} ({how}{', ' + self.rate_date if self.rate_date else ''})"


class RateTable:
    """The rates this session has from tool results. Only the exact direction asked for: no inverting,
    no chaining (USD->TRY is not derived from TRY->USD), so every number traces back to one tool result."""

    def __init__(self, rates: list[ExchangeRate] | None = None):
        self._rates: dict[tuple[str, str], ExchangeRate] = {}
        for r in rates or []:
            self.add(r)

    @classmethod
    def from_tool_results(cls, results: list[dict]) -> RateTable:
        return cls([ExchangeRate.from_tool_result(r) for r in results])

    def add(self, rate: ExchangeRate) -> None:
        self._rates[(rate.from_currency, rate.to_currency)] = rate

    def get(self, from_currency: str, to_currency: str) -> ExchangeRate | None:
        return self._rates.get((_currency(from_currency), _currency(to_currency)))

    def convert(self, amount, from_currency: str, to_currency: str) -> tuple[Decimal, ExchangeRate | None]:
        """Unrounded converted amount and the rate used (None for the same currency). Raises MissingExchangeRate."""
        src, dst = _currency(from_currency), _currency(to_currency)
        if src == dst:
            return to_decimal(amount), None
        rate = self.get(src, dst)
        if rate is None:
            raise MissingExchangeRate(src, dst)
        return to_decimal(amount) * rate.rate, rate


# ------------------------------------------------------------------ prices as the evidence states them
class PriceEvidence(BaseModel):
    """Where one price came from (a Wikivoyage listing, a second price source...)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str | None = None
    source_url: str | None = None
    raw: str | None = None  # the price text as listed: the authority when parsing is in doubt


class PriceQuote(BaseModel):
    """A price in the source's own currency and terms. amount_min < amount_max means a range."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: PriceBasis
    currency: str | None = None
    amount_min: Decimal | None = Field(default=None, ge=0)  # 0 only as the low end of "free vs paid"
    amount_max: Decimal | None = Field(default=None, gt=0)
    evidence: list[PriceEvidence] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def _code(cls, v):
        return None if v is None else _currency(v)

    @field_validator("amount_min", "amount_max", mode="before")
    @classmethod
    def _exact(cls, v):
        return None if v is None else to_decimal(v)

    @model_validator(mode="before")
    @classmethod
    def _single_price(cls, data):
        if isinstance(data, dict) and data.get("amount_max") is None and data.get("amount_min") is not None:
            data = {**data, "amount_max": data["amount_min"]}  # one price: min == max
        return data

    @model_validator(mode="after")
    def _consistent(self) -> PriceQuote:
        if self.basis in ("free", "unknown"):
            if self.amount_min is not None or self.amount_max is not None:
                raise ValueError(f"a '{self.basis}' price has no amounts")
            return self
        if self.currency is None or self.amount_min is None:
            raise ValueError("a paid price needs a currency and an amount above 0 (0 is basis 'free')")
        if self.amount_max < self.amount_min:
            raise ValueError("amount_max is below amount_min")
        return self

    @property
    def is_range(self) -> bool:
        return self.amount_min is not None and self.amount_min < self.amount_max


def quote_from_tool_price(price: dict | None, basis: Literal["per_person", "per_group"], *,
                          source_url: str | None = None, source: str = "Wikivoyage",
                          price_sources: list[dict] | None = None) -> PriceQuote:
    """A PriceQuote from the parsed `price` of a find_attractions / get_opening_hours result.

    `basis` is the caller's decision: listings do not say whether a price is per person or per group.
    `price_sources` (only when the listing has price_conflict) widens the quote to the range of all sources.
    """
    entries = ([(p.get("price"), p.get("source"), p.get("source_url")) for p in price_sources]
               if price_sources else [(price, source, source_url)])
    evidence, currencies, lows, highs = [], set(), [], []
    free_seen = False
    for p, src, url in entries:
        evidence.append(PriceEvidence(source=src, source_url=url, raw=(p or {}).get("raw")))
        if not p:
            continue
        if p.get("free"):
            free_seen = True
        elif p.get("parsed") and p.get("currency") and p.get("min") is not None:
            currencies.add(p["currency"])
            lows.append(to_decimal(p["min"]))
            highs.append(to_decimal(p.get("max") if p.get("max") is not None else p["min"]))
    if len(currencies) > 1:
        raise BudgetError(f"price sources quote different currencies {sorted(currencies)}: convert them first")
    if lows:  # a paid price exists; a "free" source next to it widens the range down to 0
        low = Decimal(0) if free_seen else min(lows)
        return PriceQuote(basis=basis, currency=currencies.pop(), amount_min=low, amount_max=max(highs),
                          evidence=evidence)
    if free_seen:
        return PriceQuote(basis="free", evidence=evidence)
    return PriceQuote(basis="unknown", evidence=evidence)


# ------------------------------------------------------------------ one activity, the whole party
class ActivityCost(BaseModel):
    """What an activity costs the whole party in the trip currency, and how we got there."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CostStatus
    currency: str  # the trip currency
    travellers: int
    quote: PriceQuote
    multiplier: int  # travellers for per_person, 1 for per_group
    total_min: Decimal | None = None
    total_max: Decimal | None = None
    rate: ExchangeRate | None = None
    missing_reason: str | None = None  # why an unknown is unknown: no price, or no exchange rate

    @property
    def estimated(self) -> Decimal | None:
        """The amount we budget with: the higher end of a range (conservative)."""
        return self.total_max

    def note(self) -> str:
        """One line a traveller or advisor can check: amounts, multiplication, conversion."""
        q = self.quote
        if self.status == "free":
            return "Listed as free"
        if self.status == "unknown":
            return f"Cost unknown: {self.missing_reason}"
        unit = _fmt(q.amount_min) if not q.is_range else f"{_fmt(q.amount_min)}-{_fmt(q.amount_max)}"
        terms = (f"per person x {self.travellers}" if q.basis == "per_person" else "for the whole group")
        text = f"{unit} {q.currency} {terms}"
        if self.rate is not None:
            text += f", converted {self.rate.describe()}"
        total = _fmt(self.total_min) if self.status == "estimated" else f"{_fmt(self.total_min)}-{_fmt(self.total_max)}"
        text += f" = {total} {self.currency}"
        if self.status == "range":
            text += f" (sources disagree; {_fmt(self.total_max)} used for the budget)"
        return text + ": estimate"


def price_activity(quote: PriceQuote, travellers: int, currency: str, rates: RateTable | None = None) -> ActivityCost:
    """Total for the whole party in `currency`. Multiply in the source currency, convert, round once."""
    if isinstance(travellers, bool) or not isinstance(travellers, int) or travellers < 1:
        raise BudgetError("travellers must be a whole number >= 1")
    currency = _currency(currency)
    multiplier = travellers if quote.basis == "per_person" else 1
    base = dict(currency=currency, travellers=travellers, quote=quote, multiplier=multiplier)
    if quote.basis == "unknown":
        return ActivityCost(status="unknown", missing_reason="no price in the sources", **base)
    if quote.basis == "free":
        return ActivityCost(status="free", total_min=Decimal("0.00"), total_max=Decimal("0.00"), **base)
    rates = rates or RateTable()
    try:
        low, rate = rates.convert(quote.amount_min * multiplier, quote.currency, currency)
        high, _ = rates.convert(quote.amount_max * multiplier, quote.currency, currency)
    except MissingExchangeRate as exc:
        return ActivityCost(status="unknown", missing_reason=f"no {exc.from_currency}->{exc.to_currency} "
                            "exchange rate from a tool result", **base)
    low, high = money(low), money(high)
    return ActivityCost(status="range" if low < high else "estimated", total_min=low, total_max=high,
                        rate=rate, **base)


def apply_to_activity(activity: Activity, cost: ActivityCost, *, add_note: bool = True) -> Activity:
    """A new Activity with the cost fields set, validated again by the model's own rules."""
    data = activity.model_dump()
    data.update(cost_status=cost.status,
                estimated_cost=None if cost.estimated is None else float(cost.estimated),
                cost_min=float(cost.total_min) if cost.status == "range" else None,
                cost_max=float(cost.total_max) if cost.status == "range" else None)
    if add_note:
        line = cost.note()
        if line not in (data.get("notes") or ""):
            data["notes"] = f"{data['notes']}; {line}" if data.get("notes") else line
    return Activity.model_validate(data)


# ------------------------------------------------------------------ the whole itinerary
class DayBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: dt.date
    weekday: str
    known_total: Decimal  # the budgeted amounts (the higher end of ranges)
    low_total: Decimal  # the same with the lower end of ranges
    unknown_items: list[str]
    range_items: list[str]


class BudgetVerdict(BaseModel):
    """The verdict on a set of costs against a budget. Which fields are set depends on the status."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: BudgetStatus
    remaining: Decimal | None = None  # only when every cost is known and the plan fits
    overrun: Decimal | None = None  # only when the known costs already exceed the budget
    max_remaining: Decimal | None = None  # undetermined: what is left at most, before the unknown costs
    fits_at_low_estimate: bool | None = None  # over budget with the high ends, but within with the low ends?


def budget_verdict(known_total, low_total, has_unknown: bool, budget_total) -> BudgetVerdict:
    """within / over / undetermined / no_budget for costs already added up (high and low ends of ranges).

    Over budget wins over unknown: known costs above the budget are over, whatever the unknown costs are.
    """
    if budget_total is None:
        return BudgetVerdict(status="no_budget")
    known, low, budget = money(known_total), money(low_total), money(budget_total)
    if known > budget:
        return BudgetVerdict(status="over_budget", overrun=known - budget, fits_at_low_estimate=low <= budget)
    if has_unknown:
        return BudgetVerdict(status="undetermined", max_remaining=budget - known)
    return BudgetVerdict(status="within_budget", remaining=budget - known)


class BudgetSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str
    travellers: int
    budget_total: Decimal | None
    days: list[DayBudget]
    known_total: Decimal
    low_total: Decimal
    unknown_items: list[str]  # "2026-11-02 Osman Gourmet Restaurant"
    range_items: list[str]
    status: BudgetStatus
    remaining: Decimal | None = None  # the BudgetVerdict fields
    overrun: Decimal | None = None
    max_remaining: Decimal | None = None
    fits_at_low_estimate: bool | None = None

    @property
    def cost_complete(self) -> bool:
        return not self.unknown_items

    @property
    def within_budget(self) -> bool | None:
        """The same three answers as Itinerary.within_budget."""
        return {"within_budget": True, "over_budget": False}.get(self.status)


def summarize(itinerary: Itinerary) -> BudgetSummary:
    """Per-day and trip totals in Decimal, and an honest verdict on the budget."""
    days, unknown, ranges = [], [], []
    for d in itinerary.days:
        known = low = Decimal("0")
        d_unknown, d_ranges = [], []
        for a in d.activities:
            if a.estimated_cost is None:
                d_unknown.append(a.name)
                continue
            known += to_decimal(a.estimated_cost)
            low += to_decimal(a.cost_min if a.cost_status == "range" else a.estimated_cost)
            if a.cost_status == "range":
                d_ranges.append(a.name)
        days.append(DayBudget(date=d.date, weekday=d.weekday, known_total=money(known), low_total=money(low),
                              unknown_items=d_unknown, range_items=d_ranges))
        unknown += [f"{d.date.isoformat()} {n}" for n in d_unknown]
        ranges += [f"{d.date.isoformat()} {n}" for n in d_ranges]
    known_total = money(sum((x.known_total for x in days), Decimal("0")))
    low_total = money(sum((x.low_total for x in days), Decimal("0")))
    budget = None if itinerary.budget_total is None else money(itinerary.budget_total)
    verdict = budget_verdict(known_total, low_total, bool(unknown), budget)
    return BudgetSummary(currency=itinerary.currency, travellers=itinerary.travellers, budget_total=budget,
                         days=days, known_total=known_total, low_total=low_total, unknown_items=unknown,
                         range_items=ranges, **verdict.model_dump())
