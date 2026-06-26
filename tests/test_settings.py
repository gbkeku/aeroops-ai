"""Unit tests for the centralized settings layer and offline mode isolation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aeroops.config import AeroOpsSettings, get_settings


def test_settings_default_live(monkeypatch) -> None:
    """Prove that live mode is the default when the variable is absent."""
    # Clear environment variables
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    # Force AeroOpsSettings to ignore local .env file for testing defaults
    settings = AeroOpsSettings(_env_file=None)
    assert settings.offline_demo is False
    assert settings.google_api_key is None


def test_settings_env_offline(monkeypatch) -> None:
    """Prove that environment variables activate offline mode with various valid boolean values."""
    # 1. yes / true / 1 / on
    for val in ("yes", "true", "1", "on"):
        monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", val)
        settings = AeroOpsSettings(_env_file=None)
        assert settings.offline_demo is True

    # 2. no / false / 0 / off
    for val in ("no", "false", "0", "off"):
        monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", val)
        settings = AeroOpsSettings(_env_file=None)
        assert settings.offline_demo is False


def test_settings_loads_explicit_env_file(tmp_path, monkeypatch) -> None:
    """Prove that a project-style .env file activates offline mode."""
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AEROOPS_OFFLINE_DEMO=1\n"
        "AEROOPS_MODEL=gemini-2.5-flash\n"
        "AEROOPS_DB_PATH=data/aeroops.db\n",
        encoding="utf-8",
    )

    settings = AeroOpsSettings(_env_file=env_file)

    assert settings.offline_demo is True
    assert settings.model == "gemini-2.5-flash"
    assert str(settings.db_path) == "data/aeroops.db"
    assert settings.google_api_key is None


def test_settings_invalid_bool(monkeypatch) -> None:
    """Prove that invalid boolean values for AEROOPS_OFFLINE_DEMO fail clearly with ValidationError."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "invalid_value")

    with pytest.raises(ValidationError):
        AeroOpsSettings(_env_file=None)


def test_offline_mode_aborts_live_execution(monkeypatch) -> None:
    """Prove that in offline mode, any attempt to run the live investigation pipeline is blocked immediately."""
    get_settings.cache_clear()
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    from aeroops.services import run_investigation_async

    with pytest.raises(RuntimeError, match="Cannot run live investigation in offline mode"):
        import asyncio

        asyncio.run(run_investigation_async(query="Why is AC-009 delayed?", db_path=None))


def test_live_errors_never_fallback_to_offline_fixtures(monkeypatch) -> None:
    """Verify that in live mode, failures do not silently fall back to offline fixtures."""
    get_settings.cache_clear()
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "0")  # Live mode

    from aeroops.ui_controller import get_fleet_dashboard_snapshot

    # Point to a non-existent database file so that it fails to connect
    non_existent_db = "data/non_existent_file_xyz.db"

    # We expect get_fleet_dashboard_snapshot to raise an exception, NOT return the mock fixtures
    with pytest.raises(Exception) as exc_info:
        get_fleet_dashboard_snapshot(db_path_override=non_existent_db)

    # Verify that the exception is related to the database/connection failure and not a silent fallback
    assert "MOCK_FLEET_SNAPSHOT" not in str(exc_info.value)


def test_constructing_settings_does_not_change_environ(monkeypatch) -> None:
    """Prove that constructing AeroOpsSettings does not change os.environ."""
    import os

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # Construct settings with a key
    settings = AeroOpsSettings(_env_file=None, google_api_key="MY_SECRET_KEY")
    # Verify os.environ did not receive it
    assert "GOOGLE_API_KEY" not in os.environ
    assert settings.google_api_key == "MY_SECRET_KEY"


