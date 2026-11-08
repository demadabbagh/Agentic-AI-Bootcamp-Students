"""The Revision Agent's interpretation step: a traveller's change request, read by the LLM against THIS plan.

    "exchange the budget to 200 euro"  ->  interpret_with_agent()  ->  Interpretation(ops=[set_budget 200 EUR])
                                                                    (then convert_budget_currency: a real rate)

The rules in planning/user_revision.py read the requests they were written for. Anything phrased differently
("lower what I spend on food", "Day 2 is too much", "make it work for my kids", a follow-up such as "do the same
for Day 3" or an answer to the question just asked) is read here, by the same LLM the Revision Agent uses
(app/planning_adapter.create_planning_llm), in one call:

  - The LLM sees a digest of the current plan: each day's activities by reference (D2A1...), their kind, times,
    known cost and research tags, the budget and currency, the walking limit, which interests the research has
    alternatives for, the last accepted changes, and the recent revision dialogue.
  - It answers with JSON only: operations from a closed list (the same RevisionOp kinds Python applies), or ONE
    short clarification question, or why the request is not something a revision can do.
  - Python validates every field against the plan: an activity reference must be one listed, a day must be a trip
    day, an interest one the research uses, a time HH:MM, a percentage or amount a number. Python writes every
    note the traveller sees about the operations (never the LLM), and the same checks as the rules run on the
    result (user_revision.finalize: a place the traveller asked for is never removed).
  - The LLM never states a price, a rate, an opening hour or a travel time: it names what to change. Prices come
    from the research, rates from convert_currency, times from the scheduler.

Any failure (no LLM, an error, an answer that does not validate) raises InterpreterError; the caller then reads
the request with the rules, and says which one read it (Interpretation.source).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .user_revision import (FAMILY_WALK_MINUTES, INTEREST_NAMES, INTEREST_WORDS, Interpretation, PlanState,
                            RevisionOp, _day_label, _label, budget_op, budget_percent_op, currency_code,
                            current_walk_limit, finalize, reduce_walking_op, start_time_ops)

INTERESTS = sorted(set(INTEREST_WORDS.values()))
MAX_DIALOGUE = 8  # turns of the revision chat the LLM sees
MAX_TEXT = 400  # characters kept of a clarification or reason the LLM writes
KINDS = ("set_budget", "change_budget_percent", "cheaper", "free_alternatives", "remove", "replace", "add", "move",
         "start_time", "lighten_day", "walk_limit", "reduce_walking", "use_rides", "prefer", "family_friendly")


class InterpreterError(RuntimeError):
    """The agent gave no usable reading: the rules read the request instead."""


SYSTEM = f"""You are the Revision Agent of Rihla, an AI trip planner. A traveller is looking at their CURRENT
itinerary and asks for a change in their own words. Turn the request into structured edit operations on THIS
itinerary. You never plan a new trip and never state prices, exchange rates, opening hours or travel times:
Python computes those from verified research.

Answer with ONE JSON object and nothing else:
{{"action": "apply" | "clarify" | "unsupported",
  "operations": [ ... ],            // action "apply": one or more operations, in the order to apply them
  "question": "...",                // action "clarify": ONE short question (max 2 sentences)
  "reason": "..."}}                 // action "unsupported": why, and what you CAN do instead

Operations (use only these kinds and fields; "activity" is a reference such as "D2A1" from the plan; days are
1-based day numbers of the trip):
- {{"kind":"set_budget","amount":200,"currency":"EUR"}}      total budget to an amount; currency = ISO code the
   traveller used (omit or the trip currency when they gave none)
- {{"kind":"change_budget_percent","percent":20,"direction":"decrease"|"increase"}}
- {{"kind":"cheaper","activity":"D3A2"}} | {{"kind":"cheaper","category":"restaurant"|"attraction","day":2}}
   replace an expensive planned place with a cheaper researched one (all fields optional)
- {{"kind":"free_alternatives"}}                              paid attractions -> free ones where possible
- {{"kind":"remove","activity":"D2A1"}}
- {{"kind":"replace","activity":"D2A1","interest":"history"}}  interest optional (a different place of that kind)
- {{"kind":"add","interest":"markets","day":3}}               day optional; one operation per interest
- {{"kind":"move","activity":"D1A2","to_day":3}}
- {{"kind":"start_time","time":"10:00","days":[2]}} | {{"kind":"start_time","time":"10:00","all_days":true}}
   when the day starts (also "departure time", "leave the hotel at")
