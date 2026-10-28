"""CrewAI tools for the Researcher: read-only MCP calls, every raw result recorded.

The LLM gets a short view of each result (no description text: it is untrusted).
The full raw result goes to the log, and only the log becomes the ResearchBundle.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .mcp_client import ToolNotAllowed, call_read_tool

Caller = Callable[[str, dict], dict]


def recorded_call(log: list, caller: Caller, name: str, args: dict) -> dict:
    """Call one tool, append {tool, args, result} to the log, return the raw result."""
    clean = {k: v for k, v in args.items() if v is not None}
    try:
        result = caller(name, clean)
    except ToolNotAllowed:
        raise
    except Exception as exc:  # a crash is a tool failure, never a reason to guess
        result = {"ok": False, "error": {"code": "tool_crashed", "message": str(exc)[:200]}}
    log.append({"tool": name, "args": clean, "result": result})
    return result


def _raw(value: Any) -> Any:
    return value.get("raw") if isinstance(value, dict) else None


def _brief(item: dict) -> dict:
    """What the LLM may see of one listing: facts and source, no free text."""
    out = {k: item.get(k) for k in ("id", "name", "kind", "tags", "district", "price_tier", "source_url")}
    out["hours"] = _raw(item.get("hours"))
    out["price"] = _raw(item.get("price"))
    if item.get("price_conflict"):
        out["price_conflict"] = True
    if item.get("synthetic"):
        out["synthetic"] = True
    return out


def llm_view(tool: str, result: Any) -> str:
    if not isinstance(result, dict) or result.get("ok") is False:
        error = result.get("error") if isinstance(result, dict) else "bad result"
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False)
    if tool in ("find_attractions", "find_restaurants"):
        key = "attractions" if tool == "find_attractions" else "restaurants"
        return json.dumps({"ok": True, "count": result.get("count"),
                           key: [_brief(i) for i in result.get(key) or []]}, ensure_ascii=False)
    view = {k: v for k, v in result.items() if k not in ("description", "source_pages", "licence")}
    return json.dumps(view, ensure_ascii=False)[:3000]


class AttractionsInput(BaseModel):
    city: str
    interests: str | None = Field(None, description="Comma-separated interests (history,museum,food...) or one place name")
    limit: int = Field(15, ge=1, le=30)


class RestaurantsInput(BaseModel):
    city: str
    budget: str = Field("any", description="any | budget | mid-range | splurge")
    limit: int = Field(10, ge=1, le=20)


class HoursInput(BaseModel):
    place_name: str
    city: str


class FxInput(BaseModel):
    amount: float
    from_currency: str
    to_currency: str


class WeatherInput(BaseModel):
    city: str
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")


class _RecordedTool(BaseTool):
    mcp_name: str = ""
    log: Any = Field(default=None, exclude=True)   # Any keeps the shared list (no copy)
    caller: Any = Field(default=None, exclude=True)

    def _run(self, **kwargs: Any) -> str:
        result = recorded_call(self.log, self.caller or call_read_tool, self.mcp_name, kwargs)
        return llm_view(self.mcp_name, result)


class FindAttractionsTool(_RecordedTool):
    name: str = "find_attractions"
    description: str = "Find sights, activities and markets in a city. Only places returned here may be planned."
    args_schema: Type[BaseModel] = AttractionsInput
    mcp_name: str = "find_attractions"


class FindRestaurantsTool(_RecordedTool):
    name: str = "find_restaurants"
    description: str = "Find places to eat in a city for a budget level."
    args_schema: Type[BaseModel] = RestaurantsInput
    mcp_name: str = "find_restaurants"


class OpeningHoursTool(_RecordedTool):
    name: str = "get_opening_hours"
    description: str = "Opening hours, closed days and listed price of one place."
    args_schema: Type[BaseModel] = HoursInput
    mcp_name: str = "get_opening_hours"


class ConvertCurrencyTool(_RecordedTool):
    name: str = "convert_currency"
    description: str = "Convert an amount between currencies with official rates."
    args_schema: Type[BaseModel] = FxInput
    mcp_name: str = "convert_currency"


class WeatherTool(_RecordedTool):
    name: str = "get_weather"
    description: str = "Daily weather for a city between two dates."
    args_schema: Type[BaseModel] = WeatherInput
    mcp_name: str = "get_weather"


def make_tools(log: list, caller: Caller = call_read_tool) -> list[BaseTool]:
    """The Researcher's tool list. Read-only by construction: save_trip is not here."""
    return [cls(log=log, caller=caller) for cls in
            (FindAttractionsTool, FindRestaurantsTool, OpeningHoursTool, ConvertCurrencyTool, WeatherTool)]