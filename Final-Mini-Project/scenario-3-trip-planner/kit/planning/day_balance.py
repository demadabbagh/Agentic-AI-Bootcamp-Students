"""Python's own day assignment, and the deterministic completion of the Planner agent's.

The Planner agent chooses which trip date each selected place goes on. Everything Python can decide by itself is
decided here, so an answer is never sent back to the LLM (a full, slow retry) for a fault Python can fix:

  propose(ctx)       Python's plan from scratch: places grouped by location into days sized for the trip (lighter
                     arrival and departure days), each day in its best visiting order, one restaurant at a meal
                     time. Shown to the agent as its starting point; used, labelled, when the agent gives no
                     valid answer.
  complete(a, ctx)   structure: drop dates outside the trip and ids that may not be planned, merge a date listed
                     twice, add a missing date, keep a duplicated place once, take a place off a closed date and
                     place every selected place the answer left out (or say why none fits).
  rebalance(a, ctx)  quality: give an avoidable empty or thin day places from a fuller day, take an unscheduled
                     place that fits, and visit each day's places in the order that walks least.
  refine_walks(a, ctx)  after the walks were measured: avoid a measured walk over the limit by a reorder or by
                     moving that place to a day where it is closer.

What is estimated and what is not. To compare candidate plans without spending travel lookups (live routing is
rate-limited to one request a second), walks are ranked by the straight-line distance between the listings'
coordinates (at 4.5 km/h, times 1.3 for streets) when a walk was not measured yet. That estimate only ranks
plans: it is never shown, and every time in the itinerary comes from the scheduler with a measured walk or the
labelled planning buffer. Feasibility is always Python's scheduler (opening hours, the planning day).
"""
from __future__ import annotations

import datetime as dt
import itertools
import math
from typing import TYPE_CHECKING

from .plan_quality import EXTRA_PER_DAY, QualityRules, daily_targets
from .schedule import Stop, schedule_day

if TYPE_CHECKING:  # the models live in itinerary_planner, which imports this module lazily
    from .itinerary_planner import DayAssignment, PlanningContext

WALK_KMH = 4.5
STREET_FACTOR = 1.3  # streets are longer than the straight line
MAX_PERMUTED_STOPS = 6  # a day with up to this many stops is tried in every order (720); longer: nearest neighbour
MAX_PASSES = 3
MAX_SCHEDULED = 24  # orders of one day checked by the scheduler, cheapest walks first


# ------------------------------------------------------------------ geometry and day costs (no lookups)
def km(a: Stop, b: Stop) -> float | None:
    if None in (a.lat, a.lon, b.lat, b.lon):
        return None
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


