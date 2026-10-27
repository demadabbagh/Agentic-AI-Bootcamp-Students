"""Deterministic scheduling for the Itinerary Planner: opening hours, visit durations and travel, one day at a time.

No LLM and no network. Travel times come from an injected TravelLookup (the Coordinator wraps the real
travel_time MCP tool; tests replay recorded results). Every number is computed or looked up, and every
assumption is labelled:

  hours     "open"         the listing gives times for this weekday (or says 24 hours)
            "unconfirmed"  times are listed, but not for certain on this day: no days stated, seasonal hours,
                           overlapping times, or only a closing time. Scheduled inside those times.
            "unknown"      no usable hours (none listed, or text the parser could not read). Scheduled
                           tentatively; never called open.
            "closed"       the listing says closed that day (or temporarily closed). Never scheduled.
  duration  a planning estimate by listing kind: no tool gives visit lengths
  travel    "measured"     a travel_time result
            "unavailable"  no route (not cached, tool error): a planning buffer spaces the schedule, but the
                           travel time stays unknown and a walking limit counts as unverified
            "not_looked_up" no lookup source, or the lookup budget is spent: same as unavailable

Completeness (was every stop scheduled?) and uncertainty (unknown hours or travel) are reported separately.
Nothing here books, saves or verifies a plan: verification against fresh tool results is the Verifier's job.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import time
from typing import Callable, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import WEEKDAYS, Time
from .research import DEFAULT_DAY_END, DEFAULT_DAY_START, ResearchedPlace, TripRequest

HoursStatus = Literal["open", "unconfirmed", "unknown", "closed"]
TravelStatus = Literal["measured", "unavailable", "not_looked_up"]
StopStatus = Literal["scheduled", "closed", "does_not_fit"]
IssueCode = Literal["closed_on_date", "does_not_fit", "duplicate_place", "date_outside_trip", "required_not_scheduled",
                    "hours_unknown", "hours_unconfirmed", "travel_unknown", "walk_over_limit", "walk_unverified",
                    "outside_meal_time"]
ERRORS = {"closed_on_date", "does_not_fit", "duplicate_place", "date_outside_trip", "required_not_scheduled"}

# Planning estimates (minutes) by Wikivoyage listing kind; no tool gives visit durations.
DEFAULT_DURATIONS = {"see": 90, "do": 120, "buy": 90, "eat": 60, "drink": 45}
CATEGORY_DURATIONS = {"attraction": 90, "restaurant": 60}
DEFAULT_TRAVEL_BUFFER = 30  # minutes between stops when no travel time is available: an assumption, not a fact
MIDNIGHT = 24 * 60
_MONTHS = [m.lower() for m in calendar.month_abbr[1:]]  # jan .. dec


def to_minutes(hhmm: str) -> int:
    """'09:30' -> 570. '24:00' -> 1440 (the end of the day)."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ------------------------------------------------------------------ opening hours on a date
