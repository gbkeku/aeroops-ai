"""Integration tests for the AeroOps Streamlit UI controller.

Verifies the integration between the UI controller, the ADK investigation pipeline,
and the local stdio MCP server using a seeded temporary SQLite database and
scripted LLM doubles at the model boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aeroops.ui_models import DashboardInvestigationResult


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Path:
    """Seed a temporary SQLite database with AC-009 data."""
    tmp_dir = tmp_path_factory.mktemp("aeroops_ui_integration_db")
    db_path = tmp_dir / "aeroops_test_ui.db"

    from aeroops.db import get_db_connection
    from aeroops.db.schema import create_tables
    from aeroops.db.seed import seed_all

    with get_db_connection(db_path) as conn:
        create_tables(conn)
        seed_all(conn)
        conn.commit()

    return db_path


def test_ui_controller_live_integration(seeded_db, monkeypatch) -> None:
    """Verify live investigation path from UI controller through ADK runner and stdio MCP server."""
    # 1. Enforce live mode by removing AEROOPS_OFFLINE_DEMO from env
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)

    # 2. Inject model boundary doubles (ScriptedLlm) by patching aeroops.agent builders
    import aeroops.agent
    from tests.test_e2e_deterministic import ScriptedLlm  # noqa: F401

    orig_build_intake = aeroops.agent._build_intake_agent
    orig_build_specialists = aeroops.agent._build_specialist_agents
    orig_build_synthesis = aeroops.agent._build_synthesis_agent

    def mock_build_intake(model):
        return orig_build_intake("scripted:intake_extractor")

    def mock_build_specialists(model, db_path_override=None):
        agents = orig_build_specialists(model, db_path_override)
        for a in agents:
            a.model = f"scripted:{a.name}"
        return agents

    def mock_build_synthesis(model, **kwargs):
        agent = orig_build_synthesis(model, **kwargs)
        agent.model = f"scripted:{agent.name}"
        return agent

    monkeypatch.setattr(aeroops.agent, "_build_intake_agent", mock_build_intake)
    monkeypatch.setattr(aeroops.agent, "_build_specialist_agents", mock_build_specialists)
    monkeypatch.setattr(aeroops.agent, "_build_synthesis_agent", mock_build_synthesis)

    # 3. Call UI controller function
    from aeroops.ui_controller import run_dashboard_investigation

    result = run_dashboard_investigation(
        query="Why is AC-009 delayed? Produce an executive brief.",
        aircraft_id="AC-009",
        db_path_override=seeded_db,
    )

    # 4. Assertions on DashboardInvestigationResult
    assert isinstance(result, DashboardInvestigationResult)
    assert result.response.aircraft_id == "AC-009"
    assert result.response.delay_days == 6
    assert result.response.overall_status == "red"
    assert result.response.planned_milestone_date.isoformat() == "2026-06-29"
    assert result.response.forecast_milestone_date.isoformat() == "2026-07-05"

    # Exact evidence IDs present
    expected_evidence_set = {
        "MS-009-FTC",
        "TEST-009-118",
        "TEST-009-121",
        "DEF-009-042",
        "PART-ACT-774",
        "CR-184",
        "MNT-009-015",
        "DEP-009-001",
        "DEP-009-002",
        "DEP-009-003",
        "DEP-009-004",
    }
    assert set(result.response.evidence) == expected_evidence_set

    # Graph and timeline records generated
    assert len(result.dependency_nodes) > 0
    assert len(result.dependency_edges) > 0
    assert len(result.timeline_events) > 0

    # Ensure dependency edge details and specific blocker relationships appear
    dependency_ids = {edge.dependency_id for edge in result.dependency_edges}
    for dep_id in {"DEP-009-001", "DEP-009-002", "DEP-009-003", "DEP-009-004"}:
        assert dep_id in dependency_ids, f"Dependency edge '{dep_id}' not found in UI results"

    # Ensure four blockers appear in edges
    blocker_types = {edge.relationship for edge in result.dependency_edges}
    expected_blockers = {"defect", "parts_constraint", "change_request", "maintenance_task"}
    for blocker in expected_blockers:
        assert blocker in blocker_types, f"Blocker type '{blocker}' not found in dependency edges"

    # Safe activity records present
    assert len(result.activity) > 0
    for act in result.activity:
        assert act.duration_ms > 0
        assert act.agent_name != "unknown"
        # Tool name should be populated (or 'none' for synthesis)
        assert act.tool_name
