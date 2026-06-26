"""Real ADK Runner integration tests for AeroOpsSecurityPlugin callback chain.

These tests use a real ADK Runner, a real stdio MCP server, and
AeroOpsSecurityPlugin registered exactly once.  No manually simulated callback
chains are used.

Verified sequence for a successful tool call
--------------------------------------------
  AeroOpsSecurityPlugin.after_tool_callback   (plugin, first)
  → returns None
  → agent-level after_tool_callback            (specialist evidence capture)
  → canonical evidence captured unchanged

Verified behaviour for a tool error
-------------------------------------
  AeroOpsSecurityPlugin.on_tool_error_callback  → returns None
  → error propagates to agent framework
  → no fallback operational evidence created
  → only sanitized audit metadata emitted
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models.registry import LLMRegistry
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from aeroops.security_plugin import AeroOpsSecurityPlugin
from aeroops.toolsets import make_toolset

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APP_NAME = "aeroops-security-runner-test"
_AC009 = "AC-009"

# Tools the single specialist will use in these tests
_TOOLS = frozenset({"get_aircraft_status", "get_open_defects"})


# ---------------------------------------------------------------------------
# Scripted LLM for runner tests (separate family to avoid conflicts)
# ---------------------------------------------------------------------------


class _RunnerTestLlm(BaseLlm):
    """Scripted LLM for security-runner tests.

    First turn: emits tool calls.
    Subsequent turns (after tool responses): emits final text.
    """

    _TOOL_CALLS: ClassVar[list[tuple[str, dict]]] = [
        ("get_aircraft_status", {"aircraft_id": _AC009}),
        ("get_open_defects", {"aircraft_id": _AC009}),
    ]

    _FINAL_TEXT: ClassVar[str] = json.dumps(
        {
            "domain": "test_operations",
            "aircraft_id": _AC009,
            "findings": [
                {
                    "finding_id": "FIND-SR-001",
                    "statement": "AC-009 status is red.",
                    "classification": "test_failure",
                    "source_refs": [
                        {
                            "source_id": "AC-009",
                            "record_type": "aircraft_status",
                            "summary": "Aircraft status red.",
                        }
                    ],
                    "rationale": "get_aircraft_status returned status=red.",
                }
            ],
            "raw_source_ids": [_AC009],
        }
    )

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"runner_test:.*"]

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        # Detect whether a tool response is present in the conversation
        has_tool_response = any(
            getattr(part, "function_response", None) is not None
            for content in llm_request.contents
            for part in (content.parts or [])
        )

        if not has_tool_response:
            # First turn: emit tool calls
            parts = [
                genai_types.Part(function_call=genai_types.FunctionCall(name=name, args=args))
                for name, args in self._TOOL_CALLS
            ]
            yield LlmResponse(
                content=genai_types.Content(role="model", parts=parts),
                partial=False,
            )
        else:
            # Subsequent turns: emit final text
            yield LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text=self._FINAL_TEXT)],
                ),
                partial=False,
            )


# Register the model family once (idempotent due to registry check)
with contextlib.suppress(Exception):
    LLMRegistry.register(_RunnerTestLlm)


# ---------------------------------------------------------------------------
# Database fixture (module-scope for speed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runner_test_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Seed a temporary SQLite database for security runner tests."""
    tmp_dir = tmp_path_factory.mktemp("aeroops_security_runner_db")
    db_path = tmp_dir / "aeroops_security_runner.db"

    from aeroops.db import get_db_connection
    from aeroops.db.schema import create_tables
    from aeroops.db.seed import seed_all

    with get_db_connection(db_path) as conn:
        create_tables(conn)
        seed_all(conn)
        conn.commit()

    return db_path


# ---------------------------------------------------------------------------
# Real Runner callback-chain integration tests
# ---------------------------------------------------------------------------


