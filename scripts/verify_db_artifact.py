#!/usr/bin/env python3
"""Regenerate and semantically verify the committed AeroOps database artifact."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DB = PROJECT_ROOT / "data" / "aeroops.db"
REGENERATED_DB = PROJECT_ROOT / "data" / "aeroops-regenerated.db"
TABLES = (
    "aircraft",
    "milestones",
    "defects",
    "test_events",
    "maintenance_tasks",
    "parts_constraints",
    "change_requests",
    "schedule_dependencies",
)


def _rows_by_table(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY source_id")]
            for table in TABLES
        }


def _assert_synthetic(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    for table, rows in snapshot.items():
        for row in rows:
            if "synthetic_data" in row and row["synthetic_data"] != 1:
                raise AssertionError(f"Non-synthetic row in {table}: {row['source_id']}")


def _verify_story(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    aircraft_ids = {row["source_id"] for row in snapshot["aircraft"]}
    expected_aircraft = {"AC-007", "AC-008", "AC-009", "AC-010"}
    if aircraft_ids != expected_aircraft:
        raise AssertionError(f"Unexpected aircraft IDs: {sorted(aircraft_ids)}")

    milestone = next(row for row in snapshot["milestones"] if row["source_id"] == "MS-009-FTC")
    delay_days = (
        date.fromisoformat(milestone["forecast_date"])
        - date.fromisoformat(milestone["planned_date"])
    ).days
    if delay_days != 6:
        raise AssertionError(f"AC-009 delay is {delay_days}, expected 6")

    dependency_ids = {
        row["source_id"]
        for row in snapshot["schedule_dependencies"]
        if row["aircraft_id"] == "AC-009"
    }
    expected_dependencies = {
        "DEP-009-001",
        "DEP-009-002",
        "DEP-009-003",
        "DEP-009-004",
    }
    if dependency_ids != expected_dependencies:
        raise AssertionError(f"Unexpected AC-009 dependencies: {sorted(dependency_ids)}")

    _assert_synthetic(snapshot)


def _verify_schema_has_no_sensitive_domains(path: Path) -> None:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    forbidden = {"users", "user", "companies", "company", "passwords", "secrets", "credentials"}
    overlap = {name.lower() for name in tables} & forbidden
    if overlap:
        raise AssertionError(f"Forbidden sensitive-domain tables: {sorted(overlap)}")


def _verify_read_only(path: Path) -> None:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        try:
            conn.execute("UPDATE aircraft SET status='green' WHERE source_id='AC-009'")
        except sqlite3.OperationalError:
            return
    raise AssertionError("Write unexpectedly succeeded through a read-only connection")


def _git_tracking_status() -> str:
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0:
        return "PENDING (archive has no .git metadata)"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "data/aeroops.db"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise AssertionError("data/aeroops.db exists but is not tracked by Git")
    return "TRACKED"


def main() -> None:
    REGENERATED_DB.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "aeroops.db.init_db",
        "--reset",
        "--db-path",
        str(REGENERATED_DB),
    ]
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        committed = _rows_by_table(COMMITTED_DB)
        regenerated = _rows_by_table(REGENERATED_DB)
        if committed != regenerated:
            summary = {
                table: {
                    "committed": len(committed[table]),
                    "regenerated": len(regenerated[table]),
                }
                for table in TABLES
            }
            raise AssertionError(
                "Committed and regenerated databases differ semantically: "
                + json.dumps(summary, sort_keys=True)
            )

        _verify_story(committed)
        _verify_schema_has_no_sensitive_domains(COMMITTED_DB)
        _verify_read_only(COMMITTED_DB)
        git_status = _git_tracking_status()

        print("Database regeneration: PASS")
        print("Semantic comparison: PASS")
        print("Aircraft set: AC-007, AC-008, AC-009, AC-010")
        print("AC-009 milestone variance: 6 days")
        print("AC-009 dependencies: DEP-009-001 through DEP-009-004")
        print("Synthetic-data and sensitive-domain checks: PASS")
        print("Read-only connection check: PASS")
        print(f"Git tracking status: {git_status}")
    finally:
        REGENERATED_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
