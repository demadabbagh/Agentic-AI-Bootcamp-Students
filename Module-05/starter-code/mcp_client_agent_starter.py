"""
Module 5 Lab — STARTER (MCP CLIENT AGENT)
A LangGraph agent that connects to the MCP server and calls its tools.

Install:  pip install mcp langgraph langchain-openai python-dotenv
The agent must call tools THROUGH MCP (not by importing the server module).

Fill in the TODOs.
"""

import asyncio
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

SERVER = StdioServerParameters(command="python", args=["mcp_server.py"])


async def main():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Step 6: list tools
            tools = await session.list_tools()
            print("Available tools:", [t.name for t in tools.tools])

            # TODO (Step 7-8): wrap each MCP tool as a callable for your LangGraph ReAct agent,
            # then run the question:
            #   "What's the capital of the country with ISO code 'JO',
            #    and find one recent headline about it?"
            # The act node should call:  await session.call_tool(name, arguments={...})

            # Minimal direct demo (replace with your ReAct agent):
            r1 = await session.call_tool("read_data", arguments={"key": "JO"})
            print("read_data ->", r1.content)
            r2 = await session.call_tool("web_search", arguments={"query": "Jordan news"})
            print("web_search ->", r2.content)


if __name__ == "__main__":
    asyncio.run(main())
