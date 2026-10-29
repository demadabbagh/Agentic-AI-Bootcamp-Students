"""Targeted Revision, deterministic part: fix what the Verifier found without rebuilding the whole plan.

  VerificationReport.revision_targets  ->  RevisionPlan (one RevisionStep per target)  ->  a revised itinerary
                                                                                      ->  verify_itinerary() again

A revision edits the Planner's own representation, never times or numbers: which selected place goes on which
trip date, in what order (a DayAssignment). Then
  - build_itinerary() validates the assignment with the Planner's own rules (selected places only, each exactly
    once, never on a closed date, a required place never left out while a date can take it) and schedules it;
  - every date no step touched is copied from the original itinerary verbatim: unaffected activities keep their
    times, costs and notes exactly;
  - verify_itinerary() checks the result from scratch.
Times come from the scheduler, costs from the Budget Analyst's pricing, travel times from the injected travel_time
lookup (bounded, cached). Nothing is invented: a missing price, route or opening time becomes an EvidenceRequest
naming the MCP tool and its arguments, never a value.

Steps by kind:
  plan_change         remove, move, reschedule, reorder, add a required place, restore a selected place,
                      reprice from the listing, recompute totals, align with the request, reduce cost
  evidence_request    lookup_opening_hours / lookup_travel_time / lookup_price / add_source: the tool to call
  traveller_decision  ask_traveller: no plan change can settle it
  escalation          rebuild_itinerary: not a targeted fix (back to the Planner)

Bounded: at most MAX_ATTEMPTS rounds of plan -> rebuild -> re-verify; each round's searches are bounded (trip dates
x insertion positions, orders of at most MAX_REORDER_STOPS stops, one eviction to make room), and travel lookups are
capped. A round is kept only if it leaves strictly fewer confirmed errors, adds no new kind of confirmed error, and
every required place that was planned is still planned; otherwise the previous itinerary stands. The final status
and verified flag are always the final VerificationReport's. Nothing here calls an LLM, saves or books anything.

Choices: where a step has two or more feasible options (the date a place moves to, the place that gives way for a
required one, the order that fixes a walk), the round records a DecisionPoint with up to MAX_ALTERNATIVES options,
each scheduled by Python, and applies Python's best-ranked one. An optional `decide` callback (revision_agent.py)
may pick others in the first round; Python re-runs that round with the picks and keeps it only if it is no worse
than its own (_compare).
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .budget import apply_to_activity, summarize
from .budget_analyst import BudgetReport, fold, prepare_candidates
from .itinerary_planner import (MAX_REORDER_STOPS, DayAssignment, DayChoice, InvalidAssignment, PlannerReport,
                                PlanningContext, UnscheduledChoice, build_itinerary)
from .models import DayPlan, Itinerary
from .research import ResearchBundle
from .schedule import DaySchedule, ScheduleRules, TravelLookup, hours_on, schedule_day
from .verification import (EVIDENCE_ACTIONS, CheckStatus, HoursLookup, RevisionAction, RevisionTarget,
                           VerificationReport, verify_itinerary)

MAX_ATTEMPTS = 3  # plan -> rebuild -> re-verify rounds
DEFAULT_MAX_LOOKUPS = 40  # travel_time lookups for all rounds together (cached pairs are free)

StepKind = Literal["plan_change", "evidence_request", "traveller_decision", "escalation"]
StepOutcome = Literal["applied", "impossible", "superseded", "pending_evidence", "awaiting_traveller", "escalated"]
RevisionOutcome = Literal["no_change_needed", "revised", "partially_revised", "unchanged"]

# Plan changes in the order they are applied: removals free places, moves free dates, additions fill them, then
# each touched date is settled (rescheduled, reordered), costs are corrected in place, budget cuts come last.
_ORDER = ["remove_activity", "move_activity", "add_required_place", "restore_selected_place", "reorder_day",
          "reschedule_activity", "align_with_request", "reprice_activity", "recalculate_budget", "reduce_cost"]
_KIND = {**{a: "plan_change" for a in _ORDER}, **{a: "evidence_request" for a in EVIDENCE_ACTIONS},
         "ask_traveller": "traveller_decision", "rebuild_itinerary": "escalation"}


# ------------------------------------------------------------------ the plan
class EvidenceRequest(BaseModel):
    """A fact the plan is missing, as the MCP call that would supply it. It carries no value: only the question."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Literal["get_opening_hours", "travel_time"]
    arguments: dict
    reason: str
    codes: list[str]
    place_id: str | None = None
    date: dt.date | None = None


DecisionKind = Literal["placement", "make_room", "reorder"]
MAX_ALTERNATIVES = 4  # feasible options offered per decision point, Python's best first


