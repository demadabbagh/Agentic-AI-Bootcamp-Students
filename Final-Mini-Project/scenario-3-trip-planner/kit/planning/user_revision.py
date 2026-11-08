"""A traveller's change request, applied as a targeted revision of the current plan.

    "Reduce my budget by 20%."  ->  interpret()  ->  [RevisionOp(kind="set_budget", amount=560)]
                                ->  apply_revision()  ->  the revised plan, what changed, what stayed

Nothing is re-planned from scratch and nothing is invented:
  - interpret() turns the request into structured operations with Python's rules only (amounts, day numbers,
    places of THIS plan, the traveller's interests). A request it cannot pin down becomes one specific question
    (`clarification`); one that cannot be done, or contradicts the plan (a place the traveller required), becomes
    an explanation (`infeasible`). It never guesses which activity "this" is.
  - apply_revision() edits the Planner's own representation (which selected place goes on which date, in what
    order). Where an edit has several feasible options (which researched alternative replaces a place, which day
    takes a moved place, which expensive place gives way to the new budget), each option is scheduled by Python
    and offered as a DecisionPoint to the existing Revision Agent (planning/revision_agent.py, a CrewAI crew):
    the agent chooses, Python checks the choice is one of its options, and Python's own best option stands if
    the agent gives no valid answer.
  - The set of planned places goes back through the Budget Analyst's own fitting (apply_decision, without
    top-up: nothing is added that was not asked for); the edited days through the Planner's validation and
    scheduler (build_itinerary). Every date no operation touched is copied from the current itinerary
    verbatim: same places, times, costs and notes.
  - Prices, opening hours and routes come from the research and the travel_time lookups only.

The caller then runs the existing verification pipeline (Verifier agent, targeted repair) on the result; nothing
here marks a plan verified.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import Decimal
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .budget_analyst import (_FOLD, BudgetDecision, BudgetReport, PlaceDecision, PlannedItem, apply_decision,
                             money, prepare_candidates)
from .day_balance import Planner
from .itinerary_planner import (DayAssignment, DayChoice, PlannerReport, PlanningContext, UnscheduledChoice,
                                assignment_problems, build_itinerary)
from .models import DayPlan, Itinerary
from .research import ResearchBundle, ResearchedPlace
from .revision import MAX_ALTERNATIVES, Alternative, DecisionPoint
from .schedule import ScheduleRules, TravelLookup, TravelTimes, hours_on, schedule_day
from .setting import setting

OpKind = Literal["set_budget", "cheaper", "free_alternatives", "remove", "replace", "add", "move", "start_time",
                 "lighten_day", "walk_limit", "use_rides", "prefer", "family_friendly",
                 # WHAT IF (app/whatif.py): hypothetical conditions, applied with the same edits as the ones above
                 "closed", "rain_indoor"]

# Words for interests in a request -> the research's interest tags (planning/research.py TripRequest.interests).
INTEREST_WORDS = {
    "history": "history", "historical": "history", "historic": "history", "heritage": "history",
    "culture": "history", "cultural": "history", "museum": "museum", "museums": "museum", "art": "art",
    "arts": "art", "gallery": "art", "galleries": "art", "shopping": "markets", "shop": "markets",
    "shops": "markets", "market": "markets", "markets": "markets", "bazaar": "markets", "nature": "nature",
    "outdoor": "nature", "outdoors": "nature", "park": "nature", "parks": "nature", "garden": "nature",
    "gardens": "nature", "food": "food", "culinary": "food", "cuisine": "food", "relaxation": "relaxation",
    "relaxing": "relaxation", "spa": "relaxation", "adventure": "adventure", "entertainment": "entertainment",
    "nightlife": "entertainment", "show": "entertainment", "shows": "entertainment", "religious": "religious",
    "mosque": "religious", "mosques": "religious", "church": "religious", "churches": "religious",
    "temple": "religious", "temples": "religious", "views": "views", "viewpoint": "views",
}
INTEREST_NAMES = {"markets": "shopping"}
# A word in the request that names a kind of activity, and how a planned activity is recognised as one.
KIND_WORDS = {"restaurant": {"eat", "drink"}, "meal": {"eat", "drink"}, "lunch": {"eat"}, "dinner": {"eat"},
              "cafe": {"drink", "eat"}, "bar": {"drink"}}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
STOP = {"the", "a", "an", "my", "of", "on", "day", "to", "from", "and", "with", "in", "at", "this", "that", "please",
        "activity", "activities", "place", "places", "visit", "stop", "attraction", "attractions", "experience",
        "experiences", "for", "trip", "itinerary"}
MAX_ADDED = 2  # "add more X": at most this many places per request
FAMILY_WALK_MINUTES = 20  # "more suitable for families": the walking limit (only ever lowered, never raised)
# Money words in a request -> ISO codes ("try" and "mad" are English words: TRY and MAD count only next to an
# amount, as a 3-letter code). A budget amount in another currency than the trip's is converted with a
# convert_currency rate only (convert_budget_currency): never taken as if it were in the trip's currency.
CURRENCY_WORDS = {
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP", "gbp": "GBP", "pound": "GBP",
    "pounds": "GBP", "sterling": "GBP", "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "us dollars": "USD", "lira": "TRY", "liras": "TRY", "jod": "JOD", "dinar": "JOD",
    "dinars": "JOD", "jd": "JOD", "aed": "AED", "dirham": "AED", "dirhams": "AED", "sar": "SAR", "riyal": "SAR",
    "riyals": "SAR", "egp": "EGP", "chf": "CHF", "franc": "CHF", "francs": "CHF", "jpy": "JPY", "yen": "JPY",
    "cad": "CAD", "aud": "AUD", "inr": "INR", "rupee": "INR", "rupees": "INR", "qar": "QAR",
    "kwd": "KWD", "bhd": "BHD", "omr": "OMR",
}
ISO_CODES = set(CURRENCY_WORDS.values()) | {"TRY", "MAD", "NZD", "SEK", "NOK", "DKK", "CNY", "SGD", "THB", "MYR"}
_CUR_RE = "|".join(sorted((re.escape(k) for k in CURRENCY_WORDS), key=len, reverse=True))
_AMOUNT = re.compile(r"(?:(" + _CUR_RE + r"|\b[a-z]{3})\s*)?(\d[\d,]*(?:\.\d+)?)(?:\s*(" + _CUR_RE + r"|[a-z]{3})\b)?")


class RevisionOp(BaseModel):
    """One structured change. Only the fields the kind uses are set."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OpKind
    place_id: str | None = None
    date: dt.date | None = None
    to_date: dt.date | None = None
    avoid_dates: list[dt.date] = Field(default_factory=list)  # add: days just made lighter stay lighter
    tag: str | None = None  # an interest tag (add, replace with, prefer)
    from_tag: str | None = None  # prefer A instead of B: B
    amount: Decimal | None = None  # set_budget: the new total
    currency: str | None = None  # set_budget: the currency `amount` was given in, when not the trip's (converted
    # by convert_budget_currency before anything is applied)
    category: Literal["restaurant", "attraction"] | None = None  # cheaper: only this kind of planned place
    minutes: int | None = None  # walk_limit, family_friendly
    time: str | None = None  # start_time: HH:MM
    note: str = ""  # Python's words for what was understood


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[RevisionOp] = Field(default_factory=list)
    clarification: str | None = None  # one specific question: nothing is changed
    infeasible: str | None = None  # why it cannot be done (or conflicts): nothing is changed
    understood: list[str] = Field(default_factory=list)
    # who read the request: Python's rules, or the Revision Agent's interpretation step (planning/
    # revision_interpreter.py), whose operations Python validated against this plan
    source: Literal["rules", "revision_agent"] = "rules"
    agent_error: str | None = None  # why the agent's reading was not used (the rules read it instead)


class PlanState(BaseModel):
    """What the session keeps of a plan so it can be revised without new research or a new plan."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    bundle: ResearchBundle
    budget_report: BudgetReport
    planner_report: PlannerReport
    itinerary: Itinerary
    rules: ScheduleRules
    version: str
    history: list[dict] = Field(default_factory=list)  # one entry per accepted revision: request and changes


class UserRevision(BaseModel):
    """The revised plan (not verified: the caller runs the verification pipeline) and what changed."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    bundle: ResearchBundle
    budget_report: BudgetReport
    planner_report: PlannerReport
    itinerary: Itinerary
    rules: ScheduleRules
    changes: list[str]  # Python's words, from the actual difference between the two itineraries
    unchanged_dates: list[dt.date]
    changed_dates: list[dt.date]
    notes: list[str] = Field(default_factory=list)  # what could not be done, and why
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    decided_by: Literal["python", "agent", "python_fallback"] = "python"
    agent_error: str | None = None


class RevisionInfeasible(ValueError):
    """The request cannot be applied to this plan; the message says why and what would work."""


Decide = Callable[[list[DecisionPoint]], dict[str, str]]


