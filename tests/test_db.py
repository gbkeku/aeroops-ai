"""Integration and database logic tests for the AeroOps synthetic data layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aeroops.db import get_db_connection
from aeroops.db.repository import (
    get_aircraft,
    get_blockers_for_test,
    get_milestones,
    get_schedule_dependencies,
    get_test_events,
)
from aeroops.db.schema import create_tables, drop_tables
from aeroops.db.seed import seed_all


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Fixture to provide a clean temporary database path for each test."""
    db_file = tmp_path / "aeroops_test.db"
    return db_file


def test_schema_creation_and_reset(temp_db_path: Path) -> None:
    """Test that schema creation and reset work in child-to-parent order."""
    with get_db_connection(db_path=temp_db_path) as conn:
        # Create tables
        create_tables(conn)

        # Verify tables exist
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]
        assert "aircraft" in tables
        assert "schedule_dependencies" in tables

        # Drop tables (should drop without FK errors)
        drop_tables(conn)

        # Verify tables are dropped
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables_after = [row["name"] for row in cursor.fetchall()]
        assert not tables_after


def test_idempotent_seeding(temp_db_path: Path) -> None:
    """Test that seeding is idempotent and does not duplicate or delete records."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)

        # Seed first time
        seed_all(conn)

        # Verify initial counts
        ac_count_1 = conn.execute("SELECT COUNT(*) as c FROM aircraft;").fetchone()["c"]
        dep_count_1 = conn.execute("SELECT COUNT(*) as c FROM schedule_dependencies;").fetchone()[
            "c"
        ]
        assert ac_count_1 == 4

        # Seed second time
        seed_all(conn)

        # Verify counts are unchanged
        ac_count_2 = conn.execute("SELECT COUNT(*) as c FROM aircraft;").fetchone()["c"]
        dep_count_2 = conn.execute("SELECT COUNT(*) as c FROM schedule_dependencies;").fetchone()[
            "c"
        ]
        assert ac_count_1 == ac_count_2
        assert dep_count_1 == dep_count_2


def test_database_check_constraints(temp_db_path: Path) -> None:
    """Test database-level CHECK constraints."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)
        seed_all(conn)

        # 1. Invalid status for Aircraft
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO aircraft (
                    source_id, name, status, responsible_org,
                    created_at, updated_at, synthetic_data
                )
                VALUES (
                    'AC-099', 'Invalid Status Plane', 'blue', 'Org',
                    '2026-06-24T00:00:00Z', '2026-06-24T00:00:00Z', 1
                );
                """
            )

        # 2. Non-synthetic data (synthetic_data must be 1)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO aircraft (
                    source_id, name, status, responsible_org,
                    created_at, updated_at, synthetic_data
                )
                VALUES (
                    'AC-099', 'Non Synthetic Plane', 'green', 'Org',
                    '2026-06-24T00:00:00Z', '2026-06-24T00:00:00Z', 0
                );
                """
            )

        # 3. Exactly one blocker constraint on schedule_dependencies
        # Case A: Zero blockers
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO schedule_dependencies (
                    source_id, aircraft_id, blocked_test_id,
                    created_at, updated_at, synthetic_data
                )
                VALUES (
                    'DEP-009-999', 'AC-009', 'TEST-009-121',
                    '2026-06-24T00:00:00Z', '2026-06-24T00:00:00Z', 1
                );
                """
            )

        # Case B: Two blockers (defect and part)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO schedule_dependencies (
                    source_id, aircraft_id, blocked_test_id, blocker_defect_id,
                    blocker_parts_constraint_id, created_at, updated_at,
                    synthetic_data
                )
                VALUES (
                    'DEP-009-999', 'AC-009', 'TEST-009-121', 'DEF-009-042',
                    'PART-ACT-774', '2026-06-24T00:00:00Z', '2026-06-24T00:00:00Z', 1
                );
                """
            )


