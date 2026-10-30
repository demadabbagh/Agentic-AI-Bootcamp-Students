"""The Verifier's deterministic checks: an independent look at the final itinerary before anything is saved.

The Planner's status, warnings and evidence are claims to check, not facts to reuse. Each check re-derives its
answer from the itinerary itself, the research (recorded tool results), Python's own calculators (summarize(),
prepare_candidates(), hours_on()) and, when the Coordinator injects them, fresh lookups:

  hours_lookup(place_name)    -> a get_opening_hours result (city bound)
  travel_lookup(from, to)     -> a travel_time result (city and mode bound): the Planner's TravelLookup

Three kinds of finding, never mixed up:
  error    a confirmed failure: the evidence shows the plan is wrong (closed that day, an overlap, over budget)
  unknown  missing evidence: the plan may be right but cannot be confirmed (no hours, no route, no price)
  warning  worth knowing, not a failure (a price range, a changed listing, a lookup that fell back to recorded data)

Per check: FAIL (any error), UNKNOWN (no error, an unknown), PASS. Overall: the worst check.
Freshness: a lookup result is "fresh" only when it says mode "live" and is not stale. Replay-cache results, the
research bundle and the Planner's own legs are "recorded". verified is True only when every check passes AND every
opening-hours and travel fact behind them is fresh: a plan checked against recorded data is PASS at most.

Every finding has a stable code (CODES) and a recommended_action, the revision target for Milestone 6.
No LLM, no network except through the injected lookups, nothing saved.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable, Iterable, Literal, NamedTuple, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .budget import BudgetSummary, money, summarize
from .budget_analyst import BudgetReport, fold, match_required, prepare_candidates
from .models import Activity, Itinerary
from .research import ResearchBundle, ResearchedPlace
from .schedule import DayHours, ScheduleRules, TravelLookup, hours_on, to_minutes, trip_dates

if TYPE_CHECKING:  # itinerary_planner imports CrewAI; the checks themselves do not need it
    from .itinerary_planner import PlannerReport

VERIFICATION_SCHEMA = "verification-1"
DEFAULT_MAX_LOOKUPS = 60  # fresh lookups per verification, hours and travel together (cached repeats are free)

CheckName = Literal["itinerary", "places", "required_places", "schedule", "opening_hours", "travel", "walking",
                    "budget", "costs", "evidence", "planner_report"]
CHECK_ORDER: list[str] = list(get_args(CheckName))
CheckStatus = Literal["PASS", "FAIL", "UNKNOWN"]
Severity = Literal["error", "unknown", "warning"]
Freshness = Literal["fresh", "recorded", "computed", "claimed"]  # computed: Python's calculators; claimed: a report
EvidenceBasis = Literal["fresh", "recorded", "mixed", "none"]
RevisionAction = Literal[
    "remove_activity",          # take the place out of the plan
    "move_activity",            # put it on another trip date
    "reschedule_activity",      # new times on the same day
    "reorder_day",              # change the order of the day's places
    "add_required_place",       # schedule a place the traveller asked for
    "restore_selected_place",   # schedule a selected place, or record why it is left out
    "reprice_activity",         # take the cost from Python's pricing of the listing
    "recalculate_budget",       # the totals must come from summarize()
    "reduce_cost",              # over budget: drop or swap a paid place
    "align_with_request",       # currency, travellers or budget differ from the traveller's request
    "lookup_opening_hours", "lookup_travel_time", "lookup_price", "add_source",
    "ask_traveller",            # no plan change can fix it: the traveller decides
    "rebuild_itinerary",        # nothing usable to revise
    "none"]
EVIDENCE_ACTIONS = {"lookup_opening_hours", "lookup_travel_time", "lookup_price", "add_source"}

# code -> (check, severity, default recommended_action). Stable: Milestone 6 and the evals key on these strings.
IssueCode = Literal[
    "invalid_itinerary", "date_outside_trip", "trip_date_missing", "activity_untimed", "invalid_activity_time",
    "duplicate_place", "synthetic_place", "withheld_place", "excluded_place", "not_selected_place",
    "place_not_in_research", "place_unidentified", "selected_place_unaccounted", "selected_place_unscheduled",
    "exclusion_lists_unavailable",
    "required_place_missing", "required_place_unresolved",
    "activity_overlap", "outside_planning_day", "activity_order_mismatch",
    "closed_on_date", "outside_opening_hours", "hours_unknown", "hours_unconfirmed", "hours_lookup_failed",
    "hours_changed", "hours_source_mismatch",
    "insufficient_travel_gap", "route_unknown", "route_lookup_failed", "travel_minutes_mismatch",
    "walk_over_limit", "walk_unverified",
    "budget_total_mismatch", "day_total_mismatch", "budget_verdict_mismatch", "over_budget", "budget_undetermined",
    "currency_mismatch", "travellers_mismatch", "budget_limit_mismatch",
    "cost_unknown", "activity_cost_mismatch", "cost_range", "price_changed",
    "source_missing", "source_mismatch", "citation_missing", "route_citation_missing", "place_name_mismatch",
    "research_tool_failure",
    "planner_no_itinerary", "planner_itinerary_differs", "planner_reported_partial", "planner_reported_infeasible",
    "planner_status_inconsistent"]

CODES: dict[str, tuple[str, str, str]] = {
    "invalid_itinerary": ("itinerary", "error", "rebuild_itinerary"),
    "date_outside_trip": ("itinerary", "error", "move_activity"),
    "trip_date_missing": ("itinerary", "warning", "none"),
    "activity_untimed": ("itinerary", "unknown", "reschedule_activity"),
    "invalid_activity_time": ("itinerary", "error", "reschedule_activity"),
    "duplicate_place": ("places", "error", "remove_activity"),
    "synthetic_place": ("places", "error", "remove_activity"),
    "withheld_place": ("places", "error", "remove_activity"),
    "excluded_place": ("places", "error", "remove_activity"),
    "not_selected_place": ("places", "error", "remove_activity"),
    "place_not_in_research": ("places", "error", "remove_activity"),
    "place_unidentified": ("places", "unknown", "add_source"),
    "selected_place_unaccounted": ("places", "error", "restore_selected_place"),
    "selected_place_unscheduled": ("places", "warning", "none"),
    "exclusion_lists_unavailable": ("places", "unknown", "none"),
    "required_place_missing": ("required_places", "error", "add_required_place"),
    "required_place_unresolved": ("required_places", "unknown", "ask_traveller"),
    "activity_overlap": ("schedule", "error", "reschedule_activity"),
    "outside_planning_day": ("schedule", "error", "reschedule_activity"),
    "activity_order_mismatch": ("schedule", "warning", "reorder_day"),
    "closed_on_date": ("opening_hours", "error", "move_activity"),
    "outside_opening_hours": ("opening_hours", "error", "reschedule_activity"),
    "hours_unknown": ("opening_hours", "unknown", "lookup_opening_hours"),
    "hours_unconfirmed": ("opening_hours", "unknown", "lookup_opening_hours"),
    "hours_lookup_failed": ("opening_hours", "warning", "lookup_opening_hours"),
    "hours_changed": ("opening_hours", "warning", "none"),
    "hours_source_mismatch": ("opening_hours", "unknown", "lookup_opening_hours"),
    "insufficient_travel_gap": ("travel", "error", "reschedule_activity"),
    "route_unknown": ("travel", "unknown", "lookup_travel_time"),
    "route_lookup_failed": ("travel", "warning", "lookup_travel_time"),
    "travel_minutes_mismatch": ("travel", "warning", "reschedule_activity"),
    "walk_over_limit": ("walking", "error", "reorder_day"),
    "walk_unverified": ("walking", "unknown", "lookup_travel_time"),
    "budget_total_mismatch": ("budget", "error", "recalculate_budget"),
    "day_total_mismatch": ("budget", "error", "recalculate_budget"),
    "budget_verdict_mismatch": ("budget", "error", "recalculate_budget"),
    "over_budget": ("budget", "error", "reduce_cost"),
    "budget_undetermined": ("budget", "unknown", "lookup_price"),
    "currency_mismatch": ("budget", "error", "align_with_request"),
    "travellers_mismatch": ("budget", "error", "align_with_request"),
    "budget_limit_mismatch": ("budget", "error", "align_with_request"),
    "cost_unknown": ("costs", "unknown", "lookup_price"),
    "activity_cost_mismatch": ("costs", "error", "reprice_activity"),
    "cost_range": ("costs", "warning", "none"),
    "price_changed": ("costs", "warning", "lookup_price"),
    "source_missing": ("evidence", "unknown", "add_source"),
    "source_mismatch": ("evidence", "warning", "add_source"),
    "citation_missing": ("evidence", "unknown", "add_source"),
    "route_citation_missing": ("evidence", "warning", "add_source"),
    "place_name_mismatch": ("evidence", "warning", "none"),
    "research_tool_failure": ("evidence", "warning", "none"),
    "planner_no_itinerary": ("planner_report", "error", "rebuild_itinerary"),
    "planner_itinerary_differs": ("planner_report", "warning", "none"),
    "planner_reported_partial": ("planner_report", "warning", "none"),
    "planner_reported_infeasible": ("planner_report", "warning", "ask_traveller"),
    "planner_status_inconsistent": ("planner_report", "error", "rebuild_itinerary"),
}

HoursLookup = Callable[[str], dict]  # place_name -> a get_opening_hours result, city bound


def freshness_of(result: dict | None) -> Freshness:
    """'fresh' only for a result that says mode 'live' and is not a stale fallback; anything else is 'recorded'
    (the replay cache, a stale copy, or a result that does not say where it came from)."""
    if isinstance(result, dict) and result.get("mode") == "live" and not result.get("stale"):
        return "fresh"
    return "recorded"


def _ok(result) -> bool:
    return isinstance(result, dict) and result.get("ok") is not False and not isinstance(result.get("error"), dict)


def _error_code(result) -> str:
    err = (result.get("error") or {}) if isinstance(result, dict) else {}
    return str(err.get("code") or "bad_result")


# ------------------------------------------------------------------ the report
class Evidence(BaseModel):
    """What a finding rests on, and how fresh it is."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # "listing hours", "get_opening_hours", "travel_time", "summarize()", "planner report", ...
    freshness: Freshness
    detail: str
    citation_id: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None


class VerificationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: IssueCode
    check: CheckName
    severity: Severity
    date: dt.date | None = None
    place_id: str | None = None
    related_place_id: str | None = None  # the other place: the previous stop for overlaps and travel
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_action: RevisionAction

    @model_validator(mode="after")
    def _catalogue(self) -> VerificationIssue:
        check, severity, _ = CODES[self.code]
        if (self.check, self.severity) != (check, severity):
            raise ValueError(f"{self.code} is a {severity} of the {check} check")
        return self

    @classmethod
    def of(cls, code: str, explanation: str, *, date: dt.date | None = None, place_id: str | None = None,
           related_place_id: str | None = None, evidence: Iterable[Evidence] = (),
           action: str | None = None) -> VerificationIssue:
        check, severity, default = CODES[code]
        return cls(code=code, check=check, severity=severity, date=date, place_id=place_id,
                   related_place_id=related_place_id, explanation=explanation, evidence=list(evidence),
                   recommended_action=action or default)


class RevisionTarget(BaseModel):
    """One thing Milestone 6 can act on: the action, where, and the findings behind it."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: RevisionAction
    kind: Literal["plan_change", "evidence_lookup", "traveller_decision"]
    date: dt.date | None = None
    place_id: str | None = None
    related_place_id: str | None = None
    codes: list[IssueCode]


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check: CheckName
    status: CheckStatus
    summary: str
    codes: list[IssueCode] = Field(default_factory=list)  # every finding of this check, warnings included


class VerificationReport(BaseModel):
    """What the Coordinator (and the Phase B Verifier agent) receive. Every number and status is Python's."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = VERIFICATION_SCHEMA
    status: CheckStatus
    verified: bool = False  # PASS on fresh evidence only: see the module docstring
    evidence_basis: EvidenceBasis
    checks: list[CheckResult]
    issues: list[VerificationIssue] = Field(default_factory=list)  # confirmed failures (severity error)
    unknowns: list[VerificationIssue] = Field(default_factory=list)  # missing evidence
    warnings: list[VerificationIssue] = Field(default_factory=list)
    verified_checks: list[CheckName] = Field(default_factory=list)  # the checks that passed
    revision_targets: list[RevisionTarget] = Field(default_factory=list)
    budget: BudgetSummary | None = None  # summarize() of the verified itinerary
    tool_evidence: list[dict] = Field(default_factory=list)  # the fresh lookups made: tool, ok, ms, freshness
    citations: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # Python's wording, safe to show

    @model_validator(mode="after")
    def _consistent(self) -> VerificationReport:
        for name, items, severity in (("issues", self.issues, "error"), ("unknowns", self.unknowns, "unknown"),
                                      ("warnings", self.warnings, "warning")):
            if any(i.severity != severity for i in items):
                raise ValueError(f"{name} holds only findings of severity {severity}")
        statuses = {c.status for c in self.checks}
        worst = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
        if self.status != worst:
            raise ValueError(f"the overall status must be the worst check status ({worst})")
        if self.verified_checks != [c.check for c in self.checks if c.status == "PASS"]:
            raise ValueError("verified_checks must list exactly the checks that passed")
        if self.verified and (self.status != "PASS" or self.evidence_basis != "fresh"):
            raise ValueError("verified=True needs every check to pass on fresh evidence")
        return self

    @property
    def findings(self) -> list[VerificationIssue]:
        return self.issues + self.unknowns + self.warnings

    @property
    def codes(self) -> set[str]:
        return {i.code for i in self.findings}

    def check(self, name: str) -> CheckResult:
        return next(c for c in self.checks if c.check == name)

    def by_code(self, code: str) -> list[VerificationIssue]:
        return [i for i in self.findings if i.code == code]


