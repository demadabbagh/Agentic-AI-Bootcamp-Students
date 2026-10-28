"""Tests for planning/revision.py. No live service and no LLM call.

The plans and reports come from the real Python path (test_verification's helpers: recorded research, the
Planner's build_itinerary(), verify_itinerary()), broken one fact at a time; travel times replay the recorded
travel_time results. Trip: Istanbul, Mon 2026-11-02 to Wed 2026-11-04, 2 travellers, 700 USD, Topkapı Palace
required (closed on Tuesdays).
"""
import datetime as dt
import os
from decimal import Decimal

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest
from pydantic import ValidationError

from planning.budget_analyst import prepare_candidates
from planning.itinerary_planner import PlanningContext
from planning.models import Activity, Itinerary
from planning.revision import (MAX_ATTEMPTS, EvidenceRequest, RevisionResult, _reduce, _State, revise_itinerary)
from planning.schedule import validate_day
from research_fixtures import bundle, recorded_lookup
from test_verification import (BAZAAR, CISTERN, DOLMABAHCE, HAGIA, MON, PERA, PICKS, SYNTHETIC, TOPKAPI,
                               TUE, VALID, WED, add, clean, edit, move, place, plan, remove, verify)


def revise(it, b, report, *, planner=None, travel=True, **kw):
    """revise_itinerary on the report verify() gives for `it`, with the recorded travel times."""
    lookup = recorded_lookup() if travel is True else (travel or None)
    v = verify(it, b=b, report=report, planner=planner, travel=lookup)
    return v, revise_itinerary(it, b, report, verification=v, planner_report=planner, travel_lookup=lookup, **kw)


def day(it: Itinerary, date: dt.date):
    return next((d for d in it.days if d.date == date), None)


def ids(it: Itinerary, date: dt.date) -> list[str]:
    d = day(it, date)
    return [a.place_id for a in d.activities] if d else []


def steps(result, attempt=0):
    return [(s.action, s.outcome) for s in result.attempts[attempt].plan.steps]


def assert_schedulable(result):
    """Every rebuilt date is a valid Python schedule: no overlaps, inside the hours and the planning day."""
    for d in result.planner_report.schedule.days:
        assert validate_day(d) == []


# ------------------------------------------------------------------ successful revisions
def test_a_closed_venue_moves_to_a_date_it_is_open_and_the_rest_is_kept():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")  # closed on Tuesdays
    v, r = revise(bad, b, report, planner=planner)
    assert v.status == "FAIL" and {"closed_on_date", "insufficient_travel_gap"} <= {i.code for i in v.issues}
    assert r.outcome == "revised" and r.final.issues == [] and r.status == "PASS"
    assert r.verified is False  # recorded data: never verified
    assert TOPKAPI in ids(r.itinerary, MON) and TOPKAPI not in ids(r.itinerary, TUE)
    assert steps(r) == [("move_activity", "applied"), ("reschedule_activity", "superseded")]
    assert r.changed_dates == [MON, TUE] and r.preserved_dates == [WED]
    assert day(r.itinerary, WED) == day(bad, WED)  # untouched date: verbatim
    assert set(r.resolved) == {"closed_on_date wv-istanbul-topkapi-palace 2026-11-03",
                               "insufficient_travel_gap wv-istanbul-topkapi-palace 2026-11-03"}
    assert_schedulable(r)


def test_a_travel_time_conflict_is_rescheduled_on_its_own_date_only():
    b, report, planner, it = clean()
    bad = edit(it, TOPKAPI, start_time="10:35", end_time="12:05")  # 5 minutes for a 15-minute walk
    v, r = revise(bad, b, report)
    assert "insufficient_travel_gap" in {i.code for i in v.issues}
    assert r.outcome == "revised" and r.final.issues == []
    assert r.changed_dates == [MON] and r.preserved_dates == [TUE, WED]
    assert [day(r.itinerary, d) for d in (TUE, WED)] == [day(bad, d) for d in (TUE, WED)]
    mon = {a.place_id: a for a in day(r.itinerary, MON).activities}
    assert mon[TOPKAPI].start_time == "10:45"  # 10:30 + the measured 15 minutes: Python's, not a guess
    assert_schedulable(r)


