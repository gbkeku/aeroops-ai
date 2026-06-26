"""Integration tests for the deterministic database bootstrap and seeded data.

Verifies that the synthetic database exists, is initialized correctly,
and can be regenerated, and that the MCP server operates in read-only mode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aeroops.db import get_db_connection
from aeroops.db.repository import (
    get_aircraft,
    get_milestones,
    get_schedule_dependencies,
)


def test_committed_db_exists() -> None:
    """Verify that data/aeroops.db is committed and is a valid file."""
    db_path = Path("data/aeroops.db")
    assert db_path.is_file(), "Committed database data/aeroops.db does not exist!"
    assert db_path.stat().st_size > 0, "Database file is empty!"


def test_db_can_be_regenerated(tmp_path: Path) -> None:
    """Verify database initialization script runs successfully to create and seed tables."""
    temp_db = tmp_path / "aeroops_regen.db"

    # Run the init_db main function directly or as script
    import sys

    from aeroops.db.init_db import main as init_main

    orig_argv = sys.argv
    try:
        sys.argv = ["aeroops-init-db", "--reset", "--db-path", str(temp_db)]
        init_main()
    finally:
        sys.argv = orig_argv

    assert temp_db.is_file(), "Regenerated database file was not created!"

    # Verify that we can read records from it
    with get_db_connection(temp_db) as conn:
        cursor = conn.execute("SELECT COUNT(*) as c FROM aircraft;")
        assert cursor.fetchone()["c"] == 4


def test_mcp_server_opens_db_read_only() -> None:
    """Verify that get_db_connection enforces read-only mode when requested."""
    db_path = Path("data/aeroops.db")

    with (
        get_db_connection(db_path, read_only=True) as conn,
        pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"),
    ):
        conn.execute("UPDATE aircraft SET name = 'Broken' WHERE source_id = 'AC-009';")


def test_ac009_seeded_records_and_delay() -> None:
    """Verify AC-009 delay calculations and key blocker records exist in the database."""
    db_path = Path("data/aeroops.db")

    # 1. Verify Aircraft exists
    ac = get_aircraft("AC-009", db_path=db_path)
    assert ac is not None
    assert ac.status == "red"

    # 2. Verify Milestone delay is exactly 6 days
    milestones = get_milestones("AC-009", db_path=db_path)
    ms_ftc = next((m for m in milestones if m.source_id == "MS-009-FTC"), None)
    assert ms_ftc is not None

    from datetime import date

    planned = (
        ms_ftc.planned_date
        if isinstance(ms_ftc.planned_date, date)
        else date.fromisoformat(ms_ftc.planned_date)
    )
    forecast = (
        ms_ftc.forecast_date
        if isinstance(ms_ftc.forecast_date, date)
        else date.fromisoformat(ms_ftc.forecast_date)
    )
    delay_days = (forecast - planned).days
    assert delay_days == 6, f"AC-009 delay should be 6 days, got {delay_days}"

    # 3. Verify dependency records and four blocker types are present for TEST-009-121
    deps = get_schedule_dependencies("TEST-009-121", db_path=db_path)
    assert len(deps) == 4, "TEST-009-121 must have exactly 4 dependency blocker records"

    blockers = {
        "defect": False,
        "parts_constraint": False,
        "change_request": False,
        "maintenance_task": False,
    }
    for dep in deps:
        if dep.blocker_defect_id == "DEF-009-042":
            blockers["defect"] = True
        elif dep.blocker_parts_constraint_id == "PART-ACT-774":
            blockers["parts_constraint"] = True
        elif dep.blocker_change_request_id == "CR-184":
            blockers["change_request"] = True
        elif dep.blocker_maintenance_task_id == "MNT-009-015":
            blockers["maintenance_task"] = True

    assert all(blockers.values()), f"Missing blocker dependency types: {blockers}"