def revision_targets(findings: Iterable[VerificationIssue]) -> list[RevisionTarget]:
    """Errors and unknowns grouped by (action, date, place): warnings are advice and set no target."""
    groups: dict[tuple, list[str]] = {}
    for i in findings:
        if i.severity == "warning" or i.recommended_action == "none":
            continue
        key = (i.recommended_action, i.date, i.place_id, i.related_place_id)
        if i.code not in groups.setdefault(key, []):
            groups[key].append(i.code)
    out = []
    for (action, date, place_id, related), codes in groups.items():
        kind = ("evidence_lookup" if action in EVIDENCE_ACTIONS else
                "traveller_decision" if action == "ask_traveller" else "plan_change")
        out.append(RevisionTarget(action=action, kind=kind, date=date, place_id=place_id, related_place_id=related,
                                  codes=codes))
    return out


# ------------------------------------------------------------------ the injectable fresh lookups
class FreshLookups:
    """The injected lookups, cached per question, bounded and logged. A raised exception becomes a failed result,
    never a guess. None from hours()/travel() means no lookup was made (no source, or the limit was reached)."""

    def __init__(self, hours_lookup: HoursLookup | None = None, travel_lookup: TravelLookup | None = None, *,
                 max_lookups: int = DEFAULT_MAX_LOOKUPS):
        self._hours, self._travel, self._max = hours_lookup, travel_lookup, max_lookups
        self._cache: dict[tuple, dict] = {}
        self.calls: list[dict] = []
        self.skipped = 0  # questions not asked because the limit was reached

    def hours(self, place_name: str) -> dict | None:
        return self._ask("get_opening_hours", self._hours, (place_name,))

    def travel(self, from_place: str, to_place: str) -> dict | None:
        return self._ask("travel_time", self._travel, (from_place, to_place))

    def _ask(self, tool: str, fn, args: tuple) -> dict | None:
        key = (tool, *args)
        if key in self._cache:
            return self._cache[key]
        if fn is None:
            return None
        if len(self.calls) >= self._max:
            self.skipped += 1
            return None
        result, ms = _call(fn, args)
        self._record(tool, args, result, ms)
        return result

    def _record(self, tool: str, args: tuple, result: dict, ms: int) -> None:
        self.calls.append({"tool": tool, "ok": _ok(result), "ms": ms, "args": list(args),
                           "freshness": freshness_of(result) if _ok(result) else None})
        self._cache[(tool, *args)] = result

    def prefetch(self, questions: list[tuple[str, tuple]]) -> None:
        """Ask the questions the checks will ask, concurrently: the verification then waits for the slowest
        lookup, not for their sum (about 40 lookups for a six-day plan). Each is a real lookup made now, within
        the same limit; the results and the call log are the same as asking them one by one, in this order."""
        fns = {"get_opening_hours": self._hours, "travel_time": self._travel}
        todo = [(tool, args) for tool, args in dict.fromkeys(questions)
                if (tool, *args) not in self._cache and fns.get(tool) is not None]
        todo = todo[:max(0, self._max - len(self.calls))]
        if len(todo) < 2:
            return
        with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(todo))) as pool:
            futures = [pool.submit(_call, fns[tool], args) for tool, args in todo]
            for (tool, args), future in zip(todo, futures):
                self._record(tool, args, *future.result())


PREFETCH_WORKERS = 6


def _call(fn, args: tuple) -> tuple[dict, int]:
    started = time.perf_counter()
    try:
        result = fn(*args)
    except Exception as exc:  # a failing tool is missing evidence, never a fact
        result = {"ok": False, "error": {"code": "lookup_error", "message": type(exc).__name__}}
    if not isinstance(result, dict):
        result = {"ok": False, "error": {"code": "bad_result", "message": "the result is not a JSON object"}}
    return result, round((time.perf_counter() - started) * 1000)


# ------------------------------------------------------------------ one planned visit
class _Visit(NamedTuple):
    date: dt.date
    position: int  # index in the day's activity list
    activity: Activity
    place_id: str | None  # the activity's, or the one listing its name matches
    place: ResearchedPlace | None

    @property
    def key(self) -> str:
        return self.place_id or f"name:{fold(self.activity.name)}"

    @property
    def label(self) -> str:
        return self.activity.name

    @property
    def timed(self) -> bool:
        a = self.activity
        return _valid_time(a.start_time) and _valid_time(a.end_time) and to_minutes(a.end_time) > to_minutes(a.start_time)

    @property
    def start(self) -> int:
        return to_minutes(self.activity.start_time)

    @property
    def end(self) -> int:
        return to_minutes(self.activity.end_time)


_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _valid_time(t) -> bool:
    return isinstance(t, str) and bool(_TIME.match(t))


class _Route(NamedTuple):
    minutes: int | None  # None: no measured time
    freshness: Freshness | None
    profile: str | None
    citation: dict | None
    evidence: Evidence | None


def _route_evidence(result: dict, freshness: Freshness, source: str = "travel_time") -> Evidence:
    cite = result.get("citation") or {}
    return Evidence(source=source, freshness=freshness, citation_id=cite.get("id"),
                    source_url=result.get("source_url") or cite.get("url"), retrieved_at=result.get("retrieved_at"),
                    detail=f"{result.get('duration_min')} min {result.get('profile') or ''}".strip())