class Alternative(BaseModel):
    """One feasible option Python found and scheduled. Every number is the scheduler's; nothing is estimated."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # D1-A1: unique in the round
    key: str  # what the option is (date, date|place giving way, or the order): stable when the round is re-run
    summary: str  # Python's words
    date: dt.date
    order: list[str]  # the date's place ids in visiting order with this option
    gives_way: str | None = None  # make_room: the place that moves to another date or is left out
    hours: str | None = None  # the placed place's opening-hours status on that date (open / unconfirmed / unknown)
    hours_windows: str | None = None  # its recorded listing windows that date ("09:00-18:00"): the only opening times
    measured_travel_minutes: int  # sum of the measured walks of the date
    unmeasured_legs: int  # legs with no measured time (the planning buffer is used, never a guess)
    walks_over_limit: int
    places_on_date: int
    reordered_places: int  # places of that date whose relative order changes: the disruption
    schedule: list[dict] = Field(default_factory=list)  # Python's times: place_id, start, end


class DecisionPoint(BaseModel):
    """A choice with two or more feasible options. Python's default is its best-ranked one."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # D1, D2, ... in the order the round met them
    signature: str  # placement:<place> | make_room:<place> | reorder:<date>: stable when the round is re-run
    kind: DecisionKind
    place_id: str | None = None
    date: dt.date | None = None  # the date the place leaves (placement, make_room) or the date reordered
    hours_on_date: str | None = None  # the place's listed hours status on `date` (closed, open, ...), recorded
    alternatives: list[Alternative]
    default: str  # Python's choice (an alternative id)

    def by_id(self, alt_id: str) -> Alternative | None:
        return next((a for a in self.alternatives if a.id == alt_id), None)


Chooser = Callable[[DecisionPoint], "str | None"]  # a point -> the chosen alternative's key (None: Python's default)
Decide = Callable[[list[DecisionPoint]], "dict[str, str]"]  # points -> {point id: alternative id}


class RevisionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # S1, S2, ... in the order the targets came
    target: RevisionTarget  # Python's, from the VerificationReport
    action: RevisionAction
    kind: StepKind
    outcome: StepOutcome
    affected_dates: list[dt.date] = Field(default_factory=list)  # the dates this step rebuilt
    moved_to: dt.date | None = None
    detail: str = ""
    evidence: EvidenceRequest | None = None
    superseded_by: str | None = None


class RevisionPlan(BaseModel):
    """One round: every target's step, and the assignment before and after (place ids per date, in order)."""
    model_config = ConfigDict(extra="forbid")

    attempt: int
    steps: list[RevisionStep]
    before: dict[str, list[str]]
    after: dict[str, list[str]]
    unscheduled_after: dict[str, str] = Field(default_factory=dict)  # selected place id -> why it is not planned
    affected_dates: list[dt.date] = Field(default_factory=list)
    preserved_dates: list[dt.date] = Field(default_factory=list)  # copied from the itinerary verbatim

    @property
    def plan_changes(self) -> list[RevisionStep]:
        return [s for s in self.steps if s.kind == "plan_change" and s.outcome == "applied"]


class RevisionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: RevisionPlan
    accepted: bool
    reason: str
    errors_before: int
    errors_after: int | None = None
    verification: VerificationReport | None = None
    decision_points: list[DecisionPoint] = Field(default_factory=list)  # the choices this round offered
    choices: dict[str, str] = Field(default_factory=dict)  # point id -> the alternative id applied
    decided_by: Literal["python", "agent"] = "python"
    comparison: str = ""  # when an agent chose: how its round compared with Python's default round


class RevisionResult(BaseModel):
    """What the Coordinator receives. status and verified are the final VerificationReport's, never set here."""
    model_config = ConfigDict(extra="forbid")

    outcome: RevisionOutcome
    status: CheckStatus
    verified: bool
    itinerary: Itinerary | None  # the final one: the revised itinerary, or the original when nothing was kept
    planner_report: PlannerReport | None = None  # build_itinerary() of the final assignment (None: unchanged)
    original: VerificationReport
    final: VerificationReport
    attempts: list[RevisionAttempt] = Field(default_factory=list)
    changed_dates: list[dt.date] = Field(default_factory=list)
    preserved_dates: list[dt.date] = Field(default_factory=list)
    resolved: list[str] = Field(default_factory=list)  # "code place date" of errors fixed
    remaining_errors: list[str] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)  # from the final report's unknowns
    traveller_decisions: list[str] = Field(default_factory=list)
    escalations: list[str] = Field(default_factory=list)
    travel_lookups: list[dict] = Field(default_factory=list)  # travel_time calls the revision made
    decided_by: Literal["python", "agent", "python_fallback"] = "python"  # who chose among feasible options
    decision_error: str | None = None  # python_fallback: why the agent's choice was not used

    @model_validator(mode="after")
    def _python_owns_the_verdict(self) -> RevisionResult:
        if (self.status, self.verified) != (self.final.status, self.final.verified):
            raise ValueError("status and verified are the final VerificationReport's")
        return self


def _err_key(i) -> str:
    return " ".join(str(x) for x in (i.code, i.place_id or "-", i.date or "-"))


