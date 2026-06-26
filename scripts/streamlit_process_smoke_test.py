#!/usr/bin/env python3
"""Verify real Streamlit startup and project-root .env offline behavior."""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "src" / "aeroops" / "app.py"
ENV_PATH = PROJECT_ROOT / ".env"
HEALTH_TIMEOUT_SECONDS = 30.0


def _fail(message: str, stdout: str = "", stderr: str = "") -> NoReturn:
    print(f"[ERROR] {message}", file=sys.stderr)
    if stdout:
        print("=== STREAMLIT STDOUT ===", file=sys.stderr)
        print(stdout, file=sys.stderr)
    if stderr:
        print("=== STREAMLIT STDERR ===", file=sys.stderr)
        print(stderr, file=sys.stderr)
    raise SystemExit(1)


def _wait_for_health(proc: subprocess.Popen[str], port: int) -> str:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Streamlit exited early with code {proc.returncode}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            connection.request("GET", "/_stcore/health")
            response = connection.getresponse()
            body = response.read().decode("utf-8").strip()
            connection.close()
            if response.status == 200 and body == "ok":
                return body
        except OSError:
            pass
        time.sleep(0.25)
    raise TimeoutError(
        f"Streamlit health endpoint did not become ready: http://127.0.0.1:{port}/_stcore/health"
    )


def _run_apptest_offline_assertions() -> None:
    """Execute the real app through AppTest while live boundaries are booby-trapped."""
    from aeroops.config import get_settings

    get_settings.cache_clear()
    import aeroops.ui_controller as controller

    original_mcp = controller.call_mcp_tool_direct
    original_investigation = controller.run_investigation_async

    async def forbidden_live_call(*_args, **_kwargs):
        raise AssertionError("Offline preview attempted to invoke a live boundary")

    controller.call_mcp_tool_direct = forbidden_live_call
    controller.run_investigation_async = forbidden_live_call
    try:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(APP_PATH))
        app.run(timeout=30)
        warnings = [item.value for item in app.warning]
        if not any("Offline Preview" in text for text in warnings):
            raise AssertionError("Offline Preview banner was not rendered from .env")

        app.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run(
            timeout=30
        )
        analyze = next(button for button in app.button if button.label == "🚀 Analyze")
        analyze.click().run(timeout=30)
        result = app.session_state["last_result"]
        if result is None or result.response.aircraft_id != "AC-009":
            raise AssertionError("Offline AC-009 fixture did not render")
        if result.response.delay_days != 6:
            raise AssertionError("Offline AC-009 delay must be six days")
    finally:
        controller.call_mcp_tool_direct = original_mcp
        controller.run_investigation_async = original_investigation
        get_settings.cache_clear()


def main() -> None:
    """Create a temporary .env, launch Streamlit, verify health, and clean up."""
    original_env_file = ENV_PATH.read_bytes() if ENV_PATH.exists() else None
    missing_db = PROJECT_ROOT / "data" / "offline-smoke-database-must-not-exist.db"
    missing_db.unlink(missing_ok=True)
    ENV_PATH.write_text(
        "AEROOPS_OFFLINE_DEMO=1\n"
        "AEROOPS_MODEL=gemini-2.5-flash\n"
        f"AEROOPS_DB_PATH={missing_db.as_posix()}\n",
        encoding="utf-8",
    )

    child_env = os.environ.copy()
    for key in (
        "AEROOPS_OFFLINE_DEMO",
        "AEROOPS_MODEL",
        "AEROOPS_DB_PATH",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GEMINI_API_KEY",
    ):
        child_env.pop(key, None)

    port = int(child_env.get("AEROOPS_STREAMLIT_SMOKE_PORT", "8501"))
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_PATH),
        "--server.headless=true",
        f"--server.port={port}",
        "--browser.gatherUsageStats=false",
    ]

    try:
        with (
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file,
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file,
        ):
            proc = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=child_env,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )
            health_body = ""
            failure: Exception | None = None
            try:
                health_body = _wait_for_health(proc, port)
            except Exception as exc:  # captured so logs can be printed after cleanup
                failure = exc
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)

            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
            if failure is not None:
                _fail(str(failure), stdout, stderr)
            if proc.poll() is None:
                _fail("Streamlit process did not terminate", stdout, stderr)

        _run_apptest_offline_assertions()
        if missing_db.exists():
            _fail("Offline verification unexpectedly created the configured database")

        print(f"Streamlit health endpoint: {health_url} -> {health_body!r}")
        print(".env offline mode: verified")
        print("Gemini/ADK/MCP live boundaries: not invoked")
        print("Configured missing database: not opened or created")
        print("Streamlit process termination: verified")
    finally:
        if original_env_file is None:
            ENV_PATH.unlink(missing_ok=True)
        else:
            ENV_PATH.write_bytes(original_env_file)
        missing_db.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