def _hours_evidence(day: DayHours, source: str, freshness: Freshness, citation: dict | None = None) -> Evidence:
    text = day.status + (f" {day.windows_text}" if day.windows and day.status != "closed" else "")
    detail = f"{day.date} ({day.weekday}): {text}" + (f"; {day.note}" if day.note else "")
    if day.raw:
        detail += f"; listed: {day.raw[:120]}"
    return Evidence(source=source, freshness=freshness, detail=detail, citation_id=(citation or {}).get("id"),
                    source_url=(citation or {}).get("url"))


# ------------------------------------------------------------------ the verifier
class _Verifier:
    def __init__(self, bundle: ResearchBundle, budget_report: BudgetReport | None, planner_report: PlannerReport | None,
                 rules: ScheduleRules,
                 lookups: FreshLookups):
        self.bundle, self.trip, self.rules, self.lookups = bundle, bundle.trip, rules, lookups
        self.budget_report, self.planner_report = budget_report, planner_report
        self.places = {p.place_id: p for p in bundle.places}
        self.cands = prepare_candidates(bundle)  # Python's own pricing of every listing, independent of any report
        self.dates = trip_dates(bundle.trip)
        self.found: list[VerificationIssue] = []
        self.ran: list[str] = []
        self.facts: list[Freshness] = []  # the freshness of every hours and route fact a check relied on
        self.citations: list[dict] = []
        self.notes: list[str] = []
        self.routes: dict[tuple[str, str], _Route] = {}
        self.planner_same = False  # the Planner's itinerary is the one being verified
        self.from_store = False  # a Trip Store dict: no place ids and no cost_status, so names and amounts only

    def add(self, code: str, explanation: str, **kw) -> None:
        self.found.append(VerificationIssue.of(code, explanation, **kw))

    def cite(self, c: dict | None) -> None:
        if c and c.get("id") not in {x.get("id") for x in self.citations}:
            self.citations.append(c)

    # -------------------------------------------------- resolving the plan
    def resolve(self, it: Itinerary) -> list[_Visit]:
        by_name: dict[str, list[ResearchedPlace]] = {}
        for p in self.bundle.places:
            by_name.setdefault(fold(p.name), []).append(p)
        visits = []
        for day in it.days:
            for n, a in enumerate(day.activities):
                pid = a.place_id
                if pid is None:
                    hits = by_name.get(fold(a.name), [])
                    pid = hits[0].place_id if len(hits) == 1 else None
                visits.append(_Visit(day.date, n, a, pid, self.places.get(pid) if pid else None))
        return visits

    def sorted_days(self, visits: list[_Visit]) -> dict[dt.date, list[_Visit]]:
        days: dict[dt.date, list[_Visit]] = {}
        for v in visits:
            if v.timed:
                days.setdefault(v.date, []).append(v)
        return {d: sorted(vs, key=lambda v: (v.start, v.position)) for d, vs in days.items()}

    # -------------------------------------------------- itinerary: dates and times
    def check_itinerary(self, it: Itinerary, visits: list[_Visit]) -> None:
        self.ran.append("itinerary")
        trip = set(self.dates)
        for day in it.days:
            if day.date not in trip and day.activities:
                self.add("date_outside_trip", f"{day.date} is not a trip date ({self.dates[0]} to {self.dates[-1]}), "
                         f"but {len(day.activities)} activities are planned on it.", date=day.date)
        planned = {d.date for d in it.days}
        for d in self.dates:
            if d not in planned:
                self.add("trip_date_missing", f"The itinerary has no entry for the trip date {d} ({d:%a}).", date=d)
        for v in visits:
            a = v.activity
            if a.start_time is None and a.end_time is None:
                self.add("activity_untimed", f"{v.label} on {v.date} has no times: its hours, overlaps and travel "
                         "cannot be checked.", date=v.date, place_id=v.place_id)
            elif not v.timed:
                self.add("invalid_activity_time", f"{v.label} on {v.date} has invalid times "
                         f"({a.start_time!r}-{a.end_time!r}).", date=v.date, place_id=v.place_id)

    # -------------------------------------------------- places: duplicates and what may be planned
    def check_places(self, visits: list[_Visit]) -> None:
        self.ran.append("places")
        first: dict[str, _Visit] = {}
        for v in visits:
            if v.key in first:
                f = first[v.key]
                self.add("duplicate_place", f"{v.label} is planned more than once ({f.date} and {v.date}).",
                         date=v.date, place_id=v.place_id)
            else:
                first[v.key] = v
        withheld = {w.place_id: w.reason for w in self.cands.withheld}
        report = self.budget_report
        refused: dict[str, tuple[str, str]] = {}
        if report is not None:
            for items, code, why in ((report.excluded, "excluded_place", "excluded by the Budget Analyst"),
                                     (report.dropped_for_budget, "excluded_place", "left out to fit the budget"),
                                     (report.optional, "not_selected_place", "an optional alternative, not selected")):
                for item in items:
                    refused.setdefault(item.place_id, (code, why))
            for w in report.withheld:
                withheld.setdefault(w.place_id, w.reason)
        else:
            self.add("exclusion_lists_unavailable", "No Budget Analyst report was given: whether each place was "
                     "selected (and not excluded) cannot be checked.")
        selected = {i.place_id for i in report.selected} if report is not None else None
        for v in first.values():
            if v.place is None:
                if v.activity.place_id:
                    self.add("place_not_in_research", f"{v.label} ({v.activity.place_id}) is not in the research: "
                             "no tool result backs it.", date=v.date, place_id=v.activity.place_id)
                else:
                    self.add("place_unidentified", f"{v.label} has no place id and its name matches no single "
                             "researched listing.", date=v.date)
                continue
            if v.place.synthetic:
                self.add("synthetic_place", f"{v.label} is a synthetic test listing, not a real place.", date=v.date,
                         place_id=v.place_id, evidence=[Evidence(source="research listing", freshness="recorded",
                                                                 detail="synthetic: true")])
            elif v.place_id in withheld:
                self.add("withheld_place", f"{v.label} was withheld: {withheld[v.place_id]}.", date=v.date,
                         place_id=v.place_id)
            elif v.place_id in refused:
                code, why = refused[v.place_id]
                self.add(code, f"{v.label} is {why}; it is not in the plan's budget.", date=v.date, place_id=v.place_id)
            elif selected is not None and v.place_id not in selected:
                self.add("not_selected_place", f"{v.label} was not selected by the Budget Analyst.", date=v.date,
                         place_id=v.place_id)
        if report is None:
            return
        planned = {v.place_id for v in visits}
        unscheduled = {u.place_id: u.reason for u in (self.planner_report.unscheduled if self.planner_report else [])}
        for item in report.selected:
            if item.place_id in planned or item.decision == "required":  # required: the required_places check
                continue
            if item.place_id in unscheduled:
                self.add("selected_place_unscheduled", f"{item.name} was selected but is not in the plan: "
                         f"{unscheduled[item.place_id]}.", place_id=item.place_id,
                         evidence=[Evidence(source="planner report", freshness="claimed",
                                            detail=unscheduled[item.place_id][:200])])
            else:
                self.add("selected_place_unaccounted", f"{item.name} was selected by the Budget Analyst but is "
                         "neither in the plan nor reported as unscheduled.", place_id=item.place_id)

    # -------------------------------------------------- required places
    def check_required(self, visits: list[_Visit]) -> None:
        self.ran.append("required_places")
        found, missing = match_required(self.trip.required_places, self.bundle.places)
        planned = {v.place_id for v in visits}
        withheld = {w.place_id for w in self.cands.withheld}
        for pid, wanted in found.items():
            if pid in planned:
                continue
            place = self.places[pid]
            closed = all(hours_on(place.hours, d).status == "closed" for d in self.dates)
            if pid in withheld:
                why, action = "it is withheld (synthetic or untrusted)", "ask_traveller"
            elif closed:
                why, action = "the listing says it is closed on every trip date", "ask_traveller"
            else:
                why, action = "at least one trip date can take it", "add_required_place"
            self.add("required_place_missing", f"'{wanted}' was asked for but {place.name} is not in the plan; {why}.",
                     place_id=pid, action=action)
        for name in missing:
            self.add("required_place_unresolved", f"'{name}' was asked for, but the research has no single listing "
                     "for it: whether it is planned cannot be checked.")

    # -------------------------------------------------- schedule: overlaps and the planning day
    def check_schedule(self, visits: list[_Visit]) -> None:
        self.ran.append("schedule")
        day_start, day_end = to_minutes(self.rules.day_start), to_minutes(self.rules.day_end)
        for date, day in self.sorted_days(visits).items():
            if [v.position for v in day] != sorted(v.position for v in day):
                self.add("activity_order_mismatch", f"On {date} the activities are not listed in time order.",
                         date=date)
            for v in day:
                if v.start < day_start or v.end > day_end:
                    self.add("outside_planning_day", f"{v.label} on {date} ({v.activity.start_time}-"
                             f"{v.activity.end_time}) is outside the planning day {self.rules.day_start}-"
                             f"{self.rules.day_end}.", date=date, place_id=v.place_id)
            for prev, cur in zip(day, day[1:]):
                if cur.start < prev.end:
                    self.add("activity_overlap", f"{cur.label} starts at {cur.activity.start_time}, before "
                             f"{prev.label} ends ({prev.activity.end_time}) on {date}.", date=date,
                             place_id=cur.place_id, related_place_id=prev.place_id)

    # -------------------------------------------------- opening hours, fresh where possible
    def check_hours(self, visits: list[_Visit]) -> None:
        self.ran.append("opening_hours")
        for v in visits:
            hours, freshness, source, citation = (v.place.hours if v.place else None), "recorded", "listing hours", \
                (v.place.citation if v.place else None)
            have_listing = v.place is not None
            result = self.lookups.hours(v.activity.name)
            if result is not None:
                if not _ok(result):
                    self.add("hours_lookup_failed", f"The fresh opening-hours lookup for {v.label} failed "
                             f"({_error_code(result)}); the recorded listing is used.", date=v.date,
                             place_id=v.place_id)
                elif v.place_id and result.get("id") and result["id"] != v.place_id:
                    self.add("hours_source_mismatch", f"The opening-hours lookup for {v.label} returned another "
                             f"listing ({result['id']}); the recorded listing is used, but the hours on {v.date} "
                             "are not confirmed.", date=v.date, place_id=v.place_id)
                else:
                    fresh = hours_on(result.get("hours"), v.date)
                    if have_listing:
                        old = hours_on(hours, v.date)
                        if (old.status, old.windows) != (fresh.status, fresh.windows):
                            was, now = (" ".join(filter(None, (h.status, h.windows_text))) for h in (old, fresh))
                            self.add("hours_changed", f"{v.label} on {v.date}: the recorded listing said {was}, the "
                                     f"lookup says {now}; the lookup is used.", date=v.date, place_id=v.place_id)
                        self._price_changed(v, result)
                    if result.get("synthetic") and not (v.place and v.place.synthetic):
                        self.add("synthetic_place", f"The opening-hours lookup says {v.label} is a synthetic "
                                 "listing.", date=v.date, place_id=v.place_id)
                    hours, freshness, source, citation = result.get("hours"), freshness_of(result), \
                        "get_opening_hours", result.get("citation") or citation
                    have_listing = True
            if not have_listing:
                self.add("hours_unknown", f"{v.label}: no listing to read opening hours from.", date=v.date,
                         place_id=v.place_id)
                continue
            day = hours_on(hours, v.date)
            ev = [_hours_evidence(day, source, freshness, citation)]
            self.facts.append(freshness)
            if day.status == "closed":
                self.add("closed_on_date", f"{v.label} is closed on {v.date} ({day.weekday}): {day.note}.", date=v.date,
                         place_id=v.place_id, evidence=ev)
            elif not v.timed:
                if day.status != "open":
                    self.add("hours_unknown" if day.status == "unknown" else "hours_unconfirmed",
                             f"{v.label} on {v.date}: opening hours {day.status} ({day.note}).", date=v.date,
                             place_id=v.place_id, evidence=ev)
            elif day.windows and not any(a <= v.start and v.end <= b for a, b in day.windows):
                self.add("outside_opening_hours", f"{v.label} is planned {v.activity.start_time}-{v.activity.end_time} "
                         f"on {v.date}, outside its listed hours {day.windows_text}.", date=v.date,
                         place_id=v.place_id, evidence=ev)
            elif day.status == "unknown":
                self.add("hours_unknown", f"{v.label}: opening hours on {v.date} unknown ({day.note}).", date=v.date,
                         place_id=v.place_id, evidence=ev)
            elif day.status == "unconfirmed":
                self.add("hours_unconfirmed", f"{v.label}: opening hours not confirmed for {day.weekday} "
                         f"({day.note}).", date=v.date, place_id=v.place_id, evidence=ev)

    def _price_changed(self, v: _Visit, result: dict) -> None:
        old = ((v.place.price or {}).get("raw") or "").strip()
        new = ((result.get("price") or {}).get("raw") or "").strip()
        if old != new:
            self.add("price_changed", f"{v.label}: the listed price changed from {old or 'none'!r} to "
                     f"{new or 'none'!r}; the budget uses the researched price.", date=v.date, place_id=v.place_id)

    # -------------------------------------------------- travel between consecutive stops, and walking
    def route(self, prev: _Visit, cur: _Visit) -> _Route:
        key = (prev.key, cur.key)
        if key in self.routes:
            return self.routes[key]
        out = _Route(None, None, None, None, None)
        result = self.lookups.travel(prev.activity.name, cur.activity.name)
        if result is not None and _ok(result) and isinstance(result.get("duration_min"), int) \
                and not isinstance(result.get("duration_min"), bool) and result["duration_min"] >= 0:
            f = freshness_of(result)
            out = _Route(result["duration_min"], f, result.get("profile"), result.get("citation"),
                         _route_evidence(result, f))
        else:
            if result is not None:
                self.add("route_lookup_failed", f"The travel-time lookup {prev.label} -> {cur.label} failed "
                         f"({_error_code(result)}).", date=cur.date, place_id=cur.place_id,
                         related_place_id=prev.place_id)
            leg = self._planner_leg(prev.place_id, cur.place_id)
            if leg is not None:
                ev = Evidence(source="travel_time (recorded by the Planner)", freshness="recorded",
                              detail=f"{leg.minutes} min", citation_id=(leg.citation or {}).get("id"),
                              source_url=(leg.citation or {}).get("url"))
                out = _Route(leg.minutes, "recorded", None, leg.citation, ev)
        self.routes[key] = out
        return out

    def _planner_leg(self, a: str | None, b: str | None):
        if self.planner_report is None or a is None or b is None:
            return None
        for day in self.planner_report.schedule.days:
            for s in day.scheduled:
                t = s.travel
                if t is not None and t.status == "measured" and (t.from_place_id, t.to_place_id) == (a, b):
                    return t
        return None

    def check_travel(self, visits: list[_Visit]) -> None:
        self.ran += ["travel", "walking"]
        limit = self.rules.max_walk_minutes
        if not limit:
            self.notes.append("No walking limit was given: walks are checked against the schedule only.")
        for date, day in self.sorted_days(visits).items():
            for prev, cur in zip(day, day[1:]):
                if cur.start < prev.end:
                    continue  # an overlap: the schedule check reports it, travel cannot fit either way
                r = self.route(prev, cur)
                gap = cur.start - prev.end
                where = dict(date=date, place_id=cur.place_id, related_place_id=prev.place_id)
                if r.minutes is None:
                    self.add("route_unknown", f"No measured travel time {prev.label} -> {cur.label} on {date}: the "
                             f"{gap}-minute gap cannot be checked.", **where)
                    if limit:
                        self.add("walk_unverified", f"The walk to {cur.label} on {date} cannot be checked against "
                                 f"the {limit}-minute limit.", **where)
                    continue
                self.facts.append(r.freshness)
                self.cite(r.citation)
                ev = [r.evidence]
                if gap < r.minutes:
                    self.add("insufficient_travel_gap", f"{prev.label} ends at {prev.activity.end_time} and "
                             f"{cur.label} starts at {cur.activity.start_time} on {date}: {gap} minutes for a "
                             f"{r.minutes}-minute trip.", evidence=ev, **where)
                claim = cur.activity.travel_minutes_from_previous
                if claim is not None and claim != r.minutes:
                    self.add("travel_minutes_mismatch", f"{cur.label} on {date} says {claim} minutes of travel, the "
                             f"measured time is {r.minutes}.", evidence=ev, **where)
                if limit:
                    if r.profile not in (None, "foot"):
                        self.add("walk_unverified", f"The travel time to {cur.label} on {date} is a {r.profile} "
                                 "time, not a walking time.", evidence=ev, **where)
                    elif r.minutes > limit:
                        self.add("walk_over_limit", f"The walk to {cur.label} on {date} takes {r.minutes} minutes, "
                                 f"over the {limit}-minute limit.", evidence=ev, **where)

    # -------------------------------------------------- money: the totals and each activity's cost
    def check_budget(self, it: Itinerary, summary: BudgetSummary, claimed_total) -> None:
        self.ran.append("budget")
        trip = self.trip
        calc = Evidence(source="summarize()", freshness="computed",
                        detail=f"known {summary.known_total} {summary.currency}, status {summary.status}")
        for who, cur in (("the itinerary", it.currency),
                         ("the Budget Analyst report", self.budget_report.currency if self.budget_report else None),
                         ("the Planner report", self.planner_report.currency if self.planner_report else None)):
            if cur is not None and cur != trip.currency:
                self.add("currency_mismatch", f"{who} is in {cur}, the traveller's budget is in {trip.currency}.")
        for who, n in (("the itinerary", it.travellers),
                       ("the Budget Analyst report", self.budget_report.travellers if self.budget_report else None)):
            if n is not None and n != trip.travellers:
                self.add("travellers_mismatch", f"{who} is priced for {n} travellers, the request is for "
                         f"{trip.travellers}.")
        want = None if trip.budget_total is None else money(trip.budget_total)
        have = None if it.budget_total is None else money(it.budget_total)
        if want != have:
            self.add("budget_limit_mismatch", f"The itinerary's budget is {have}, the traveller's is {want}.")
        if claimed_total is not None and money(claimed_total) != summary.known_total:
            self.add("budget_total_mismatch", f"The itinerary states a total of {money(claimed_total)}, the "
                     f"activities add up to {summary.known_total} {summary.currency}.", evidence=[calc])
        pb = self.planner_report.budget if self.planner_report is not None and self.planner_same else None
        if pb is not None:
            claim = Evidence(source="planner report", freshness="claimed", detail=f"known {pb.known_total}, "
                             f"status {pb.status}")
            for field in ("known_total", "low_total"):
                if getattr(pb, field) != getattr(summary, field):
                    self.add("budget_total_mismatch", f"The Planner reports {field} {getattr(pb, field)}, "
                             f"recalculated: {getattr(summary, field)}.", evidence=[claim, calc])
            if pb.status != summary.status:
                self.add("budget_verdict_mismatch", f"The Planner reports the budget as {pb.status}, recalculated: "
                         f"{summary.status}.", evidence=[claim, calc])
            theirs = {d.date: d.known_total for d in pb.days}
            for d in summary.days:
                if d.date in theirs and theirs[d.date] != d.known_total:
                    self.add("day_total_mismatch", f"{d.date}: the Planner reports {theirs[d.date]}, recalculated: "
                             f"{d.known_total}.", date=d.date, evidence=[claim, calc])
        if summary.status == "over_budget":
            low = " (the lower ends of the price ranges would fit)" if summary.fits_at_low_estimate else ""
            self.add("over_budget", f"The planned costs come to {summary.known_total} {summary.currency}, "
                     f"{summary.overrun} over the {summary.budget_total} budget{low}.", evidence=[calc])
        elif summary.status == "undetermined":
            self.add("budget_undetermined", f"{len(summary.unknown_items)} cost(s) are unknown: the plan's known "
                     f"costs ({summary.known_total} {summary.currency}) fit, but the total cannot be confirmed.",
                     evidence=[calc])
        elif summary.status == "no_budget":
            self.notes.append("No budget was given: the totals are reported, not judged.")

    def check_costs(self, it: Itinerary, visits: list[_Visit]) -> None:
        self.ran.append("costs")
        same_terms = it.currency == self.trip.currency and it.travellers == self.trip.travellers
        for v in visits:
            a = v.activity
            cand = self.cands.get(v.place_id) if v.place_id else None
            if a.estimated_cost is None:
                why = cand.cost.missing_reason if cand is not None and cand.cost.status == "unknown" else None
                self.add("cost_unknown", f"{v.label} on {v.date}: cost unknown{f' ({why})' if why else ''}; it is "
                         "not counted as free.", date=v.date, place_id=v.place_id)
            elif a.cost_status == "range":
                self.add("cost_range", f"{v.label}: {a.cost_min}-{a.cost_max} {it.currency}; {a.estimated_cost} is "
                         "budgeted.", date=v.date, place_id=v.place_id)
            if cand is None or not same_terms:
                continue  # an unknown place is the places check's; other currency or party: the budget check's
            want = cand.cost.estimated
            have = None if a.estimated_cost is None else money(a.estimated_cost)
            if want != have or (not self.from_store and cand.cost.status != a.cost_status):
                self.add("activity_cost_mismatch", f"{v.label}: the plan says {have} ({a.cost_status}), Python's "
                         f"pricing of the listing gives {want} ({cand.cost.status}): {cand.cost.note()}.", date=v.date,
                         place_id=v.place_id, evidence=[Evidence(source="price_activity()", freshness="computed",
                                                                 detail=cand.cost.note(), source_url=cand.source_url)])
            elif cand.cost.rate is not None and cand.cost.rate.citation_id:
                r = cand.cost.rate
                self.cite({"id": r.citation_id, "title": r.describe(), "url": r.source_url})

    # -------------------------------------------------- sources and citations
    def check_evidence(self, visits: list[_Visit]) -> None:
        self.ran.append("evidence")
        for v in visits:
            a = v.activity
            if a.source_url is None:
                self.add("source_missing", f"{v.label} on {v.date} has no source link.", date=v.date,
                         place_id=v.place_id)
            elif v.place is not None and v.place.source_url and a.source_url != v.place.source_url:
                self.add("source_mismatch", f"{v.label}'s source link is not the researched listing's "
                         f"({v.place.source_url}).", date=v.date, place_id=v.place_id)
            if v.place is None:
                continue
            if v.place.citation:
                self.cite(v.place.citation)
            else:
                self.add("citation_missing", f"The research for {v.label} carries no citation.", date=v.date,
                         place_id=v.place_id)
            if v.activity.place_id and fold(a.name) != fold(v.place.name):
                self.add("place_name_mismatch", f"'{a.name}' is planned under the id of '{v.place.name}'.",
                         date=v.date, place_id=v.place_id)
        for (a, b), r in self.routes.items():
            if r.minutes is not None and not r.citation:
                self.add("route_citation_missing", f"The travel time {a} -> {b} has no citation.", place_id=b,
                         related_place_id=a)
        for f in self.bundle.failures:
            self.add("research_tool_failure", f"The research step's {f.tool} call failed ({f.code}): what it would "
                     "have returned is missing from the evidence.")

    # -------------------------------------------------- the Planner's own claims
    def check_planner(self, it: Itinerary, visits: list[_Visit]) -> None:
        pr = self.planner_report
        self.ran.append("planner_report")
        if not self.planner_same:
            self.add("planner_itinerary_differs", "The itinerary verified is not the Planner's (revised since?): the "
                     "Planner's status, totals and travel legs are not compared.")
        if pr.status == "partial":
            self.add("planner_reported_partial", f"The Planner reports a partial plan: {len(pr.unscheduled)} "
                     "selected place(s) not scheduled.")
        elif pr.status == "infeasible":
            self.add("planner_reported_infeasible", "The Planner reports the plan as infeasible.")
        if not self.planner_same:
            return
        planned = {v.place_id for v in visits}
        required, _ = match_required(self.trip.required_places, self.bundle.places)
        if any(r not in planned for r in required) or not visits:
            expected = "infeasible"
        elif self.budget_report is not None and any(i.place_id not in planned for i in self.budget_report.selected):
            expected = "partial"
        elif self.budget_report is None and pr.status == "partial":
            expected = "partial"  # cannot tell without the selection: the Planner's word stands
        else:
            expected = "complete"
        if pr.status != expected:
            self.add("planner_status_inconsistent", f"The Planner reports '{pr.status}', the itinerary shows "
                     f"'{expected}'.")
        for u in pr.unscheduled:
            if u.place_id in planned:
                self.add("planner_status_inconsistent", f"The Planner lists {u.name} as unscheduled, but it is in the "
                         "itinerary.", place_id=u.place_id)


