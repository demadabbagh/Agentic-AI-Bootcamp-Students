"""Tests for planning/verification.py. No live service and no LLM call.

The plans come from the real Python path (Budget Analyst report from the recorded research, then the Planner's
build_itinerary() on a fixed assignment), and are then broken one fact at a time. Travel times replay the recorded
travel_time results; "fresh" lookups are scripted results that say mode "live". Trip: Istanbul, Mon 2026-11-02 to
Wed 2026-11-04, 2 travellers, 700 USD, Topkapı Palace required (closed on Tuesdays).
"""
import copy
import datetime as dt
import os
from decimal import Decimal
from typing import get_args

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")

import pytest
from pydantic import ValidationError

from planning.budget_analyst import BudgetDecision, PlaceDecision, apply_decision, prepare_candidates
from planning.itinerary_planner import DayAssignment, DayChoice, PlanningContext, UnscheduledChoice, build_itinerary
from planning.models import Activity, Itinerary
from planning.research import ToolFailure
from planning.verification import (CHECK_ORDER, CODES, CheckResult, FreshLookups, IssueCode, RevisionAction,
                                   VerificationIssue, VerificationReport, freshness_of, verify_itinerary)
from research_fixtures import bundle, recorded_lookup


def I(slug):  # noqa: E743 - a place id of the Istanbul fixture
    return f"wv-istanbul-{slug}"


TOPKAPI, HAGIA, CISTERN, SULEYMANIYE = I("topkapi-palace"), I("hagia-sophia"), I("basilica-cistern"), I("suleymaniye-mosque")
DOLMABAHCE, PERA, ALI_USTA, BAZAAR = I("dolmabahce-palace"), I("pera-museum"), I("ali-usta-cig-kofte"), I("grand-bazaar")
GALATA, HAMAM, SYNTHETIC = I("galata-tower"), I("hurrem-sultan-hamami"), "synthetic-istanbul-rihla-test-kitchen"
MON, TUE, WED = dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)

# A plan with every price known, every hour confirmed and every walk measured in the recorded data.
CLEAN_PICKS = {HAGIA: ("include", 1), CISTERN: ("include", 2), SULEYMANIYE: ("include", 3),
               DOLMABAHCE: ("include", 4), ALI_USTA: ("include", 5), GALATA: ("optional", 1)}
CLEAN = {MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA], TUE: [SULEYMANIYE], WED: [DOLMABAHCE]}
# The Planner tests' plan: the Grand Bazaar has no price and Dolmabahçe -> Pera was never recorded.
PICKS = {HAGIA: ("include", 1), CISTERN: ("include", 2), SULEYMANIYE: ("include", 3), DOLMABAHCE: ("include", 4),
         PERA: ("include", 5), ALI_USTA: ("include", 6), BAZAAR: ("include", 7), GALATA: ("optional", 1)}
VALID = {MON: [HAGIA, TOPKAPI, CISTERN, ALI_USTA], TUE: [SULEYMANIYE, BAZAAR], WED: [DOLMABAHCE, PERA]}


def budget_report(b, picks):
    cands = prepare_candidates(b)
    return apply_decision(cands, BudgetDecision(decisions=[
        PlaceDecision(place_id=i, decision=picks.get(i, ("exclude", None))[0], rank=picks.get(i, ("exclude", None))[1])
        for i in cands.open_ids]))


def assign(days, unscheduled=None):
    return DayAssignment(days=[DayChoice(date=d, place_ids=ids) for d, ids in days.items()],
                         unscheduled=[UnscheduledChoice(place_id=i, reason=r) for i, r in (unscheduled or {}).items()])


def plan(days=CLEAN, picks=CLEAN_PICKS, unscheduled=None, **trip):
    """(bundle, Budget Analyst report, Planner report) from the recorded research."""
    b = bundle(**trip)
    report = budget_report(b, picks)
    ctx = PlanningContext(report, b, recorded_lookup())
    return b, report, build_itinerary(ctx, assign(days, unscheduled))


def verify(it=None, *, b=None, report=None, planner=None, travel=True, **kw):
    """verify_itinerary with the recorded travel times, unless travel= says otherwise."""
    lookup = recorded_lookup() if travel is True else (travel or None)
    return verify_itinerary(it, b, budget_report=report, planner_report=planner, travel_lookup=lookup, **kw)


def clean():
    b, report, planner = plan()
    return b, report, planner, planner.itinerary