- {{"kind":"lighten_day","day":2}}                            fewer activities that day (less crowded/busy)
- {{"kind":"walk_limit","minutes":15}}                        a stated maximum walk
- {{"kind":"reduce_walking"}}                                 less walking, no number given
- {{"kind":"use_rides"}}                                      taxi/car for long distances
- {{"kind":"prefer","interest":"history","from_interest":"markets"}}  more of one kind instead of another
- {{"kind":"family_friendly"}}                                suitable for families/children (shorter walks,
   lighter days)
Interests: {", ".join(INTERESTS)} ("markets" = shopping, "history" = culture/heritage).

Rules:
1. Change only what was asked. Several requests in one message -> several operations.
2. Resolve "it", "that", "the same", "there" from the recent dialogue and the last changes. If the traveller is
   answering your previous question, combine it with their previous request.
3. Ask ONE clarification question (action "clarify") instead of guessing when the target is ambiguous: several
   activities could match, no day is given where one is needed and "every day" is not meant, or an amount with a
   currency but no clear meaning (e.g. "exchange to 20 euro": ask whether the total budget should be 20 EUR).
   A description that fits two or more listed activities ("the palace" on a day with two palaces) is ambiguous:
   ask which one. Name the candidate activities or days in the question.
   An activity marked asked_for_by_traveller is never removed or replaced (it may be moved): if the request
   would need that, do not pick it silently; ask, or say so with action "unsupported".
4. A budget amount in another currency than the trip's is a set_budget with that currency (Python converts it
   with an official rate). Showing the whole trip in another currency is "unsupported": offer to set the budget
   in that currency instead.
5. A matching activity must exist in the plan; do not reference activities that are not listed. To ADD things
   use "add" with an interest, never a place name you made up.
