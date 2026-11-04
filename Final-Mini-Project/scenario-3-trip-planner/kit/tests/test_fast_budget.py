"""The fast path's time budget and its head starts: a slow source costs a checked fact, never the answer or a wrong one.

Offline. TEST FIXTURE (labelled): the routing and Wikivoyage answers are recorded shapes served by a stand-in for
http_get, so nothing here depends on the network; the code under test is the real one.
"""
import threading
import time

import pytest

from app import mcp_lookup_adapter as adapter
from planning.schedule import Stop, TravelTimes
from services import travel_data as td
from test_auth import ORIGIN, auth_env  # noqa: F401  (auth_env: autouse env)
from test_my_trips_journey import user


class Answer:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        assert self.status_code < 400


def point(n, lat=41.0):
    return {"id": f"p{n}", "name": f"P{n}", "lat": lat + n / 100, "lon": 29.0}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(td, "mode", lambda: "live")
    monkeypatch.setattr(td, "_MEM", {})


def test_past_the_time_limit_a_leg_is_not_looked_up_and_says_so():
    asked = []
    a, b = (Stop(place_id=i, name=i, category="attraction") for i in "ab")
    travel = TravelTimes(lambda x, y: asked.append((x, y)) or {"duration_min": 5}, seconds=0)
    leg = travel.leg(a, b)
    assert leg.status == "not_looked_up" and leg.minutes is None and "time limit" in leg.note and not asked


def test_a_multi_stop_route_stores_each_leg_under_the_key_route_reads(live, monkeypatch):
    legs = [{"duration": 600.0, "distance": 800.0}, {"duration": 120.0, "distance": 150.0}]
    monkeypatch.setattr(td, "http_get", lambda url, params=None: (Answer({"routes": [{"legs": legs}]}), url))
    a, b, c = point(1), point(2), point(3)
    assert td.warm_routes("foot", [a, b, c]) == 2
    key = f"foot__{a['lat']:.5f},{a['lon']:.5f}__{b['lat']:.5f},{b['lon']:.5f}"
    assert td._MEM[("route", key)][1]["duration_s"] == 600.0
    back = f"foot__{b['lat']:.5f},{b['lon']:.5f}__{a['lat']:.5f},{a['lon']:.5f}"
    assert ("route", back) not in td._MEM  # that direction was not measured


def test_no_way_between_two_stops_stores_nothing_and_raises_nothing(live, monkeypatch):
    monkeypatch.setattr(td, "http_get", lambda url, params=None: (Answer({"code": "NoRoute"}, 400), url))
    assert td.warm_routes("driving", [point(1), point(2), point(3)]) == 0
    assert not td._MEM


def test_only_the_long_walks_are_asked_by_car(live, monkeypatch):
    urls = []

    def get(url, params=None):
        urls.append(url)
        n = url.split("/driving/")[1].count(";")
        return Answer({"routes": [{"legs": [{"duration": 2400.0 if "routed-foot" in url and i == 0 else 60.0,
                                             "distance": 100.0} for i in range(n)]}]}), url
    monkeypatch.setattr(td, "http_get", get)
    td.warm_routes("foot", [point(1), point(2), point(3)], ride_after_s=1740)
    assert len(urls) == 2 and "routed-car" in urls[1] and urls[1].split("/driving/")[1].count(";") == 1


def test_recorded_data_is_never_warmed(monkeypatch):
    monkeypatch.setattr(td, "mode", lambda: "replay")
    monkeypatch.setattr(td, "http_get", lambda *a, **k: pytest.fail("replay must not reach the network"))
    assert td.warm_routes("foot", [point(1), point(2)]) == 0


def test_district_pages_arrive_in_one_request_and_a_redirect_keeps_its_page(live, monkeypatch):
    calls = []
    page = {"title": "Town/Old Quarter", "revisions": [{"revid": 7, "slots": {"main": {"content": "text"}}}]}
    other = {"title": "Town/North", "revisions": [{"revid": 8, "slots": {"main": {"content": "text"}}}]}

    def get(url, params=None):
        calls.append(params)
        return Answer({"query": {"redirects": [{"from": "Town/Old", "to": "Town/Old Quarter"}],
                                 "pages": [page, other, {"title": "Town/Gone", "missing": True}]}}), url
    monkeypatch.setattr(td, "http_get", get)
    td.wv_pages_prefetch(["Town/Old", "Town/North", "Town/Gone"])
    assert len(calls) == 1
    assert td._MEM[("wikivoyage", td.slug("Town/Old"))][1]["title"] == "Town/Old Quarter"
    assert ("wikivoyage", td.slug("Town/Gone")) not in td._MEM  # wv_page() asks, and reports it missing


def test_a_failed_batch_leaves_each_page_to_its_own_request(live, monkeypatch):
    def down(url, params=None):
        raise RuntimeError("down")
    monkeypatch.setattr(td, "http_get", down)
    td.wv_pages_prefetch(["Town/A", "Town/B"])
    assert not td._MEM


def test_two_requests_for_a_new_city_build_it_once(monkeypatch):
    built = []

    def build(city):
        built.append(city)
        time.sleep(0.1)
        return [{"id": "a"}], {"city": city}
    monkeypatch.setattr(td, "_build_city_listings", build)
    monkeypatch.setattr(td, "_listings_memo", {})
    workers = [threading.Thread(target=td.city_listings, args=("Newtown",)) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert built == ["Newtown"]


@pytest.fixture
def clean_prepare(monkeypatch):
    monkeypatch.setattr(adapter, "_prepared", {})
    monkeypatch.setattr(adapter, "_preparing", 0)


class Inline:
    def submit(self, fn):
        fn()


def test_a_picked_city_is_read_once(clean_prepare, monkeypatch):
    asked = []
    monkeypatch.setattr(adapter, "_PREPARE_POOL", Inline())
    monkeypatch.setattr(adapter, "call_read_tool", lambda tool, args: asked.append((tool, args)))
    assert adapter.prepare_city("Paris, France") == "started"
    assert adapter.prepare_city(" paris, france ") == "ready"
    assert asked == [("find_attractions", {"city": "Paris, France", "limit": 1})]


def test_a_failed_read_can_be_tried_again_and_a_burst_is_refused(clean_prepare, monkeypatch):
    def down(tool, args):
        raise RuntimeError("down")
    monkeypatch.setattr(adapter, "_PREPARE_POOL", Inline())
    monkeypatch.setattr(adapter, "call_read_tool", down)
    assert adapter.prepare_city("Oslo, Norway") == "started"
    assert adapter.prepare_city("Oslo, Norway") == "started"
    monkeypatch.setattr(adapter, "_preparing", adapter.MAX_PREPARING)
    assert adapter.prepare_city("Lima, Peru") == "busy"


def test_prepare_needs_a_signed_in_user_and_the_right_origin(clean_prepare, monkeypatch):
    from fastapi.testclient import TestClient
    from test_auth import real_app
    monkeypatch.setattr(adapter, "_PREPARE_POOL", Inline())
    monkeypatch.setattr(adapter, "call_read_tool", lambda tool, args: None)
    body = {"destination": "Paris, France"}
    assert TestClient(real_app()).post("/api/plan/prepare", headers=ORIGIN, json=body).status_code == 401
    alice = user("alice")
    assert alice.post("/api/plan/prepare", headers={"Origin": "https://evil.example"}, json=body).status_code == 403
    r = alice.post("/api/plan/prepare", headers=ORIGIN, json=body)
    assert r.status_code == 202 and r.json() == {"status": "started"}
    assert alice.post("/api/plan/prepare", headers=ORIGIN, json={"destination": "x" * 101}).status_code == 422