class DayHours(BaseModel):
    """A place's opening hours on one date, from its listing."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: dt.date
    weekday: str
    status: HoursStatus
    windows: list[tuple[int, int]] = Field(default_factory=list)  # minutes from midnight; end may be 1440
    raw: str | None = None  # the listing's hours text: the authority when in doubt
    note: str | None = None

    @property
    def windows_text(self) -> str:
        return ", ".join(f"{to_hhmm(a)}-{to_hhmm(b)}" for a, b in self.windows)


def _ranges(ranges: list[str]) -> tuple[list[tuple[int, int]], list[str], bool]:
    """'09:00-17:00' strings -> windows, notes, and whether the times are uncertain for this day."""
    windows, notes, uncertain = [], [], False
    for r in ranges:
        start, _, end = r.partition("-")
        end_m = to_minutes(end)
        if start == "?":  # "Fri to 20:00": only the closing time is listed
            start_m, uncertain = 0, True
            notes.append(f"only the closing time ({end}) is listed")
        else:
            start_m = to_minutes(start)
        if end_m == 0:
            end_m = MIDNIGHT  # "09:00-00:00" closes at midnight
        elif end_m <= start_m:
            notes.append(f"open past midnight (until {end}); the planning day ends earlier")
            end_m = MIDNIGHT
        windows.append((start_m, end_m))
    windows.sort()
    merged: list[tuple[int, int]] = []
    for a, b in windows:  # overlapping times (often seasonal labels the parser dropped): keep the narrowest
        if merged and a < merged[-1][1]:
            merged[-1] = (max(merged[-1][0], a), min(merged[-1][1], b))
            if "overlapping times" not in " ".join(notes):
                notes.append("the listing gives overlapping times (perhaps seasonal); the narrowest is used")
            uncertain = True
        else:
            merged.append((a, b))
    return merged, notes, uncertain


def _months(text: str) -> set[int] | None:
    """'May-Oct' -> {5..10}; 'Nov-Apr' wraps the year; None when the months cannot be read."""
    found = [w[:3] for w in re.findall(r"[a-z]+", text.lower()) if w[:3] in _MONTHS]
    if len(found) == 1:
        return {_MONTHS.index(found[0]) + 1}
    if len(found) != 2:
        return None
    a, b = _MONTHS.index(found[0]) + 1, _MONTHS.index(found[1]) + 1
    return set(range(a, b + 1)) if a <= b else set(range(a, 13)) | set(range(1, b + 1))


def hours_on(hours: dict | None, day: dt.date) -> DayHours:
    """What the listing says about `day`. `hours` is the listing's parsed hours (travel_data.parse_hours)."""
    wd = WEEKDAYS[day.weekday()]
    base = dict(date=day, weekday=wd, raw=(hours or {}).get("raw"))
    if not hours:
        return DayHours(status="unknown", note="the listing gives no opening hours", **base)
    if hours.get("temporarily_closed"):
        return DayHours(status="closed", note="the listing says it is temporarily closed", **base)
    if hours.get("open_24h"):
        return DayHours(status="open", windows=[(0, MIDNIGHT)], note="listed as open 24 hours", **base)
    if not hours.get("parsed"):
        return DayHours(status="unknown", note="the listed hours could not be read", **base)
    if wd in (hours.get("closed_days") or []):
        return DayHours(status="closed", note=f"listed as closed on {wd}", **base)
    weekly, seasonal = hours.get("weekly") or {}, hours.get("seasonal") or []
    if seasonal and not weekly:
        season = next((s for s in seasonal if day.month in (_months(s.get("months", "")) or set())), None)
        if season is None:
            return DayHours(status="unknown", note=f"the seasonal hours do not cover {day:%B}", **base)
        windows, notes, _ = _ranges(season.get("hours") or [])
        notes.insert(0, f"seasonal hours ({season['months']}), no days stated")
        return DayHours(status="unconfirmed", windows=windows, note="; ".join(notes), **base)
    if not weekly.get(wd):
        return DayHours(status="unknown", note=f"no times listed for {wd}", **base)
    windows, notes, uncertain = _ranges(weekly[wd])
    if not hours.get("days_stated"):
        notes.insert(0, f"times are listed without days; assumed to apply on {wd}")
        uncertain = True
    return DayHours(status="unconfirmed" if uncertain else "open", windows=windows, note="; ".join(notes) or None,
                    **base)


# ------------------------------------------------------------------ stops and rules
class Stop(BaseModel):
    """A place to schedule, with what the scheduler needs from its listing."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    place_id: str
    name: str  # the listing name: also what the travel lookup is asked about
    category: Literal["attraction", "restaurant"]
    kind: str | None = None
    required: bool = False
    hours: dict | None = None
    lat: float | None = None
    lon: float | None = None

    @classmethod
    def from_place(cls, place: ResearchedPlace, *, required: bool = False) -> Stop:
        return cls(place_id=place.place_id, name=place.name, category=place.category, kind=place.kind,
                   required=required, hours=place.hours, lat=place.lat, lon=place.lon)


class ScheduleRules(BaseModel):
    """The planning day and the assumptions. Defaults: 09:00-21:00, the duration table, a 30-minute buffer."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    day_start: Time = DEFAULT_DAY_START
    day_end: Time = DEFAULT_DAY_END
    max_walk_minutes: int | None = Field(default=None, ge=1)
    travel_buffer_minutes: int = Field(default=DEFAULT_TRAVEL_BUFFER, ge=0, le=180)
    durations: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_DURATIONS))
    lunch: tuple[Time, Time] = ("12:00", "14:30")  # soft: a meal outside these is a warning, not an error
    dinner: tuple[Time, Time] = ("18:30", "21:00")

    @model_validator(mode="after")
    def _window(self) -> ScheduleRules:
        if self.day_end <= self.day_start:
            raise ValueError(f"day_end {self.day_end} must be after day_start {self.day_start}")
        if any(m < 1 for m in self.durations.values()):
            raise ValueError("durations must be at least 1 minute")
        return self

    @classmethod
    def from_trip(cls, trip: TripRequest, **overrides) -> ScheduleRules:
        return cls(day_start=trip.day_start, day_end=trip.day_end, max_walk_minutes=trip.max_walk_minutes,
                   **overrides)

    def duration_for(self, stop: Stop) -> int:
        return self.durations.get(stop.kind or "", CATEGORY_DURATIONS[stop.category])

    def is_meal_time(self, start: int) -> bool:
        return any(to_minutes(a) <= start <= to_minutes(b) for a, b in (self.lunch, self.dinner))


