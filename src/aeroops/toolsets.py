"""MCP toolset factory for AeroOps specialist agents.

This module is the single place that defines the self-contained stdio MCP
subprocess used by the specialist agents.  Data access remains inside the MCP
server; no repository or SQLite imports are permitted here.
"""

from __future__ import annotations

import os
import sys

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from aeroops.config import get_settings


def _server_params(db_path_override: str | None = None) -> StdioServerParameters:
    """Build the underlying MCP stdio server parameters.

    ``sys.executable -m aeroops.mcp_server`` is used instead of nesting
    ``uv run`` inside Streamlit.  This guarantees that the child process uses
    the same installed environment as the application on local and cloud
    deployments.
    """
    settings = get_settings()
    db_path = db_path_override or str(settings.db_path.resolve())
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "aeroops.mcp_server"],
        env={
            **os.environ,
            "AEROOPS_DB_PATH": db_path,
            "PYTHONUNBUFFERED": "1",
        },
    )


def _connection_params(db_path_override: str | None = None) -> StdioConnectionParams:
    """Return ADK's recommended stdio connection wrapper with a cloud-safe timeout."""
    settings = get_settings()
    return StdioConnectionParams(
        server_params=_server_params(db_path_override),
        timeout=settings.mcp_timeout_seconds,
    )


def make_toolset(
    allowed_tools: frozenset[str],
    db_path_override: str | None = None,
) -> McpToolset:
    """Create a least-privilege MCP toolset for one specialist agent."""
    return McpToolset(
        connection_params=_connection_params(db_path_override),
        tool_filter=sorted(allowed_tools),
    )
