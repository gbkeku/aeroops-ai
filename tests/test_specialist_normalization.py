"""Regression tests for live specialist-response normalization."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from aeroops.agent import create_pipeline
from aeroops.models import SpecialistReport
from aeroops.report_validator import ReportValidatorAgent
from aeroops.specialist_normalization import (
    _SPECS,
    canonicalize_specialist_report,
    make_specialist_model_error_fallback,
    make_specialist_response_normalizer,
)


def _envelope(data, source_refs: list[str]) -> dict:
    return {
        "snapshot_date": "2026-06-24",
        "synthetic_data": True,
        "source_refs": source_refs,
        "data": data,
        "count": len(data) if isinstance(data, list) else 1,
        "truncated": False,
    }


def _dependency_graph() -> dict:
    nodes = [
        {
            "id": "TEST-009-121",
            "type": "test_event",
            "name_or_title": "High-speed taxi and initial rotation",
            "status": "blocked",
        },
        {
            "id": "DEF-009-042",
            "type": "defect",
            "name_or_title": "Flight-control actuator position mismatch",
            "status": "open",
        },
        {
            "id": "PART-ACT-774",
            "type": "parts_constraint",
            "name_or_title": "Flight-control actuator assembly",
            "status": "awaiting_delivery",
        },
        {
            "id": "CR-184",
            "type": "change_request",
            "name_or_title": "Actuator threshold adjustment",
            "status": "pending_review",
        },
        {
            "id": "MNT-009-015",
            "type": "maintenance_task",
            "name_or_title": "Post-abort inspection",
            "status": "scheduled",
        },
    ]
    dependencies = [
        {
            "source_id": "DEP-009-001",
            "aircraft_id": "AC-009",
            "blocked_test_id": "TEST-009-121",
            "blocker_defect_id": "DEF-009-042",
            "blocker_parts_constraint_id": None,
            "blocker_change_request_id": None,
            "blocker_maintenance_task_id": None,
        },
        {
            "source_id": "DEP-009-002",
            "aircraft_id": "AC-009",
            "blocked_test_id": "TEST-009-121",
            "blocker_defect_id": None,
            "blocker_parts_constraint_id": "PART-ACT-774",
            "blocker_change_request_id": None,
            "blocker_maintenance_task_id": None,
        },
        {
            "source_id": "DEP-009-003",
            "aircraft_id": "AC-009",
            "blocked_test_id": "TEST-009-121",
            "blocker_defect_id": None,
            "blocker_parts_constraint_id": None,
            "blocker_change_request_id": "CR-184",
            "blocker_maintenance_task_id": None,
        },
        {
            "source_id": "DEP-009-004",
            "aircraft_id": "AC-009",
            "blocked_test_id": "TEST-009-121",
            "blocker_defect_id": None,
            "blocker_parts_constraint_id": None,
            "blocker_change_request_id": None,
            "blocker_maintenance_task_id": "MNT-009-015",
        },
    ]
    return _envelope(
        {
            "aircraft_id": "AC-009",
            "nodes": nodes,
            "edges": [],
            "dependencies": dependencies,
        },
        [node["id"] for node in nodes] + [dep["source_id"] for dep in dependencies],
    )


def _captured_state() -> dict:
    test_events = _envelope(
        [
            {
                "source_id": "TEST-009-118",
                "aircraft_id": "AC-009",
                "name": "Low-speed taxi and brake test",
                "status": "aborted",
            },
            {
                "source_id": "TEST-009-121",
                "aircraft_id": "AC-009",
                "name": "High-speed taxi and initial rotation",
                "status": "blocked",
            },
        ],
        ["TEST-009-118", "TEST-009-121"],
    )
    defects = _envelope(
        [
            {
                "source_id": "DEF-009-042",
                "aircraft_id": "AC-009",
                "title": "Flight-control actuator position mismatch",
                "status": "open",
                "severity": "high",
            }
        ],
        ["DEF-009-042"],
    )
    maintenance = _envelope(
        [
            {
                "source_id": "MNT-009-015",
                "aircraft_id": "AC-009",
                "title": "Post-abort inspection",
                "status": "scheduled",
                "due_date": "2026-06-26",
            }
        ],
        ["MNT-009-015"],
    )
    parts = _envelope(
        [
            {
                "source_id": "PART-ACT-774",
                "aircraft_id": "AC-009",
                "description": "Flight-control actuator assembly",
                "status": "awaiting_delivery",
                "needed_by": "2026-06-27",
                "estimated_arrival": "2026-06-30",
            }
        ],
        ["PART-ACT-774"],
    )
    changes = _envelope(
        [
            {
                "source_id": "CR-184",
                "aircraft_id": "AC-009",
                "title": "Actuator threshold adjustment",
                "status": "pending_review",
            }
        ],
        ["CR-184"],
    )
    aircraft = _envelope(
        {
            "source_id": "AC-009",
            "name": "AC-009 Avionics Testbed",
            "status": "red",
        },
        ["AC-009"],
    )

    def entry(tool_name: str, response: dict, sequence: int) -> dict:
        return {
            "tool_name": tool_name,
            "response": response,
            "args": {"aircraft_id": "AC-009"},
            "sequence": sequence,
            "branch_sequence": sequence,
            "invocation_id": "inv-test",
            "function_call_id": f"call-{sequence}",
        }

    graph = _dependency_graph()
    return {
        "aircraft_id": "AC-009",
        "investigation_scope": json.dumps(
            {
                "aircraft_id": "AC-009",
                "user_intent": "Explain the delay",
                "requested_time_horizon": "90 days",
                "requested_output_type": "executive_brief",
            }
        ),
        "test_ops_mcp_evidence": [
            entry("get_aircraft_status", aircraft, 1),
            entry("get_test_events", test_events, 2),
            entry("get_open_defects", defects, 3),
            entry("get_dependency_graph", graph, 4),
        ],
        "maintenance_mcp_evidence": [
            entry("get_open_defects", defects, 1),
            entry("get_maintenance_tasks", maintenance, 2),
        ],
        "configuration_supply_mcp_evidence": [
            entry("get_parts_constraints", parts, 1),
            entry("get_change_requests", changes, 2),
        ],
        "schedule_risk_mcp_evidence": [
            entry("get_aircraft_status", aircraft, 1),
            entry("get_dependency_graph", graph, 2),
        ],
    }


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(
        content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)])
    )


def test_specialist_callback_does_not_replace_function_call_turn() -> None:
    callback = make_specialist_response_normalizer("test_operations")
    context = SimpleNamespace(state=_captured_state())
    response = LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        name="get_test_events",
                        args={"aircraft_id": "AC-009"},
                    )
                )
            ],
        )
    )

    assert callback(context, response) is None
    assert "temp:test_ops_findings_normalization" not in context.state


def test_specialist_callback_repairs_live_near_miss_json() -> None:
    callback = make_specialist_response_normalizer("test_operations")
    context = SimpleNamespace(state=_captured_state())
    response = _text_response(
        """```json
        {
          "domain": "test ops",
          "aircraft_id": "AC-999",
          "findings": [
            {
              "statement": "DEF-009-042 blocks TEST-009-121",
              "classification": "blocker",
              "source_refs": ["DEF-009-042"],
              "claims": [{"claim_type": "unsupported_live_shape"}]
            }
          ]
        }
        ```"""
    )

    normalized = callback(context, response)
    assert normalized is not None
    report = SpecialistReport.model_validate_json(normalized.content.parts[0].text)
    assert report.domain == "test_operations"
    assert report.aircraft_id == "AC-009"
    assert report.findings
    assert all(finding.source_refs for finding in report.findings)
    cited = {ref.source_id for finding in report.findings for ref in finding.source_refs}
    assert "DEF-009-042" in cited
    assert "DEP-009-001" in cited
    assert "TEST-009-121" in cited
    assert "AC-999" not in cited
    assert context.state["temp:test_ops_findings_normalization"]["status"] == "canonicalized"


def test_specialist_callback_rejects_unreturned_source_id() -> None:
    callback = make_specialist_response_normalizer("configuration_supply")
    context = SimpleNamespace(state=_captured_state())
    response = _text_response(
        json.dumps(
            {
                "findings": [
                    {
                        "statement": "PART-FAKE-999 is unavailable",
                        "source_refs": ["PART-FAKE-999"],
                    }
                ]
            }
        )
    )

    normalized = callback(context, response)
    assert normalized is not None
    report = SpecialistReport.model_validate_json(normalized.content.parts[0].text)
    cited = {ref.source_id for finding in report.findings for ref in finding.source_refs}
    assert "PART-FAKE-999" not in cited
    assert cited == {"PART-ACT-774", "CR-184"}


def test_all_live_specialists_register_deterministic_normalizers() -> None:
    pipeline = create_pipeline()
    parallel = pipeline.sub_agents[2]
    callbacks = {
        agent.name: getattr(agent.after_model_callback, "__name__", "")
        for agent in parallel.sub_agents
    }
    error_callbacks = {
        agent.name: getattr(agent.on_model_error_callback, "__name__", "")
        for agent in parallel.sub_agents
    }
    assert callbacks == {
        "test_ops_specialist": "normalize_test_operations_specialist_response",
        "maintenance_specialist": "normalize_maintenance_specialist_response",
        "config_supply_specialist": "normalize_configuration_supply_specialist_response",
        "schedule_risk_specialist": "normalize_schedule_risk_specialist_response",
    }
    assert error_callbacks == {
        "test_ops_specialist": "recover_test_operations_specialist_response",
        "maintenance_specialist": "recover_maintenance_specialist_response",
        "config_supply_specialist": "recover_configuration_supply_specialist_response",
        "schedule_risk_specialist": "recover_schedule_risk_specialist_response",
    }


@pytest.mark.asyncio
async def test_report_validator_accepts_all_canonicalized_specialist_reports() -> None:
    state = _captured_state()
    candidates = {
        "test_operations": {"findings": [{"statement": "not schema conforming"}]},
        "maintenance": {"findings": "wrong type"},
        "configuration_supply": {},
        "schedule_risk": {"aircraft_id": "AC-999", "findings": []},
    }
    for domain, candidate in candidates.items():
        spec = _SPECS[domain]
        report, diagnostics = canonicalize_specialist_report(spec, candidate, state)
        state[spec.output_key] = report.model_dump_json()
        state[f"temp:{spec.output_key}_normalization"] = diagnostics

    context = SimpleNamespace(session=SimpleNamespace(state=state))
    agent = ReportValidatorAgent(name="report_validator")
    events = [event async for event in agent._run_async_impl(context)]

    assert len(events) == 1
    assert sorted(state["mcp_evidence_ids"]) == [
        "CR-184",
        "DEF-009-042",
        "DEP-009-001",
        "DEP-009-002",
        "DEP-009-003",
        "DEP-009-004",
        "MNT-009-015",
        "PART-ACT-774",
        "TEST-009-118",
        "TEST-009-121",
    ]
    for spec in _SPECS.values():
        report = SpecialistReport.model_validate_json(state[spec.output_key])
        assert report.aircraft_id == "AC-009"
        assert report.findings


@pytest.mark.asyncio
async def test_report_validator_reconstructs_when_live_branch_has_evidence_but_no_output_key() -> (
    None
):
    """Live branches with successful MCP captures do not need a final JSON output."""
    state = _captured_state()
    for spec in _SPECS.values():
        state.pop(spec.output_key, None)

    context = SimpleNamespace(session=SimpleNamespace(state=state))
    agent = ReportValidatorAgent(name="report_validator")
    events = [event async for event in agent._run_async_impl(context)]

    assert len(events) == 1
    assert sorted(state["mcp_evidence_ids"]) == [
        "CR-184",
        "DEF-009-042",
        "DEP-009-001",
        "DEP-009-002",
        "DEP-009-003",
        "DEP-009-004",
        "MNT-009-015",
        "PART-ACT-774",
        "TEST-009-118",
        "TEST-009-121",
    ]
    for spec in _SPECS.values():
        report = SpecialistReport.model_validate_json(state[spec.output_key])
        assert report.aircraft_id == "AC-009"
        assert report.findings
        assert state[f"temp:{spec.output_key}_normalization"]["candidate_json_object"] is False


@pytest.mark.asyncio
async def test_specialist_model_error_callback_recovers_from_captured_evidence() -> None:
    """Provider errors after required tools can be repaired from canonical MCP evidence."""
    callback = make_specialist_model_error_fallback("schedule_risk")
    context = SimpleNamespace(state=_captured_state())

    recovered = await callback(
        context,
        SimpleNamespace(),
        RuntimeError("provider unavailable"),
    )

    assert recovered is not None
    report = SpecialistReport.model_validate_json(recovered.content.parts[0].text)
    assert report.domain == "schedule_risk"
    assert report.aircraft_id == "AC-009"
    assert report.findings
    assert context.state["temp:schedule_risk_findings_normalization"]["status"] == (
        "recovered_from_model_error"
    )
    cited = {ref.source_id for finding in report.findings for ref in finding.source_refs}
    assert {"DEP-009-001", "DEP-009-002", "DEP-009-003", "DEP-009-004"} <= cited


@pytest.mark.asyncio
async def test_specialist_model_error_callback_propagates_when_no_evidence() -> None:
    """Provider errors before tool evidence still propagate to ADK."""
    callback = make_specialist_model_error_fallback("schedule_risk")
    context = SimpleNamespace(state={"aircraft_id": "AC-009"})

    recovered = await callback(
        context,
        SimpleNamespace(),
        RuntimeError("provider unavailable"),
    )

    assert recovered is None
    assert context.state["temp:schedule_risk_findings_normalization"]["status"] == (
        "failed_no_evidence"
    )


def test_canonicalizer_builds_preflight_scoped_report_for_empty_successful_branch() -> None:
    """Green aircraft can have no domain records without failing report validation."""
    spec = _SPECS["configuration_supply"]
    state = {
        "aircraft_id": "AC-007",
        "investigation_scope": json.dumps(
            {
                "aircraft_id": "AC-007",
                "user_intent": "Explain AC-007 status",
                "requested_time_horizon": "90 days",
                "requested_output_type": "executive_brief",
            }
        ),
        "milestone_source_id": "MS-007-FTC",
        "planned_milestone_date": "2026-06-20",
        "forecast_milestone_date": "2026-06-20",
        "preflight_aircraft_record": {
            "source_id": "AC-007",
            "aircraft_id": "AC-007",
            "name": "AC-007 Systems Testbed",
            "status": "green",
            "synthetic_data": True,
        },
        "preflight_milestone_record": {
            "source_id": "MS-007-FTC",
            "aircraft_id": "AC-007",
            "title": "Flight Test Clearance",
            "status": "complete",
            "planned_date": "2026-06-20",
            "forecast_date": "2026-06-20",
            "synthetic_data": True,
        },
        "configuration_supply_mcp_evidence": [
            {
                "tool_name": "get_parts_constraints",
                "response": _envelope([], []),
                "args": {"aircraft_id": "AC-007"},
                "sequence": 1,
                "branch_sequence": 1,
                "invocation_id": "inv-empty",
                "function_call_id": "call-empty-1",
            },
            {
                "tool_name": "get_change_requests",
                "response": _envelope([], []),
                "args": {"aircraft_id": "AC-007"},
                "sequence": 2,
                "branch_sequence": 2,
                "invocation_id": "inv-empty",
                "function_call_id": "call-empty-2",
            },
        ],
    }

    report, diagnostics = canonicalize_specialist_report(spec, {}, state)

    assert diagnostics["captured_tool_calls"] == 2
    assert diagnostics["normalized_finding_count"] == 1
    assert diagnostics["fallback_finding_count"] == 1
    assert report.aircraft_id == "AC-007"
    assert report.domain == "configuration_supply"
    assert report.findings
    cited = {ref.source_id for finding in report.findings for ref in finding.source_refs}
    assert cited == {"AC-007"}
