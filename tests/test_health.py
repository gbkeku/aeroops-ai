"""Health-check and settings tests for AeroOps."""

from __future__ import annotations

from aeroops import __version__
from aeroops.config import AeroOpsSettings, get_settings
from aeroops.health import check_health


def test_health_check_returns_ok() -> None:
    """check_health() should return status 'ok' and the current package version."""
    result = check_health()
    assert result.status == "ok"
    assert result.version == __version__


def test_settings_defaults() -> None:
    """AeroOpsSettings should load with sensible defaults when no env vars are set."""
    settings = AeroOpsSettings()
    assert settings.model == "gemini-2.5-flash"
    assert str(settings.db_path).endswith("aeroops.db")


def test_get_settings_returns_same_instance() -> None:
    """get_settings() is cached — consecutive calls return the same object."""
    first = get_settings()
    second = get_settings()
    assert first is second