def edit(it: Itinerary, place_id: str, **changes) -> Itinerary:
    data = it.model_dump()
    for day in data["days"]:
        for a in day["activities"]:
            if a["place_id"] == place_id:
                a.update(changes)
    return Itinerary.model_validate(data)


def move(it: Itinerary, place_id: str, to: dt.date, start: str, end: str) -> Itinerary:
    data = it.model_dump()
    act = None
    for day in data["days"]:
        for a in list(day["activities"]):
            if a["place_id"] == place_id:
                day["activities"].remove(a)
                act = a
    act.update(start_time=start, end_time=end, travel_minutes_from_previous=None)
    day = next(d for d in data["days"] if d["date"] == to)
    day["activities"].append(act)
    day["activities"].sort(key=lambda a: a["start_time"])
    return Itinerary.model_validate(data)


def add(it: Itinerary, date: dt.date, activity: Activity) -> Itinerary:
    data = it.model_dump()
    next(d for d in data["days"] if d["date"] == date)["activities"].append(activity.model_dump())
    return Itinerary.model_validate(data)


def remove(it: Itinerary, place_id: str) -> Itinerary:
    data = it.model_dump()
    for day in data["days"]:
        day["activities"] = [a for a in day["activities"] if a["place_id"] != place_id]
    return Itinerary.model_validate(data)


def place(b, place_id):
    return next(p for p in b.places if p.place_id == place_id)


def hours_lookup(b, *, mode="live", overrides=None, asked=None):
    """A scripted get_opening_hours: the research listing, as the tool returns it, saying where it came from."""
    by_name = {p.name: p for p in b.places}

    def lookup(name):
        if asked is not None:
            asked.append(name)
        p = by_name.get(name)
        if p is None:
            return {"ok": False, "error": {"code": "place_not_found", "message": f"no listing called {name}"}}
        result = {"ok": True, "id": p.place_id, "name": p.name, "known": p.hours is not None,
                  "hours": copy.deepcopy(p.hours), "price": copy.deepcopy(p.price), "synthetic": p.synthetic,
                  "citation": p.citation, "source_url": p.source_url, "mode": mode}
        result.update((overrides or {}).get(p.place_id, {}))
        return result
    return lookup


def live_travel(**extra):
    """The recorded travel times, scripted as if the tool had answered live."""
    rec = recorded_lookup()

    def lookup(a, b):
        r = rec(a, b)
        if r.get("ok") is not False:
            r.update(mode="live", **extra)
        return r
    return lookup


def codes(report, severity=None):
    return {i.code for i in report.findings if severity is None or i.severity == severity}


def only(report, code):
    found = report.by_code(code)
    assert found, f"no {code} in {sorted(report.codes)}"
    return found


# ------------------------------------------------------------------ the schema
def test_code_catalogue_is_complete_and_consistent():
    assert set(CODES) == set(get_args(IssueCode))
    actions = set(get_args(RevisionAction))
    for code, (check, severity, action) in CODES.items():
        assert check in CHECK_ORDER and severity in ("error", "unknown", "warning") and action in actions, code
    with pytest.raises(ValidationError):  # a code cannot change its check or severity
        VerificationIssue(code="closed_on_date", check="opening_hours", severity="unknown", explanation="x",
                          recommended_action="move_activity")


def test_a_report_cannot_claim_verified_without_a_fresh_pass():
    checks = [CheckResult(check="opening_hours", status="UNKNOWN", summary="x")]
    with pytest.raises(ValidationError, match="verified=True"):
        VerificationReport(status="UNKNOWN", verified=True, evidence_basis="fresh", checks=checks)
    passed = [CheckResult(check="opening_hours", status="PASS", summary="x")]
    with pytest.raises(ValidationError, match="verified=True"):
        VerificationReport(status="PASS", verified=True, evidence_basis="recorded", checks=passed,
                           verified_checks=["opening_hours"])
    with pytest.raises(ValidationError, match="worst"):
        VerificationReport(status="PASS", evidence_basis="none", checks=checks)


def test_freshness_needs_a_live_result_that_is_not_stale():
    assert freshness_of({"ok": True, "mode": "live"}) == "fresh"
    assert freshness_of({"ok": True, "mode": "live", "stale": True}) == "recorded"
    assert freshness_of({"ok": True, "mode": "replay"}) == "recorded"
    assert freshness_of({"ok": True}) == "recorded"  # a result that does not say where it came from


