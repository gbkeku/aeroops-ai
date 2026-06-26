"""Regression tests for the cloud/live AeroOps execution path."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from aeroops.agent import _model_generation_config
from aeroops.config import AeroOpsSettings, configure_live_model_credentials
from aeroops.security import ToolAuthorizationError
from aeroops.security_plugin import AeroOpsSecurityPlugin
from aeroops.services import (
    LiveInvestigationError,
    _normalize_synthesis_evidence,
    _synthesis_error_metadata,
)
from aeroops.toolsets import _connection_params


def test_specialist_mcp_uses_recommended_connection_wrapper(tmp_path, monkeypatch) -> None:
    """Cloud specialist toolsets must not use deprecated bare server params."""
    monkeypatch.setenv("AEROOPS_DB_PATH", str(tmp_path / "aeroops.db"))
    params = _connection_params(str(tmp_path / "aeroops.db"))

    assert isinstance(params, StdioConnectionParams)
    assert isinstance(params.server_params, StdioServerParameters)
    assert params.server_params.command == sys.executable
    assert params.server_params.args == ["-m", "aeroops.mcp_server"]
    assert params.timeout >= 30.0
    assert params.server_params.command != "uv"


def test_live_settings_expose_bounded_resilience_defaults() -> None:
    settings = AeroOpsSettings(_env_file=None)
    assert settings.mcp_timeout_seconds == 30.0
    assert settings.max_model_calls == 24
    assert settings.max_tool_calls == 24
    assert settings.model == "gemini-2.5-flash"
    assert settings.model_request_timeout_ms == 120_000
    assert settings.model_retry_attempts == 4
    assert settings.model_retry_initial_delay_seconds == 1.0
    assert settings.model_retry_max_delay_seconds == 8.0
    assert settings.debug is False


def test_every_model_request_uses_bounded_retry_policy() -> None:
    config = _model_generation_config()
    assert config.temperature == 0.0
    assert config.http_options is not None
    assert config.http_options.timeout == 120_000
    retry = config.http_options.retry_options
    assert retry is not None
    assert retry.attempts == 4
    assert retry.initial_delay == 1.0
    assert retry.max_delay == 8.0
    assert retry.http_status_codes == [408, 429, 500, 502, 503, 504]


def test_live_failed_test_status_alias_is_normalized_to_unfiltered_read() -> None:
    """Gemini's human-domain `failed` label must not abort the live workflow."""
    from aeroops.security import normalize_tool_arguments, validate_tool_execution

    normalized, changes = normalize_tool_arguments(
        "get_test_events",
        {"aircraft_id": "AC-009", "status": "failed"},
    )

    assert normalized == {"aircraft_id": "AC-009"}
    assert changes == ("TEST_STATUS_ALIAS_FILTER_REMOVED",)
    validate_tool_execution("get_test_events", "test_ops_specialist", normalized)


@pytest.mark.asyncio
async def test_live_model_budget_allows_sequential_tool_call_pattern() -> None:
    """A valid live run can require more than the old ten model turns."""
    plugin = AeroOpsSecurityPlugin(max_model_calls=24, max_tool_calls=24)
    context = MagicMock()
    context.invocation_id = "inv-live-sequential"
    context.state = {}
    request = MagicMock()
    request.contents = []

    # Intake (1), specialists issuing one tool per turn (14), synthesis (1).
    for _ in range(16):
        assert (
            await plugin.before_model_callback(
                callback_context=context,
                llm_request=request,
            )
            is None
        )

    assert context.state["temp:security_model_calls"] == 16

    # The budget is still finite and enforced.
    for _ in range(8):
        await plugin.before_model_callback(callback_context=context, llm_request=request)
    with pytest.raises(ToolAuthorizationError):
        await plugin.before_model_callback(callback_context=context, llm_request=request)

    await plugin.close()


def test_live_credentials_select_google_ai_studio_and_restore(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "existing-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    settings = AeroOpsSettings(
        _env_file=None,
        offline_demo=False,
        google_api_key="temporary-key",
    )

    with configure_live_model_credentials(settings):
        assert os.environ["GOOGLE_API_KEY"] == "temporary-key"
        assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "FALSE"

    assert os.environ["GOOGLE_API_KEY"] == "existing-key"
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "TRUE"


