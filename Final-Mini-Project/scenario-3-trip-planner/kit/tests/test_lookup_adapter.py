"""The MCP lookup adapter: one retry for transient failures, and car rides for walks over the threshold.

Live runs showed walks of 80-135 minutes in Tokyo and Barcelona planned as if normal. The travel_time tool
measures walking and driving (OSRM) only, so a long walk is measured again by car; nothing is estimated and no
public-transport time is ever given. No network: call_read_tool is replaced.
"""
import pytest

import app.mcp_lookup_adapter as adapter


def tool(results):
    """A fake call_read_tool answering from a queue per mode; it records every call."""
    calls = []

    def call(name, args):
        calls.append((name, dict(args)))
        r = results[args.get("mode", "hours")].pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    call.calls = calls
    return call


def walk(minutes):
    return {"ok": True, "duration_min": minutes, "distance_m": minutes * 75, "profile": "foot",
            "citation": {"id": "osrm-foot"}, "mode": "live"}


def drive(minutes):
    return {"ok": True, "duration_min": minutes, "distance_m": 9000, "profile": "driving",
            "citation": {"id": "osrm-driving"}, "mode": "live"}


def test_a_walk_over_the_threshold_is_measured_by_car_and_keeps_the_walk(monkeypatch):
    fake = tool({"foot": [walk(80)], "driving": [drive(17)]})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    leg = adapter.create_travel_lookup("Tokyo", mode="foot", ride_after_minutes=30)("Mohri Garden", "Hama-rikyu")
    assert (leg["profile"], leg["duration_min"], leg["walk_duration_min"]) == ("driving", 17, 80)
    assert leg["citation"] == {"id": "osrm-driving"} and leg["walk_citation"] == {"id": "osrm-foot"}
    assert [c[1]["mode"] for c in fake.calls] == ["foot", "driving"]


@pytest.mark.parametrize("walk_minutes, ride, expected", [
    (25, None, ("foot", 25)),  # under the threshold: no ride lookup at all
    (80, drive(95), ("foot", 80)),  # the ride is not shorter: the walk stays
])
def test_a_ride_is_used_only_when_it_helps(monkeypatch, walk_minutes, ride, expected):
    fake = tool({"foot": [walk(walk_minutes)], "driving": [ride] if ride else []})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    leg = adapter.create_travel_lookup("Paris", mode="foot", ride_after_minutes=30)("A", "B")
    assert (leg["profile"], leg["duration_min"]) == expected and "walk_duration_min" not in leg
    assert len(fake.calls) == (1 if ride is None else 2)


def test_a_failed_ride_lookup_keeps_the_measured_walk_never_a_guess(monkeypatch):
    fake = tool({"foot": [walk(80)], "driving": [{"ok": False, "error": {"code": "not_found"}}]})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    leg = adapter.create_travel_lookup("Barcelona", mode="foot", ride_after_minutes=30)("Tibidabo", "Raval")
    assert (leg["profile"], leg["duration_min"], leg["ride_unavailable"]) == ("foot", 80, True)


def test_transient_failures_are_retried_once_and_answers_never(monkeypatch):
    fake = tool({"hours": [{"ok": False, "error": {"code": "upstream_unavailable"}}, {"ok": True, "hours": "x"}]})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    slept = []
    assert adapter.call_lookup("get_opening_hours", {"place_name": "x"}, sleep=slept.append)["ok"] is True
    assert slept == [0.7]
    fake = tool({"hours": [{"ok": False, "error": {"code": "not_found"}}]})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    with pytest.raises(adapter.MCPLookupError, match="not_found"):
        adapter.call_lookup("get_opening_hours", {"place_name": "x"}, sleep=slept.append)
    assert len(fake.calls) == 1  # "not found" is an answer: no retry


def test_a_crash_is_retried_once_then_reported(monkeypatch):
    fake = tool({"hours": [RuntimeError("pipe"), RuntimeError("pipe")]})
    monkeypatch.setattr(adapter, "call_read_tool", fake)
    with pytest.raises(adapter.MCPLookupError, match="could not be completed"):
        adapter.call_lookup("get_opening_hours", {"place_name": "x"}, sleep=lambda s: None)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("value, expected", [(None, 30), ("45", 45), ("0", 30), ("abc", 30), ("500", 30)])
def test_the_walking_threshold_is_configurable_with_a_safe_default(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RIHLA_MAX_WALK_MINUTES", raising=False)
    else:
        monkeypatch.setenv("RIHLA_MAX_WALK_MINUTES", value)
    assert adapter.max_walk_minutes() == expected
