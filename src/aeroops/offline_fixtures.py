"""Deterministic fixtures for the AeroOps offline preview mode.

All data is dated 2026-06-24 and matches the synthetic AC-009 database.
"""

from __future__ import annotations

from aeroops.security import AeroOpsResponse
from aeroops.ui_models import (
    DashboardInvestigationResult,
    DependencyEdge,
    DependencyNode,
    EvidenceTableRow,
    FleetDashboardSnapshot,
    SafeAgentActivity,
    TimelineEvent,
)

MOCK_FLEET_SNAPSHOT = FleetDashboardSnapshot(
    aircraft_options=["AC-007", "AC-008", "AC-009", "AC-010"],
    total_aircraft=4,
    green_count=2,
    amber_count=1,
    red_count=1,
    high_critical_defect_count=1,
    blocked_delayed_test_count=2,
    upcoming_milestone_count=3,
    snapshot_date="2026-06-24",
    synthetic_data=True,
)

# For AeroOpsResponse in the result:
MOCK_AEROOPS_RESPONSE = AeroOpsResponse(
    session_id="offline-session-id-12345",
    aircraft_id="AC-009",
    overall_status="red",
    planned_milestone_date="2026-06-29",
    forecast_milestone_date="2026-07-05",
    delay_days=6,
    milestone_source_id="MS-009-FTC",
    executive_summary=(
        "AC-009 is delayed 6 days due to an aborted actuator test and four "
        "unresolved blockers. Resolution requires part delivery, defect closure, "
        "and CR approval."
    ),
    confirmed_root_causes=[
        {
            "finding_id": "FIND-TEST-003",
            "statement": "Flight-control actuator mismatch (DEF-009-042) caused TEST-009-118 abort.",
            "classification": "defect",
            "source_refs": [
                {
                    "source_id": "DEF-009-042",
                    "record_type": "defect",
                    "summary": "Critical open defect.",
                },
                {
                    "source_id": "TEST-009-118",
                    "record_type": "test_event",
                    "summary": "Aborted low-speed taxi test.",
                },
            ],
            "rationale": "Direct cause of test abort and downstream blockage.",
            "claims": [],
        }
    ],
    contributing_factors=[
        {
            "finding_id": "FIND-TEST-002",
            "statement": "TEST-009-121 is blocked by four unresolved dependencies.",
            "classification": "dependency_blocker",
            "source_refs": [
                {
                    "source_id": "TEST-009-121",
                    "record_type": "test_event",
                    "summary": "High-speed taxi test blocked.",
                },
                {
                    "source_id": "DEP-009-001",
                    "record_type": "schedule_dependency",
                    "summary": "Blocked by DEF-009-042.",
                },
                {
                    "source_id": "DEP-009-002",
                    "record_type": "schedule_dependency",
                    "summary": "Blocked by PART-ACT-774.",
                },
                {
                    "source_id": "DEP-009-003",
                    "record_type": "schedule_dependency",
                    "summary": "Blocked by CR-184.",
                },
                {
                    "source_id": "DEP-009-004",
                    "record_type": "schedule_dependency",
                    "summary": "Blocked by MNT-009-015.",
                },
            ],
            "rationale": "get_dependency_graph shows four blocker edges.",
            "claims": [],
        },
        {
            "finding_id": "FIND-CONFIG-001",
            "statement": "PART-ACT-774 awaiting delivery blocks TEST-009-121.",
            "classification": "parts_constraint",
            "source_refs": [
                {
                    "source_id": "PART-ACT-774",
                    "record_type": "parts_constraint",
                    "summary": "Actuator assembly delayed.",
                }
            ],
            "rationale": "Prevents replacement and test resumption.",
            "claims": [],
        },
        {
            "finding_id": "FIND-CONFIG-002",
            "statement": "CR-184 pending review blocks configuration sign-off.",
            "classification": "change_request",
            "source_refs": [
                {
                    "source_id": "CR-184",
                    "record_type": "change_request",
                    "summary": "Software threshold CR pending.",
                }
            ],
            "rationale": "Required before actuator software update can proceed.",
            "claims": [],
        },
        {
            "finding_id": "FIND-MAINT-001",
            "statement": "MNT-009-015 inspection not yet complete.",
            "classification": "maintenance",
            "source_refs": [
                {
                    "source_id": "MNT-009-015",
                    "record_type": "maintenance_task",
                    "summary": "Post-abort inspection scheduled.",
                }
            ],
            "rationale": "Blocks sign-off on actuator airworthiness.",
            "claims": [],
        },
    ],
    recommended_actions=[
        {
            "action_id": "ACT-001",
            "action": "Expedite delivery of PART-ACT-774.",
            "classification": "parts_constraint",
            "supporting_finding_ids": ["FIND-CONFIG-001"],
            "source_refs": [
                {
                    "source_id": "PART-ACT-774",
                    "record_type": "parts_constraint",
                    "summary": "Actuator assembly.",
                }
            ],
            "rationale": "Critical path blocker for TEST-009-121.",
            "owner_role": "supply_chain",
            "suggested_due_date": "2026-06-27",
        },
        {
            "action_id": "ACT-002",
            "action": "Complete MNT-009-015 post-abort inspection.",
            "classification": "maintenance",
            "supporting_finding_ids": ["FIND-MAINT-001"],
            "source_refs": [
                {
                    "source_id": "MNT-009-015",
                    "record_type": "maintenance_task",
                    "summary": "Post-abort inspection.",
                }
            ],
            "rationale": "Confirm physical integrity before software test.",
            "owner_role": "maintenance_lead",
            "suggested_due_date": "2026-06-26",
        },
        {
            "action_id": "ACT-003",
            "action": "Approve CR-184 feedback software adjustment.",
            "classification": "change_request",
            "supporting_finding_ids": ["FIND-CONFIG-002"],
            "source_refs": [
                {
                    "source_id": "CR-184",
                    "record_type": "change_request",
                    "summary": "Actuator feedback CR.",
                }
            ],
            "rationale": "Allows software calibration to resolve position mismatch warnings.",
            "owner_role": "engineering",
            "suggested_due_date": "2026-06-25",
        },
    ],
    assumptions=[
        "Actuator physical structural damage is absent pending MNT-009-015 results.",
        "Supplier PART-ACT-774 delivery is on path for 2026-06-30.",
    ],
    unknowns=[
        "Root cause of position feedback discrepancy (whether mechanical or calibration only)."
    ],
    confidence="high",
    evidence=[
        "MS-009-FTC",
        "TEST-009-118",
        "TEST-009-121",
        "DEF-009-042",
        "PART-ACT-774",
        "CR-184",
        "MNT-009-015",
    ],
)