# ------------------------------------------------------------------ reading the request
def _norm(text: str) -> str:
    """Lower case without accents, keeping punctuation such as % and $ (fold() drops it)."""
    plain = unicodedata.normalize("NFKD", str(text).translate(_FOLD)).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().replace("’", "'").split())


def _dates(st: PlanState) -> list[dt.date]:
    return [d.date for d in st.itinerary.days]


def _day_refs(text: str, st: PlanState) -> list[dt.date]:
    """Trip dates the request names: "day 2", "the second day", "the last day", "Monday", "2026-11-03"."""
    dates, out = _dates(st), []
    ordinal = "|".join(list(ORDINALS) + ["last", "final"])
    for m in re.finditer(r"\bday\s*(\d{1,2})\b|\b(" + ordinal + r")\s+day\b|\b(\d{4}-\d{2}-\d{2})\b|\b("
                         + "|".join(WEEKDAYS) + r")\b", text):
        n, word, iso, weekday = m.groups()
        d = None
        if n and 1 <= int(n) <= len(dates):
            d = dates[int(n) - 1]
        elif word in ORDINALS and ORDINALS[word] <= len(dates):
            d = dates[ORDINALS[word] - 1]
        elif word in ("last", "final"):
            d = dates[-1]
        elif iso:
            d = next((x for x in dates if str(x) == iso), None)
        elif weekday:
            matching = [x for x in dates if WEEKDAYS[x.weekday()] == weekday]
            d = matching[0] if len(matching) == 1 else None
        if d is not None and d not in out:
            out.append(d)
    return out


def _bad_day(text: str, st: PlanState) -> str | None:
    m = re.search(r"\bday\s*(\d{1,2})\b", text)
    if m and not 1 <= int(m.group(1)) <= len(_dates(st)):
        return f"The trip has {len(_dates(st))} days, so there is no Day {m.group(1)}."
    return None


def currency_code(word: str | None) -> str | None:
    """An ISO code for a currency word, symbol or code ("euros", "€", "eur" -> "EUR"); None when not one."""
    if not word:
        return None
    w = _norm(word.replace("€", " eur ").replace("£", " gbp ")).strip()
    if w in CURRENCY_WORDS:
        return CURRENCY_WORDS[w]
    return w.upper() if re.fullmatch(r"[a-z]{3}", w) and w.upper() in ISO_CODES else None


def _amount_after(text: str, start: int = 0) -> tuple[Decimal, str | None] | None:
    """The first money amount in `text` from `start` that is not a percentage, with its currency if stated."""
    for m in _AMOUNT.finditer(text, start):
        if re.match(r"\s*%", text[m.end():]) or re.match(r"\s*(?:min|minute|am|pm|a\.m|p\.m|:)", text[m.end():]):
            continue
        if re.search(r"\bday\s*$", text[:m.start(2)]):  # "day 2" is a day, not an amount
            continue
        code = currency_code(m.group(1)) or currency_code(m.group(3))
        return Decimal(m.group(2).replace(",", "")), code
    return None


def money_text(amount: Decimal) -> str:
    shown = f"{amount:f}"
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def budget_op(st: PlanState, amount: Decimal, currency: str | None = None) -> RevisionOp | Interpretation:
    """set_budget to an absolute amount; in another currency than the trip's it is converted later
    (convert_budget_currency), with a convert_currency rate only."""
    trip_cur = st.budget_report.currency
    if amount <= 0:
        return Interpretation(infeasible="A budget must be more than zero.")
    if currency and currency != trip_cur:
        return RevisionOp(kind="set_budget", amount=money(amount), currency=currency,
                          note=f"budget set to {money(amount)} {currency} (converted to {trip_cur}, the trip's "
                               "currency)")
    return RevisionOp(kind="set_budget", amount=money(amount), note=f"budget set to {money(amount)} {trip_cur}")


def budget_percent_op(st: PlanState, pct: Decimal, up: bool) -> RevisionOp | Interpretation:
    budget = st.budget_report.budget_total
    if budget is None:
        return Interpretation(infeasible="This trip has no budget yet, so it cannot be changed by a percentage. "
                                         "Tell me the budget you want, for example \"Set my budget to 600\".")
    if not 0 < pct < 100 and not up:
        return Interpretation(infeasible=f"A {money_text(pct)}% reduction is not possible: give a percentage below 100.")
    if pct <= 0:
        return Interpretation(infeasible="Give a percentage above 0.")
    new = money(budget * (1 + pct / 100) if up else budget * (1 - pct / 100))
    return RevisionOp(kind="set_budget", amount=new,
                      note=f"budget {'raised' if up else 'reduced'} by {money_text(pct)}%: "
                           f"{budget} -> {new} {st.budget_report.currency}")


def start_time_ops(st: PlanState, dates: list[dt.date], hour: int, minute: int) -> list[RevisionOp] | Interpretation:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return Interpretation(infeasible="That is not a valid time.")
    hhmm = f"{hour:02d}:{minute:02d}"
    rules = st.rules
    if not rules.day_start <= hhmm < rules.day_end:
        return Interpretation(infeasible=f"The planning day runs {rules.day_start}-{rules.day_end}: a start at "
                                         f"{hhmm} is outside it.")
    return [RevisionOp(kind="start_time", date=d, time=hhmm, note=f"{_day_label(st, d)} starts at {hhmm}")
            for d in dates]


def current_walk_limit(st: PlanState) -> int:
    from .plan_quality import QualityRules
    return st.rules.max_walk_minutes or st.bundle.trip.max_walk_minutes or QualityRules().long_walk_minutes


def reduce_walking_op(st: PlanState) -> RevisionOp | Interpretation:
    """"Reduce walking time" with no number: the walking limit is lowered (halved from 20 minutes up, else 5
    minutes less), never below 10. Long walks are then reordered, or planned as a ride where a driving time is
    measured."""
    cur = current_walk_limit(st)
    if cur <= 10:
        return Interpretation(infeasible=(
            f"Your walking limit is already {cur} minutes, the lowest I plan for. I can plan taxis for the longer "
            "legs instead: \"Use a taxi for long distances\"."))
    new = max(10, cur // 2 if cur >= 20 else cur - 5)
    return RevisionOp(kind="walk_limit", minutes=new, note=f"walking limit lowered from {cur} to {new} minutes")


def _tags_of(phrase: str) -> list[str]:
    out: list[str] = []
    for word in re.findall(r"[a-z]+", phrase):
        tag = INTEREST_WORDS.get(word)
        if tag and tag not in out:
            out.append(tag)
    return out


def _tag_of(phrase: str) -> str | None:
    for word in re.findall(r"[a-z]+", phrase):
        if word in INTEREST_WORDS:
            return INTEREST_WORDS[word]
    return None


def _label(tag: str | None) -> str:
    return INTEREST_NAMES.get(tag, tag or "")


def _day_label(st: PlanState, d: dt.date) -> str:
    return f"Day {_dates(st).index(d) + 1} ({d:%a %d %b})"


def _places(st: PlanState) -> dict[str, ResearchedPlace]:
    return {p.place_id: p for p in st.bundle.places}


def _planned(st: PlanState, dates: list[dt.date] | None = None) -> list[tuple[dt.date, str, str, str | None]]:
    """(date, place id, name, kind) of every planned activity, optionally only on `dates`."""
    out = []
    for day in st.itinerary.days:
        if dates and day.date not in dates:
            continue
        out += [(day.date, a.place_id, a.name, a.kind) for a in day.activities if a.place_id]
    return out


def _matches(st: PlanState, phrase: str, item: tuple) -> bool:
    """A planned activity matches a phrase by its own name (a distinctive word), its kind (restaurant, meal...)
    or its research tags (museum, shopping, park...)."""
    _, pid, name, kind = item
    words = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in STOP and len(w) > 2]
    name_words = set(re.findall(r"[a-z0-9]+", _norm(name)))
    if words and all(w in name_words or w.rstrip("s") in name_words for w in words):
        return True
    place = _places(st).get(pid)
    for w in words:
        if w in KIND_WORDS and kind in KIND_WORDS[w]:
            return True
        tag = INTEREST_WORDS.get(w)
        if tag and place is not None and tag in place.tags and (kind not in ("eat", "drink") or tag == "food"):
            return True
        if tag == "markets" and kind == "buy":
            return True
    return False


