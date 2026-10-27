"""Unit tests for planning/budget.py.

MOCK_* places, prices and rates are made up for arithmetic only: they are not real prices and never reach
a plan. REAL_FX_TRY_USD is a convert_currency result copied verbatim from the kit's replay cache
(ECB reference rates of 2026-10-23), so the adapter is tested against the true tool shape.
No test calls a live service.
"""
from decimal import Decimal

import pytest

from planning.budget import (ActivityCost, BudgetError, ExchangeRate, MissingExchangeRate, PriceQuote, RateTable,
                             apply_to_activity, money, price_activity, quote_from_tool_price, summarize, to_decimal)
from planning.models import Activity, DayPlan, Itinerary
from services.travel_api import check_itinerary, trip_record

MOCK_URL = "https://example.org/mock-listing"
REAL_FX_TRY_USD = {
    "ok": True, "amount": 2750, "from": "TRY", "to": "USD", "rate": 0.020105, "converted": 55.29,
    "rate_date": "2026-10-23", "method": "ecb_reference_rate", "source": "Frankfurter (ECB reference rates)",
    "source_url": "https://api.frankfurter.dev/v1/latest",
    "licence": "Euro foreign exchange reference rates of the European Central Bank, via Frankfurter",
    "mode": "replay", "retrieved_at": "2026-10-23T22:22:37Z",
    "citation": {"id": "fx-ecb-2026-10-23", "title": "ECB reference rates 2026-10-23 (Frankfurter)",
                 "url": "https://api.frankfurter.dev/v1/latest"}}
MOCK_FX_EUR_USD = {"ok": True, "from": "EUR", "to": "USD", "rate": 1.25, "rate_date": "2000-01-01",
                   "method": "ecb_reference_rate", "source": "MOCK", "citation": {"id": "fx-mock"}}
FAILED_FX = {"ok": False, "error": {"code": "unknown_currency", "message": "XYZ is not in the ECB reference rates.",
                                    "status": 422}}


def per_person(amount, currency="USD", high=None):
    return PriceQuote(basis="per_person", currency=currency, amount_min=amount, amount_max=high)


def per_group(amount, currency="USD"):
    return PriceQuote(basis="per_group", currency=currency, amount_min=amount)


def tool_price(lo, hi=None, currency="TRY", raw="MOCK"):
    """The shape of a listing's parsed `price` (travel_data.parse_price)."""
    return {"raw": raw, "parsed": True, "free": False, "currency": currency,
            "amounts": {currency: [lo] if hi is None else [lo, hi]}, "min": lo, "max": hi if hi is not None else lo}


def act(name, cost=None, **kw):
    return Activity(name=name, estimated_cost=cost, source_url=MOCK_URL, **kw)


def itinerary(days, budget=None, travellers=1):
    return Itinerary(destination="Mockville", currency="USD", travellers=travellers, budget_total=budget, days=days)


# ------------------------------------------------------------------ Decimal helpers
def test_to_decimal_is_exact_for_floats():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")  # float: 0.30000000000000004


@pytest.mark.parametrize("bad", [True, None, "abc", float("nan"), float("inf")])
def test_to_decimal_rejects_non_amounts(bad):
    with pytest.raises(BudgetError):
        to_decimal(bad)


@pytest.mark.parametrize("value, cents", [(2.675, "2.68"), (0.125, "0.13"), (0.124, "0.12"), (5500, "5500.00")])
def test_money_rounds_half_up_to_cents(value, cents):
    assert money(value) == Decimal(cents)


# ------------------------------------------------------------------ exchange rates
def test_rate_from_real_tool_result_keeps_its_source():
    r = ExchangeRate.from_tool_result(REAL_FX_TRY_USD)
    assert (r.from_currency, r.to_currency, r.rate) == ("TRY", "USD", Decimal("0.020105"))
    assert (r.rate_date, r.method, r.citation_id) == ("2026-10-23", "ecb_reference_rate", "fx-ecb-2026-10-23")
    assert r.source_url == "https://api.frankfurter.dev/v1/latest"


def test_failed_tool_result_is_an_explicit_error():
    with pytest.raises(BudgetError, match="unknown_currency"):
        ExchangeRate.from_tool_result(FAILED_FX)


