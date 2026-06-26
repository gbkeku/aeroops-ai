"""Lightweight, read-only stdio MCP client for the AeroOps user interface.

Spawns the aeroops-data-mcp server as a subprocess and queries list_aircraft
and get_fleet_summary without importing sqlite3, aeroops.db, or repository layers.
"""

from __future__ import annotations

import json
import os
from typing import Any

from aeroops.toolsets import _server_params


async def call_mcp_tool_direct(
    tool_name: str,
    arguments: dict[str, Any],
    db_path_override: str | None = None,
) -> dict[str, Any]:
    """Invoke an MCP tool on the aeroops-data-mcp subprocess using stdio transport.

    Enforces that only list_aircraft and get_fleet_summary are callable from this client.

    Args:
        tool_name: Name of the tool to execute.
        arguments: Key-value parameters for the tool call.
        db_path_override: Optional path to the database to configure in the server.

    Returns:
        The tool's result dictionary.
    """
    allowed = {"list_aircraft", "get_fleet_summary"}
    if tool_name not in allowed:
        raise ValueError(f"UI read-path is only authorized to call: {allowed}")

    params = _server_params(db_path_override)

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=params.command,
        args=list(params.args),
        env={**os.environ, **params.env, "PYTHONUNBUFFERED": "1"},
    )

    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        response = await session.call_tool(tool_name, arguments)
        if not response.content or not hasattr(response.content[0], "text"):
            raise RuntimeError(f"Unexpected response content from tool {tool_name}")
        raw_text = response.content[0].text
        result = json.loads(raw_text)
        return result
