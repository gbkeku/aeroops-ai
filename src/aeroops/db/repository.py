"""Read-only repository functions for querying synthetic operational database records.

All functions use parameterized SQL, validate ID inputs via regex, and enforce
read-only mode.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aeroops.db import get_db_connection
from aeroops.models import (
    AIRCRAFT_ID_PATTERN,
    CR_ID_PATTERN,
    DEFECT_ID_PATTERN,
    MNT_ID_PATTERN,
    PART_ID_PATTERN,
    TEST_ID_PATTERN,
    Aircraft,
    BlockerRecord,
    ChangeRequest,
    Defect,
    MaintenanceTask,
    Milestone,
    PartsConstraint,
    ScheduleDependency,
    TestEvent,
)


def _validate_id(identifier: str, pattern: str, name: str) -> None:
    """Validate that the identifier matches the expected pattern."""
    if not re.match(pattern, identifier):
        raise ValueError(
            f"Malformed {name} identifier: '{identifier}'. Expected pattern: {pattern}"
        )


def get_aircraft(aircraft_id: str, db_path: Path | str | None = None) -> Aircraft | None:
    """Retrieve an aircraft by source ID.

    Args:
        aircraft_id: Valid aircraft identifier (e.g., 'AC-009').
        db_path: Optional path to the database.

    Returns:
        Aircraft model if found, None if unknown.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(
            "SELECT * FROM aircraft WHERE source_id = ?;",
            (aircraft_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return Aircraft(**dict(row))


def list_aircraft(db_path: Path | str | None = None, limit: int | None = None) -> list[Aircraft]:
    """Retrieve all aircraft.

    Args:
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of all Aircraft models.
    """
    query = "SELECT * FROM aircraft"
    params = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [Aircraft(**dict(row)) for row in cursor.fetchall()]


def get_milestones(aircraft_id: str, db_path: Path | str | None = None) -> list[Milestone]:
    """Retrieve all milestones for a given aircraft.

    Args:
        aircraft_id: Valid aircraft identifier.
        db_path: Optional path to the database.

    Returns:
        List of Milestone models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(
            "SELECT * FROM milestones WHERE aircraft_id = ? ORDER BY planned_date ASC;",
            (aircraft_id,),
        )
        return [Milestone(**dict(row)) for row in cursor.fetchall()]


def get_defects(
    aircraft_id: str,
    status: str | None = None,
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> list[Defect]:
    """Retrieve defects for a given aircraft, optionally filtered by status.

    Args:
        aircraft_id: Valid aircraft identifier.
        status: Optional status string to filter by.
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of Defect models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    query = "SELECT * FROM defects WHERE aircraft_id = ?"
    params: list[Any] = [aircraft_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY discovered_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    query += ";"

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [Defect(**dict(row)) for row in cursor.fetchall()]


def get_test_events(
    aircraft_id: str,
    status: str | None = None,
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> list[TestEvent]:
    """Retrieve test events for a given aircraft, optionally filtered by status.

    Args:
        aircraft_id: Valid aircraft identifier.
        status: Optional status string.
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of TestEvent models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    query = "SELECT * FROM test_events WHERE aircraft_id = ?"
    params: list[Any] = [aircraft_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY scheduled_date ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    query += ";"

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [TestEvent(**dict(row)) for row in cursor.fetchall()]


def get_maintenance_tasks(
    aircraft_id: str,
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> list[MaintenanceTask]:
    """Retrieve all maintenance tasks for a given aircraft.

    Args:
        aircraft_id: Valid aircraft identifier.
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of MaintenanceTask models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    query = "SELECT * FROM maintenance_tasks WHERE aircraft_id = ? ORDER BY due_date ASC"
    params = [aircraft_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    query += ";"

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [MaintenanceTask(**dict(row)) for row in cursor.fetchall()]


def get_parts_constraints(
    aircraft_id: str,
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> list[PartsConstraint]:
    """Retrieve all parts constraints for a given aircraft.

    Args:
        aircraft_id: Valid aircraft identifier.
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of PartsConstraint models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    query = "SELECT * FROM parts_constraints WHERE aircraft_id = ? ORDER BY needed_by ASC"
    params = [aircraft_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    query += ";"

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [PartsConstraint(**dict(row)) for row in cursor.fetchall()]


def get_change_requests(
    aircraft_id: str,
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> list[ChangeRequest]:
    """Retrieve all change requests for a given aircraft.

    Args:
        aircraft_id: Valid aircraft identifier.
        db_path: Optional path to the database.
        limit: Optional limit on number of records returned.

    Returns:
        List of ChangeRequest models.
    """
    _validate_id(aircraft_id, AIRCRAFT_ID_PATTERN, "Aircraft")

    query = "SELECT * FROM change_requests WHERE aircraft_id = ? ORDER BY submitted_at ASC"
    params = [aircraft_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    query += ";"

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(query, params)
        return [ChangeRequest(**dict(row)) for row in cursor.fetchall()]


def get_schedule_dependencies(
    blocked_test_id: str, db_path: Path | str | None = None
) -> list[ScheduleDependency]:
    """Retrieve all schedule dependencies for a given blocked test event.

    Args:
        blocked_test_id: Valid test event identifier.
        db_path: Optional path to the database.

    Returns:
        List of ScheduleDependency models.
    """
    _validate_id(blocked_test_id, TEST_ID_PATTERN, "Test Event")

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(
            "SELECT * FROM schedule_dependencies WHERE blocked_test_id = ?;",
            (blocked_test_id,),
        )
        return [ScheduleDependency(**dict(row)) for row in cursor.fetchall()]


def get_blockers_for_test(test_id: str, db_path: Path | str | None = None) -> list[BlockerRecord]:
    """Retrieve typed blocker records for a given test event.

    Args:
        test_id: Valid test event identifier.
        db_path: Optional path to the database.

    Returns:
        List of typed BlockerRecord models representing the blockers.
    """
    _validate_id(test_id, TEST_ID_PATTERN, "Test Event")

    blockers: list[BlockerRecord] = []

    with get_db_connection(db_path=db_path, read_only=True) as conn:
        cursor = conn.execute(
            "SELECT * FROM schedule_dependencies WHERE blocked_test_id = ?;",
            (test_id,),
        )
        deps = cursor.fetchall()

        for dep in deps:
            if dep["blocker_defect_id"] is not None:
                db_id = dep["blocker_defect_id"]
                _validate_id(db_id, DEFECT_ID_PATTERN, "Defect")
                d_row = conn.execute(
                    "SELECT * FROM defects WHERE source_id = ?;", (db_id,)
                ).fetchone()
                if d_row:
                    blockers.append(
                        BlockerRecord(
                            blocker_type="defect",
                            source_id=d_row["source_id"],
                            aircraft_id=d_row["aircraft_id"],
                            title=d_row["title"],
                            status=d_row["status"],
                            relevant_dates={
                                "discovered_at": d_row["discovered_at"],
                                "closed_at": d_row["closed_at"],
                            },
                            responsible_role_or_org=d_row["responsible_role"],
                        )
                    )

            elif dep["blocker_parts_constraint_id"] is not None:
                db_id = dep["blocker_parts_constraint_id"]
                _validate_id(db_id, PART_ID_PATTERN, "Parts Constraint")
                p_row = conn.execute(
                    "SELECT * FROM parts_constraints WHERE source_id = ?;", (db_id,)
                ).fetchone()
                if p_row:
                    blockers.append(
                        BlockerRecord(
                            blocker_type="parts_constraint",
                            source_id=p_row["source_id"],
                            aircraft_id=p_row["aircraft_id"],
                            title=f"{p_row['part_number']} - {p_row['description']}",
                            status=p_row["status"],
                            relevant_dates={
                                "needed_by": p_row["needed_by"],
                                "estimated_arrival": p_row["estimated_arrival"],
                            },
                            responsible_role_or_org=p_row["responsible_org"],
                        )
                    )

            elif dep["blocker_change_request_id"] is not None:
                db_id = dep["blocker_change_request_id"]
                _validate_id(db_id, CR_ID_PATTERN, "Change Request")
                cr_row = conn.execute(
                    "SELECT * FROM change_requests WHERE source_id = ?;", (db_id,)
                ).fetchone()
                if cr_row:
                    blockers.append(
                        BlockerRecord(
                            blocker_type="change_request",
                            source_id=cr_row["source_id"],
                            aircraft_id=cr_row["aircraft_id"],
                            title=cr_row["title"],
                            status=cr_row["status"],
                            relevant_dates={
                                "submitted_at": cr_row["submitted_at"],
                                "approved_at": cr_row["approved_at"],
                            },
                            responsible_role_or_org=cr_row["responsible_role"],
                        )
                    )

            elif dep["blocker_maintenance_task_id"] is not None:
                db_id = dep["blocker_maintenance_task_id"]
                _validate_id(db_id, MNT_ID_PATTERN, "Maintenance Task")
                m_row = conn.execute(
                    "SELECT * FROM maintenance_tasks WHERE source_id = ?;", (db_id,)
                ).fetchone()
                if m_row:
                    blockers.append(
                        BlockerRecord(
                            blocker_type="maintenance_task",
                            source_id=m_row["source_id"],
                            aircraft_id=m_row["aircraft_id"],
                            title=m_row["title"],
                            status=m_row["status"],
                            relevant_dates={
                                "due_date": m_row["due_date"],
                                "completed_at": m_row["completed_at"],
                            },
                            responsible_role_or_org=m_row["responsible_role"],
                        )
                    )

    return blockers