def _resolve(st: PlanState, phrase: str, dates: list[dt.date], verb: str) -> tuple[tuple | None, str | None]:
    """The one planned activity a phrase means, or a question / an explanation."""
    phrase = phrase.strip()
    if re.fullmatch(r"(it|this|that|this one|that one|this activity|that activity|the activity)", phrase):
        return None, (f"Which activity should I {verb}? Name it as it appears in your itinerary, for example "
                      f"\"{verb.capitalize()} {_planned(st)[0][2]}\".")
    pool = _planned(st, dates or None)
    words = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in STOP and len(w) > 2]
    if not words and pool:  # "an activity", "something": name it rather than guess
        where = f" on {_day_label(st, dates[0])}" if dates else ""
        listed = "; ".join(i[2] for i in pool[:8])
        return None, f"Which activity{where} should I {verb}? Planned{where}: {listed}."
    found = [i for i in pool if _matches(st, phrase, i)]
    named = [i for i in found if any(w in set(re.findall(r"[a-z0-9]+", _norm(i[2]))) for w in words)]
    if len(found) > 1 and len(named) == 1:  # "the museum" on a day with Pera Museum: the one its name says
        found = named
    where = f" on {_day_label(st, dates[0])}" if dates else ""
    if not found:
        listed = ", ".join(i[2] for i in pool) or "nothing"
        return None, f"I could not find \"{phrase}\"{where} in your itinerary (planned{where}: {listed})."
    if len(found) > 1:
        names = "; ".join(f"{i[2]} ({_day_label(st, i[0])})" for i in found[:6])
        return None, f"More than one activity matches \"{phrase}\"{where}: {names}. Which one should I {verb}?"
    return found[0], None


def interpret(request: str, st: PlanState) -> Interpretation:
    """Structured operations for a request, a clarification question, or why it cannot be done."""
    text = _norm(request.replace("€", " eur ").replace("£", " gbp "))
    if (why := _bad_day(text, st)) is not None:
        return Interpretation(infeasible=why)
    ops: list[RevisionOp] = []
    days = _day_refs(text, st)
    every_day = bool(re.search(r"\b(?:every|each|all(?: the)?)\s+(?:day|days|morning|mornings)\b|\bdaily\b"
                               r"|\b(?:whole|entire) trip\b", text))
    trip_cur = st.budget_report.currency

    # budget
    m = re.search(r"\b(reduce|cut|lower|decrease|drop|shrink|increase|raise|up)\b[^%]*?\bbudget\b[^%]*?(\d+(?:\.\d+)?)\s*%"
                  r"|\bbudget\b[^%]*?\b(reduce|cut|lower|decrease|increase|raise)\w*\b[^%]*?(\d+(?:\.\d+)?)\s*%"
                  r"|(\d+(?:\.\d+)?)\s*%\s*(less|lower|cheaper|more)\b", text)
    budget_at = re.search(r"\bbudget\b", text)
    absolute = _amount_after(text, budget_at.end()) if budget_at else None
    cheaper = re.search(r"\b(cheaper|less expensive|save money|lower (the )?cost|spend less|too expensive|"
                        r"more affordable|budget[- ]friendly)\b", text)
    if m:
        verb = (m.group(1) or m.group(3) or m.group(6) or "").lower()
        op = budget_percent_op(st, Decimal(m.group(2) or m.group(4) or m.group(5)),
                               verb in ("increase", "raise", "up", "more"))
        if isinstance(op, Interpretation):
            return op
        ops.append(op)
    elif absolute and re.search(r"\b(set|change|make|update|adjust|switch|reduce|lower|cut|raise|increase|want|keep|"
                                r"exchange)\b|\bbudget\s+(?:to|of|is|=|at)\b|\bbudget\b.*\bto\b", text):
        op = budget_op(st, *absolute)
        if isinstance(op, Interpretation):
            return op
        ops.append(op)
    elif re.search(r"\bfree\b.*\b(alternative|option|attraction|place|instead)s?\b|\breplace\b.*\bexpensive\b.*\bfree\b",
                   text):
        ops.append(RevisionOp(kind="free_alternatives", note="paid attractions replaced by free ones where possible"))
    elif cheaper:
        category = "restaurant" if re.search(r"\b(restaurants?|meals?|lunch|dinner|breakfast|cafes?|food|eat\w*)\b",
                                             text) else None
        named = re.search(r"\b(?:replace|swap|change)\s+(.+?)\s+(?:with|for|by)\b", text)
        item = None
        if named and not re.search(r"\b(expensive|pricey|costly)\b", named.group(1)):
            item, _ = _resolve(st, re.sub(r"\b(on|from)\s+day\s*\d+\b", "", named.group(1)), days, "replace")
        if item is not None:
            ops.append(RevisionOp(kind="cheaper", place_id=item[1], date=item[0],
                                  note=f"{item[2]} replaced by a cheaper researched place"))
        else:
            what = "restaurant" if category else "place"
            where = f" on {_day_label(st, days[0])}" if days else ""
            ops.append(RevisionOp(kind="cheaper", category=category, date=days[0] if days else None,
                                  note=f"the most expensive planned {what}{where} replaced by a cheaper one"))

    # walking and transport
    w = (re.search(r"\bwalk\w*\b.*?\b(?:more than|over|longer than|max(?:imum)?|at most|under|less than|beyond|up to)"
                   r"\s*(\d{1,3})\s*(?:min|minute)", text)
         or re.search(r"\b(\d{1,3})\s*(?:min|minute)s?\b.*\bwalk", text))
    family = re.search(r"\b(famil(?:y|ies)|kids?|children|child|toddlers?|stroller|pram|baby)\b", text)
    if w:
        minutes = int(w.group(1))
        if not 5 <= minutes <= 240:
            return Interpretation(infeasible=f"A walking limit of {minutes} minutes is not supported: choose between "
                                             "5 and 240 minutes.")
        ops.append(RevisionOp(kind="walk_limit", minutes=minutes, note=f"walking limit {minutes} minutes"))
    elif re.search(r"\b(taxi|cab|car|uber|ride|drive)\b", text) and re.search(r"\b(long|far|distance|instead of walk)", text):
        ops.append(RevisionOp(kind="use_rides", note="taxi or car for long walks"))
    elif family:
        ops.append(RevisionOp(kind="family_friendly", minutes=FAMILY_WALK_MINUTES, note=(
            f"family pace: walks of at most {FAMILY_WALK_MINUTES} minutes, and days above the planner's daily "
            "target made lighter")))
    elif re.search(r"\b(less|reduce|minimi[sz]e|cut|shorter|fewer|lower|decrease)\b[^.]*\bwalk|\bwalk\w*\s+(?:less|time"
                   r"\s+(?:down|shorter))\b|\btoo much walking\b", text):
        op = reduce_walking_op(st)
        if isinstance(op, Interpretation):
            return op
        ops.append(op)

    # schedule: "Start Day 2 at 10 AM", "Change the departure time to 10 AM every day"
    s = re.search(r"\b(?:start\w*|begin\w*|depart\w*|head(?:ing)? out)\b.*?\b(?:at|to|from|by|around)\s*(\d{1,2})"
                  r"(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?", text)
    if s:
        hour, minute = int(s.group(1)), int(s.group(2) or 0)
        half = (s.group(3) or "").replace(".", "")
        if half == "pm" and hour < 12:
            hour += 12
        if half == "am" and hour == 12:
            hour = 0
        dates = _dates(st) if every_day else days
        if not dates:
            return Interpretation(clarification=(
                f"Which day should start at {hour:02d}:{minute:02d}: one day (for example \"Day 2\") or every day?"))
        out = start_time_ops(st, dates, hour, minute)
        if isinstance(out, Interpretation):
            return out
        ops += out
    if re.search(r"\b(less crowded|less busy|less packed|less hectic|less rushed|lighter|more relaxed|quieter|"
                 r"fewer (activities|places|stops|things)|too (busy|full|much|packed))\b", text) and not family:
        if not days:
            return Interpretation(clarification="Which day should be lighter? For example \"Make Day 4 less crowded\".")
        ops += [RevisionOp(kind="lighten_day", date=d, note=f"{_day_label(st, d)} made lighter") for d in days]

    # activities
    prefer = re.search(r"\b(?:prefer|rather have|want more)\s+(.+?)\s+(?:instead of|rather than|over)\s+(.+)", text)
    replace = re.search(r"\b(?:replace|swap|substitute|change)\s+(.+?)\s+(?:with|for|by|into)\s+(?:an?\s+|some\s+)?(.+)",
                        text)
    remove = re.search(r"\b(?:remove|delete|drop|skip|cancel|take out|get rid of|don'?t want(?: to visit)?)\s+(.+)", text)
    move = re.search(r"\bmove\s+(.+?)\s+to\s+(.+)", text)
    add = re.search(r"\b(?:add|include|more|extra)\b\s*(.*)", text)
    # "Make Day 3 less crowded and add more historical attractions": two requests in one. Only an explicit verb counts
    # here ("more relaxed" is not a request to add). The second half must not be dropped without a word.
    lighter = [o.date for o in ops if o.kind == "lighten_day"]
    add_too = re.search(r"\b(?:add|include)\b\s*(.*)", text) if lighter else None
    if prefer:
        to_tag, from_tag = _tag_of(prefer.group(1)), _tag_of(prefer.group(2))
        if not to_tag or not from_tag:
            return Interpretation(clarification="Which kinds of activities do you mean? For example \"I prefer "
                                                "cultural activities instead of shopping\".")
        ops.append(RevisionOp(kind="prefer", tag=to_tag, from_tag=from_tag,
                              note=f"{_label(to_tag)} instead of {_label(from_tag)}"))
    elif replace and not ops:
        target = re.sub(r"\b(on|from)\s+day\s*\d+\b", "", replace.group(1)).strip()
        if _tag_of(target) == "food" and "expensive" in target:  # "replace expensive ..." handled above
            pass
        item, problem = _resolve(st, target, days, "replace")
        if problem:
            return Interpretation(clarification=problem) if "Which" in problem else Interpretation(infeasible=problem)
        tag = _tag_of(replace.group(2))
        if tag is None and not re.search(r"\b(something else|another|alternative|other)\b", replace.group(2)):
            return Interpretation(clarification=f"What should replace {item[2]}? For example \"Replace {item[2]} with "
                                                "a historical attraction\".")
        ops.append(RevisionOp(kind="replace", place_id=item[1], date=item[0], tag=tag,
                              note=f"{item[2]} replaced" + (f" with a {_label(tag)} place" if tag else "")))
    elif move:
        target_days = _day_refs(move.group(2), st)
        if not target_days:
            return Interpretation(clarification="To which day? For example \"Move Topkapi Palace to Day 3\".")
        item, problem = _resolve(st, re.sub(r"\b(on|from)\s+day\s*\d+\b", "", move.group(1)),
                                 [d for d in days if d != target_days[-1]], "move")
        if problem:
            return Interpretation(clarification=problem) if "Which" in problem else Interpretation(infeasible=problem)
        if item[0] == target_days[-1]:
            return Interpretation(infeasible=f"{item[2]} is already on {_day_label(st, item[0])}.")
        ops.append(RevisionOp(kind="move", place_id=item[1], date=item[0], to_date=target_days[-1],
                              note=f"{item[2]} moved to {_day_label(st, target_days[-1])}"))
    elif remove and not re.search(r"\bwalk", text):
        target = re.sub(r"\b(on|from)\s+(day\s*\d+|the \w+ day|" + "|".join(WEEKDAYS) + r")\b", "", remove.group(1))
        item, problem = _resolve(st, target, days, "remove")
        if problem:
            return Interpretation(clarification=problem) if "Which" in problem else Interpretation(infeasible=problem)
        ops.append(RevisionOp(kind="remove", place_id=item[1], date=item[0], note=f"{item[2]} removed"))
    elif add and not ops:
        tags = _tags_of(add.group(1))
        if not tags:
            return Interpretation(clarification="What would you like more of? For example \"Add more nature "
                                                "activities\" or \"Add more food experiences\".")
        ops += [RevisionOp(kind="add", tag=tag, date=days[0] if days else None,
                           note=f"more {_label(tag)}" + (f" on {_day_label(st, days[0])}" if days else ""))
                for tag in tags]
    elif add_too:
        tags = _tags_of(add_too.group(1))
        if not tags:
            return Interpretation(clarification="What would you like more of? For example \"Add more nature "
                                                "activities\" or \"Add more food experiences\".")
        ops += [RevisionOp(kind="add", tag=tag, avoid_dates=lighter, note=f"more {_label(tag)} on the other days")
                for tag in tags]

    if not ops and (why := _currency_only(text, st)) is not None:
        return why

    if not ops:
        n_days = len(_dates(st))
        first = next((name for _, _, name, _ in _planned(st)), None)
        last = f"Day {n_days}"
        return Interpretation(clarification=(
            "I'm not sure what to change. You can ask, for example: \"Reduce my budget by 20%\", "
            + (f"\"Remove {first}\", \"Move {first} to {last}\", " if first else "")
            + f"\"Start Day 1 at 10 AM\", \"Make {last} less crowded\", \"I don't want to walk more than 15 "
            "minutes\" or \"Add more food experiences\"."))
    return finalize(ops, st)


