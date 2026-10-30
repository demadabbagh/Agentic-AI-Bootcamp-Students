"""The application's own MCP calls: the approved save and the traveller's preferences.

These tools are NOT in any agent's tool list and not in researcher.mcp_client.READ_ONLY_TOOLS. Only the
application code calls them: save_trip from POST /api/approve after its checks, get/save_preferences from the
chat endpoint's memory step. They use the same MCP server (services.travel_mcp over stdio) and the same
per-request recording as the agents' read tools, so they show in tool_calls too.
"""
from __future__ import annotations

from typing import Any

from researcher.mcp_client import _dispatch

APP_TOOLS = frozenset({"save_trip", "get_preferences", "save_preferences"})


class AppToolNotAllowed(Exception):
    """Raised for a tool outside APP_TOOLS."""


def call_app_tool(name: str, args: dict[str, Any]) -> dict:
    """Call one application-controlled MCP tool. Returns the tool's {"ok": ...} dict; raises when the MCP
    server cannot be reached (the call is recorded as failed either way)."""
    if name not in APP_TOOLS:
        raise AppToolNotAllowed(f"'{name}' is not an application tool.")
    return _dispatch(name, args)
