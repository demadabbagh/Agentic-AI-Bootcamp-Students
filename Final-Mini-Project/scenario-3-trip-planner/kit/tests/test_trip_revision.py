"""The traveller's change requests: interpret, targeted edit, verification, session memory and approval safety.

Offline: the Istanbul research fixture, the real Python edit / Budget Analyst fitting / Planner scheduling /
verify_itinerary, recorded travel times. The LLM parts are replaced: the Revision Agent's choice by a scripted
`decide`, the Verifier agent by Python's own verify_itinerary (status and verified are Python's either way).
"""
import copy
import datetime as dt
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CREWAI_STORAGE_DIR", "trip-planner-tests")
sys.path.insert(0, str(Path(__file__).resolve().parent / "planning"))

from app.main import app  # noqa: E402
from app.memory import get_session, sessions  # noqa: E402
from app.revision import plan_state_from, run_trip_revision  # noqa: E402
from planning.day_balance import propose  # noqa: E402
from planning.itinerary_planner import PlanningContext, build_itinerary  # noqa: E402
from planning.user_revision import interpret  # noqa: E402
from planning.verification import verify_itinerary  # noqa: E402
from research_fixtures import bundle, recorded_lookup  # noqa: E402
from test_itinerary_planner import budget_report  # noqa: E402

client = TestClient(app)
TRIP_INFO = {"destination": "Istanbul", "interests": ["History & Culture", "Street food"]}


def plan_state(**trip):
    b = bundle(**trip)  # 3 days, 2 travellers, 700 USD
    report = budget_report(b)
    ctx = PlanningContext(report, b, recorded_lookup())
    planner = build_itinerary(ctx, propose(ctx))
    return plan_state_from(b, report, planner, planner.itinerary)


def python_verification(itinerary, research_bundle, llm, *, budget_report=None, planner_report=None,
                        hours_lookup=None, travel_lookup=None):
    report = verify_itinerary(itinerary, research_bundle, budget_report=budget_report, planner_report=planner_report,
                              travel_lookup=travel_lookup)
    return {"status": "needs_revision", "verified": report.verified, "message": "The itinerary could not be fully "
            "verified. No approval is allowed.", "itinerary_model": itinerary,
            "verifier_report": SimpleNamespace(verification=report, decided_by="python"), "revision_report": None}


def revise(session, request, *, decide_picks=None, decide_error=None, base_version=None, verify=python_verification):
    def make_decide(llm, b, report, run):
        def decide(points):
            run.called = True
            if decide_error:
                raise decide_error
            return {p.id: (decide_picks(p) if decide_picks else p.default) for p in points}
        return decide
    with patch("app.verification_adapter.run_verification_pipeline", side_effect=verify), \
            patch("app.revision.make_decide", make_decide):
        return run_trip_revision(session, request, TRIP_INFO, base_version=base_version, llm=object(),
                                 lookups=(None, recorded_lookup()))


def acts(itinerary_dict, n):
    return [(a["name"], a.get("start_time"), a.get("end_time"), a.get("estimated_cost"))
            for a in itinerary_dict["days"][n]["activities"]]


@pytest.fixture()
def session():
    sessions.clear()
    s = get_session("rev")
    s["plan_state"] = plan_state()
    s["itinerary"] = s["plan_state"].itinerary.to_trip_store()
    return s


# ------------------------------------------------------------------ budget
def test_reduce_a_700_budget_by_20_percent_to_a_560_target_keeping_every_day(session):
    before = copy.deepcopy(session["itinerary"])
    out = revise(session, "Reduce my budget by 20%.")
    assert out["status"] == "verification_required" and out["revision"]["outcome"] == "revised"
    assert (out["revision"]["budget_before"], out["revision"]["budget_after"]) == ("700.00", "560.00")
    assert out["itinerary"]["budget_total"] == 560.0
    assert out["revision"]["changes"][0] == "Budget changed from 700.00 to 560.00 USD."
    assert out["revision"]["changes"][1].startswith("The plan's known costs (315.32 USD) already fit the new budget")
    assert out["revision"]["unchanged_dates"] == ["2026-11-02", "2026-11-03", "2026-11-04"]  # 315 known: fits
    for n in range(3):
        assert acts(out["itinerary"], n) == acts(before, n)  # same places, times and costs
    assert session["plan_state"].bundle.trip.budget_total == 560  # the session's plan carries the new target
    assert out["budget"]["budget_total"] == "560.00"