def finalize(ops: list[RevisionOp], st: PlanState, source: Literal["rules", "revision_agent"] = "rules"
             ) -> Interpretation:
    """The checks every reading of a request goes through (the rules' and the Revision Agent's alike): a place
    the traveller asked to visit is never removed or replaced."""
    required = {i.place_id for i in st.budget_report.selected if i.decision == "required"}
    for op in ops:
        if op.kind in ("remove", "replace") and op.place_id in required:
            name = next(n for _, p, n, _ in _planned(st) if p == op.place_id)
            return Interpretation(infeasible=(
                f"{name} is a place you asked to visit, so I will not {op.kind} it. If you no longer want it, plan "
                f"the trip again without it; or I can move it to another day (\"Move {name} to Day 2\")."))
    return Interpretation(ops=ops, understood=[o.note for o in ops], source=source)


RateFor = Callable[[str, str], "ExchangeRate | None"]


def convert_budget_currency(interpretation: Interpretation, st: PlanState, rate_for: RateFor | None = None
                            ) -> Interpretation:
    """A budget given in another currency than the trip's, converted into the trip's currency.

    The rate is the research's own convert_currency result for that exact direction (bundle.fx_results), else one
    `rate_for(from, to)` fetches (the convert_currency tool). Without a rate nothing is changed and the traveller is
    told so: an amount is never taken as if it were in the trip's currency, and no rate is ever assumed."""
    from .budget import BudgetError, RateTable
    if not any(op.kind == "set_budget" and op.currency for op in interpretation.ops):
        return interpretation
    trip_cur = st.budget_report.currency
    try:
        table = RateTable.from_tool_results(list(st.bundle.fx_results or []))
    except BudgetError:
        table = RateTable()
    ops, understood = [], []
    for op in interpretation.ops:
        if op.kind != "set_budget" or not op.currency or op.currency == trip_cur:
            ops.append(op)
            understood.append(op.note)
            continue
        rate = table.get(op.currency, trip_cur)
        if rate is None and rate_for is not None:
            try:
                rate = rate_for(op.currency, trip_cur)
            except Exception:  # an unreachable rate service is the same as no rate
                rate = None
        if rate is None:
            return Interpretation(infeasible=(
                f"I could not get a verified exchange rate from {op.currency} to {trip_cur} right now, so I did not "
                f"change your budget. Your trip's prices are in {trip_cur}: tell me the budget in {trip_cur}, or try "
                "again in a moment."), source=interpretation.source)
        converted = money(op.amount * rate.rate)
        note = (f"budget set to {money(op.amount)} {op.currency} = {converted} {trip_cur} "
                f"(rate {rate.describe()})")
        ops.append(op.model_copy(update={"amount": converted, "currency": None, "note": note}))
        understood.append(note)
    return interpretation.model_copy(update={"ops": ops, "understood": understood})


def _currency_only(text: str, st: PlanState) -> Interpretation | None:
    """A request that names a currency but no budget: "exchange to 20 euro", "show prices in euros"."""
    trip_cur = st.budget_report.currency
    words = [CURRENCY_WORDS.get(w) for w in re.findall(r"[a-z]+|\$", text)]
    named = next((c for c in words if c), None)
    if named is None:
        return None
    amount = _amount_after(text)
    if amount is not None:
        value, cur = amount
        cur = cur or named
        return Interpretation(clarification=(
            f"Do you want your total trip budget to be {money_text(value)} {cur}? Your trip's prices are in "
            f"{trip_cur}, so I would convert it with the official exchange rate. Reply \"Set my budget to "
            f"{money_text(value)} {cur}\" to confirm."))
    if named != trip_cur and re.search(r"\b(currency|exchange|convert\w*|switch|change|show|display|prices?|in)\b", text):
        return Interpretation(infeasible=(
            f"The prices in this plan come from the research in {trip_cur}, the trip's currency, and I cannot show "
            f"the whole itinerary in {named} without changing what was verified. I can set your budget in {named} "
            f"instead (converted with the official rate), for example \"Set my budget to 500 {named}\"."))
    return None


