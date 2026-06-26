"""Validate public documentation for local-only paths, placeholders, and broken links."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = [PROJECT_ROOT / "README.md", *sorted((PROJECT_ROOT / "docs").glob("*.md"))]
OPTIONAL_ROOT_FILES = [PROJECT_ROOT / "walkthrough_deployment.md"]

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("file URL", re.compile(r"file:///", re.IGNORECASE)),
    ("Windows absolute path", re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])")),
    ("Antigravity brain path", re.compile(r"\.gemini[\\/]antigravity[\\/]brain", re.IGNORECASE)),
    ("sandbox path", re.compile(r"(?:/mnt/[^/\s]+|/home/[^/\s]+)(?:/|\b)", re.IGNORECASE)),
    (
        "local test-session path",
        re.compile(r"pytest-of-|tmp_path|AppData[\\/]Local[\\/]Temp", re.IGNORECASE),
    ),
)
PUBLIC_LOCALHOST = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?", re.IGNORECASE
)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _files_to_check() -> list[Path]:
    files = list(PUBLIC_FILES)
    files.extend(path for path in OPTIONAL_ROOT_FILES if path.exists())
    return [path for path in files if path.exists()]


def main() -> int:
    errors: list[str] = []
    placeholders: list[str] = []

    for path in _files_to_check():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(PROJECT_ROOT)
        for label, pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: forbidden {label}: {match.group(0)!r}")

        for match in PUBLIC_LOCALHOST.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end == -1:
                line_end = len(text)
            line_text = text[line_start:line_end].lower()
            if not any(
                token in line_text for token in ("local", "health", "development", "browser")
            ):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{rel}:{line}: localhost URL is not labelled as a local-only example"
                )

        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(1).strip().split()[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            if not (path.parent / path_part).resolve().exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: broken relative Markdown link: {target}")

        for placeholder in ("<github-owner>", "<aeroops-repository>", "<streamlit-app-url>"):
            if placeholder in text:
                placeholders.append(f"{rel}: {placeholder}")

    if errors:
        print("Public documentation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    if placeholders:
        print(
            "Public documentation validation failed: unresolved repository placeholders",
            file=sys.stderr,
        )
        for item in sorted(set(placeholders)):
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Public documentation validation passed for {len(_files_to_check())} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
