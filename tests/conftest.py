"""Pytest configuration and shared fixtures for AeroOps."""

from __future__ import annotations

import pytest

from aeroops.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch) -> None:
    """Automatically clear settings cache and isolate environment between tests.

    Deletes AEROOPS_OFFLINE_DEMO and GOOGLE_API_KEY by default so that local developer
    environment variables or previous tests do not leak.
    """
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
