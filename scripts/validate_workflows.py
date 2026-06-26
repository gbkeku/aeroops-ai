#!/usr/bin/env python3
"""Validate immutable and least-privilege GitHub Actions workflows."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@([0-9a-f]{40})\s+#\s+\S+", re.IGNORECASE)
ANY_ACTION = re.compile(r"^\s*-?\s*uses:\s*(\S+)")


def _errors_for(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    errors: list[str] = []

    if not re.search(r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$", content):
        errors.append("top-level permissions must be exactly contents: read")
    if re.search(r"(?m)^\s+[A-Za-z_-]+:\s*write\s*$", content):
        errors.append("write permissions are not permitted")

    for number, line in enumerate(lines, 1):
        if ANY_ACTION.match(line) and not PINNED_ACTION.match(line):
            errors.append(
                f"line {number}: action must use a 40-character commit SHA and tag comment"
            )
        command = line.strip().lower()
        if command in {"run: env", "run: printenv"} or command.startswith("run: env "):
            errors.append(f"line {number}: complete environment output is forbidden")

    if "|| echo" in content or "|| true" in content:
        errors.append("test failures must not be converted into successful steps")

    if "actions/checkout@" in content and not re.search(
        r"(?m)^\s+persist-credentials:\s*false\s*$", content
    ):
        errors.append("checkout must disable persisted Git credentials")

    if path.name == "ci.yml":
        if "pull_request:" not in content or "push:" not in content:
            errors.append("normal CI must run on push and pull_request")
        if "secrets." in content or "GOOGLE_API_KEY" in content:
            errors.append("normal CI must not reference live credentials")
        init_lines = [line for line in lines if "aeroops-init-db" in line]
        if not init_lines or not all("runner.temp" in line for line in init_lines):
            errors.append("normal CI databases must be created under runner.temp")
        if "data/aeroops.db" in "\n".join(init_lines):
            errors.append("normal CI must not modify the committed deployment database")

    if path.name == "live-e2e.yml":
        on_block = content.split("permissions:", 1)[0]
        if "workflow_dispatch:" not in on_block:
            errors.append("live workflow must support workflow_dispatch")
        if "push:" in on_block or "pull_request:" in on_block:
            errors.append("live workflow must be manual only")
        if not re.search(r"(?m)^\s+environment:\s*live-testing\s*$", content):
            errors.append("live workflow must use the protected live-testing environment")
        if "${{ secrets.GOOGLE_API_KEY }}" not in content:
            errors.append("live workflow must read GOOGLE_API_KEY from GitHub secrets")

    return errors


def main() -> None:
    if not WORKFLOWS_DIR.is_dir():
        raise SystemExit(f"Workflow directory not found: {WORKFLOWS_DIR}")

    failures: list[str] = []
    workflow_paths = sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])
    if not workflow_paths:
        raise SystemExit("No GitHub Actions workflows found")

    for path in workflow_paths:
        errors = _errors_for(path)
        if errors:
            failures.extend(f"{path.name}: {error}" for error in errors)
        else:
            print(f"{path.name}: PASS")

    if failures:
        for failure in failures:
            print(f"[ERROR] {failure}")
        raise SystemExit(1)

    print("Workflow immutability and least-privilege validation: PASS")


if __name__ == "__main__":
    main()