def test_overlapping_activities_are_rescheduled():
    b, report, planner, it = clean()
    bad = edit(it, CISTERN, start_time="12:00", end_time="13:30")  # Topkapı ends 12:15
    v, r = revise(bad, b, report)
    assert "activity_overlap" in {i.code for i in v.issues}
    assert r.outcome == "revised" and "activity_overlap" not in {i.code for i in r.final.issues}
    assert_schedulable(r)


def test_a_missing_required_place_is_added_back():
    b, report, planner, it = clean()
    bad = remove(it, TOPKAPI)
    v, r = revise(bad, b, report)
    assert [(i.code, i.recommended_action) for i in v.issues] == [("required_place_missing", "add_required_place")]
    assert r.outcome == "revised" and r.status == "PASS"
    placed_on = [d.date for d in r.itinerary.days if TOPKAPI in ids(r.itinerary, d.date)]
    assert len(placed_on) == 1 and placed_on[0] != TUE  # never on its closed day
    assert steps(r) == [("add_required_place", "applied")]
    assert all(day(r.itinerary, d) == day(bad, d) for d in r.preserved_dates)


def test_a_required_place_can_take_the_slot_of_a_less_important_one():
    # Monday only, 09:00-13:00: Hagia Sophia and the Cistern fill it, so Topkapı (required) needs room.
    b, report, planner = plan(days={MON: [HAGIA, TOPKAPI]}, unscheduled={CISTERN: "does not fit"},
                              picks={HAGIA: ("include", 1), CISTERN: ("include", 2)},
                              start_date="2026-11-02", end_date="2026-11-02", day_end="13:00")
    cistern = report.to_activity(CISTERN, start_time="10:44", end_time="12:14")
    bad = add(remove(planner.itinerary, TOPKAPI), MON, cistern)
    v, r = revise(bad, b, report)
    assert "required_place_missing" in {i.code for i in v.issues}
    assert r.outcome == "revised" and r.final.issues == []
    assert set(ids(r.itinerary, MON)) == {HAGIA, TOPKAPI}
    assert r.attempts[0].plan.unscheduled_after[CISTERN].startswith("left out to make room for Topkapı Palace")
    assert "selected_place_unscheduled" in {i.code for i in r.final.warnings}  # said out loud, not hidden
    assert HAGIA in ids(r.itinerary, MON)  # the more important place stays


def test_a_walk_over_the_limit_is_reordered_with_measured_walks_only():
    b, report, planner, it = clean()
    tight = bundle(max_walk_minutes=14)  # Hagia Sophia -> Topkapı is 15 minutes
    v, r = revise(it, tight, report)
    assert [i.code for i in v.issues] == ["walk_over_limit"]
    assert r.outcome == "revised" and r.final.issues == [] and steps(r) == [("reorder_day", "applied")]
    assert set(ids(r.itinerary, MON)) == set(ids(it, MON))  # the same places, another order
    walks = [a.travel_minutes_from_previous for a in day(r.itinerary, MON).activities[1:]]
    assert all(w is not None and w <= 14 for w in walks)


def test_an_unreachable_walking_limit_is_partially_revised_and_says_so():
    b, report, planner, it = clean()
    v, r = revise(it, bundle(max_walk_minutes=12), report)
    assert len(v.issues) == 3
    assert r.outcome == "partially_revised" and r.status == "FAIL"
    assert len(r.final.issues) < len(v.issues) and r.remaining_errors
    first = r.attempts[0].plan.steps
    assert [s.outcome for s in first] == ["applied", "superseded", "superseded"]  # one reorder per date
    assert not r.attempts[-1].accepted and len(r.attempts) <= MAX_ATTEMPTS