MOCK_INVESTIGATION_RESULT = DashboardInvestigationResult(
    response=MOCK_AEROOPS_RESPONSE,
    dependency_nodes=[
        DependencyNode(
            source_id="TEST-009-121",
            record_type="test_event",
            label="High-speed taxi and initial rotation",
            status="blocked",
        ),
        DependencyNode(
            source_id="DEF-009-042",
            record_type="defect",
            label="Flight-control actuator position mismatch",
            status="open",
        ),
        DependencyNode(
            source_id="PART-ACT-774",
            record_type="parts_constraint",
            label="Flight-control actuator assembly",
            status="awaiting_delivery",
        ),
        DependencyNode(
            source_id="CR-184",
            record_type="change_request",
            label="Actuator feedback software threshold adjustment",
            status="pending_review",
        ),
        DependencyNode(
            source_id="MNT-009-015",
            record_type="maintenance_task",
            label="Post-abort actuator housing inspection",
            status="scheduled",
        ),
    ],
    dependency_edges=[
        DependencyEdge(
            dependency_id="DEP-009-001",
            source_id="DEF-009-042",
            target_id="TEST-009-121",
            relationship="defect",
        ),
        DependencyEdge(
            dependency_id="DEP-009-002",
            source_id="PART-ACT-774",
            target_id="TEST-009-121",
            relationship="parts_constraint",
        ),
        DependencyEdge(
            dependency_id="DEP-009-003",
            source_id="CR-184",
            target_id="TEST-009-121",
            relationship="change_request",
        ),
        DependencyEdge(
            dependency_id="DEP-009-004",
            source_id="MNT-009-015",
            target_id="TEST-009-121",
            relationship="maintenance_task",
        ),
    ],
    timeline_events=[
        TimelineEvent(
            source_id="MS-009-FTC",
            event_type="milestone",
            date="2026-07-05",
            title="Flight Test Clearance",
            status="at_risk",
        ),
        TimelineEvent(
            source_id="MNT-009-015",
            event_type="maintenance_task",
            date="2026-06-26",
            title="Post-abort actuator housing inspection",
            status="scheduled",
        ),
        TimelineEvent(
            source_id="PART-ACT-774",
            event_type="parts_constraint",
            date="2026-06-30",
            title="Flight-control actuator assembly",
            status="awaiting_delivery",
        ),
        TimelineEvent(
            source_id="TEST-009-121",
            event_type="test_event",
            date="2026-07-02",
            title="High-speed taxi and initial rotation",
            status="blocked",
        ),
    ],
    evidence_rows=[
        EvidenceTableRow(
            source_id="MS-009-FTC",
            record_type="milestone",
            title="Flight Test Clearance",
            status="AT_RISK",
            relevant_date="2026-06-29",
            originating_agent="preflight",
        ),
        EvidenceTableRow(
            source_id="DEF-009-042",
            record_type="defect",
            title="Flight-control actuator position mismatch",
            status="OPEN",
            relevant_date="2026-06-23",
            originating_agent="test_ops_specialist",
        ),
        EvidenceTableRow(
            source_id="TEST-009-118",
            record_type="test_event",
            title="Low-speed taxi and brake test",
            status="ABORTED",
            relevant_date="2026-06-23",
            originating_agent="test_ops_specialist",
        ),
        EvidenceTableRow(
            source_id="TEST-009-121",
            record_type="test_event",
            title="High-speed taxi and initial rotation",
            status="BLOCKED",
            relevant_date="2026-07-02",
            originating_agent="test_ops_specialist",
        ),
        EvidenceTableRow(
            source_id="DEP-009-001",
            record_type="schedule_dependency",
            title="Dependency link",
            status="BLOCKING_LINK",
            relevant_date="2026-06-24",
            originating_agent="test_ops_specialist",
        ),
        EvidenceTableRow(
            source_id="DEP-009-002",
            record_type="schedule_dependency",
            title="Dependency link",
            status="BLOCKING_LINK",
            relevant_date="2026-06-24",
            originating_agent="config_supply_specialist",
        ),
        EvidenceTableRow(
            source_id="DEP-009-003",
            record_type="schedule_dependency",
            title="Dependency link",
            status="BLOCKING_LINK",
            relevant_date="2026-06-24",
            originating_agent="config_supply_specialist",
        ),
        EvidenceTableRow(
            source_id="DEP-009-004",
            record_type="schedule_dependency",
            title="Dependency link",
            status="BLOCKING_LINK",
            relevant_date="2026-06-24",
            originating_agent="maintenance_specialist",
        ),
        EvidenceTableRow(
            source_id="PART-ACT-774",
            record_type="parts_constraint",
            title="Flight-control actuator assembly",
            status="AWAITING_DELIVERY",
            relevant_date="2026-06-27",
            originating_agent="config_supply_specialist",
        ),
        EvidenceTableRow(
            source_id="CR-184",
            record_type="change_request",
            title="Actuator feedback software threshold adjustment",
            status="PENDING_REVIEW",
            relevant_date="2026-06-23",
            originating_agent="config_supply_specialist",
        ),
        EvidenceTableRow(
            source_id="MNT-009-015",
            record_type="maintenance_task",
            title="Post-abort actuator housing inspection",
            status="SCHEDULED",
            relevant_date="2026-06-26",
            originating_agent="maintenance_specialist",
        ),
    ],
    activity=[
        SafeAgentActivity(
            agent_name="intake_extractor",
            tool_name="none",
            duration_ms=45.2,
            succeeded=True,
            source_ref_count=0,
        ),
        SafeAgentActivity(
            agent_name="test_ops_specialist",
            tool_name="get_test_events",
            duration_ms=120.5,
            succeeded=True,
            source_ref_count=2,
        ),
        SafeAgentActivity(
            agent_name="test_ops_specialist",
            tool_name="get_open_defects",
            duration_ms=95.1,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="test_ops_specialist",
            tool_name="get_dependency_graph",
            duration_ms=150.3,
            succeeded=True,
            source_ref_count=5,
        ),
        SafeAgentActivity(
            agent_name="maintenance_specialist",
            tool_name="get_open_defects",
            duration_ms=90.0,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="maintenance_specialist",
            tool_name="get_maintenance_tasks",
            duration_ms=110.2,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="config_supply_specialist",
            tool_name="get_parts_constraints",
            duration_ms=115.4,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="config_supply_specialist",
            tool_name="get_change_requests",
            duration_ms=98.8,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="schedule_risk_specialist",
            tool_name="get_aircraft_status",
            duration_ms=85.3,
            succeeded=True,
            source_ref_count=1,
        ),
        SafeAgentActivity(
            agent_name="schedule_risk_specialist",
            tool_name="get_dependency_graph",
            duration_ms=140.9,
            succeeded=True,
            source_ref_count=5,
        ),
        SafeAgentActivity(
            agent_name="executive_synthesis",
            tool_name="none",
            duration_ms=250.7,
            succeeded=True,
            source_ref_count=0,
        ),
    ],
)