# ------------------------------------------------------------------ travel times (read-only lookup, cached)
TravelLookup = Callable[[str, str], dict]  # (from_place, to_place) -> a travel_time result, city and mode bound


class TravelLeg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_place_id: str
    to_place_id: str
    status: TravelStatus
    minutes: int | None = None  # only when measured
    distance_m: float | None = None
    error_code: str | None = None
    note: str | None = None
    citation: dict | None = None  # the travel_time result's citation (OSRM route)


class TravelTimes:
    """Travel legs from an injected lookup, cached per ordered pair, with a bound on lookups."""

    def __init__(self, lookup: TravelLookup | None, *, max_lookups: int = 40):
        self._lookup, self._max = lookup, max_lookups
        self._cache: dict[tuple[str, str], TravelLeg] = {}
        self.calls: list[dict] = []  # contract-style tool_calls: tool, ok, ms (+ the pair)

    @property
    def lookups_used(self) -> int:
        return len(self.calls)

    def peek(self, a: Stop, b: Stop) -> TravelLeg | None:
        """The leg if it is already known, without a lookup."""
        return self._cache.get((a.place_id, b.place_id))

    def cached_view(self) -> TravelTimes:
        """The same cache, read-only and with no lookup: for what-if checks that must not spend lookups, guess
        routes, or write "not looked up" into the shared cache."""
        view = TravelTimes(None, max_lookups=0)
        view._cache, view._read_only = self._cache, True
        return view

    _read_only = False

    def leg(self, a: Stop, b: Stop) -> TravelLeg:
        key = (a.place_id, b.place_id)
        if key in self._cache:
            return self._cache[key]
        leg = self._fetch(a, b)
        if not self._read_only:
            self._cache[key] = leg
        return leg

    def _fetch(self, a: Stop, b: Stop) -> TravelLeg:
        base = dict(from_place_id=a.place_id, to_place_id=b.place_id)
        if self._lookup is None:
            return TravelLeg(status="not_looked_up", note="no travel-time source was given", **base)
        if len(self.calls) >= self._max:
            return TravelLeg(status="not_looked_up", note=f"the travel lookup limit ({self._max}) was reached", **base)
        started = time.perf_counter()
        try:
            result = self._lookup(a.name, b.name)
        except Exception as exc:  # a failing tool is an unknown travel time, never a guess
            self._log(a, b, False, started)
            return TravelLeg(status="unavailable", error_code="lookup_error", note=f"{type(exc).__name__}", **base)
        ok = isinstance(result, dict) and result.get("ok") is not False and not isinstance(result.get("error"), dict)
        minutes = result.get("duration_min") if ok else None
        if not ok or not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 0:
            err = (result.get("error") or {}) if isinstance(result, dict) else {}
            self._log(a, b, False, started)
            return TravelLeg(status="unavailable", error_code=str(err.get("code") or "bad_result"),
                             note=str(err.get("message") or "the travel_time result has no duration")[:200], **base)
        self._log(a, b, True, started)
        return TravelLeg(status="measured", minutes=minutes, distance_m=result.get("distance_m"),
                         citation=result.get("citation"), note=result.get("note"), **base)

    def _log(self, a: Stop, b: Stop, ok: bool, started: float) -> None:
        self.calls.append({"tool": "travel_time", "ok": ok, "ms": round((time.perf_counter() - started) * 1000),
                           "from": a.place_id, "to": b.place_id})


