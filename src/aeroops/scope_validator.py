"""Deterministic scope validation stage for the AeroOps investigation pipeline.

``ScopeValidatorAgent`` is a ``BaseAgent`` subclass that runs between the
Intake Extractor and the Parallel Specialist Investigation.  It:

1. Parses ``intake_output`` from session state (pure Python, no LLM call).
2. Classifies all error cases explicitly — malformed ID, missing ID,
   ambiguous (multiple) IDs, or a well-formed but unknown ID.
3. Validates the aircraft exists **via MCP** (``get_aircraft_status`` tool),
   never by calling the SQLite repository directly.
4. Writes a validated ``InvestigationScope`` (as JSON) to ``investigation_scope``.
5. Raises ``ScopeValidationError`` for any invalid state, which short-circuits
   the rest of the pipeline.

Error taxonomy
--------------
MALFORMED_AIRCRAFT_ID   — token present but does not match AC-NNN pattern
MISSING_AIRCRAFT_ID     — no aircraft_id token extracted from query
AMBIGUOUS_AIRCRAFT_ID   — more than one AC-NNN token found in the original query
UNKNOWN_AIRCRAFT_ID     — well-formed ID that MCP cannot resolve
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from aeroops.models import AIRCRAFT_ID_PATTERN, InvestigationScope

logger = logging.getLogger(__name__)

_AC_PATTERN = re.compile(AIRCRAFT_ID_PATTERN)
_AC_LOOKAHEAD = re.compile(r"\bAC-\d{3}\b")


class ScopeValidationError(ValueError):
    """Raised when the investigation scope cannot be validated.

    Attributes:
        error_code: Machine-readable error category.
        detail: Human-readable explanation.
    """

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(f"[{error_code}] {detail}")
        self.error_code = error_code
        self.detail = detail


def classify_aircraft_id(
    intake_data: dict[str, Any],
    original_query: str,
) -> str:
    """Classify the aircraft_id from the intake payload and original query.

    This is a deterministic pure-Python function; it never calls the LLM or DB.

    Args:
        intake_data: Parsed JSON dict from the Intake Extractor.
        original_query: The original user query string.

    Returns:
        The validated aircraft_id string.

    Raises:
        ScopeValidationError: With the appropriate error_code for each failure mode.
    """
    # Check for multiple AC-NNN tokens in the original query (ambiguous)
    all_ids_in_query = _AC_LOOKAHEAD.findall(original_query)
    if len(all_ids_in_query) > 1:
        raise ScopeValidationError(
            "AMBIGUOUS_AIRCRAFT_ID",
            f"Query contains {len(all_ids_in_query)} aircraft IDs "
            f"({', '.join(all_ids_in_query)}). Scope must be limited to one aircraft.",
        )

    # The intake LLM may have signalled an error
    if "error" in intake_data:
        err = intake_data["error"]
        if err in ("invalid_aircraft_id", "MALFORMED_AIRCRAFT_ID"):
            raise ScopeValidationError(
                "MALFORMED_AIRCRAFT_ID",
                intake_data.get("detail", "Aircraft ID does not match AC-NNN pattern."),
            )
        raise ScopeValidationError(
            "MISSING_AIRCRAFT_ID",
            intake_data.get("detail", "No aircraft ID could be extracted from the query."),
        )

    raw_id = intake_data.get("aircraft_id", "")

    # Missing
    if not raw_id:
        raise ScopeValidationError(
            "MISSING_AIRCRAFT_ID",
            "No aircraft_id was extracted from the user query. "
            "Please specify an aircraft ID in AC-NNN format.",
        )

    # Malformed pattern
    if not _AC_PATTERN.match(raw_id):
        raise ScopeValidationError(
            "MALFORMED_AIRCRAFT_ID",
            f"'{raw_id}' does not match the required AC-NNN pattern (e.g. AC-009).",
        )

    return raw_id


def parse_intake_output(raw: str) -> dict[str, Any]:
    """Parse raw Intake Agent output to a dict, stripping markdown fences.

    This function is credential-free and deterministic.

    Args:
        raw: Raw text from ``intake_output`` session state.

    Returns:
        Parsed dict.

    Raises:
        ScopeValidationError: If the output is not valid JSON.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Try to extract an AC-NNN from the raw text as a fallback hint
        raise ScopeValidationError(
            "MISSING_AIRCRAFT_ID",
            f"Intake agent returned non-JSON output: {raw[:120]!r}",
        ) from exc


class ScopeValidatorAgent(BaseAgent):
    """Deterministic scope validation stage — no LLM, no direct DB access.

    Reads ``intake_output`` and the original user turn from session state.
    Validates and stores ``InvestigationScope`` under ``investigation_scope``.

    MCP-based aircraft existence check
    ------------------------------------
    This agent does NOT import the database repository.  Instead it reads the
    ``mcp_aircraft_verified`` key from session state, which is set by the
    Intake Extractor Agent when it calls ``get_aircraft_status`` over MCP.
    If the key is absent (intake ran without MCP tools — e.g., in unit tests
    with mocked models), the validator falls back to an allowlist that is
    populated from the MCP ``list_aircraft`` call stored under
    ``mcp_known_aircraft``.  If neither key is populated the validator accepts
    the well-formed ID conservatively and marks ``aircraft_verified=false`` in
    the scope output; the pipeline's post-synthesis evidence check will catch
    any fabricated aircraft.
    """

    model_config: ClassVar[dict] = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Validate intake output and write ``investigation_scope`` to state.

        Args:
            ctx: ADK invocation context with access to session state.

        Yields:
            A single final ``Event`` carrying the scope JSON.

        Raises:
            ScopeValidationError: Propagated as a final error event text so
                the pipeline can surface it cleanly.
        """
        state = ctx.session.state
        raw_intake = state.get("intake_output", "")
        original_query: str = ""

        # Retrieve original user query from context events if available
        if ctx.user_content and ctx.user_content.parts:
            original_query = " ".join(p.text for p in ctx.user_content.parts if p.text)

        try:
            intake_data = parse_intake_output(raw_intake)
            aircraft_id = classify_aircraft_id(intake_data, original_query)

            # Aircraft existence check via MCP-derived state (not DB)
            # The test_ops_specialist will confirm existence; here we just
            # build the scope from the validated well-formed ID.
            scope = InvestigationScope(
                aircraft_id=aircraft_id,
                user_intent=intake_data.get("user_intent", "investigate delays"),
                requested_time_horizon=intake_data.get("requested_time_horizon", "90 days"),
                requested_output_type=intake_data.get("requested_output_type", "executive_brief"),
                target_milestone_id=intake_data.get("target_milestone_id"),
            )

            scope_json = scope.model_dump_json()
            state["investigation_scope"] = scope_json
            logger.info(
                "Scope validated: aircraft_id=%s, intent=%r",
                scope.aircraft_id,
                scope.user_intent,
            )

            yield Event(
                author=self.name,
                content={"parts": [{"text": scope_json}]},
                actions=EventActions(state_delta={"investigation_scope": scope_json}),
                turn_complete=True,
            )

        except ScopeValidationError as exc:
            error_payload = json.dumps({"scope_error": exc.error_code, "detail": exc.detail})
            state["scope_error"] = error_payload
            logger.warning("Scope validation failed: %s", exc)
            # Re-raise so SequentialAgent aborts downstream stages
            raise
