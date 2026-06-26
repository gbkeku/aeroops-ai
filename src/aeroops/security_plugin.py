"""AeroOps ADK security plugin implementing BasePlugin lifecycle hooks."""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from aeroops.security import (
    SecurityReasonCode,
    check_and_increment_budget,
    log_audit_event,
    normalize_tool_arguments,
    sanitize_model_bound_data,
    sanitize_text_if_json,
    validate_tool_execution,
    validate_tool_result,
    validate_user_query,
)

logger = logging.getLogger(__name__)


class AeroOpsSecurityPlugin(BasePlugin):
    """A reusable ADK security plugin that enforces security policies across the agent lifecycle.

    Serializable integer counters are tracked in invocation-scoped session state
    (``temp:security_*`` keys).  Per-invocation ``threading.Lock`` objects are
    stored exclusively in this plugin's own ``_invocation_locks`` registry —
    never in ADK session/tool/callback context state, which must remain
    JSON-serializable at all times.

    Lock lifecycle
    --------------
    - Created in ``on_user_message_callback`` (start of invocation).
    - Used in every budget check via ``check_and_increment_budget``.
    - Removed in ``after_run_callback`` (normal completion, denial, failure).
    - All remaining locks cleared in ``close`` (plugin shutdown).
    """

    def __init__(
        self,
        *,
        max_model_calls: int | None = None,
        max_tool_calls: int | None = None,
    ) -> None:
        super().__init__(name="aeroops_security")
        from aeroops.config import get_settings

        settings = get_settings()
        self._max_model_calls = max_model_calls or settings.max_model_calls
        self._max_tool_calls = max_tool_calls or settings.max_tool_calls
        # Registry-level lock protects _invocation_locks mutations.
        self._registry_lock: threading.Lock = threading.Lock()
        # Per-invocation locks keyed by invocation_id string.
        self._invocation_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_lock(self, invocation_id: str) -> threading.Lock:
        """Return the existing lock for this invocation, creating it if needed."""
        with self._registry_lock:
            if invocation_id not in self._invocation_locks:
                self._invocation_locks[invocation_id] = threading.Lock()
            return self._invocation_locks[invocation_id]

    def _remove_lock(self, invocation_id: str) -> None:
        """Remove the per-invocation lock from the registry."""
        with self._registry_lock:
            self._invocation_locks.pop(invocation_id, None)

    def _budget_lock(self, tool_context: ToolContext | CallbackContext) -> threading.Lock:
        """Retrieve the per-invocation lock using the invocation_id from context."""
        inv_id: str = getattr(tool_context, "invocation_id", "") or ""
        return self._get_or_create_lock(inv_id)

    # ------------------------------------------------------------------
    # BasePlugin hooks
    # ------------------------------------------------------------------

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> types.Content | None:
        """Validate user query and initialize budget counters in session state."""
        inv_id: str = invocation_context.invocation_id or ""

        # Create the per-invocation lock in the registry (not in session state).
        self._get_or_create_lock(inv_id)

        # Initialize invocation budget counters in session state.
        # Only serializable integers are stored here — no Lock objects.
        state = invocation_context.session.state
        state.setdefault("temp:security_model_calls", 0)
        state.setdefault("temp:security_tool_calls", 0)
        state.setdefault("temp:security_result_bytes", 0)
        state.setdefault("temp:security_denials", 0)

        # Defense-in-depth: Validate user query if present
        if user_message and user_message.parts:
            query = " ".join(p.text for p in user_message.parts if p.text)
            try:
                validate_user_query(query)
            except Exception as exc:
                reason = getattr(exc, "reason_code", SecurityReasonCode.UNSUPPORTED_MESSAGE_TYPE)
                state["temp:security_denials"] = state.get("temp:security_denials", 0) + 1
                log_audit_event(
                    event_type="user_query_denied",
                    audit_session_id=invocation_context.session_id,
                    invocation_id=inv_id,
                    decision="denied",
                    reason_code=reason,
                )
                self._remove_lock(inv_id)
                raise

        return None

    async def before_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> types.Content | None:
        """Hook called before the Runner starts executing."""
        return None

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Hook called before any agent executes."""
        return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Hook called after an agent successfully completes execution."""
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Check model budget and sanitize the model-bound representation of the request."""
        # A live model may issue one function call per turn.  The configurable
        # budget remains bounded while allowing the full specialist workflow.
        lock = self._budget_lock(callback_context)
        check_and_increment_budget(
            callback_context.state,
            "model_calls",
            limit=self._max_model_calls,
            lock=lock,
        )

        # 2. Sanitize model-bound request contents (preserving the original canonical evidence)
        if llm_request.contents:
            sanitized_contents = []
            for content in llm_request.contents:
                content_copy = copy.deepcopy(content)
                if content_copy.parts:
                    for part in content_copy.parts:
                        if part.function_response:
                            if (
                                hasattr(part.function_response, "response")
                                and part.function_response.response
                            ):
                                part.function_response.response = sanitize_model_bound_data(
                                    part.function_response.response
                                )
                        elif part.text:
                            part.text = sanitize_text_if_json(part.text)
                sanitized_contents.append(content_copy)
            llm_request.contents = sanitized_contents

        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Hook called after a model successfully returns a response."""
        return None

    async def on_model_error_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
        error: Exception,
    ) -> LlmResponse | None:
        """Record a sanitized provider failure and propagate the original error.

        The Gen AI SDK exposes safe classification fields such as ``code`` and
        ``status`` on API errors.  Persisting only those fields and the current
        agent name makes live failures diagnosable without retaining prompts,
        provider response bodies, credentials, or stack traces.
        """
        del llm_request  # The request may contain prompts and tool payloads.
        code = getattr(error, "code", None)
        status = getattr(error, "status", None)
        agent_name = getattr(callback_context, "agent_name", "") or "unknown"

        validation_issues: list[str] = []
        errors_method = getattr(error, "errors", None)
        if callable(errors_method):
            try:
                for item in errors_method(include_input=False, include_url=False)[:8]:
                    location = ".".join(str(part) for part in item.get("loc", ())) or "root"
                    issue_type = str(item.get("type", "validation_error"))
                    validation_issues.append(f"{location}:{issue_type}")
            except Exception:
                validation_issues = []

        callback_context.state["temp:last_model_error"] = {
            "agent_name": agent_name,
            "exception_type": type(error).__name__,
            "code": code if isinstance(code, int) else None,
            "status": status if isinstance(status, str) else None,
            "validation_issues": validation_issues,
        }
        logger.error(
            "model_call_failed agent=%s exception_type=%s code=%s status=%s",
            agent_name,
            type(error).__name__,
            code,
            status,
        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict | None:
        """Check tool budget, validate tool allowlist and parameter arguments."""
        # Enforce a bounded, configurable tool budget.
        lock = self._budget_lock(tool_context)
        check_and_increment_budget(
            tool_context.state,
            "tool_calls",
            limit=self._max_tool_calls,
            lock=lock,
        )

        # 2. Normalize only narrowly approved read-filter aliases before
        # strict authorization/schema validation.  The dict is mutated in
        # place because ADK passes the same object to the eventual tool call.
        normalized_args, normalization_codes = normalize_tool_arguments(tool.name, tool_args)
        if normalized_args != tool_args:
            tool_args.clear()
            tool_args.update(normalized_args)

        agent_name = getattr(tool_context, "agent_name", "")
        validate_tool_execution(tool.name, agent_name, tool_args)

        if normalization_codes:
            log_audit_event(
                event_type="tool_arguments_normalized",
                audit_session_id=tool_context.session.id,
                invocation_id=tool_context.invocation_id,
                decision="allowed",
                agent_name=agent_name,
                tool_name=tool.name,
                result_count=len(normalization_codes),
            )

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> dict | None:
        """Validate response envelope, record metadata, and return None to keep evidence unmodified."""
        # 1. Envelope, size, and count verification
        validate_tool_result(tool.name, result)

        # Update budget counter for bytes
        import json

        try:
            serialized_size = len(json.dumps(result))
            state = tool_context.state
            # Use the per-invocation lock for this atomic update too
            lock = self._budget_lock(tool_context)
            with lock:
                current_bytes = state.get("temp:security_result_bytes", 0)
                state["temp:security_result_bytes"] = current_bytes + serialized_size
        except Exception:
            pass

        # 2. Log allowed tool call
        log_audit_event(
            event_type="tool_execution_allowed",
            audit_session_id=tool_context.session.id,
            invocation_id=tool_context.invocation_id,
            decision="allowed",
            agent_name=getattr(tool_context, "agent_name", ""),
            tool_name=tool.name,
            result_count=len(result.get("data", []))
            if isinstance(result.get("data"), list)
            else 1,
        )

        # Must return None to preserve the specialist evidence callback capturing unmodified evidence
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict | None:
        """Log sanitized metadata and return None to propagate the required exception."""
        state = tool_context.state
        lock = self._budget_lock(tool_context)
        with lock:
            state["temp:security_denials"] = state.get("temp:security_denials", 0) + 1

        log_audit_event(
            event_type="tool_execution_failed",
            audit_session_id=tool_context.session.id,
            invocation_id=tool_context.invocation_id,
            decision="denied",
            agent_name=getattr(tool_context, "agent_name", ""),
            tool_name=tool.name,
            reason_code=SecurityReasonCode.INVALID_TOOL_ARGUMENTS,
        )
        # Returns None to propagate error to parent workflow / client
        return None

    async def after_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> None:
        """Remove the per-invocation lock from the registry on completion."""
        inv_id: str = invocation_context.invocation_id or ""
        self._remove_lock(inv_id)

    async def close(self) -> None:
        """Clear all remaining per-invocation locks on plugin shutdown."""
        with self._registry_lock:
            self._invocation_locks.clear()
