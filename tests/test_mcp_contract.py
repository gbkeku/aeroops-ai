"""Contract tests for the AeroOps MCP server, running over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aeroops.db import get_db_connection
from aeroops.db.schema import create_tables
from aeroops.db.seed import seed_all


@pytest.fixture
def setup_test_db(tmp_path):
    """Fixture to create and seed a temporary database for the contract tests."""
    db_file = tmp_path / "aeroops_contract_test.db"
    with get_db_connection(db_path=db_file) as conn:
        create_tables(conn)
        seed_all(conn)
    return db_file


@asynccontextmanager
async def connect_to_mcp(db_path: Path):
    """Context manager to start the MCP server subprocess and connect to it over stdio."""
    env = os.environ.copy()
    env["AEROOPS_DB_PATH"] = str(db_path.resolve())

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aeroops.mcp_server"],
        env=env,
    )

    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout=10.0)
        yield session


async def test_tool_registration(setup_test_db):
    """Verify tool registration constraints."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(mcp_session.list_tools(), timeout=5.0)
        tools = result.tools
        tool_names = [t.name for t in tools]

        expected_tools = {
            "health_check",
            "list_aircraft",
            "get_aircraft_status",
            "get_open_defects",
            "get_test_events",
            "get_maintenance_tasks",
            "get_parts_constraints",
            "get_change_requests",
            "get_dependency_graph",
            "get_fleet_summary",
            "get_milestones",
        }

        assert set(tool_names) == expected_tools
        assert len(tool_names) == 11

        # Ensure no generic SQL or mutation tools
        for name in tool_names:
            assert "sql" not in name.lower()
            assert "create" not in name.lower()
            assert "update" not in name.lower()
            assert "delete" not in name.lower()
            assert "remove" not in name.lower()

        # Ensure valid input schemas
        for t in tools:
            assert t.inputSchema is not None
            assert t.inputSchema.get("type") == "object"


async def test_health_check_contract(setup_test_db):
    """Test health_check via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(mcp_session.call_tool("health_check"), timeout=5.0)
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert res_data["data"]["status"] == "ok"
        assert res_data["data"]["db_connected"] is True
        assert res_data["snapshot_date"] == "2026-06-24"
        assert res_data["synthetic_data"] is True
        assert res_data["source_refs"] == []


async def test_list_aircraft_contract(setup_test_db):
    """Test list_aircraft via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(mcp_session.call_tool("list_aircraft"), timeout=5.0)
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert len(res_data["data"]) == 4
        assert res_data["count"] == 4
        assert res_data["truncated"] is False
        assert "AC-009" in res_data["source_refs"]


async def test_get_aircraft_status_contract(setup_test_db):
    """Test get_aircraft_status via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_aircraft_status", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert res_data["data"]["name"] == "AC-009 Avionics Testbed"
        assert res_data["data"]["status"] == "red"
        assert res_data["source_refs"] == ["AC-009"]


async def test_get_open_defects_contract(setup_test_db):
    """Test get_open_defects via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_open_defects", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert len(res_data["data"]) == 1
        assert res_data["data"][0]["source_id"] == "DEF-009-042"
        assert res_data["source_refs"] == ["DEF-009-042"]


async def test_get_test_events_contract(setup_test_db):
    """Test get_test_events via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_test_events", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)
        assert len(res_data["data"]) == 2


async def test_get_maintenance_tasks_contract(setup_test_db):
    """Test get_maintenance_tasks via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_maintenance_tasks", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)
        assert len(res_data["data"]) == 1


async def test_get_parts_constraints_contract(setup_test_db):
    """Test get_parts_constraints via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_parts_constraints", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)
        assert len(res_data["data"]) == 1


async def test_get_change_requests_contract(setup_test_db):
    """Test get_change_requests via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_change_requests", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)
        assert len(res_data["data"]) == 1


async def test_get_dependency_graph_contract(setup_test_db):
    """Test get_dependency_graph via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_dependency_graph", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        graph = res_data["data"]
        assert graph["aircraft_id"] == "AC-009"
        # Verify exactly 4 blockers on rotation test event + 2 test events
        assert len(graph["nodes"]) == 6
        assert len(graph["edges"]) == 4

        # Node source IDs and schedule-dependency source IDs must be present in source_refs
        assert "AC-009" in res_data["source_refs"]
        assert "DEP-009-001" in res_data["source_refs"]
        assert "DEP-009-002" in res_data["source_refs"]
        assert "DEF-009-042" in res_data["source_refs"]
        assert "TEST-009-121" in res_data["source_refs"]


async def test_get_fleet_summary_contract(setup_test_db):
    """Test get_fleet_summary via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(mcp_session.call_tool("get_fleet_summary"), timeout=5.0)
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert res_data["data"]["total_aircraft"] == 4
        assert res_data["data"]["total_high_critical_defects"] == 1
        assert res_data["data"]["total_blocked_delayed_tests"] == 2
        assert res_data["data"]["total_upcoming_milestones"] == 3
        assert "AC-009" in res_data["source_refs"]


async def test_get_milestones_contract(setup_test_db):
    """Test get_milestones via the MCP protocol."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        result = await asyncio.wait_for(
            mcp_session.call_tool("get_milestones", {"aircraft_id": "AC-009"}), timeout=5.0
        )
        assert result.isError is False
        res_data = json.loads(result.content[0].text)

        assert len(res_data["data"]) > 0
        assert "MS-009-FTC" in res_data["source_refs"]


def parse_mcp_error(result) -> dict:
    """Helper to extract JSON payload from a FastMCP tool execution error message."""
    text = result.content[0].text
    idx = text.find("{")
    if idx == -1:
        raise ValueError(f"Could not find JSON in error response: {text}")
    return json.loads(text[idx:])


async def test_validation_errors_contract(setup_test_db):
    """Verify input validation errors return isError=True and proper category codes."""
    async with connect_to_mcp(setup_test_db) as mcp_session:
        # 1. Invalid aircraft ID format
        result_malformed = await asyncio.wait_for(
            mcp_session.call_tool("get_aircraft_status", {"aircraft_id": "AC-09"}), timeout=5.0
        )
        assert result_malformed.isError is True
        err_data = parse_mcp_error(result_malformed)
        assert err_data["error"]["category"] == "VALIDATION_ERROR"
        assert "Malformed Aircraft identifier" in err_data["error"]["message"]

        # 2. Well-formed unknown aircraft ID
        result_unknown = await asyncio.wait_for(
            mcp_session.call_tool("get_aircraft_status", {"aircraft_id": "AC-999"}), timeout=5.0
        )
        assert result_unknown.isError is True
        err_data = parse_mcp_error(result_unknown)
        assert err_data["error"]["category"] == "NOT_FOUND"
        assert "Aircraft not found" in err_data["error"]["message"]

        # 3. Invalid aircraft status filter
        result_status = await asyncio.wait_for(
            mcp_session.call_tool("list_aircraft", {"status": "blue"}), timeout=5.0
        )
        assert result_status.isError is True
        err_data = parse_mcp_error(result_status)
        assert err_data["error"]["category"] == "VALIDATION_ERROR"

        # 4. Invalid defect severity filter
        result_severity = await asyncio.wait_for(
            mcp_session.call_tool(
                "get_open_defects", {"aircraft_id": "AC-009", "severity": "super"}
            ),
            timeout=5.0,
        )
        assert result_severity.isError is True
        err_data = parse_mcp_error(result_severity)
        assert err_data["error"]["category"] == "VALIDATION_ERROR"