def test_duplicates_and_unplannable_places_are_removed():
    b, report, planner, it = clean()
    synthetic, hamam = place(b, SYNTHETIC), place(b, "wv-istanbul-hurrem-sultan-hamami")
    bad = add(it, WED, it.days[0].activities[0].model_copy(update={"start_time": "12:00", "end_time": "13:30",
                                                                    "travel_minutes_from_previous": None}))
    bad = add(bad, TUE, Activity(name=synthetic.name, place_id=SYNTHETIC, start_time="12:00", end_time="13:00",
                                 estimated_cost=10.0))
    bad = add(bad, TUE, Activity(name=hamam.name, place_id=hamam.place_id, source_url=hamam.source_url,
                                 start_time="15:00", end_time="16:00", estimated_cost=335.79))
    v, r = revise(bad, b, report)
    assert {"duplicate_place", "synthetic_place", "excluded_place"} <= {i.code for i in v.issues}
    assert r.outcome == "revised" and r.final.issues == []
    assert ids(r.itinerary, MON).count(HAGIA) == 1 and HAGIA not in ids(r.itinerary, WED)  # the first kept
    assert SYNTHETIC not in ids(r.itinerary, TUE) and hamam.place_id not in ids(r.itinerary, TUE)
    assert day(r.itinerary, MON) == day(bad, MON)  # Monday was not touched


def test_a_wrong_cost_is_repriced_in_place_keeping_the_times():
    b, report, planner, it = clean()
    bad = edit(it, HAGIA, estimated_cost=5.0)
    v, r = revise(bad, b, report)
    assert [i.code for i in v.issues] == ["activity_cost_mismatch"]
    assert r.outcome == "revised" and r.final.issues == []
    before, after = day(bad, MON).activities, day(r.itinerary, MON).activities
    assert [(a.place_id, a.start_time, a.end_time) for a in after] == [(a.place_id, a.start_time, a.end_time)
                                                                        for a in before]
    want = prepare_candidates(b).get(HAGIA).cost.estimated
    assert Decimal(str(after[0].estimated_cost)) == want  # Python's pricing of the listing


def test_a_date_outside_the_trip_is_brought_into_the_trip():
    b, report, planner, it = clean()
    data = it.model_dump()
    dolma = next(a for d in data["days"] for a in d["activities"] if a["place_id"] == DOLMABAHCE)
    data["days"][2]["activities"] = []
    data["days"].append({"date": dt.date(2026, 11, 5), "activities": [dolma]})
    bad = Itinerary.model_validate(data)
    v, r = revise(bad, b, report)
    assert "date_outside_trip" in {i.code for i in v.issues}
    assert r.outcome == "revised" and r.final.issues == []
    assert all(d.date <= WED for d in r.itinerary.days) and DOLMABAHCE in {a.place_id for d in r.itinerary.days
                                                                          for a in d.activities}


# ------------------------------------------------------------------ impossible revisions
def test_a_required_place_closed_on_every_date_is_the_travellers_decision():
    b, report, planner = plan(days={TUE: [HAGIA, CISTERN]}, unscheduled={TOPKAPI: "Closed on Tuesdays"},
                              picks={HAGIA: ("include", 1), CISTERN: ("include", 2)},
                              start_date="2026-11-03", end_date="2026-11-03")
    v, r = revise(planner.itinerary, b, report, planner=planner)
    assert r.outcome == "no_change_needed" and r.status == "FAIL" and r.attempts == []  # nothing to change
    assert r.itinerary == planner.itinerary
    assert any("Topkapı" in d for d in r.traveller_decisions)


def test_a_required_place_on_its_closed_day_with_no_other_date_is_never_dropped():
    b, report, planner = plan(days={TUE: [HAGIA, CISTERN]}, unscheduled={TOPKAPI: "Closed on Tuesdays"},
                              picks={HAGIA: ("include", 1), CISTERN: ("include", 2)},
                              start_date="2026-11-03", end_date="2026-11-03")
    topkapi = report.to_activity(TOPKAPI, start_time="15:00", end_time="16:30")
    bad = add(planner.itinerary, TUE, topkapi)  # forced onto its closed day
    v, r = revise(bad, b, report)
    assert "closed_on_date" in {i.code for i in v.issues}
    assert r.outcome == "unchanged" and r.itinerary == bad and r.status == "FAIL"
    assert ("move_activity", "impossible") in steps(r)
    assert r.attempts[0].reason == "no plan change could be applied"
    assert any("required and no other trip date" in d for d in r.traveller_decisions)


def test_required_places_over_the_budget_leave_nothing_to_cut():
    b, report, planner = plan(days={MON: [TOPKAPI]}, picks={}, budget_total=50, start_date="2026-11-02",
                              end_date="2026-11-02")
    assert report.status == "infeasible"  # the Budget Analyst keeps the required place, over budget
    v, r = revise(planner.itinerary, b, report, planner=planner)
    assert "over_budget" in {i.code for i in v.issues}
    assert ("reduce_cost", "impossible") in steps(r) and r.outcome == "unchanged"
    assert TOPKAPI in ids(r.itinerary, MON)


