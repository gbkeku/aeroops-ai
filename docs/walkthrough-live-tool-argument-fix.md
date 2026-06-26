# Live Tool-Argument Compatibility Fix

## Failure reproduced

The live Gemini run completed MCP preflight and began the four specialist
branches. The Test Operations specialist then requested:

```json
{"aircraft_id": "AC-009", "status": "failed"}
```

The `get_test_events` MCP contract intentionally supports only:

- `planned`
- `blocked`
- `in_progress`
- `completed`
- `aborted`

The security plugin therefore rejected the unsupported enum before execution,
and ADK propagated the failure through the parallel task group.

## Root cause

The Test Operations instruction itself asked the model to identify events with
status `failed`, even though that status does not exist in the domain model or
MCP input schema. The model followed the natural-language instruction rather
than the generated enum schema.

## Correction

1. The Test Operations instruction now calls `get_test_events` without an
   optional status filter and asks the specialist to inspect authoritative
   stored statuses.
2. The instruction contains only the supported enum values and never includes
   the unsupported tool argument.
3. `normalize_tool_arguments()` applies a narrow, read-only compatibility rule:
   human labels `failed`, `failure`, and `unsuccessful` remove the optional test
   status filter. They are not mapped to a fabricated database status.
4. The security plugin mutates the actual ADK argument dictionary before strict
   Pydantic validation and tool execution.
5. Unknown values remain invalid, global and per-agent allowlists remain
   unchanged, and all record-count limits remain enforced.
6. Normalization audit events contain only stable category/count metadata and
   never raw model arguments.

## Verification

Verified locally in the corrected repository:

```text
Ruff format/check:                         PASS
Secret scan:                               PASS
Live runtime tests:                        15 passed
Agent architecture tests:                  53 passed, 4 optional live skipped
Specialist normalization tests:             5 passed
Evidence validation tests:                 17 passed
UI model-boundary integration:              1 passed
Lifecycle regression subset:                3 passed
MCP smoke test:                             PASS (11 read-only tools)
```

A direct compatibility check produced:

```text
normalized_args={'aircraft_id': 'AC-009'}
normalization_codes=('TEST_STATUS_ALIAS_FILTER_REMOVED',)
statuses=['aborted', 'blocked']
source_refs=['TEST-009-118', 'TEST-009-121']
```

The remaining credential-backed confirmation command is:

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
