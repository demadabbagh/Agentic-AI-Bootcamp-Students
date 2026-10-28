"""Try the read-only MCP client on Amman. Run from kit/: python scripts/try_mcp_tools.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from researcher.mcp_client import ToolNotAllowed, call_read_tool


def show(title: str, data: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])


show("find_attractions: Amman (3)",
     call_read_tool("find_attractions", {"city": "Amman", "limit": 3}))
show("get_opening_hours: The Jordan Museum",
     call_read_tool("get_opening_hours", {"place_name": "The Jordan Museum", "city": "Amman"}))
show("find_restaurants: Amman, budget (3)",
     call_read_tool("find_restaurants", {"city": "Amman", "budget": "budget", "limit": 3}))

try:
    call_read_tool("save_trip", {})
    print("\n❌ save_trip was NOT blocked")
except ToolNotAllowed as exc:
    print(f"\n✅ Blocked as expected: {exc}")