class Planner:
    """The engine for one PlanningContext: memoised schedules and walk estimates, read-only travel cache."""

    def __init__(self, ctx: PlanningContext):
        self.ctx = ctx
        self.view = ctx.travel.cached_view()  # never a lookup, never a guess written into the cache
        self.limit = ctx.rules.max_walk_minutes or QualityRules().long_walk_minutes
        self.targets = dict(zip(ctx.dates, daily_targets(len(ctx.dates))))
        self._fits: dict = {}
        self._orders: dict = {}

    # facts ------------------------------------------------------------------
    def stop(self, pid: str) -> Stop:
        return self.ctx.stops[pid]

    def is_meal(self, pid: str) -> bool:
        return self.stop(pid).category == "restaurant"

    def sights(self, ids: list[str]) -> list[str]:
        return [i for i in ids if not self.is_meal(i)]

    def walk(self, a: str, b: str) -> int:
        """Minutes between two places for ranking: measured if known, else the straight-line estimate, else the
        planning buffer (a place without coordinates is never assumed to be near)."""
        leg = self.view.peek(self.stop(a), self.stop(b))
        if leg is not None and leg.status == "measured":
            return leg.minutes
        d = km(self.stop(a), self.stop(b))
        if d is None:
            return self.ctx.rules.travel_buffer_minutes
        return round(d * STREET_FACTOR / WALK_KMH * 60)

    def route(self, ids: list[str]) -> int:
        return sum(self.walk(a, b) for a, b in zip(ids, ids[1:]))

    def long_legs(self, ids: list[str]) -> int:
        return sum(self.walk(a, b) > self.limit for a, b in zip(ids, ids[1:]))

    def fits(self, day: dt.date, ids: list[str]) -> bool:
        """Python's scheduler places every stop (open that day, inside the planning day); unknown walks get the
        planning buffer, as in the real schedule."""
        key = (day, tuple(ids))
        if key not in self._fits:
            s = schedule_day(day, [self.stop(i) for i in ids], self.ctx.rules, self.view)
            self._fits[key] = (not s.unscheduled, sum(i.code == "outside_meal_time" for i in s.issues))
        return self._fits[key][0]

    def meal_misses(self, day: dt.date, ids: list[str]) -> int:
        self.fits(day, ids)
        return self._fits[(day, tuple(ids))][1]

    def can_go(self, pid: str, day: dt.date) -> bool:
        return self.ctx.hours(pid, day) != "closed" and self.fits(day, [pid])

    # orders -----------------------------------------------------------------
    def order_key(self, day: dt.date, ids: list[str]) -> tuple:
        return self.meal_misses(day, ids), self.long_legs(ids), self.route(ids)

    def best_order(self, day: dt.date, ids: list[str]) -> list[str] | None:
        """The feasible order of the same places with the fewest meals outside meal times, then long walks, then
        walking; the given order wins every tie. None when no order fits."""
        key = (day, tuple(ids))
        if key in self._orders:
            return self._orders[key]
        if len(ids) <= 1:
            out = list(ids) if not ids or self.fits(day, ids) else None
        else:
            if len(ids) <= MAX_PERMUTED_STOPS:
                others = [list(p) for p in itertools.permutations(ids) if list(p) != list(ids)]
            else:
                others = [self._nearest_first(ids, start) for start in ids]
            # Only the orders that walk least go through the scheduler (the expensive part): the given order,
            # and the MAX_SCHEDULED cheapest others by long walks and walking time.
            others.sort(key=lambda o: (self.long_legs(o), self.route(o)))
            candidates = [list(ids)] + others[:MAX_SCHEDULED]
            out, best = None, None
            for cand in candidates:
                if not self.fits(day, cand):
                    continue
                k = self.order_key(day, cand)
                if best is None or k < best:
                    out, best = cand, k
        self._orders[key] = out
        return out

    def _nearest_first(self, ids: list[str], start: str) -> list[str]:
        rest, path = [i for i in ids if i != start], [start]
        while rest:
            nxt = min(rest, key=lambda i: self.walk(path[-1], i))
            rest.remove(nxt)
            path.append(nxt)
        return path

    def insert(self, day: dt.date, ids: list[str], pid: str) -> list[str] | None:
        """The best feasible order of the day's places with `pid` added (None: it does not fit)."""
        if self.ctx.hours(pid, day) == "closed":
            return None
        if len(ids) + 1 <= MAX_PERMUTED_STOPS:
            return self.best_order(day, ids + [pid])
        best, out = None, None
        for k in range(len(ids) + 1):  # long day: keep the others' order, try every position
            cand = ids[:k] + [pid] + ids[k:]
            if self.fits(day, cand) and (best is None or self.order_key(day, cand) < best):
                best, out = self.order_key(day, cand), cand
        return out

    def cap(self, day: dt.date) -> int:
        return self.targets[day] + EXTRA_PER_DAY

    def day_score(self, plan: dict[dt.date, list[str]]) -> tuple:
        """Lower is better: empty sightseeing days, then places short of the daily targets (a full day has two
        or three), then walks over the limit, then walking time."""
        return (sum(not self.sights(ids) for ids in plan.values()),
                sum(max(0, self.targets[d] - len(self.sights(ids))) for d, ids in plan.items()),
                sum(self.long_legs(ids) for ids in plan.values()),
                sum(self.route(ids) for ids in plan.values()))


