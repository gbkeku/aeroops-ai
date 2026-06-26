"""AeroOps application settings.

Settings are loaded lazily via ``get_settings()`` so that module import never
triggers validation.  Environment variables are read with the ``AEROOPS_``
prefix (e.g. ``AEROOPS_MODEL``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AeroOpsSettings(BaseSettings):
    """Central configuration for the AeroOps application."""

    def __init__(self, *args, **kwargs):
        if "PYTEST_CURRENT_TEST" in os.environ and "_env_file" not in kwargs:
            kwargs["_env_file"] = None

        # Streamlit secrets compatibility
        try:
            import streamlit as st

            if st.runtime.exists():
                for key in st.secrets:
                    if key.startswith("AEROOPS_") or key == "GOOGLE_API_KEY":
                        field_name = (
                            key[8:].lower() if key.startswith("AEROOPS_") else "google_api_key"
                        )
                        if (
                            field_name in self.model_fields
                            and field_name not in kwargs
                            and os.getenv(key) is None
                        ):
                            kwargs[field_name] = st.secrets[key]
        except Exception:
            pass

        super().__init__(*args, **kwargs)

    model_config = SettingsConfigDict(
        env_prefix="AEROOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Pin the production default to a stable model.  The ``*-latest`` aliases
    # can be hot-swapped to preview or experimental releases and are therefore
    # better suited to exploration than a public deployment.
    model: str = "gemini-2.5-flash"

    # Path to the synthetic SQLite database.
    db_path: Path = Path("data/aeroops.db")

    # Offline demo flag.
    offline_demo: bool = False

    # Google API Key for live Gemini model access.
    google_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "AEROOPS_GOOGLE_API_KEY"),
    )

    # Specialist MCP subprocesses can take longer to initialize on a cold cloud
    # worker than on a developer laptop.  ADK's bare stdio parameter path uses a
    # five-second default, so AeroOps exposes an explicit, bounded timeout.
    mcp_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    # A live Gemini model may call specialist tools sequentially rather than in
    # one parallel function-call response.  The old limit of 10 was exactly the
    # deterministic batch-tool minimum and could abort a valid live run.
    max_model_calls: int = Field(default=24, ge=10, le=100)
    max_tool_calls: int = Field(default=24, ge=10, le=100)

    # Gemini request resilience.  The Google Gen AI SDK does not retry unless
    # retry options are supplied.  These values are intentionally bounded so
    # a provider incident cannot keep an AeroOps request alive indefinitely.
    model_request_timeout_ms: int = Field(default=120_000, ge=10_000, le=600_000)
    model_retry_attempts: int = Field(default=4, ge=1, le=8)
    model_retry_initial_delay_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    model_retry_max_delay_seconds: float = Field(default=8.0, ge=1.0, le=120.0)

    # Enables sanitized stage diagnostics only; credentials and payloads are
    # never logged.
    debug: bool = False

    @field_validator("offline_demo", mode="before")
    @classmethod
    def parse_offline_demo(cls, v: Any) -> bool:
        """Parse common Boolean values safely."""
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            if v == 1:
                return True
            elif v == 0:
                return False
            raise ValueError(f"Invalid boolean value: {v}")
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("1", "true", "yes", "on"):
                return True
            elif val in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"Invalid boolean value: {v}")
        raise ValueError(f"Invalid boolean type: {type(v)}")


@lru_cache(maxsize=1)
def get_settings() -> AeroOpsSettings:
    """Return the singleton application settings instance.

    Settings are constructed on first call and cached thereafter.
    """
    return AeroOpsSettings()


@contextmanager
def configure_live_model_credentials(settings: AeroOpsSettings) -> Iterator[None]:
    """Temporarily expose a configured Gemini key at the live model boundary.

    The settings model itself never mutates process-global environment state.
    This context manager changes ``GOOGLE_API_KEY`` only for the lifetime of a
    live investigation and restores the prior value even when execution fails.
    Offline and credential-free deterministic runs are no-ops.
    """
    if settings.offline_demo or not settings.google_api_key:
        yield
        return

    sentinel = object()
    previous_key: str | object = os.environ.get("GOOGLE_API_KEY", sentinel)
    previous_vertex: str | object = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", sentinel)

    # ADK's Google AI Studio path reads GOOGLE_API_KEY and expects Vertex AI to
    # be explicitly disabled.  Both values are restored after the bounded run.
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
    try:
        yield
    finally:
        if previous_key is sentinel:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = str(previous_key)

        if previous_vertex is sentinel:
            os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
        else:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = str(previous_vertex)
