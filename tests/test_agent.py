"""Tests for the multi-agent AeroOps investigation workflow.

This module covers:
1. Architecture/hierarchy checks (no live LLM calls)
2. Tool allowlist enforcement (no live LLM calls)
3. Domain model validation (Pydantic, deterministic)
4. Schedule variance determinism (date arithmetic only)
5. Scope validation and intake parsing
6. Service-layer milestone resolution
7. End-to-end (gated by AEROOPS_RUN_E2E_TESTS)

No test in this file calls the Gemini API or imports the SQLite repository
from within agent or pipeline code.  The repository is accessed only at the
service layer, and only in the milestone-resolution tests which exercise
the service layer directly.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from aeroops.agent import (
    _CONFIG_SUPPLY_TOOLS,
    _MAINTENANCE_TOOLS,
    _SCHEDULE_RISK_TOOLS,
    _TEST_OPS_TOOLS,
    create_pipeline,
    get_specialist_output_keys,
    get_tool_allowlist,
)
from aeroops.models import (
    AgentActivity,
    EvidenceRef,
    ExecutiveBrief,
    Finding,
    InvestigationScope,
    InvestigationTrace,
    RecommendedAction,
    SpecialistReport,
)
from aeroops.scope_validator import (
    ScopeValidationError,
    classify_aircraft_id,
    parse_intake_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REAL_DB = Path(__file__).parent.parent / "data" / "aeroops.db"
_DB_AVAILABLE = _REAL_DB.exists()


@pytest.fixture()
def db_path() -> Path | None:
    """Return path to the synthetic database if available."""
    return _REAL_DB if _DB_AVAILABLE else None


# ---------------------------------------------------------------------------
# 1. Architecture / hierarchy checks
# ---------------------------------------------------------------------------


class TestArchitecture:
    """Validate the pipeline hierarchy without calling any LLM."""

    def test_pipeline_type(self) -> None:
        """Pipeline root must be a SequentialAgent."""
        from google.adk.agents import SequentialAgent

        pipeline = create_pipeline()
        assert isinstance(pipeline, SequentialAgent), (
            f"Expected SequentialAgent, got {type(pipeline).__name__}"
        )

    def test_pipeline_name(self) -> None:
        """Pipeline must have the canonical name."""
        pipeline = create_pipeline()
        assert pipeline.name == "aeroops_investigation_pipeline"

    def test_pipeline_has_five_stages(self) -> None:
        """Pipeline must have exactly five stages."""
        pipeline = create_pipeline()
        assert len(pipeline.sub_agents) == 5, (
            f"Expected 5 pipeline stages, got {len(pipeline.sub_agents)}"
        )

    def test_stage_names(self) -> None:
        """Pipeline stages must appear in canonical order."""
        pipeline = create_pipeline()
        names = [a.name for a in pipeline.sub_agents]
        assert names == [
            "intake_extractor",
            "scope_validator",
            "parallel_specialist_investigation",
            "report_validator",
            "executive_synthesis",
        ], f"Unexpected stage order: {names}"

    def test_parallel_stage_has_four_specialists(self) -> None:
        """ParallelAgent must contain exactly four specialist agents."""
        from google.adk.agents import ParallelAgent

        pipeline = create_pipeline()
        parallel = pipeline.sub_agents[2]  # index 2 after scope_validator
        assert isinstance(parallel, ParallelAgent)
        assert len(parallel.sub_agents) == 4, (
            f"Expected 4 specialists, got {len(parallel.sub_agents)}"
        )

    def test_specialist_names(self) -> None:
        """Specialist agents must carry the correct canonical names."""
        pipeline = create_pipeline()
        parallel = pipeline.sub_agents[2]
        names = {a.name for a in parallel.sub_agents}
        expected = {
            "test_ops_specialist",
            "maintenance_specialist",
            "config_supply_specialist",
            "schedule_risk_specialist",
        }
        assert names == expected, f"Unexpected specialist names: {names}"

    def test_specialist_output_keys(self) -> None:
        """Each specialist must write to its exact distinct state key."""
        pipeline = create_pipeline()
        parallel = pipeline.sub_agents[2]
        output_keys = {a.output_key for a in parallel.sub_agents}
        expected = {
            "test_ops_findings",
            "maintenance_findings",
            "configuration_supply_findings",
            "schedule_risk_findings",
        }
        assert output_keys == expected, f"Unexpected output_keys: {output_keys}"

    def test_intake_output_key(self) -> None:
        """Intake agent must write to 'intake_output'."""
        pipeline = create_pipeline()
        intake = pipeline.sub_agents[0]
        assert intake.output_key == "intake_output"

    def test_scope_validator_is_second_stage(self) -> None:
        """ScopeValidatorAgent must be stage 1 (index 1) in the pipeline."""
        from aeroops.scope_validator import ScopeValidatorAgent

        pipeline = create_pipeline()
        assert isinstance(pipeline.sub_agents[1], ScopeValidatorAgent)
        assert pipeline.sub_agents[1].name == "scope_validator"

    def test_report_validator_is_fourth_stage(self) -> None:
        """ReportValidatorAgent must be stage 3 (index 3) in the pipeline."""
        from aeroops.report_validator import ReportValidatorAgent

        pipeline = create_pipeline()
        assert isinstance(pipeline.sub_agents[3], ReportValidatorAgent)
        assert pipeline.sub_agents[3].name == "report_validator"

    def test_synthesis_output_key(self) -> None:
        """Synthesis agent must write to 'synthesis_output'."""
        pipeline = create_pipeline()
        synthesis = pipeline.sub_agents[4]
        assert synthesis.output_key == "synthesis_output"

    def test_synthesis_has_no_tools(self) -> None:
        """Synthesis agent must have no MCP tools (empty tools list)."""
        pipeline = create_pipeline()
        synthesis = pipeline.sub_agents[4]
        tools = list(getattr(synthesis, "tools", []))
        assert tools == [], f"Synthesis agent must have no tools, got: {tools}"

    def test_intake_has_no_tools(self) -> None:
        """Intake agent must have no MCP tools."""
        pipeline = create_pipeline()
        intake = pipeline.sub_agents[0]
        tools = list(getattr(intake, "tools", []))
        assert tools == [], f"Intake agent must have no tools, got: {tools}"

    def test_synthesis_include_contents(self) -> None:
        """Synthesis agent must have include_contents='none'."""
        pipeline = create_pipeline()
        synthesis = pipeline.sub_agents[4]
        assert getattr(synthesis, "include_contents", None) == "none", (
            "Synthesis agent must set include_contents='none'"
        )

    def test_synthesis_uses_deterministic_normalizer(self) -> None:
        """Provider-side schema validation is disabled in favor of deterministic normalization."""
        from aeroops.synthesis import normalize_executive_synthesis_response

        pipeline = create_pipeline()
        synthesis = pipeline.sub_agents[4]
        assert getattr(synthesis, "output_schema", None) is None
        assert synthesis.after_model_callback is normalize_executive_synthesis_response
        assert "compact executive draft" in synthesis.instruction
        assert '"confirmed_root_causes"' not in synthesis.instruction

    def test_test_ops_instruction_uses_only_supported_test_statuses(self) -> None:
        """The live model must not be instructed to call an unsupported `failed` filter."""
        pipeline = create_pipeline()
        parallel = pipeline.sub_agents[2]
        test_ops = next(
            agent for agent in parallel.sub_agents if agent.name == "test_ops_specialist"
        )
        assert 'status="failed"' not in test_ops.instruction
        assert "without a status filter" in test_ops.instruction
        assert 'stored status is "blocked"' in test_ops.instruction

    def test_no_repository_import_in_agent_module(self) -> None:
        """agent.py must not import the repository module."""

        # Reload agent module and inspect its namespace
        import aeroops.agent as agent_mod

        # The repository should NOT be in agent module's globals
        assert "repository" not in vars(agent_mod), (
            "agent.py must not import repository — DB access belongs in services.py"
        )

    def test_specialist_output_key_api(self) -> None:
        """get_specialist_output_keys() returns all four expected mappings."""
        keys = get_specialist_output_keys()
        assert keys["test_ops_specialist"] == "test_ops_findings"
        assert keys["maintenance_specialist"] == "maintenance_findings"
        assert keys["config_supply_specialist"] == "configuration_supply_findings"
        assert keys["schedule_risk_specialist"] == "schedule_risk_findings"


# ---------------------------------------------------------------------------
# 2. Tool allowlist enforcement
# ---------------------------------------------------------------------------


class TestToolAllowlists:
    """Validate that specialist tool allowlists are correct and immutable."""

    def test_test_ops_allowlist(self) -> None:
        result = get_tool_allowlist("test_ops")
        assert result == _TEST_OPS_TOOLS
        assert isinstance(result, frozenset)

    def test_maintenance_allowlist(self) -> None:
        result = get_tool_allowlist("maintenance")
        assert result == _MAINTENANCE_TOOLS
        assert isinstance(result, frozenset)

    def test_config_supply_allowlist(self) -> None:
        result = get_tool_allowlist("config_supply")
        assert result == _CONFIG_SUPPLY_TOOLS
        assert isinstance(result, frozenset)

    def test_schedule_risk_allowlist(self) -> None:
        result = get_tool_allowlist("schedule_risk")
        assert result == _SCHEDULE_RISK_TOOLS
        assert isinstance(result, frozenset)

    def test_unknown_domain_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown domain"):
            get_tool_allowlist("nonexistent_domain")

    def test_test_ops_required_tools_present(self) -> None:
        allowed = get_tool_allowlist("test_ops")
        for tool in ("get_aircraft_status", "get_test_events", "get_open_defects"):
            assert tool in allowed, f"'{tool}' must be in test_ops allowlist"

    def test_maintenance_does_not_have_schedule_tools(self) -> None:
        """Maintenance specialist must not have dependency_graph access."""
        allowed = get_tool_allowlist("maintenance")
        assert "get_dependency_graph" not in allowed

    def test_synthesis_not_a_domain(self) -> None:
        """There is no synthesis domain — synthesis has no tools."""
        with pytest.raises(KeyError):
            get_tool_allowlist("synthesis")

    def test_allowlists_are_disjoint_pairs(self) -> None:
        """Config/supply and test_ops should not share tools (different domains)."""
        test_ops = get_tool_allowlist("test_ops")
        config = get_tool_allowlist("config_supply")
        overlap = test_ops & config
        assert not overlap, f"test_ops and config_supply share unexpected tools: {overlap}"


# ---------------------------------------------------------------------------
# 3. Domain model validation
# ---------------------------------------------------------------------------


class TestDomainModels:
    """Validate expanded Pydantic domain models without LLM calls."""

    def test_evidence_ref_creation(self) -> None:
        ref = EvidenceRef(
            source_id="TEST-009-118",
            record_type="test_event",
            summary="Functional test aborted due to hydraulic actuator defect.",
        )
        assert ref.source_id == "TEST-009-118"

    def test_finding_creation(self) -> None:
        ref = EvidenceRef(
            source_id="DEF-009-042",
            record_type="defect",
            summary="Critical hydraulic defect blocking FTC.",
        )
        f = Finding(
            finding_id="FIND-TEST-001",
            statement="Hydraulic actuator defect DEF-009-042 is blocking test TEST-009-118.",
            classification="defect",
            source_refs=[ref],
            rationale="The defect is linked via dependency DEP-009-001.",
        )
        assert f.classification == "defect"
        assert len(f.source_refs) == 1

    def test_recommended_action_creation(self) -> None:
        ref = EvidenceRef(source_id="MNT-009-015", record_type="maintenance_task", summary="x")
        ra = RecommendedAction(
            action_id="ACT-001",
            action="Complete hydraulic actuator maintenance MNT-009-015 before next test.",
            classification="maintenance",
            supporting_finding_ids=["FIND-TEST-001"],
            source_refs=[ref],
            rationale="Task is on the critical path for FTC.",
            owner_role="maintenance_lead",
            suggested_due_date="2026-07-01",
        )
        assert ra.owner_role == "maintenance_lead"
        assert ra.suggested_due_date == "2026-07-01"

    def test_investigation_scope_pattern_validation(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationScope(
                aircraft_id="INVALID",
                user_intent="test",
                requested_time_horizon="30 days",
                requested_output_type="executive_brief",
            )

    def test_investigation_scope_valid(self) -> None:
        scope = InvestigationScope(
            aircraft_id="AC-009",
            user_intent="Why is AC-009 delayed?",
            requested_time_horizon="90 days",
            requested_output_type="executive_brief",
        )
        assert scope.aircraft_id == "AC-009"

    def test_specialist_report_creation(self) -> None:
        rpt = SpecialistReport(
            domain="test_operations",
            aircraft_id="AC-009",
            findings=[],
            raw_source_ids=["TEST-009-118"],
        )
        assert rpt.domain == "test_operations"

    def test_agent_activity_creation(self) -> None:
        act = AgentActivity(
            agent_name="test_ops_specialist",
            action="Called get_test_events for AC-009",
            state_key="test_ops_findings",
            status="ok",
        )
        assert act.status == "ok"

    def test_investigation_trace(self) -> None:
        trace = InvestigationTrace(
            run_id="abc-123",
            aircraft_id="AC-009",
            activities=[
                AgentActivity(agent_name="intake_extractor", action="parsed scope", status="ok")
            ],
        )
        assert len(trace.activities) == 1


# ---------------------------------------------------------------------------
# 4. Schedule variance — ExecutiveBrief determinism
# ---------------------------------------------------------------------------


class TestScheduleVariance:
    """Validate that delay_days is computed deterministically and validated."""

    def _make_brief(self, planned: str, forecast: str, delay_days: int) -> ExecutiveBrief:
        """Helper to build a minimal ExecutiveBrief with given dates."""
        ref = EvidenceRef(source_id="MS-009-FTC", record_type="milestone", summary="FTC milestone")
        finding = Finding(
            finding_id="FIND-SCHEDULE-001",
            statement="Test finding",
            classification="schedule_risk",
            source_refs=[ref],
            rationale="Test rationale",
        )
        action = RecommendedAction(
            action_id="ACT-001",
            action="Take action",
            classification="schedule_risk",
            supporting_finding_ids=["FIND-SCHEDULE-001"],
            source_refs=[ref],
            rationale="Test rationale",
            owner_role="program_management",
            suggested_due_date="2026-07-01",
        )
        return ExecutiveBrief(
            aircraft_id="AC-009",
            overall_status="red",
            planned_milestone_date=date.fromisoformat(planned),
            forecast_milestone_date=date.fromisoformat(forecast),
            delay_days=delay_days,
            executive_summary="AC-009 is delayed by 6 days due to hydraulic defect.",
            confirmed_root_causes=[finding],
            contributing_factors=[],
            recommended_actions=[action],
            assumptions=["Parts will arrive on schedule"],
            unknowns=["Root cause of actuator failure"],
            confidence="high",
            milestone_source_id="MS-009-FTC",
        )

    def test_correct_delay_days_accepted(self) -> None:
        """Exactly 6 days between 2026-07-01 and 2026-07-07."""
        brief = self._make_brief("2026-07-01", "2026-07-07", 6)
        assert brief.delay_days == 6

    def test_zero_delay_accepted(self) -> None:
        brief = self._make_brief("2026-07-01", "2026-07-01", 0)
        assert brief.delay_days == 0

    def test_negative_delay_accepted(self) -> None:
        """Forecast before planned = negative delay (ahead of schedule)."""
        brief = self._make_brief("2026-07-10", "2026-07-01", -9)
        assert brief.delay_days == -9

    def test_wrong_delay_days_rejected(self) -> None:
        """Providing delay_days=5 for a 6-day gap must raise ValueError."""
        with pytest.raises(Exception, match="delay_days mismatch"):
            self._make_brief("2026-07-01", "2026-07-07", 5)

    def test_wrong_delay_days_off_by_one(self) -> None:
        with pytest.raises(Exception, match="delay_days mismatch"):
            self._make_brief("2026-07-01", "2026-07-07", 7)

    def test_ac009_deterministic_delay_is_six_days(self) -> None:
        """AC-009 dates: 2026-06-29 → 2026-07-05 = 6 days exactly."""
        planned = date.fromisoformat("2026-06-29")
        forecast = date.fromisoformat("2026-07-05")
        delay = (forecast - planned).days
        assert delay == 6, f"Expected 6, got {delay}"


# ---------------------------------------------------------------------------
# 5. Scope validation and intake parsing (replaces _parse_intake_output tests)
# ---------------------------------------------------------------------------


class TestScopeValidation:
    """Test deterministic scope validator — no DB or LLM calls."""

    def test_valid_intake_json_parses_scope(self) -> None:
        raw = json.dumps(
            {
                "aircraft_id": "AC-009",
                "user_intent": "investigate delays",
                "requested_time_horizon": "90 days",
                "requested_output_type": "executive_brief",
            }
        )
        data = parse_intake_output(raw)
        aircraft_id = classify_aircraft_id(data, "Why is AC-009 delayed?")
        assert aircraft_id == "AC-009"

    def test_intake_with_markdown_fences(self) -> None:
        raw = '```json\n{"aircraft_id":"AC-009","user_intent":"test","requested_time_horizon":"30 days","requested_output_type":"executive_brief"}\n```'
        data = parse_intake_output(raw)
        assert data["aircraft_id"] == "AC-009"

    def test_intake_error_response_raises_scope_error(self) -> None:
        raw = json.dumps({"error": "invalid_aircraft_id", "detail": "AC-999 not found"})
        data = parse_intake_output(raw)
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "AC-999")
        assert exc_info.value.error_code == "MALFORMED_AIRCRAFT_ID"

    def test_intake_non_json_raises_scope_error(self) -> None:
        with pytest.raises(ScopeValidationError, match="MISSING_AIRCRAFT_ID"):
            parse_intake_output("Sorry, I could not parse your request.")

    def test_missing_aircraft_id_raises_scope_error(self) -> None:
        raw = json.dumps({"aircraft_id": "", "user_intent": "test"})
        data = parse_intake_output(raw)
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "some query")
        assert exc_info.value.error_code == "MISSING_AIRCRAFT_ID"

    def test_malformed_aircraft_id_raises_scope_error(self) -> None:
        raw = json.dumps({"aircraft_id": "AC009", "user_intent": "test"})
        data = parse_intake_output(raw)
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Investigate AC009")
        assert exc_info.value.error_code == "MALFORMED_AIRCRAFT_ID"

    def test_ambiguous_two_ids_raises_scope_error(self) -> None:
        raw = json.dumps({"aircraft_id": "AC-009", "user_intent": "compare"})
        data = parse_intake_output(raw)
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Compare AC-009 and AC-010")
        assert exc_info.value.error_code == "AMBIGUOUS_AIRCRAFT_ID"

    def test_defaults_time_horizon(self) -> None:
        """Missing time_horizon should fall back to '90 days' in InvestigationScope."""
        raw = json.dumps(
            {
                "aircraft_id": "AC-009",
                "user_intent": "test",
                "requested_output_type": "executive_brief",
            }
        )
        data = parse_intake_output(raw)
        scope = InvestigationScope(
            aircraft_id=data["aircraft_id"],
            user_intent=data.get("user_intent", "investigate"),
            requested_time_horizon=data.get("requested_time_horizon", "90 days"),
            requested_output_type=data.get("requested_output_type", "executive_brief"),
        )
        assert scope.requested_time_horizon == "90 days"


# ---------------------------------------------------------------------------
# 6. Service-layer milestone resolution (DB integration at service boundary)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DB_AVAILABLE, reason="Database not seeded")
class TestMilestoneContext:
    """Test deterministic milestone resolution at the service layer using MCP."""

    @pytest.mark.asyncio
    async def test_resolve_milestone_ac009(self, db_path: Path) -> None:
        from aeroops.services import _resolve_milestone_via_mcp

        ctx = await _resolve_milestone_via_mcp(
            "AC-009", "MS-009-FTC", db_path_override=str(db_path)
        )
        assert "planned_milestone_date" in ctx
        assert "forecast_milestone_date" in ctx
        assert "delay_days" in ctx
        assert "milestone_source_id" in ctx
        # Verify arithmetic consistency
        planned = date.fromisoformat(ctx["planned_milestone_date"])
        forecast = date.fromisoformat(ctx["forecast_milestone_date"])
        assert ctx["delay_days"] == (forecast - planned).days

    @pytest.mark.asyncio
    async def test_ac009_delay_is_six_days(self, db_path: Path) -> None:
        from aeroops.services import _resolve_milestone_via_mcp

        ctx = await _resolve_milestone_via_mcp(
            "AC-009", "MS-009-FTC", db_path_override=str(db_path)
        )
        assert ctx["delay_days"] == 6, (
            f"Expected 6 days delay for AC-009, got {ctx['delay_days']}. "
            f"planned={ctx['planned_milestone_date']}, "
            f"forecast={ctx['forecast_milestone_date']}"
        )

    @pytest.mark.asyncio
    async def test_ac009_milestone_source_id(self, db_path: Path) -> None:
        from aeroops.services import _resolve_milestone_via_mcp

        ctx = await _resolve_milestone_via_mcp(
            "AC-009", "MS-009-FTC", db_path_override=str(db_path)
        )
        assert ctx["milestone_source_id"] == "MS-009-FTC", (
            f"Expected milestone_source_id='MS-009-FTC', got '{ctx['milestone_source_id']}'"
        )

    @pytest.mark.asyncio
    async def test_nonexistent_aircraft_raises(self, db_path: Path) -> None:
        from aeroops.services import _resolve_milestone_via_mcp

        with pytest.raises(ValueError, match="Aircraft not found"):
            await _resolve_milestone_via_mcp("AC-999", "MS-009-FTC", db_path_override=str(db_path))


# ---------------------------------------------------------------------------
# 7. End-to-end (gated by env var to avoid quota exhaustion)
# ---------------------------------------------------------------------------

_RUN_E2E = os.environ.get("AEROOPS_RUN_E2E_TESTS", "").lower() in ("1", "true", "yes")


@pytest.mark.skipif(not _RUN_E2E, reason="Set AEROOPS_RUN_E2E_TESTS=1 to run live LLM tests")
@pytest.mark.skipif(not _DB_AVAILABLE, reason="Database not seeded")
class TestEndToEnd:
    """Live integration tests that call real LLM endpoints."""

    @pytest.mark.asyncio()
    async def test_ac009_investigation_returns_executive_brief(self, db_path: Path) -> None:
        from aeroops.services import run_investigation_async

        brief = await run_investigation_async(
            query="Why is aircraft AC-009 delayed? What is blocking its next test "
            "and what actions should leadership take?",
            db_path=db_path,
        )
        assert isinstance(brief, ExecutiveBrief)
        assert brief.aircraft_id == "AC-009"

    @pytest.mark.asyncio()
    async def test_ac009_delay_days_is_six(self, db_path: Path) -> None:
        from aeroops.services import run_investigation_async

        brief = await run_investigation_async(
            query="Investigate AC-009 schedule delay.",
            db_path=db_path,
        )
        assert brief.delay_days == 6, f"Expected delay_days=6 for AC-009, got {brief.delay_days}"

    @pytest.mark.asyncio()
    async def test_invalid_aircraft_raises_value_error(self, db_path: Path) -> None:
        from aeroops.services import run_investigation_async

        with pytest.raises((ValueError, RuntimeError)):
            await run_investigation_async(
                query="Investigate aircraft AC-999 delays.",
                db_path=db_path,
            )

    @pytest.mark.asyncio()
    async def test_brief_has_source_refs(self, db_path: Path) -> None:
        from aeroops.services import run_investigation_async

        brief = await run_investigation_async(
            query="Full investigation for AC-009.",
            db_path=db_path,
        )
        all_refs: list[str] = []
        for f in brief.confirmed_root_causes + brief.contributing_factors:
            all_refs.extend(r.source_id for r in f.source_refs)
        for a in brief.recommended_actions:
            all_refs.extend(r.source_id for r in a.source_refs)
        assert all_refs, "ExecutiveBrief must contain at least one source reference"
