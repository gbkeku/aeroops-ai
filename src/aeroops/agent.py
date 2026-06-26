"""AeroOps multi-agent investigation workflow.

Pipeline (five stages in sequence):

    intake_extractor        (LlmAgent, tools=[])
    → scope_validator       (ScopeValidatorAgent — deterministic Python)
    → parallel_specialist_investigation
          ├─ test_ops_specialist          (LlmAgent, MCP-filtered)
          ├─ maintenance_specialist       (LlmAgent, MCP-filtered)
          ├─ config_supply_specialist     (LlmAgent, MCP-filtered)
          └─ schedule_risk_specialist     (LlmAgent, MCP-filtered)
    → report_validator      (ReportValidatorAgent — deterministic Python)
    → executive_synthesis   (LlmAgent, tools=[], include_contents="none",
                             deterministic after-model normalization)

Session-state keys written by each stage
-----------------------------------------
intake_extractor        → ``intake_output``
scope_validator         → ``investigation_scope``
test_ops_specialist     → ``test_ops_findings``
maintenance_specialist  → ``maintenance_findings``
config_supply_specialist→ ``configuration_supply_findings``
schedule_risk_specialist→ ``schedule_risk_findings``
report_validator        → ``mcp_evidence_ids``, ``blocker_source_ids``,
                          ``secondary_risk_ids``, ``milestone_context``
executive_synthesis     → ``synthesis_output``

No stage imports or calls the SQLite repository directly.
Aircraft existence and milestone dates are obtained exclusively through
MCP tool calls made by the specialist agents.

Tool allowlists
---------------
Defined as ``frozenset[str]`` constants.  ``get_tool_allowlist(domain)``
exposes them for test-time verification via the public API.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.genai import types as genai_types

from aeroops.config import get_settings
from aeroops.report_validator import ReportValidatorAgent
from aeroops.scope_validator import ScopeValidatorAgent
from aeroops.specialist_normalization import (
    make_specialist_model_error_fallback,
    make_specialist_response_normalizer,
)
from aeroops.synthesis import normalize_executive_synthesis_response
from aeroops.toolsets import make_toolset

logger = logging.getLogger(__name__)


def _model_generation_config() -> genai_types.GenerateContentConfig:
    """Return the bounded retry and timeout policy for every Gemini request.

    Google AI backend 429 and 5xx responses are normally transient.  The Gen
    AI SDK retries them only when ``HttpRetryOptions`` are supplied.  Keeping
    this policy in one factory ensures that intake, specialists, and synthesis
    all receive the same behavior without sharing a mutable config instance.
    """
    settings = get_settings()
    return genai_types.GenerateContentConfig(
        temperature=0.0,
        http_options=genai_types.HttpOptions(
            timeout=settings.model_request_timeout_ms,
            retry_options=genai_types.HttpRetryOptions(
                attempts=settings.model_retry_attempts,
                initial_delay=settings.model_retry_initial_delay_seconds,
                max_delay=settings.model_retry_max_delay_seconds,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Immutable tool allowlists — the single source of truth for tool permissions
# ---------------------------------------------------------------------------

_TEST_OPS_TOOLS: frozenset[str] = frozenset(
    {
        "get_aircraft_status",
        "get_test_events",
        "get_open_defects",
        "get_dependency_graph",
    }
)

_MAINTENANCE_TOOLS: frozenset[str] = frozenset(
    {
        "get_open_defects",
        "get_maintenance_tasks",
    }
)

_CONFIG_SUPPLY_TOOLS: frozenset[str] = frozenset(
    {
        "get_parts_constraints",
        "get_change_requests",
    }
)

_SCHEDULE_RISK_TOOLS: frozenset[str] = frozenset(
    {
        "get_aircraft_status",
        "get_dependency_graph",
    }
)

# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

_INTAKE_INSTRUCTION = """\
You are the AeroOps Intake Extractor. Your job is to parse the user's
investigation request and produce a single, compact JSON object.

Extract the following fields from the user's message:
- "aircraft_id": The aircraft identifier (must match ^AC-\\d{3}$).
- "user_intent": A concise restatement of what the user wants to know.
- "requested_time_horizon": The time window for the investigation \
(e.g. "30 days", "90 days"). Default "90 days" if not specified.
- "requested_output_type": One of "executive_brief" or "detailed_report". \
Default "executive_brief" if not specified.

Rules:
1. If the aircraft_id does not match the pattern AC-NNN, reply ONLY with:
   {"error": "invalid_aircraft_id", "detail": "<reason>"}