@pytest.mark.parametrize("bad", [{"ok": True, "from": "TRY", "to": "USD"},             # no rate
                                 {"ok": True, "from": "TRY", "to": "USD", "rate": 0},  # not a rate
                                 {"ok": True, "from": "TL", "to": "USD", "rate": 1}])  # not a code
def test_incomplete_tool_result_is_refused(bad):
    with pytest.raises(ValueError):
        ExchangeRate.from_tool_result(bad)


def test_rate_table_same_currency_needs_no_rate():
    amount, rate = RateTable().convert(10, "usd", "USD")
    assert amount == 10 and rate is None


def test_rate_table_never_inverts_or_invents():
    table = RateTable.from_tool_results([REAL_FX_TRY_USD])
    with pytest.raises(MissingExchangeRate):
        table.convert(100, "USD", "TRY")  # only TRY->USD was returned by the tool
    with pytest.raises(MissingExchangeRate):
        table.convert(100, "EUR", "USD")


# ------------------------------------------------------------------ PriceQuote
def test_single_price_is_min_and_max():
    q = per_person(25)
    assert q.amount_min == q.amount_max == 25 and not q.is_range


@pytest.mark.parametrize("kw", [dict(basis="per_person", amount_min=10),                        # no currency
                                dict(basis="per_person", currency="USD"),                       # no amount
                                dict(basis="per_person", currency="USD", amount_min=0),         # 0 is "free"
                                dict(basis="per_group", currency="USD", amount_min=20, amount_max=10),
                                dict(basis="free", currency="USD", amount_min=10),
                                dict(basis="unknown", amount_min=10)])
def test_inconsistent_quotes_are_refused(kw):
    with pytest.raises(ValueError):
        PriceQuote(**kw)


def test_quote_from_tool_price_cases():
    assert quote_from_tool_price(None, "per_person").basis == "unknown"
    assert quote_from_tool_price({"raw": "ask", "parsed": False, "free": False, "currency": None, "min": None,
                                  "max": None}, "per_person").basis == "unknown"
    assert quote_from_tool_price({"raw": "Free", "parsed": True, "free": True, "currency": None, "min": 0,
                                  "max": 0}, "per_person").basis == "free"
    q = quote_from_tool_price(tool_price(100), "per_person", source_url=MOCK_URL)
    assert (q.basis, q.currency, q.amount_min, q.amount_max) == ("per_person", "TRY", 100, 100)
    assert q.evidence[0].source_url == MOCK_URL and q.evidence[0].raw == "MOCK"


def test_listed_price_range_stays_a_range():
    q = quote_from_tool_price(tool_price(250, 400), "per_person")
    assert q.is_range and (q.amount_min, q.amount_max) == (250, 400)


def test_conflicting_sources_become_a_range_with_all_evidence():
    # the shape of price_sources when a listing has price_conflict (the kit's synthetic overlay)
    sources = [{"source": "Wikivoyage", "price": tool_price(100), "source_url": MOCK_URL},
               {"source": "MOCK second sheet", "price": tool_price(200), "source_url": None}]
    q = quote_from_tool_price(None, "per_person", price_sources=sources)
    assert (q.amount_min, q.amount_max) == (100, 200)
    assert [e.source for e in q.evidence] == ["Wikivoyage", "MOCK second sheet"]


def test_free_versus_paid_sources_keep_the_disagreement():
    free = {"raw": "Free", "parsed": True, "free": True, "currency": None, "min": 0, "max": 0}
    q = quote_from_tool_price(None, "per_person", price_sources=[{"source": "A", "price": free},
                                                                 {"source": "B", "price": tool_price(80)}])
    assert (q.amount_min, q.amount_max) == (0, 80)


def test_sources_in_different_currencies_are_refused():
    with pytest.raises(BudgetError, match="different currencies"):
        quote_from_tool_price(None, "per_person", price_sources=[{"source": "A", "price": tool_price(10, currency="EUR")},
                                                                 {"source": "B", "price": tool_price(400)}])


# ------------------------------------------------------------------ price_activity
def test_per_person_one_vs_several_travellers():
    assert price_activity(per_person(25), 1, "USD").estimated == Decimal("25.00")
    c = price_activity(per_person(25), 3, "USD")
    assert (c.estimated, c.multiplier, c.status) == (Decimal("75.00"), 3, "estimated")


