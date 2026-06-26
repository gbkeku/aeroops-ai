"""Typed Pydantic view models for the AeroOps Streamlit UI.

These models are decoupled from the database layers and are used to build
dashboard components from structured data.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeroops.security import AeroOpsResponse


class FleetDashboardSnapshot(BaseModel):
    """View model representing the high-level fleet overview metrics."""

    aircraft_options: list[str] = Field(
        default_factory=list, description="Available aircraft IDs for selection."
    )
    total_aircraft: int
    green_count: int
    amber_count: int
    red_count: int
    high_critical_defect_count: int
    blocked_delayed_test_count: int
    upcoming_milestone_count: int
    snapshot_date: str = "2026-06-24"
    synthetic_data: bool = True


class DependencyNode(BaseModel):
    """View model representing a node in the schedule dependency graph."""

    source_id: str
    record_type: str
    label: str
    status: str


class DependencyEdge(BaseModel):
    """View model representing a directed edge in the schedule dependency graph."""

    dependency_id: str
    source_id: str
    target_id: str
    relationship: str


class TimelineEvent(BaseModel):
    """View model representing a chronologically sorted event in the program timeline."""

    source_id: str
    event_type: str
    date: str
    title: str
    status: str


class EvidenceTableRow(BaseModel):
    """View model representing a row in the evidence verification table."""

    source_id: str
    record_type: str
    title: str
    status: str
    relevant_date: str
    originating_agent: str


class SafeAgentActivity(BaseModel):
    """View model representing a safe, sanitised trace record of agent tool calls."""

    agent_name: str
    tool_name: str
    duration_ms: float
    succeeded: bool
    source_ref_count: int


class DashboardInvestigationResult(BaseModel):
    """Unified result containing the executive brief and all presentation-ready view models."""

    response: AeroOpsResponse
    dependency_nodes: list[DependencyNode] = Field(default_factory=list)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    evidence_rows: list[EvidenceTableRow] = Field(default_factory=list)
    activity: list[SafeAgentActivity] = Field(default_factory=list)
