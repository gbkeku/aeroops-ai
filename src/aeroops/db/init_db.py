"""CLI helper script to initialize, drop/reset, and seed the SQLite database.

Usage:
    python -m aeroops.db.init_db [--reset] [--db-path PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aeroops.db import get_db_connection
from aeroops.db.schema import create_tables, drop_tables
from aeroops.db.seed import seed_all


def main() -> None:
    """CLI Entrypoint for database initialization."""
    parser = argparse.ArgumentParser(description="Initialize and seed AeroOps synthetic database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables before creating and seeding.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Custom path to the SQLite database file.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    try:
        with get_db_connection(db_path=db_path) as conn:
            if args.reset:
                print("Resetting database (dropping existing tables)...")
                drop_tables(conn)

            print("Creating tables and indexes...")
            create_tables(conn)

            print("Seeding synthetic data...")
            seed_all(conn)

            print("Database initialized and seeded successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