def test_a_revision_that_is_not_better_is_rejected_and_the_original_stands():
    b, report, planner, it = clean()
    v, r = revise(it, bundle(max_walk_minutes=12), report, max_attempts=1)
    assert len(r.attempts) == 1 and r.attempts[0].accepted  # 3 -> 2 errors: kept
    v2, r2 = revise(r.itinerary, bundle(max_walk_minutes=12), report)
    assert r2.outcome == "unchanged" and r2.itinerary == r.itinerary  # no order is better: not changed
    assert all(not a.accepted for a in r2.attempts)


# ------------------------------------------------------------------ unresolved: evidence, never invented
def test_unknown_routes_and_prices_become_evidence_requests_not_values():
    b, report, planner = plan(days=VALID, picks=PICKS)
    v, r = revise(planner.itinerary, b, report, planner=planner)
    assert v.issues == [] and r.outcome == "no_change_needed" and r.itinerary == planner.itinerary
    assert {(e.tool, e.place_id) for e in r.evidence_requests} == {("travel_time", PERA), ("get_opening_hours", BAZAAR)}
    route = next(e for e in r.evidence_requests if e.tool == "travel_time")
    assert route.arguments == {"from_place": "Dolmabahce Palace", "to_place": "Pera Museum", "city": "Istanbul",
                               "mode": "foot"}
    assert set(EvidenceRequest.model_fields) == {"tool", "arguments", "reason", "codes", "place_id", "date"}
    bazaar = next(a for d in r.itinerary.days for a in d.activities if a.place_id == BAZAAR)
    assert bazaar.estimated_cost is None and r.status == "UNKNOWN"  # still unknown, never filled in


def test_a_plan_change_never_turns_a_route_into_a_number():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")
    v, r = revise(bad, b, report, travel=None)  # no travel source at all
    assert r.outcome == "revised" and "closed_on_date" not in {i.code for i in r.final.issues}
    assert all(a.travel_minutes_from_previous is None for d in r.itinerary.days if d.date in r.changed_dates
               for a in d.activities)
    assert "route_unknown" in {i.code for i in r.final.unknowns} and r.status == "UNKNOWN"
    assert r.travel_lookups == []


# ------------------------------------------------------------------ bounds and ownership
def test_lookups_and_attempts_are_bounded():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")
    v, r = revise(bad, b, report, max_lookups=3)
    assert len(r.travel_lookups) <= 3 and len(r.attempts) <= MAX_ATTEMPTS
    assert "closed_on_date" not in {i.code for i in r.final.issues}  # unknown legs get the buffer, never a guess


def test_status_and_verified_are_the_final_reports():
    b, report, planner, it = clean()
    v, r = revise(move(it, TOPKAPI, TUE, "11:00", "12:30"), b, report)
    with pytest.raises(ValidationError, match="final VerificationReport"):
        RevisionResult(**{**r.model_dump(), "status": "FAIL"})
    with pytest.raises(ValidationError, match="final VerificationReport"):
        RevisionResult(**{**r.model_dump(), "verified": True})
    assert RevisionResult.model_validate_json(r.model_dump_json()) == r  # the Coordinator can store it


def test_the_cut_to_fit_the_budget_drops_the_least_important_paid_place_first():
    b, report, planner, it = clean()
    ctx = PlanningContext(report, b, recorded_lookup())
    st = _State(ctx, b, it, planner)
    total = sum(i.cost.estimated or 0 for i in report.selected)
    dropped = _reduce(st, total - Decimal("1"))  # just over: one paid place must go
    assert len(dropped) == 1 and not ctx.stops[dropped[0]].required
    paid = [i.place_id for i in report.selected if not ctx.stops[i.place_id].required and i.cost.estimated]
    assert dropped == [paid[-1]]  # the Budget Analyst's lowest-ranked paid include
    assert st.unscheduled[dropped[0]] == "left out to fit the budget"
    assert _reduce(_State(ctx, b, it, planner), None) == []  # no budget: nothing is cut
