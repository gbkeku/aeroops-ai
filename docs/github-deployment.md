# GitHub repository and CI setup

AeroOps is prepared for the public repository:

- **Owner:** `gbkeku`
- **Repository:** `aeroops-ai`
- **URL:** https://github.com/gbkeku/aeroops-ai
- **Visibility:** Public
- **Default branch:** `main`
- **Required workflow:** `.github/workflows/ci.yml`

At review time the remote repository exists but contains no project files. Push this reviewed package only after running the local checks.

## First push to the existing empty repository

```bash
git init
git add .
git status
git commit -m "feat: publish AeroOps multi-agent operations manager"
git branch -M main
git remote add origin https://github.com/gbkeku/aeroops-ai.git
git push -u origin main
```

When a local repository or remote already exists, inspect `git status`, `git branch --show-current`, and `git remote -v` before changing it. Never force-push.

## Required pre-push checks

```bash
uv sync --locked --all-groups
uv run python scripts/secret_scanner.py
uv run python scripts/validate_workflows.py
uv run python scripts/validate_public_docs.py
uv run python scripts/verify_db_artifact.py
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run pytest tests/ -v -ra
uv run pytest tests/ -W error::ResourceWarning
```

Confirm `.env`, `.streamlit/secrets.toml`, virtual environments, caches, logs, coverage files, generated databases, credentials, and private keys are absent from staging.

## Workflows

### AeroOps CI

Runs on pushes to `main`, pull requests targeting `main`, and manual dispatch. It installs locked dependencies, validates workflows and public docs, scans secrets, creates a temporary database, verifies the committed database, runs Ruff, deterministic evaluation, UI integration, MCP smoke tests, the complete suite, the ResourceWarning gate, AppTest, and a real Streamlit health check.

### AeroOps Live E2E Verification

Manual-only and protected by the `live-testing` environment. It reads `GOOGLE_API_KEY` from GitHub Secrets and never runs for pull requests.

External actions are pinned to full commit SHAs, checkout does not persist Git credentials, and top-level workflow permissions are read-only.

## Recommended repository settings

After the first successful run:

- require pull requests before merging to `main`;
- require the `AeroOps CI` check;
- block force pushes and branch deletion;
- retain read-only workflow permissions;
- enable private vulnerability reporting when available.

## Post-push verification

Open the repository in a private browser session and confirm the README images render, links resolve, GitHub detects the MIT license, `data/aeroops.db` exists, secret files are absent, and CI completes successfully.