# ------------------------------------------------------------------ the entry point
_PASS_TEXT = {
    "itinerary": "Every activity is on a trip date with valid times.",
    "places": "Each place appears once and every place is a selected, real listing.",
    "required_places": "Every place the traveller asked for is in the plan.",
    "schedule": "No overlaps; every activity is inside the planning day.",
    "opening_hours": "Every visit is inside the place's listed opening hours on its date.",
    "travel": "Every gap between activities covers the measured travel time.",
    "walking": "Every measured walk is within the traveller's limit (or no limit was given).",
    "budget": "The totals recalculate exactly and the plan fits the budget.",
    "costs": "Every cost is known and matches Python's pricing of the listing.",
    "evidence": "Every activity has its source and citation.",
    "planner_report": "The Planner's status and totals match the itinerary.",
}


def _check_results(found: list[VerificationIssue], ran: list[str]) -> list[CheckResult]:
    out = []
    for name in CHECK_ORDER:
        if name not in ran:
            continue
        mine = [i for i in found if i.check == name]
        sev = {i.severity for i in mine}
        status = "FAIL" if "error" in sev else "UNKNOWN" if "unknown" in sev else "PASS"
        codes = list(dict.fromkeys(i.code for i in mine))
        if status == "PASS":
            summary = _PASS_TEXT[name] + (f" Warnings: {', '.join(codes)}." if codes else "")
        else:
            counts = ", ".join(f"{sum(i.severity == s for i in mine)} {s}" for s in ("error", "unknown", "warning")
                               if s in sev)
            summary = f"{counts}: {', '.join(codes)}."
        out.append(CheckResult(check=name, status=status, summary=summary, codes=codes))
    return out


