"""Synchronous gateway to TripMate's local stdio MCP server."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

SERVER_PATH = Path(__file__).resolve().with_name("travel_mcp_server.py")


async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=os.environ.copy(),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)

    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        detail = " ".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise RuntimeError(detail or f"MCP tool {name!r} failed.")

    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured

    text_parts = [block.text for block in result.content if isinstance(block, TextContent)]
    return "\n".join(text_parts)


def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Call one local MCP tool from a synchronous LangGraph node."""
    return asyncio.run(_call_tool(name, arguments))
