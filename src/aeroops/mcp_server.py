"""AeroOps Read-Only MCP Server.

This module implements a Model Context Protocol (MCP) server over stdio
transport providing read-only access to synthetic aviation program data.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from aeroops import __version__
from aeroops.config import get_settings
from aeroops.db import repository
from aeroops.models import (
    AIRCRAFT_ID_PATTERN,
    Aircraft,
    ChangeRequest,
    Defect,
    MaintenanceTask,
    Milestone,
    PartsConstraint,
    ScheduleDependency,
    TestEvent,
)
from aeroops.services_data import get_fleet_summary_data

# Configure logging to sys.stderr only
logger = logging.getLogger("aeroops-data-mcp")
logger.setLevel(logging.INFO)
logger.handlers.clear()
stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] aeroops-data-mcp: %(message)s")
)
logger.addHandler(stream_handler)

# Instantiate the FastMCP server
mcp = FastMCP("aeroops-data-mcp")

# Enforce limits
MAX_RECORDS = 50

# Validator constants
ALLOWED_AIRCRAFT_STATUSES = {"green", "amber", "red"}
ALLOWED_DEFECT_SEVERITIES = {"low", "medium", "high", "critical"}
ALLOWED_TEST_STATUSES = {"planned", "blocked", "in_progress", "completed", "aborted"}
ALLOWED_MAINTENANCE_STATUSES = {"scheduled", "in_progress", "completed", "deferred"}


# Pydantic response models
class BaseResponse(BaseModel):
    snapshot_date: str = "2026-06-24"
    synthetic_data: bool = True
    source_refs: list[str] = Field(default_factory=list)


class HealthData(BaseModel):
    status: str
    version: str
    db_connected: bool


class HealthCheckResponse(BaseResponse):
    data: HealthData


class AircraftListResponse(BaseResponse):
    data: list[Aircraft]
    count: int
    truncated: bool


class AircraftStatusResponse(BaseResponse):
    data: Aircraft


class DefectListResponse(BaseResponse):
    data: list[Defect]
    count: int
    truncated: bool


class TestEventListResponse(BaseResponse):
    data: list[TestEvent]
    count: int
    truncated: bool


class MaintenanceTaskListResponse(BaseResponse):
    data: list[MaintenanceTask]
    count: int
    truncated: bool


class PartsConstraintListResponse(BaseResponse):
    data: list[PartsConstraint]
    count: int
    truncated: bool


class ChangeRequestListResponse(BaseResponse):
    data: list[ChangeRequest]
    count: int
    truncated: bool


class MilestoneListResponse(BaseResponse):
    data: list[Milestone]
    count: int
    truncated: bool


class DependencyNode(BaseModel):
    id: str
    type: (
        str  # 'test_event' | 'defect' | 'parts_constraint' | 'change_request' | 'maintenance_task'
    )
    name_or_title: str
    status: str
    severity: str | None = None
    responsible: str | None = None


class DependencyEdge(BaseModel):
    blocked_id: str
    blocker_id: str
    blocker_type: str


class DependencyGraphData(BaseModel):
    aircraft_id: str
    nodes: list[DependencyNode]
    edges: list[DependencyEdge]
    dependencies: list[ScheduleDependency] = Field(default_factory=list)


class DependencyGraphResponse(BaseResponse):
    data: DependencyGraphData
    count: int
    truncated: bool


class FleetSummaryData(BaseModel):
    total_aircraft: int
    status_counts: dict[str, int]
    total_open_defects: int
    total_blocked_tests: int
    total_outstanding_parts_constraints: int
    total_pending_change_requests: int
    total_high_critical_defects: int
    total_blocked_delayed_tests: int
    total_upcoming_milestones: int


class FleetSummaryResponse(BaseResponse):
    data: FleetSummaryData


# Helper functions
def resolve_and_verify_db_path() -> Path:
    """Resolve and verify database path from environment, settings, or fallback.

    Returns:
        The verified absolute Path to the SQLite database file.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    # 1. Environment variable
    env_path = os.getenv("AEROOPS_DB_PATH")
    if env_path:
        path = Path(env_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"Database file specified in AEROOPS_DB_PATH does not exist: {path}"
            )
        return path

    # 2. Existing settings
    settings_path = get_settings().db_path
    path = Path(settings_path).resolve()
    if path.is_file():
        return path

    # 3. Package/project-relative fallback
    fallback_path = Path(__file__).resolve().parents[2] / "data" / "aeroops.db"
    if fallback_path.is_file():
        return fallback_path

    raise FileNotFoundError(
        f"Could not locate database file. Checked env: {env_path}, "
        f"settings: {path}, fallback: {fallback_path}"
    )


