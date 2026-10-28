"""Read-only MCP client for the Researcher.

Starts services.travel_mcp over stdio and calls ONLY read tools.
Write tools (save_trip, save_preferences) are blocked here, in code,
so no prompt or LLM decision can ever save a trip from the research step.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

KIT_DIR = Path(__file__).resolve().parents[1]

# The only tools the Researcher may call. save_trip is deliberately missing.
READ_ONLY_TOOLS = frozenset({
    "geocode",
    "find_attractions",
    "find_restaurants",
    "get_opening_hours",
    "get_weather",
    "convert_currency",
    "travel_time",
})


class ToolNotAllowed(Exception):
    """Raised when someone asks the Researcher to call a non-read tool."""


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "services.travel_mcp"],
        cwd=str(KIT_DIR),
        env={
            **os.environ,
            "TRAVEL_API_URL": os.environ.get("TRAVEL_API_URL", "http://localhost:8103"),
        },
    )


def _parse(result: Any) -> dict:
    """Turn an MCP CallToolResult into the kit's {"ok": ...} dict."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": False, "error": {"code": "bad_tool_output", "message": text[:200]}}
    return {"ok": False, "error": {"code": "empty_tool_output", "message": "The tool returned nothing."}}


async def _call(name: str, args: dict) -> dict:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
    return _parse(result)


def call_read_tool(name: str, args: dict | None = None) -> dict:
    """Call one read-only MCP tool and return its dict result.

    Always returns {"ok": true, ...} or {"ok": false, "error": {...}}.
    Raises ToolNotAllowed for any tool outside READ_ONLY_TOOLS.
    """
    if name not in READ_ONLY_TOOLS:
        raise ToolNotAllowed(f"'{name}' is not a read-only tool; the Researcher cannot call it.")

    coro = _call(name, args or {})
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # normal case: no event loop yet

    # Already inside an event loop (e.g. called from FastAPI): use a worker thread.
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # re-raised below in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=runner)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box["value"]