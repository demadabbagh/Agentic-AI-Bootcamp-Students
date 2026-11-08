"""Natural-language change requests: the Revision Agent's interpretation step, the rules behind it, currencies,
follow-ups.

Offline: the Istanbul plan of tests/test_trip_revision.py. The LLM is a scripted object with .call (the answers
the real one would give); a convert_currency rate is a stub. Everything Python does with the answer is real.
"""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.memory import get_session, sessions
from app.revision import read_request, run_trip_revision
from planning.budget import ExchangeRate
from planning.revision_interpreter import InterpreterError, build_interpretation, plan_digest
from planning.user_revision import convert_budget_currency, interpret
from test_trip_revision import TRIP_INFO, plan_state, python_verification
from research_fixtures import recorded_lookup


class ScriptedLLM:
    """Answers each call with the next scripted JSON, and keeps what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def call(self, messages, **_):
        self.prompts.append(messages[-1]["content"])
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


# The research already holds EUR->USD 1.1193 (and TRY->USD): a GBP budget needs a convert_currency lookup.
GBP_USD = ExchangeRate(from_currency="GBP", to_currency="USD", rate=Decimal("1.30"), rate_date="2026-11-06",
                       method="ecb_reference_rate", source="Frankfurter (ECB reference rates)")


@pytest.fixture()
def session(monkeypatch):
    monkeypatch.setenv("RIHLA_REVISION_INTERPRETER", "agent")
    sessions.clear()
    s = get_session("nl")
    s["plan_state"] = plan_state()
    s["itinerary"] = s["plan_state"].itinerary.to_trip_store()
    return s


def revise(session, request, llm, rate=GBP_USD):
    def make_decide(llm_, b, report, run):
        return lambda points: {p.id: p.default for p in points}
    with patch("app.verification_adapter.run_verification_pipeline", side_effect=python_verification), \
            patch("app.revision.make_decide", make_decide), \
            patch("app.revision._fetch_rate", lambda a, b: rate if rate and (a, b) == ("GBP", "USD") else None):
        return run_trip_revision(session, request, TRIP_INFO, llm=llm, lookups=(None, recorded_lookup()))


def ref(st, name):
    _, refs = plan_digest(st)
    return next(r for r, (_, _, n) in refs.items() if n == name)


# ------------------------------------------------------------------ the agent's reading, checked by Python
def test_the_agent_asks_one_question_for_an_ambiguous_currency_request(session):
    llm = ScriptedLLM({"action": "clarify", "question": "Should your total trip budget be 20 EUR?"})
    version = session["plan_state"].version
    out = revise(session, "exchange to 20 euro", llm)
    assert out["status"] == "needs_clarification" and "20 EUR" in out["message"]
    assert out["revision"]["interpreted_by"] == "revision_agent"
    assert session["plan_state"].version == version  # nothing changed
    assert "exchange to 20 euro" in llm.prompts[0] and "Basilica Cistern" in llm.prompts[0]  # it saw the plan


def test_a_budget_in_pounds_is_converted_with_a_fetched_rate_and_applied(session):
    llm = ScriptedLLM({"action": "apply", "operations": [{"kind": "set_budget", "amount": 500, "currency": "GBP"}]})
    out = revise(session, "Change my total budget to 500 pounds", llm)
    assert out["revision"]["outcome"] == "revised"
    assert out["revision"]["budget_after"] == "650.00"  # 500 GBP x 1.30, in the trip's currency (USD)
    assert "500.00 GBP = 650.00 USD" in out["revision"]["understood"][0]
    assert session["plan_state"].bundle.trip.budget_total == Decimal("650.00")


def test_without_a_verified_rate_the_budget_is_not_changed(session):
    llm = ScriptedLLM({"action": "apply", "operations": [{"kind": "set_budget", "amount": 500, "currency": "GBP"}]})
    version = session["plan_state"].version
    out = revise(session, "Set my budget to 500 pounds", llm, rate=None)
    assert out["status"] == "revision_not_feasible" and "verified exchange rate" in out["message"]
    assert session["plan_state"].version == version


def test_follow_ups_reach_the_agent_with_the_dialogue(session):
    st = session["plan_state"]
    llm = ScriptedLLM({"action": "clarify", "question": "Which activity on Day 1: Süleymaniye Mosque or Grand Bazaar?"},
                      {"action": "apply", "operations": [{"kind": "move", "activity": ref(st, "Grand Bazaar"),
                                                          "to_day": 3}]})
    assert revise(session, "Move an activity from Day 1 to Day 3", llm)["status"] == "needs_clarification"
    out = revise(session, "the bazaar", llm)
    assert out["revision"]["outcome"] == "revised"
    assert "Grand Bazaar" in [a["name"] for a in out["itinerary"]["days"][2]["activities"]]
    assert "Which activity on Day 1" in llm.prompts[1]  # the question it asked is part of the second prompt


def test_an_answer_naming_an_activity_not_in_the_plan_falls_back_to_the_rules(session):
    llm = ScriptedLLM({"action": "apply", "operations": [{"kind": "remove", "activity": "D9A9"}]})
    i = read_request(session, "Remove the museum on Day 2", session["plan_state"], llm)
    assert i.source == "rules" and [o.kind for o in i.ops] == ["remove"] and "D9A9" in i.agent_error


def test_an_llm_failure_falls_back_to_the_rules(session):
    llm = ScriptedLLM(TimeoutError("provider"))
    i = read_request(session, "Reduce my budget by 20%", session["plan_state"], llm)
    assert i.source == "rules" and i.ops[0].amount == Decimal("560.00")


def test_the_agent_cannot_remove_a_place_the_traveller_asked_for():
    st = plan_state()
    _, refs = plan_digest(st)
    out = build_interpretation({"action": "apply", "operations": [{"kind": "remove", "activity": ref(st, "Topkapı Palace")}]},
                               st, refs)
    assert out.infeasible and "is a place you asked to visit" in out.infeasible


def test_python_writes_the_notes_and_checks_days():
    st = plan_state()
    _, refs = plan_digest(st)
    out = build_interpretation({"action": "apply", "operations": [
        {"kind": "change_budget_percent", "percent": 20, "direction": "decrease"},
        {"kind": "add", "interest": "shopping"}, {"kind": "start_time", "time": "10:00", "all_days": True}]}, st, refs)
    assert [o.kind for o in out.ops] == ["set_budget", "add", "start_time", "start_time", "start_time"]
    assert out.ops[0].amount == Decimal("560.00") and out.ops[1].tag == "markets"
    late = build_interpretation({"action": "apply", "operations": [{"kind": "lighten_day", "day": 7}]}, st, refs)
    assert late.infeasible == "The trip has 3 days, so there is no Day 7."
    with pytest.raises(InterpreterError):
        build_interpretation({"action": "apply", "operations": [{"kind": "book_hotel"}]}, st, refs)


# ------------------------------------------------------------------ the rules (no LLM)
@pytest.mark.parametrize("request_text, kinds", [
    ("Change my total budget to 200 EUR", ["set_budget"]),
    ("Replace an expensive restaurant with a cheaper one", ["cheaper"]),
    ("Add more shopping and cultural activities", ["add", "add"]),
    ("Reduce walking time", ["walk_limit"]),
    ("Change the departure time to 10 AM every day", ["start_time"] * 3),
    ("Make the itinerary more suitable for families", ["family_friendly"]),
])
def test_the_rules_read_the_requested_phrasings(request_text, kinds):
    assert [o.kind for o in interpret(request_text, plan_state()).ops] == kinds


def test_an_amount_in_another_currency_is_never_taken_as_the_trips():
    st = plan_state()
    op = interpret("Change my total budget to 200 EUR", st).ops[0]
    assert (op.amount, op.currency) == (Decimal("200.00"), "EUR")
    fetched = []
    converted = convert_budget_currency(interpret("Change my total budget to 200 EUR", st), st,
                                        lambda a, b: fetched.append((a, b)))
    # the research's own EUR->USD rate (1.1193), no new lookup
    assert converted.ops[0].amount == Decimal("223.86") and converted.ops[0].currency is None and not fetched


@pytest.mark.parametrize("request_text, expected", [
    ("exchange to 20 euro", "Do you want your total trip budget to be 20 EUR?"),
    ("Change the departure time to 10 AM", "Which day should start at 10:00"),
    ("Move an activity from Day 1 to Day 3", "Which activity on Day 1"),
])
def test_the_rules_ask_instead_of_guessing(request_text, expected):
    assert expected in interpret(request_text, plan_state()).clarification


def test_showing_the_trip_in_another_currency_is_explained():
    assert "set your budget in EUR" in interpret("Show prices in euros", plan_state()).infeasible


def test_an_answer_to_the_rules_question_is_read_with_the_request(monkeypatch):
    monkeypatch.setenv("RIHLA_REVISION_INTERPRETER", "rules")
    sessions.clear()
    s = get_session("nl")
    s["plan_state"] = plan_state()
    first = revise(s, "Change the departure time to 10 AM", object())
    assert first["status"] == "needs_clarification"
    out = revise(s, "every day", object())
    assert out["revision"]["outcome"] == "revised"
    assert all(d["activities"][0]["start_time"] == "10:00" for d in out["itinerary"]["days"])


def test_a_family_pace_lowers_the_walking_limit(monkeypatch):
    monkeypatch.setenv("RIHLA_REVISION_INTERPRETER", "rules")
    sessions.clear()
    s = get_session("nl")
    s["plan_state"] = plan_state()
    out = revise(s, "Make the itinerary more suitable for families", object())
    assert out["revision"]["outcome"] == "revised", out["message"]
    assert s["plan_state"].rules.max_walk_minutes == 20


def test_an_indefinite_target_is_asked_about_even_when_the_agent_guesses(session):
    st = session["plan_state"]  # Day 1 has two activities: the agent picking one is a guess
    llm = ScriptedLLM({"action": "apply", "operations": [{"kind": "move", "activity": ref(st, "Grand Bazaar"),
                                                          "to_day": 3}]})
    out = revise(session, "Move an activity from Day 1 to Day 3", llm)
    assert out["status"] == "needs_clarification" and "Which activity on Day 1" in out["message"]
    assert session["plan_state"].version == st.version


def test_one_impossible_half_of_a_two_part_add_does_not_block_the_other(session):
    llm = ScriptedLLM({"action": "apply", "operations": [{"kind": "add", "interest": "nature"},
                                                         {"kind": "add", "interest": "food"}]})
    out = revise(session, "Add more nature and food experiences", llm)
    # the research has no other nature place (tests/test_trip_revision.py): the food half is still applied
    assert out["revision"]["outcome"] == "revised", out["message"]
    assert any("found no other nature place" in n for n in out["revision"]["notes"])
    assert any("added" in c for c in out["revision"]["changes"])


def test_start_time_lines_read_in_day_order_and_say_when_a_day_already_started_then(monkeypatch):
    monkeypatch.setenv("RIHLA_REVISION_INTERPRETER", "rules")
    sessions.clear()
    s = get_session("nl")
    s["plan_state"] = plan_state()
    first_start = s["plan_state"].itinerary.days[0].activities[0].start_time
    out = revise(s, f"Start every day at {first_start}", object())
    lines = [c for c in out["revision"]["changes"] if "start" in c]
    assert [l.split(" (")[0] for l in lines] == ["Day 1", "Day 2", "Day 3"]
    assert lines[0].endswith(f"already started at {first_start}.")