# ------------------------------------------------------------------ the working assignment
class _State:
    """The assignment being revised: place ids per trip date in visiting order, foreign activities (not plannable)
    per date, the selected places left out with why, and the dates touched."""

    def __init__(self, ctx: PlanningContext, bundle: ResearchBundle, itinerary: Itinerary,
                 planner_report: PlannerReport | None, chooser: Chooser | None = None):
        self.ctx, self.itinerary, self.chooser = ctx, itinerary, chooser
        self.points: list[DecisionPoint] = []
        self.applied: dict[str, str] = {}  # point id -> the alternative id applied
        by_name: dict[str, list[str]] = {}
        for p in bundle.places:
            by_name.setdefault(fold(p.name), []).append(p.place_id)
        self.days: dict[dt.date, list[str]] = {d: [] for d in ctx.dates}
        self.foreign: dict[dt.date, list[str]] = {}  # place ids (or "name:...") the Planner cannot schedule
        for day in itinerary.days:
            acts = sorted(day.activities, key=lambda a: (a.start_time is None, a.start_time or ""))
            for a in acts:
                pid = a.place_id or (by_name.get(fold(a.name), [None])[0] if len(by_name.get(fold(a.name), [])) == 1
                                     else None)
                if pid in ctx.stops:
                    self.days.setdefault(day.date, []).append(pid)
                else:
                    self.foreign.setdefault(day.date, []).append(pid or f"name:{fold(a.name)}")
        planned = {i for ids in self.days.values() for i in ids}
        given = {u.place_id: u.reason for u in (planner_report.unscheduled if planner_report else [])}
        self.unscheduled: dict[str, str] = {i: given.get(i, "not in the itinerary") for i in ctx.stops
                                            if i not in planned}
        self.affected: set[dt.date] = set()

    def snapshot(self) -> dict[str, list[str]]:
        return {str(d): list(ids) for d, ids in sorted(self.days.items())}

    def where(self, pid: str) -> list[dt.date]:
        return [d for d, ids in self.days.items() if pid in ids]

    def choose(self, kind: str, subject: str, place_id: str | None, date: dt.date | None,
               options: list[tuple[str, dict]]) -> int:
        """Which of `options` (key, facts; Python's best first) to apply. With fewer than two there is nothing to
        decide; otherwise the point is recorded and the chooser may pick another option by its key."""
        if len(options) < 2:
            return 0
        pid = f"D{len(self.points) + 1}"
        alts = [Alternative(id=f"{pid}-A{n}", key=k, **facts) for n, (k, facts) in enumerate(options, 1)]
        hours = (hours_on(self.ctx.stops[place_id].hours, date).status
                 if place_id in self.ctx.stops and date is not None else None)
        point = DecisionPoint(id=pid, signature=f"{kind}:{subject}", kind=kind, place_id=place_id, date=date,
                              hours_on_date=hours, alternatives=alts, default=alts[0].id)
        self.points.append(point)
        key = self.chooser(point) if self.chooser else None
        index = next((n for n, a in enumerate(alts) if a.key == key), 0)
        self.applied[pid] = alts[index].id
        return index

    def priority(self, pid: str) -> int:
        """Lower = more important: the Budget Analyst's order (required first, then includes by rank)."""
        return list(self.ctx.stops).index(pid)

    def assignment(self) -> DayAssignment:
        placed = {i for d in self.ctx.dates for i in self.days.get(d, [])}
        unscheduled = [UnscheduledChoice(place_id=i, reason=(self.unscheduled.get(i) or "not planned")[:300])
                       for i in self.ctx.stops if i not in placed]
        return DayAssignment(days=[DayChoice(date=d, place_ids=list(self.days.get(d, []))) for d in self.ctx.dates],
                             unscheduled=unscheduled)


# ------------------------------------------------------------------ bounded searches on the scheduler
def _schedule(ctx: PlanningContext, day: dt.date, ids: list[str]) -> DaySchedule:
    return schedule_day(day, [ctx.stops[i] for i in ids], ctx.rules, ctx.travel)


def _score(s: DaySchedule, limit: int | None) -> tuple[int, int, int]:
    """Lower is better: walks over the limit, unmeasured legs (counted at the buffer, never as zero), travel."""
    legs = [x for x in s.scheduled if x.travel is not None]
    over = sum(bool(limit) and x.travel.status == "measured" and x.travel.minutes > limit for x in legs)
    return over, sum(x.buffer_used for x in legs), sum(x.gap_minutes for x in legs)


def _orders(ids: list[str], focus: str | None = None) -> list[list[str]]:
    """Other orders of the same places: every permutation of a small day, else the focus place at each position."""
    if len(ids) <= MAX_REORDER_STOPS:
        return [list(p) for p in itertools.permutations(ids) if list(p) != ids]
    focus = focus if focus in ids else ids[-1]
    rest = [i for i in ids if i != focus]
    return [rest[:k] + [focus] + rest[k:] for k in range(len(rest) + 1) if rest[:k] + [focus] + rest[k:] != ids]


def _best_order(ctx: PlanningContext, day: dt.date, ids: list[str], focus: str | None = None, *,
                keep: bool = True) -> list[str] | None:
    """With keep, the given order whenever Python schedules it in full (the plan is changed as little as possible);
    otherwise, or without keep, the best order Python schedules in full. None: no order fits."""
    limit = ctx.rules.max_walk_minutes
    if keep and not _schedule(ctx, day, list(ids)).unscheduled:
        return list(ids)
    best = None
    for order in [list(ids), *_orders(ids, focus)]:
        s = _schedule(ctx, day, order)
        if not s.unscheduled and (best is None or _score(s, limit) < best[0]):
            best = (_score(s, limit), order)
    return best[1] if best else None


def _facts(st: _State, day: dt.date, order: list[str], s: DaySchedule, summary: str, *, placed: str | None = None,
           gives_way: str | None = None) -> dict:
    """What an option means, from Python's schedule of it: travel, unknown legs, walks, disruption, times."""
    limit = st.ctx.rules.max_walk_minutes
    legs = [x for x in s.scheduled if x.travel is not None]
    before = [i for i in st.days.get(day, []) if i in order]
    after = [i for i in order if i in before]
    listed = hours_on(st.ctx.stops[placed].hours, day) if placed else None
    return dict(summary=summary, date=day, order=list(order), gives_way=gives_way,
                hours=listed.status if listed else None, hours_windows=(listed.windows_text or None) if listed else None,
                measured_travel_minutes=sum(x.travel.minutes for x in legs if x.travel.status == "measured"),
                unmeasured_legs=sum(x.buffer_used for x in legs),
                walks_over_limit=sum(bool(limit) and x.travel.status == "measured" and x.travel.minutes > limit
                                     for x in legs),
                places_on_date=len(order), reordered_places=sum(a != b for a, b in zip(before, after)),
                schedule=[{"place_id": x.place_id, "start": x.start, "end": x.end} for x in s.scheduled])


