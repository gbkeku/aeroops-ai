"""UI controller and service adapter for the AeroOps Streamlit application.

Coordinates interactions between Streamlit, the offline preview fixtures,
the read-only MCP client, and the ADK investigation service.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aeroops.config import get_settings
from aeroops.mcp_client import call_mcp_tool_direct
from aeroops.offline_fixtures import MOCK_FLEET_SNAPSHOT, MOCK_INVESTIGATION_RESULT
from aeroops.security import AeroOpsResponse
from aeroops.services import run_investigation_async
from aeroops.ui_models import (
    DashboardInvestigationResult,
    DependencyEdge,
    DependencyNode,
    EvidenceTableRow,
    FleetDashboardSnapshot,
    SafeAgentActivity,
    TimelineEvent,
)


def _run_async_sync(coro: Any) -> Any:
    """Run an async coroutine synchronously, supporting calls from running event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def get_aircraft_options(db_path_override: str | None = None) -> list[str]:
    """Retrieve aircraft IDs available for selection."""
    if get_settings().offline_demo:
        return MOCK_FLEET_SNAPSHOT.aircraft_options

    async def _fetch():
        result = await call_mcp_tool_direct("list_aircraft", {}, db_path_override)
        aircraft_list = result.get("data", [])
        return [ac["source_id"] for ac in aircraft_list if "source_id" in ac]

    try:
        return _run_async_sync(_fetch())
    except Exception:
        # Live failure does not fallback to offline fixtures
        raise


def get_fleet_dashboard_snapshot(db_path_override: str | None = None) -> FleetDashboardSnapshot:
    """Retrieve aggregate fleet summary and status metrics."""
    if get_settings().offline_demo:
        return MOCK_FLEET_SNAPSHOT

    async def _fetch():
        # Call list_aircraft to get selector options
        ac_result = await call_mcp_tool_direct("list_aircraft", {}, db_path_override)
        aircraft_list = ac_result.get("data", [])
        options = [ac["source_id"] for ac in aircraft_list if "source_id" in ac]

        # Call get_fleet_summary to get metrics
        summary_result = await call_mcp_tool_direct("get_fleet_summary", {}, db_path_override)
        data = summary_result.get("data", {})

        status_counts = data.get("status_counts", {})
        return FleetDashboardSnapshot(
            aircraft_options=options,
            total_aircraft=data.get("total_aircraft", 0),
            green_count=status_counts.get("green", 0),
            amber_count=status_counts.get("amber", 0),
            red_count=status_counts.get("red", 0),
            high_critical_defect_count=data.get("total_high_critical_defects", 0),
            blocked_delayed_test_count=data.get("total_blocked_delayed_tests", 0),
            upcoming_milestone_count=data.get("total_upcoming_milestones", 0),
            snapshot_date=summary_result.get("snapshot_date", "2026-06-24"),
            synthetic_data=True,
        )

    try:
        return _run_async_sync(_fetch())
    except Exception:
        # Live failure does not fallback to offline fixtures
        raise