def verify_aircraft_exists(aircraft_id: str, db_path: Path) -> None:
    """Verify that the aircraft ID is valid and exists in the database.

    Raises:
        ValueError: If validation fails or the aircraft does not exist.
    """
    if not re.match(AIRCRAFT_ID_PATTERN, aircraft_id):
        raise ValueError(
            f"Malformed Aircraft identifier: '{aircraft_id}'. Expected pattern: ^AC-\\d{{3}}$"
        )
    ac = repository.get_aircraft(aircraft_id, db_path=db_path)
    if ac is None:
        raise ValueError(f"Aircraft not found: '{aircraft_id}'")


def validate_aircraft_status(status: str | None) -> str | None:
    """Normalize and validate aircraft status filter."""
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized not in ALLOWED_AIRCRAFT_STATUSES:
        raise ValueError(
            f"Invalid Aircraft status filter: '{status}'. "
            f"Allowed values: {', '.join(sorted(ALLOWED_AIRCRAFT_STATUSES))}"
        )
    return normalized


def validate_defect_severity(severity: str | None) -> str | None:
    """Normalize and validate defect severity filter."""
    if severity is None:
        return None
    normalized = severity.strip().lower()
    if normalized not in ALLOWED_DEFECT_SEVERITIES:
        raise ValueError(
            f"Invalid Defect severity filter: '{severity}'. "
            f"Allowed values: {', '.join(sorted(ALLOWED_DEFECT_SEVERITIES))}"
        )
    return normalized


def validate_test_status(status: str | None) -> str | None:
    """Normalize and validate test event status filter."""
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized not in ALLOWED_TEST_STATUSES:
        raise ValueError(
            f"Invalid Test status filter: '{status}'. "
            f"Allowed values: {', '.join(sorted(ALLOWED_TEST_STATUSES))}"
        )
    return normalized


def validate_maintenance_status(status: str | None) -> str | None:
    """Normalize and validate maintenance task status filter."""
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized not in ALLOWED_MAINTENANCE_STATUSES:
        raise ValueError(
            f"Invalid Maintenance status filter: '{status}'. "
            f"Allowed values: {', '.join(sorted(ALLOWED_MAINTENANCE_STATUSES))}"
        )
    return normalized


