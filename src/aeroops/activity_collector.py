"""Safe agent activity collector plugin for AeroOps.

Measures tool execution durations using a monotonic clock without accessing
the database or storing non-serializable objects in the session state.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from aeroops.ui_models import SafeAgentActivity


class ActivityCollectorPlugin(BasePlugin):
    """ADK plugin to safely collect tool call events and durations."""

    def __init__(self) -> None:
        super().__init__(name="activity_collector")
        self._lock = threading.Lock()
        # Maps (invocation_id, function_call_id) -> start_time_ns
        self._starts: dict[tuple[str, str], int] = {}
        # Stores collected SafeAgentActivity view models
        self.activities: list[SafeAgentActivity] = []

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict | None:
        """Record the start time of the tool call."""
        inv_id = tool_context.invocation_id or ""
        fc_id = tool_context.function_call_id or ""
        start_ns = time.monotonic_ns()
        with self._lock:
            self._starts[(inv_id, fc_id)] = start_ns
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> dict | None:
        """Record a successful tool execution and its duration."""
        inv_id = tool_context.invocation_id or ""
        fc_id = tool_context.function_call_id or ""
        end_ns = time.monotonic_ns()

        with self._lock:
            start_ns = self._starts.pop((inv_id, fc_id), None)

        if start_ns is not None:
            duration_ms = (end_ns - start_ns) / 1_000_000.0
            agent_name = getattr(tool_context, "agent_name", "unknown") or "unknown"

            # Count the number of source records retrieved if the result holds lists
            source_ref_count = 0
            if isinstance(result, dict):
                # Check data or structuredContent
                data = result.get("data")
                if isinstance(data, list):
                    source_ref_count = len(data)
                elif "structuredContent" in result:
                    sc = result["structuredContent"]
                    sc_data = sc.get("data") if isinstance(sc, dict) else None
                    if isinstance(sc_data, list):
                        source_ref_count = len(sc_data)
                    elif sc_data is not None:
                        source_ref_count = 1
                elif data is not None:
                    source_ref_count = 1

            activity = SafeAgentActivity(
                agent_name=agent_name,
                tool_name=tool.name,
                duration_ms=duration_ms,
                succeeded=True,
                source_ref_count=source_ref_count,
            )
            with self._lock:
                self.activities.append(activity)

        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict | None:
        """Record a failed tool execution and its duration."""
        inv_id = tool_context.invocation_id or ""
        fc_id = tool_context.function_call_id or ""
        end_ns = time.monotonic_ns()

        with self._lock:
            start_ns = self._starts.pop((inv_id, fc_id), None)

        if start_ns is not None:
            duration_ms = (end_ns - start_ns) / 1_000_000.0
            agent_name = getattr(tool_context, "agent_name", "unknown") or "unknown"

            activity = SafeAgentActivity(
                agent_name=agent_name,
                tool_name=tool.name,
                duration_ms=duration_ms,
                succeeded=False,
                source_ref_count=0,
            )
            with self._lock:
                self.activities.append(activity)

        return None

    async def after_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> None:
        """Clean up outstanding start timers for this invocation."""
        inv_id = invocation_context.invocation_id or ""
        with self._lock:
            keys_to_remove = [k for k in self._starts if k[0] == inv_id]
            for k in keys_to_remove:
                self._starts.pop(k, None)