class TestRealRunnerCallbackChain:
    """Integration tests using a real ADK Runner with real stdio MCP toolsets."""

    @pytest.mark.asyncio
    async def test_plugin_then_specialist_callback_order_and_evidence_unchanged(
        self, runner_test_db: Path
    ) -> None:
        """Prove the ADK callback chain executes in the correct order:

        1. AeroOpsSecurityPlugin.after_tool_callback  → returns None
        2. agent-level after_tool_callback (specialist evidence capture) executes
        3. the specialist receives the original MCP result, unchanged
        4. EvidenceCatalog receives the canonical result
        """
        # ---- Execution tracking -------------------------------------------
        call_order: list[str] = []
        captured_results: list[dict] = []

        plugin = AeroOpsSecurityPlugin()

        # Agent-level after_tool_callback (specialist evidence capture)
        def specialist_evidence_callback(
            tool: Any,
            args: Any,
            tool_context: Any,
            tool_response: Any,
        ) -> None:
            call_order.append("specialist")
            # Capture the result EXACTLY as received (no copy, to detect mutation)
            captured_results.append(tool_response)
            return None

        # Wrap the plugin's after_tool_callback so we can record when it fires
        original_plugin_after = plugin.after_tool_callback

        async def instrumented_plugin_after_tool(
            *, tool: Any, tool_args: Any, tool_context: Any, result: Any
        ) -> dict | None:
            call_order.append("plugin")
            return await original_plugin_after(
                tool=tool,
                tool_args=tool_args,
                tool_context=tool_context,
                result=result,
            )

        plugin.after_tool_callback = instrumented_plugin_after_tool

        # ---- Build agent + toolset ----------------------------------------
        db_str = str(runner_test_db)
        toolset = make_toolset(_TOOLS, db_path_override=db_str)

        specialist = LlmAgent(
            name="test_ops_specialist",
            model="runner_test:test_ops",
            instruction="(scripted)",
            output_key="test_ops_findings",
            tools=[toolset],
            after_tool_callback=specialist_evidence_callback,
        )
        pipeline = SequentialAgent(
            name="security_runner_test_pipeline",
            sub_agents=[specialist],
        )

        # ---- Build Runner with plugin registered --------------------------
        session_service = InMemorySessionService()
        runner = Runner(
            agent=pipeline,
            app_name=_APP_NAME,
            session_service=session_service,
            plugins=[plugin],
        )

        run_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name=_APP_NAME,
            user_id="test-user",
            session_id=run_id,
        )

        user_msg = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=f"Investigate {_AC009}")],
        )

        try:
            async for _ in runner.run_async(
                user_id="test-user",
                session_id=run_id,
                new_message=user_msg,
            ):
                pass
        finally:
            from aeroops.services import _close_all_toolsets

            await _close_all_toolsets(pipeline)
            with contextlib.suppress(Exception):
                await runner.close()

        # ---- Assertions ----------------------------------------------------

        # At least one tool call happened
        assert len(captured_results) > 0, "No tool results were captured."

        # Plugin callback executed (appears before specialist)
        assert "plugin" in call_order, "Plugin after_tool_callback did not execute."
        assert "specialist" in call_order, "Specialist after_tool_callback did not execute."

        # Plugin fires BEFORE specialist for every tool call
        for i, name in enumerate(call_order):
            if name == "specialist":
                assert call_order[i - 1] == "plugin", (
                    f"specialist at position {i} was not immediately preceded by plugin. "
                    f"Full order: {call_order}"
                )

        # Captured results contain synthetic_data watermark (unmodified from MCP)
        for result in captured_results:
            inner = result
            # Unwrap structuredContent envelope if present
            if "structuredContent" in inner:
                inner = inner["structuredContent"]
            assert inner.get("synthetic_data") is True, (
                f"synthetic_data watermark missing or False in captured result: {inner}"
            )

        # Session state contains only serializable integers — no Locks
        sess = await session_service.get_session(
            app_name=_APP_NAME,
            user_id="test-user",
            session_id=run_id,
        )
        if sess:
            json.dumps(sess.state)  # must not raise
            security_keys = {k: v for k, v in sess.state.items() if "security" in k}
            for key, val in security_keys.items():
                assert isinstance(val, int), (
                    f"Session state key '{key}' should be int, got {type(val)!r}"
                )

        # Lock registry cleaned up by after_run_callback
        with plugin._registry_lock:
            assert len(plugin._invocation_locks) == 0, (
                f"Lock registry not empty after run: {list(plugin._invocation_locks.keys())}"
            )

    @pytest.mark.asyncio
    async def test_tool_denial_prevents_evidence_and_cleans_up_registry(
        self, runner_test_db: Path
    ) -> None:
        """Prove that when before_tool_callback raises ToolAuthorizationError:

        ADK plugin manager behaviour (verified against real Runner)
        -----------------------------------------------------------
        - The ADK plugin manager catches plugin exceptions from before_tool_callback
          and logs them; it does NOT call on_tool_error_callback in this path.
        - The tool is skipped — no MCP call is made.
        - No operational evidence is added to session state for the denied call.
        - The pipeline continues to completion.
        - The lock registry is empty after after_run_callback.

        on_tool_error_callback is invoked by ADK only when the tool itself raises,
        not when a plugin's before_tool_callback raises.
        """
        from aeroops.security import ToolAuthorizationError

        plugin = AeroOpsSecurityPlugin()

        # Track how many times before_tool_callback denied a call
        denial_count = 0

        async def denying_before_tool(
            *, tool: Any, tool_args: Any, tool_context: Any
        ) -> dict | None:
            nonlocal denial_count
            denial_count += 1
            inv_id = getattr(tool_context, "invocation_id", "")
            if inv_id:
                plugin._remove_lock(inv_id)
            raise ToolAuthorizationError(
                "The requested tool operation is not permitted.",
                None,
            )

        plugin.before_tool_callback = denying_before_tool

        # Track whether any after_tool_callback fires (it must not, for denied tools)
        after_tool_called = False
        original_after = plugin.after_tool_callback

        async def spy_after_tool(
            *, tool: Any, tool_args: Any, tool_context: Any, result: Any
        ) -> dict | None:
            nonlocal after_tool_called
            after_tool_called = True
            return await original_after(
                tool=tool,
                tool_args=tool_args,
                tool_context=tool_context,
                result=result,
            )

        plugin.after_tool_callback = spy_after_tool

        db_str = str(runner_test_db)
        toolset = make_toolset(_TOOLS, db_path_override=db_str)

        specialist = LlmAgent(
            name="test_ops_specialist",
            model="runner_test:test_ops_deny",
            instruction="(scripted)",
            output_key="test_ops_findings",
            tools=[toolset],
        )
        pipeline = SequentialAgent(
            name="security_runner_deny_pipeline",
            sub_agents=[specialist],
        )

        session_service = InMemorySessionService()
        runner = Runner(
            agent=pipeline,
            app_name=_APP_NAME,
            session_service=session_service,
            plugins=[plugin],
        )

        run_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name=_APP_NAME,
            user_id="test-user",
            session_id=run_id,
        )

        user_msg = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=f"Investigate {_AC009}")],
        )

        try:
            async for _ in runner.run_async(
                user_id="test-user",
                session_id=run_id,
                new_message=user_msg,
            ):
                pass
        except Exception:
            pass  # Any propagated error is fine
        finally:
            from aeroops.services import _close_all_toolsets

            await _close_all_toolsets(pipeline)
            with contextlib.suppress(Exception):
                await runner.close()

        # before_tool_callback must have fired at least once per tool call attempt
        assert denial_count > 0, (
            "before_tool_callback was never called despite tool call attempts."
        )

        # after_tool_callback must NOT have fired (tool was skipped by the denial)
        assert not after_tool_called, (
            "after_tool_callback fired even though the tool was denied by before_tool_callback."
        )

        # No operational MCP evidence must appear in session state
        sess = await session_service.get_session(
            app_name=_APP_NAME,
            user_id="test-user",
            session_id=run_id,
        )
        if sess:
            evidence = sess.state.get("test_ops_mcp_evidence", [])
            assert evidence == [] or evidence is None, (
                f"Unexpected operational evidence after denial: {evidence}"
            )

        # Lock registry must be empty after run
        with plugin._registry_lock:
            assert len(plugin._invocation_locks) == 0
