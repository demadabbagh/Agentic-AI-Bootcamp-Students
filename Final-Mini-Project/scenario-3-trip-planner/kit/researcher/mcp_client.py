"""Read-only MCP client for the Researcher.

Starts services.travel_mcp over stdio and calls ONLY read tools.
Write tools (save_trip, save_preferences) are blocked here, in code,
so no prompt or LLM decision can ever save a trip from the research step.
(The application's own approval and memory code writes through app/mcp_app_client.py, never an agent.)

Every MCP call, read or write, goes through _dispatch(), which records it for the current request when a
recording() is open: the API's tool_calls are these records, never reconstructed or estimated.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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


def reset_shared_session() -> None:
    """Close the shared MCP server process; the next call starts a new one (with the environment of that moment)."""
    with _shared._lock:
        _shared._reset()


# ------------------------------------------------------------------ per-request record of MCP calls
# A ContextVar, so concurrent requests (each in its own thread or task) never see each other's calls.
_CALLS: contextvars.ContextVar[list | None] = contextvars.ContextVar("rihla_mcp_calls", default=None)
_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("rihla_mcp_stage", default=None)


@contextmanager
def recording() -> Iterator[list[dict]]:
    """Record every MCP call made in this context: [{"tool", "ok", "ms", "agent", "via": "mcp", "error"?}]."""
    calls: list[dict] = []
    token = _CALLS.set(calls)
    try:
        yield calls
    finally:
        _CALLS.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Label the MCP calls made in this context with the agent stage that made them (researcher, verifier...)."""
    token = _STAGE.set(name)
    try:
        yield
    finally:
        _STAGE.reset(token)


def current_stage() -> str | None:
    return _STAGE.get()


def record_call(entry: dict) -> None:
    """Add one already-made tool call to the current recording (no-op outside a recording)."""
    calls = _CALLS.get()
    if calls is not None:
        calls.append(entry)


def _error_code(result: Any) -> str | None:
    if not isinstance(result, dict):
        return "bad_result"
    if result.get("ok") is False or isinstance(result.get("error"), dict):
        err = result.get("error")
        return str(err.get("code") or "error") if isinstance(err, dict) else "error"
    return None


def _transport(name: str, args: dict) -> dict:
    """Send one tool call to the MCP server over stdio. No allowlist here: callers check it."""
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


def _dispatch(name: str, args: dict | None) -> dict:
    """Make one MCP call and record it: its name, outcome and time. Arguments and results are not recorded
    (they may hold a traveller's data). A call that could not reach the server is recorded as failed, then
    re-raised. Used by call_read_tool and by the application's own write path (app/mcp_app_client.py) only."""
    started = time.perf_counter()
    try:
        result = _transport(name, dict(args or {}))
    except Exception as exc:
        record_call({"tool": name, "ok": False, "ms": round((time.perf_counter() - started) * 1000),
                     "agent": _STAGE.get(), "via": "mcp", "error": f"mcp_unavailable ({type(exc).__name__})"})
        raise
    entry = {"tool": name, "ok": _error_code(result) is None, "ms": round((time.perf_counter() - started) * 1000),
             "agent": _STAGE.get(), "via": "mcp"}
    if not entry["ok"]:
        entry["error"] = _error_code(result)
    record_call(entry)
    return result


def call_read_tool(name: str, args: dict | None = None) -> dict:
    """Call one read-only MCP tool and return its dict result.

    Always returns {"ok": true, ...} or {"ok": false, "error": {...}}.
    Raises ToolNotAllowed for any tool outside READ_ONLY_TOOLS (before anything reaches the server).
    Calls share one MCP server process (MCP_SHARED_SESSION=0 starts one per call, the old behaviour).
    """
    if name not in READ_ONLY_TOOLS:
        raise ToolNotAllowed(f"'{name}' is not a read-only tool; the Researcher cannot call it.")
    return _dispatch(name, args)