def test_offline_mode_never_invokes_credential_configuration(monkeypatch) -> None:
    """Prove that offline mode never invokes credential configuration."""
    import os

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = AeroOpsSettings(_env_file=None, offline_demo=True, google_api_key="MY_SECRET_KEY")

    from aeroops.config import configure_live_model_credentials

    with configure_live_model_credentials(settings):
        assert "GOOGLE_API_KEY" not in os.environ
    assert "GOOGLE_API_KEY" not in os.environ


def test_live_mode_configures_runtime_only_at_live_service_boundary(monkeypatch) -> None:
    """Prove that live mode configures the required runtime only at the live service boundary."""
    import os

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = AeroOpsSettings(
        _env_file=None, offline_demo=False, google_api_key="BOUNDARY_TEST_KEY"
    )

    from aeroops.config import configure_live_model_credentials

    with configure_live_model_credentials(settings):
        assert os.environ.get("GOOGLE_API_KEY") == "BOUNDARY_TEST_KEY"

    assert "GOOGLE_API_KEY" not in os.environ


def test_live_credential_context_restores_existing_value(monkeypatch) -> None:
    """A live run must restore a key that existed before the boundary."""
    import os

    monkeypatch.setenv("GOOGLE_API_KEY", "ORIGINAL_KEY")
    settings = AeroOpsSettings(
        _env_file=None,
        offline_demo=False,
        google_api_key="TEMPORARY_KEY",
    )

    from aeroops.config import configure_live_model_credentials

    with configure_live_model_credentials(settings):
        assert os.environ["GOOGLE_API_KEY"] == "TEMPORARY_KEY"

    assert os.environ["GOOGLE_API_KEY"] == "ORIGINAL_KEY"


def test_credentials_are_not_included_in_errors_or_audit_logs(monkeypatch, caplog) -> None:
    """Prove that credentials are not included in errors or audit logs."""
    import logging

    logger = logging.getLogger("aeroops.config")

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = AeroOpsSettings(
        _env_file=None, offline_demo=False, google_api_key="SUPER_SECRET_KEY_12345"
    )

    # Trigger configuration and log something
    from aeroops.config import configure_live_model_credentials

    with caplog.at_level(logging.DEBUG):
        logger.info("Configuring live credentials...")
        with configure_live_model_credentials(settings):
            logger.info("Credentials configured successfully.")

    # Assert secret key is NOT in logs
    for record in caplog.records:
        assert "SUPER_SECRET_KEY_12345" not in record.message


@pytest.mark.asyncio
async def test_live_service_boundary_exposes_and_restores_key(monkeypatch) -> None:
    """The key exists during Runner construction and is restored on failure."""
    import os

    from aeroops import services

    class BoundaryObserved(RuntimeError):
        pass

    async def fake_milestone(*_args, **_kwargs):
        return {
            "planned_milestone_date": "2026-06-29",
            "forecast_milestone_date": "2026-07-05",
            "delay_days": 6,
            "milestone_source_id": "MS-009-FTC",
            "aircraft_record": {"source_id": "AC-009"},
            "milestone_record": {"source_id": "MS-009-FTC"},
        }

    def fake_pipeline(**_kwargs):
        return object()

    class FakeRunner:
        def __init__(self, **_kwargs):
            assert os.environ.get("GOOGLE_API_KEY") == "BOUNDARY_ONLY_KEY"
            raise BoundaryObserved("runner boundary reached")

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "BOUNDARY_ONLY_KEY")
    get_settings.cache_clear()
    # Remove the source variable after settings are cached so restoration can be observed.
    settings = get_settings()
    assert settings.google_api_key == "BOUNDARY_ONLY_KEY"
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    monkeypatch.setattr(services, "_resolve_milestone_via_mcp", fake_milestone)
    monkeypatch.setattr(services, "create_pipeline", fake_pipeline)
    monkeypatch.setattr(services, "Runner", FakeRunner)

    with pytest.raises(BoundaryObserved, match="runner boundary reached"):
        await services.run_investigation_async("Why is AC-009 delayed?")

    assert "GOOGLE_API_KEY" not in os.environ