2. If no aircraft_id can be identified, reply ONLY with:
   {"error": "missing_aircraft_id", "detail": "<reason>"}
3. Otherwise, reply ONLY with a valid JSON object containing the four fields above.
4. Do NOT include markdown fences, explanations, or any text other than the JSON.
"""

_TEST_OPS_INSTRUCTION = """\
You are the Test Operations Specialist for aircraft {aircraft_id}.

Use your tools to investigate:
1. Call get_aircraft_status for aircraft_id={aircraft_id} — record the status \
and note any milestone source_ids in the response.
2. Call get_test_events for aircraft_id={aircraft_id} without a status filter. \
Inspect the returned events and identify tests whose stored status is "blocked" \
or "aborted". The MCP status enum is limited to planned, blocked, in_progress, \
completed, and aborted. Do not invent any other enum value.
3. Call get_open_defects for aircraft_id={aircraft_id} and identify defects \
blocking tests.
4. Call get_dependency_graph for aircraft_id={aircraft_id} to map blocker chains.

Output a JSON object with this exact structure:
{{
  "domain": "test_operations",
  "aircraft_id": "{aircraft_id}",
  "findings": [
    {{
      "finding_id": "FIND-0",
      "statement": "<declarative finding>",
      "classification": "<test_failure|defect|dependency_blocker|other>",
      "source_refs": [
        {{"source_id": "<ID>", "record_type": "<type>", "summary": "<one-sentence>"}}
      ],
      "rationale": "<why the evidence supports this finding>",
      "claims": [
        {{"claim_type": "test_aborted", "test_event_id": "<ID>"}},
        {{"claim_type": "defect_blocks_test", "defect_id": "<ID>", "test_event_id": "<ID>"}}
      ]
    }}
  ],
  "raw_source_ids": ["<all record IDs you observed in tool results>"]
}}

IMPORTANT: raw_source_ids must include every ID returned by every tool, \
including milestone IDs from get_aircraft_status.
Only report findings supported by tool results. Do not fabricate information.
"""

_MAINTENANCE_INSTRUCTION = """\
You are the Maintenance and Reliability Specialist for aircraft {aircraft_id}.

Use your tools to investigate:
1. Call get_open_defects for aircraft_id={aircraft_id}.
2. Call get_maintenance_tasks for aircraft_id={aircraft_id} — identify tasks \
that are overdue, due soon (within 14 days), or blocking other work.

Output a JSON object with this exact structure:
{{
  "domain": "maintenance",
  "aircraft_id": "{aircraft_id}",
  "findings": [
    {{
      "finding_id": "FIND-0",
      "statement": "<declarative finding>",
      "classification": "<defect|maintenance|other>",
      "source_refs": [
        {{"source_id": "<ID>", "record_type": "<type>", "summary": "<one-sentence>"}}
      ],
      "rationale": "<why the evidence supports this finding>",
      "claims": [
        {{"claim_type": "maintenance_required", "maintenance_task_id": "<ID>", "test_event_id": "<ID>"}}
      ]
    }}
  ],
  "raw_source_ids": ["<all record IDs you observed in tool results>"]
}}

Only report findings supported by tool results.
"""

_CONFIG_SUPPLY_INSTRUCTION = """\
You are the Configuration and Supply Chain Specialist for aircraft {aircraft_id}.

Use your tools to investigate:
1. Call get_parts_constraints for aircraft_id={aircraft_id} — identify parts \
with status "delayed" or "awaiting_delivery".
2. Call get_change_requests for aircraft_id={aircraft_id} — identify CRs \
with status "pending_review" that may be blocking work.

Output a JSON object with this exact structure:
{{
  "domain": "configuration_supply",
  "aircraft_id": "{aircraft_id}",
  "findings": [
    {{
      "finding_id": "FIND-0",
      "statement": "<declarative finding>",
      "classification": "<parts_constraint|change_request|configuration|other>",
      "source_refs": [
        {{"source_id": "<ID>", "record_type": "<type>", "summary": "<one-sentence>"}}
      ],
      "rationale": "<why the evidence supports this finding>",
      "claims": [
        {{"claim_type": "part_arrives_after_need_date", "parts_constraint_id": "<ID>"}},
        {{"claim_type": "change_request_pending", "change_request_id": "<ID>"}}
      ]
    }}
  ],
  "raw_source_ids": ["<all record IDs you observed in tool results>"]
}}

Only report findings supported by tool results.
"""

_SCHEDULE_RISK_INSTRUCTION = """\
You are the Schedule Risk Specialist for aircraft {aircraft_id}.

