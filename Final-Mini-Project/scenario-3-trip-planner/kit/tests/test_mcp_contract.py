"""MCP tool-call reporting, the MCP write path and traveller memory, offline.

Only researcher.mcp_client._transport (the stdio send) is replaced: the allowlists, the per-request recorder,
the adapters, the endpoints and (for the planner) the genuine CrewAI agent loop with a scripted LLM all run as in
production. No network, no LLM cost. tests/test_mcp_integration.py runs the same paths against the real server.
"""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "planning"))  # the planning test fixtures

import researcher.mcp_client as mcp_client  # noqa: E402
from app import contract, traveller_memory  # noqa: E402
from app.main import app  # noqa: E402
from app.mcp_app_client import AppToolNotAllowed, call_app_tool  # noqa: E402
from app.mcp_lookup_adapter import create_hours_lookup, create_travel_lookup  # noqa: E402
from app.memory import sessions  # noqa: E402
from researcher.mcp_client import ToolNotAllowed, call_read_tool, recording, stage  # noqa: E402

client = TestClient(app)


class FakeServer:
    """Stands in for the stdio send only: answers per tool and remembers every call it received."""

    def __init__(self, answers=None, raise_for=()):
        self.answers = answers or {}
        self.raise_for = set(raise_for)
        self.calls = []
        self.prefs = {"TRV-101": {"dietary": ["vegetarian"], "max_walk_minutes": 25, "budget_style": "budget",
                                  "interests": ["history", "local food"], "home_currency": "USD",
                                  "travel_party": "1 adult"},
                      "TRV-102": {"dietary": ["halal", "nut allergy"], "max_walk_minutes": 15}}

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if name in self.raise_for:
            raise ConnectionError("the MCP server did not start")
        if name in self.answers:
            answer = self.answers[name]
            return answer(args) if callable(answer) else answer
        if name == "get_preferences":
            prefs = self.prefs.get(args["traveller_id"])
            return ({"ok": True, "traveller_id": args["traveller_id"], "preferences": dict(prefs)} if prefs is not None
                    else {"ok": False, "error": {"code": "traveller_not_found", "status": 404}})
        if name == "save_preferences":
            stored = self.prefs.setdefault(args["traveller_id"], {})
            stored.update(args["preferences"])
            return {"ok": True, "traveller_id": args["traveller_id"], "preferences": dict(stored)}
        if name == "save_trip":
            return {"ok": True, "trip_id": "TRIP-3001", "title": args["title"], "mode": "simulated"}
        return {"ok": True, "tool": name}

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(mcp_client, "_transport", fake)
    return fake


@pytest.fixture(autouse=True)
def clean_sessions():
    sessions.clear()
    yield
    sessions.clear()


# ------------------------------------------------------------------ the recorder
def test_every_mcp_call_is_recorded_with_its_outcome_and_no_arguments(server):
    server.answers["travel_time"] = {"ok": False, "error": {"code": "not_cached", "status": 404}}
    with recording() as calls, stage("researcher"):
        call_read_tool("find_attractions", {"city": "Amman"})
        call_read_tool("travel_time", {"from_place": "A", "to_place": "B", "city": "Amman"})
    assert [(c["tool"], c["ok"], c["agent"], c["via"]) for c in calls] == [
        ("find_attractions", True, "researcher", "mcp"), ("travel_time", False, "researcher", "mcp")]
    assert calls[1]["error"] == "not_cached"
    assert all(isinstance(c["ms"], int) and c["ms"] >= 0 for c in calls)
    assert all(set(c) <= {"tool", "ok", "ms", "agent", "via", "error"} for c in calls)  # no args, no results


def test_a_server_that_cannot_be_reached_is_recorded_as_failed_and_raised(server):
    server.raise_for = {"get_opening_hours"}
    with recording() as calls, pytest.raises(ConnectionError):
        call_read_tool("get_opening_hours", {"place_name": "X", "city": "Y"})
    assert calls[0]["ok"] is False and calls[0]["error"].startswith("mcp_unavailable")