# ------------------------------------------------------------------ applying it
class _Edit:
    """The plan being edited: places per date, the decisions behind the budget, the touched dates."""

    def __init__(self, st: PlanState, travel_lookup: TravelLookup | None, chooser: dict[str, str] | None = None):
        self.st, self.bundle, self.rules = st, st.bundle, st.rules
        self.chooser = chooser or {}  # decision signature -> the option key the Revision Agent chose
        self.days: dict[dt.date, list[str]] = {d.date: list(d.place_ids) for d in st.planner_report.assignment.days}
        self.unscheduled: dict[str, str] = {u.place_id: u.reason for u in st.planner_report.assignment.unscheduled}
        r = st.budget_report
        self.decision: dict[str, tuple[str, str]] = {}  # place id -> (include | optional | exclude | required, why)
        for items, kind in ((r.selected, None), (r.optional, "optional"), (r.dropped_for_budget, "optional"),
                            (r.excluded, "exclude")):
            for i in items:
                self.decision.setdefault(i.place_id, (kind or i.decision, i.reason))
        self.serves = {i.place_id: list(i.serves) for i in r.selected + r.optional + r.excluded + r.dropped_for_budget}
        self.rank = {i.place_id: n for n, i in enumerate(r.selected + r.optional)}
        self.affected: set[dt.date] = set()
        self.notes: list[str] = []
        self.points: list[DecisionPoint] = []
        self.chosen: dict[str, str] = {}  # signature -> the key applied
        self.cands = prepare_candidates(self.bundle)
        self.travel = TravelTimes(travel_lookup, max_lookups=40)
        # the current plan's measured legs are known already: never looked up again
        for day in st.planner_report.schedule.days:
            for s in day.scheduled:
                if s.travel is not None and s.travel.status == "measured":
                    self.travel._cache[(s.travel.from_place_id, s.place_id)] = s.travel

    # facts ------------------------------------------------------------------
    def planned(self) -> set[str]:
        return {i for ids in self.days.values() for i in ids}

    def report(self) -> BudgetReport:
        """The Budget Analyst's fitting of exactly the planned places (no top-up: nothing added unasked)."""
        planned = self.planned() | set(self.unscheduled)
        decisions = []
        for pid in self.cands.open_ids:
            kind, reason = self.decision.get(pid, ("exclude", "Not part of this plan"))
            if pid in planned:
                kind = "include"
            elif kind in ("include", "required"):
                kind = "optional"
            decisions.append(PlaceDecision(place_id=pid, decision=kind, reason=(reason or "")[:200],
                                           rank=None if kind == "exclude" else self.rank.get(pid, len(self.rank)) + 1,
                                           serves=self.serves.get(pid)))
        return apply_decision(self.cands, BudgetDecision(decisions=decisions), fill=False)

    def place(self, pid: str) -> ResearchedPlace | None:
        return next((p for p in self.bundle.places if p.place_id == pid), None)

    def name(self, pid: str) -> str:
        p = self.place(pid)
        return p.name if p else pid

    def cost(self, pid: str) -> Decimal | None:
        c = self.cands.get(pid)
        return None if c is None or c.cost.status == "unknown" else c.cost.total_max

    def required(self, pid: str) -> bool:
        return self.decision.get(pid, ("",))[0] == "required"

    def withdrawn(self, pid: str) -> bool:
        """The traveller took this place out (removed or replaced it in an earlier change): never offered again."""
        kind, why = self.decision.get(pid, (None, ""))
        return kind == "exclude" and "at your request" in (why or "")

    def alternatives_pool(self, tag: str | None, *, cheaper_than: Decimal | None = None,
                          free_only: bool = False, indoor_only: bool = False) -> list[str]:
        """Researched places not in the plan that a revision may add: never withheld, never closed on every trip
        date, never an event outside the trip; with `tag`, only places the research tags for that interest (a
        restaurant only for food). An unknown price is never "cheaper": it may not be. `indoor_only`: only places
        whose own listing shows they are indoors (planning/setting.py)."""
        out = []
        for c in self.cands.candidates:
            if c.place_id in self.planned() or c.closed_all_trip or c.not_during_trip or self.withdrawn(c.place_id):
                continue
            if c.category == "restaurant" and tag != "food":
                continue
            if tag and tag not in c.tags:
                continue
            if free_only and c.cost.status != "free":
                continue
            if indoor_only and (self.place(c.place_id) is None or setting(self.place(c.place_id)) != "indoor"):
                continue
            cost = None if c.cost.status == "unknown" else c.cost.total_max
            if cheaper_than is not None and (cost is None or cost >= cheaper_than):
                continue
            out.append(c.place_id)
        return out

    def ctx(self, extra: list[str] = ()) -> PlanningContext:
        """A Planner context whose stops are the plan's places plus `extra` candidates, on the edit's travel cache."""
        base = self.report()
        wanted = set(extra) | self.planned()  # every planned place, even one the budget fitting would drop
        items = [PlannedItem(**dict(c), decision="include", rank=99, serves=self.serves.get(c.place_id, []))
                 for c in self.cands.candidates if c.place_id in wanted and c.place_id not in
                 {i.place_id for i in base.selected}]
        ctx = PlanningContext(base.model_copy(update={"selected": list(base.selected) + items}), self.bundle, None,
                              rules=self.rules)
        ctx.travel = self.travel
        return ctx

    def planner(self, ctx: PlanningContext) -> Planner:
        p = Planner(ctx)
        p.view = self.travel.cached_view()  # options are ranked on known legs only: no lookup, nothing guessed
        if self.rules.max_walk_minutes:
            p.limit = self.rules.max_walk_minutes
        return p

    def option(self, key: str, summary: str, day: dt.date, order: list[str], ctx: PlanningContext,
               placed: str | None, gives_way: str | None = None) -> Alternative | None:
        """One feasible option, scheduled by Python (an unknown leg gets the planning buffer, never a guess)."""
        s = schedule_day(day, [ctx.stops[i] for i in order], self.rules, self.travel.cached_view())
        if s.unscheduled:
            return None
        legs = [x for x in s.scheduled if x.travel is not None]
        limit = self.rules.max_walk_minutes
        listed = hours_on(ctx.stops[placed].hours, day) if placed else None
        before = self.days.get(day, [])
        return Alternative(
            id="D0-A0", key=key, summary=summary, date=day, order=list(order), gives_way=gives_way,
            hours=listed.status if listed else None, hours_windows=(listed.windows_text or None) if listed else None,
            measured_travel_minutes=sum(x.travel.minutes for x in legs if x.travel.status == "measured"),
            unmeasured_legs=sum(x.buffer_used for x in legs),
            walks_over_limit=sum(bool(limit) and x.travel.status == "measured" and x.travel.mode == "foot"
                                 and x.travel.minutes > limit for x in legs),
            places_on_date=len(order),
            reordered_places=sum(a != b for a, b in zip([i for i in before if i in order],
                                                        [i for i in order if i in before])),
            schedule=[{"place_id": x.place_id, "start": x.start, "end": x.end} for x in s.scheduled])

    def rank_key(self, ctx: PlanningContext, alt: Alternative) -> tuple:
        return (alt.walks_over_limit, alt.hours not in (None, "open"), self.planner(ctx).route(alt.order),
                alt.places_on_date)

    def set_day(self, day: dt.date, ids: list[str]) -> None:
        if self.days.get(day) != ids:
            self.days[day] = list(ids)
            self.affected.add(day)

    def include(self, ids: list[str]) -> None:
        for pid in ids:
            if self.decision.get(pid, ("",))[0] not in ("required", "include"):
                self.decision[pid] = ("include", "Added in a revision you asked for")

    def decide(self, kind: str, subject: str, place_id: str | None, date: dt.date | None,
               options: list[Alternative], apply: Callable[[Alternative], None]) -> None:
        """Apply one option: the Revision Agent's choice when this run has one for this decision, else Python's
        best. With two or more options the decision point is recorded for the agent."""
        options = options[:MAX_ALTERNATIVES]
        if not options:
            return
        signature = f"{kind}:{subject}"
        if len(options) > 1:
            pid = f"D{len(self.points) + 1}"
            options = [o.model_copy(update={"id": f"{pid}-A{n}"}) for n, o in enumerate(options, 1)]
            place = self.place(place_id) if place_id else None
            hours = hours_on(place.hours, date).status if place is not None and date is not None else None
            self.points.append(DecisionPoint(id=pid, signature=signature, kind=kind, place_id=place_id, date=date,
                                             hours_on_date=hours, alternatives=options, default=options[0].id))
        chosen = next((o for o in options if o.key == self.chooser.get(signature)), options[0])
        self.chosen[signature] = chosen.key
        apply(chosen)


def _swap(e: _Edit, out_pid: str, day: dt.date, pool: list[str], reason: str, *, allow_remove: bool,
          kind: str = "placement", need: Decimal | None = None) -> bool:
    """Replace `out_pid` on `day` with the best of `pool` (the agent may choose another), or remove it. False
    when no option fits."""
    ctx = e.ctx(pool)
    p = e.planner(ctx)
    rest = [i for i in e.days[day] if i != out_pid]
    options = []
    for cand in pool:
        order = p.insert(day, rest, cand)
        if order is None:
            continue
        c = e.cands.get(cand)
        price = ("free" if c.cost.status == "free" else f"{c.cost.total_max} {e.st.budget_report.currency}"
                 if c.cost.status != "unknown" else "price unknown")
        alt = e.option(f"{day}|{cand}", f"{e.name(cand)} instead of {e.name(out_pid)} on {day:%a %d %b} ({price})",
                       day, order, ctx, cand, gives_way=out_pid)
        if alt is not None:
            options.append(alt)
    options.sort(key=lambda a: e.rank_key(ctx, a))
    if allow_remove:
        order = p.best_order(day, rest) if rest else []
        alt = e.option(f"{day}|-", f"remove {e.name(out_pid)} from {day:%a %d %b}, no replacement", day,
                       order if order is not None else rest, ctx, None, gives_way=out_pid)
        if alt is not None:
            options.append(alt)
    if not options:
        return False
    if need is not None and need > 0:  # a budget cut: first the options that close the gap (a replacement before
        # a plain removal), then the biggest saving; walking decides among equals (the order above)
        out_cost = e.cost(out_pid) or Decimal(0)

        def saving(alt: Alternative) -> Decimal:
            added = alt.key.split("|")[1]
            return out_cost - (e.cost(added) or Decimal(0) if added != "-" else Decimal(0))
        options.sort(key=lambda a: (saving(a) < need, a.key.endswith("|-") if saving(a) >= need else False,
                                    -saving(a) if saving(a) < need else Decimal(0)))

    def apply(alt: Alternative) -> None:
        e.set_day(day, list(alt.order))
        e.decision[out_pid] = ("exclude", reason)
        e.include(list(alt.order))
    e.decide(kind, out_pid, out_pid, day, options, apply)
    return True


