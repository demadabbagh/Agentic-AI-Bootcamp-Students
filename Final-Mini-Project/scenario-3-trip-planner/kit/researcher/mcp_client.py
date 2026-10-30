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


CALL_TIMEOUT = 90  # seconds for one tool call on the shared session (a live Wikivoyage city fetch can be slow)


class _SharedSession:
    """One MCP server process kept open and reused. Starting a server per call costs about 1.6 s, which a
    planning request paid for every travel_time and opening-hours lookup. The same read-only allowlist applies:
    call_read_tool checks the tool name before anything reaches the session.

    The session lives in one asyncio task on a private event-loop thread (anyio requires that its contexts are
    entered and exited by the same task); calls from any thread are submitted to that loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._closed: asyncio.Event | None = None

    async def _hold(self, ready: "asyncio.Future") -> None:
        try:
            async with stdio_client(_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._closed = asyncio.Event()
                    ready.set_result(session)
                    await self._closed.wait()
        except BaseException as exc:  # the server did not start, or the pipe broke
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._session = None

    def _start(self) -> None:
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="mcp-shared-session", daemon=True).start()

        async def opened() -> ClientSession:
            ready = loop.create_future()
            loop.create_task(self._hold(ready))
            return await ready

        self._session = asyncio.run_coroutine_threadsafe(opened(), loop).result(timeout=CALL_TIMEOUT)
        self._loop = loop

    def _reset(self) -> None:
        loop, closed = self._loop, self._closed
        self._session = self._loop = self._closed = None
        if loop is not None:
            if closed is not None:
                loop.call_soon_threadsafe(closed.set)
            loop.call_soon_threadsafe(loop.stop)

    def call(self, name: str, args: dict) -> dict:
        for attempt in (1, 2):  # a broken session is restarted once; a tool's own error is never retried
            with self._lock:
                if self._session is None or self._loop is None:
                    self._reset()
                    self._start()
                session, loop = self._session, self._loop
            try:
                result = asyncio.run_coroutine_threadsafe(session.call_tool(name, args), loop).result(CALL_TIMEOUT)
                return _parse(result)
            except Exception:
                with self._lock:
                    if self._session is session:
                        self._reset()
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")


_shared = _SharedSession()


def call_read_tool(name: str, args: dict | None = None) -> dict:
    """Call one read-only MCP tool and return its dict result.

    Always returns {"ok": true, ...} or {"ok": false, "error": {...}}.
    Raises ToolNotAllowed for any tool outside READ_ONLY_TOOLS.
    Calls share one MCP server process (MCP_SHARED_SESSION=0 starts one per call, the old behaviour).
    """
    if name not in READ_ONLY_TOOLS:
        raise ToolNotAllowed(f"'{name}' is not a read-only tool; the Researcher cannot call it.")

    if os.environ.get("MCP_SHARED_SESSION", "1").strip() != "0":
        return _shared.call(name, dict(args or {}))

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