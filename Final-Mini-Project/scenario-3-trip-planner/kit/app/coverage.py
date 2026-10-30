"""Trip coverage: is the plan a useful trip for its length, not just a valid schedule?

The Planner's "complete" means every selected place was scheduled. This checks
how many of the trip's calendar days have something to do. Deterministic: it
only reads the trip dates, the interests and the planned activities.

  - The trip runs from start_date to end_date inclusive (2 to 9 November is
    8 calendar days, 7 nights): the same dates the Planner schedules.
  - A day is covered by a sightseeing, cultural, shopping or other non-meal
    activity. A meal alone covers a day only on a food-focused trip (food is
    the traveller's only interest).
  - At least half the calendar days (rounded up) must be covered.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from planning.plan_quality import daily_targets, target_range

MIN_COVERED_SHARE = 0.5
MEAL_KINDS = {"eat", "drink"}
NON_MEAL_KINDS = {"see", "do", "buy"}


def _date(value) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def calendar_dates(start_date, end_date) -> list[dt.date]:
    start, end = _date(start_date), _date(end_date)
    return [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]


def duration_problem(trip_info: dict) -> str | None:
    """
    A message when duration_days disagrees with the dates, else None.

    The dates are what gets planned. A duration equal to the number of
    nights (one less than the calendar days) is the same trip.
    """
    try:
        start, end = _date(trip_info["start_date"]), _date(trip_info["end_date"])
    except (KeyError, TypeError, ValueError):
        return "Please give the start and end dates as YYYY-MM-DD."

    if end < start:
        return f"The end date {end} is before the start date {start}. Please check the dates."

    days = (end - start).days + 1
    nights = days - 1
    duration = trip_info.get("duration_days")

    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration != int(duration):
        return "Please confirm how many days the trip lasts."

    if int(duration) in (days, nights) and int(duration) > 0:
        return None

    return (
        f"The dates {start} to {end} cover {days} calendar day{'s' * (days != 1)} "
        f"({nights} night{'s' * (nights != 1)}), but the trip length was given as "
        f"{int(duration)} days. Please confirm the dates or the length of the trip."
    )


def refreshed_duration(previous: dict, merged: dict) -> int | None:
    """
    The duration that matches new dates, when the dates changed but the
    duration did not: the old value describes the old trip. None otherwise.

    Only a change of existing dates counts. A duration stated before any
    dates were given is the traveller's own and is checked, not replaced.
    """
    if not (previous.get("start_date") and previous.get("end_date")):
        return None
    if all(merged.get(k) == previous.get(k) for k in ("start_date", "end_date")):
        return None
    if merged.get("duration_days") != previous.get("duration_days"):
        return None  # restated in this message: duration_problem() checks it

    try:
        start, end = _date(merged["start_date"]), _date(merged["end_date"])
    except (KeyError, TypeError, ValueError):
        return None
    return (end - start).days + 1 if end >= start else None


def is_food_focused(interests) -> bool:
    """Food is the only interest (Travel API tags, as research used them)."""
    return bool(interests) and set(interests) == {"food"}


@dataclass(frozen=True)
class CoverageReport:
    trip_days: int
    food_focused: bool
    covered_dates: list[dt.date] = field(default_factory=list)
    meal_only_dates: list[dt.date] = field(default_factory=list)  # meals, but nothing else to do
    empty_dates: list[dt.date] = field(default_factory=list)  # nothing at all planned

    @property
    def nights(self) -> int:
        return self.trip_days - 1

    @property
    def required_days(self) -> int:
        return max(1, math.ceil(self.trip_days * MIN_COVERED_SHARE))

    @property
    def uncovered_dates(self) -> list[dt.date]:
        return sorted(self.meal_only_dates + self.empty_dates)

    @property
    def sufficient(self) -> bool:
        return len(self.covered_dates) >= self.required_days

    def _what(self) -> str:
        return "planned activities" if self.food_focused else "sightseeing or other non-meal activities"

    def insufficient_message(self) -> str:
        return (
            f"Only {len(self.covered_dates)} of {self.trip_days} trip days have {self._what()}; "
            f"a useful plan needs at least {self.required_days}. Research found too few places "
            "matching these interests for this trip length. Nothing was verified or saved. "
            "Try broader interests or a shorter trip."
        )

    def warning(self) -> str | None:
        """For a plan that passes: which days still have nothing (or only meals) planned."""
        if not self.uncovered_dates:
            return None
        dates = ", ".join(d.isoformat() for d in self.uncovered_dates)
        return (
            f"{len(self.uncovered_dates)} of {self.trip_days} trip days have no {self._what()} "
            f"planned: {dates}."
        )

    def as_dict(self) -> dict:
        return {
            "trip_days": self.trip_days,
            "nights": self.nights,
            "required_days": self.required_days,
            "food_focused": self.food_focused,
            "sufficient": self.sufficient,
            "covered_dates": [d.isoformat() for d in self.covered_dates],
            "meal_only_dates": [d.isoformat() for d in self.meal_only_dates],
            "empty_dates": [d.isoformat() for d in self.empty_dates],
            "warning": self.warning(),
        }


def _is_meal(place_id, name, kind, context: dict) -> bool | None:
    """True for a meal, False for a non-meal activity, None when nothing says which."""
    category = context["by_id"].get(place_id or "") or context["by_name"].get(name or "")
    if category is not None:
        return category == "restaurant"
    if kind in MEAL_KINDS:
        return True
    if kind in NON_MEAL_KINDS:
        return False
    return None


def coverage_context(bundle) -> dict:
    """What a later coverage check needs from the research, JSON-safe for the session."""
    trip = bundle.trip
    return {
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "interests": list(trip.interests),
        "by_id": {p.place_id: p.category for p in bundle.places},
        "by_name": {p.name: p.category for p in bundle.places},
    }


def assess_coverage(itinerary, bundle) -> CoverageReport:
    """
    Coverage of an Itinerary model over the trip's calendar dates.

    A place's category comes from the research bundle (attraction or
    restaurant), else from the listing kind; an activity that says
    neither is not counted.
    """
    context = coverage_context(bundle)
    days = {
        d.date: [_is_meal(a.place_id, a.name, a.kind, context) for a in d.activities]
        for d in (itinerary.days if itinerary is not None else [])
    }
    return _assess(days, context)


def assess_store_coverage(itinerary: dict | None, context: dict) -> CoverageReport:
    """The same check for a Trip Store itinerary dict (activities carry only names)."""
    days = {}
    for day in (itinerary or {}).get("days") or []:
        try:
            date = _date(day.get("date"))
        except (TypeError, ValueError):
            continue
        days[date] = [
            _is_meal(a.get("place_id"), a.get("name"), a.get("kind"), context)
            for a in day.get("activities") or [] if isinstance(a, dict)
        ]
    return _assess(days, context)


def _assess(days: dict, context: dict) -> CoverageReport:
    dates = calendar_dates(context["start_date"], context["end_date"])
    food_focused = is_food_focused(context["interests"])
    covered, meal_only, empty = [], [], []

    for date in dates:
        kinds = days.get(date, [])
        if False in kinds or (food_focused and True in kinds):
            covered.append(date)
        elif True in kinds:
            meal_only.append(date)
        else:
            empty.append(date)

    return CoverageReport(trip_days=len(dates), food_focused=food_focused,
                          covered_dates=covered, meal_only_dates=meal_only, empty_dates=empty)


# ------------------------------------------------------------------ density: a useful plan, not just a valid one
@dataclass(frozen=True)
class DensityReport:
    """How full each day is against daily_targets(). Advice that labels a plan; it never blocks one."""
    dates: list[dt.date]
    targets: list[int]
    non_meal_per_day: list[int]
    meals: int
    matching_attractions: int  # researched attractions that match the interests
    selected_non_meal: int  # what the Budget Analyst put in the plan
    excluded_matching: int  # matching attractions the Budget Analyst excluded
    dropped_for_budget: int
    unscheduled: int  # selected places the Planner or the scheduler could not place

    @property
    def total_non_meal(self) -> int:
        return sum(self.non_meal_per_day)

    @property
    def target_min(self) -> int:
        return target_range(len(self.dates))[0]

    @property
    def target_max(self) -> int:
        return target_range(len(self.dates))[1]

    @property
    def light_dates(self) -> list[dt.date]:
        return [d for d, n, t in zip(self.dates, self.non_meal_per_day, self.targets) if n < t]

    @property
    def level(self) -> str:
        return "light" if self.light_dates else "full"

    def reason(self) -> str:
        if self.matching_attractions < self.target_min:
            return (f"research found only {self.matching_attractions} attractions matching the interests, "
                    "so there are not enough feasible activities to fill the days")
        if self.selected_non_meal < self.target_min:
            extra = f", {self.dropped_for_budget} left out to fit the budget" if self.dropped_for_budget else ""
            return (f"the Budget Analyst selected {self.selected_non_meal} of {self.matching_attractions} matching "
                    f"attractions ({self.excluded_matching} excluded{extra})")
        if self.unscheduled:
            return (f"{self.unscheduled} selected place(s) could not be scheduled "
                    "(opening hours or the time available)")
        return "the selected places could not be spread to reach every day's target"

    def message(self) -> str | None:
        if self.level == "full":
            return None
        dates = ", ".join(d.isoformat() for d in self.light_dates)
        return (
            f"Light itinerary: {self.total_non_meal} sightseeing activities over {len(self.dates)} days; a full plan "
            f"has about {self.target_min} to {self.target_max} (two or three on full days, one on arrival and "
            f"departure days). Lighter days: {dates}. Why: {self.reason()}."
        )

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "total_non_meal": self.total_non_meal,
            "meals": self.meals,
            "target_min": self.target_min,
            "target_max": self.target_max,
            "days": [{"date": d.isoformat(), "non_meal": n, "target": t}
                     for d, n, t in zip(self.dates, self.non_meal_per_day, self.targets)],
            "light_dates": [d.isoformat() for d in self.light_dates],
            "matching_attractions": self.matching_attractions,
            "selected_non_meal": self.selected_non_meal,
            "excluded_matching": self.excluded_matching,
            "dropped_for_budget": self.dropped_for_budget,
            "unscheduled": self.unscheduled,
            "message": self.message(),
        }


def _matches(tags, interests) -> bool:
    return not interests or bool(set(tags or []) & set(interests))


def assess_density(itinerary, bundle, budget_report, planner_report=None) -> DensityReport:
    """Per-day non-meal activities against daily_targets(), and where the supply ran short."""
    context = coverage_context(bundle)
    dates = calendar_dates(context["start_date"], context["end_date"])
    interests = context["interests"]
    by_date = {d.date: d.activities for d in (itinerary.days if itinerary is not None else [])}

    non_meal, meals = [], 0
    for date in dates:
        kinds = [_is_meal(a.place_id, a.name, a.kind, context) for a in by_date.get(date, [])]
        non_meal.append(kinds.count(False))
        meals += kinds.count(True)

    attractions = [p for p in bundle.places if p.category == "attraction"]
    return DensityReport(
        dates=dates,
        targets=daily_targets(len(dates)),
        non_meal_per_day=non_meal,
        meals=meals,
        matching_attractions=sum(_matches(p.tags, interests) for p in attractions),
        selected_non_meal=sum(i.category == "attraction" for i in budget_report.selected),
        excluded_matching=sum(i.category == "attraction" and _matches(i.tags, interests)
                              for i in budget_report.excluded),
        dropped_for_budget=len(budget_report.dropped_for_budget),
        unscheduled=len(planner_report.unscheduled) if planner_report is not None else 0,
    )


def density_summary(report: DensityReport) -> list[str]:
    return [
        "=== DENSITY DIAGNOSTICS ===",
        f"Non-meal per day: {report.non_meal_per_day} (targets {report.targets}), meals: {report.meals}",
        f"Level: {report.level}; matching attractions: {report.matching_attractions}, "
        f"selected: {report.selected_non_meal}, excluded matching: {report.excluded_matching}, "
        f"unscheduled: {report.unscheduled}",
        "===========================",
    ]


def coverage_summary(report: CoverageReport) -> list[str]:
    return [
        "=== COVERAGE DIAGNOSTICS ===",
        f"Trip days: {report.trip_days} ({report.nights} nights), food-focused: {report.food_focused}",
        f"Covered: {len(report.covered_dates)} (needs {report.required_days})",
        f"Meal only: {len(report.meal_only_dates)}, empty: {len(report.empty_dates)}",
        f"Sufficient: {report.sufficient}",
        "============================",
    ]
