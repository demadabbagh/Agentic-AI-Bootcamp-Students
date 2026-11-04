"""Two-stage planning: the fast answer, and the agents' background review, which can only ever make an offer.

Real here, as in test_my_trips_journey: app.main behind install_auth, the approval policy, MCP save_trip and the
Trip Store process. TEST FIXTURE (labelled): the pipeline results of both stages. The review is run synchronously by
a stand-in for the pool, so nothing depends on timing.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import refinement
from app.memory import sessions
from researcher import perf
from test_auth import ORIGIN, auth_env  # noqa: F401  (auth_env: autouse env)
from test_mcp_integration import api, travel_api  # noqa: F401  (the real Trip Store process)
from test_my_trips_journey import ISTANBUL, decide, plan, planned, store_trips, user

REFINED = deepcopy(ISTANBUL)
REFINED["days"][1]["activities"][0]["name"] = "Dolmabahce Palace"


def state(version):
    return SimpleNamespace(version=version, itinerary=SimpleNamespace(to_trip_store=lambda: deepcopy(REFINED)))


def reviewed(status="PASS", itinerary=REFINED, version="v2"):
    """TEST FIXTURE: what the agents' review returns."""
    out = planned(itinerary)
    out["verification"]["status"] = status
    out["verification_result"]["verifier_report"].verification.status = status
    out["_plan_state"] = state(version)
    return out


class Inline:
    """The pool, run at once."""
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture(autouse=True)
def inline_pool(monkeypatch):
    monkeypatch.setattr(refinement, "_POOL", Inline())


def with_review(client, session_id, review, base_status="PASS"):
    """A plan on screen with a finished review."""
    data = plan(client, session_id, planned())
    session = sessions[session_id]
    session["plan_state"] = state("v1")
    base = {"itinerary": data["itinerary"], "verification": {"status": base_status}}
    refinement.start(session, base_version="v1", base_result=base, review=review)
    return data, session


def ask(client, session_id, path="/api/plan/refinement", version="v1"):
    return client.post(path, headers=ORIGIN, json={"session_id": session_id, "base_version": version})


def test_a_better_plan_is_offered_and_the_plan_on_screen_is_untouched(api):
    alice = user("alice")
    data, session = with_review(alice, "s-1", lambda: reviewed())
    body = ask(alice, "s-1").json()
    assert body["status"] == "ready" and "Adds Dolmabahce Palace." in body["changes"]
    assert set(body) == {"status", "changes", "verification_status"}  # no plan, no internals
    assert session["pending_action"]["action_id"] == data["pending_action"]["action_id"]
    assert session["plan_state"].version == "v1"


def test_applying_goes_through_the_gate_and_the_old_action_stops_working(api):
    alice = user("alice")
    data, _ = with_review(alice, "s-2", lambda: reviewed())
    old = data["pending_action"]["action_id"]
    r = ask(alice, "s-2", "/api/plan/refinement/apply").json()
    assert (r["revision"] or {}).get("outcome") == "revised" and r["pending_action"]["action_id"] != old
    assert decide(alice, "s-2", old).json()["status"] == "no_pending_action"
    assert store_trips(api) == []
    assert decide(alice, "s-2", r["pending_action"]["action_id"]).json()["status"] == "approved"
    [saved] = store_trips(api)
    assert saved["itinerary"]["days"][1]["activities"][0]["name"] == "Dolmabahce Palace"


def test_a_worse_verified_plan_is_never_offered(api):
    alice = user("alice")
    with_review(alice, "s-3", lambda: reviewed("UNKNOWN"), base_status="PASS")
    assert ask(alice, "s-3").json()["status"] == "no_change"
    assert ask(alice, "s-3", "/api/plan/refinement/apply").json()["revision"]["outcome"] == "stale_plan"


def test_the_same_plan_is_not_offered(api):
    alice = user("alice")
    with_review(alice, "s-4", lambda: reviewed(itinerary=ISTANBUL))
    assert ask(alice, "s-4").json()["status"] == "no_change"