def mcp_error_handler(func):
    """Decorator to catch exceptions and raise them formatted as JSON strings."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            err_msg = str(e)
            category = "VALIDATION_ERROR"
            if "not found" in err_msg.lower():
                category = "NOT_FOUND"

            logger.warning(f"Error calling {func.__name__}: [{category}] {err_msg}")
            error_data = {"error": {"category": category, "message": err_msg}}
            raise ValueError(json.dumps(error_data)) from e
        except FileNotFoundError as e:
            err_msg = str(e)
            logger.error(f"Database unavailable: {err_msg}")
            error_data = {
                "error": {
                    "category": "DATABASE_UNAVAILABLE",
                    "message": "The operational database is unavailable.",
                }
            }
            raise ValueError(json.dumps(error_data)) from e
        except Exception as e:
            logger.error(f"Internal error in {func.__name__}: {e!s}", exc_info=True)
            error_data = {
                "error": {
                    "category": "INTERNAL_ERROR",
                    "message": "An internal error occurred during tool execution.",
                }
            }
            raise ValueError(json.dumps(error_data)) from e

    return wrapper


# MCP Tool Definitions
@mcp.tool()
@mcp_error_handler
def health_check() -> HealthCheckResponse:
    """Verify that the MCP server is operational and can connect to the database.

    Returns:
        Structured health status and database connection details.
    """
    db_connected = False
    try:
        db_path = resolve_and_verify_db_path()
        # Run a simple query to confirm database connection is active
        repository.list_aircraft(db_path=db_path, limit=1)
        db_connected = True
    except Exception as e:
        logger.warning(f"Health check failed to verify database: {e}")

    if not db_connected:
        raise FileNotFoundError("Database connection check failed.")

    return HealthCheckResponse(
        source_refs=[],
        data=HealthData(
            status="ok",
            version=__version__,
            db_connected=db_connected,
        ),
    )


@mcp.tool()
@mcp_error_handler
def list_aircraft(status: str | None = None) -> AircraftListResponse:
    """Retrieve all aircraft prototypes, optionally filtered by health status.

    Args:
        status: Optional status to filter by ('green', 'amber', 'red').
    """
    status_filter = validate_aircraft_status(status)
    db_path = resolve_and_verify_db_path()

    # Query with MAX_RECORDS + 1 to detect truncation
    aircraft = repository.list_aircraft(db_path=db_path, limit=MAX_RECORDS + 1)

    # Perform filter if requested
    if status_filter is not None:
        aircraft = [ac for ac in aircraft if ac.status == status_filter]

    truncated = len(aircraft) > MAX_RECORDS
    aircraft = aircraft[:MAX_RECORDS]

    source_refs = sorted(list({ac.source_id for ac in aircraft}))

    return AircraftListResponse(
        data=aircraft, count=len(aircraft), truncated=truncated, source_refs=source_refs
    )


@mcp.tool()
@mcp_error_handler
def get_aircraft_status(aircraft_id: str) -> AircraftStatusResponse:
    """Retrieve detailed metadata for a specific aircraft by ID.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
    """
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    ac = repository.get_aircraft(aircraft_id, db_path=db_path)
    if ac is None:
        raise ValueError(f"Aircraft not found: '{aircraft_id}'")

    return AircraftStatusResponse(data=ac, source_refs=[ac.source_id])


@mcp.tool()
@mcp_error_handler
def get_milestones(aircraft_id: str) -> MilestoneListResponse:
    """Retrieve all milestones for a given aircraft.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
    """
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    milestones = repository.get_milestones(aircraft_id, db_path=db_path)

    truncated = len(milestones) > MAX_RECORDS
    milestones = milestones[:MAX_RECORDS]

    source_refs = sorted(list({m.source_id for m in milestones}))

    return MilestoneListResponse(
        data=milestones,
        count=len(milestones),
        truncated=truncated,
        source_refs=source_refs,
    )


@mcp.tool()
@mcp_error_handler
def get_open_defects(aircraft_id: str, severity: str | None = None) -> DefectListResponse:
    """Retrieve open defects for a specific aircraft, optionally filtered by severity.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
        severity: Optional severity filter ('low', 'medium', 'high', 'critical').
    """
    severity_filter = validate_defect_severity(severity)
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    defects = repository.get_defects(
        aircraft_id=aircraft_id, status="open", db_path=db_path, limit=MAX_RECORDS + 1
    )

    if severity_filter is not None:
        defects = [d for d in defects if d.severity == severity_filter]

    truncated = len(defects) > MAX_RECORDS
    defects = defects[:MAX_RECORDS]

    source_refs = sorted(list({d.source_id for d in defects}))

    return DefectListResponse(
        data=defects,
        count=len(defects),
        truncated=truncated,
        source_refs=source_refs,
        aircraft_id=aircraft_id,
    )


@mcp.tool()
@mcp_error_handler
def get_test_events(aircraft_id: str, status: str | None = None) -> TestEventListResponse:
    """Retrieve test events for an aircraft, optionally filtered by status.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
        status: Optional status filter ('planned', 'blocked', 'in_progress', 'completed',
          'aborted').
    """
    status_filter = validate_test_status(status)
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    test_events = repository.get_test_events(
        aircraft_id=aircraft_id, status=status_filter, db_path=db_path, limit=MAX_RECORDS + 1
    )

    truncated = len(test_events) > MAX_RECORDS
    test_events = test_events[:MAX_RECORDS]

    source_refs = sorted(list({te.source_id for te in test_events}))

    return TestEventListResponse(
        data=test_events,
        count=len(test_events),
        truncated=truncated,
        source_refs=source_refs,
        aircraft_id=aircraft_id,
    )


@mcp.tool()
@mcp_error_handler
def get_maintenance_tasks(
    aircraft_id: str, status: str | None = None
) -> MaintenanceTaskListResponse:
    """Retrieve maintenance tasks for an aircraft, optionally filtered by status.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
        status: Optional status filter ('scheduled', 'in_progress', 'completed', 'deferred').
    """
    status_filter = validate_maintenance_status(status)
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    tasks = repository.get_maintenance_tasks(
        aircraft_id=aircraft_id, db_path=db_path, limit=MAX_RECORDS + 1
    )

    if status_filter is not None:
        tasks = [t for t in tasks if t.status == status_filter]

    truncated = len(tasks) > MAX_RECORDS
    tasks = tasks[:MAX_RECORDS]

    source_refs = sorted(list({t.source_id for t in tasks}))

    return MaintenanceTaskListResponse(
        data=tasks,
        count=len(tasks),
        truncated=truncated,
        source_refs=source_refs,
        aircraft_id=aircraft_id,
    )


@mcp.tool()
@mcp_error_handler
def get_parts_constraints(aircraft_id: str) -> PartsConstraintListResponse:
    """Retrieve parts constraints (supply delay, awaiting delivery) for an aircraft.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
    """
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    parts = repository.get_parts_constraints(
        aircraft_id=aircraft_id, db_path=db_path, limit=MAX_RECORDS + 1
    )

    truncated = len(parts) > MAX_RECORDS
    parts = parts[:MAX_RECORDS]

    source_refs = sorted(list({p.source_id for p in parts}))

    return PartsConstraintListResponse(
        data=parts,
        count=len(parts),
        truncated=truncated,
        source_refs=source_refs,
        aircraft_id=aircraft_id,
    )


@mcp.tool()
@mcp_error_handler
def get_change_requests(aircraft_id: str) -> ChangeRequestListResponse:
    """Retrieve engineering change requests submitted for an aircraft.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
    """
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    crs = repository.get_change_requests(
        aircraft_id=aircraft_id, db_path=db_path, limit=MAX_RECORDS + 1
    )

    truncated = len(crs) > MAX_RECORDS
    crs = crs[:MAX_RECORDS]

    source_refs = sorted(list({c.source_id for c in crs}))

    return ChangeRequestListResponse(
        data=crs,
        count=len(crs),
        truncated=truncated,
        source_refs=source_refs,
        aircraft_id=aircraft_id,
    )


@mcp.tool()
@mcp_error_handler
def get_dependency_graph(aircraft_id: str) -> DependencyGraphResponse:
    """Retrieve schedule dependency graph of test events and blockers for an aircraft.

    Args:
        aircraft_id: Valid aircraft ID in AC-NNN format (e.g. 'AC-009').
    """
    db_path = resolve_and_verify_db_path()
    verify_aircraft_exists(aircraft_id, db_path)

    # Fetch test events (limit to MAX_RECORDS + 1)
    test_events = repository.get_test_events(
        aircraft_id=aircraft_id, db_path=db_path, limit=MAX_RECORDS + 1
    )
    truncated = len(test_events) > MAX_RECORDS
    test_events = test_events[:MAX_RECORDS]

    nodes = []
    edges = []
    node_ids = set()
    edge_keys = set()
    dep_ids = set()
    dependencies = []

    for te in test_events:
        # Add test event node
        if te.source_id not in node_ids:
            nodes.append(
                DependencyNode(
                    id=te.source_id,
                    type="test_event",
                    name_or_title=te.name,
                    status=te.status,
                    responsible=te.responsible_role,
                )
            )
            node_ids.add(te.source_id)

        # Get blockers for this test event
        blockers = repository.get_blockers_for_test(te.source_id, db_path=db_path)
        # Also fetch schedule dependencies to get DEP-XXX-XXX IDs
        deps = repository.get_schedule_dependencies(te.source_id, db_path=db_path)
        for dep in deps:
            dep_ids.add(dep.source_id)
            dependencies.append(dep)

        for bl in blockers:
            # Add blocker node
            if bl.source_id not in node_ids:
                # Blocker title and status are mapped directly
                nodes.append(
                    DependencyNode(
                        id=bl.source_id,
                        type=bl.blocker_type,
                        name_or_title=bl.title,
                        status=bl.status,
                        responsible=bl.responsible_role_or_org,
                    )
                )
                node_ids.add(bl.source_id)

            # Add edge
            edge_key = (bl.source_id, te.source_id)
            if edge_key not in edge_keys:
                edges.append(
                    DependencyEdge(
                        blocked_id=te.source_id,
                        blocker_id=bl.source_id,
                        blocker_type=bl.blocker_type,
                    )
                )
                edge_keys.add(edge_key)

    # Apply limits to nodes and edges
    if len(nodes) > MAX_RECORDS or len(edges) > MAX_RECORDS:
        truncated = True
        nodes = nodes[:MAX_RECORDS]
        active_node_ids = {n.id for n in nodes}
        edges = [
            e for e in edges if e.blocked_id in active_node_ids and e.blocker_id in active_node_ids
        ]
        edges = edges[:MAX_RECORDS]

    # Gather source_refs: aircraft_id, all node IDs, and DEP-NNN-NNN IDs
    all_source_refs = {aircraft_id, *node_ids, *dep_ids}
    source_refs = sorted(list(all_source_refs))

    graph_data = DependencyGraphData(
        aircraft_id=aircraft_id, nodes=nodes, edges=edges, dependencies=dependencies
    )

    return DependencyGraphResponse(
        data=graph_data, count=len(nodes), truncated=truncated, source_refs=source_refs
    )


@mcp.tool()
@mcp_error_handler
def get_fleet_summary() -> FleetSummaryResponse:
    """Retrieve program-level summary metrics across all aircraft."""
    db_path = resolve_and_verify_db_path()
    summary, source_refs = get_fleet_summary_data(db_path=db_path)

    fleet_summary_data = FleetSummaryData(**summary)

    return FleetSummaryResponse(data=fleet_summary_data, source_refs=source_refs)


def main():
    """Main execution entrypoint for the stdio MCP server."""
    # Ensure database path is resolvable at startup
    try:
        resolve_and_verify_db_path()
    except Exception as e:
        logger.error(f"Startup check failed: {e}")
        sys.exit(1)

    mcp.run()


if __name__ == "__main__":
    main()