def _insertions(st: _State, pid: str, exclude: set[dt.date]) -> list[tuple[tuple, dt.date, list[str], DaySchedule]]:
    """Every (date, position) Python can schedule `pid` at with the date's other places still fitting, best first."""
    ctx, out = st.ctx, []
    limit = ctx.rules.max_walk_minutes
    for d in ctx.dates:
        if d in exclude or hours_on(ctx.stops[pid].hours, d).status == "closed" or st.foreign.get(d):
            continue
        ids = st.days.get(d, [])
        for k in range(len(ids) + 1):
            cand = ids[:k] + [pid] + ids[k:]
            s = _schedule(ctx, d, cand)
            if not s.unscheduled:
                out.append(((*_score(s, limit), len(ids), ctx.dates.index(d), k), d, cand, s))
    return sorted(out, key=lambda x: x[0])


def _place(st: _State, pid: str, exclude: set[dt.date], *, make_room: bool) -> tuple[dt.date, str] | None:
    """Put `pid` on its best date; with make_room, one less important non-required place may give way (and is
    itself re-placed if possible, else left out with the reason). Returns (date, what happened) or None."""
    name = st.ctx.stops[pid].name
    found = _insertions(st, pid, exclude)
    if found:
        best_per_date = [f for n, f in enumerate(found) if f[1] not in {x[1] for x in found[:n]}][:MAX_ALTERNATIVES]
        options = [(str(d), _facts(st, d, ids, s, f"{name} on {d} ({d:%a})", placed=pid))
                   for _, d, ids, s in best_per_date]
        _, d, ids, _ = best_per_date[st.choose("placement", pid, pid, min(exclude) if exclude else None, options)]
        st.days[d] = ids
        st.affected.add(d)
        st.unscheduled.pop(pid, None)
        return d, f"placed on {d}"
    if not make_room:
        return None
    # Making room: each (date, less important non-required place) that frees a slot is one option.
    found_room = []
    for d in st.ctx.dates:
        if d in exclude or hours_on(st.ctx.stops[pid].hours, d).status == "closed" or st.foreign.get(d):
            continue
        evictable = sorted((i for i in st.days.get(d, []) if not st.ctx.stops[i].required), key=st.priority,
                           reverse=True)
        for victim in evictable:
            saved = list(st.days[d])
            st.days[d] = [i for i in saved if i != victim]
            fits = _insertions(st, pid, exclude | (set(st.ctx.dates) - {d}))
            st.days[d] = saved
            if fits:
                found_room.append((d, victim, fits[0][2], fits[0][3]))
            if len(found_room) >= MAX_ALTERNATIVES:
                break
        if len(found_room) >= MAX_ALTERNATIVES:
            break
    if not found_room:
        return None
    options = [(f"{d}|{victim}", _facts(st, d, ids, s, f"{name} on {d} ({d:%a}); {st.ctx.stops[victim].name} gives way",
                                        placed=pid, gives_way=victim))
               for d, victim, ids, s in found_room]
    d, victim, ids, _ = found_room[st.choose("make_room", pid, pid, min(exclude) if exclude else None, options)]
    st.days[d] = ids
    st.affected.add(d)
    st.unscheduled.pop(pid, None)
    again = _place(st, victim, exclude | {d}, make_room=False)
    if again is None:
        st.unscheduled[victim] = f"left out to make room for {name} on {d}"
        return d, f"placed on {d}; {st.ctx.stops[victim].name} left out to make room"
    return d, f"placed on {d}; {st.ctx.stops[victim].name} moved to {again[0]} to make room"


def _settle(st: _State, day: dt.date) -> str | None:
    """Make Python schedule every place of a touched date: the order as is, else a better order, else the least
    important non-required places go to another date or are left out. None = settled; else why not."""
    ids = st.days.get(day, [])
    if not ids:
        return None
    order = _best_order(st.ctx, day, ids)
    if order is not None:
        st.days[day] = order
        return None
    for victim in sorted((i for i in ids if not st.ctx.stops[i].required), key=st.priority, reverse=True):
        st.days[day] = [i for i in st.days[day] if i != victim]
        order = _best_order(st.ctx, day, st.days[day])
        if _place(st, victim, {day}, make_room=False) is None:
            st.unscheduled[victim] = f"does not fit on {day} after the revision"
        if order is not None:
            st.days[day] = order
            return None
    return f"the required places of {day} do not fit together on that day"


# ------------------------------------------------------------------ one step per revision target
def _names(ctx: PlanningContext, bundle: ResearchBundle, pid: str | None) -> str | None:
    if pid is None:
        return None
    if pid in ctx.stops:
        return ctx.stops[pid].name
    return next((p.name for p in bundle.places if p.place_id == pid), None)


