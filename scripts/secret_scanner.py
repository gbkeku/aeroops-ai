#!/usr/bin/env python3
"""Repository-level secret scanner for AeroOps.

The scanner focuses on credential formats and assignments with secret-bearing
variable names. It intentionally ignores immutable GitHub Action commit SHAs,
which are required by the release workflow and are not credentials.
"""

from __future__ import annotations

import re
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "Google API key": re.compile(r"\bAIzaSy[a-zA-Z0-9_-]{33}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "PEM private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key ID": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Bearer token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "internal package registry": re.compile(
        r"(?i)(?:https?://[^/\s]*(?:internal|artifactory)[^/\s]*/|private-package-registry)"
    ),
}

SECRET_ASSIGNMENT = re.compile(
    r"(?ix)\b(?:api[_-]?key|secret|token|password|passwd|authorization)\b"
    r"\s*[:=]\s*[\"']?([^\"'\s#]+)"
)
ACTION_SHA = re.compile(r"^\s*uses:\s*[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$", re.IGNORECASE)

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
}
EXCLUDE_FILES = {
    ".env.example",
    ".env.local.example",
    "secrets.toml.example",
    "secret_scanner.py",
}
IGNORE_EXTENSIONS = {
    ".db",
    ".png",
    ".jpg",
    ".jpeg",
    ".ico",
    ".gif",
    ".webm",
    ".zip",
    ".tar",
    ".gz",
    ".pyc",
    ".pyd",
    ".exe",
}
FORBIDDEN_LOCAL_SECRET_FILES = {
    Path(".env"),
    Path(".streamlit/secrets.toml"),
}
PLACEHOLDER_MARKERS = {
    "",
    "none",
    "null",
    "changeme",
    "placeholder",
    "your-api-key",
    "your_actual_api_key",
    "<configured-in-streamlit-cloud>",
    "<your-actual-google-api-key>",
    "set-in-streamlit-cloud-secrets",
}


def _is_placeholder(value: str, line: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if normalized in PLACEHOLDER_MARKERS:
        return True
    if normalized.startswith(("${{", "<", "example", "test", "mock", "dummy")):
        return True
    return "secrets." in line or "os.environ" in line or "getenv(" in line


def scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return potential credential findings from one text file."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings: list[tuple[str, int, str]] = []
    for line_num, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if ACTION_SHA.match(line):
            continue
        if "scanner: allow-test-secret" in line:
            continue
        if stripped.startswith("#"):
            continue

        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append((name, line_num, stripped[:120]))

        for match in SECRET_ASSIGNMENT.finditer(line):
            value = match.group(1)
            if not _is_placeholder(value, line):
                findings.append(("credential assignment", line_num, stripped[:120]))

    return findings


def main() -> None:
    """Scan public repository files and fail on likely credential exposure."""
    root = Path(__file__).resolve().parents[1]
    print(f"Scanning directory: {root}")

    findings_total = 0
    for relative in sorted(FORBIDDEN_LOCAL_SECRET_FILES):
        if (root / relative).exists():
            print(f"[ERROR] Local secret file must not be packaged or committed: {relative}")
            findings_total += 1

    files_scanned = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in relative.parts[:-1]):
            continue
        if path.name in EXCLUDE_FILES or path.suffix.lower() in IGNORE_EXTENSIONS:
            continue

        files_scanned += 1
        for name, line_num, snippet in scan_file(path):
            print(f"[ERROR] Potential {name} in {relative}:{line_num}")
            print(f"    {snippet}")
            findings_total += 1

    print(f"Scan completed. Inspected {files_scanned} files.")
    if findings_total:
        print(f"Failed: found {findings_total} potential secret leak(s).")
        raise SystemExit(1)

    print("Success: no secrets or local secret files found.")


if __name__ == "__main__":
    main()