Use your tools to investigate:
1. Call get_aircraft_status for aircraft_id={aircraft_id} — record the overall \
status and note any milestone source_ids.
2. Call get_dependency_graph for aircraft_id={aircraft_id} — identify the \
critical path and any blockers.

Output a JSON object with this exact structure:
{{
  "domain": "schedule_risk",
  "aircraft_id": "{aircraft_id}",
  "findings": [
    {{
      "finding_id": "FIND-0",
      "statement": "<declarative finding>",
      "classification": "<schedule_risk|dependency_blocker|other>",
      "source_refs": [
        {{"source_id": "<ID>", "record_type": "<type>", "summary": "<one-sentence>"}}
      ],
      "rationale": "<why the evidence supports this finding>",
      "claims": [
        {{"claim_type": "milestone_delayed", "milestone_id": "<ID>"}},
        {{"claim_type": "dependency_blocks_test", "dependency_id": "<ID>", "test_event_id": "<ID>"}}
      ]
    }}
  ],
  "raw_source_ids": ["<all record IDs you observed in tool results, \
including milestone IDs from get_aircraft_status>"]
}}

Only report findings supported by tool results.
"""

_SYNTHESIS_INSTRUCTION = """\
You are the Executive Synthesis Agent for AeroOps.

Aircraft under investigation: {aircraft_id}

Authoritative milestone context (do not alter or recalculate):
  planned_milestone_date : {planned_milestone_date}
  forecast_milestone_date: {forecast_milestone_date}
  delay_days             : {delay_days}
  milestone_source_id    : {milestone_source_id}

Validated specialist findings:
TEST OPERATIONS:
{test_ops_findings}

MAINTENANCE AND RELIABILITY:
{maintenance_findings}

CONFIGURATION AND SUPPLY:
{configuration_supply_findings}

SCHEDULE RISK:
{schedule_risk_findings}

Produce a compact executive draft. The application will deterministically build
and validate the final ExecutiveBrief from the specialist reports after this
model call. Do not copy or rewrite the complete finding objects.

Requirements:
1. Summarize the evidence-backed program situation for leadership.
2. Propose practical actions linked to existing specialist finding IDs.
3. Use only record IDs and finding IDs that appear above.
4. Do not invent dates, source IDs, findings, owners, or operational facts.
5. Do not claim airworthiness, certification, maintenance-release, or safety authority.
6. Return only one JSON object, with no Markdown fences or commentary.