def test_synthesis_evidence_union_is_derived_deterministically() -> None:
    payload = {
        "evidence": ["MADE-UP-ORDER"],
        "confirmed_root_causes": [
            {"source_refs": [{"source_id": "DEF-009-042"}, {"source_id": "TEST-009-118"}]}
        ],
        "contributing_factors": [{"source_refs": [{"source_id": "PART-ACT-774"}]}],
        "recommended_actions": [
            {"source_refs": [{"source_id": "DEF-009-042"}, {"source_id": "CR-184"}]}
        ],
    }

    _normalize_synthesis_evidence(payload, "MS-009-FTC")

    assert payload["evidence"] == [
        "CR-184",
        "DEF-009-042",
        "MS-009-FTC",
        "PART-ACT-774",
        "TEST-009-118",
    ]


def test_live_investigation_error_is_sanitized() -> None:
    err = LiveInvestigationError(
        "agent_execution",
        "ServerError",
        provider_code=500,
        provider_status="INTERNAL",
        agent_name="test_ops_specialist",
    )
    assert str(err) == "Live investigation failed during agent_execution."
    assert err.stage == "agent_execution"
    assert err.cause_type == "ServerError"
    assert err.provider_code == 500
    assert err.provider_status == "INTERNAL"
    assert err.agent_name == "test_ops_specialist"
    assert "key" not in str(err).lower()
    assert "path" not in str(err).lower()


@pytest.mark.asyncio
async def test_model_error_callback_records_only_safe_classification() -> None:
    plugin = AeroOpsSecurityPlugin()
    context = MagicMock()
    context.agent_name = "maintenance_specialist"
    context.state = {}
    request = MagicMock()
    error = RuntimeError("secret provider payload must not be retained")
    error.code = 500
    error.status = "INTERNAL"

    assert (
        await plugin.on_model_error_callback(
            callback_context=context,
            llm_request=request,
            error=error,
        )
        is None
    )

    assert context.state["temp:last_model_error"] == {
        "agent_name": "maintenance_specialist",
        "exception_type": "RuntimeError",
        "code": 500,
        "status": "INTERNAL",
        "validation_issues": [],
    }
    assert "secret" not in repr(context.state)
    await plugin.close()


