"""Prefer places whose facts can be checked: so a plan can actually pass verification instead of ending UNKNOWN.

The verifier (planning/verification.py) reports UNKNOWN for a place whose listing gives no opening hours, whose
price is unknown, or whose walking legs cannot be measured. That is right, and it is not changed here. What changes
is the choice: when the research found enough places with complete evidence, the ones without are set aside
before planning, and the plan says so. Nothing is filled in or estimated:

    opening hours   the listing's own hours must say open or closed on every trip date (planning/schedule.hours_on)
    price           the Budget Analyst's pricing of the listing must be known (a figure, a range or free)
    walking legs    a real travel_time lookup (the same MCP tool the Planner and the Verifier use) must measure a
                    leg between the place and another well-connected place

A place the traveller asked for is never set aside. If too few complete places remain to fill the trip (or a
meal per day), nothing of that kind is set aside: the plan then shows what could not be checked, as before.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Callable

from .budget_analyst import meal_target, prepare_candidates
from .plan_quality import target_range
from .research import ResearchBundle, ResearchedPlace
from .schedule import hours_on

MIN_SPARE = 0  # complete attractions beyond the trip's minimum (target_range) needed before any is set aside
MAX_PROBES = 60  # travel_time lookups this step may make
ANCHORS = 2  # well-connected places a candidate's legs are measured against
SEED_SEARCH = 6  # the first anchors are a measured pair among this many leading candidates
PARALLEL = 6  # probes at once, as the Verifier's prefetch (one MCP session; each probe is one travel_time call)
# Seconds this step may spend probing. Usually 1-40 s; once a stalled routing service made it take 589 s. Past the
# budget no new probe starts and an unanswered one counts as not asked, so its place is not set aside on it.
TIME_BUDGET_S = 45


def _dates(bundle: ResearchBundle) -> list[dt.date]:
    t = bundle.trip
    return [t.start_date + dt.timedelta(days=n) for n in range((t.end_date - t.start_date).days + 1)]


def _hours_gap(place: ResearchedPlace, dates: list[dt.date]) -> bool:
    statuses = [hours_on(place.hours, d).status for d in dates]
    return any(s in ("unknown", "unconfirmed") for s in statuses) or all(s == "closed" for s in statuses)


def _measured(result) -> bool:
    minutes = result.get("duration_min") if isinstance(result, dict) and result.get("ok") is not False else None
    return isinstance(minutes, int) and not isinstance(minutes, bool) and minutes >= 0


class _Prober:
    def __init__(self, lookup: Callable[[str, str], dict] | None, seconds: float | None = None):
        self.lookup, self.calls, self.cache = lookup, 0, {}
        self.timed = seconds is not None  # the fast path: batched, and no ride asked for just to see a leg can be measured
        self.measure = getattr(lookup, "walk", lookup) if self.timed else lookup
        self.deadline = time.monotonic() + (TIME_BUDGET_S if seconds is None else seconds)
        self._lock = threading.Lock()

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def leg(self, a: ResearchedPlace, b: ResearchedPlace) -> bool | None:
        """True measured, False not measurable, None not asked (no lookup, the probe limit or the time budget)."""
        key = tuple(sorted((a.place_id, b.place_id)))
        with self._lock:
            if key in self.cache:
                return self.cache[key]
            if self.lookup is None or self.calls >= MAX_PROBES or not self.remaining():
                return None
            self.calls += 1
        try:
            ok = _measured(self.measure(a.name, b.name))
        except Exception:  # a failing tool is "not measured", never a guess
            ok = False
        with self._lock:
            self.cache[key] = ok
        return ok


def _routable(places: list[ResearchedPlace], prober: _Prober) -> set[str] | None:
    """The places with at least one measured leg to a well-connected anchor; None when this cannot be told (no
    lookup, or no two candidate anchors connect at all)."""
    warm = getattr(prober.lookup, "warm", None) if prober.timed else None
    if warm is not None and len(places) > 1:  # hub, a, hub, b, ...: every probe below in one routing request
        hub = places[0].name
        warm([hub] + [stop for p in places[1:] for stop in (p.name, hub)], rides=False, seconds=prober.remaining())
    head = places[:SEED_SEARCH]  # the research's own order: its most relevant places first
    seed = next(((a, b) for i, a in enumerate(head) for b in head[i + 1:] if prober.leg(a, b)), None)
    if seed is None:
        return None
    anchors = list(seed)
    for p in places:  # more anchors: places measured against one already found
        if len(anchors) >= ANCHORS:
            break
        if p not in anchors and any(prober.leg(p, a) for a in anchors):
            anchors.append(p)
    ok = {a.place_id for a in anchors}

    def answer(p: ResearchedPlace) -> tuple[str, bool]:
        answers = []
        for a in anchors:  # stop at the first measured leg
            answers.append(prober.leg(p, a))
            if answers[-1]:
                return p.place_id, True
        # not asked (probe limit): never set aside on a question that was not asked
        return p.place_id, all(x is None for x in answers)

    todo = [p for p in places if p.place_id not in ok]
    pool = ThreadPoolExecutor(max_workers=PARALLEL)
    # each worker carries this request's context, so its lookups are recorded with the request's
    futures = {pool.submit(contextvars.copy_context().run, answer, p): p for p in todo}
    done, _ = wait(futures, timeout=prober.remaining())
    # A probe still running when the budget ends is not waited for: its place was not judged, so it stays.
    ok |= {p.place_id for f, p in futures.items() if f not in done or f.result()[1]}
    pool.shutdown(wait=False, cancel_futures=True)
    return ok


def checkable_bundle(bundle: ResearchBundle, travel_lookup: Callable[[str, str], dict] | None = None, *,
                     seconds: float | None = None) -> tuple[ResearchBundle, list[str]]:
    """The bundle to plan from, and what was set aside and why (sentences for the plan's limitations)."""
    dates = _dates(bundle)
    cands = prepare_candidates(bundle)
    required = set(cands.required_ids)
    cost = {c.place_id: c.cost.status for c in cands.candidates}
    gaps: dict[str, list[str]] = {}
    for p in bundle.places:
        why = []
        if _hours_gap(p, dates):
            why.append("no opening hours for the trip dates")
        if cost.get(p.place_id, "unknown") == "unknown":
            why.append("no price")
        if why:
            gaps[p.place_id] = why

    prober = _Prober(travel_lookup, seconds)
    by_kind = {k: [p for p in bundle.places if p.category == k] for k in ("attraction", "restaurant")}
    routable = _routable([p for p in by_kind["attraction"] + by_kind["restaurant"] if p.place_id not in gaps], prober)
    if routable is not None:
        for p in bundle.places:
            if p.place_id not in gaps and p.place_id not in routable:
                gaps[p.place_id] = ["no measurable walking route to the other places"]

    # Meals are optional in a plan (a meal that is not planned is said to be not included), so one checkable
    # restaurant is enough to leave the unverifiable ones out, unless food is what the traveller came for: then a
    # meal a day is the trip, and a restaurant that cannot be checked stays in the plan, reported as unknown.
    meals = meal_target(len(dates)) if "food" in bundle.trip.interests else 1
    need = {"attraction": target_range(len(dates), bundle.trip.activity_pace)[0] + MIN_SPARE, "restaurant": meals}
    keep: set[str] = set()
    notes: list[str] = []
    for kind, places in by_kind.items():
        complete = [p for p in places if p.place_id not in gaps or p.place_id in required]
        if len(complete) >= need[kind]:
            keep |= {p.place_id for p in complete}
            aside = [p for p in places if p.place_id not in keep]
            if aside:
                label = "attractions" if kind == "attraction" else "restaurants"
                notes.append(f"Left out {len(aside)} researched {label} whose facts cannot be checked, so the plan can "
                             "be verified: " + "; ".join(f"{p.name} ({', '.join(gaps[p.place_id])})" for p in aside[:8])
                             + (f"; and {len(aside) - 8} more" if len(aside) > 8 else "") + ".")
        else:
            keep |= {p.place_id for p in places}
    kept = [p for p in bundle.places if p.place_id in keep]
    for interest in bundle.trip.interests:
        before = [p for p in bundle.places if interest in p.tags]
        if before and not any(interest in p.tags for p in kept):
            notes.append(f"No place for '{interest}' could be fully checked, so none is planned (researched: "
                         + ", ".join(p.name for p in before[:4]) + ").")
    return bundle.model_copy(update={"places": kept}), notes