def test_a_budget_the_plan_does_not_fit_changes_only_what_it_must(session):
    before = copy.deepcopy(session["itinerary"])
    out = revise(session, "Set my budget to 200")
    r = out["revision"]
    assert "Day 2 (Tue 03 Nov): removed Basilica Cistern." in r["changes"]
    assert "Day 3 (Wed 04 Nov): removed Dolmabahce Palace." in r["changes"]
    assert r["unchanged_dates"] == ["2026-11-02"] and acts(out["itinerary"], 0) == acts(before, 0)
    assert float(out["budget"]["planned_known_total"]) <= 200
    names = [a["name"] for d in out["itinerary"]["days"] for a in d["activities"]]
    assert "Topkapı Palace" in names  # the traveller's required place is never cut


def test_a_budget_below_the_required_places_is_a_conflict_not_a_silent_cut(session):
    out = revise(session, "Set my budget to 50")
    assert out["status"] == "revision_not_feasible" and "places you asked to visit already cost" in out["message"]
    assert session["plan_state"].bundle.trip.budget_total == 700  # nothing changed


def test_a_budget_that_would_empty_a_day_is_refused_with_alternatives():
    # Found live (Istanbul, 20 USD): fitting the budget emptied Day 1 and was accepted with only a warning.
    sessions.clear()
    s = get_session("no-required")
    s["plan_state"] = plan_state(required_places=[])
    state = s["plan_state"]
    out = revise(s, "Set my budget to 20")
    assert out["status"] == "revision_not_feasible" and "itinerary" not in out
    assert "with no sightseeing" in out["message"] and "You could keep a budget of at least" in out["message"]
    assert "free alternatives" in out["message"] and s["plan_state"] is state


def test_a_place_the_traveller_removed_never_comes_back(session):
    # Found live: after "Remove the museum on Day 2", a budget change swapped that museum back in.
    first = revise(session, "Remove the museum on Day 3")
    for request in ("Add more history activities", "Set my budget to 200", "Make the trip cheaper"):
        out = revise(session, request, base_version=session["plan_state"].version)
        names = [a["name"] for d in (out.get("itinerary") or first["itinerary"])["days"] for a in d["activities"]]
        assert "Pera Museum" not in names, request


# ------------------------------------------------------------------ activities
def test_remove_a_specific_attraction(session):
    out = revise(session, "Remove the museum on Day 3")
    names = [a["name"] for d in out["itinerary"]["days"] for a in d["activities"]]
    assert "Pera Museum" not in names and len(names) == 7
    assert out["revision"]["changes"][0] == "Day 3 (Wed 04 Nov): removed Pera Museum."
    assert out["revision"]["unchanged_dates"] == ["2026-11-02", "2026-11-03"]
    excluded = {i.place_id: i.reason for i in session["plan_state"].budget_report.excluded}
    assert excluded["wv-istanbul-pera-museum"] == "Removed at your request"


def test_replace_with_a_researched_alternative_chosen_by_the_revision_agent(session):
    out = revise(session, "Replace the shopping activity with a historical attraction",
                 decide_picks=lambda p: p.alternatives[-1].id)
    r = out["revision"]
    assert r["decided_by"] == "agent" and r["decision_points"] >= 1
    added = [a["name"] for a in out["itinerary"]["days"][0]["activities"]]
    assert "Grand Bazaar" not in added
    research = {p.name for p in session["plan_state"].bundle.places}
    assert all(n in research for n in added)  # only researched places: nothing invented
    assert r["changes"][0].startswith("Day 1 (Mon 02 Nov): replaced Grand Bazaar with ")