# ------------------------------------------------------------------ one day
class Issue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: IssueCode
    severity: Literal["error", "warning"]
    place_id: str | None = None
    date: dt.date | None = None
    message: str

    @classmethod
    def of(cls, code: IssueCode, message: str, place_id: str | None = None, date: dt.date | None = None) -> Issue:
        return cls(code=code, severity="error" if code in ERRORS else "warning", place_id=place_id, date=date,
                   message=message)


class ScheduledStop(BaseModel):
    """One stop's outcome on one day, with the evidence behind it."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    place_id: str
    name: str
    required: bool
    status: StopStatus
    start: Time | None = None
    end: Time | None = None
    duration_minutes: int
    duration_is_estimate: bool = True
    hours: DayHours
    window_used: str | None = None  # the opening window the visit sits in ("09:00-17:00"); None = hours unknown
    travel: TravelLeg | None = None  # from the previous scheduled stop; None for the first stop of the day
    gap_minutes: int = 0  # reserved before this stop: the measured travel time or the planning buffer
    buffer_used: bool = False  # the gap is the planning buffer, not a travel time
    wait_minutes: int = 0  # waiting after arrival for the place to open
    reason: str | None = None  # why it was not scheduled


class DaySchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: dt.date
    weekday: str
    day_start: Time
    day_end: Time
    stops: list[ScheduledStop] = Field(default_factory=list)  # in the order given, scheduled or not
    issues: list[Issue] = Field(default_factory=list)

    @property
    def scheduled(self) -> list[ScheduledStop]:
        return [s for s in self.stops if s.status == "scheduled"]

    @property
    def unscheduled(self) -> list[ScheduledStop]:
        return [s for s in self.stops if s.status != "scheduled"]

    @property
    def complete(self) -> bool:
        """Every stop was scheduled and nothing is wrong. Says nothing about uncertain facts."""
        return not any(i.severity == "error" for i in self.issues)

    @property
    def uncertain(self) -> bool:
        """A scheduled stop rests on unknown or unconfirmed hours, or on an unknown travel time."""
        return any(s.hours.status != "open" or s.buffer_used for s in self.scheduled)


def _fit(arrive: int, duration: int, hours: DayHours, day_end: int) -> tuple[int, int, str | None] | None:
    """The earliest visit from `arrive` that ends inside an opening window and the planning day."""
    if hours.status == "unknown" and not hours.windows:
        return (arrive, arrive + duration, None) if arrive + duration <= day_end else None
    for a, b in hours.windows:
        start = max(arrive, a)
        if start + duration <= min(b, day_end):
            return start, start + duration, f"{to_hhmm(a)}-{to_hhmm(b)}"
    return None


def schedule_day(day: dt.date, stops: Sequence[Stop], rules: ScheduleRules, travel: TravelTimes) -> DaySchedule:
    """Place `stops` on `day` in the given order: travel (or the buffer) first, then the earliest time the place
    is open, never past the planning day. A stop that is closed or does not fit is reported, not moved."""
    out: list[ScheduledStop] = []
    issues: list[Issue] = []
    clock, day_end = to_minutes(rules.day_start), to_minutes(rules.day_end)
    prev: Stop | None = None
    seen: set[str] = set()
    for stop in stops:
        if stop.place_id in seen:
            issues.append(Issue.of("duplicate_place", f"{stop.name} is listed twice on {day}; kept once.",
                                   stop.place_id, day))
            continue
        seen.add(stop.place_id)
        hours, duration = hours_on(stop.hours, day), rules.duration_for(stop)
        common = dict(place_id=stop.place_id, name=stop.name, required=stop.required, duration_minutes=duration,
                      hours=hours)
        if hours.status == "closed":
            reason = f"closed on {day} ({hours.weekday}): {hours.note}"
            out.append(ScheduledStop(status="closed", reason=reason, **common))
            issues.append(Issue.of("closed_on_date", f"{stop.name} is {reason}.", stop.place_id, day))
            continue
        leg = travel.leg(prev, stop) if prev else None
        gap = 0 if leg is None else (leg.minutes if leg.status == "measured" else rules.travel_buffer_minutes)
        arrive = clock + gap
        slot = _fit(arrive, duration, hours, day_end)
        if slot is None:
            opening = f"open {hours.windows_text}" if hours.windows else "opening hours unknown"
            reason = (f"a {duration}-minute visit does not fit: earliest arrival {to_hhmm(min(arrive, MIDNIGHT - 1))}, "
                      f"{opening}, the planning day ends at {rules.day_end}")
            out.append(ScheduledStop(status="does_not_fit", reason=reason, **common))
            issues.append(Issue.of("does_not_fit", f"{stop.name} on {day}: {reason}.", stop.place_id, day))
            continue
        start, end, window = slot
        out.append(ScheduledStop(status="scheduled", start=to_hhmm(start), end=to_hhmm(end), window_used=window,
                                 travel=leg, gap_minutes=gap, buffer_used=leg is not None and leg.status != "measured",
                                 wait_minutes=start - arrive, **common))
        issues += _stop_warnings(stop, hours, leg, start, rules, day)
        prev, clock = stop, end
    return DaySchedule(date=day, weekday=WEEKDAYS[day.weekday()], day_start=rules.day_start, day_end=rules.day_end,
                       stops=out, issues=issues)


def _stop_warnings(stop: Stop, hours: DayHours, leg: TravelLeg | None, start: int, rules: ScheduleRules,
                   day: dt.date) -> list[Issue]:
    out = []
    if hours.status == "unknown":
        out.append(Issue.of("hours_unknown", f"{stop.name}: opening hours unknown ({hours.note}); scheduled "
                            "tentatively, check before you go.", stop.place_id, day))
    elif hours.status == "unconfirmed":
        out.append(Issue.of("hours_unconfirmed", f"{stop.name}: opening hours not confirmed for {hours.weekday} "
                            f"({hours.note}).", stop.place_id, day))
    if leg is not None and leg.status != "measured":
        out.append(Issue.of("travel_unknown", f"Travel time to {stop.name} unknown ({leg.error_code or leg.note}); a "
                            f"{rules.travel_buffer_minutes}-minute planning buffer is used, not a measured time.",
                            stop.place_id, day))
        if rules.max_walk_minutes:
            out.append(Issue.of("walk_unverified", f"The walk to {stop.name} cannot be checked against the "
                                f"{rules.max_walk_minutes}-minute limit.", stop.place_id, day))
    elif leg is not None and rules.max_walk_minutes and leg.minutes > rules.max_walk_minutes:
        out.append(Issue.of("walk_over_limit", f"The walk to {stop.name} takes {leg.minutes} minutes, over the "
                            f"{rules.max_walk_minutes}-minute limit.", stop.place_id, day))
    if stop.category == "restaurant" and stop.kind != "drink" and not rules.is_meal_time(start):
        out.append(Issue.of("outside_meal_time", f"{stop.name} is scheduled at {to_hhmm(start)}, outside the usual "
                            f"meal times ({'-'.join(rules.lunch)}, {'-'.join(rules.dinner)}).", stop.place_id, day))
    return out


def validate_day(schedule: DaySchedule) -> list[str]:
    """Re-check a DaySchedule's own invariants (not the facts behind it): what a correct schedule must satisfy."""
    errors, seen = [], set()
    day_start, day_end = to_minutes(schedule.day_start), to_minutes(schedule.day_end)
    prev: ScheduledStop | None = None
    for s in schedule.scheduled:
        name = s.name
        if s.place_id in seen:
            errors.append(f"{name} is scheduled twice")
        seen.add(s.place_id)
        if s.start is None or s.end is None:
            errors.append(f"{name} is scheduled without times")
            continue
        start, end = to_minutes(s.start), to_minutes(s.end)
        if end - start != s.duration_minutes:
            errors.append(f"{name}: {s.start}-{s.end} is not {s.duration_minutes} minutes")
        if start < day_start or end > day_end:
            errors.append(f"{name}: {s.start}-{s.end} is outside the planning day {schedule.day_start}-{schedule.day_end}")
        if s.hours.status == "closed":
            errors.append(f"{name} is scheduled on a day it is closed")
        if s.hours.status in ("open", "unconfirmed") and not any(a <= start and end <= b for a, b in s.hours.windows):
            errors.append(f"{name}: {s.start}-{s.end} is outside its opening hours {s.hours.windows_text}")
        if s.travel is not None and s.travel.status == "measured" and s.gap_minutes != s.travel.minutes:
            errors.append(f"{name}: the gap {s.gap_minutes} is not the measured travel time {s.travel.minutes}")
        if prev is None:
            if s.travel is not None:
                errors.append(f"{name} is the first stop but has travel from another stop")
        else:
            if s.travel is None or s.travel.from_place_id != prev.place_id:
                errors.append(f"{name}: the travel leg does not start at the previous stop {prev.name}")
            if start < to_minutes(prev.end) + s.gap_minutes:
                errors.append(f"{name} starts at {s.start}, before {prev.name} ends ({prev.end}) plus "
                              f"{s.gap_minutes} minutes of travel")
        prev = s
    return errors