def _evidence(t: RevisionTarget, ctx: PlanningContext, bundle: ResearchBundle) -> tuple[EvidenceRequest | None, str]:
    """The MCP call that would supply the missing fact. No value is filled in."""
    city = bundle.trip.destination
    name = _names(ctx, bundle, t.place_id)
    base = dict(codes=list(t.codes), place_id=t.place_id, date=t.date)
    if t.action == "lookup_travel_time":
        origin = _names(ctx, bundle, t.related_place_id)
        if name and origin:
            return EvidenceRequest(tool="travel_time", arguments={"from_place": origin, "to_place": name,
                                                                  "city": city, "mode": "foot"},
                                   reason="no measured travel time for this leg", **base), "travel_time request"
        return None, "the leg's places are unknown: nothing to look up"
    if t.action in ("lookup_opening_hours", "lookup_price", "add_source") and name:
        why = {"lookup_opening_hours": "opening hours unknown or unconfirmed for the visit date",
               "lookup_price": "no price in the listing (get_opening_hours returns the listed price)",
               "add_source": "no source link or citation for this place"}[t.action]
        return EvidenceRequest(tool="get_opening_hours", arguments={"place_name": name, "city": city}, reason=why,
                               **base), "get_opening_hours request"
    if t.action == "lookup_price":
        return None, "the total depends on the unknown prices above: re-verify after those lookups"
    return None, "no single place to look up"


class _Planner:
    """Turns one VerificationReport's targets into steps and applies the plan changes to a _State."""

    def __init__(self, st: _State, report: BudgetReport, bundle: ResearchBundle):
        self.st, self.report, self.bundle = st, report, bundle
        self.steps: list[RevisionStep] = []
        self.reprice: set[str] = set()
        self.day_done: dict[tuple[str, dt.date], str] = {}  # (reorder|reschedule, date) -> the step that did it
        self.reduce = False
        self.rebuild_all = False

    def step(self, n: int, t: RevisionTarget, outcome: StepOutcome, detail: str, **kw) -> RevisionStep:
        s = RevisionStep(id=f"S{n}", target=t, action=t.action, kind=_KIND.get(t.action, "escalation"),
                         outcome=outcome, detail=detail, **kw)
        self.steps.append(s)
        return s

    def run(self, targets: list[RevisionTarget]) -> None:
        indexed = list(enumerate(targets, 1))
        indexed.sort(key=lambda x: (_ORDER.index(x[1].action) if x[1].action in _ORDER else len(_ORDER), x[0]))
        moved_by: dict[str, str] = {}  # place id -> the step that moved or removed it
        ctx, st = self.st.ctx, self.st
        for n, t in indexed:
            pid, day = t.place_id, t.date
            kind = _KIND.get(t.action, "escalation")
            if kind == "evidence_request":
                req, detail = _evidence(t, ctx, self.bundle)
                self.step(n, t, "pending_evidence", detail, evidence=req)
                continue
            if kind == "traveller_decision":
                self.step(n, t, "awaiting_traveller", "no plan change can settle it: the traveller decides")
                continue
            if kind == "escalation":
                self.step(n, t, "escalated", "not a targeted fix: the Planner must rebuild the plan")
                continue
            if pid and pid in moved_by and t.action in ("reschedule_activity", "reorder_day", "move_activity"):
                self.step(n, t, "superseded", f"{pid} was already moved or removed by {moved_by[pid]}",
                          superseded_by=moved_by[pid])
                continue
            if (t.action, day) in self.day_done:  # one reorder or reschedule handles the whole date
                done = self.day_done[(t.action, day)]
                self.step(n, t, "superseded", f"{day} was already handled as a whole by {done}", superseded_by=done)
                continue
            getattr(self, t.action)(n, t, moved_by)
        self.steps.sort(key=lambda s: int(s.id[1:]))

    # -------------------------------------------------- plan changes
    def remove_activity(self, n, t, moved_by):
        st, pid, day = self.st, t.place_id, t.date
        if pid is None or day is None:
            self.step(n, t, "impossible", "the target names no place and date")
            return
        if pid in st.foreign.get(day, []):
            st.foreign[day].remove(pid)
        elif pid in st.days.get(day, []):
            ids = st.days[day]
            del ids[len(ids) - 1 - ids[::-1].index(pid)]  # the repeat on that date; any earlier one stays
        else:
            self.step(n, t, "superseded", f"{pid} is no longer on {day}")
            return
        st.affected.add(day)
        if "duplicate_place" not in t.codes:
            moved_by[pid] = f"S{n}"
        self.step(n, t, "applied", f"removed {pid} from {day}", affected_dates=[day])

    def move_activity(self, n, t, moved_by):
        st, ctx = self.st, self.st.ctx
        if t.place_id is None and t.date is not None:  # date_outside_trip: every place of that date
            pids, day = list(st.days.get(t.date, [])), t.date
        else:
            pids, day = [t.place_id], t.date
        if day is None or not pids or any(p not in st.days.get(day, []) for p in pids):
            self.step(n, t, "impossible", "nothing of the target is planned on that date (or it is not plannable)")
            return
        moved, notes = None, []
        for pid in pids:
            st.days[day] = [i for i in st.days[day] if i != pid]
            st.affected.add(day)
            required = ctx.stops[pid].required
            placed = _place(st, pid, {day}, make_room=required)
            if placed:
                moved = placed[0]
                notes.append(f"{pid}: {placed[1]}")
            elif not required:
                st.unscheduled[pid] = f"no trip date other than {day} can take it"
                notes.append(f"{pid}: left out (no other trip date can take it)")
            else:
                st.days[day].append(pid)  # never drop a required place: the error stays, the traveller decides
                self.step(n, t, "impossible", f"{pid} is required and no other trip date can take it")
                return
            moved_by[pid] = f"S{n}"
        dates = sorted({day} | ({moved} if moved else set()))
        self.step(n, t, "applied", "; ".join(notes), affected_dates=dates, moved_to=moved)

    def add_required_place(self, n, t, moved_by, *, required=True):
        st, pid = self.st, t.place_id
        if pid is None or pid not in st.ctx.stops:
            self.step(n, t, "impossible", f"{pid} is not a selected place the Planner can schedule")
            return
        if st.where(pid):
            self.step(n, t, "superseded", f"{pid} is already planned on {st.where(pid)[0]}")
            return
        placed = _place(st, pid, set(), make_room=required)
        if placed is None:
            self.step(n, t, "impossible", f"no trip date can take {pid}" + ("" if required else "; it stays out"))
            return
        self.step(n, t, "applied", placed[1], affected_dates=[placed[0]], moved_to=placed[0])

    def restore_selected_place(self, n, t, moved_by):
        self.add_required_place(n, t, moved_by, required=False)

    def reschedule_activity(self, n, t, moved_by):
        st, day = self.st, t.date
        if day is None or day not in st.days:
            self.step(n, t, "impossible", "the target date is not a trip date")
            return
        if st.foreign.get(day):
            self.step(n, t, "impossible", f"{day} holds places the Planner cannot schedule; remove them first")
            return
        st.affected.add(day)
        self.day_done[("reschedule_activity", day)] = f"S{n}"
        self.step(n, t, "applied", f"{day} is rescheduled by Python's scheduler", affected_dates=[day])

    def reorder_day(self, n, t, moved_by):
        st, day = self.st, t.date
        ids = st.days.get(day or dt.date.min, [])
        if not ids:
            self.step(n, t, "impossible", "nothing planned on the target date")
            return
        limit = st.ctx.rules.max_walk_minutes
        now = _score(_schedule(st.ctx, day, ids), limit)
        better = []
        for order in _orders(ids, t.place_id):
            s = _schedule(st.ctx, day, order)
            if not s.unscheduled and _score(s, limit)[0] < now[0]:
                better.append((_score(s, limit), order, s))
        better = sorted(better, key=lambda x: x[0])[:MAX_ALTERNATIVES]
        if not better:
            self.step(n, t, "impossible", f"no order of {day}'s places Python can schedule removes the walk over "
                                          "the limit")
            return
        options = [(",".join(order), _facts(st, day, order, s, f"{day} in the order "
                                            + ", ".join(st.ctx.stops[i].name for i in order)))
                   for _, order, s in better]
        order = better[st.choose("reorder", str(day), t.place_id, day, options)][1]
        st.days[day] = order
        st.affected.add(day)
        self.day_done[("reorder_day", day)] = f"S{n}"
        self.step(n, t, "applied", f"reordered {day}: {', '.join(order)}", affected_dates=[day])

    def align_with_request(self, n, t, moved_by):
        trip, r = self.bundle.trip, self.report
        if (r.currency, r.travellers) != (trip.currency, trip.travellers) or \
                (r.budget_total is None) != (trip.budget_total is None) or \
                (r.budget_total is not None and float(r.budget_total) != float(trip.budget_total)):
            self.step(n, t, "impossible", "the Budget Analyst's report itself differs from the request: it must be "
                                          "priced again")
            return
        self.rebuild_all = True
        self.step(n, t, "applied", "every date rebuilt from the Budget Analyst's report (currency, travellers, "
                                   "budget of the request)", affected_dates=list(self.st.ctx.dates))

    def reprice_activity(self, n, t, moved_by):
        self.reprice.add(t.place_id)
        self.step(n, t, "applied", f"{t.place_id}: cost taken from Python's pricing of the listing")

    def recalculate_budget(self, n, t, moved_by):
        self.step(n, t, "applied", "totals are recomputed by summarize() from the revised itinerary")

    def reduce_cost(self, n, t, moved_by):
        self.reduce = True
        self.step(n, t, "applied", "least important paid places are left out until the plan fits the budget")


