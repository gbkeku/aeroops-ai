"""AeroOps Database Package.

This package manages the schema, seeding, and repository operations for the
synthetic database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from aeroops.config import get_settings


@contextmanager
def get_db_connection(
    db_path: Path | str | None = None,
    read_only: bool = False,
) -> Generator[sqlite3.Connection, None, None]:
    """Provide a context-managed SQLite connection.

    Args:
        db_path: Optional path to the SQLite database file. Defaults to settings value.
        read_only: If True, sets PRAGMA query_only = ON to prevent modifications.

    Yields:
        A configured sqlite3.Connection.
    """
    if db_path is None:
        db_path = get_settings().db_path

    # Convert to Path and resolve absolute path
    path = Path(db_path).resolve()

    # Ensure parent directory exists for writable database
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)

    # If read-only, we can open with URI file:path?mode=ro
    # Note: URI mode requires uri=True.
    if read_only:
        # SQLite needs forward slashes in URI on Windows
        uri_path = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri_path, uri=True, timeout=5.0)
    else:
        conn = sqlite3.connect(str(path), timeout=5.0)

    try:
        conn.row_factory = sqlite3.Row

        # Register REGEXP function in SQLite
        import re

        def regexp(expr, item):
            if item is None:
                return False
            return re.search(expr, item) is not None

        conn.create_function("regexp", 2, regexp)

        # Enable foreign key constraint enforcement (connection-specific)
        conn.execute("PRAGMA foreign_keys = ON;")
        if read_only:
            # Additional safety layer to enforce read-only
            conn.execute("PRAGMA query_only = ON;")
        yield conn
    finally:
        conn.close()
