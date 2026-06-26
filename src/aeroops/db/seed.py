"""Idempotent database seeding for AeroOps.

Dataset Version: 1.0.0
Snapshot Date: 2026-06-24

This module populates the database using SQL UPSERT operations.
It executes the entire operation inside a single transaction to ensure consistency.
"""

from __future__ import annotations

import sqlite3
from datetime import date

# Define deterministic snapshot date
SNAPSHOT_DATE = date(2026, 6, 24)


def seed_all(conn: sqlite3.Connection) -> None:
    """Seed the database with synthetic demonstration data using SQL UPSERT.

    This operation is completely idempotent and is run inside a transaction.
    """
    # Enforce foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        # --- 1. AIRCRAFT ---
        aircraft_data = [
            (
                "AC-007",
                "AC-007 Prototype",
                "green",
                "Flight Test Group",
                "2026-01-10T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            (
                "AC-008",
                "AC-008 Fleet Lead",
                "amber",
                "Systems Engineering",
                "2026-02-15T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            (
                "AC-009",
                "AC-009 Avionics Testbed",
                "red",
                "Flight Controls Division",
                "2026-03-01T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            (
                "AC-010",
                "AC-010 Structural Testbed",
                "green",
                "Structures Group",
                "2026-04-12T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in aircraft_data:
            conn.execute(
                """
                INSERT INTO aircraft (
                    source_id, name, status, responsible_org,
                    created_at, updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    responsible_org = excluded.responsible_org,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 2. MILESTONES ---
        milestone_data = [
            # AC-007: Completed FTC
            (
                "MS-007-FTC",
                "AC-007",
                "Flight Test Clearance",
                "2026-06-20",
                "2026-06-20",
                "complete",
                "Test Director",
                "2026-01-10T09:00:00Z",
                "2026-06-20T17:00:00Z",
            ),
            # AC-008: At risk due to a part constraint
            (
                "MS-008-FTC",
                "AC-008",
                "Flight Test Clearance",
                "2026-06-25",
                "2026-06-28",
                "at_risk",
                "Operations Lead",
                "2026-02-15T09:00:00Z",
                "2026-06-24T09:00:00Z",
            ),
            # AC-009: Slip from planned (2026-06-29) to forecast (2026-07-05) -> 6-day delay
            (
                "MS-009-FTC",
                "AC-009",
                "Flight Test Clearance",
                "2026-06-29",
                "2026-07-05",
                "at_risk",
                "Program Manager",
                "2026-03-01T09:00:00Z",
                "2026-06-24T09:00:00Z",
            ),
            # AC-010: On Track
            (
                "MS-010-FTC",
                "AC-010",
                "Flight Test Clearance",
                "2026-07-15",
                "2026-07-15",
                "on_track",
                "Structures Lead",
                "2026-04-12T09:00:00Z",
                "2026-06-24T09:00:00Z",
            ),
        ]
        for row in milestone_data:
            conn.execute(
                """
                INSERT INTO milestones (
                    source_id, aircraft_id, name, planned_date, forecast_date,
                    status, responsible_role, created_at, updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    name = excluded.name,
                    planned_date = excluded.planned_date,
                    forecast_date = excluded.forecast_date,
                    status = excluded.status,
                    responsible_role = excluded.responsible_role,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 3. DEFECTS ---
        defect_data = [
            # AC-007: Closed defect
            (
                "DEF-007-001",
                "AC-007",
                "Rudder bracket paint chip",
                "Minor paint degradation on tail bracket",
                "low",
                "closed",
                "2026-06-15T10:00:00Z",
                "2026-06-18T15:00:00Z",
                "Quality Inspector",
                "2026-06-15T10:00:00Z",
                "2026-06-18T15:00:00Z",
            ),
            # AC-009: The critical actuator mismatch discovered during aborted test
            (
                "DEF-009-042",
                "AC-009",
                "Flight-control actuator position mismatch",
                "Intermittent feedback deviation in primary flight-control actuator",
                "high",
                "open",
                "2026-06-23T14:30:00Z",
                None,
                "Controls Engineer",
                "2026-06-23T14:30:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in defect_data:
            conn.execute(
                """
                INSERT INTO defects (
                    source_id, aircraft_id, title, description, severity,
                    status, discovered_at, closed_at, responsible_role,
                    created_at, updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    title = excluded.title,
                    description = excluded.description,
                    severity = excluded.severity,
                    status = excluded.status,
                    discovered_at = excluded.discovered_at,
                    closed_at = excluded.closed_at,
                    responsible_role = excluded.responsible_role,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 4. TEST EVENTS ---
        test_event_data = [
            # AC-007: Completed test
            (
                "TEST-007-101",
                "AC-007",
                "Ground Vibration Test",
                "completed",
                "Test Engineer",
                "2026-06-19",
                "2026-06-19T09:00:00Z",
                "2026-06-19T16:00:00Z",
                "2026-06-10T08:00:00Z",
                "2026-06-19T16:00:00Z",
            ),
            # AC-008: Planned test
            (
                "TEST-008-202",
                "AC-008",
                "Fuel System Flow Test",
                "planned",
                "Fuel Systems Lead",
                "2026-06-26",
                None,
                None,
                "2026-06-15T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # AC-009: Aborted flight test on 2026-06-23
            (
                "TEST-009-118",
                "AC-009",
                "Low-speed taxi and brake test",
                "aborted",
                "Command Pilot",
                "2026-06-23",
                "2026-06-23T10:00:00Z",
                "2026-06-23T12:15:00Z",
                "2026-06-01T08:00:00Z",
                "2026-06-23T12:15:00Z",
            ),
            # AC-009: Blocked test event
            (
                "TEST-009-121",
                "AC-009",
                "High-speed taxi and initial rotation",
                "blocked",
                "Lead Test Pilot",
                "2026-07-02",
                None,
                None,
                "2026-06-01T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # AC-010: Planned test
            (
                "TEST-010-301",
                "AC-010",
                "Fuselage Pressure Test",
                "planned",
                "Structures Team",
                "2026-07-02",
                None,
                None,
                "2026-06-20T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in test_event_data:
            conn.execute(
                """
                INSERT INTO test_events (
                    source_id, aircraft_id, name, status, responsible_role,
                    scheduled_date, started_at, completed_at, created_at,
                    updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    name = excluded.name,
                    status = excluded.status,
                    responsible_role = excluded.responsible_role,
                    scheduled_date = excluded.scheduled_date,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 5. MAINTENANCE TASKS ---
        maintenance_data = [
            # AC-009: Post-abort inspection due 2026-06-26
            (
                "MNT-009-015",
                "AC-009",
                "Post-abort actuator housing inspection",
                "Detailed physical inspection of flight-control actuator linkage after test abort",
                "scheduled",
                "Rigging Technician",
                "2026-06-26",
                None,
                "2026-06-23T13:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in maintenance_data:
            conn.execute(
                """
                INSERT INTO maintenance_tasks (
                    source_id, aircraft_id, title, description, status,
                    responsible_role, due_date, completed_at, created_at,
                    updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    responsible_role = excluded.responsible_role,
                    due_date = excluded.due_date,
                    completed_at = excluded.completed_at,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 6. PARTS CONSTRAINTS ---
        parts_data = [
            # AC-008: Blocker part for AC-008
            (
                "PART-ACT-550",
                "AC-008",
                "ACT-550",
                "Fuel manifold pressure regulator",
                "delayed",
                "Supply Chain",
                "2026-06-25",
                "2026-06-27",
                "2026-06-15T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # AC-009: Replacement part needed by 2026-06-27 but arriving on 2026-06-30
            (
                "PART-ACT-774",
                "AC-009",
                "PART-ACT-774",
                "Flight-control actuator assembly",
                "awaiting_delivery",
                "Procurement",
                "2026-06-27",
                "2026-06-30",
                "2026-06-23T15:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in parts_data:
            conn.execute(
                """
                INSERT INTO parts_constraints (
                    source_id, aircraft_id, part_number, description, status,
                    responsible_org, needed_by, estimated_arrival, created_at,
                    updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    part_number = excluded.part_number,
                    description = excluded.description,
                    status = excluded.status,
                    responsible_org = excluded.responsible_org,
                    needed_by = excluded.needed_by,
                    estimated_arrival = excluded.estimated_arrival,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 7. CHANGE REQUESTS ---
        change_requests_data = [
            # AC-009: CR-184 submitted 2026-06-23, awaiting safety/configuration review
            (
                "CR-184",
                "AC-009",
                "Actuator feedback software threshold adjustment",
                "Configuration update to increase actuator position mismatch warning tolerance",
                "pending_review",
                "Systems Engineering Board",
                "2026-06-23T16:00:00Z",
                None,
                "2026-06-23T16:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in change_requests_data:
            conn.execute(
                """
                INSERT INTO change_requests (
                    source_id, aircraft_id, title, description, status,
                    responsible_role, submitted_at, approved_at, created_at,
                    updated_at, synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    responsible_role = excluded.responsible_role,
                    submitted_at = excluded.submitted_at,
                    approved_at = excluded.approved_at,
                    updated_at = excluded.updated_at;
                """,
                row,
            )

        # --- 8. SCHEDULE DEPENDENCIES ---
        # TEST-009-121 depends on: DEF-009-042, PART-ACT-774, CR-184, MNT-009-015
        dependency_data = [
            # Blocker 1: DEF-009-042
            (
                "DEP-009-001",
                "AC-009",
                "TEST-009-121",
                "DEF-009-042",
                None,
                None,
                None,
                "2026-06-24T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # Blocker 2: PART-ACT-774
            (
                "DEP-009-002",
                "AC-009",
                "TEST-009-121",
                None,
                "PART-ACT-774",
                None,
                None,
                "2026-06-24T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # Blocker 3: CR-184
            (
                "DEP-009-003",
                "AC-009",
                "TEST-009-121",
                None,
                None,
                "CR-184",
                None,
                "2026-06-24T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # Blocker 4: MNT-009-015
            (
                "DEP-009-004",
                "AC-009",
                "TEST-009-121",
                None,
                None,
                None,
                "MNT-009-015",
                "2026-06-24T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
            # AC-008 blocker: PART-ACT-550
            (
                "DEP-008-001",
                "AC-008",
                "TEST-008-202",
                None,
                "PART-ACT-550",
                None,
                None,
                "2026-06-24T08:00:00Z",
                "2026-06-24T08:00:00Z",
            ),
        ]
        for row in dependency_data:
            conn.execute(
                """
                INSERT INTO schedule_dependencies (
                    source_id, aircraft_id, blocked_test_id, blocker_defect_id,
                    blocker_parts_constraint_id, blocker_change_request_id,
                    blocker_maintenance_task_id, created_at, updated_at,
                    synthetic_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    aircraft_id = excluded.aircraft_id,
                    blocked_test_id = excluded.blocked_test_id,
                    blocker_defect_id = excluded.blocker_defect_id,
                    blocker_parts_constraint_id = excluded.blocker_parts_constraint_id,
                    blocker_change_request_id = excluded.blocker_change_request_id,
                    blocker_maintenance_task_id = excluded.blocker_maintenance_task_id,
                    updated_at = excluded.updated_at;
                """,
                row,
            )