def test_a_failed_revision_agent_falls_back_to_pythons_choice(session):
    out = revise(session, "Replace the shopping activity with a historical attraction",
                 decide_error=RuntimeError("provider down"))
    assert out["revision"]["outcome"] == "revised" and out["revision"]["decided_by"] == "python_fallback"
    assert "provider down" in out["revision"]["agent_error"]


def test_walking_preference_becomes_the_trips_limit(session):
    out = revise(session, "I don't want to walk more than 15 minutes")
    assert session["plan_state"].bundle.trip.max_walk_minutes == 15
    assert out["revision"]["changes"][0].startswith("Walking limit 15 minutes:")
    codes = {f["code"] for f in out["verification"]["findings"]}
    assert "walk_over_limit" in codes  # no car time recorded: the long walks are flagged, never hidden


def test_start_a_day_later(session):
    out = revise(session, "Start Day 2 at 10 AM")
    assert out["itinerary"]["days"][1]["activities"][0]["start_time"] == "10:00"
    assert out["revision"]["unchanged_dates"] == ["2026-11-02", "2026-11-04"]


# ------------------------------------------------------------------ questions, refusals, memory
@pytest.mark.parametrize("request_text, expected", [
    ("Change it", "not sure what to change"),
    ("Remove this", "Which activity should I remove?"),
    ("Make it less crowded", "Which day should be lighter?"),
])
def test_ambiguous_requests_get_a_specific_question_and_change_nothing(session, request_text, expected):
    version = session["plan_state"].version
    out = revise(session, request_text)
    assert out["status"] == "needs_clarification" and expected in out["message"]
    assert "itinerary" not in out and session["plan_state"].version == version


@pytest.mark.parametrize("request_text, expected", [
    ("Move the cistern to Day 9", "The trip has 3 days"),
    ("Remove Topkapi Palace", "is a place you asked to visit"),
    ("Add more nature activities", "found no other nature place"),
])
def test_impossible_or_conflicting_requests_are_explained(session, request_text, expected):
    out = revise(session, request_text)
    assert out["status"] == "revision_not_feasible" and expected in out["message"]
    assert "itinerary" not in out


def test_no_plan_in_the_session_is_said_explicitly():
    sessions.clear()
    out = run_trip_revision(get_session("empty"), "Reduce my budget by 20%", TRIP_INFO)
    assert out["status"] == "needs_clarification" and out["revision"]["outcome"] == "no_plan"


def test_a_change_made_on_an_older_version_is_refused(session):
    old = session["plan_state"].version
    revise(session, "Reduce my budget by 20%")
    out = revise(session, "Remove the museum on Day 3", base_version=old)
    assert out["revision"]["outcome"] == "stale_plan" and "not the latest version" in out["message"]


def test_a_failing_verification_leaves_the_previous_plan_untouched(session):
    state = session["plan_state"]

    def broken(*a, **k):
        raise RuntimeError("Verifier unavailable")
    with pytest.raises(RuntimeError):
        revise(session, "Reduce my budget by 20%", verify=broken)
    assert session["plan_state"] is state


def test_a_change_that_leaves_the_trip_too_thin_is_refused(session):
    state = session["plan_state"]
    thin = SimpleNamespace(sufficient=False, insufficient_message=lambda: "Day 2 would have no sightseeing.")
    with patch("app.coverage.assess_coverage", return_value=thin):
        out = revise(session, "Remove the museum on Day 3")
    assert out["status"] == "revision_not_feasible" and "too thin" in out["message"] and "itinerary" not in out
    assert session["plan_state"] is state


def test_consecutive_revisions_build_on_the_latest_plan(session):
    first = revise(session, "Reduce my budget by 20%")
    second = revise(session, "Remove the museum on Day 3", base_version=first["plan_version"])
    assert second["itinerary"]["budget_total"] == 560.0  # the first change is kept
    assert second["revision"]["revision_number"] == 2
    assert [h["request"] for h in session["plan_state"].history] == ["Reduce my budget by 20%",
                                                                      "Remove the museum on Day 3"]