def _basis(facts: list[Freshness]) -> EvidenceBasis:
    if not facts:
        return "none"
    if all(f == "fresh" for f in facts):
        return "fresh"
    return "recorded" if not any(f == "fresh" for f in facts) else "mixed"


def _report(found: list[VerificationIssue], ran: list[str], *, facts: list[Freshness] = (), budget=None,
            lookups: FreshLookups | None = None, citations=(), notes=()) -> VerificationReport:
    checks = _check_results(found, ran)
    statuses = {c.status for c in checks}
    status = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    basis = _basis(list(facts))
    notes = list(notes)
    if status == "PASS" and basis != "fresh":
        notes.append("Every check passed on recorded data (research listings, replay cache): the plan is consistent "
                     "but not freshly verified.")
    if lookups is not None and lookups.skipped:
        notes.append(f"{lookups.skipped} fresh lookup(s) were not made: the lookup limit was reached.")
    notes.append("Prices are the researched listing prices (estimates); only entry fees and listed meals are priced.")
    return VerificationReport(
        status=status, verified=status == "PASS" and basis == "fresh", evidence_basis=basis, checks=checks,
        issues=[i for i in found if i.severity == "error"], unknowns=[i for i in found if i.severity == "unknown"],
        warnings=[i for i in found if i.severity == "warning"],
        verified_checks=[c.check for c in checks if c.status == "PASS"], revision_targets=revision_targets(found),
        budget=budget, tool_evidence=list(lookups.calls) if lookups else [], citations=list(citations), notes=notes)