# ------------------------------------------------------------------ a correct itinerary
def test_correct_itinerary_passes_on_recorded_data_but_is_not_verified():
    b, report, planner, it = clean()
    assert planner.status == "complete"
    out = verify(b=b, report=report, planner=planner)
    assert out.status == "PASS", [c.summary for c in out.checks if c.status != "PASS"]
    assert out.issues == [] and out.unknowns == [] and out.revision_targets == []
    assert out.verified is False and out.evidence_basis == "recorded"  # replay data: never "freshly verified"
    assert out.verified_checks == CHECK_ORDER
    assert any("not freshly verified" in n for n in out.notes)
    assert out.budget.known_total == planner.budget.known_total and out.budget.status == "within_budget"
    assert {TOPKAPI, "osrm-foot-hagia-sophia-topkapi-palace"} <= {c["id"] for c in out.citations}


def test_correct_itinerary_with_fresh_lookups_is_verified():
    b, report, planner, it = clean()
    out = verify(it, b=b, report=report, planner=planner, travel=live_travel(), hours_lookup=hours_lookup(b))
    assert out.status == "PASS" and out.evidence_basis == "fresh" and out.verified is True
    tools = {c["tool"] for c in out.tool_evidence}
    assert tools == {"get_opening_hours", "travel_time"}
    assert all(c["ok"] and c["freshness"] == "fresh" for c in out.tool_evidence)


def test_replayed_or_stale_lookups_are_never_reported_as_fresh():
    b, report, planner, it = clean()
    replay = verify(it, b=b, report=report, planner=planner, hours_lookup=hours_lookup(b, mode="replay"))
    assert replay.status == "PASS" and replay.verified is False and replay.evidence_basis == "recorded"
    stale = verify(it, b=b, report=report, travel=live_travel(stale=True), hours_lookup=hours_lookup(b))
    assert stale.evidence_basis == "mixed" and stale.verified is False  # fresh hours, stale routes


def test_the_planners_status_is_not_trusted():
    b, report, planner, it = clean()
    out = verify(it, b=b, report=report)  # no Planner report at all: the itinerary alone is checked
    assert out.status == "PASS" and "planner_report" not in [c.check for c in out.checks]
    assert any("No Planner report" in n for n in out.notes)


def test_a_trip_store_dict_is_checked_including_its_stated_total():
    b, report, planner, it = clean()
    store = it.to_trip_store()  # no place ids, no cost_status: names and amounts only
    assert verify(store, b=b, report=report).status == "PASS"
    wrong = {**store, "estimated_total": store["estimated_total"] + 50}
    issue = only(verify(wrong, b=b, report=report), "budget_total_mismatch")[0]
    assert issue.severity == "error" and issue.recommended_action == "recalculate_budget"


# ------------------------------------------------------------------ opening hours
def test_a_venue_closed_on_the_visit_date_fails():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")  # Topkapı is closed on Tuesdays
    out = verify(bad, b=b, report=report, planner=planner)
    issue = only(out, "closed_on_date")[0]
    assert (issue.date, issue.place_id, issue.severity, issue.recommended_action) == (TUE, TOPKAPI, "error",
                                                                                      "move_activity")
    assert issue.evidence[0].freshness == "recorded" and "closed" in issue.evidence[0].detail
    assert out.status == "FAIL" and out.check("opening_hours").status == "FAIL" and not out.verified
    assert out.check("planner_report").codes == ["planner_itinerary_differs"]  # a revised plan, not the Planner's


def test_a_fresh_lookup_overrides_the_recorded_hours():
    b, report, planner, it = clean()
    now_closed = {HAGIA: {"hours": {"raw": "Closed for restoration", "parsed": True, "temporarily_closed": True}}}
    out = verify(it, b=b, report=report, planner=planner, travel=live_travel(),
                 hours_lookup=hours_lookup(b, overrides=now_closed))
    assert {"closed_on_date", "hours_changed"} <= codes(out) and out.status == "FAIL"
    issue = only(out, "closed_on_date")[0]
    assert issue.place_id == HAGIA and issue.evidence[0].source == "get_opening_hours"
    assert issue.evidence[0].freshness == "fresh"