# ------------------------------------------------------------------ one round: plan, rebuild, compose
def _compose(st: _State, built: PlannerReport, original: Itinerary, reprice: set[str],
             bundle: ResearchBundle) -> Itinerary | None:
    """The revised itinerary: rebuilt days for touched dates, every other date copied verbatim (costs corrected in
    place where a reprice step says so)."""
    cands = prepare_candidates(bundle)
    new_days = {d.date: d for d in (built.itinerary.days if built.itinerary else [])}
    old_days = {d.date: d for d in original.days}
    days = []
    for d in sorted(set(st.ctx.dates) | {x for x in old_days if x not in st.affected}):
        if d in st.affected or d not in old_days:
            day = new_days.get(d) or DayPlan(date=d)
        else:
            acts = []
            for a in old_days[d].activities:
                cand = cands.get(a.place_id) if a.place_id in reprice else None
                acts.append(apply_to_activity(a, cand.cost) if cand else a)
            day = DayPlan(date=d, activities=acts)
        days.append(day)
    if not any(d.activities for d in days):
        return None
    r = st.ctx.report
    return Itinerary(destination=r.destination, currency=r.currency, travellers=r.travellers,
                     budget_total=None if r.budget_total is None else float(r.budget_total), days=days)


def _reduce(st: _State, budget_total) -> list[str]:
    """Leave out the least important non-required paid places until the known costs fit. Returns the ids."""
    out = []
    if budget_total is None:
        return out
    costs = {i.place_id: i.cost.estimated for i in st.ctx.report.selected}  # Python's; None (unknown) counts 0

    def total():
        return sum((costs.get(i) or 0) for ids in st.days.values() for i in ids)
    placed = [(d, i) for d, ids in st.days.items() for i in ids]
    for d, pid in sorted(placed, key=lambda x: st.priority(x[1]), reverse=True):
        if total() <= budget_total:
            break
        if st.ctx.stops[pid].required or not costs.get(pid):
            continue
        st.days[d] = [i for i in st.days[d] if i != pid]
        st.affected.add(d)
        st.unscheduled[pid] = "left out to fit the budget"
        out.append(pid)
    return out