def test_dependency_integrity(temp_db_path: Path) -> None:
    """Test dependency-specific rules (FKs, matched aircraft_id)."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)
        seed_all(conn)

        # Check: Every dependency belongs to the same aircraft as the blocked test
        cursor = conn.execute(
            """
            SELECT sd.source_id, sd.aircraft_id as dep_ac, te.aircraft_id as test_ac
            FROM schedule_dependencies sd
            JOIN test_events te ON sd.blocked_test_id = te.source_id;
            """
        )
        for row in cursor.fetchall():
            assert row["dep_ac"] == row["test_ac"], (
                f"Dependency {row['source_id']} aircraft mismatch"
            )


def test_repository_read_only(temp_db_path: Path) -> None:
    """Test that repository connection forces read-only behavior."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)
        seed_all(conn)

    # Now use repository/read-only connection to try writing
    with get_db_connection(db_path=temp_db_path, read_only=True) as ro_conn:
        with pytest.raises((sqlite3.OperationalError, sqlite3.DatabaseError)) as excinfo:
            ro_conn.execute("UPDATE aircraft SET name = 'New Name' WHERE source_id = 'AC-009';")
        assert "readonly" in str(excinfo.value) or "query_only" in str(excinfo.value)


def test_repository_validation_and_not_found(temp_db_path: Path) -> None:
    """Test repository functions raise on malformed input and return None on unknown."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)
        seed_all(conn)

    # 1. Malformed Aircraft ID
    with pytest.raises(ValueError):
        get_aircraft("AC-99", db_path=temp_db_path)

    # 2. Well-formed but unknown Aircraft ID
    assert get_aircraft("AC-999", db_path=temp_db_path) is None

    # 3. Malformed Test ID
    with pytest.raises(ValueError):
        get_schedule_dependencies("TEST-009", db_path=temp_db_path)


def test_ac009_narrative_from_db(temp_db_path: Path) -> None:
    """Verify the AC-009 narrative and blocker chain is correctly modeled in the DB."""
    with get_db_connection(db_path=temp_db_path) as conn:
        create_tables(conn)
        seed_all(conn)

    # 1. Planned milestone vs Forecast milestone
    milestones = get_milestones("AC-009", db_path=temp_db_path)
    ftc_ms = next(m for m in milestones if m.source_id == "MS-009-FTC")
    assert ftc_ms.planned_date.isoformat() == "2026-06-29"
    assert ftc_ms.forecast_date.isoformat() == "2026-07-05"
    assert ftc_ms.variance_days == 6  # 6-day delay calculated from DB values

    # 2. TEST-009-118 was aborted on 2026-06-23
    test_events = get_test_events("AC-009", db_path=temp_db_path)
    aborted_test = next(t for t in test_events if t.source_id == "TEST-009-118")
    assert aborted_test.status == "aborted"
    assert aborted_test.completed_at is not None
    assert aborted_test.completed_at.isoformat().startswith("2026-06-23")

    # 3. TEST-009-121 blockers
    blockers = get_blockers_for_test("TEST-009-121", db_path=temp_db_path)
    assert len(blockers) == 4

    blocker_types = {b.blocker_type for b in blockers}
    assert blocker_types == {"defect", "parts_constraint", "change_request", "maintenance_task"}

    # Defect blocker
    def_blocker = next(b for b in blockers if b.blocker_type == "defect")
    assert def_blocker.source_id == "DEF-009-042"
    assert def_blocker.status == "open"
    assert "actuator position mismatch" in def_blocker.title

    # Parts constraint blocker
    part_blocker = next(b for b in blockers if b.blocker_type == "parts_constraint")
    assert part_blocker.source_id == "PART-ACT-774"
    assert part_blocker.relevant_dates["needed_by"] == "2026-06-27"
    assert part_blocker.relevant_dates["estimated_arrival"] == "2026-06-30"

    # Change request blocker
    cr_blocker = next(b for b in blockers if b.blocker_type == "change_request")
    assert cr_blocker.source_id == "CR-184"
    assert cr_blocker.status == "pending_review"

    # Maintenance task blocker
    mnt_blocker = next(b for b in blockers if b.blocker_type == "maintenance_task")
    assert mnt_blocker.source_id == "MNT-009-015"
    assert mnt_blocker.relevant_dates["due_date"] == "2026-06-26"