def test_a_visit_outside_the_listed_hours_fails():
    b, report, planner, it = clean()
    bad = edit(remove(it, CISTERN), ALI_USTA, start_time="19:00", end_time="20:00")
    bad = edit(bad, TOPKAPI, start_time="16:30", end_time="18:00")  # Topkapı: 09:00-17:00
    out = verify(bad, b=b, report=report)
    issue = only(out, "outside_opening_hours")[0]
    assert issue.place_id == TOPKAPI and issue.recommended_action == "reschedule_activity"


def test_unknown_hours_are_unknown_not_failed():
    b, report, planner, it = clean()
    b = b.model_copy(deep=True)
    place(b, DOLMABAHCE).hours = None
    out = verify(it, b=b, report=report)
    issue = only(out, "hours_unknown")[0]
    assert issue.severity == "unknown" and issue.recommended_action == "lookup_opening_hours"
    assert out.issues == [] and out.status == "UNKNOWN" and out.check("opening_hours").status == "UNKNOWN"


def test_a_failed_or_mismatched_hours_lookup_falls_back_to_recorded_data():
    b, report, planner, it = clean()

    def broken(name):
        raise TimeoutError("no answer")
    out = verify(it, b=b, report=report, hours_lookup=broken)
    assert "hours_lookup_failed" in codes(out, "warning") and out.status == "PASS" and out.verified is False
    other = {HAGIA: {"id": I("galata-tower")}}
    mismatch = verify(it, b=b, report=report, hours_lookup=hours_lookup(b, overrides=other))
    assert only(mismatch, "hours_source_mismatch")[0].place_id == HAGIA and mismatch.status == "UNKNOWN"


# ------------------------------------------------------------------ schedule and travel
def test_overlapping_activities_fail():
    b, report, planner, it = clean()
    bad = edit(it, CISTERN, start_time="12:00", end_time="13:30")  # Topkapı ends 12:15
    out = verify(bad, b=b, report=report)
    issue = only(out, "activity_overlap")[0]
    assert (issue.date, issue.place_id, issue.related_place_id) == (MON, CISTERN, TOPKAPI)
    assert issue.recommended_action == "reschedule_activity" and out.check("schedule").status == "FAIL"


def test_a_travel_time_longer_than_the_gap_fails():
    b, report, planner, it = clean()
    bad = edit(it, TOPKAPI, start_time="10:35", end_time="12:05")  # Hagia Sophia ends 10:30; the walk is 15 minutes
    out = verify(bad, b=b, report=report)
    issue = only(out, "insufficient_travel_gap")[0]
    assert (issue.place_id, issue.related_place_id, issue.severity) == (TOPKAPI, HAGIA, "error")
    assert issue.evidence[0].source == "travel_time" and issue.evidence[0].citation_id == \
        "osrm-foot-hagia-sophia-topkapi-palace"
    assert "travel_minutes_mismatch" not in codes(out)  # the stated 15 minutes is the measured time


def test_activities_outside_the_planning_day_fail():
    b, report, planner, it = clean()
    bad = edit(it, DOLMABAHCE, start_time="08:00", end_time="09:30")  # planning day 09:00-21:00
    assert only(verify(bad, b=b, report=report), "outside_planning_day")[0].place_id == DOLMABAHCE


def test_an_unknown_route_is_unknown_with_a_lookup_target():
    b, report, planner, it = clean()
    out = verify(it, b=b, report=report, travel=None)  # no travel source and no Planner legs
    unknown = only(out, "route_unknown")
    assert len(unknown) == 3 and all(i.severity == "unknown" for i in unknown)
    assert out.issues == [] and out.status == "UNKNOWN"
    assert {t.action for t in out.revision_targets} == {"lookup_travel_time"}
    assert all(t.kind == "evidence_lookup" for t in out.revision_targets)


def test_recorded_planner_legs_are_used_when_the_lookup_fails():
    b, report, planner, it = clean()

    def failing(a, b_):
        return {"ok": False, "error": {"code": "upstream_unavailable", "message": "down"}}
    out = verify(it, b=b, report=report, planner=planner, travel=failing)
    assert "route_lookup_failed" in codes(out, "warning") and "route_unknown" not in codes(out)
    assert out.status == "PASS" and out.evidence_basis == "recorded"