def test_group_price_is_never_multiplied():
    c = price_activity(per_group(60), 4, "USD")
    assert (c.estimated, c.multiplier) == (Decimal("60.00"), 1)
    assert "for the whole group" in c.note()


def test_free_activity_costs_zero():
    c = price_activity(PriceQuote(basis="free"), 3, "USD")
    assert (c.status, c.estimated) == ("free", Decimal("0.00"))


def test_unknown_price_is_not_free():
    c = price_activity(PriceQuote(basis="unknown"), 2, "USD")
    assert (c.status, c.estimated) == ("unknown", None)
    assert c.missing_reason == "no price in the sources"


def test_range_is_multiplied_at_both_ends_and_budgets_the_high_end():
    c = price_activity(per_person(100, high=200), 2, "USD")
    assert (c.status, c.total_min, c.total_max, c.estimated) == ("range", Decimal("200.00"), Decimal("400.00"),
                                                                Decimal("400.00"))
    assert "sources disagree" in c.note()


def test_conversion_with_a_supplied_rate():
    c = price_activity(per_person(10, currency="EUR"), 2, "USD", RateTable.from_tool_results([MOCK_FX_EUR_USD]))
    assert c.estimated == Decimal("25.00")  # 10 EUR x 2 x 1.25
    assert c.rate.citation_id == "fx-mock"
    assert "EUR->USD 1.25" in c.note() and "2000-01-01" in c.note()


def test_conversion_matches_the_tools_own_result():
    # REAL: 2750 TRY at the cached ECB rate; the tool itself answered converted = 55.29
    c = price_activity(per_person(2750, currency="TRY"), 1, "USD", RateTable.from_tool_results([REAL_FX_TRY_USD]))
    assert c.estimated == Decimal(str(REAL_FX_TRY_USD["converted"]))


def test_missing_rate_makes_an_incomplete_estimate_not_a_guess():
    c = price_activity(per_person(10, currency="EUR"), 1, "USD", RateTable())
    assert (c.status, c.estimated) == ("unknown", None)
    assert "EUR->USD" in c.missing_reason


def test_rounding_happens_once_at_the_end():
    # 33.335 x 3 = 100.005 -> 100.01; rounding per person first would give 33.34 x 3 = 100.02
    assert price_activity(per_person("33.335"), 3, "USD").estimated == Decimal("100.01")


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_travellers_must_be_a_whole_positive_number(bad):
    with pytest.raises(BudgetError):
        price_activity(per_person(10), bad, "USD")


# ------------------------------------------------------------------ apply_to_activity (the bridge to the models)
@pytest.mark.parametrize("quote, status, cost, lo, hi", [
    (per_person(25), "estimated", 50.0, None, None),
    (PriceQuote(basis="free"), "free", 0.0, None, None),
    (PriceQuote(basis="unknown"), "unknown", None, None, None),
    (per_person(100, high=200), "range", 400.0, 200.0, 400.0),
])
def test_apply_sets_the_model_fields(quote, status, cost, lo, hi):
    a = apply_to_activity(act("MOCK Museum"), price_activity(quote, 2, "USD"))
    assert (a.cost_status, a.estimated_cost, a.cost_min, a.cost_max) == (status, cost, lo, hi)


def test_apply_keeps_the_original_and_adds_the_note_once():
    original = act("MOCK Museum", notes="Book online")
    cost = price_activity(per_person(25), 2, "USD")
    a = apply_to_activity(apply_to_activity(original, cost), cost)
    assert original.estimated_cost is None  # the input is not changed
    assert a.notes == "Book online; 25 USD per person x 2 = 50 USD: estimate"


def test_apply_after_a_missing_rate_leaves_the_cost_unknown():
    a = apply_to_activity(act("MOCK Museum"), price_activity(per_person(10, currency="EUR"), 1, "USD"))
    assert a.estimated_cost is None and "EUR->USD" in a.notes


# ------------------------------------------------------------------ summarize
def two_days(extra=None):
    return [DayPlan(date="2026-11-02", activities=[act("MOCK A", 0.1), act("MOCK B", 0.2)]),
            DayPlan(date="2026-11-03", activities=[act("MOCK C", 50)] + (extra or []))]


