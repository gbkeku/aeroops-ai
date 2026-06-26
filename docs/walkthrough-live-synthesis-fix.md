# Walkthrough: Live executive-synthesis validation fix

## Reproduced boundary

The live diagnostic established this sequence:

- the database path exists
- read-only MCP preflight passes
- the direct Gemini probe passes with `gemini-2.5-flash`
- all four specialist agents call their authorized MCP tools successfully
- `executive_synthesis` fails with a Pydantic `ValidationError`

This isolates the defect to the executive model's provider-side structured-output boundary rather than authentication, SQLite, MCP, or specialist tool routing.

## Root cause

`executive_synthesis` used `output_schema=ExecutiveBrief`. ADK sent that deeply nested Pydantic model to Gemini as the provider response schema. The contract contains findings, evidence references, recommendations, and discriminated semantic claims. A live-model near miss was rejected during the Gemini call before AeroOps could apply its own deterministic normalizer, EvidenceCatalog checks, or response-security validation.

## Correction

- Removed provider-side `output_schema` from the tool-free synthesis agent.
- Reduced the requested model response to a compact JSON draft.
- Added `src/aeroops/synthesis.py` with an agent-level `after_model_callback` that constructs the canonical `ExecutiveBrief` from:
  - the four validated specialist reports
  - authoritative milestone state
  - bounded model wording and proposed action links
- Preserved service-level Pydantic, EvidenceCatalog, and response-security checks.
- Added deterministic fallbacks for malformed JSON, missing actions, invalid owner roles, null dates, unsupported identifiers, and contradictory schedule wording.
- Added sanitized synthesis-validation diagnostics that never retain raw model text.

## Security and evidence guarantees retained

- synthesis has zero tools
- findings and semantic claims come only from validated specialist state
- milestone dates and delay are authoritative
- action evidence comes from specifically linked findings
- unsupported source IDs are discarded
- the final evidence list is derived deterministically
- evidence or response validation failure still prevents a result from being returned

## Verification completed

| Check | Result |
|---|---:|
| Ruff formatting | Passed |
| Ruff lint | Passed |
| `tests/test_live_runtime.py` | 14 passed |
| `tests/test_agent.py` | 52 passed, 4 optional live tests skipped |
| Deterministic AC-009 full pipeline | 1 passed |
| Real UI-controller / ADK / stdio MCP integration | 1 passed |
| Secret scan | Passed |
| Public-document link validation | Passed |
| Syntax compilation | Passed |

The deterministic full-pipeline test confirmed the six-day delay and exact accepted evidence set:

- `MS-009-FTC`
- `TEST-009-118`
- `TEST-009-121`
- `DEF-009-042`
- `PART-ACT-774`
- `CR-184`
- `MNT-009-015`
- `DEP-009-001`
- `DEP-009-002`
- `DEP-009-003`
- `DEP-009-004`

A credential-backed Gemini run was not available in the verification environment. It must be retested locally with the user's configured API key.

## Local retest

```bash
uv sync --locked --all-groups
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run pytest tests/test_live_runtime.py -v
uv run pytest tests/test_agent.py -v
uv run pytest tests/test_ui_integration.py -v
uv run pytest tests/test_e2e_deterministic.py::TestDeterministicE2E::test_full_pipeline_ac009 -v
uv run python scripts/diagnose_live_path.py \
  --aircraft AC-009 \
  --model gemini-2.5-flash \
  --run-live
```

Expected final diagnostic:

```text
preflight=PASS
model_probe=PASS
live_investigation=PASS:AC-009:6
```