def test_lookups_are_cached_and_bounded():
    calls = []
    lookups = FreshLookups(None, lambda a, b: calls.append((a, b)) or {"ok": True, "duration_min": 5}, max_lookups=1)
    assert lookups.travel("A", "B") == lookups.travel("A", "B") and calls == [("A", "B")]
    assert lookups.travel("B", "C") is None and lookups.skipped == 1
    b, report, planner, it = clean()
    out = verify(it, b=b, report=report, max_lookups=0)
    assert out.tool_evidence == [] and "route_unknown" in codes(out)
    assert any("lookup limit" in n for n in out.notes)


# ------------------------------------------------------------------ walking
def test_a_walk_over_the_limit_fails_and_an_unmeasured_one_is_unknown():
    b, report, planner, it = clean()
    tight = bundle(max_walk_minutes=14)  # Hagia Sophia -> Topkapı is 15 minutes
    out = verify(it, b=tight, report=report)
    issue = only(out, "walk_over_limit")[0]
    assert (issue.place_id, issue.related_place_id, issue.recommended_action) == (TOPKAPI, HAGIA, "reorder_day")
    unmeasured = verify(it, b=tight, report=report, travel=None)
    assert "walk_unverified" in codes(unmeasured, "unknown") and "walk_over_limit" not in codes(unmeasured)


def test_a_driving_time_does_not_pass_a_walking_limit():
    b, report, planner, it = clean()
    out = verify(it, b=bundle(max_walk_minutes=60), report=report, travel=live_travel(profile="driving"))
    assert "walk_unverified" in codes(out) and out.check("walking").status == "UNKNOWN"


# ------------------------------------------------------------------ places
def test_missing_and_duplicate_required_places():
    b, report, planner, it = clean()
    missing = verify(remove(it, TOPKAPI), b=b, report=report)
    issue = only(missing, "required_place_missing")[0]
    assert (issue.place_id, issue.recommended_action) == (TOPKAPI, "add_required_place")
    target = next(t for t in missing.revision_targets if t.place_id == TOPKAPI)
    assert target.kind == "plan_change" and target.codes == ["required_place_missing"]

    twice = add(it, WED, it.days[0].activities[1].model_copy(update={"start_time": "11:00", "end_time": "12:30",
                                                                      "travel_minutes_from_previous": None}))
    issue = only(verify(twice, b=b, report=report), "duplicate_place")[0]
    assert (issue.date, issue.place_id, issue.recommended_action) == (WED, TOPKAPI, "remove_activity")


def test_a_required_place_closed_on_every_date_is_the_travellers_decision():
    b, report, planner = plan(days={TUE: [HAGIA, CISTERN]}, unscheduled={TOPKAPI: "Closed on Tuesdays"},
                              picks={HAGIA: ("include", 1), CISTERN: ("include", 2)},
                              start_date="2026-11-03", end_date="2026-11-03")
    assert planner.status == "infeasible"
    out = verify(b=b, report=report, planner=planner)
    issue = only(out, "required_place_missing")[0]
    assert issue.recommended_action == "ask_traveller"
    assert any(t.kind == "traveller_decision" for t in out.revision_targets)
    assert "planner_reported_infeasible" in codes(out, "warning")
    assert "planner_status_inconsistent" not in codes(out)


def test_an_unresolved_required_place_is_unknown():
    b = bundle(required_places=["Topkapi Palace", "Atlantis Museum"])
    report = budget_report(b, CLEAN_PICKS)
    ctx = PlanningContext(report, b, recorded_lookup())
    out = verify(build_itinerary(ctx, assign(CLEAN)).itinerary, b=b, report=report)
    assert only(out, "required_place_unresolved")[0].severity == "unknown" and out.issues == []


def test_synthetic_excluded_and_unselected_places_fail():
    b, report, planner, it = clean()
    synthetic, hamam = place(b, SYNTHETIC), place(b, HAMAM)
    bad = add(it, TUE, Activity(name=synthetic.name, place_id=SYNTHETIC, start_time="12:00", end_time="13:00",
                                estimated_cost=10.0))  # its link is about:synthetic-overlay/..., not a web source
    bad = add(bad, WED, report.to_activity(GALATA, start_time="12:00", end_time="13:30"))  # optional only
    bad = add(bad, WED, Activity(name=hamam.name, place_id=HAMAM, source_url=hamam.source_url, start_time="15:00",
                                 end_time="16:00", estimated_cost=335.79))  # excluded
    bad = add(bad, MON, Activity(name="Invented Palace", place_id=I("invented-palace"), start_time="18:00",
                                 end_time="19:00", estimated_cost=5.0))
    out = verify(bad, b=b, report=report)
    found = {i.code: i.place_id for i in out.issues if i.check == "places"}
    assert found == {"synthetic_place": SYNTHETIC, "not_selected_place": GALATA, "excluded_place": HAMAM,
                     "place_not_in_research": I("invented-palace")}
    assert all(i.recommended_action == "remove_activity" for i in out.issues if i.check == "places")