def test_day_and_trip_totals_are_exact():
    s = summarize(itinerary(two_days(), budget=100))
    assert [d.known_total for d in s.days] == [Decimal("0.30"), Decimal("50.00")]
    assert [d.weekday for d in s.days] == ["Mon", "Tue"]
    assert s.known_total == Decimal("50.30")


def test_within_budget_reports_the_remaining_budget():
    s = summarize(itinerary(two_days(), budget=100))
    assert (s.status, s.remaining, s.overrun, s.within_budget) == ("within_budget", Decimal("49.70"), None, True)


def test_exactly_on_budget_is_within():
    assert summarize(itinerary(two_days(), budget=50.30)).status == "within_budget"


def test_over_budget_reports_the_overrun():
    s = summarize(itinerary(two_days(), budget=40))
    assert (s.status, s.overrun, s.remaining, s.within_budget) == ("over_budget", Decimal("10.30"), None, False)


def test_unknown_costs_make_the_verdict_undetermined():
    s = summarize(itinerary(two_days([act("MOCK Lunch")]), budget=100))
    assert (s.status, s.remaining, s.max_remaining) == ("undetermined", None, Decimal("49.70"))
    assert s.unknown_items == ["2026-11-03 MOCK Lunch"] and not s.cost_complete
    assert s.within_budget is None


def test_known_costs_over_budget_are_over_even_with_unknowns():
    s = summarize(itinerary(two_days([act("MOCK Lunch")]), budget=40))
    assert (s.status, s.overrun) == ("over_budget", Decimal("10.30"))


def test_no_budget():
    s = summarize(itinerary(two_days(), budget=None))
    assert (s.status, s.within_budget, s.remaining) == ("no_budget", None, None)


def test_ranges_stay_visible_in_the_summary():
    rng = act("MOCK Tower", 400, cost_min=200, cost_max=400)
    s = summarize(itinerary([DayPlan(date="2026-11-02", activities=[rng])], budget=300))
    assert (s.known_total, s.low_total) == (Decimal("400.00"), Decimal("200.00"))
    assert s.range_items == ["2026-11-02 MOCK Tower"]
    assert (s.status, s.fits_at_low_estimate) == ("over_budget", True)


@pytest.mark.parametrize("budget, extra", [(100, None), (40, None), (100, "unknown"), (40, "unknown"), (None, None)])
def test_summary_agrees_with_the_model(budget, extra):
    it = itinerary(two_days([act("MOCK Lunch")] if extra else None), budget=budget)
    s = summarize(it)
    assert s.within_budget is it.within_budget
    assert float(s.known_total) == it.estimated_total


# ------------------------------------------------------------------ end to end: price, apply, summarize, save shape
def test_priced_plan_matches_the_trip_store_arithmetic():
    rates = RateTable.from_tool_results([MOCK_FX_EUR_USD])
    raw = [("MOCK Palace", per_person(20, currency="EUR")), ("MOCK Boat", per_group(90)),
           ("MOCK Mosque", PriceQuote(basis="free")), ("MOCK Tower", per_person(10, high=15))]
    acts = [apply_to_activity(act(n), price_activity(q, 2, "USD", rates)) for n, q in raw]
    it = itinerary([DayPlan(date="2026-11-02", activities=acts)], budget=200, travellers=2)
    s = summarize(it)
    assert s.known_total == Decimal("170.00")  # 20 EUR x 2 x 1.25 = 50, + 90 (group), + 0, + 15 x 2 = 30
    assert (s.status, s.remaining) == ("within_budget", Decimal("30.00"))
    payload = it.to_trip_store()
    assert check_itinerary(payload) == []
    rec = trip_record("TRIP-3001", {"traveller_id": "TRV-200", "title": "t", "itinerary": payload}, None)
    assert Decimal(str(rec["computed_total"])) == s.known_total and rec["within_budget"] is True


def test_activity_cost_is_immutable():
    c = price_activity(per_person(25), 1, "USD")
    with pytest.raises(ValueError):
        c.total_max = Decimal("1")
    assert isinstance(c, ActivityCost)