# ------------------------------------------------------------------ placing one place
def _place(p: Planner, plan: dict[dt.date, list[str]], pid: str, *, exclude: set[dt.date] = frozenset(),
           over_cap: bool = False) -> dt.date | None:
    """Put `pid` on the trip date where it costs least: a date below its target first, then below its cap,
    then the least extra walking. A restaurant goes where it lands at a meal time, one per day first."""
    meal = p.is_meal(pid)
    options = []
    for day in p.ctx.dates:
        if day in exclude:
            continue
        ids = plan[day]
        n = len(p.sights(ids))
        meals = len(ids) - n
        if not meal and n >= p.cap(day) and not over_cap:
            continue
        new = p.insert(day, ids, pid)
        if new is None:
            continue
        extra = p.route(new) - p.route(ids)
        if meal:  # one restaurant per date first, at a meal time where possible, near the date's places
            key = (meals, not n, p.meal_misses(day, new) > p.meal_misses(day, ids), extra)
        else:
            key = (n >= p.targets[day], n >= p.cap(day), p.long_legs(new) - p.long_legs(ids), extra, n)
        options.append((key, p.ctx.dates.index(day), day, new))
    if not options:
        return None
    *_, day, new = min(options)
    plan[day] = new
    return day


def _why_not(p: Planner, pid: str) -> str:
    if all(p.ctx.hours(pid, d) == "closed" for d in p.ctx.dates):
        return "closed on every trip date (from its listed hours)"
    if not any(p.fits(d, [pid]) for d in p.ctx.dates):
        return "a visit does not fit the planning day on any open trip date"
    return "no trip date has room for it with the other planned places"


# ------------------------------------------------------------------ Python's own plan
def _quotas(p: Planner, n: int) -> dict[dt.date, int]:
    """How many sightseeing places each date takes: the daily targets, then up to one more on full days, or
    fewer on the arrival and departure days when there are not enough places."""
    q = dict(p.targets)
    total = sum(q.values())
    full = [d for d in p.ctx.dates if p.targets[d] == max(p.targets.values())]
    light = [d for d in p.ctx.dates if d not in full]
    while total < n:  # more places than targets: full days first, then the light ones, never past a cap
        room = [d for d in full if q[d] < p.cap(d)] or [d for d in light if q[d] < p.cap(d)]
        if not room:
            break
        for d in room:
            if total >= n:
                break
            q[d] += 1
            total += 1
    while total > n:  # fewer places than targets: a full day gives one up first (never leaving a date with
        # nothing while another has two), then the light days
        give = [d for d in full[::-1] if q[d] > 1] or [d for d in light[::-1] if q[d] > 0] or                [d for d in full[::-1] if q[d] > 0]
        for d in give:
            if total <= n:
                break
            q[d] -= 1
            total -= 1
    return q


def _clusters(p: Planner, ids: list[str], sizes: list[int]) -> list[list[str]]:
    """Places with coordinates split into len(sizes) groups of at most those sizes, near places together.

    Seeds are spread out (each the place farthest from the seeds so far); every other place joins the nearest
    seed with room, the places with the most to lose (largest gap between their nearest and second-nearest
    seed) first. Group k has at most sizes[k] places; the largest groups go to the densest seeds."""
    pts = [i for i in ids if km(p.stop(i), p.stop(i)) is not None]
    k = min(len(sizes), len(pts))
    if not k:
        return [[] for _ in sizes]
    clat = sum(p.stop(i).lat for i in pts) / len(pts)
    clon = sum(p.stop(i).lon for i in pts) / len(pts)
    centre = Stop(place_id="_centre", name="centre", category="attraction", lat=clat, lon=clon)
    seeds = [max(pts, key=lambda i: km(p.stop(i), centre))]
    while len(seeds) < k:
        seeds.append(max((i for i in pts if i not in seeds),
                         key=lambda i: min(km(p.stop(i), p.stop(s)) for s in seeds)))
    near = {s: sum(min(seeds, key=lambda t: km(p.stop(i), p.stop(t))) == s for i in pts) for s in seeds}
    by_density = sorted(seeds, key=lambda s: -near[s])
    order = sorted(range(len(sizes)), key=lambda j: -sizes[j])
    group_of = {by_density[n]: order[n] for n in range(k)}
    groups: list[list[str]] = [[] for _ in sizes]
    for s in seeds:
        groups[group_of[s]].append(s)

    def dists(i):
        return sorted((km(p.stop(i), p.stop(s)), s) for s in seeds)

    rest = [i for i in pts if i not in seeds]
    rest.sort(key=lambda i: -((dists(i)[1][0] - dists(i)[0][0]) if len(seeds) > 1 else 0))
    for i in rest:
        for _, s in dists(i):
            g = group_of[s]
            if len(groups[g]) < sizes[g]:
                groups[g].append(i)
                break
    return groups


