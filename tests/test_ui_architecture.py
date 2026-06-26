"""Architecture test for AeroOps UI.

Enforces that UI modules do not import sqlite3, aeroops.db, or repository layers.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_ui_no_direct_db_imports() -> None:
    """Statically verify that no UI files import sqlite3, aeroops.db, or repository."""
    ui_files = [
        Path("src/aeroops/app.py"),
        Path("src/aeroops/ui_controller.py"),
        Path("src/aeroops/ui_components.py"),
        Path("src/aeroops/ui_models.py"),
        Path("src/aeroops/offline_fixtures.py"),
        Path("src/aeroops/mcp_client.py"),
    ]

    for f in ui_files:
        assert f.exists(), f"UI file {f} does not exist."
        content = f.read_text(encoding="utf-8")
        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    assert "sqlite3" not in name.name, f"Forbidden import of sqlite3 in {f}"
                    assert "aeroops.db" not in name.name, f"Forbidden import of aeroops.db in {f}"
                    assert "repository" not in name.name, f"Forbidden import of repository in {f}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "sqlite3" not in node.module, f"Forbidden import from sqlite3 in {f}"
                assert "aeroops.db" not in node.module, f"Forbidden import from aeroops.db in {f}"
                assert "repository" not in node.module, f"Forbidden import from repository in {f}"
