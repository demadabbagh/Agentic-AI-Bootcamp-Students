"""Planning quality: soft advice about a valid schedule, kept apart from the hard checks.

validate_day() and the Planner's guardrail decide whether a plan is valid. This module asks whether it uses the
trip well: an avoidable empty day, a front-loaded trip, an overfull day, a long walk a reorder would avoid, a
meal outside the usual times. Every finding rests on the same deterministic engine and on data already known:

  - a place is "movable" to a date only if Python schedules it there (open, and it fits the planning day);
  - a reorder is suggested only when every leg of the new order is an already-measured travel time.

Nothing is invented and no travel lookup is spent (the checks read the travel cache only). Unequal days are not a
problem in themselves: a date with fewer feasible places is reported as unavoidable, not as a mistake.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .schedule import DaySchedule, ScheduleRules, Stop, TravelTimes, TripSchedule, schedule_day, to_minutes

QualityCode = Literal["empty_day", "front_loaded", "busy_day", "long_walk", "meal_time"]


class QualityRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_places_per_day: int = Field(default=5, ge=1)
    max_busy_minutes: int = Field(default=480, ge=60)  # first start to last end
    long_walk_minutes: int = Field(default=30, ge=1)  # used when the traveller set no walking limit
    front_loaded_margin: float = Field(default=0.30, gt=0, lt=1)  # first half's share of places above an even share


class QualityIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: QualityCode
    date: dt.date | None = None
    place_ids: list[str] = Field(default_factory=list)  # the places concerned, or the ones that could move
    message: str
    avoidable: bool  # Python found a feasible alternative among the planned places


FULL_DAY_TARGET = 2  # non-meal places on a full sightseeing day; up to one more when it fits
LIGHT_DAY_TARGET = 1  # arrival and departure days of a trip of three days or more
EXTRA_PER_DAY = 1


def daily_targets(n_days: int) -> list[int]:
    """The non-meal places a useful day has, per trip date: lighter first and last days on longer trips.

    A target, never a quota: a day with fewer feasible places stays lighter, and nothing is added to reach it.
    """
    if n_days <= 0:
        return []
    if n_days <= 2:
        return [FULL_DAY_TARGET] * n_days
    return [LIGHT_DAY_TARGET] + [FULL_DAY_TARGET] * (n_days - 2) + [LIGHT_DAY_TARGET]


def target_range(n_days: int) -> tuple[int, int]:
    """(min, max) non-meal places for the whole trip: the daily targets, and one more per day at most."""
    low = sum(daily_targets(n_days))
    return low, low + EXTRA_PER_DAY * max(n_days, 0)


def movable_to(day: dt.date, schedule: TripSchedule, stops: Mapping[str, Stop], rules: ScheduleRules,
               travel: TravelTimes) -> list[str]:
    """Places planned on another date that has two or more places, which Python can schedule alone on `day`."""
    view = travel.cached_view()  # one stop has no travel: no lookup, nothing guessed
    out = []
    for other in schedule.days:
        if other.date == day or len(other.scheduled) < 2:
            continue
        out += [s.place_id for s in other.scheduled if schedule_day(day, [stops[s.place_id]], rules, view).scheduled]
    return out


def _better_order(day: DaySchedule, target: str, limit: int, stops: Mapping[str, Stop], rules: ScheduleRules,
                  travel: TravelTimes) -> list[str] | None:
    """An order of the same places, every leg already measured and none over `limit`, that Python schedules in
    full. Tries moving `target` to each other position, and the reverse order: at most n + 1 candidates."""
    order = [s.place_id for s in day.scheduled]
    j = order.index(target)
    rest = order[:j] + order[j + 1:]
    candidates = [rest[:k] + [target] + rest[k:] for k in range(len(rest) + 1)] + [order[::-1]]
    for cand in dict.fromkeys(map(tuple, candidates)):
        if list(cand) == order:
            continue
        legs = [travel.peek(stops[a], stops[b]) for a, b in zip(cand, cand[1:])]
        if any(leg is None or leg.status != "measured" or leg.minutes > limit for leg in legs):
            continue
        if len(schedule_day(day.date, [stops[i] for i in cand], rules, travel.cached_view()).scheduled) == len(cand):
            return list(cand)
    return None


def assess_quality(schedule: TripSchedule, stops: Mapping[str, Stop], rules: ScheduleRules, travel: TravelTimes,
                   q: QualityRules | None = None, *, compared_assignments: int = 0) -> list[QualityIssue]:
    """Soft findings about a schedule, most important first. Never changes the schedule.

    `compared_assignments`: how many alternative day assignments (the planner's own placements) were compared
    before this schedule was chosen; 0 = none. Messages say what was examined and never claim an optimum."""
    q = q or QualityRules()
    days = schedule.days
    counts = {d.date: len(d.scheduled) for d in days}
    total = sum(counts.values())
    out: list[QualityIssue] = []
    for d in days:
        if counts[d.date] or total < 2:
            continue
        movable = movable_to(d.date, schedule, stops, rules, travel)
        if movable:
            out.append(QualityIssue(code="empty_day", date=d.date, place_ids=movable, avoidable=True,
                                    message=f"{d.date} ({d.weekday}) has no places, although {len(movable)} planned "
                                            f"place(s) could go on it: {', '.join(stops[i].name for i in movable)}."))
        elif any(c >= 2 for c in counts.values()):
            out.append(QualityIssue(code="empty_day", date=d.date, avoidable=False,
                                    message=f"{d.date} ({d.weekday}) has no places: none of the planned places can go "
                                            "on it (closed that day or too long for the planning day)."))
    n = len(days)
    if n >= 2 and total >= n:
        first, later = days[:n // 2], days[n // 2:]  # 3 days: the first day against the other two
        share = sum(counts[d.date] for d in first) / total
        if share >= len(first) / n + q.front_loaded_margin:
            movable = list(dict.fromkeys(i for d in later for i in movable_to(d.date, schedule, stops, rules, travel)))
            out.append(QualityIssue(code="front_loaded", place_ids=movable, avoidable=bool(movable),
                                    message=f"{round(share * 100)}% of the places are in the first {len(first)} of "
                                            f"{n} days" + (f"; {len(movable)} could move later." if movable else ".")))
    for d in days:
        sched = d.scheduled
        if not sched:
            continue
        busy = to_minutes(sched[-1].end) - to_minutes(sched[0].start)
        if len(sched) > q.max_places_per_day or busy > q.max_busy_minutes:
            out.append(QualityIssue(code="busy_day", date=d.date, place_ids=[s.place_id for s in sched],
                                    avoidable=False,  # advice: a lighter day is not checked here
                                    message=f"{d.date} ({d.weekday}) is full: {len(sched)} places from {sched[0].start} "
                                            f"to {sched[-1].end}."))
    limit = rules.max_walk_minutes or q.long_walk_minutes
    for d in days:
        for s in d.scheduled[1:]:
            if s.travel is None or s.travel.status != "measured" or s.travel.mode != "foot" or s.travel.minutes <= limit:
                continue
            better = _better_order(d, s.place_id, limit, stops, rules, travel)
            if better:
                hint = (f"; visiting this day's places in the order {', '.join(stops[i].name for i in better)} "
                        "avoids it (every walk in that order already measured)")
            else:
                hint = ("; no reorder of this day's places using walking times already measured avoids it"
                        + (f", and it remains in the best of {compared_assignments} combinations of the planner's own "
                           "day assignments that Python compared" if compared_assignments
                           else "; other day assignments were not compared"))
            out.append(QualityIssue(code="long_walk", date=d.date, place_ids=better or [s.place_id],
                                    avoidable=better is not None,
                                    message=f"The walk to {s.name} on {d.date} takes {s.travel.minutes} minutes{hint}."))
    for d in days:
        for issue in d.issues:
            if issue.code == "outside_meal_time":
                out.append(QualityIssue(code="meal_time", date=d.date, place_ids=[issue.place_id],
                                        avoidable=False, message=issue.message))
    return out