def test_interpretation_reads_the_examples():
    st = plan_state()
    kinds = {r: [o.kind for o in interpret(r, st).ops] for r in [
        "Reduce my budget by 20%.", "Make the trip cheaper.", "Replace expensive attractions with free alternatives.",
        "Remove the museum on Day 3.", "Replace the shopping activity with a historical attraction.",
        "Move Hagia Sophia to Day 3.", "Start Day 2 at 10 AM.", "Make Day 3 less crowded.",
        "I don't want to walk more than 15 minutes.", "Use a taxi for long distances.",
        "Add more food experiences.", "I prefer cultural activities instead of shopping."]}
    assert [v[0] for v in kinds.values()] == ["set_budget", "cheaper", "free_alternatives", "remove", "replace",
                                             "move", "start_time", "lighten_day", "walk_limit", "use_rides", "add",
                                             "prefer"]


# ------------------------------------------------------------------ through /api/chat: approval safety
def test_a_revision_invalidates_the_old_approval_and_never_approves_an_unverified_plan(session):
    session["pending_action"] = {"action_id": "old-action", "type": "save_trip", "verification_status": "verified",
                                 "itinerary_hash": "x", "payload": {"itinerary": session["itinerary"]}}
    with patch("app.orchestrator.understand_trip_request", return_value={"intent": "revise_trip"}), \
            patch("app.verification_adapter.run_verification_pipeline", side_effect=python_verification), \
            patch("app.revision.create_planning_llm" if False else "app.planning_adapter.create_planning_llm",
                  return_value=object()), \
            patch("app.mcp_lookup_adapter.create_travel_lookup", return_value=recorded_lookup()), \
            patch("app.mcp_lookup_adapter.create_hours_lookup", return_value=None), \
            patch("app.revision.make_decide", lambda *a: (lambda points: {p.id: p.default for p in points})):
        r = client.post("/api/chat", json={"session_id": "rev", "message": "Reduce my budget by 20%",
                                           "revision": True, "base_version": session["plan_state"].version})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "verification_required"
    assert body["pending_action"] is None and body["revision"]["outcome"] == "revised"
    assert body["itinerary"]["budget_total"] == 560.0 and body["plan_version"] == session["plan_state"].version
    with patch.dict("os.environ", {"TRAVEL_TRAVELLER_ID": "TRV-101"}), patch("app.main.save_trip_to_store") as save:
        old = client.post("/api/approve", json={"session_id": "rev", "action_id": "old-action", "approved": True})
    # the outdated action can no longer be used (CONTRACT §3: a clear reply, nothing written)
    assert old.json()["status"] == "no_pending_action" and old.json()["result"] is None and not save.called


def test_a_question_keeps_the_pending_approval_of_the_plan_on_screen(session):
    pending = {"action_id": "keep-me", "type": "save_trip", "summary": "Save the plan.", "verification_status": "verified",
               "itinerary_hash": "x", "payload": {"itinerary": session["itinerary"]}}
    session["pending_action"] = pending
    with patch("app.orchestrator.understand_trip_request", return_value={"intent": "revise_trip"}):
        r = client.post("/api/chat", json={"session_id": "rev", "message": "Change it", "revision": True})
    assert r.json()["status"] == "needs_clarification" and r.json()["itinerary"] is None
    assert session["pending_action"] == pending


def test_the_change_box_never_starts_a_new_trip(session):
    with patch("app.orchestrator.understand_trip_request", return_value={"intent": "create_trip"}), \
            patch("app.orchestrator.run_research_pipeline") as research:
        r = client.post("/api/chat", json={"session_id": "rev", "message": "Change it", "revision": True})
    assert r.json()["status"] == "needs_clarification"
    research.assert_not_called()