def _op_remove(e: _Edit, op: RevisionOp) -> None:
    rest = [i for i in e.days.get(op.date, []) if i != op.place_id]
    order = e.planner(e.ctx()).best_order(op.date, rest) if rest else []
    e.set_day(op.date, order if order is not None else rest)
    e.decision[op.place_id] = ("exclude", "Removed at your request")


def _op_replace(e: _Edit, op: RevisionOp, *, reason: str = "Replaced at your request", indoor_only: bool = False) -> None:
    what = f"{_label(op.tag)} " if op.tag else "indoor " if indoor_only else ""
    pool = e.alternatives_pool(op.tag, indoor_only=indoor_only)
    if not pool:
        raise RevisionInfeasible(f"The research has no other {what}place that could replace {e.name(op.place_id)}.")
    if not _swap(e, op.place_id, op.date, pool, reason, allow_remove=False):
        raise RevisionInfeasible(f"No researched {what}place fits {_day_label(e.st, op.date)} in place of "
                                 f"{e.name(op.place_id)} (opening hours, the planning day).")


def _op_move(e: _Edit, op: RevisionOp) -> None:
    ctx = e.ctx()
    if hours_on(ctx.stops[op.place_id].hours, op.to_date).status == "closed":
        raise RevisionInfeasible(f"{e.name(op.place_id)} is closed on {_day_label(e.st, op.to_date)} (from its "
                                 "listed hours).")
    p = e.planner(ctx)
    order = p.insert(op.to_date, e.days.get(op.to_date, []), op.place_id)
    if order is None:
        raise RevisionInfeasible(f"{e.name(op.place_id)} does not fit on {_day_label(e.st, op.to_date)} with the "
                                 "places already planned there (opening hours, the planning day).")
    rest = [i for i in e.days[op.date] if i != op.place_id]
    e.set_day(op.date, p.best_order(op.date, rest) or rest)
    e.set_day(op.to_date, order)


def _op_add(e: _Edit, op: RevisionOp) -> None:
    pool = e.alternatives_pool(op.tag)
    if not pool:
        raise RevisionInfeasible(f"The research found no other {_label(op.tag)} place for this trip, so I cannot add "
                                 "more without inventing one.")
    added = 0
    for n in range(1 if op.date else MAX_ADDED):
        pool = [c for c in pool if c not in e.planned()]
        ctx = e.ctx(pool)
        p = e.planner(ctx)
        options = []
        for day in [op.date] if op.date else [d for d in e.days if d not in op.avoid_dates]:
            meal = any(ctx.stops[c].category == "restaurant" for c in pool)
            if not meal and len(p.sights(e.days[day])) >= p.hard_max(day):
                continue
            for cand in pool:
                order = p.insert(day, e.days[day], cand)
                alt = e.option(f"{day}|{cand}", f"add {e.name(cand)} on {day:%a %d %b}", day, order, ctx, cand) \
                    if order is not None else None
                if alt is not None:
                    options.append(alt)
        if not options:
            break
        options.sort(key=lambda a: (a.places_on_date, e.rank_key(ctx, a)))

        def apply(alt: Alternative) -> None:
            e.set_day(alt.date, list(alt.order))
            e.include(list(alt.order))
        e.decide("placement", f"add-{op.tag}-{n}", None, op.date, options, apply)
        added += 1
    if not added:
        where = _day_label(e.st, op.date) if op.date else "any day"
        raise RevisionInfeasible(f"No researched {_label(op.tag)} place fits {where} (the days are full, or the "
                                 "places are closed then).")


def _op_lighten(e: _Edit, op: RevisionOp) -> None:
    ctx = e.ctx()
    p = e.planner(ctx)
    day = op.date
    movable = [i for i in p.sights(e.days[day]) if not e.required(i)]
    if len(p.sights(e.days[day])) <= 1 or not movable:
        raise RevisionInfeasible(f"{_day_label(e.st, day)} has {len(p.sights(e.days[day]))} sightseeing place(s) "
                                 "that could go: it is already as light as it can be.")
    pid = max(movable, key=lambda i: e.rank.get(i, 0))  # the least important to the plan
    rest = [i for i in e.days[day] if i != pid]
    options = []
    for other in e.days:
        if other == day or len(p.sights(e.days[other])) >= p.cap(other):
            continue
        order = p.insert(other, e.days[other], pid)
        alt = e.option(f"{other}|{pid}", f"move {e.name(pid)} to {other:%a %d %b}", other, order, ctx, pid) \
            if order is not None else None
        if alt is not None:
            options.append(alt)
    options.sort(key=lambda a: e.rank_key(ctx, a))
    e.set_day(day, p.best_order(day, rest) or rest)
    if options:
        e.decide("placement", f"lighten-{pid}", pid, day, options, lambda alt: e.set_day(alt.date, list(alt.order)))
    else:
        e.decision[pid] = ("optional", "Left out to make a day lighter, at your request")
        e.notes.append(f"No other day had room for {e.name(pid)}, so it was left out (kept as an alternative).")


def _op_budget(e: _Edit, *, amount: Decimal | None = None, cheaper_once: bool = False, free_only: bool = False,
               only: RevisionOp | None = None) -> None:
    """Fit the known costs to the budget by replacing (or removing) the most expensive non-required paid place,
    one at a time and only while the total is over: everything else stays. "cheaper" does one such step; "free
    alternatives" swaps each paid attraction for a free one where one fits."""
    if amount is not None:
        e.bundle = e.bundle.model_copy(update={"trip": e.bundle.trip.model_copy(update={"budget_total": amount})})
        e.cands = prepare_candidates(e.bundle)
    budget = e.bundle.trip.budget_total
    required_known = sum((e.cost(i) or Decimal(0)) for i, (k, _) in e.decision.items() if k == "required")
    if budget is not None and required_known > budget:
        raise RevisionInfeasible(
            f"The places you asked to visit already cost {money(required_known)} {e.st.budget_report.currency} "
            f"(known prices), more than {money(budget)}. Keep a budget of at least {money(required_known)}, or "
            "tell me which of those places to drop.")

    def known() -> Decimal:
        return sum((e.cost(i) or Decimal(0)) for i in e.planned())
    tried: set[str] = set()
    while True:
        if not free_only and not cheaper_once and (budget is None or known() <= budget):
            break
        paid = [(d, i) for d, ids in e.days.items() for i in ids if not e.required(i) and i not in tried
                and (e.cost(i) or 0) > 0 and (not free_only or e.cands.get(i).category == "attraction")
                and _in_scope(e, only, d, i)]
        if not paid and only is not None and not tried:
            raise RevisionInfeasible(_no_paid_in_scope(e, only))
        if not paid or (cheaper_once and tried):
            break
        need = (known() - budget) if budget is not None and not cheaper_once and not free_only else Decimal(0)
        ctx_now = e.ctx()
        sights = e.planner(ctx_now).sights

        def key(x):
            d, i = x
            empties = len(sights(e.days[d])) <= 1 and ctx_now.stops[i].category == "attraction"
            cost = e.cost(i)
            covers = cost >= need if need > 0 else True
            # never empty a date if avoidable; then one place that covers the gap, the cheapest such; else the
            # most expensive (it covers most of it)
            return (empties, not covers, cost if covers and need > 0 else -cost)
        day, pid = min(paid, key=key)
        tried.add(pid)
        tags = [t for t in (e.serves.get(pid) or getattr(e.place(pid), "tags", [])) if t in e.bundle.trip.interests]
        if e.cands.get(pid).category == "restaurant":
            tags = ["food"]  # a restaurant gives way only to another researched restaurant
        pool: list[str] = []
        for tag in tags or [None]:
            pool += [c for c in e.alternatives_pool(tag, cheaper_than=e.cost(pid), free_only=free_only)
                     if c not in pool and (e.cands.get(c).category == e.cands.get(pid).category)]
        reason = ("Replaced by a free alternative, at your request" if free_only else
                  "Replaced to make the trip cheaper, at your request" if cheaper_once else
                  "Left out to fit your new budget")
        swapped = _swap(e, pid, day, pool, reason, allow_remove=not free_only and only is None, kind="make_room",
                        need=need if need > 0 else None)
        if not swapped and only is not None:
            what = "restaurant" if e.cands.get(pid).category == "restaurant" else "place"
            raise RevisionInfeasible(f"The research has no cheaper {what} with a known price that fits "
                                     f"{_day_label(e.st, day)} in place of {e.name(pid)} ({e.cost(pid)} "
                                     f"{e.st.budget_report.currency}), so I left it as planned.")
        if not swapped and free_only:
            e.notes.append(f"No free researched place fits in place of {e.name(pid)} on {_day_label(e.st, day)}.")
    if amount is not None:
        before = {d.date: list(d.place_ids) for d in e.st.planner_report.assignment.days}
        ctx_all = e.ctx(extra=[i for ids in before.values() for i in ids])
        sights = e.planner(ctx_all).sights
        emptied = [d for d, ids in before.items() if sights(ids) and not sights(e.days.get(d, []))]
        if emptied:
            restore = sum(min((e.cost(i) or Decimal(0)) for i in sights(before[d])) for d in emptied)
            days = ", ".join(_day_label(e.st, d) for d in emptied)
            raise RevisionInfeasible(
                f"A budget of {money(budget)} {e.st.budget_report.currency} would leave {days} with no sightseeing: "
                f"the plan's known costs are {money(sum((e.cost(i) or Decimal(0)) for ids in before.values() for i in ids))} "
                f"{e.st.budget_report.currency}. You could keep a budget of at least {money(known() + restore)} "
                f"{e.st.budget_report.currency} (each day keeps its cheapest sight), ask me to \"replace expensive "
                "attractions with free alternatives\", or tell me which places to remove. Your itinerary has not "
                "been changed.")
    if budget is not None and not cheaper_once and not free_only and known() > budget:
        e.notes.append(f"The known costs ({money(known())}) are still over the budget ({money(budget)}): the "
                       "remaining paid places were asked for, or no cheaper researched place fits.")


