"""Run diagnostics: who decided what, with no secrets in the output."""
from planning.budget_analyst import BudgetDecision, PlaceDecision, apply_decision, prepare_candidates
from planning.research import ResearchBundle, ResearchedPlace, TripRequest

from app.diagnostics import budget_decisions, research_summary


def _price(amount):
    return {"raw": f"{amount} USD per person", "parsed": True, "currency": "USD", "min": amount, "max": amount}


def _bundle() -> ResearchBundle:
    trip = TripRequest(destination="Amman", currency="USD", travellers=2, budget_total=100,
                       start_date="2026-11-02", end_date="2026-11-04", interests=["history", "markets"])
    places = [
        ResearchedPlace(place_id="citadel", name="Amman Citadel", category="attraction", price=_price(30)),
        ResearchedPlace(place_id="theatre", name="Roman Theater", category="attraction", price=_price(50)),
        ResearchedPlace(place_id="rainbow", name="Rainbow Street", category="attraction",
                        price={"raw": "Free", "free": True}),
        ResearchedPlace(place_id="hashem", name="Hashem", category="restaurant"),
        ResearchedPlace(place_id="fake", name="Test Listing", category="attraction", synthetic=True),
    ]
    return ResearchBundle(trip=trip, places=places)


def _report():
    cands = prepare_candidates(_bundle())
    decision = BudgetDecision(decisions=[
        PlaceDecision(place_id="citadel", decision="include", rank=1, reason="Top history sight"),
        PlaceDecision(place_id="theatre", decision="include", rank=2, reason="Second history sight"),
        PlaceDecision(place_id="rainbow", decision="optional", rank=1, reason="Shopping street"),
        PlaceDecision(place_id="hashem", decision="exclude", reason="Cost unknown,\n off-interest"),
    ])
    return apply_decision(cands, decision)


def test_research_summary_counts_by_category():
    lines = research_summary(_bundle())
    assert "Interest tags used: history, markets" in lines
    assert "Attractions: 4" in lines
    assert "Restaurants: 1" in lines


def test_budget_decisions_separate_llm_choices_from_python_rules():
    report = _report()
    text = "\n".join(budget_decisions(report))

    # 2 x 30 + 2 x 50 = 160 > 100: Python drops the lower-ranked include.
    assert [i.name for i in report.dropped_for_budget] == ["Roman Theater"]

    assert "Selected (required + LLM include) (1):\n  - Amman Citadel" in text
    assert "Optional (LLM, not in plan) (1):\n  - Rainbow Street" in text
    assert "Excluded by LLM (1):\n  - Hashem [restaurant, cost unknown] LLM reason: Cost unknown, off-interest" in text
    assert "Dropped by Python budget rule (over budget) (1):\n  - Roman Theater" in text
    assert "Withheld by Python safety rules (1):\n  - Test Listing: a synthetic test listing" in text


def test_budget_decisions_cut_long_reasons_to_one_line():
    report = _report()
    long = report.excluded[0].model_copy(update={"reason": "x" * 500})
    report = report.model_copy(update={"excluded": [long]})

    line = next(l for l in budget_decisions(report) if "Hashem" in l)
    assert len(line) < 260
    assert line.endswith("…")


def test_stage_llm_use_is_the_difference_of_the_shared_cumulative_counter():
    """The agents share one LLM object whose counters are cumulative: summing the stages counted calls twice."""
    from types import SimpleNamespace
    from app.orchestrator import _performance

    def rep(n, p, c):
        return SimpleNamespace(llm_usage={"successful_requests": n, "prompt_tokens": p, "completion_tokens": c})

    perf = _performance({"planner_ms": 1}, {"budget_analyst": rep(8, 70000, 4800), "planner": rep(10, 82000, 5700),
                                            "verifier": rep(12, 93000, 8000), "revision": rep(0, 0, 0)})
    assert perf["llm"]["planner"] == {"successful_requests": 2, "prompt_tokens": 12000, "completion_tokens": 900}
    assert perf["llm"]["revision"] == {"successful_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    assert perf["llm_total"] == {"successful_requests": 12, "prompt_tokens": 93000, "completion_tokens": 8000}
