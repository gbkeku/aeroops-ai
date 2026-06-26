# Walkthrough: Live specialist-report normalization fix

## Reproduced boundary

The credential-backed diagnostic reached the following stages successfully:

- database and MCP preflight
- direct `gemini-2.5-flash` model probe
- all four specialist agents
- every authorized specialist MCP tool call

The workflow then stopped in `ReportValidatorAgent` with a
`ReportValidationError`. This isolated the remaining defect to variation in the
specialists' final JSON rather than Gemini authentication, SQLite, MCP startup,
tool authorization, or the executive synthesis boundary.

## Correction

A deterministic `after_model_callback` is now registered on every specialist:

- `test_ops_specialist`
- `maintenance_specialist`
- `config_supply_specialist`
- `schedule_risk_specialist`

The callback returns `None` for intermediate function-call turns, preserving the
ADK tool loop. After the final model response, it builds a canonical
`SpecialistReport` from the branch-specific MCP evidence captured by the
existing `after_tool_callback`.

Model wording is retained only when all referenced AeroOps IDs were returned to
that specialist. Aircraft scope, domain, classifications, source references,
semantic claims, and `raw_source_ids` are derived deterministically. Malformed
or incomplete model JSON therefore no longer aborts an otherwise valid
investigation.

## Additional correction

Dependency-graph nodes contain compact presentation records. When a later
specialist list tool returns the same source ID with full operational fields,
`EvidenceCatalog` now keeps the richer payload. This is required for semantic
checks such as comparing `PART-ACT-774`'s need date and estimated arrival.

## Error and lifecycle behavior

- report-validation failures use the stage `specialist_report_validation`
- diagnostics expose only bounded violation categories
- public errors remain generic
- original exceptions remain available as causes for deterministic tests
- Runner and MCP cleanup tests now assert the intentional
  `LiveInvestigationError` boundary

## Verification

| Check | Result |
|---|---:|
| Ruff formatting | Passed |
| Ruff lint | Passed |
| Specialist normalization regression tests | 5 passed |
| Live-runtime regression tests | 14 passed |
| Agent architecture tests | 52 passed, 4 optional live tests skipped |
| Evidence validation tests | 17 passed |
| Updated lifecycle regression tests | 3 passed |
| Deterministic AC-009 core checks | 3 passed |
| Combined targeted gate | 88 passed, 4 optional live tests skipped |
| Secret scan | Passed |

A credential-backed Gemini call cannot be executed in the packaging
environment. Retest locally with:

```bash
uv run python scripts/diagnose_live_path.py \
  --aircraft AC-009 \
  --model gemini-2.5-flash \
  --run-live
```

Expected result:

```text
preflight=PASS
model_probe=PASS
live_investigation=PASS:AC-009:6
```