def test_nothing_is_recorded_outside_a_request_and_nothing_leaks_into_the_next_one(server):
    call_read_tool("geocode", {"place": "Amman"})  # no recording open: nothing kept anywhere
    with recording() as first:
        call_read_tool("geocode", {"place": "Amman"})
    with recording() as second:
        pass
    assert len(first) == 1 and second == []


def test_concurrent_requests_record_only_their_own_calls(server):
    results = {}

    def request(name, count):
        with recording() as calls:
            for _ in range(count):
                call_read_tool("geocode", {"place": name})
                time.sleep(0.01)
            results[name] = list(calls)

    threads = [threading.Thread(target=request, args=(n, c)) for n, c in (("a", 2), ("b", 5), ("c", 3))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {n: len(c) for n, c in results.items()} == {"a": 2, "b": 5, "c": 3}


# ------------------------------------------------------------------ read-only boundary
@pytest.mark.parametrize("write_tool", ["save_trip", "save_preferences", "get_preferences", "list_trips"])
def test_agents_cannot_reach_application_tools(server, write_tool):
    with recording() as calls, pytest.raises(ToolNotAllowed):
        call_read_tool(write_tool, {})
    assert server.calls == [] and calls == []  # refused before anything reached the server


def test_the_application_path_only_allows_its_own_tools(server):
    with pytest.raises(AppToolNotAllowed):
        call_app_tool("find_attractions", {"city": "Amman"})
    assert server.calls == []


def test_no_agent_tool_list_contains_a_write_tool():
    from researcher.tools import make_tools
    assert {t.name for t in make_tools([])} & {"save_trip", "save_preferences"} == set()
    assert {"save_trip", "save_preferences"} & mcp_client.READ_ONLY_TOOLS == set()


# ------------------------------------------------------------------ calls from each agent stage
def test_research_calls_are_reported_as_the_researchers(server):
    from researcher.researcher import run_research
    trip = {"destination": "Amman", "currency": "USD", "travellers": 1, "budget_total": 400,
            "start_date": "2026-11-02", "end_date": "2026-11-03", "interests": ["history"]}
    with recording() as calls, stage("researcher"):
        result = run_research(trip)  # the real caller: call_read_tool -> _dispatch -> (fake) transport
    assert [c["tool"] for c in calls] == [c["tool"] for c in result.calls] == ["find_attractions", "find_restaurants"]
    assert {c["agent"] for c in calls} == {"researcher"}


def test_the_planners_travel_lookups_inside_the_crewai_loop_are_reported(server):
    """The real Itinerary Planner agent (scripted LLM) calls check_day; Python looks travel times up over MCP."""
    from planning.itinerary_planner import plan_itinerary
    from research_fixtures import bundle, recorded_lookup
    from scripted_llm import NativeScriptedLLM, native_tool_call
    from test_itinerary_planner import MON, VALID, assign, budget_report

    recorded = recorded_lookup()
    server.answers["travel_time"] = lambda args: recorded(args["from_place"], args["to_place"])
    b = bundle()
    llm = NativeScriptedLLM(model="scripted-test", replies=[
        native_tool_call("check_day", {"date": MON, "place_ids": VALID[MON]}), assign(VALID).model_dump_json()])
    with recording() as calls, stage("itinerary_planner"):
        report = plan_itinerary(budget_report(b), b, llm, create_travel_lookup("Istanbul", mode="foot"))
    mcp_calls = [c for c in calls if c["via"] == "mcp"]
    assert mcp_calls and {c["tool"] for c in mcp_calls} == {"travel_time"}
    assert {c["agent"] for c in mcp_calls} == {"itinerary_planner"}
    assert len(mcp_calls) == len(server.calls) == len(report.travel_lookups)  # every lookup, once


def test_the_planning_adapter_reports_each_agents_own_tools_after_its_mcp_calls(server, monkeypatch):
    from app import planning_adapter
    from research_fixtures import bundle as fixture_bundle

    def fake_budget(bundle, llm):
        return SimpleNamespace(agent_tool_calls=[{"tool": "check_selection", "ok": True, "ms": 3, "picked": [1]}])

    def fake_planner(report, bundle, llm, travel_lookup):
        travel_lookup("A", "B")
        return SimpleNamespace(agent_tool_calls=[{"tool": "check_day", "ok": True, "ms": 2}], itinerary=None,
                               status="incomplete")

    monkeypatch.setattr(planning_adapter, "run_budget_analyst", fake_budget)
    monkeypatch.setattr(planning_adapter, "plan_itinerary", fake_planner)
    with recording() as calls:
        planning_adapter.run_planning_pipeline(fixture_bundle("amman"), llm=object(),
                                               travel_lookup=create_travel_lookup("Amman", mode="driving"))
    assert [(c["tool"], c["agent"], c["via"]) for c in calls] == [
        ("check_selection", "budget_analyst", "python"), ("travel_time", "itinerary_planner", "mcp"),
        ("check_day", "itinerary_planner", "python")]
    assert "picked" not in calls[0]  # only {tool, ok, ms, agent, via}


def test_the_verifiers_lookups_and_its_own_tool_are_reported(server, monkeypatch):
    from app import verification_adapter

    def fake_verifier(itinerary, research_bundle, llm, budget_report, planner_report, hours_lookup, travel_lookup):
        hours_lookup("Topkapi Palace")
        return SimpleNamespace(verified=True, agent_tool_calls=[{"tool": "inspect_verification", "ok": True, "ms": 1}])

    monkeypatch.setattr(verification_adapter, "run_verifier_agent", fake_verifier)
    with recording() as calls:
        out = verification_adapter.run_verification_pipeline(object(), research_bundle=None, llm=None,
                                                             hours_lookup=create_hours_lookup("Istanbul"))
    assert out["verified"] is True
    assert [(c["tool"], c["agent"], c["via"]) for c in calls] == [
        ("get_opening_hours", "verifier", "mcp"), ("inspect_verification", "verifier", "python")]


# ------------------------------------------------------------------ /api/chat contract fields
AMMAN = {"destination": "Amman", "currency": "USD", "travellers": 2, "budget_total": 600, "estimated_total": 16.92,
         "days": [{"date": "2026-11-02", "activities": [
             {"name": "Amman Citadel", "start_time": "09:00", "end_time": "10:30", "estimated_cost": 8.46,
              "source_url": "https://en.wikivoyage.org/wiki/Amman#See"},
             {"name": "Hashem", "start_time": "13:00", "end_time": "14:00", "estimated_cost": None,
              "source_url": "https://en.wikivoyage.org/wiki/Amman#Eat"}]}]}
COVERED = {"trip_days": 1, "sufficient": True, "covered_dates": ["2026-11-02"], "meal_only_dates": [],
           "empty_dates": []}


def verified_result():
    final = SimpleNamespace(status="PASS", verified=True, evidence_basis="fresh")
    return {"status": "verified", "message": "Passed.", "itinerary": AMMAN, "coverage": COVERED,
            "verification_result": {"verified": True, "verifier_report": SimpleNamespace(verification=final)},
            "verification": {"state": "confirmed_valid", "findings": []},
            "summary": {"days": [{"date": "2026-11-02", "activities": [
                {"name": "Amman Citadel"}, {"name": "Hashem", "travel_minutes": 12, "travel_mode": "foot"}]}]},
            "performance": {"llm_total": {"successful_requests": 3, "prompt_tokens": 1000, "completion_tokens": 200}}}


def orchestrate_with_calls(result, *tools):
    """A stand-in for the pipeline that makes real (faked-transport) MCP calls, then returns `result`."""
    def fake(message, session_id=None, trip_form=None, preferences=None):
        with stage("researcher"):
            for tool in tools:
                call_read_tool(tool, {"city": "Amman"})
        contract.record_llm_usage({"successful_requests": 1, "prompt_tokens": 100, "completion_tokens": 10})
        return dict(result)
    return fake


def test_chat_returns_the_contract_fields_from_what_really_happened(server):
    with patch("app.main.orchestrate_trip", side_effect=orchestrate_with_calls(verified_result(), "find_attractions")):
        data = client.post("/api/chat", json={"session_id": "s1", "message": "Plan Amman",
                                              "user": {"traveller_id": "TRV-200", "name": "Test"}}).json()
    assert data["status"] == data["state"] == "pending_approval"
    assert data["result"] is None
    assert [c["tool"] for c in data["tool_calls"]] == ["get_preferences", "find_attractions"]
    assert data["usage"]["prompt_tokens"] == 1100 and data["usage"]["completion_tokens"] == 210  # coordinator + pipeline
    assert data["usage"]["cost_usd"] == pytest.approx(1100 / 1e6 * 0.40 + 210 / 1e6 * 1.60)
    assert [c["url"] for c in data["citations"]] == ["https://en.wikivoyage.org/wiki/Amman#See",
                                                     "https://en.wikivoyage.org/wiki/Amman#Eat"]
    reply = data["reply"].lower()
    assert "day 1" in reply and "estimate" in reply and "12-minute walk" in reply and "price unknown" in reply
    assert "shall i save this plan" in reply
    action = data["pending_action"]
    assert action["payload"]["traveller_id"] == "TRV-200" and action["payload"]["title"] == "Amman, 1 day"
    assert action["payload"]["itinerary"] == AMMAN and "TRV-200" in action["summary"]


def test_a_request_without_tools_or_llm_reports_none(server):
    with patch("app.main.orchestrate_trip", return_value={"status": "needs_clarification",
                                                          "message": "Please provide the following information: start_date"}):
        data = client.post("/api/chat", json={"session_id": "s2", "message": "Plan Istanbul"}).json()
    assert data["tool_calls"] == [] and data["citations"] == [] and data["pending_action"] is None
    assert data["usage"]["prompt_tokens"] == 0 and data["usage"]["cost_usd"] == 0
    assert data["reply"] == "Please provide the following information: start_date"


def test_consecutive_requests_do_not_share_tool_calls(server):
    with patch("app.main.orchestrate_trip", side_effect=orchestrate_with_calls(
            {"status": "needs_clarification", "message": "?"}, "find_attractions", "find_restaurants")):
        first = client.post("/api/chat", json={"session_id": "s3", "message": "a"}).json()
    with patch("app.main.orchestrate_trip", side_effect=orchestrate_with_calls(
            {"status": "needs_clarification", "message": "?"}, "geocode")):
        second = client.post("/api/chat", json={"session_id": "s3", "message": "b"}).json()
    assert [c["tool"] for c in first["tool_calls"]] == ["find_attractions", "find_restaurants"]
    assert [c["tool"] for c in second["tool_calls"]] == ["geocode"]


def test_concurrent_chat_requests_keep_their_own_tool_calls(server):
    def slow(tools):
        def fake(message, session_id=None, trip_form=None, preferences=None):
            for tool in tools:
                call_read_tool(tool, {})
                time.sleep(0.02)
            return {"status": "needs_clarification", "message": "?"}
        return fake

    out = {}
    plans = {"x": ["geocode"] * 2, "y": ["find_attractions"] * 4}

    def fake(message, session_id=None, trip_form=None, preferences=None):
        return slow(plans[session_id])(message)

    with patch("app.main.orchestrate_trip", side_effect=fake):
        threads = [threading.Thread(target=lambda s=s: out.__setitem__(
            s, client.post("/api/chat", json={"session_id": s, "message": "go"}).json())) for s in plans]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert [c["tool"] for c in out["x"]["tool_calls"]] == ["geocode"] * 2
    assert [c["tool"] for c in out["y"]["tool_calls"]] == ["find_attractions"] * 4


# ------------------------------------------------------------------ /api/approve through MCP
def pending(server, session_id="s-approve", traveller="TRV-200"):
    with patch("app.main.orchestrate_trip", return_value=verified_result()):
        data = client.post("/api/chat", json={"session_id": session_id, "message": "Plan Amman",
                                              "user": {"traveller_id": traveller}}).json()
    server.calls.clear()
    return data["pending_action"]


def approve(session_id, action_id, approved=True):
    return client.post("/api/approve", json={"session_id": session_id, "action_id": action_id,
                                             "approved": approved}).json()


def test_approval_saves_exactly_the_approved_payload_through_mcp(server):
    action = pending(server)
    data = approve("s-approve", action["action_id"])
    assert server.calls == [("save_trip", {**action["payload"], "idempotency_key": action["action_id"]})]
    assert data["status"] == "approved" and data["result"] == {"trip_id": "TRIP-3001"}
    assert "TRIP-3001" in data["reply"] and data["pending_action"] is None
    assert [(c["tool"], c["ok"], c["via"]) for c in data["tool_calls"]] == [("save_trip", True, "mcp")]


def test_a_repeated_approval_writes_nothing_again(server):
    action = pending(server)
    first = approve("s-approve", action["action_id"])
    second = approve("s-approve", action["action_id"])
    assert server.names() == ["save_trip"]
    assert second["result"] == first["result"] == {"trip_id": "TRIP-3001"}
    assert second["tool_calls"] == []


def test_rejection_writes_nothing(server):
    action = pending(server)
    data = approve("s-approve", action["action_id"], approved=False)
    assert server.calls == [] and data["status"] == "rejected" and data["result"] is None
    assert "TRIP-" not in data["reply"]
    again = approve("s-approve", action["action_id"])  # approving after rejecting: still nothing
    assert server.calls == [] and again["result"] is None


@pytest.mark.parametrize("answer", [
    {"ok": False, "error": {"code": "upstream_unavailable", "status": 503}},
    {"ok": False, "error": {"code": "idempotency_conflict", "status": 409}},
    {"ok": True, "note": "no trip id"},
    {"ok": True, "trip_id": "made-up"},
    "not a dict",
])
def test_a_failed_or_malformed_save_is_never_reported_as_success(server, answer):
    action = pending(server)
    server.answers["save_trip"] = answer
    data = approve("s-approve", action["action_id"])
    assert data["status"] == "save_failed" and data["result"] is None and "TRIP-" not in data["reply"]
    assert sessions["s-approve"]["pending_action"] is not None  # the traveller can retry
    del server.answers["save_trip"]
    retry = approve("s-approve", action["action_id"])
    assert retry["result"] == {"trip_id": "TRIP-3001"}


def test_an_unreachable_mcp_server_is_a_failed_save_and_there_is_no_rest_fallback(server, monkeypatch):
    import httpx
    import app.trip_store as trip_store

    def no_http(*a, **k):
        raise AssertionError("a direct HTTP call was made")

    monkeypatch.setattr(httpx.Client, "request", no_http)
    monkeypatch.setattr(httpx.AsyncClient, "request", no_http)
    action = pending(server)
    server.raise_for = {"save_trip"}
    data = approve("s-approve", action["action_id"])
    assert data["status"] == "save_failed" and data["result"] is None and "mcp_unavailable" in data["reply"]
    assert not hasattr(trip_store, "httpx")


def test_an_unknown_action_gets_a_clear_reply_and_no_write(server):
    pending(server)
    data = approve("s-approve", "not-the-action")
    assert data["status"] == "no_pending_action" and data["result"] is None and server.calls == []


# ------------------------------------------------------------------ memory through MCP
def chat_as(traveller, message, session_id="s-mem", result=None):
    seen = {}

    def fake(message, session_id=None, trip_form=None, preferences=None):
        seen["preferences"] = preferences
        return result or {"status": "needs_clarification", "message": "When do you travel?",
                          "preferences_used": traveller_memory.apply_preferences({}, preferences)}

    with patch("app.main.orchestrate_trip", side_effect=fake):
        user = {"traveller_id": traveller} if traveller else None
        data = client.post("/api/chat", json={"session_id": session_id, "message": message, "user": user}).json()
    return data, seen.get("preferences")


def test_a_returning_travellers_preferences_are_loaded_through_mcp_and_used(server):
    data, prefs = chat_as("TRV-101", "Plan one day in Istanbul. Use my saved preferences.")
    assert prefs["dietary"] == ["vegetarian"]
    assert ("get_preferences", {"traveller_id": "TRV-101"}) in server.calls
    assert "get_preferences" in [c["tool"] for c in data["tool_calls"]]
    assert "vegetarian" in data["reply"]
    _, again = chat_as("TRV-101", "And on Friday?")
    assert server.names().count("get_preferences") == 1 and again == prefs  # loaded once per session


def test_preferences_fill_only_what_the_traveller_did_not_say():
    trip = {"travellers": 3, "currency": "EUR", "interests": None, "max_walk_minutes": None}
    used = traveller_memory.apply_preferences(trip, FakeServer().prefs["TRV-101"])
    assert trip["travellers"] == 3 and trip["currency"] == "EUR"  # what was said stays
    assert trip["max_walk_minutes"] == 25 and trip["budget_style"] == "budget"
    assert trip["interests"] == ["history", "food"]  # "local food" -> the food tag
    assert any("vegetarian" in u for u in used)


def test_a_lasting_preference_is_saved_through_mcp_merged_with_what_is_stored(server):
    data, prefs = chat_as("TRV-102", "Please remember that I'm vegan and walk up to 20 minutes.")
    saved = [args for name, args in server.calls if name == "save_preferences"]
    assert saved == [{"traveller_id": "TRV-102",
                      "preferences": {"dietary": ["halal", "nut allergy", "vegan"], "max_walk_minutes": 20}}]
    assert server.prefs["TRV-101"]["dietary"] == ["vegetarian"]  # another traveller is untouched
    assert prefs["dietary"] == ["halal", "nut allergy", "vegan"]  # planning uses the update at once
    assert "remember" in data["reply"].lower()


@pytest.mark.parametrize("message", [
    "I'm vegan.",                                # a fact about this trip, not asked to be kept
    "Remember to book cheap tickets.",           # asks to remember nothing the store knows
    "Plan 2 days in Amman, remember the dates.",
])
def test_nothing_is_saved_without_an_explicit_lasting_preference(server, message):
    chat_as("TRV-101", message)
    assert "save_preferences" not in server.names()


def test_an_empty_update_never_reaches_the_store_and_lists_are_not_erased():
    assert traveller_memory.merged_update({"dietary": ["halal"]}, {"dietary": [], "notes": ""}) == {}
    assert traveller_memory.save_preferences("TRV-102", {}) == (None, "nothing_to_save")


def test_a_session_belongs_to_one_traveller(server):
    chat_as("TRV-101", "Plan Istanbul", session_id="shared")
    data, prefs = chat_as("TRV-102", "Plan Istanbul", session_id="shared")
    assert data["status"] == "traveller_mismatch" and prefs is None
    assert "vegetarian" not in data["reply"]


def test_no_traveller_no_memory_call(server):
    _, prefs = chat_as(None, "Remember that I'm vegetarian")
    assert prefs is None and server.calls == []


def test_an_unavailable_memory_service_is_said_and_planning_continues(server):
    server.raise_for = {"get_preferences"}
    data, prefs = chat_as("TRV-101", "Plan Istanbul")
    assert prefs is None and "could not load your saved preferences" in data["reply"]
    assert data["tool_calls"][0] == {**data["tool_calls"][0], "tool": "get_preferences", "ok": False}


@pytest.mark.parametrize("user, expected", [
    ({"traveller_id": "TRV-101"}, "TRV-101"), ({"traveller_id": " TRV-7 "}, "TRV-7"), ({"traveller_id": "../x y"}, None),
    ({"name": "Sara"}, None), (None, None), ({"traveller_id": 101}, None),
])
def test_only_a_valid_traveller_id_identifies_the_traveller(user, expected):
    assert traveller_memory.traveller_id_from(user) == expected


def test_loaded_preferences_are_said_even_when_the_pipeline_stops_early(server):
    data, _ = chat_as("TRV-101", "Plan Istanbul", result={"status": "unsupported_request", "message": "?"})
    assert "Your saved preferences: diet: vegetarian" in data["reply"]
    assert "Used for this plan" not in data["reply"]  # nothing is claimed as used when nothing was planned