def propose(ctx: PlanningContext) -> DayAssignment:
    """Python's deterministic plan for the selected places (see the module docstring)."""
    from .itinerary_planner import DayAssignment, DayChoice, UnscheduledChoice

    p = Planner(ctx)
    plan: dict[dt.date, list[str]] = {d: [] for d in ctx.dates}
    sights = [i for i in ctx.stops if not p.is_meal(i)]
    meals = [i for i in ctx.stops if p.is_meal(i)]
    usable = [i for i in sights if any(p.can_go(i, d) for d in ctx.dates)]
    quotas = _quotas(p, len(usable))

    # 1. groups of nearby places, sized by the quotas, each matched to a date of its size that can take most of
    #    it (a place closed on its group's date is placed in step 2)
    days_with_quota = [d for d in ctx.dates if quotas[d] > 0]
    sizes = [quotas[d] for d in days_with_quota]
    groups = list(zip(sizes, _clusters(p, usable, sizes)))
    groups.sort(key=lambda g: -g[0])
    free_days = list(days_with_quota)
    for size, group in groups:  # each group goes to a date of its own size that can take most of it
        same = [d for d in free_days if quotas[d] == size]
        if not group or not same:
            continue
        best = max(same, key=lambda d: (sum(p.can_go(i, d) for i in group), -ctx.dates.index(d)))
        free_days.remove(best)
        for pid in group:
            if p.can_go(pid, best):
                new = p.insert(best, plan[best], pid)
                if new is not None:
                    plan[best] = new
    # 2. everything not placed yet (closed on its group's date, no coordinates, did not fit): cheapest date
    unscheduled: list[UnscheduledChoice] = []
    placed = {i for ids in plan.values() for i in ids}
    rest = sorted((i for i in sights if i not in placed),
                  key=lambda i: (not ctx.stops[i].required, sum(p.can_go(i, d) for d in ctx.dates)))
    for pid in rest:
        if _place(p, plan, pid) is None and _place(p, plan, pid, over_cap=True) is None:
            unscheduled.append(UnscheduledChoice(place_id=pid, reason=f"Python: {_why_not(p, pid)}"))
    # 3. local improvement, then one restaurant per day at a meal time
    plan = _improve(p, plan)[0]
    for pid in sorted(meals, key=lambda i: not ctx.stops[i].required):
        if _place(p, plan, pid) is None:
            unscheduled.append(UnscheduledChoice(place_id=pid, reason=f"Python: {_why_not(p, pid)}"))
    days = [DayChoice(date=d, place_ids=plan[d], reason="Grouped by location by Python" if plan[d] else "")
            for d in ctx.dates]
    return DayAssignment(days=days, unscheduled=unscheduled)