def _round(attempt: int, itinerary: Itinerary, report: VerificationReport, ctx: PlanningContext,
           bundle: ResearchBundle, planner_report: PlannerReport | None, chooser: Chooser | None = None):
    """One round. Returns (plan, built PlannerReport or None, revised itinerary or None, why not, state)."""
    st = _State(ctx, bundle, itinerary, planner_report, chooser)
    before = st.snapshot()
    p = _Planner(st, ctx.report, bundle)
    p.run(list(report.revision_targets))
    if p.rebuild_all:
        st.affected |= set(ctx.dates)
    for day in sorted(st.affected):
        if day in ctx.dates and (why := _settle(st, day)):
            for s in p.steps:
                if day in s.affected_dates and s.outcome == "applied":
                    s.outcome, s.detail = "impossible", f"{s.detail}; but {why}"
    if p.reduce:
        dropped = _reduce(st, None if ctx.report.budget_total is None else ctx.report.budget_total)
        for s in p.steps:
            if s.action == "reduce_cost":
                s.affected_dates = sorted(d for d in st.affected)
                s.detail = (f"left out to fit the budget: {', '.join(dropped)}" if dropped else
                            "no non-required paid place left to leave out: the traveller decides")
                s.outcome = "applied" if dropped else "impossible"
    affected = sorted(d for d in st.affected if d in ctx.dates or d in {x.date for x in itinerary.days})
    plan = RevisionPlan(attempt=attempt, steps=p.steps, before=before, after=st.snapshot(),
                        unscheduled_after=dict(st.unscheduled), affected_dates=affected,
                        preserved_dates=[d.date for d in itinerary.days if d.date not in st.affected])
    if not plan.plan_changes:
        return plan, None, None, "no plan change could be applied", st
    if any(st.foreign.get(d) for d in st.affected):
        return plan, None, None, "a touched date still holds places the Planner cannot schedule", st
    try:
        built = build_itinerary(ctx, st.assignment())  # the Planner's own validation and scheduling
    except InvalidAssignment as exc:
        return plan, None, None, f"the Planner's validation rejects the revised assignment: {exc}", st
    revised = _compose(st, built, itinerary, p.reprice, bundle)
    if revised is None:
        return plan, built, None, "the revision leaves nothing planned", st
    return plan, built, revised, "", st


def _required_planned(it: Itinerary, ctx: PlanningContext) -> set[str]:
    return {a.place_id for d in it.days for a in d.activities if a.place_id in ctx.stops and ctx.stops[a.place_id].required}


class _Trial:
    """One round, rebuilt and re-verified, with the verdict on keeping it."""

    def __init__(self, attempt: RevisionAttempt, built: PlannerReport | None, revised: Itinerary | None,
                 points: list[DecisionPoint]):
        self.attempt, self.built, self.revised, self.points = attempt, built, revised, points


def _trial(n: int, current: Itinerary, current_report: VerificationReport, ctx: PlanningContext,
           bundle: ResearchBundle, base: PlannerReport | None, check: dict, chooser: Chooser | None = None) -> _Trial:
    """Plan, rebuild and re-verify one round, and judge it. A round is kept only if it leaves strictly fewer
    confirmed errors, adds no new KIND of confirmed error, and loses no required place that was planned."""
    plan, built, revised, why, st = _round(n, current, current_report, ctx, bundle, base, chooser)
    errors_before = len(current_report.issues)
    common = dict(plan=plan, errors_before=errors_before, decision_points=st.points, choices=dict(st.applied))
    if revised is None:
        return _Trial(RevisionAttempt(accepted=False, reason=why, **common), None, None, st.points)
    checked = verify_itinerary(revised, bundle, planner_report=built, **check)
    lost = _required_planned(current, ctx) - _required_planned(revised, ctx)
    new_kinds = sorted({i.code for i in checked.issues} - {i.code for i in current_report.issues})
    if lost:
        accepted, reason = False, f"a required place would be lost: {', '.join(sorted(lost))}"
    elif new_kinds:
        accepted, reason = False, f"it introduces a new kind of confirmed error: {', '.join(new_kinds)}"
    elif len(checked.issues) >= errors_before:
        accepted, reason = False, f"not fewer confirmed errors ({errors_before} -> {len(checked.issues)})"
    else:
        accepted, reason = True, "fewer confirmed errors"
    attempt = RevisionAttempt(accepted=accepted, reason=reason, errors_after=len(checked.issues),
                              verification=checked, **common)
    return _Trial(attempt, built, revised, st.points)


_RANK = {"PASS": 0, "UNKNOWN": 1, "FAIL": 2}