def _derive_result_from_catalog(
    response: AeroOpsResponse,
    catalog: Any,
    activities: list[SafeAgentActivity],
) -> DashboardInvestigationResult:
    """Construct presentation models from the validated EvidenceCatalog."""
    if not catalog or not hasattr(catalog, "records"):
        return DashboardInvestigationResult(response=response, activity=activities)

    edges: list[DependencyEdge] = []
    referenced_node_ids: set[str] = set()

    # 1. Dependency Edges
    for rec in catalog.records.values():
        if getattr(rec, "record_type", None) and rec.record_type.value == "schedule_dependency":
            payload = rec.payload
            blocked_test_id = payload.get("blocked_test_id")

            # Extract the non-None blocker ID and type
            blocker_id = None
            blocker_type = None
            if payload.get("blocker_defect_id"):
                blocker_id = payload["blocker_defect_id"]
                blocker_type = "defect"
            elif payload.get("blocker_parts_constraint_id"):
                blocker_id = payload["blocker_parts_constraint_id"]
                blocker_type = "parts_constraint"
            elif payload.get("blocker_change_request_id"):
                blocker_id = payload["blocker_change_request_id"]
                blocker_type = "change_request"
            elif payload.get("blocker_maintenance_task_id"):
                blocker_id = payload["blocker_maintenance_task_id"]
                blocker_type = "maintenance_task"

            if blocker_id and blocked_test_id:
                edges.append(
                    DependencyEdge(
                        dependency_id=rec.source_id,
                        source_id=blocker_id,
                        target_id=blocked_test_id,
                        relationship=blocker_type or "unknown",
                    )
                )
                referenced_node_ids.add(blocker_id)
                referenced_node_ids.add(blocked_test_id)

    # 2. Dependency Nodes
    nodes: list[DependencyNode] = []
    for rec in catalog.records.values():
        rec_type = getattr(rec, "record_type", None)
        if not rec_type:
            continue

        # Test events are nodes, as are any blockers referenced in edges
        if rec_type.value == "test_event" or rec.source_id in referenced_node_ids:
            payload = rec.payload
            label = rec.source_id

            if rec_type.value == "test_event":
                label = payload.get("name") or rec.source_id
            elif rec_type.value == "defect":
                label = payload.get("title") or rec.source_id
            elif rec_type.value == "parts_constraint":
                label = f"{payload.get('part_number')} - {payload.get('description')}"
            elif rec_type.value == "change_request" or rec_type.value == "maintenance_task":
                label = payload.get("title") or rec.source_id

            nodes.append(
                DependencyNode(
                    source_id=rec.source_id,
                    record_type=rec_type.value,
                    label=str(label),
                    status=str(payload.get("status") or "unknown"),
                )
            )

    # 3. Timeline Events
    timeline_events: list[TimelineEvent] = []
    for rec in catalog.records.values():
        rec_type = getattr(rec, "record_type", None)
        if not rec_type:
            continue

        payload = rec.payload
        date_str = None
        title = None

        if rec_type.value == "milestone":
            date_str = payload.get("forecast_date") or payload.get("planned_date")
            title = payload.get("name")
        elif rec_type.value == "test_event":
            date_str = payload.get("scheduled_date")
            title = payload.get("name")
        elif rec_type.value == "maintenance_task":
            date_str = payload.get("due_date")
            title = payload.get("title")
        elif rec_type.value == "parts_constraint":
            date_str = payload.get("estimated_arrival") or payload.get("needed_by")
            title = payload.get("description")

        if date_str and title:
            timeline_events.append(
                TimelineEvent(
                    source_id=rec.source_id,
                    event_type=rec_type.value,
                    date=str(date_str),
                    title=str(title),
                    status=str(payload.get("status") or "unknown"),
                )
            )

    # Sort chronologically by date
    timeline_events.sort(key=lambda e: e.date)

    # 4. Evidence Table Rows
    evidence_rows: list[EvidenceTableRow] = []
    for rec in catalog.records.values():
        rec_type = getattr(rec, "record_type", None)
        if not rec_type:
            continue

        payload = rec.payload

        # Determine title
        title = (
            payload.get("name")
            or payload.get("title")
            or payload.get("description")
            or rec.source_id
        )
        if rec_type.value == "parts_constraint":
            title = f"{payload.get('part_number')} - {payload.get('description')}"

        # Determine date
        relevant_date = (
            payload.get("forecast_date")
            or payload.get("scheduled_date")
            or payload.get("due_date")
            or payload.get("needed_by")
            or payload.get("discovered_at")
            or payload.get("submitted_at")
            or "2026-06-24"
        )

        # Determine originating agent from provenance trace
        originating_agent = "unknown"
        if rec.provenance:
            prov = rec.provenance[0]
            agent_str = prov.originating_agent or prov.originating_stage or ""
            if "test_ops" in agent_str:
                originating_agent = "test_ops_specialist"
            elif "maintenance" in agent_str:
                originating_agent = "maintenance_specialist"
            elif "configuration_supply" in agent_str:
                originating_agent = "config_supply_specialist"
            elif "schedule_risk" in agent_str:
                originating_agent = "schedule_risk_specialist"
            elif "preflight" in agent_str:
                originating_agent = "preflight"
            else:
                originating_agent = agent_str or "unknown"

        evidence_rows.append(
            EvidenceTableRow(
                source_id=rec.source_id,
                record_type=rec_type.value,
                title=str(title),
                status=str(payload.get("status") or "unknown"),
                relevant_date=str(relevant_date),
                originating_agent=originating_agent,
            )
        )

    return DashboardInvestigationResult(
        response=response,
        dependency_nodes=nodes,
        dependency_edges=edges,
        timeline_events=timeline_events,
        evidence_rows=evidence_rows,
        activity=activities,
    )