def _improve(p: Planner, plan: dict[dt.date, list[str]]) -> tuple[dict[dt.date, list[str]], list[str]]:
    """Moves and swaps of sightseeing places between dates that lower day_score, until none does."""
    notes: list[str] = []
    for _ in range(MAX_PASSES):
        changed = False
        dates = p.ctx.dates
        for a, b in itertools.permutations(dates, 2):
            for pid in list(p.sights(plan[a])):
                if pid not in plan[a]:
                    continue
                left = [i for i in plan[a] if i != pid]
                if not p.sights(left) and len(p.sights(plan[a])) <= 1:
                    continue  # never empty a day to fill another
                if len(p.sights(plan[b])) >= p.cap(b):
                    # full: try a swap with one of b's places instead
                    for other in list(p.sights(plan[b])):
                        new_b = p.insert(b, [i for i in plan[b] if i != other], pid)
                        new_a = p.insert(a, left, other) if new_b else None
                        if new_a and new_b and p.day_score({a: new_a, b: new_b}) < p.day_score(
                                {a: plan[a], b: plan[b]}):
                            plan[a], plan[b] = new_a, new_b
                            changed = True
                            break
                    continue
                new_b = p.insert(b, plan[b], pid)
                new_a = p.best_order(a, left) if new_b else None
                if new_a is None or new_b is None:
                    continue
                before = p.day_score({a: plan[a], b: plan[b]})
                after = p.day_score({a: new_a, b: new_b})
                fill = len(p.sights(plan[b])) < p.targets[b] and len(p.sights(plan[a])) > p.targets[a]
                if after < before or (fill and after[:2] <= before[:2]):
                    plan[a], plan[b] = new_a, new_b
                    changed = True
        if not changed:
            break
    return plan, notes


# ------------------------------------------------------------------ the agent's answer, completed
def complete(a: DayAssignment, ctx: PlanningContext) -> tuple[DayAssignment, list[str]]:
    """The agent's assignment with every structural fault Python can fix fixed, and one line per change.

    Never adds a place that was not selected and never drops a selected one: each is on one date or in
    unscheduled with the agent's reason or Python's. The agent's order of a date is kept when it is feasible."""
    from .itinerary_planner import DayAssignment, DayChoice, UnscheduledChoice, _prose_problems

    p = Planner(ctx)
    notes: list[str] = []
    plan: dict[dt.date, list[str]] = {d: [] for d in ctx.dates}
    reasons: dict[dt.date, str] = {}
    for day in a.days:
        if day.date not in plan:
            if day.place_ids:
                notes.append(f"{day.date} is not a trip date: its places were placed again")
            for pid in day.place_ids:
                plan.setdefault(None, []).append(pid)
            continue
        if reasons.get(day.date) is not None and day.place_ids:
            notes.append(f"{day.date} was listed twice: merged")
        plan[day.date] += day.place_ids
        reasons.setdefault(day.date, day.reason)
    pending = plan.pop(None, [])
    seen: set[str] = set()
    for day in ctx.dates:
        kept = []
        for pid in plan[day]:
            if (why := ctx.refusal(pid)) is not None:
                notes.append(f"{pid} removed: {why}")
                continue
            if pid in seen:
                notes.append(f"{p.stop(pid).name} was listed more than once: kept once")
                continue
            if ctx.hours(pid, day) == "closed":
                notes.append(f"{p.stop(pid).name} is closed on {day} ({day:%a}): moved")
                pending.append(pid)
                seen.add(pid)
                continue
            seen.add(pid)
            kept.append(pid)
        plan[day] = kept
    unscheduled: list[UnscheduledChoice] = []
    for u in a.unscheduled:
        if u.place_id in seen or ctx.refusal(u.place_id) is not None:
            if u.place_id in seen and u.place_id not in pending:
                notes.append(f"{p.stop(u.place_id).name} was both planned and unscheduled: kept on its date")
            continue
        seen.add(u.place_id)
        stop = ctx.stops[u.place_id]
        if stop.required and ctx.possible_dates(u.place_id):
            notes.append(f"{stop.name} was asked for by the traveller and fits: placed")
            pending.append(u.place_id)
        elif u.place_id not in [x.place_id for x in unscheduled]:
            unscheduled.append(u if not _prose_problems("", u.reason)
                               else u.model_copy(update={"reason": "Left out by the planner"}))
    missing = [pid for pid in ctx.stops if pid not in seen]
    if missing:
        notes.append("not accounted for by the planner, placed by Python: "
                     + ", ".join(p.stop(i).name for i in missing))
    pending += missing
    # a date whose places do not all fit: keep the agent's order if it fits, else the best order, else move some
    for day in ctx.dates:
        if p.fits(day, plan[day]):
            continue
        order = p.best_order(day, plan[day])
        if order is not None:
            plan[day] = order
            notes.append(f"reordered {day} so every place fits")
            continue
        while plan[day] and not p.best_order(day, plan[day]):
            drop = max(plan[day], key=lambda i: (not ctx.stops[i].required, p.is_meal(i) is False,
                                                 ctx.rules.duration_for(p.stop(i))))
            plan[day].remove(drop)
            pending.append(drop)
            notes.append(f"{p.stop(drop).name} did not fit on {day}: moved")
        plan[day] = p.best_order(day, plan[day]) or plan[day]
    for pid in dict.fromkeys(pending):
        if _place(p, plan, pid) is None and _place(p, plan, pid, over_cap=True) is None:
            unscheduled.append(UnscheduledChoice(place_id=pid, reason=f"Python: {_why_not(p, pid)}"))
    days = [DayChoice(date=d, place_ids=plan[d],
                      reason="" if _prose_problems("", reasons.get(d) or "") else (reasons.get(d) or ""))
            for d in ctx.dates]
    clean_notes = [n for n in a.notes if not _prose_problems("", n)]
    return DayAssignment(days=days, unscheduled=unscheduled, notes=clean_notes), notes


