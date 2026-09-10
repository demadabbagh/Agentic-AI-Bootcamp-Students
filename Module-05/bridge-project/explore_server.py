"""
BRIDGE PROJECT  -  EXPLORE ANY MCP SERVER  (given to you, complete)
===================================================================

Run:   python explore_server.py <scenario>

    python explore_server.py qa        # a real browser  (24 tools)
    python explore_server.py radar     # live Hacker News ( 9 tools)
    python explore_server.py chart     # chart generator (27 tools)

This is the first thing you run, and the thing you will come back to
every time you are unsure what a server can do.

It answers the only question that matters when you meet a server you
did not write:

    "What can you do, and what arguments do you want?"

You never read the server's source code. You never read its docs. You
ask it, and it tells you. That is the entire point of a protocol.

You can also point it at ANY server, not just the three below:

    python explore_server.py --raw npx -y @modelcontextprotocol/server-filesystem .
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Third-party servers document themselves in whatever characters they like -
# the chart server's descriptions contain Chinese. The default Windows console
# is cp1252 and raises UnicodeEncodeError trying to print them, which looks
# like the server is broken when it is really just the terminal.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The three servers offered by this project. Each is a real, published
# MCP server written by somebody else. None of them needs an API key.
SERVERS = {
    "qa":     ("npx", ["-y", "@playwright/mcp@latest", "--headless",
                       "--isolated", "--browser", "chromium"]),
    "radar":  ("npx", ["-y", "@devabdultech/hn-mcp-server"]),
    "chart":  ("npx", ["-y", "@antv/mcp-server-chart"]),
}


def signature(schema: dict) -> str:
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    parts = [k for k in props if k in required]
    parts += [f"{k}?" for k in props if k not in required]
    return "(" + ", ".join(parts) + ")"


async def explore(command: str, args: list) -> None:
    params = StdioServerParameters(command=command, args=args)

    print(f"\n  launching:  {command} {' '.join(args)}")
    print("  (first run downloads the server - this can take a couple of minutes)\n")

    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                # The MCP SDK renamed these fields in v2 (serverInfo -> server_info).
                # Accept either, so this works whichever version you installed.
                srv = getattr(info, "server_info", None) or info.serverInfo
                ver = getattr(info, "protocol_version", None) or info.protocolVersion
                print(f"  connected to : {srv.name} {srv.version}")
                print(f"  protocol     : {ver}")

                listed = await session.list_tools()
                print(f"\n  {'='*74}")
                print(f"  THIS SERVER OFFERS {len(listed.tools)} TOOLS. YOU WROTE NONE OF THEM.")
                print(f"  {'='*74}\n")

                for i, t in enumerate(listed.tools, 1):
                    print(f"  {i:>2}  {t.name}{signature(t.inputSchema)}")
                    desc = " ".join((t.description or "").split())
                    if desc:
                        print(f"      {desc[:150]}")

                    props = t.inputSchema.get("properties", {})
                    required = set(t.inputSchema.get("required", []))
                    for k, v in props.items():
                        star = "REQUIRED" if k in required else "optional"
                        kind = v.get("type", "?")
                        note = " ".join(str(v.get("description", "")).split())[:80]
                        print(f"        - {k:16} {kind:8} {star:9} {note}")
                    print()

                print(f"  {'='*74}")
                print("  a trailing ? means the argument is optional")
                print("  every name and every argument above was discovered at runtime")
                print(f"  {'='*74}\n")


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "--raw":
        if len(argv) < 2:
            sys.exit("  usage: python explore_server.py --raw <command> [args...]")
        asyncio.run(explore(argv[1], argv[2:]))
        return

    if not argv or argv[0] not in SERVERS:
        print("\n  usage: python explore_server.py <scenario>\n")
        for name, (cmd, args) in SERVERS.items():
            print(f"     {name:12} {cmd} {' '.join(args)}")
        print("\n  or point it at any server you like:")
        print("     python explore_server.py --raw npx -y @modelcontextprotocol/server-filesystem .\n")
        return

    cmd, args = SERVERS[argv[0]]
    asyncio.run(explore(cmd, args))


if __name__ == "__main__":
    main()