# ------------------------------------------------------------------ a whole trip
class TripSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    days: list[DaySchedule]  # every trip date, in order, assigned or not
    issues: list[Issue] = Field(default_factory=list)  # trip-level: duplicates across days, dates, required places

    @property
    def all_issues(self) -> list[Issue]:
        return self.issues + [i for d in self.days for i in d.issues]

    @property
    def complete(self) -> bool:
        return not any(i.severity == "error" for i in self.all_issues)

    @property
    def uncertain(self) -> bool:
        return any(d.uncertain for d in self.days)

    @property
    def scheduled_ids(self) -> list[str]:
        return [s.place_id for d in self.days for s in d.scheduled]

    def unscheduled_required(self) -> dict[str, str]:
        """Required place id -> why it is not in the schedule."""
        return {i.place_id: i.message for i in self.issues if i.code == "required_not_scheduled" and i.place_id}


def trip_dates(trip: TripRequest) -> list[dt.date]:
    return [trip.start_date + dt.timedelta(days=n) for n in range(trip.days)]


def availability(stop: Stop, dates: Iterable[dt.date]) -> dict[dt.date, HoursStatus]:
    """The hours status of a stop on each date: where it may go, and where it may never go ("closed")."""
    return {d: hours_on(stop.hours, d).status for d in dates}