def test_without_the_budget_report_exclusion_is_unknown_but_synthetic_still_fails():
    b, report, planner, it = clean()
    synthetic = place(b, SYNTHETIC)
    bad = add(it, TUE, Activity(name=synthetic.name, place_id=SYNTHETIC, start_time="12:00", end_time="13:00",
                                estimated_cost=10.0))  # its link is about:synthetic-overlay/..., not a web source
    out = verify(bad, b=b)
    assert "exclusion_lists_unavailable" in codes(out, "unknown") and "synthetic_place" in codes(out, "error")


def test_selected_places_left_out_are_reported():
    b, report, planner, it = clean()
    silent = verify(remove(it, DOLMABAHCE), b=b, report=report)
    issue = only(silent, "selected_place_unaccounted")[0]
    assert issue.place_id == DOLMABAHCE and issue.recommended_action == "restore_selected_place"


# ------------------------------------------------------------------ budget and costs
def test_an_incorrect_activity_cost_fails_with_a_reprice_target():
    b, report, planner, it = clean()
    bad = edit(it, HAGIA, estimated_cost=5.0)  # Python's pricing: 55.97 USD for two
    out = verify(bad, b=b, report=report)
    issue = only(out, "activity_cost_mismatch")[0]
    assert issue.place_id == HAGIA and issue.recommended_action == "reprice_activity"
    assert issue.evidence[0].freshness == "computed" and "55.97" in issue.explanation


def test_incorrect_planner_totals_fail():
    b, report, planner, it = clean()
    budget = planner.budget.model_copy(update={"known_total": Decimal("10.00"), "status": "over_budget"})
    days = [d.model_copy(update={"known_total": Decimal("1.00")}) if d.date == MON else d for d in budget.days]
    tampered = planner.model_copy(update={"budget": budget.model_copy(update={"days": days})})
    out = verify(b=b, report=report, planner=tampered)
    assert {"budget_total_mismatch", "budget_verdict_mismatch", "day_total_mismatch"} <= codes(out, "error")
    assert out.budget.known_total == planner.budget.known_total  # recalculated, not copied
    assert next(i for i in out.issues if i.code == "day_total_mismatch").date == MON


def test_over_budget_fails():
    b, report, planner, it = clean()
    out = verify(it.model_copy(update={"budget_total": 100.0}), b=bundle(budget_total=100), report=report)
    issue = only(out, "over_budget")[0]
    assert issue.recommended_action == "reduce_cost" and out.check("budget").status == "FAIL"


def test_currency_travellers_and_budget_must_match_the_request():
    b, report, planner, it = clean()
    out = verify(it.model_copy(update={"currency": "EUR", "travellers": 3, "budget_total": 900.0}), b=b,
                 report=report)
    assert {"currency_mismatch", "travellers_mismatch", "budget_limit_mismatch"} <= codes(out, "error")
    assert all(i.recommended_action == "align_with_request" for i in out.issues if i.check == "budget"
               and i.code != "budget_total_mismatch")


def test_unknown_costs_and_routes_are_unknown_never_failures():
    b, report, planner = plan(days=VALID, picks=PICKS)
    out = verify(b=b, report=report, planner=planner)
    assert out.issues == [] and out.status == "UNKNOWN" and out.verified is False
    assert {"cost_unknown", "budget_undetermined", "route_unknown"} <= codes(out, "unknown")
    assert only(out, "cost_unknown")[0].place_id == BAZAAR
    assert {i.place_id for i in out.by_code("cost_range")} == {CISTERN, DOLMABAHCE, PERA}
    route = only(out, "route_unknown")[0]
    assert (route.date, route.place_id, route.related_place_id) == (WED, PERA, DOLMABAHCE)
    assert {(t.action, t.place_id) for t in out.revision_targets} == {
        ("lookup_travel_time", PERA), ("lookup_price", BAZAAR), ("lookup_price", None)}