def _in_scope(e: _Edit, only: RevisionOp | None, day: dt.date, pid: str) -> bool:
    """"Make it cheaper" narrowed by the request: one named place, a kind of place, one day."""
    if only is None:
        return True
    if only.place_id and pid != only.place_id:
        return False
    if only.date and day != only.date:
        return False
    return not only.category or e.cands.get(pid).category == only.category


def _no_paid_in_scope(e: _Edit, only: RevisionOp) -> str:
    if only.place_id:
        name = e.name(only.place_id)
        if e.required(only.place_id):
            return f"{name} is a place you asked to visit, so I will not replace it."
        return (f"{name} has no known price (or is free), so I cannot tell that another place would be cheaper "
                "without inventing a price.")
    what = "restaurants" if only.category == "restaurant" else "places"
    where = f" on {_day_label(e.st, only.date)}" if only.date else ""
    return (f"None of the planned {what}{where} has a known price above zero, so I cannot tell which one is "
            "expensive or find a verified cheaper one.")


def _op_family(e: _Edit, op: RevisionOp) -> list[dt.date]:
    """A family pace: the walking limit lowered to op.minutes (never raised), then every day above the Planner's
    own daily target made lighter, its least important sight moved to a day with room (or kept as an
    alternative)."""
    walk_days: list[dt.date] = []
    if e.rules.max_walk_minutes is None or e.rules.max_walk_minutes > op.minutes:
        walk_days = _op_walk(e, op.minutes)
    p = e.planner(e.ctx())
    busy = sorted((d for d in e.days if len(p.sights(e.days[d])) > p.targets[d]),
                  key=lambda d: -len(p.sights(e.days[d])))
    for day in busy:
        try:
            _op_lighten(e, RevisionOp(kind="lighten_day", date=day))
        except RevisionInfeasible as exc:
            e.notes.append(str(exc))
    if not walk_days and not busy and not e.affected:
        e.notes.append(f"The plan already keeps walks to {e.rules.max_walk_minutes} minutes or less and no day is "
                       "above the planner's daily target, so it already has a family pace.")
    return walk_days


def _op_walk(e: _Edit, minutes: int | None) -> list[dt.date]:
    """A walking limit: the trip's and the scheduler's. Dates with a planned walk over it are rescheduled: a better
    order where one exists, and every walk still over it is measured again (by car, as a ride, when the travel
    lookup offers one). Other dates stay as they are."""
    if minutes is not None:
        e.bundle = e.bundle.model_copy(update={"trip": e.bundle.trip.model_copy(update={"max_walk_minutes": minutes})})
        e.rules = e.rules.model_copy(update={"max_walk_minutes": minutes})
    limit = e.rules.max_walk_minutes or 30
    for key, leg in list(e.travel._cache.items()):  # walks over the new limit are looked up again (rides)
        if leg.status == "measured" and leg.mode == "foot" and leg.minutes > limit:
            del e.travel._cache[key]
    days = [d.date for d in e.st.itinerary.days
            if any(a.travel_mode_from_previous != "driving" and (a.travel_minutes_from_previous or 0) > limit
                   for a in d.activities)]
    p = e.planner(e.ctx())
    for day in days:
        e.days[day] = p.best_order(day, e.days[day]) or e.days[day]
        e.affected.add(day)
    return days


def _already_done(st: PlanState, interpretation: Interpretation) -> str:
    """Why nothing changed, when the plan already does what was asked."""
    kinds = {op.kind for op in interpretation.ops}
    if kinds & {"walk_limit", "use_rides", "family_friendly"}:
        limit = next((op.minutes for op in interpretation.ops if op.minutes), None) or current_walk_limit(st)
        walks = [a.travel_minutes_from_previous or 0 for d in st.itinerary.days for a in d.activities
                 if a.travel_mode_from_previous != "driving"]
        longest = max(walks, default=0)
        return (f"Your planned walks are already short: the longest is {longest} minutes, within {limit} minutes, so "
                "nothing needed to change. Your itinerary is as it was.")
    return "Nothing in the itinerary needed to change for this request."  # app/whatif.py reads this wording


def _diff(st: PlanState, new: Itinerary, budget_note: str | None) -> tuple[list[str], list[dt.date], list[dt.date]]:
    """What actually changed, from the two itineraries themselves."""
    old_days = {d.date: d for d in st.itinerary.days}
    changes = [budget_note] if budget_note else []
    changed, unchanged = [], []
    placed_old = {a.place_id: d.date for d in st.itinerary.days for a in d.activities}
    placed_new = {a.place_id: d.date for d in new.days for a in d.activities}
    names = {a.place_id: a.name for d in st.itinerary.days + new.days for a in d.activities}
    for day in new.days:
        old = old_days.get(day.date)
        sig = lambda d: [(a.place_id, a.start_time, a.end_time, a.estimated_cost) for a in (d.activities if d else [])]
        if sig(old) == sig(day):
            unchanged.append(day.date)
            continue
        changed.append(day.date)
        label = _day_label(st, day.date)
        gone = [p for p, d in placed_old.items() if d == day.date and p not in placed_new]
        came = [p for p, d in placed_new.items() if d == day.date and p not in placed_old]
        moved_in = [p for p, d in placed_new.items() if d == day.date and p in placed_old and placed_old[p] != d]
        if gone and came and len(gone) == len(came):
            changes += [f"{label}: replaced {names[g]} with {names[c]}." for g, c in zip(gone, came)]
        else:
            changes += [f"{label}: removed {names[g]}." for g in gone]
            changes += [f"{label}: added {names[c]}." for c in came]
        changes += [f"Moved {names[m]} from {_day_label(st, placed_old[m])} to {label}." for m in moved_in]
        if not gone and not came and not moved_in:
            changes.append(f"{label}: times or order adjusted.")
    if unchanged:
        changes.append("Unchanged: " + ", ".join(f"Day {_dates(st).index(d) + 1}" for d in unchanged) + ".")
    return changes, changed, unchanged


