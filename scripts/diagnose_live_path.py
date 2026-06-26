"""Sanitized diagnostics for the AeroOps live execution boundary.

The script never prints credential values, MCP response bodies, prompts, or
local environment mappings.  By default it checks configuration, database
availability, and the two preflight MCP tools.  Pass ``--run-live`` to execute
the complete live agent workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from aeroops.config import configure_live_model_credentials, get_settings
from aeroops.services import (
    LiveInvestigationError,
    _call_preflight_tool_via_mcp,
    run_investigation_async,
)


def _safe_api_error_fields(exc: Exception) -> tuple[int | None, str | None]:
    """Extract provider classification without exposing response bodies."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    return (
        code if isinstance(code, int) else None,
        status if isinstance(status, str) else None,
    )


async def _probe_model() -> tuple[bool, str]:
    """Issue one minimal Gemini request using the configured model and retry policy."""
    from google import genai
    from google.genai import types

    settings = get_settings()
    with configure_live_model_credentials(settings):
        client = genai.Client(
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=settings.model_request_timeout_ms,
                retry_options=types.HttpRetryOptions(
                    attempts=settings.model_retry_attempts,
                    initial_delay=settings.model_retry_initial_delay_seconds,
                    max_delay=settings.model_retry_max_delay_seconds,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )
        try:
            response = await client.aio.models.generate_content(
                model=settings.model,
                contents="Reply with exactly OK.",
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=8,
                ),
            )
            if not (response.text or "").strip():
                return False, "EMPTY_RESPONSE"
            return True, "PASS"
        except Exception as exc:
            code, status = _safe_api_error_fields(exc)
            return False, f"FAILED:{type(exc).__name__}:code={code}:status={status}"
        finally:
            await client.aio.aclose()
            client.close()


async def _diagnose(aircraft_id: str, run_live: bool) -> int:
    settings = get_settings()
    db_path = Path(settings.db_path).resolve()

    print(f"offline_demo={settings.offline_demo}")
    print(f"model={settings.model}")
    print(f"db_path_exists={db_path.is_file()}")
    print(f"live_key_configured={bool(settings.google_api_key)}")
    print(f"mcp_timeout_seconds={settings.mcp_timeout_seconds}")
    print(f"max_model_calls={settings.max_model_calls}")
    print(f"max_tool_calls={settings.max_tool_calls}")
    print(f"model_request_timeout_ms={settings.model_request_timeout_ms}")
    print(f"model_retry_attempts={settings.model_retry_attempts}")

    if settings.offline_demo:
        print("preflight=SKIPPED_OFFLINE_MODE")
        return 0
    if not db_path.is_file():
        print("preflight=FAILED_DATABASE_MISSING")
        return 2

    try:
        await _call_preflight_tool_via_mcp(
            "get_aircraft_status",
            {"aircraft_id": aircraft_id},
            str(db_path),
        )
        await _call_preflight_tool_via_mcp(
            "get_milestones",
            {"aircraft_id": aircraft_id},
            str(db_path),
        )
    except Exception as exc:  # sanitized type only
        print(f"preflight=FAILED:{type(exc).__name__}")
        return 3

    print("preflight=PASS")

    if not run_live:
        print("live_investigation=NOT_REQUESTED")
        return 0
    if not settings.google_api_key:
        print("live_investigation=SKIPPED_NO_KEY")
        return 4

    probe_ok, probe_result = await _probe_model()
    print(f"model_probe={probe_result}")
    if not probe_ok:
        if settings.model.endswith("-latest"):
            print("model_recommendation=SET_AEROOPS_MODEL=gemini-2.5-flash")
        return 5

    try:
        response = await run_investigation_async(
            f"Why is {aircraft_id} delayed? Produce an executive brief.",
            db_path=db_path,
        )
    except LiveInvestigationError as exc:
        print(
            "live_investigation=FAILED:"
            f"{exc.stage}:{exc.cause_type}:"
            f"code={exc.provider_code}:status={exc.provider_status}:"
            f"agent={exc.agent_name}"
        )
        if exc.validation_issues:
            print("validation_issues=" + ",".join(exc.validation_issues))
        return 6
    except Exception as exc:
        print(f"live_investigation=FAILED:unclassified:{type(exc).__name__}")
        return 7

    print(f"live_investigation=PASS:{response.aircraft_id}:{response.delay_days}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aircraft", default="AC-009")
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument(
        "--model",
        help="Temporarily override AEROOPS_MODEL for this diagnostic run.",
    )
    args = parser.parse_args()
    if args.model:
        os.environ["AEROOPS_MODEL"] = args.model
        get_settings.cache_clear()
    return asyncio.run(_diagnose(args.aircraft, args.run_live))


if __name__ == "__main__":
    raise SystemExit(main())