def _from_store(data: dict) -> dict:
    """A Trip Store itinerary dict as Itinerary input: estimated_total is a claim (checked, not loaded), and an
    estimated_cost of 0 is the store's way of saying "free" (to_trip_store() keeps no cost_status)."""
    out = {k: v for k, v in data.items() if k != "estimated_total"}
    days = []
    for day in data.get("days") or []:
        if isinstance(day, dict) and isinstance(day.get("activities"), list):
            acts = [{**a, "cost_status": "free"} if isinstance(a, dict) and a.get("estimated_cost") == 0
                    and "cost_status" not in a else a for a in day["activities"]]
            day = {**day, "activities": acts}
        days.append(day)
    if "days" in data:
        out["days"] = days
    return out


def verify_itinerary(itinerary: Itinerary | dict | None, bundle: ResearchBundle, *,
                     budget_report: BudgetReport | None = None, planner_report: PlannerReport | None = None,
                     hours_lookup: HoursLookup | None = None, travel_lookup: TravelLookup | None = None,
                     rules: ScheduleRules | None = None, max_lookups: int = DEFAULT_MAX_LOOKUPS) -> VerificationReport:
    """Check the final itinerary against the request, the research and (when given) fresh lookups.

    `itinerary`: an Itinerary, a Trip Store dict (its estimated_total is checked as a claim), or None to take the
    Planner report's. `planner_report` is a PlannerReport (its status, totals and recorded travel legs are checked,
    never trusted). `hours_lookup` / `travel_lookup` wrap the get_opening_hours / travel_time MCP tools, injected
    by the Coordinator; tests replay recorded results. Never raises on a bad plan: every problem is a finding.
    """
    rules = rules or ScheduleRules.from_trip(bundle.trip)
    lookups = FreshLookups(hours_lookup, travel_lookup, max_lookups=max_lookups)
    v = _Verifier(bundle, budget_report, planner_report, rules, lookups)
    if itinerary is None and planner_report is not None:
        itinerary = planner_report.itinerary
        if itinerary is None:
            v.add("invalid_itinerary", "There is no itinerary to verify.")
            v.add("planner_no_itinerary", f"The Planner produced no itinerary (status {planner_report.status}).")
            return _report(v.found, ["itinerary", "planner_report"], lookups=lookups,
                           notes=["No itinerary: the other checks were not run."])
    claimed_total = None
    if isinstance(itinerary, dict):
        claimed_total = itinerary.get("estimated_total")
        v.from_store = True
        try:
            itinerary = Itinerary.model_validate(_from_store(itinerary))
        except ValidationError as exc:
            for e in exc.errors()[:10]:
                v.add("invalid_itinerary", f"{'.'.join(map(str, e['loc'])) or 'itinerary'}: {e['msg']}")
            return _report(v.found, ["itinerary"], lookups=lookups,
                           notes=["The itinerary could not be read: the other checks were not run."])
    if not isinstance(itinerary, Itinerary):
        v.add("invalid_itinerary", "No itinerary was given.")
        return _report(v.found, ["itinerary"], lookups=lookups)
    v.planner_same = planner_report is not None and planner_report.itinerary == itinerary
    visits = v.resolve(itinerary)
    # The lookups check_hours and check_travel make (every visit's hours, every consecutive pair's walk), asked at
    # once; the checks then read the answers.
    pairs = [(a, b) for a, b in zip(visits, visits[1:]) if a.date == b.date]
    lookups.prefetch([("get_opening_hours", (x.activity.name,)) for x in visits] +
                     [("travel_time", (a.activity.name, b.activity.name)) for a, b in pairs])
    v.check_itinerary(itinerary, visits)
    v.check_places(visits)
    v.check_required(visits)
    v.check_schedule(visits)
    v.check_hours(visits)
    v.check_travel(visits)
    summary = summarize(itinerary)
    v.check_budget(itinerary, summary, claimed_total)
    v.check_costs(itinerary, visits)
    v.check_evidence(visits)
    if planner_report is not None:
        v.check_planner(itinerary, visits)
    else:
        v.notes.append("No Planner report was given: its status and totals were not compared.")
    return _report(v.found, v.ran, facts=v.facts, budget=summary, lookups=lookups, citations=v.citations,
                   notes=v.notes)
