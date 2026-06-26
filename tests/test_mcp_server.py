"""Unit tests for the AeroOps MCP server tool functions."""

from __future__ import annotations

import json

import pytest

from aeroops.db import get_db_connection
from aeroops.db.schema import create_tables
from aeroops.db.seed import seed_all
from aeroops.mcp_server import (
    get_aircraft_status,
    get_change_requests,
    get_dependency_graph,
    get_fleet_summary,
    get_maintenance_tasks,
    get_milestones,
    get_open_defects,
    get_parts_constraints,
    get_test_events,
    health_check,
    list_aircraft,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Fixture to create and seed a temporary database, and set AEROOPS_DB_PATH."""
    db_file = tmp_path / "aeroops_unit_test.db"
    with get_db_connection(db_path=db_file) as conn:
        create_tables(conn)
        seed_all(conn)
    monkeypatch.setenv("AEROOPS_DB_PATH", str(db_file.resolve()))
    return db_file


def test_health_check_unit():
    """Test health_check tool function."""
    res = health_check()
    assert res.data.status == "ok"
    assert res.data.db_connected is True
    assert res.snapshot_date == "2026-06-24"
    assert res.synthetic_data is True
    assert res.source_refs == []


def test_list_aircraft_unit():
    """Test list_aircraft tool function, including filters and validation."""
    # List all
    res = list_aircraft()
    assert res.count == 4
    assert res.truncated is False
    assert "AC-009" in res.source_refs

    # Filter status = green
    res_green = list_aircraft(status="green")
    assert len(res_green.data) == 2
    assert all(ac.status == "green" for ac in res_green.data)

    # Invalid status filter -> should raise ValueError with VALIDATION_ERROR
    with pytest.raises(ValueError) as exc_info:
        list_aircraft(status="blue")
    err_data = json.loads(str(exc_info.value))
    assert err_data["error"]["category"] == "VALIDATION_ERROR"


def test_get_aircraft_status_unit():
    """Test get_aircraft_status tool function including errors."""
    # Success
    res = get_aircraft_status("AC-009")
    assert res.data.name == "AC-009 Avionics Testbed"
    assert res.data.status == "red"

    # Malformed ID
    with pytest.raises(ValueError) as exc_info:
        get_aircraft_status("AC-9999")
    err_data = json.loads(str(exc_info.value))
    assert err_data["error"]["category"] == "VALIDATION_ERROR"
    assert "Malformed Aircraft identifier" in err_data["error"]["message"]

    # Well-formed unknown
    with pytest.raises(ValueError) as exc_info:
        get_aircraft_status("AC-999")
    err_data = json.loads(str(exc_info.value))
    assert err_data["error"]["category"] == "NOT_FOUND"
    assert "Aircraft not found" in err_data["error"]["message"]


def test_get_open_defects_unit():
    """Test get_open_defects tool function."""
    # Success
    res = get_open_defects("AC-009")
    assert len(res.data) == 1
    assert res.data[0].source_id == "DEF-009-042"
    assert res.source_refs == ["DEF-009-042"]

    # Filter severity
    res_high = get_open_defects("AC-009", severity="high")
    assert len(res_high.data) == 1

    # Filter severity invalid
    with pytest.raises(ValueError) as exc_info:
        get_open_defects("AC-009", severity="unknown")
    err_data = json.loads(str(exc_info.value))
    assert err_data["error"]["category"] == "VALIDATION_ERROR"


def test_get_test_events_unit():
    """Test get_test_events tool function."""
    res = get_test_events("AC-009")
    # AC-009 has TEST-009-118 and TEST-009-121
    assert len(res.data) == 2
    assert any(te.source_id == "TEST-009-121" for te in res.data)

    res_blocked = get_test_events("AC-009", status="blocked")
    assert len(res_blocked.data) == 1
    assert res_blocked.data[0].source_id == "TEST-009-121"


def test_get_maintenance_tasks_unit():
    """Test get_maintenance_tasks tool function."""
    res = get_maintenance_tasks("AC-009")
    assert len(res.data) == 1
    assert res.data[0].source_id == "MNT-009-015"


def test_get_parts_constraints_unit():
    """Test get_parts_constraints tool function."""
    res = get_parts_constraints("AC-009")
    assert len(res.data) == 1
    assert res.data[0].source_id == "PART-ACT-774"


def test_get_change_requests_unit():
    """Test get_change_requests tool function."""
    res = get_change_requests("AC-009")
    assert len(res.data) == 1
    assert res.data[0].source_id == "CR-184"


def test_get_dependency_graph_unit():
    """Test get_dependency_graph tool function."""
    res = get_dependency_graph("AC-009")
    data = res.data
    assert data.aircraft_id == "AC-009"
    # Nodes: TEST-009-118, TEST-009-121, DEF-009-042, PART-ACT-774, CR-184, MNT-009-015
    assert len(data.nodes) == 6
    # Edges: TEST-009-121 blocked by 4 blockers
    assert len(data.edges) == 4

    # Verify source_refs includes both node source IDs and DEP-009-001... IDs
    assert "AC-009" in res.source_refs
    assert "DEP-009-001" in res.source_refs
    assert "DEP-009-002" in res.source_refs
    assert "DEP-009-003" in res.source_refs
    assert "DEP-009-004" in res.source_refs
    assert "DEF-009-042" in res.source_refs
    assert "TEST-009-121" in res.source_refs


def test_get_fleet_summary_unit():
    """Test get_fleet_summary tool function."""
    res = get_fleet_summary()
    assert res.data.total_aircraft == 4
    assert res.data.status_counts["red"] == 1
    assert res.data.total_open_defects == 1
    assert res.data.total_blocked_tests == 1
    assert res.data.total_high_critical_defects == 1
    assert res.data.total_blocked_delayed_tests == 2
    assert res.data.total_upcoming_milestones == 3
    assert "AC-009" in res.source_refs
    assert "DEF-009-042" in res.source_refs


def test_get_milestones_unit():
    """Test get_milestones tool function."""
    res = get_milestones("AC-009")
    assert len(res.data) == 1
    assert res.data[0].source_id == "MS-009-FTC"
    assert res.data[0].planned_date.isoformat() == "2026-06-29"
    assert res.data[0].forecast_date.isoformat() == "2026-07-05"