6. Anything a revision cannot do (booking, flights, hotels, a different destination, new dates) is "unsupported".
"""


def _clean(text: Any, limit: int = 120) -> str:
    return re.sub(r"[\x00-\x1f\x7f<>`]", " ", str(text or ""))[:limit].strip()


def plan_digest(st: PlanState) -> tuple[dict, dict[str, tuple[dt.date, str, str]]]:
    """What the LLM sees of the plan, and the activity references it may answer with."""
    places = {p.place_id: p for p in st.bundle.places}
    required = {i.place_id for i in st.budget_report.selected if i.decision == "required"}
    refs: dict[str, tuple[dt.date, str, str]] = {}
    days = []
    for n, day in enumerate(st.itinerary.days, 1):
        acts = []
        for k, a in enumerate((x for x in day.activities if x.place_id), 1):
            ref = f"D{n}A{k}"
            refs[ref] = (day.date, a.place_id, a.name)
            place = places.get(a.place_id)
            acts.append({"ref": ref, "name": _clean(a.name, 80), "kind": a.kind,
                         "time": f"{a.start_time or '?'}-{a.end_time or '?'}",
                         "cost": "unknown" if a.estimated_cost is None else a.estimated_cost,
                         "tags": [t for t in (place.tags if place else []) if t in INTERESTS],
                         **({"asked_for_by_traveller": True} if a.place_id in required else {})})
        days.append({"day": n, "date": str(day.date), "weekday": f"{day.date:%A}",
                     "starts": st.rules.day_starts.get(str(day.date), st.rules.day_start), "activities": acts})
    planned = {pid for _, pid, _ in refs.values()}
    available: dict[str, int] = {}
    for p in st.bundle.places:
        if p.place_id not in planned:
            for t in set(p.tags) & set(INTERESTS):
                available[t] = available.get(t, 0) + 1
    budget = st.budget_report.budget_total
    digest = {
        "destination": _clean(st.itinerary.destination, 80), "currency": st.budget_report.currency,
        "budget_total": None if budget is None else str(budget),
        "known_costs_total": st.itinerary.estimated_total, "travellers": st.itinerary.travellers,
        "walking_limit_minutes": current_walk_limit(st),
        "planning_day": f"{st.rules.day_start}-{st.rules.day_end}",
        "trip_interests": list(st.bundle.trip.interests),
        "researched_alternatives_by_interest": available,
        "days": days,
        "last_changes": [{"request": _clean(h.get("request"), 200), "changes": [_clean(c, 160) for c in
                                                                              (h.get("changes") or [])[:6]]}
                         for h in st.history[-3:]],
    }
    return digest, refs


def _answer(text: Any) -> dict:
    if isinstance(text, dict):
        return text
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise InterpreterError("the answer is not JSON")
        try:
            value = json.loads(m.group(0))
        except ValueError as exc:
            raise InterpreterError("the answer is not JSON") from exc
    if not isinstance(value, dict):
        raise InterpreterError("the answer is not a JSON object")
    return value


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise InterpreterError(f"{what} is not a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InterpreterError(f"{what} is not a number") from exc
    if number != number.to_integral_value():
        raise InterpreterError(f"{what} is not a whole number")
    return int(number)


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InterpreterError(f"{what} is missing")
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise InterpreterError(f"{what} is not a number") from exc


class _Stop(Exception):
    """A valid reading that ends in a question or a refusal (Python's words)."""

    def __init__(self, interpretation: Interpretation):
        self.interpretation = interpretation


def build_interpretation(answer: dict, st: PlanState, refs: dict[str, tuple[dt.date, str, str]]) -> Interpretation:
    """The agent's answer checked against the plan and turned into RevisionOps (Python's notes)."""
    action = answer.get("action")
    if action == "clarify":
        question = _clean(answer.get("question"), MAX_TEXT)
        if not question:
            raise InterpreterError("a clarification without a question")
        return Interpretation(clarification=question, source="revision_agent")
    if action == "unsupported":
        reason = _clean(answer.get("reason"), MAX_TEXT)
        if not reason:
            raise InterpreterError("unsupported without a reason")
        return Interpretation(infeasible=reason, source="revision_agent")
    if action != "apply":
        raise InterpreterError(f"unknown action {action!r}")
    raw_ops = answer.get("operations")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise InterpreterError("apply without operations")
    dates = [d.date for d in st.itinerary.days]

    def day(value: Any, what: str = "day") -> dt.date:
        n = _int(value, what)
        if not 1 <= n <= len(dates):
            raise _Stop(Interpretation(infeasible=f"The trip has {len(dates)} days, so there is no Day {n}.",
                                       source="revision_agent"))
        return dates[n - 1]

    def activity(value: Any) -> tuple[dt.date, str, str]:
        ref = str(value or "").strip().upper()
        if ref not in refs:
            raise InterpreterError(f"activity {value!r} is not in the plan")
        return refs[ref]

    def interest(value: Any, what: str = "interest") -> str:
        tag = str(value or "").strip().lower()
        tag = {v: k for k, v in INTEREST_NAMES.items()}.get(tag, INTEREST_WORDS.get(tag, tag))
        if tag not in INTERESTS:
            raise InterpreterError(f"{what} {value!r} is not one the research uses")
        return tag

    ops: list[RevisionOp] = []
    try:
        for raw in raw_ops:
            if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
                raise InterpreterError(f"unknown operation {raw!r:.80}")
            kind = raw["kind"]
            if kind == "set_budget":
                code = raw.get("currency")
                cur = currency_code(code) if code else None
                if code and cur is None:
                    raise InterpreterError(f"currency {code!r} is not an ISO code")
                op = budget_op(st, _decimal(raw.get("amount"), "amount"), cur)
            elif kind == "change_budget_percent":
                if raw.get("direction") not in ("decrease", "increase"):
                    raise InterpreterError("a budget percentage without a direction")
                op = budget_percent_op(st, _decimal(raw.get("percent"), "percent"), raw["direction"] == "increase")
            elif kind == "cheaper":
                fields: dict[str, Any] = {}
                note = "the most expensive planned place replaced by a cheaper one"
                if raw.get("activity"):
                    d, pid, name = activity(raw["activity"])
                    fields, note = {"place_id": pid, "date": d}, f"{name} replaced by a cheaper researched place"
                else:
                    cat = raw.get("category")
                    if cat not in (None, "restaurant", "attraction"):
                        raise InterpreterError(f"category {cat!r}")
                    if cat:
                        fields["category"] = cat
                    if raw.get("day") is not None:
                        fields["date"] = day(raw["day"])
                    note = (f"the most expensive planned {cat or 'place'}"
                            + (f" on {_day_label(st, fields['date'])}" if "date" in fields else "")
                            + " replaced by a cheaper one")
                op = RevisionOp(kind="cheaper", note=note, **fields)
            elif kind == "free_alternatives":
                op = RevisionOp(kind="free_alternatives", note="paid attractions replaced by free ones where possible")
            elif kind == "remove":
                d, pid, name = activity(raw.get("activity"))
                op = RevisionOp(kind="remove", place_id=pid, date=d, note=f"{name} removed")
            elif kind == "replace":
                d, pid, name = activity(raw.get("activity"))
                tag = interest(raw["interest"]) if raw.get("interest") else None
                op = RevisionOp(kind="replace", place_id=pid, date=d, tag=tag,
                                note=f"{name} replaced" + (f" with a {_label(tag)} place" if tag else ""))
            elif kind == "add":
                tag = interest(raw.get("interest"))
                d = day(raw["day"]) if raw.get("day") is not None else None
                op = RevisionOp(kind="add", tag=tag, date=d,
                                avoid_dates=[o.date for o in ops if o.kind == "lighten_day"] if d is None else [],
                                note=f"more {_label(tag)}" + (f" on {_day_label(st, d)}" if d else ""))
            elif kind == "move":
                d, pid, name = activity(raw.get("activity"))
                to = day(raw.get("to_day"), "to_day")
                if to == d:
                    raise _Stop(Interpretation(infeasible=f"{name} is already on {_day_label(st, d)}.",
                                               source="revision_agent"))
                op = RevisionOp(kind="move", place_id=pid, date=d, to_date=to, note=f"{name} moved to "
                                                                                    f"{_day_label(st, to)}")
            elif kind == "start_time":
                m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw.get("time") or "").strip())
                if not m:
                    raise InterpreterError("start_time without an HH:MM time")
                targets = dates if raw.get("all_days") is True else [day(n) for n in raw.get("days") or []]
                if not targets:
                    raise _Stop(Interpretation(clarification=(
                        f"Which day should start at {int(m.group(1)):02d}:{m.group(2)}: one day (for example "
                        "\"Day 2\") or every day?"), source="revision_agent"))
                out = start_time_ops(st, list(dict.fromkeys(targets)), int(m.group(1)), int(m.group(2)))
                if isinstance(out, Interpretation):
                    raise _Stop(out.model_copy(update={"source": "revision_agent"}))
                ops += out
                continue
            elif kind == "lighten_day":
                d = day(raw.get("day"))
                op = RevisionOp(kind="lighten_day", date=d, note=f"{_day_label(st, d)} made lighter")
            elif kind == "walk_limit":
                minutes = _int(raw.get("minutes"), "minutes")
                if not 5 <= minutes <= 240:
                    raise _Stop(Interpretation(infeasible=f"A walking limit of {minutes} minutes is not supported: "
                                                          "choose between 5 and 240 minutes.", source="revision_agent"))
                op = RevisionOp(kind="walk_limit", minutes=minutes, note=f"walking limit {minutes} minutes")
            elif kind == "reduce_walking":
                op = reduce_walking_op(st)
            elif kind == "use_rides":
                op = RevisionOp(kind="use_rides", note="taxi or car for long walks")
            elif kind == "prefer":
                to_tag, from_tag = interest(raw.get("interest")), interest(raw.get("from_interest"), "from_interest")
                op = RevisionOp(kind="prefer", tag=to_tag, from_tag=from_tag,
                                note=f"{_label(to_tag)} instead of {_label(from_tag)}")
            else:  # family_friendly
                op = RevisionOp(kind="family_friendly", minutes=FAMILY_WALK_MINUTES, note=(
                    f"family pace: walks of at most {FAMILY_WALK_MINUTES} minutes, and days above the planner's "
                    "daily target made lighter"))
            if isinstance(op, Interpretation):
                raise _Stop(op.model_copy(update={"source": "revision_agent"}))
            ops.append(op)
    except _Stop as stop:
        return stop.interpretation
    return finalize(ops, st, source="revision_agent")


def interpret_with_agent(request: str, st: PlanState, llm, *, dialogue: list[dict] | None = None) -> Interpretation:
    """One LLM call: the request read against this plan. Raises InterpreterError when there is no usable reading."""
    call = getattr(llm, "call", None)
    if not callable(call):
        raise InterpreterError("no LLM")
    digest, refs = plan_digest(st)
    turns = [{"role": t.get("role"), "text": _clean(t.get("text"), 500)} for t in (dialogue or [])[-MAX_DIALOGUE:]
             if t.get("role") in ("traveller", "assistant")]
    user = ("CURRENT ITINERARY:\n" + json.dumps(digest, ensure_ascii=False, default=str)
            + "\n\nRECENT REVISION DIALOGUE (oldest first):\n" + (json.dumps(turns, ensure_ascii=False) if turns
                                                                   else "none")
            + "\n\nTRAVELLER'S REQUEST NOW:\n" + _clean(request, 1000) + "\n\nAnswer with the JSON object only.")
    try:
        text = call([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    except Exception as exc:
        raise InterpreterError(f"LLM call failed ({type(exc).__name__})") from exc
    return build_interpretation(_answer(text), st, refs)
