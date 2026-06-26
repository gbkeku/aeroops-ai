"""SQLite database DDL schema and index definitions for AeroOps.

This module provides functions to create and drop database tables, ensuring
strict CHECK constraints, indexes, and foreign keys.
"""

from __future__ import annotations

import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist."""
    # Ensure foreign keys are enabled on the connection
    conn.execute("PRAGMA foreign_keys = ON;")

    # 1. Aircraft Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aircraft (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^AC-\\d{3}$'),
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('green', 'amber', 'red')),
            responsible_org TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1)
        );
    """)

    # 2. Milestones Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^MS-\\d{3}-[A-Z0-9-]+$'),
            aircraft_id TEXT NOT NULL,
            name TEXT NOT NULL,
            planned_date TEXT NOT NULL,
            forecast_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('complete', 'on_track', 'at_risk', 'delayed')),
            responsible_role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_milestones_aircraft ON milestones (aircraft_id);")

    # 3. Defects Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS defects (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^DEF-\\d{3}-\\d{3}$'),
            aircraft_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
            status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'closed')),
            discovered_at TEXT NOT NULL,
            closed_at TEXT,
            responsible_role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_defects_aircraft ON defects (aircraft_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_defects_status ON defects (status);")

    # 4. Test Events Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_events (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^TEST-\\d{3}-\\d{3}$'),
            aircraft_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('planned', 'blocked', 'in_progress', 'completed', 'aborted')
            ),
            responsible_role TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_test_events_aircraft ON test_events (aircraft_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_test_events_status ON test_events (status);")

    # 5. Maintenance Tasks Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_tasks (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^MNT-\\d{3}-\\d{3}$'),
            aircraft_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('scheduled', 'in_progress', 'completed', 'deferred')
            ),
            responsible_role TEXT NOT NULL,
            due_date TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_aircraft "
        "ON maintenance_tasks (aircraft_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_status ON maintenance_tasks (status);"
    )

    # 6. Parts Constraints Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parts_constraints (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^PART-[A-Z0-9-]+$'),
            aircraft_id TEXT NOT NULL,
            part_number TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('awaiting_delivery', 'delivered', 'delayed')),
            responsible_org TEXT NOT NULL,
            needed_by TEXT NOT NULL,
            estimated_arrival TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parts_constraints_aircraft "
        "ON parts_constraints (aircraft_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parts_constraints_status ON parts_constraints (status);"
    )

    # 7. Change Requests Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS change_requests (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^CR-\\d{3}$'),
            aircraft_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('pending_review', 'approved', 'rejected', 'implemented')
            ),
            responsible_role TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            approved_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_change_requests_aircraft ON change_requests (aircraft_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests (status);"
    )

    # 8. Schedule Dependencies Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_dependencies (
            source_id TEXT PRIMARY KEY CHECK(source_id REGEXP '^DEP-\\d{3}-\\d{3}$'),
            aircraft_id TEXT NOT NULL,
            blocked_test_id TEXT NOT NULL,
            blocker_defect_id TEXT,
            blocker_parts_constraint_id TEXT,
            blocker_change_request_id TEXT,
            blocker_maintenance_task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            synthetic_data INTEGER NOT NULL CHECK(synthetic_data = 1),
            FOREIGN KEY (aircraft_id) REFERENCES aircraft (source_id) ON DELETE RESTRICT,
            FOREIGN KEY (blocked_test_id) REFERENCES test_events (source_id) ON DELETE CASCADE,
            FOREIGN KEY (blocker_defect_id) REFERENCES defects (source_id) ON DELETE CASCADE,
            FOREIGN KEY (blocker_parts_constraint_id)
                REFERENCES parts_constraints (source_id) ON DELETE CASCADE,
            FOREIGN KEY (blocker_change_request_id)
                REFERENCES change_requests (source_id) ON DELETE CASCADE,
            FOREIGN KEY (blocker_maintenance_task_id)
                REFERENCES maintenance_tasks (source_id) ON DELETE CASCADE,
            CHECK (
                (blocker_defect_id IS NOT NULL) +
                (blocker_parts_constraint_id IS NOT NULL) +
                (blocker_change_request_id IS NOT NULL) +
                (blocker_maintenance_task_id IS NOT NULL) = 1
            )
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_aircraft "
        "ON schedule_dependencies (aircraft_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_blocked_test "
        "ON schedule_dependencies (blocked_test_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_defect "
        "ON schedule_dependencies (blocker_defect_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_part "
        "ON schedule_dependencies (blocker_parts_constraint_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_cr "
        "ON schedule_dependencies (blocker_change_request_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_dependencies_maintenance "
        "ON schedule_dependencies (blocker_maintenance_task_id);"
    )


def drop_tables(conn: sqlite3.Connection) -> None:
    """Drop all tables in safe child-to-parent order."""
    # Temporarily disable foreign keys or drop in correct dependency order
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("DROP TABLE IF EXISTS schedule_dependencies;")
    conn.execute("DROP TABLE IF EXISTS milestones;")
    conn.execute("DROP TABLE IF EXISTS defects;")
    conn.execute("DROP TABLE IF EXISTS test_events;")
    conn.execute("DROP TABLE IF EXISTS maintenance_tasks;")
    conn.execute("DROP TABLE IF EXISTS parts_constraints;")
    conn.execute("DROP TABLE IF EXISTS change_requests;")
    conn.execute("DROP TABLE IF EXISTS aircraft;")
    conn.execute("PRAGMA foreign_keys = ON;")
