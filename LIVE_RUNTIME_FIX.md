# AeroOps live-runtime fixes

## Provider resilience

- Uses bounded retries for 408, 429, 500, 502, 503, and 504 responses.
- Uses a 120-second per-model-request timeout.
- Applies the same request policy to intake, specialist, and synthesis agents.
- Uses `StdioConnectionParams` with a configurable 30-second MCP startup timeout.
- Reports only sanitized provider code, status, failing agent, and validation locations.
- Includes a minimal direct Gemini model probe in `scripts/diagnose_live_path.py`.

## Executive synthesis validation fix

A live `gemini-2.5-flash` run reached all specialist tools but failed at
`executive_synthesis` with a Pydantic `ValidationError`. The cause was the
provider-side `output_schema=ExecutiveBrief`: the schema contains nested
findings, actions, evidence references, and discriminated claim unions, so a
near-miss model response was rejected by ADK before AeroOps could apply its own
validation and fallback controls.

The synthesis boundary now works as follows:

1. The tool-free synthesis agent returns a compact JSON draft containing only
   leadership wording, action proposals, assumptions, unknowns, and confidence.
2. An agent-level `after_model_callback` builds the canonical `ExecutiveBrief`
   from the four already validated specialist reports and authoritative
   milestone state.
3. Pydantic validation, EvidenceCatalog validation, and response-security
   validation still run before the result is returned.
4. Unsupported IDs, contradictory dates or delay values, malformed actions,
   and missing model fields are discarded or replaced with deterministic,
   evidence-backed defaults.

This avoids provider-side schema fragility without weakening the evidence or
security boundary. The executive model still contributes concise wording and
action prioritization, but it cannot create operational findings or evidence.

## Local verification

Use the model that succeeds for the current API key and region. For example:

```dotenv
AEROOPS_OFFLINE_DEMO=0
AEROOPS_MODEL=gemini-2.5-flash
AEROOPS_DB_PATH=data/aeroops.db
GOOGLE_API_KEY=<your local key>
```

Then run:

```bash
uv sync --locked --all-groups
uv run pytest tests/test_live_runtime.py -v
uv run pytest tests/test_agent.py -v
uv run pytest tests/test_e2e_deterministic.py -v
uv run python scripts/diagnose_live_path.py \
  --aircraft AC-009 \
  --model gemini-2.5-flash \
  --run-live
```

Expected diagnostic sequence:

```text
preflight=PASS
model_probe=PASS
live_investigation=PASS:AC-009:6
```
