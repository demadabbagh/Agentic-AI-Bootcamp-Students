"""
Module 5 Lab — STARTER (MCP SERVER)
Expose two tools over MCP: read_data(key) and web_search(query).

Uses the `mcp` Python SDK (FastMCP). Install:  pip install mcp httpx
Run this server, then connect from mcp_client_agent_starter.py.

Fill in the TODOs. Keep SECRETS server-side. Validate args. Set timeouts.
"""

import os
from mcp.server.fastmcp import FastMCP
# import httpx  # for real API calls

mcp = FastMCP("jhf-tools")

# Small data source for read_data (Step 2)
COUNTRIES = {
    "JO": {"country": "Jordan", "capital": "Amman"},
    "EG": {"country": "Egypt", "capital": "Cairo"},
    "TR": {"country": "Turkey", "capital": "Ankara"},
}

USE_MOCK = os.environ.get("SEARCH_API_KEY") is None


@mcp.tool()
def read_data(key: str) -> dict:
    """Look up a country by ISO 3166-1 alpha-2 code (e.g., 'JO'). Returns country and capital."""
    # TODO (Step 2 + Step 5): validate key (2 letters, uppercase); return record or a clear error dict.
    raise NotImplementedError


@mcp.tool()
def web_search(query: str) -> str:
    """Search the web for the query and return a short text result (one headline/snippet)."""
    # TODO (Step 3): if USE_MOCK -> return a canned headline for the query.
    # else -> call a real search API with httpx and a TIMEOUT; map errors to a string.
    raise NotImplementedError


if __name__ == "__main__":
    # Default transport is stdio; the client launches this server.
    mcp.run()