def _compare(theirs: _Trial, ours: _Trial) -> tuple[bool, str]:
    """Keep the agent's round only if Python keeps it on its own terms and it is no worse than Python's default:
    no worse verdict (PASS < UNKNOWN < FAIL), no more confirmed errors, no error kind Python's round does not have.
    Among options Python finds equally good the agent's judgement (balance, disruption, interests) decides."""
    a, p = theirs.attempt, ours.attempt
    if not a.accepted:
        return False, f"the agent's choice was not kept: {a.reason}"
    if p.accepted:
        mine, yours = p.verification, a.verification
        extra = sorted({i.code for i in yours.issues} - {i.code for i in mine.issues})
        if len(yours.issues) > len(mine.issues) or extra:
            return False, (f"the agent's choice was not kept: Python's own choice leaves fewer confirmed errors "
                           f"({len(mine.issues)} against {len(yours.issues)})")
        if _RANK[yours.status] > _RANK[mine.status]:
            return False, (f"the agent's choice was not kept: it would leave the plan {yours.status} where Python's "
                           f"own choice leaves it {mine.status}")
    return True, (f"the agent's choice is kept: {a.errors_after} confirmed error(s), as good as Python's own "
                  "choice" if p.accepted else "the agent's choice is kept: Python's own was not")


# ------------------------------------------------------------------ the entry point
def revise_itinerary(itinerary: Itinerary, bundle: ResearchBundle, budget_report: BudgetReport, *,
                     verification: VerificationReport | None = None, planner_report: PlannerReport | None = None,
                     travel_lookup: TravelLookup | None = None, hours_lookup: HoursLookup | None = None,
                     rules: ScheduleRules | None = None, max_attempts: int = MAX_ATTEMPTS,
                     max_lookups: int = DEFAULT_MAX_LOOKUPS, decide: Decide | None = None) -> RevisionResult:
    """Fix the Verifier's plan-change targets in bounded rounds; report evidence and decisions it cannot supply.

    `verification` is the report to act on (computed with the same arguments when None). `travel_lookup` and
    `hours_lookup` are the injected MCP wrappers, used by the scheduler (travel) and by every re-verification.
    `decide` (optional; the LLM layer in revision_agent.py) picks among the first round's feasible alternatives;
    its round is kept only if it is at least as good as Python's own (see _compare). Without it, or when nothing
    has two feasible options, Python chooses and no call is made.
    """
    rules = rules or ScheduleRules.from_trip(bundle.trip)
    check = dict(budget_report=budget_report, hours_lookup=hours_lookup, travel_lookup=travel_lookup, rules=rules)
    original = verification or verify_itinerary(itinerary, bundle, planner_report=planner_report, **check)
    ctx = PlanningContext(budget_report, bundle, travel_lookup, rules=rules, max_lookups=max_lookups)
    current, current_report, current_built, attempts = itinerary, original, None, []
    decided_by, decision_error = "python", None
    has_changes = any(_KIND.get(t.action) == "plan_change" for t in original.revision_targets)
    for n in range(1, max_attempts + 1 if has_changes else 1):
        if not current_report.issues and not any(_KIND.get(t.action) == "plan_change"
                                                 for t in current_report.revision_targets):
            break
        base = current_built or (planner_report if current is itinerary else None)
        trial = _trial(n, current, current_report, ctx, bundle, base, check)
        if n == 1 and decide is not None and trial.points:  # only the first round asks: a bounded number of calls
            try:
                picks = decide(trial.points)
            except Exception as exc:  # the agent gave no valid choice: Python's own choice stands, said out loud
                decided_by, decision_error = "python_fallback", f"no valid choice from the agent: {exc}"
            else:
                keys = {p.signature: p.by_id(picks[p.id]).key for p in trial.points
                        if p.id in picks and p.by_id(picks[p.id]) is not None}
                theirs = _trial(n, current, current_report, ctx, bundle, base, check,
                                chooser=lambda point: keys.get(point.signature))
                kept, comparison = _compare(theirs, trial)
                if kept:
                    trial, decided_by = theirs, "agent"
                    trial.attempt.decided_by = "agent"
                else:
                    decided_by, decision_error = "python_fallback", comparison
                trial.attempt.comparison = comparison
        attempts.append(trial.attempt)
        if not trial.attempt.accepted:
            break
        current, current_report, current_built = trial.revised, trial.attempt.verification, trial.built
    final = current_report
    changed = sorted({d for a in attempts if a.accepted for d in a.plan.affected_dates})
    if not has_changes:
        outcome = "no_change_needed"
    elif current is itinerary:
        outcome = "unchanged"
    else:
        outcome = "revised" if not final.issues else "partially_revised"
    before_errors = {_err_key(i) for i in original.issues}
    after_errors = {_err_key(i) for i in final.issues}
    requests = []
    for t in final.revision_targets:
        if t.kind == "evidence_lookup":
            req, _ = _evidence(t, ctx, bundle)
            if req is not None:
                requests.append(req)
    impossible = [f"{s.target.action} {s.target.place_id or ''} {s.target.date or ''}: {s.detail}".strip()
                  for a in attempts[-1:] for s in a.plan.steps if s.outcome == "impossible"]
    decisions = [i.explanation for i in final.issues + final.unknowns if i.recommended_action == "ask_traveller"]
    return RevisionResult(
        outcome=outcome, status=final.status, verified=final.verified, itinerary=current,
        planner_report=current_built, original=original, final=final, attempts=attempts, changed_dates=changed,
        preserved_dates=[d.date for d in itinerary.days if d.date not in changed],
        resolved=sorted(before_errors - after_errors), remaining_errors=sorted(after_errors),
        evidence_requests=requests, traveller_decisions=decisions + impossible,
        escalations=[t.action + (f" ({', '.join(t.codes)})") for t in final.revision_targets
                     if t.action == "rebuild_itinerary"],
        travel_lookups=list(ctx.travel.calls), decided_by=decided_by, decision_error=decision_error)
