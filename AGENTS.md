# AeroOps — Project Rules

## Project Objectives

AeroOps is a **capstone-sized** decision-support agent for aircraft development
programs. It investigates delays by correlating synthetic test events, defects,
maintenance tasks, parts constraints, engineering changes, and schedule
dependencies.

**Primary demonstration question:**

> "Why is aircraft AC-009 delayed, what is blocking its next test, and what
> actions should leadership take?"

All aviation data used in this project is **entirely synthetic**. AeroOps is
**not** an airworthiness, certification, or safety authority.

## Architecture Boundaries

- The system is **read-only** with respect to operational data. Agents query
  the MCP server; they never write to the database.
- The MCP server exposes a controlled set of read-only tools over the SQLite
  database.
- The Streamlit interface is the sole user-facing entry point.
- ADK agents coordinate through a workflow orchestrator — they do not call
  external services beyond the configured LLM and local MCP server.

## Coding Conventions

- **Language:** Python 3.11+.
- **Formatter / Linter:** Ruff (line-length 99, target py311).
- **Type hints:** Required on all public function signatures.
- **Models:** Pydantic v2 `BaseModel` for domain objects; `pydantic-settings`
  `BaseSettings` for configuration.
- **Docstrings:** Required on all public modules, classes, and functions.
  Use Google-style docstrings.
- **Imports:** Use `from __future__ import annotations` in every module.

## Test Requirements

- **Framework:** pytest with `pytest-asyncio` for async tests.
- **Coverage:** Every public function must have at least one test.
- **Test data:** Tests must use synthetic fixtures, never production data.
- **Naming:** Test files mirror source: `src/aeroops/health.py` →
  `tests/test_health.py`.

## Non-Destructive Development Rules

- Never delete or overwrite production data or configuration.
- Never commit secrets, API keys, or credentials to version control.
- Never modify files outside the `aeroops-agent/` workspace.
- Always run `ruff check` and `pytest` before considering a change complete.
- Preserve existing comments and docstrings unrelated to your changes.

## ADK Deployment Note

When deploying an ADK agent that uses `McpToolset`, define the agent and
`McpToolset` synchronously in `agent.py`. Async operations within tools,
MCP handlers, and the runtime event loop are fully supported.