def _normalized_synthesis_state() -> dict:
    """Return the authoritative state expected by the synthesis callback."""
    reports = {
        "test_ops_findings": {
            "domain": "test_operations",
            "aircraft_id": "AC-009",
            "findings": [
                {
                    "finding_id": "FIND-TEST-001",
                    "statement": "TEST-009-118 was aborted.",
                    "classification": "test_failure",
                    "source_refs": [
                        {
                            "source_id": "TEST-009-118",
                            "record_type": "test_event",
                            "summary": "Aborted test event.",
                        }
                    ],
                    "rationale": "The test record has status aborted.",
                    "claims": [],
                },
                {
                    "finding_id": "FIND-TEST-002",
                    "statement": "TEST-009-121 is blocked by DEP-009-001.",
                    "classification": "dependency_blocker",
                    "source_refs": [
                        {
                            "source_id": "TEST-009-121",
                            "record_type": "test_event",
                            "summary": "Blocked retest.",
                        },
                        {
                            "source_id": "DEP-009-001",
                            "record_type": "schedule_dependency",
                            "summary": "Defect dependency.",
                        },
                    ],
                    "rationale": "The dependency graph blocks the retest.",
                    "claims": [],
                },
            ],
            "raw_source_ids": ["TEST-009-118", "TEST-009-121", "DEP-009-001"],
        },
        "maintenance_findings": {
            "domain": "maintenance",
            "aircraft_id": "AC-009",
            "findings": [
                {
                    "finding_id": "FIND-MAINT-001",
                    "statement": "MNT-009-015 remains incomplete.",
                    "classification": "maintenance",
                    "source_refs": [
                        {
                            "source_id": "MNT-009-015",
                            "record_type": "maintenance_task",
                            "summary": "Required post-abort inspection.",
                        }
                    ],
                    "rationale": "The inspection has not been completed.",
                    "claims": [],
                }
            ],
            "raw_source_ids": ["MNT-009-015"],
        },
        "configuration_supply_findings": {
            "domain": "configuration_supply",
            "aircraft_id": "AC-009",
            "findings": [
                {
                    "finding_id": "FIND-CONFIG-001",
                    "statement": "PART-ACT-774 arrives after its need date.",
                    "classification": "parts_constraint",
                    "source_refs": [
                        {
                            "source_id": "PART-ACT-774",
                            "record_type": "parts_constraint",
                            "summary": "Late replacement actuator.",
                        }
                    ],
                    "rationale": "The estimated arrival is after needed_by.",
                    "claims": [],
                }
            ],
            "raw_source_ids": ["PART-ACT-774"],
        },
        "schedule_risk_findings": {
            "domain": "schedule_risk",
            "aircraft_id": "AC-009",
            "findings": [
                {
                    "finding_id": "FIND-SCHEDULE-001",
                    "statement": "MS-009-FTC is forecast six days late.",
                    "classification": "schedule_risk",
                    "source_refs": [
                        {
                            "source_id": "MS-009-FTC",
                            "record_type": "milestone",
                            "summary": "Planned 2026-06-29; forecast 2026-07-05.",
                        }
                    ],
                    "rationale": "The forecast is six days after plan.",
                    "claims": [],
                }
            ],
            "raw_source_ids": ["MS-009-FTC"],
        },
    }
    return {
        "aircraft_id": "AC-009",
        "planned_milestone_date": "2026-06-29",
        "forecast_milestone_date": "2026-07-05",
        "delay_days": 6,
        "milestone_source_id": "MS-009-FTC",
        **{key: json.dumps(value) for key, value in reports.items()},
    }


def test_synthesis_agent_registers_deterministic_after_model_normalizer() -> None:
    from aeroops.agent import create_pipeline
    from aeroops.synthesis import normalize_executive_synthesis_response

    synthesis = create_pipeline().sub_agents[-1]
    assert synthesis.output_schema is None
    assert synthesis.after_model_callback is normalize_executive_synthesis_response
    assert list(synthesis.tools) == []


def test_synthesis_callback_repairs_near_miss_live_json() -> None:
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from aeroops.models import ExecutiveBrief
    from aeroops.synthesis import normalize_executive_synthesis_response

    # This shape intentionally resembles a plausible live-model near miss:
    # string evidence references, missing nested finding fields, null due date,
    # and an unsupported classification value. It cannot validate directly as
    # ExecutiveBrief but must be repaired from validated specialist state.
    candidate = {
        "aircraft_id": "AC-009",
        "overall_status": "RED",
        "planned_milestone_date": "2026-06-29",
        "forecast_milestone_date": "2026-07-05",
        "delay_days": 6,
        "milestone_source_id": "MS-009-FTC",
        "executive_summary": "AC-009 is delayed six days and requires coordinated action.",
        "confirmed_root_causes": ["FIND-TEST-001"],
        "contributing_factors": ["FIND-MAINT-001"],
        "recommended_actions": [
            {
                "action": "Close the blocked test dependencies.",
                "supporting_finding_ids": ["FIND-TEST-002"],
                "source_refs": ["TEST-009-121", "DEP-009-001"],
                "owner_role": "invalid-role",
                "suggested_due_date": None,
            }
        ],
        "confidence": "HIGH",
    }
    context = MagicMock()
    context.state = _normalized_synthesis_state()
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text=json.dumps(candidate))],
        )
    )

    normalized = normalize_executive_synthesis_response(context, response)

    assert normalized is not None
    assert normalized.content is not None
    text = "".join(part.text or "" for part in normalized.content.parts or [])
    brief = ExecutiveBrief.model_validate_json(text)
    assert brief.aircraft_id == "AC-009"
    assert brief.delay_days == 6
    assert brief.overall_status == "red"
    assert {f.finding_id for f in brief.confirmed_root_causes} == {
        "FIND-TEST-001",
        "FIND-TEST-002",
    }
    assert {f.finding_id for f in brief.contributing_factors} == {
        "FIND-MAINT-001",
        "FIND-CONFIG-001",
        "FIND-SCHEDULE-001",
    }
    assert brief.recommended_actions
    assert all(action.source_refs for action in brief.recommended_actions)
    assert "MS-009-FTC" in brief.evidence
    assert context.state["temp:synthesis_normalization"]["status"] == "canonicalized"


