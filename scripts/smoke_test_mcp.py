#!/usr/bin/env python3
"""Automated MCP smoke-test client for aeroops-data-mcp.

This script launches the MCP server over stdio, initializes a session,
lists the registered tools, calls health_check and get_dependency_graph,
and validates the structured results.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Define project paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "aeroops.db"
EXPECTED_TOOLS = {
    "health_check",
    "list_aircraft",
    "get_aircraft_status",
    "get_milestones",
    "get_open_defects",
    "get_test_events",
    "get_maintenance_tasks",
    "get_parts_constraints",
    "get_change_requests",
    "get_dependency_graph",
    "get_fleet_summary",
}


async def run_smoke_test():
    print("==================================================")
    print("AeroOps MCP Server Smoke-Test Client")
    print("==================================================")

    if not DB_PATH.is_file():
        print(f"ERROR: Database file not found at: {DB_PATH}")
        sys.exit(1)

    print(f"Using database: {DB_PATH}")

    env = os.environ.copy()
    env["AEROOPS_DB_PATH"] = str(DB_PATH.resolve())

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aeroops.mcp_server"],
        env=env,
    )

    print("Launching MCP server subprocess...")
    try:
        async with (
            stdio_client(server_params) as (read, write),
            ClientSession(read, write) as session,
        ):
            print("Initializing MCP protocol session...")
            await asyncio.wait_for(session.initialize(), timeout=5.0)

            print("\n1. Listing registered tools:")
            tools_res = await asyncio.wait_for(session.list_tools(), timeout=5.0)
            tools = tools_res.tools
            tool_names = {tool.name for tool in tools}
            print(f"Registered tools ({len(tools)}): {sorted(tool_names)}")
            assert tool_names == EXPECTED_TOOLS
            assert not any(
                token in name.lower()
                for name in tool_names
                for token in ("sql", "create", "update", "delete", "approve", "close")
            )

            print("\n2. Executing 'health_check':")
            hc_res = await asyncio.wait_for(session.call_tool("health_check"), timeout=5.0)
            if hc_res.isError:
                print("ERROR: health_check failed.")
                sys.exit(1)
            hc_data = json.loads(hc_res.content[0].text)
            print(f"Response: {hc_data}")
            assert hc_data["data"]["status"] == "ok"
            assert hc_data["data"]["db_connected"] is True

            print("\n3. Executing 'get_dependency_graph' for AC-009:")
            dg_res = await asyncio.wait_for(
                session.call_tool("get_dependency_graph", {"aircraft_id": "AC-009"}),
                timeout=5.0,
            )
            if dg_res.isError:
                print("ERROR: get_dependency_graph failed.")
                sys.exit(1)
            dg_data = json.loads(dg_res.content[0].text)
            print(f"Aircraft ID: {dg_data['data']['aircraft_id']}")
            print(f"Number of nodes: {len(dg_data['data']['nodes'])}")
            print(f"Number of edges: {len(dg_data['data']['edges'])}")
            print(f"Source refs: {dg_data['source_refs']}")

            # Validate the 4 blockers
            nodes = dg_data["data"]["nodes"]
            blockers = [n for n in nodes if n["type"] != "test_event"]
            blocker_types = [b["type"] for b in blockers]
            blocker_ids = [b["id"] for b in blockers]
            print(f"Blocker types found: {blocker_types}")
            print(f"Blocker IDs found: {blocker_ids}")

            assert len(blockers) == 4
            assert "defect" in blocker_types
            assert "parts_constraint" in blocker_types
            assert "change_request" in blocker_types
            assert "maintenance_task" in blocker_types

            print("\n4. Executing 'get_milestones' for AC-009:")
            ms_res = await asyncio.wait_for(
                session.call_tool("get_milestones", {"aircraft_id": "AC-009"}),
                timeout=5.0,
            )
            if ms_res.isError:
                print("ERROR: get_milestones failed.")
                sys.exit(1)
            ms_data = json.loads(ms_res.content[0].text)
            print(f"Milestones data: {ms_data}")
            assert len(ms_data["data"]) == 1
            assert ms_data["data"][0]["source_id"] == "MS-009-FTC"

            print("\n5. Verifying malformed arguments return an MCP error:")
            invalid_res = await asyncio.wait_for(
                session.call_tool("get_aircraft_status", {"aircraft_id": "AC-09"}),
                timeout=5.0,
            )
            assert invalid_res.isError is True
            print("Malformed aircraft ID: correctly rejected")

            print("\n6. Verifying read-only tool surface:")
            print("No SQL or mutation tools are registered.")

            print("\n==================================================")
            print("SMOKE TEST SUCCESSFUL!")
            print("==================================================")
    except Exception as e:
        print(f"\nERROR: Smoke test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
