"""Minimal health-check for the AeroOps package."""

from __future__ import annotations

from aeroops import __version__
from aeroops.config import get_settings
from aeroops.models import HealthStatus


def check_health() -> HealthStatus:
    """Return a health-status snapshot confirming the package is operational.

    This function verifies that:
    * The package is importable.
    * Settings can be loaded without error.
    * The configured model name is available.
    """
    settings = get_settings()
    return HealthStatus(
        status="ok",
        version=__version__,
        model=settings.model,
    )