def test_pydantic_validation_diagnostics_contain_locations_not_inputs() -> None:
    from pydantic import BaseModel, ValidationError

    class RequiredField(BaseModel):
        count: int

    try:
        RequiredField.model_validate({"count": "secret-invalid-value"})
    except ValidationError as error:
        exc = error
    else:  # pragma: no cover
        raise AssertionError("Expected ValidationError")

    plugin = AeroOpsSecurityPlugin()
    context = MagicMock()
    context.agent_name = "executive_synthesis"
    context.state = {}
    request = MagicMock()

    import asyncio

    asyncio.run(
        plugin.on_model_error_callback(
            callback_context=context,
            llm_request=request,
            error=exc,
        )
    )
    record = context.state["temp:last_model_error"]
    assert record["validation_issues"] == ["count:int_parsing"]
    assert "secret-invalid-value" not in repr(record)
    asyncio.run(plugin.close())


def test_synthesis_callback_builds_fallback_for_non_json_model_text() -> None:
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from aeroops.models import ExecutiveBrief
    from aeroops.synthesis import normalize_executive_synthesis_response

    context = MagicMock()
    context.state = _normalized_synthesis_state()
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="I could not format the requested JSON.")],
        )
    )

    normalized = normalize_executive_synthesis_response(context, response)

    assert normalized is not None
    text = "".join(part.text or "" for part in normalized.content.parts or [])
    brief = ExecutiveBrief.model_validate_json(text)
    assert brief.aircraft_id == "AC-009"
    assert brief.delay_days == 6
    assert brief.overall_status == "red"
    assert brief.recommended_actions
    assert context.state["temp:synthesis_normalization"] == {
        "candidate_json_object": False,
        "original_schema_valid": False,
        "normalization_applied": True,
        "status": "canonicalized",
        "finding_count": 5,
        "action_count": 5,
        "evidence_count": 6,
    }


def test_synthesis_callback_replaces_contradictory_summary() -> None:
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from aeroops.models import ExecutiveBrief
    from aeroops.synthesis import normalize_executive_synthesis_response

    candidate = {
        "overall_status": "green",
        "executive_summary": (
            "AC-009 is only 2 days late and the forecast milestone is 2026-07-09."
        ),
        "recommended_actions": [],
        "confidence": "medium",
    }
    context = MagicMock()
    context.state = _normalized_synthesis_state()
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text=json.dumps(candidate))],
        )
    )

    normalized = normalize_executive_synthesis_response(context, response)

    assert normalized is not None
    text = "".join(part.text or "" for part in normalized.content.parts or [])
    brief = ExecutiveBrief.model_validate_json(text)
    assert brief.overall_status == "red"
    assert brief.delay_days == 6
    assert "2 days" not in brief.executive_summary
    assert "2026-07-09" not in brief.executive_summary
    assert "6 days" in brief.executive_summary


def test_synthesis_error_metadata_is_safe_and_bounded() -> None:
    metadata = _synthesis_error_metadata(
        {
            "temp:synthesis_normalization": {
                "status": "failed",
                "exception_type": "ValidationError",
                "original_validation_errors": [
                    {"location": "recommended_actions.0.owner_role", "type": "literal_error"},
                    {
                        "location": "executive_summary",
                        "type": "string_type",
                        "input": "secret model text must not be retained",
                    },
                ],
            }
        }
    )

    assert metadata == {
        "agent_name": "executive_synthesis",
        "validation_issues": [
            "recommended_actions.0.owner_role:literal_error",
            "executive_summary:string_type",
            "normalizer:ValidationError",
        ],
    }
    assert "secret model text" not in repr(metadata)