def _run(st: PlanState, interpretation: Interpretation, travel_lookup: TravelLookup | None,
         chooser: dict[str, str] | None = None) -> tuple[_Edit, str | None, list[dt.date]]:
    e = _Edit(st, travel_lookup, chooser)
    budget_note, walk_days = None, []
    for op in interpretation.ops:
        if op.kind == "set_budget":
            old = st.budget_report.budget_total
            budget_note = (f"Budget changed from {money(old)} to {money(op.amount)} {st.budget_report.currency}."
                           if old is not None else f"Budget set to {money(op.amount)} {st.budget_report.currency}.")
            _op_budget(e, amount=op.amount)
        elif op.kind == "cheaper":
            scoped = op if (op.place_id or op.category or op.date) else None
            _op_budget(e, cheaper_once=True, only=scoped)
        elif op.kind == "family_friendly":
            walk_days = _op_family(e, op)
        elif op.kind == "free_alternatives":
            _op_budget(e, free_only=True)
        elif op.kind == "remove":
            _op_remove(e, op)
        elif op.kind == "replace":
            _op_replace(e, op)
        elif op.kind == "move":
            _op_move(e, op)
        elif op.kind == "add":
            try:
                _op_add(e, op)
            except RevisionInfeasible as exc:  # "more shopping and culture": one impossible half does not block
                if sum(o.kind == "add" for o in interpretation.ops) < 2:  # the other; it is said in the notes
                    raise
                e.notes.append(str(exc))
        elif op.kind == "start_time":
            e.rules = e.rules.model_copy(update={"day_starts": {**e.rules.day_starts, str(op.date): op.time}})
            e.affected.add(op.date)
        elif op.kind == "lighten_day":
            _op_lighten(e, op)
        elif op.kind == "walk_limit":
            walk_days = _op_walk(e, op.minutes)
        elif op.kind == "use_rides":
            walk_days = _op_walk(e, None)
        elif op.kind == "prefer":
            planned = [(d, i) for d, ids in e.days.items() for i in ids
                       if e.place(i) and op.from_tag in e.place(i).tags and not e.required(i)]
            if not planned:
                raise RevisionInfeasible(f"No planned activity is a {_label(op.from_tag)} place, so there is nothing "
                                         "to swap.")
            interests = [t for t in e.bundle.trip.interests if t != op.from_tag]
            interests += [op.tag] if op.tag not in interests else []
            e.bundle = e.bundle.model_copy(update={"trip": e.bundle.trip.model_copy(update={"interests": interests})})
            for day, pid in planned:
                try:
                    _op_replace(e, RevisionOp(kind="replace", place_id=pid, date=day, tag=op.tag),
                                reason=f"Replaced: you prefer {_label(op.tag)} to {_label(op.from_tag)}")
                except RevisionInfeasible as exc:
                    e.notes.append(str(exc))
        elif op.kind == "closed":  # WHAT IF: this attraction is unavailable that day (hypothetical, said so)
            try:
                _op_replace(e, op, reason="Unavailable in this what-if (replaced at your request)")
            except RevisionInfeasible as exc:
                rest = [i for i in e.days.get(op.date, []) if i != op.place_id]
                order = e.planner(e.ctx()).best_order(op.date, rest) if rest else []
                e.set_day(op.date, order if order is not None else rest)
                e.decision[op.place_id] = ("exclude", "Unavailable in this what-if (removed at your request)")
                e.notes.append(f"{exc} It is removed from {_day_label(st, op.date)} instead.")
        elif op.kind == "rain_indoor":  # WHAT IF: rain on op.date (None: every date); outdoor places go indoors
            for day in [op.date] if op.date else _dates(st):
                for pid in list(e.days.get(day, [])):
                    place = e.place(pid)
                    where = setting(place) if place else "unclear"
                    if where == "indoor":
                        continue
                    if where == "unclear":
                        e.notes.append(f"{e.name(pid)} ({_day_label(st, day)}): its listing does not say whether it "
                                       "is indoors, so it is kept as planned.")
                    elif e.required(pid):
                        e.notes.append(f"{e.name(pid)} ({_day_label(st, day)}) is outdoors but you asked for it, so "
                                       "it is kept: check the weather before you go.")
                    else:
                        try:
                            _op_replace(e, RevisionOp(kind="replace", place_id=pid, date=day),
                                        reason="Outdoors on a rainy day in this what-if (replaced at your request)",
                                        indoor_only=True)
                        except RevisionInfeasible as exc:
                            e.notes.append(f"{exc} {e.name(pid)} is outdoors and stays in the plan.")
    return e, budget_note, walk_days


def apply_revision(request: str, st: PlanState, interpretation: Interpretation, *,
                   travel_lookup: TravelLookup | None = None, decide: Decide | None = None) -> UserRevision:
    """Apply interpreted operations to the plan (see the module docstring). Raises RevisionInfeasible.

    Python runs the operations with its own best options first. When that run met decision points, the Revision
    Agent (`decide`, one crew run) chooses among their options and the operations run again with its picks: each
    later choice is then made on top of the earlier ones. If the agent fails, or its run cannot be applied,
    Python's own run stands (decided_by says so)."""
    e, budget_note, walk_days = _run(st, interpretation, travel_lookup)
    decided_by, agent_error, points = "python", None, list(e.points)
    if points and decide is not None:
        try:
            picks = decide(points)
            keys = {p.signature: p.by_id(picks[p.id]).key for p in points
                    if p.id in picks and p.by_id(picks[p.id]) is not None}
            e, budget_note, walk_days = _run(st, interpretation, travel_lookup, chooser=keys)
            decided_by = "agent"
        except Exception as exc:  # no valid answer: Python's own best options stand, said out loud
            decided_by, agent_error = "python_fallback", f"{type(exc).__name__}: {str(exc)[:200]}"
            e, budget_note, walk_days = _run(st, interpretation, travel_lookup)
    if not e.affected and not budget_note and not walk_days:
        raise RevisionInfeasible(" ".join(e.notes) or _already_done(st, interpretation))

    report = e.report()
    ctx = PlanningContext(report, e.bundle, travel_lookup, rules=e.rules)
    ctx.travel._cache.update(e.travel._cache)  # legs already measured are not looked up again
    selected = set(ctx.stops)
    days = [DayChoice(date=d, place_ids=[i for i in e.days.get(d, []) if i in selected]) for d in _dates(st)]
    placed = {i for d in days for i in d.place_ids}
    unscheduled = [UnscheduledChoice(place_id=i, reason=(e.unscheduled.get(i) or "Not placed in this revision")[:300])
                   for i in selected if i not in placed]
    assignment = DayAssignment(days=days, unscheduled=unscheduled)
    if problems := assignment_problems(assignment, ctx, check_schedule=False):
        raise RevisionInfeasible("The change would break the plan's rules: " + "; ".join(p.text for p in problems[:3]))
    built = build_itinerary(ctx, assignment)
    new_days = {d.date: d for d in (built.itinerary.days if built.itinerary else [])}
    keep = {d.date: d for d in st.itinerary.days if d.date not in e.affected}
    final_days = [keep.get(d) or new_days.get(d) or DayPlan(date=d) for d in _dates(st)]
    if not any(d.activities for d in final_days):
        raise RevisionInfeasible("That change would leave nothing planned.")
    itinerary = Itinerary(destination=st.itinerary.destination, currency=st.itinerary.currency,
                          travellers=st.itinerary.travellers,
                          budget_total=None if e.bundle.trip.budget_total is None else float(e.bundle.trip.budget_total),
                          days=final_days)
    built = built.model_copy(update={"itinerary": itinerary})
    changes, changed, unchanged = _diff(st, itinerary, budget_note)
    first = 1 if budget_note else 0
    if budget_note and not changed:
        unknown = sum(a.estimated_cost is None for d in itinerary.days for a in d.activities)
        changes.insert(1, f"The plan's known costs ({money(itinerary.estimated_total)} {itinerary.currency}) already "
                          "fit the new budget, so no activity had to change." + (
                              f" {unknown} price(s) are unknown and not counted." if unknown else ""))
        first = 2
    if any(o.kind in ("walk_limit", "use_rides") for o in interpretation.ops) or (
            any(o.kind == "family_friendly" for o in interpretation.ops)
            and st.rules.max_walk_minutes != e.rules.max_walk_minutes):
        limit = e.rules.max_walk_minutes or 30
        acts = [a for d in itinerary.days if d.date in walk_days for a in d.activities]
        rides = sum(a.travel_mode_from_previous == "driving" for a in acts)
        still = sum(a.travel_mode_from_previous != "driving" and (a.travel_minutes_from_previous or 0) > limit
                    for a in acts)
        if not walk_days:
            line = "no planned walk was longer, so nothing needed to change."
        else:
            line = f"{len(walk_days)} day(s) had a longer walk." + (
                f" {rides} leg(s) are now planned by taxi or car (real driving times, fares not included)."
                if rides else "") + (
                f" {still} walk(s) are still longer: no shorter order and no measured car time was available, so "
                "they are flagged for you." if still else "")
        changes.insert(first, f"Walking limit {limit} minutes: {line}")
    starts = [op for op in interpretation.ops if op.kind == "start_time"]
    for op in reversed(starts):  # inserted at one position: reversed, so they read Day 1, Day 2...
        was = st.rules.day_starts.get(str(op.date), st.rules.day_start)
        first_act = next((a.start_time for d in st.itinerary.days if d.date == op.date for a in d.activities), None)
        already = was == op.time or (op.date in unchanged and first_act == op.time)
        changes.insert(first, f"{_day_label(st, op.date)} " + (f"already started at {op.time}." if already
                                                              else f"now starts at {op.time}."))
    return UserRevision(bundle=e.bundle, budget_report=report, planner_report=built, itinerary=itinerary,
                        rules=e.rules, changes=changes, unchanged_dates=unchanged, changed_dates=changed,
                        notes=e.notes, decision_points=points, decided_by=decided_by, agent_error=agent_error)