def test_a_failed_review_changes_nothing(api):
    def broken():
        raise RuntimeError("provider down")
    alice = user("alice")
    data, session = with_review(alice, "s-5", broken)
    assert ask(alice, "s-5").json() == {"status": "failed"}
    assert session["pending_action"]["action_id"] == data["pending_action"]["action_id"]


def test_an_offer_for_a_plan_that_moved_on_cannot_be_applied(api):
    alice = user("alice")
    data, session = with_review(alice, "s-6", lambda: reviewed())
    session["plan_state"] = state("v9")  # the traveller revised the plan meanwhile
    assert ask(alice, "s-6", version="v9").json() == {"status": "stale"}
    assert ask(alice, "s-6", "/api/plan/refinement/apply", "v9").json()["revision"]["outcome"] == "stale_plan"
    assert session["plan_state"].version == "v9"
    assert session["pending_action"]["action_id"] == data["pending_action"]["action_id"]


def test_an_offer_for_a_saved_plan_cannot_be_applied(api):
    alice = user("alice")
    data, session = with_review(alice, "s-7", lambda: reviewed())
    assert decide(alice, "s-7", data["pending_action"]["action_id"]).json()["status"] == "approved"
    assert ask(alice, "s-7").json() == {"status": "stale"}
    assert ask(alice, "s-7", "/api/plan/refinement/apply").json()["revision"]["outcome"] == "stale_plan"
    assert len(store_trips(api)) == 1


def test_a_review_of_an_older_plan_is_dropped_when_a_new_plan_started(api):
    alice = user("alice")
    plan(alice, "s-8", planned())
    session = sessions["s-8"]
    session["plan_state"] = state("v1")
    held = []

    class Held:
        def submit(self, fn, *args):
            held.append((fn, args))

    with patch.object(refinement, "_POOL", Held()):
        refinement.start(session, base_version="v1", base_result={"itinerary": ISTANBUL}, review=lambda: reviewed())
    session["plan_state"] = state("v2")
    refinement.start(session, base_version="v2", base_result={"itinerary": REFINED}, review=lambda: reviewed(itinerary=REFINED))
    fn, args = held[0]
    fn(*args)  # the first job finishes last
    assert ask(alice, "s-8", version="v2").json()["status"] == "no_change"


def test_another_account_cannot_read_or_apply_a_review(api):
    alice, bob = user("alice"), user("bob")
    with_review(alice, "s-9", lambda: reviewed())
    for path in ("/api/plan/refinement", "/api/plan/refinement/apply"):
        r = ask(bob, "s-9", path)
        assert r.status_code == 403 and r.json()["code"] == "session_not_yours"
    assert ask(alice, "s-9").json()["status"] == "ready"


def test_a_review_needs_a_signed_in_user_and_the_right_origin(api):
    from fastapi.testclient import TestClient
    from test_auth import real_app
    anonymous = TestClient(real_app())
    assert ask(anonymous, "s-10").status_code == 401
    alice = user("alice")
    r = alice.post("/api/plan/refinement/apply", headers={"Origin": "https://evil.example"},
                   json={"session_id": "s-10", "base_version": "v1"})
    assert r.status_code == 403


def test_differences_say_what_changes_in_words():
    assert refinement.differences(ISTANBUL, ISTANBUL) == []
    lines = refinement.differences(ISTANBUL, REFINED)
    assert lines[:2] == ["Adds Dolmabahce Palace.", "Removes Galata Tower."]


def test_the_trace_keeps_requests_apart_and_counts_only_names_and_times():
    with perf.tracing() as one:
        perf.count("lookup_cache_hits")
        one.add_tool("travel_time", "verifier", 12, True)
        with perf.tracing() as two:
            two.add_llm("planner", 900, True)
        one.add_llm("verifier", 100, False)
    a, b = one.summary(), two.summary()
    assert a["mcp"]["by_tool"]["travel_time"] == {"calls": 1, "ms": 12, "max_ms": 12, "failed": 0}
    assert a["llm"]["by_stage"]["verifier"]["failed"] == 1 and a["counts"] == {"lookup_cache_hits": 1}
    assert b["llm"]["calls"] == 1 and b["mcp"]["calls"] == 0
    assert perf.current() is None