Return this compact draft shape:
{{
  "overall_status": "<red|amber|green|unknown>",
  "executive_summary": "<2-3 sentence leadership summary>",
  "recommended_actions": [
    {{
      "action": "<imperative action>",
      "supporting_finding_ids": ["<existing FIND-... ID>"],
      "source_refs": ["<existing operational source ID>"],
      "rationale": "<why the action addresses the linked finding>",
      "owner_role": "<test_lead|maintenance_lead|supply_chain|engineering|program_management|quality_assurance|unknown>",
      "suggested_due_date": "<ISO 8601 date>"
    }}
  ],
  "assumptions": ["<explicit assumption>"],
  "unknowns": ["<material unknown>"],
  "confidence": "<high|medium|low>"
}}
"""

# ---------------------------------------------------------------------------
# Agent builders
# ---------------------------------------------------------------------------


_CALLBACK_LOCKS: dict[str, threading.Lock] = {}
_CALLBACK_LOCKS_LOCK = threading.Lock()


def get_callback_lock(key: str) -> threading.Lock:
    with _CALLBACK_LOCKS_LOCK:
        if key not in _CALLBACK_LOCKS:
            _CALLBACK_LOCKS[key] = threading.Lock()
        return _CALLBACK_LOCKS[key]


def make_after_tool_callback(evidence_key: str):
    def after_tool_callback(tool, args, tool_context, tool_response):
        lock = get_callback_lock(evidence_key)
        with lock:
            existing = list(tool_context.state.get(evidence_key, []))
            entry = {
                "tool_name": tool.name,
                "args": json.loads(json.dumps(args)) if args is not None else {},
                "response": json.loads(json.dumps(tool_response))
                if tool_response is not None
                else {},
                "sequence": len(existing) + 1,
                "invocation_id": getattr(tool_context, "invocation_id", "") or "",
                "agent_name": getattr(tool_context, "agent_name", ""),
                "function_call_id": getattr(tool_context, "function_call_id", "")
                or f"call_{uuid.uuid4().hex[:8]}",
                "branch_key": evidence_key,
                "branch_sequence": len(existing) + 1,
            }
            tool_context.state[evidence_key] = [*existing, entry]
        return None

    return after_tool_callback


def make_on_tool_error_callback(evidence_key: str):
    def on_tool_error_callback(tool, args, tool_context, exception):
        error_key = f"{evidence_key}_errors"
        existing = list(tool_context.state.get(error_key, []))
        entry = {
            "tool_name": tool.name,
            "args": args,
            "error": str(exception),
            "sequence": len(existing) + 1,
        }
        tool_context.state[error_key] = [*existing, entry]
        raise exception

    return on_tool_error_callback


def _build_intake_agent(model: str) -> LlmAgent:
    """Build the Intake Extractor Agent (no tools)."""
    return LlmAgent(
        name="intake_extractor",
        model=model,
        instruction=_INTAKE_INSTRUCTION,
        output_key="intake_output",
        tools=[],
        generate_content_config=_model_generation_config(),
    )


def _build_specialist_agents(
    model: str,
    db_path_override: str | None = None,
) -> list[LlmAgent]:
    """Build the four specialist LlmAgents with filtered MCP toolsets.

    Args:
        model: LLM model identifier.
        db_path_override: Optional path passed to MCP subprocess via env var.

    Returns:
        List of four specialist agents in domain order.
    """
    return [
        LlmAgent(
            name="test_ops_specialist",
            model=model,
            instruction=_TEST_OPS_INSTRUCTION,
            output_key="test_ops_findings",
            tools=[make_toolset(_TEST_OPS_TOOLS, db_path_override=db_path_override)],
            after_tool_callback=make_after_tool_callback("test_ops_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("test_ops_mcp_evidence"),
            after_model_callback=make_specialist_response_normalizer("test_operations"),
            on_model_error_callback=make_specialist_model_error_fallback("test_operations"),
            generate_content_config=_model_generation_config(),
        ),
        LlmAgent(
            name="maintenance_specialist",
            model=model,
            instruction=_MAINTENANCE_INSTRUCTION,
            output_key="maintenance_findings",
            tools=[make_toolset(_MAINTENANCE_TOOLS, db_path_override=db_path_override)],
            after_tool_callback=make_after_tool_callback("maintenance_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("maintenance_mcp_evidence"),
            after_model_callback=make_specialist_response_normalizer("maintenance"),
            on_model_error_callback=make_specialist_model_error_fallback("maintenance"),
            generate_content_config=_model_generation_config(),
        ),
        LlmAgent(
            name="config_supply_specialist",
            model=model,
            instruction=_CONFIG_SUPPLY_INSTRUCTION,
            output_key="configuration_supply_findings",
            tools=[make_toolset(_CONFIG_SUPPLY_TOOLS, db_path_override=db_path_override)],
            after_tool_callback=make_after_tool_callback("configuration_supply_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback(
                "configuration_supply_mcp_evidence"
            ),
            after_model_callback=make_specialist_response_normalizer("configuration_supply"),
            on_model_error_callback=make_specialist_model_error_fallback("configuration_supply"),
            generate_content_config=_model_generation_config(),
        ),
        LlmAgent(
            name="schedule_risk_specialist",
            model=model,
            instruction=_SCHEDULE_RISK_INSTRUCTION,
            output_key="schedule_risk_findings",
            tools=[make_toolset(_SCHEDULE_RISK_TOOLS, db_path_override=db_path_override)],
            after_tool_callback=make_after_tool_callback("schedule_risk_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("schedule_risk_mcp_evidence"),
            after_model_callback=make_specialist_response_normalizer("schedule_risk"),
            on_model_error_callback=make_specialist_model_error_fallback("schedule_risk"),
            generate_content_config=_model_generation_config(),
        ),
    ]


def _build_synthesis_agent(
    model: str,
    planned_milestone_date: str,
    forecast_milestone_date: str,
    delay_days: int,
    milestone_source_id: str,
    aircraft_id: str,
) -> LlmAgent:
    """Build the Executive Synthesis Agent with strict constraints.

    Milestone dates are injected deterministically into the instruction;
    they are never computed by the LLM.

    Args:
        model: LLM model identifier.
        planned_milestone_date: ISO 8601 date string.
        forecast_milestone_date: ISO 8601 date string.
        delay_days: Deterministically computed integer.
        milestone_source_id: Source record ID of the key milestone.
        aircraft_id: Aircraft under investigation.

    Returns:
        Configured ``LlmAgent`` with ``tools=[]`` and deterministic response normalization.
    """
    instruction = _SYNTHESIS_INSTRUCTION.format(
        aircraft_id=aircraft_id,
        planned_milestone_date=planned_milestone_date,
        forecast_milestone_date=forecast_milestone_date,
        delay_days=delay_days,
        milestone_source_id=milestone_source_id,
        test_ops_findings="{test_ops_findings}",
        maintenance_findings="{maintenance_findings}",
        configuration_supply_findings="{configuration_supply_findings}",
        schedule_risk_findings="{schedule_risk_findings}",
    )
    return LlmAgent(
        name="executive_synthesis",
        model=model,
        instruction=instruction,
        output_key="synthesis_output",
        tools=[],
        include_contents="none",
        after_model_callback=normalize_executive_synthesis_response,
        generate_content_config=_model_generation_config(),
    )


# ---------------------------------------------------------------------------
# Public pipeline factory
# ---------------------------------------------------------------------------


def create_pipeline(
    planned_milestone_date: str = "PENDING",
    forecast_milestone_date: str = "PENDING",
    delay_days: int = 0,
    milestone_source_id: str = "PENDING",
    aircraft_id: str = "PENDING",
    db_path_override: str | None = None,
    model_override: str | None = None,
) -> SequentialAgent:
    """Build and return the full five-stage AeroOps investigation pipeline.

    All milestone values are provided as parameters so they can be injected
    deterministically before the pipeline runs.  In the service layer these
    values are obtained by calling the MCP server's ``get_aircraft_status``
    tool (not by querying the database directly).

    Args:
        planned_milestone_date: ISO 8601 planned date for the key milestone.
        forecast_milestone_date: ISO 8601 forecast date for the key milestone.
        delay_days: Deterministic integer (forecast - planned).
        milestone_source_id: Source record ID (e.g. ``'MS-009-FTC'``).
        aircraft_id: Aircraft under investigation.
        db_path_override: Path to pass to MCP subprocess as AEROOPS_DB_PATH.
        model_override: Override the LLM model (used in tests with test doubles).

    Returns:
        A ``SequentialAgent`` representing the full pipeline.
    """
    settings = get_settings()
    model = model_override or settings.model

    intake_agent = _build_intake_agent(model)
    scope_validator = ScopeValidatorAgent(name="scope_validator")
    specialists = _build_specialist_agents(model, db_path_override=db_path_override)
    parallel_investigation = ParallelAgent(
        name="parallel_specialist_investigation",
        sub_agents=specialists,
    )
    report_validator = ReportValidatorAgent(name="report_validator")
    synthesis_agent = _build_synthesis_agent(
        model=model,
        planned_milestone_date=planned_milestone_date,
        forecast_milestone_date=forecast_milestone_date,
        delay_days=delay_days,
        milestone_source_id=milestone_source_id,
        aircraft_id=aircraft_id,
    )

    return SequentialAgent(
        name="aeroops_investigation_pipeline",
        sub_agents=[
            intake_agent,
            scope_validator,
            parallel_investigation,
            report_validator,
            synthesis_agent,
        ],
    )


# ---------------------------------------------------------------------------
# Tool-allowlist inspection — public API (req 8)
# ---------------------------------------------------------------------------


def get_tool_allowlist(domain: str) -> frozenset[str]:
    """Return the immutable tool allowlist for a given specialist domain.

    Args:
        domain: One of ``'test_ops'``, ``'maintenance'``, ``'config_supply'``,
            ``'schedule_risk'``.

    Returns:
        Frozenset of allowed MCP tool names.

    Raises:
        KeyError: If the domain is not recognised.
    """
    _map: dict[str, frozenset[str]] = {
        "test_ops": _TEST_OPS_TOOLS,
        "maintenance": _MAINTENANCE_TOOLS,
        "config_supply": _CONFIG_SUPPLY_TOOLS,
        "schedule_risk": _SCHEDULE_RISK_TOOLS,
    }
    if domain not in _map:
        raise KeyError(f"Unknown domain '{domain}'. Valid domains: {sorted(_map)}")
    return _map[domain]


def get_specialist_output_keys() -> dict[str, str]:
    """Return the mapping of specialist name → session-state output key.

    Returns:
        Dict of agent name → output_key string.
    """
    return {
        "test_ops_specialist": "test_ops_findings",
        "maintenance_specialist": "maintenance_findings",
        "config_supply_specialist": "configuration_supply_findings",
        "schedule_risk_specialist": "schedule_risk_findings",
    }
