# AeroOps Security Policy & Architecture

This document describes the security policies, enums, error mappings, resource budgets, sanitization mechanisms, and database read-only guarantees implemented in the AeroOps agent.

---

## 1. Security Enums & Reason Codes

### `SecurityReasonCode`
A central enum mapping all security violation conditions:

- `QUERY_TOO_LONG`: Query exceeds character limits (1000).
- `QUERY_TOO_LARGE`: Query exceeds byte size limits (2000 UTF-8 bytes).
- `UNSUPPORTED_MESSAGE_TYPE`: User query is not a string.
- `INVALID_CONTROL_CHARACTER`: Presence of control characters ord < 32 (excluding `\t`, `\n`, `\r`).
- `MALFORMED_AIRCRAFT_ID`: Aircraft identifier fails validation pattern.
- `AMBIGUOUS_AIRCRAFT_SCOPE`: Multiple aircraft identifiers detected in a single query.
- `SECRET_EXFILTRATION_REQUEST`: Request for API credentials, keys, or environmental variables.
- `HIDDEN_REASONING_REQUEST`: Attempt to request model thoughts or hidden reasoning.
- `SYSTEM_PROMPT_REQUEST`: Request to view the agent system prompts/instructions.
- `ARBITRARY_SQL_REQUEST`: Blocked arbitrary SQL queries.
- `FILESYSTEM_ACCESS_REQUEST`: Blocked local path/file access requests.
- `MUTATION_REQUEST`: Blocked write/delete/update operations.
- `TOOL_NOT_ALLOWED`: Tool called is not in the global allowlist.
- `TOOL_NOT_ALLOWED_FOR_AGENT`: Tool called is not authorized for the requesting agent.
- `INVALID_TOOL_ARGUMENTS`: Tool arguments fail schema validation.
- `TOOL_BUDGET_EXCEEDED`: Tool or model execution budget exhausted.
- `RESULT_LIMIT_EXCEEDED`: Tool returned more than 50 records.
- `RESULT_PAYLOAD_TOO_LARGE`: Tool response size exceeded 50,000 bytes.
- `INDIRECT_PROMPT_INJECTION_DETECTED`: Prompt injection sequence discovered in operational text fields.
- `UNSAFE_RECOMMENDATION`: Recommended actions that suggest bypassing tests, inspections, or safety approvals.
- `SECURITY_VALIDATION_UNAVAILABLE`: Core security framework failure.

---

## 2. Public Error Mapping & Safe Responses

Security policy violations raise distinct exceptions mapping to HTTP-safe public error representations:

- `SecurityPolicyViolation` -> ValueError mapping to input-denied responses.
- `ToolAuthorizationError` -> PermissionError mapping to tool-execution denials.
- `ToolArgumentValidationError` -> ValueError mapping to schema failures.
- `UnsafeToolResultError` -> ValueError mapping to data-safety failures.
- `UnsafeResponseError` -> ValueError mapping to synthesis-boundary safety failures.
- `SecurityInfrastructureError` -> RuntimeError mapping to validation unavailability.

All exception messages are sanitized of stack traces, database paths, and API keys before being returned or logged.

---

## 3. Resource Budgets & Invocation Isolation

To prevent resource exhaustion, Rate-Limiting and budgets are enforced per-invocation:

- **Model Budget**: Max 10 calls per investigation.
- **Tool Budget**: Max 15 calls per investigation.
- **Result Count Budget**: Max 50 items returned per tool call.
- **Payload Size Budget**: Max 50,000 bytes per tool result.
- **State Isolation**: Budgets are tracked in session state (`temp:security_*`) ensuring concurrency safety and isolated contexts.

---

## 4. Operational Data Sanitization (Model-Bound Sanitization)

Operational database results are treated as untrusted data before being exposed to the model.

1. **Deep Copy**: The original results (canonical evidence) are deep-copied in `before_model_callback`.
2. **Text Sanitization**: Only free-text fields (`title`, `description`, `rationale`, `notes`) are modified:
   - Control characters are stripped.
   - Text is truncated to a maximum of 400 characters.
   - Values are wrapped in safety envelopes: `[UNTRUSTED OPERATIONAL DATA: <text> (untrusted_operational_text=true)]`.
   - Prompt injection sequences are replaced with a high-priority warning prefix.
3. **Parity**: IDs, status colors, timestamps, dates, and numbers are kept completely unmodified to ensure structural parity and correctness of reasoning.

---

## 5. Stdio subprocess & Cleanups

All subprocesses (transient stdio MCP clients) and runner contexts are fully cleaned up under `finally` blocks:

- Subprocess streams (`stdin`, `stdout`) are closed and waited.
- Processes are gracefully terminated, falling back to `kill` on timeouts.
- All specialist toolsets and ADK runners are closed.
- pytest runs assert zero `ResourceWarning` leaks.

---

## 6. Read-Only Enforcement

- **SQLite query_only**: Database connections are established with `read_only=True` which configures SQLite's `query_only` PRAGMA, blocking inserts, updates, and deletes at the engine level.
- **Allowlist**: Stdio MCP server exposes exactly 11 read-only queries.
- **Keyword Scanner**: Rejects delete, drop, update, and clear query requests at the validation boundary.