def rebalance(a: DayAssignment, ctx: PlanningContext) -> tuple[DayAssignment, list[str]]:
    """The agent's plan, kept, with what Python can improve without second-guessing it: a place the agent left
    out that a thin date can take is placed, an avoidable empty or thin date gets a place from a fuller one,
    and each date is visited in its best order. One line per change; a changed date loses the agent's reason."""
    from .itinerary_planner import DayAssignment

    p = Planner(ctx)
    plan = {d.date: list(d.place_ids) for d in a.days}
    before = {d: list(ids) for d, ids in plan.items()}
    notes: list[str] = []
    still: list = []
    for u in a.unscheduled:
        meal = p.is_meal(u.place_id)
        thin = {d for d in ctx.dates
                if (meal and p.sights(plan[d]) and all(not p.is_meal(i) for i in plan[d]))
                or (not meal and len(p.sights(plan[d])) < p.targets[d])}
        day = _place(p, plan, u.place_id, exclude=set(ctx.dates) - thin) if thin else None
        if day is not None:
            notes.append(f"{p.stop(u.place_id).name} was left out but fits on {day} ({day:%a}): placed")
        else:
            still.append(u)
    for _ in range(len(ctx.dates) * 2):
        light = sorted((d for d in ctx.dates if len(p.sights(plan[d])) < p.targets[d]),
                       key=lambda d: len(p.sights(plan[d])) - p.targets[d])
        moved = False
        for d in light:
            empty = not p.sights(plan[d])
            moves = []
            for e in ctx.dates:
                n = len(p.sights(plan[e]))
                if e == d or n <= (1 if empty else p.targets[e]):
                    continue
                for pid in p.sights(plan[e]):
                    new_d = p.insert(d, plan[d], pid)
                    new_e = p.best_order(e, [i for i in plan[e] if i != pid])
                    if new_d is None or new_e is None:
                        continue
                    cost = p.route(new_d) + p.route(new_e) - p.route(plan[d]) - p.route(plan[e])
                    moves.append((p.long_legs(new_d) + p.long_legs(new_e), cost, ctx.dates.index(e), pid, e,
                                  new_d, new_e))
            if moves:
                *_, pid, e, new_d, new_e = min(moves)
                plan[d], plan[e] = new_d, new_e
                notes.append(f"moved {p.stop(pid).name} from {e} to {d} ({d:%a}), which had "
                             + ("no places" if empty else "fewer places than a full day"))
                moved = True
                break
        if not moved:
            break
    for d in ctx.dates:
        order = p.best_order(d, plan[d])
        if order is not None and order != plan[d] and p.order_key(d, order) < p.order_key(d, plan[d]):
            if set(plan[d]) == set(before[d]):
                notes.append(f"reordered {d} to walk less")
            plan[d] = order
    days = [day.model_copy(update={"place_ids": plan[day.date],
                                   "reason": day.reason if plan[day.date] == before[day.date] else ""})
            for day in a.days]
    return DayAssignment(days=days, unscheduled=still, notes=a.notes if not notes else []), notes