def schedule_trip(assignment: Mapping[dt.date, Sequence[Stop]], dates: Sequence[dt.date], rules: ScheduleRules,
                  travel: TravelTimes, *, required: Iterable[Stop] = ()) -> TripSchedule:
    """Schedule each assigned day, then check the trip: dates inside the trip, each place on one day only, and
    every required place scheduled somewhere (or reported with the reason)."""
    issues: list[Issue] = []
    wanted = list(dates)
    for day in sorted(set(assignment) - set(wanted)):
        issues.append(Issue.of("date_outside_trip", f"{day} is not a trip date; its places were not scheduled.",
                               date=day))
    placed: dict[str, dt.date] = {}
    days: list[DaySchedule] = []
    for day in wanted:
        stops = []
        for stop in assignment.get(day, ()):
            if stop.place_id in placed and placed[stop.place_id] != day:
                issues.append(Issue.of("duplicate_place", f"{stop.name} is already planned on {placed[stop.place_id]}; "
                                       f"not repeated on {day}.", stop.place_id, day))
                continue
            placed.setdefault(stop.place_id, day)
            stops.append(stop)
        days.append(schedule_day(day, stops, rules, travel))
    scheduled = {s.place_id for d in days for s in d.scheduled}
    reasons = {s.place_id: f"{s.reason} on {d.date}" for d in days for s in d.unscheduled}
    for stop in required:
        if stop.place_id not in scheduled:
            why = reasons.get(stop.place_id, "it was not assigned to any trip date")
            issues.append(Issue.of("required_not_scheduled", f"{stop.name} was asked for but is not scheduled: {why}.",
                                   stop.place_id))
    return TripSchedule(days=days, issues=issues)


# ------------------------------------------------------------------ hand-over to BudgetReport.to_activity()
def activity_fields(stop: ScheduledStop) -> dict:
    """Keyword arguments for BudgetReport.to_activity(place_id, **fields): times, measured travel and honest
    notes. An unknown travel time stays None; the buffer is described, never reported as travel."""
    if stop.status != "scheduled":
        raise ValueError(f"{stop.name} is not scheduled ({stop.status})")
    notes = []
    if stop.hours.status == "unknown":
        notes.append("Opening hours unknown: check before you go")
    elif stop.hours.status == "unconfirmed":
        notes.append(f"Opening hours not confirmed for this day ({stop.hours.note})")
    if stop.buffer_used:
        notes.append(f"Travel time from the previous stop unknown: {stop.gap_minutes}-minute planning buffer")
    measured = stop.travel is not None and stop.travel.status == "measured"
    return {"start_time": stop.start, "end_time": stop.end, "duration_is_estimate": stop.duration_is_estimate,
            "travel_minutes_from_previous": stop.travel.minutes if measured else None,
            "notes": "; ".join(notes) or None}
