"""Load the mock Researcher output in tests/fixtures.

Those files hold real tool results recorded from the kit's replay cache (see their "_about"), with a mock trip
request. They stand in for the Researcher until its output contract is final.
"""
import copy
import json
from pathlib import Path

from planning.research import ResearchBundle

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def raw(name: str = "istanbul") -> dict:
    return json.loads((FIXTURES / f"{name}_research.json").read_text(encoding="utf-8"))


def listing(name: str, place_id: str) -> dict:
    """One recorded listing (a deep copy, safe to edit)."""
    results = raw(name)["tool_results"]
    for item in [x for r in results["find_attractions"] for x in r["attractions"]] + \
                [x for r in results["find_restaurants"] for x in r["restaurants"]]:
        if item["id"] == place_id:
            return copy.deepcopy(item)
    raise KeyError(place_id)


def routes(name: str = "istanbul") -> dict:
    """Recorded travel_time results, 'from|to' -> the MCP result (tests/fixtures/<name>_routes.json)."""
    return json.loads((FIXTURES / f"{name}_routes.json").read_text(encoding="utf-8"))["results"]


def recorded_lookup(name: str = "istanbul", asked: list | None = None):
    """A TravelLookup that replays the recorded results. A pair that was never recorded answers like a tool
    error, so a test can never pass on an invented route. `asked` collects the pairs requested."""
    results = routes(name)

    def lookup(from_place: str, to_place: str) -> dict:
        if asked is not None:
            asked.append((from_place, to_place))
        return copy.deepcopy(results.get(f"{from_place}|{to_place}") or
                             {"ok": False, "error": {"code": "not_recorded", "message": "not in the fixture"}})
    return lookup


def bundle(name: str = "istanbul", *, drop_fx: tuple = (), extra_attractions: list | None = None,
           **trip) -> ResearchBundle:
    """The fixture as a ResearchBundle. trip=... overrides the mock request; drop_fx removes rates by source currency."""
    data = raw(name)
    results = data["tool_results"]
    attractions = results["find_attractions"]
    if extra_attractions:
        attractions = attractions + [{"ok": True, "attractions": extra_attractions}]
    return ResearchBundle.from_tool_results(
        {**data["trip"], **trip}, attractions=attractions, restaurants=results["find_restaurants"],
        fx=[r for r in results["convert_currency"] if r.get("from") not in drop_fx])