def refine_walks(a: DayAssignment, ctx: PlanningContext) -> tuple[DayAssignment, list[str]]:
    """After the walks were measured: a measured walk over the limit is avoided by the day's best order, or by
    moving that place to the date where it is closest (or swapping it with a place there when that date is full),
    when that lowers the walks over the limit and every date still fits. Up to MAX_PASSES passes."""
    from .itinerary_planner import DayAssignment

    p = Planner(ctx)
    plan = {d.date: list(d.place_ids) for d in a.days}
    notes: list[str] = []
    for d in ctx.dates * MAX_PASSES:  # one fix per date per pass, until no date has a walk Python can avoid
        ids = plan[d]
        if not p.long_legs(ids):
            continue
        order = p.best_order(d, ids)
        if order is not None and p.long_legs(order) < p.long_legs(ids):
            plan[d] = order
            notes.append(f"reordered {d} to avoid a walk over {p.limit} minutes")
            continue
        for x, y in zip(ids, ids[1:]):
            if p.walk(x, y) <= p.limit:
                continue
            for pid in (y, x):
                if p.is_meal(pid) or len(p.sights(plan[d])) <= 1:
                    continue
                options = []
                for e in ctx.dates:
                    if e == d:
                        continue
                    before = p.long_legs(plan[d]) + p.long_legs(plan[e])
                    rest_d = [i for i in plan[d] if i != pid]
                    # a move, where the other date has room; otherwise a swap with one of its places, so both
                    # dates keep their number of places
                    trades = [None] if len(p.sights(plan[e])) < p.cap(e) else p.sights(plan[e])
                    for other in trades:
                        new_e = p.insert(e, [i for i in plan[e] if i != other], pid)
                        new_d = p.best_order(d, rest_d) if other is None else p.insert(d, rest_d, other)
                        if new_e is None or new_d is None:
                            continue
                        gain = before - (p.long_legs(new_d) + p.long_legs(new_e))
                        # never thinner than a date's target to save a walk: balance ranks first (day_score)
                        short = p.day_score({d: new_d, e: new_e})[:2] > p.day_score({d: plan[d], e: plan[e]})[:2]
                        if gain > 0 and not short:
                            options.append((-gain, p.route(new_d) + p.route(new_e), ctx.dates.index(e), other or "",
                                            e, new_d, new_e))
                if options:
                    *_, other, e, new_d, new_e = min(options)
                    plan[d], plan[e] = new_d, new_e
                    notes.append(f"moved {p.stop(pid).name} from {d} to {e}"
                                 + (f" (and {p.stop(other).name} the other way)" if other else "")
                                 + f" to avoid a walk over {p.limit} minutes")
                    break
            break
    if not notes:
        return a, []
    days = [day.model_copy(update={"place_ids": plan[day.date], "reason": ""}) if plan[day.date] != day.place_ids
            else day for day in a.days]
    return a.model_copy(update={"days": days}), notes


def score(a: DayAssignment, ctx: PlanningContext) -> tuple:
    """Lower is better: places left out, empty days, places short of the targets, long walks, walking."""
    p = Planner(ctx)
    plan = {d.date: list(d.place_ids) for d in a.days}
    return (len(a.unscheduled),) + p.day_score(plan)