# ------------------------------------------------------------------ evidence
def test_missing_source_evidence_is_unknown():
    b, report, planner, it = clean()
    out = verify(edit(it, HAGIA, source_url=None), b=b, report=report)
    issue = only(out, "source_missing")[0]
    assert issue.severity == "unknown" and issue.recommended_action == "add_source"
    assert out.issues == [] and out.status == "UNKNOWN"
    b2 = b.model_copy(deep=True)
    place(b2, CISTERN).citation = None
    assert only(verify(it, b=b2, report=report), "citation_missing")[0].place_id == CISTERN


def test_research_tool_failures_are_carried_as_warnings():
    b, report, planner, it = clean()
    b2 = b.model_copy(update={"failures": [*b.failures, ToolFailure(tool="find_restaurants", code="timeout")]})
    out = verify(it, b=b2, report=report)
    assert "research_tool_failure" in codes(out, "warning")


# ------------------------------------------------------------------ the Planner's report
def test_a_partial_planner_report_is_checked_not_trusted():
    b, report, planner = plan(days={**CLEAN, WED: []}, unscheduled={DOLMABAHCE: "Too far from the rest"})
    assert planner.status == "partial"
    out = verify(b=b, report=report, planner=planner)
    assert {"planner_reported_partial", "selected_place_unscheduled"} <= codes(out, "warning")
    assert "planner_status_inconsistent" not in codes(out) and out.check("planner_report").status == "PASS"
    lying = planner.model_copy(update={"status": "complete"})
    issue = only(verify(b=b, report=report, planner=lying), "planner_status_inconsistent")[0]
    assert issue.severity == "error" and "'complete'" in issue.explanation


def test_a_planner_report_without_an_itinerary_fails():
    b, report, planner = plan()
    out = verify(b=b, report=report, planner=planner.model_copy(update={"itinerary": None, "status": "infeasible"}))
    assert out.status == "FAIL" and [c.check for c in out.checks] == ["itinerary", "planner_report"]
    assert {"invalid_itinerary", "planner_no_itinerary"} == codes(out)
    assert {t.action for t in out.revision_targets} == {"rebuild_itinerary"}


# ------------------------------------------------------------------ dates and times
def test_invalid_dates_and_times():
    b, report, planner, it = clean()
    store = it.to_trip_store()
    store["days"][0]["activities"][0]["start_time"] = "25:00"
    out = verify(store, b=b, report=report)
    assert out.status == "FAIL" and codes(out) == {"invalid_itinerary"}
    outside = Itinerary.model_validate({**it.model_dump(), "days": [*it.model_dump()["days"],
        {"date": dt.date(2026, 11, 5), "activities": [it.days[2].activities[0].model_dump()]}]})
    issue = only(verify(outside, b=b, report=report), "date_outside_trip")[0]
    assert issue.date == dt.date(2026, 11, 5) and issue.recommended_action == "move_activity"


def test_untimed_and_missing_dates_are_reported():
    b, report, planner, it = clean()
    untimed = edit(it, SULEYMANIYE, start_time=None, end_time=None)
    issue = only(verify(untimed, b=b, report=report), "activity_untimed")[0]
    assert issue.severity == "unknown" and issue.place_id == SULEYMANIYE
    data = it.model_dump()
    data["days"] = [d for d in data["days"] if d["date"] != TUE]
    assert only(verify(Itinerary.model_validate(data), b=b, report=report), "trip_date_missing")[0].date == TUE
    bypass = Itinerary.model_construct(**{**it.__dict__, "days": [it.days[0].model_copy(update={"activities": [
        Activity.model_construct(**{**it.days[0].activities[0].__dict__, "end_time": "08:00"})]})]})
    assert "invalid_activity_time" in codes(verify(bypass, b=b, report=report), "error")


def test_revision_targets_name_the_place_date_and_codes():
    b, report, planner, it = clean()
    bad = move(it, TOPKAPI, TUE, "11:00", "12:30")
    bad = edit(bad, HAGIA, estimated_cost=5.0)
    out = verify(bad, b=b, report=report)
    targets = {(t.action, t.date, t.place_id): t for t in out.revision_targets}
    assert targets[("move_activity", TUE, TOPKAPI)].codes == ["closed_on_date"]
    assert targets[("reprice_activity", MON, HAGIA)].codes == ["activity_cost_mismatch"]
    assert all(t.kind == "plan_change" for t in targets.values())
    assert all(i.recommended_action in get_args(RevisionAction) for i in out.findings)
