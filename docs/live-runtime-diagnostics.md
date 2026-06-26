# Live Gemini runtime diagnostics

AeroOps separates the live path into three observable boundaries:

1. read-only MCP preflight (`get_aircraft_status`, `get_milestones`)
2. a minimal Gemini model probe
3. the complete ADK multi-agent investigation

Run:

```bash
uv run python scripts/diagnose_live_path.py --aircraft AC-009 --run-live
```

Test a specific model without editing `.env`:

```bash
uv run python scripts/diagnose_live_path.py \
  --aircraft AC-009 \
  --model gemini-2.5-flash \
  --run-live
```

The diagnostic never prints credentials, prompts, MCP response bodies, or
provider response details. It reports only safe classifications such as:

```text
model_probe=FAILED:ServerError:code=500:status=INTERNAL
```

or:

```text
live_investigation=FAILED:agent_execution:ServerError:code=500:status=INTERNAL:agent=test_ops_specialist
```

## Retry behavior

Every intake, specialist, and synthesis model request uses the same bounded
policy:

- request timeout: 120 seconds
- total attempts: 4
- initial retry delay: 1 second
- maximum retry delay: 8 seconds
- retry status codes: 408, 429, 500, 502, 503, and 504

These values can be overridden with the `AEROOPS_MODEL_*` environment variables
documented in `.env.example`.

## Model selection

The production default is the stable `gemini-2.5-flash` model. A specific
stable model is preferred to the rotating `gemini-flash-latest` alias for a
public deployment.

A provider-side 500 response may remain after all retries. In that case, retry
later, check the Gemini API service status, or test another supported stable
model. AeroOps keeps the public UI error generic while the diagnostic command
reports the safe failure classification.
## Executive synthesis normalization

The synthesis agent intentionally has no provider-side `output_schema`. Live
models can return a near-miss for the deeply nested `ExecutiveBrief`, causing
ADK to raise a Pydantic `ValidationError` before application-level validation
can run. AeroOps instead requests a compact JSON draft and uses an agent-level
`after_model_callback` to reconstruct the canonical brief from validated
specialist reports and authoritative milestone state. Pydantic, evidence, and
security validation still run afterward.

When the API key or region returns a transient error for the configured model,
test another supported model with `--model`. A model probe passing proves the
key and provider boundary work; a subsequent failure identifies an AeroOps
workflow stage.


## Specialist report normalization

A live model can also return a near-miss `SpecialistReport` after its MCP tool
calls. AeroOps now registers an agent-level `after_model_callback` on each of
the four specialists. Intermediate function-call responses are left unchanged,
so authorized tools still execute. The final visible response is rebuilt from
the exact MCP responses captured for that specialist.

The deterministic normalizer:

- forces the validated aircraft scope and specialist domain
- accepts bounded model wording only when it cites returned records
- derives source references, classifications, and semantic claims from MCP data
- adds relationship records required to prove blocker claims
- rejects identifiers that were not returned to that specialist
- creates evidence-backed fallback findings when the model JSON is malformed

`ReportValidatorAgent` remains the next strict boundary. If it rejects a report,
the diagnostic reports the stage as `specialist_report_validation` and emits
only bounded categories such as `SPECIALIST_SCHEMA_INVALID`; raw model output is
never printed.

## Recoverable tool-argument aliases

The live model may occasionally use a human-domain label that is not part of a
strict MCP enum. For example, it may request `get_test_events` with
`status="failed"`, while the AeroOps test-event model stores only `planned`,
`blocked`, `in_progress`, `completed`, and `aborted`.

AeroOps handles this specific read-only mismatch at the security boundary:

- the Test Operations instruction requests the complete bounded event list and
  tells the model to inspect authoritative stored statuses
- the security plugin removes only narrowly approved aliases that have no exact
  stored equivalent before strict Pydantic validation
- `status="failed"` becomes an unfiltered `get_test_events` read; it is **not**
  mapped to a fabricated status
- unknown values remain invalid and still stop the call
- normalization events contain only a stable category and never log raw model
  arguments

This recovery does not broaden the global or per-agent tool allowlists, does
not enable writes, and does not bypass the MCP record limit.