def run_dashboard_investigation(
    query: str,
    aircraft_id: str,
    db_path_override: str | None = None,
) -> DashboardInvestigationResult:
    """Run the aircraft investigation pipeline, validating all input constraints first."""
    # Input validation
    import re

    if not re.search(rf"\b{aircraft_id}\b", query):
        raise ValueError(
            f"Query does not contain the selected aircraft ID '{aircraft_id}'. "
            "Please specify the correct aircraft ID in the query box."
        )

    if get_settings().offline_demo:
        if aircraft_id != "AC-009":
            raise ValueError(
                f"Offline preview mode only supports 'AC-009'. Selected aircraft: '{aircraft_id}'."
            )
        # Returns the deterministic fixture validated against view models
        return MOCK_INVESTIGATION_RESULT

    async def _run():
        # Perform the actual ADK pipeline run
        response = await run_investigation_async(query, db_path=db_path_override)

        # Retrieve private attributes attached during execution
        catalog = getattr(response, "_evidence_catalog", None)
        activities = getattr(response, "_activities", [])

        return _derive_result_from_catalog(response, catalog, activities)

    try:
        return _run_async_sync(_run())
    except Exception:
        # Ensure errors are propagated to the caller
        raise


def build_dependency_dot(nodes: list[DependencyNode], edges: list[DependencyEdge]) -> str:
    """Generate DOT graph representation from validated view models, escaping labels."""
    dot_lines = [
        "digraph G {",
        '    node [shape=box, style="filled,rounded", fontname="Helvetica", fontsize=10, penwidth=1];',
        '    edge [fontname="Helvetica", fontsize=8, color="#888888", arrowhead=vee];',
        '    graph [rankdir=TB, bgcolor="transparent"];',
    ]

    for node in nodes:
        status_lower = node.status.lower()
        fillcolor = "#f0f2f6"
        pencolor = "#e0e0e0"

        if status_lower in ("red", "blocked", "aborted", "open", "delayed"):
            fillcolor = "#ffebee"  # soft red
            pencolor = "#ef9a9a"
        elif status_lower in (
            "orange",
            "awaiting_delivery",
            "pending_review",
            "scheduled",
            "in_progress",
        ):
            fillcolor = "#fff3e0"  # soft orange
            pencolor = "#ffcc80"
        elif status_lower in (
            "green",
            "completed",
            "complete",
            "on_track",
            "delivered",
            "approved",
            "implemented",
        ):
            fillcolor = "#e8f5e9"  # soft green
            pencolor = "#a5d6a7"
        else:
            fillcolor = "#f5f5f5"
            pencolor = "#e0e0e0"

        escaped_label = node.label.replace('"', '\\"')
        escaped_status = node.status.replace('"', '\\"')

        label_text = f"{node.source_id}\\n{escaped_label}\\n({escaped_status})"
        dot_lines.append(
            f'    "{node.source_id}" [label="{label_text}", fillcolor="{fillcolor}", color="{pencolor}"];'
        )

    for edge in edges:
        dot_lines.append(
            f'    "{edge.source_id}" -> "{edge.target_id}" [label="{edge.relationship}"];'
        )

    dot_lines.append("}")
    return "\n".join(dot_lines)
