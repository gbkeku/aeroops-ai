# AeroOps Threat Model

This threat model outlines the security assets, trust boundaries, threat scenarios analyzed via STRIDE methodology, security controls, and residual risks for the `aeroops-agent` application prepared for a public, read-only decision-support demonstration.

---

## 1. Security Assets under Protection

The following assets are identified within the AeroOps system boundary:

| Asset | Description | Sensitivity |
|---|---|---|
| **API Credentials** | Google Gemini API keys, MCP connection details, and other credentials. | **High** |
| **Agent Instructions** | System prompts, configuration parameters, and persona definitions. | **Medium** |
| **Synthetic Operational Records** | The synthetic database records including aircraft status, milestones, defects, test events, change requests, and maintenance tasks. | **Low (Public Demo)** |
| **Session State** | In-memory session data, variable state, and intermediate specialist reports. | **Medium** |
| **EvidenceCatalog Records** | The assembled, immutable facts captured during investigation preflight and tool execution. | **High (Integrity)** |
| **Audit Logs** | Security and operational event records outputted to `stderr`. | **Medium** |
| **Invocation Budgets** | Counters tracking model calls, tool calls, and result payload sizes. | **Medium** |

---

## 2. Trust Boundaries & Architecture

```mermaid
flowchart TD
    subgraph Public Internet (Untrusted)
        User[User / Client Interface]
    end

    subgraph AeroOps Security Sandbox (Trusted Boundary)
        App[Application Services]
        Policy[Security Policy Scanner]
        Plugin[AeroOpsSecurityPlugin]
        Runner[ADK Runner]
        
        subgraph Subprocesses
            Preflight[Preflight Stdio Client]
            MCP[Stdio MCP Process]
        end
        
        subgraph Storage
            DB[(Operational SQLite DB)]
        end
    end
    
    subgraph External Boundary
        LLM[Google Gemini LLM Service]
    end

    User -->|1. Natural Language Query| App
    App -->|2. Check Input| Policy
    App -->|3. Validate Scope & Resolve Milestone| Preflight
    Preflight -->|4. Launch Read-Only Stdio| MCP
    MCP -->|5. SQL Query| DB
    
    App -->|6. Execute Investigation| Runner
    Runner -->|7. Enforce Budgets & Allowlists| Plugin
    Plugin -->|8. Model Calls| LLM
```

- **Boundary 1: Client to App Service**: Untrusted inputs (prompts) cross this boundary. Input sanitization and scanner policies are applied here.
- **Boundary 2: App Service to Stdio Subprocesses**: Local environment boundary. Preflight controls restrict allowed commands.
- **Boundary 3: ADK Runner to external Google Gemini API**: Data sent to/received from external model is sanitized via model-bound views.

---

## 3. STRIDE Threat Analysis

### Spoofing
- **Threat Scenario**: An attacker impersonates a trusted aircraft system or injects a fake aircraft ID scope.
- **Security Controls**:
  - Authoritative validation of `aircraft_id` using strict regex pattern `^AC-\d{3}$`.
  - Verification that the aircraft exists in the database before any workflow is initialized.
- **Verification Tests**:
  - `test_investigation_scope_pattern_validation`
  - `test_nonexistent_aircraft_raises`

### Tampering
- **Threat Scenario**: An attacker uses prompt injection to override specialist findings or bypass safety requirements.
- **Security Controls**:
  - Specialist evidence is captured via independent callback state.
  - Final briefs are validated against the `EvidenceCatalog` containing immutable evidence records.
  - Model-bound data is deep-copied and sanitized to wrap untrusted text, warning the LLM to ignore instructions inside data fields.
- **Verification Tests**:
  - `test_model_bound_sanitization`
  - `test_full_pipeline_ac009` (E2E)

### Repudiation
- **Threat Scenario**: An execution denial or system abuse occurs without a log trace.
- **Security Controls**:
  - Structured, single-line JSON audit logging to `stderr`.
  - Logging of every tool call, decision (allowed/denied), and policy enforcement.
- **Verification Tests**:
  - `test_audit_logging_to_stderr`

### Information Disclosure
- **Threat Scenario**: Prompt injection or system exceptions leak API keys, system prompts, database filepaths, or hidden thoughts in final reports or logs.
- **Security Controls**:
  - Log redactor (`redact_secrets`) recursively strips API keys, passwords, and DB paths.
  - Response validation (`validate_security_response`) serializes the final brief and scans for credentials, system prompt fragments, or hidden thinking.
- **Verification Tests**:
  - `test_secrets_redaction`
  - `test_response_validation_cross_aircraft_defect`

### Denial of Service
- **Threat Scenario**: An attacker craft queries designed to trigger infinite tool-looping, huge payloads, or model budget exhaustion, racking up API costs or consuming resources.
- **Security Controls**:
  - Concurrency-safe invocation budgets tracked in session state.
  - Strictly limited parameters: max 10 model calls, max 15 tool calls, max 50 records, max 50,000 bytes result payload.
- **Verification Tests**:
  - `test_budget_accounting`
  - `test_budget_concurrency_isolation`
  - `test_tool_result_payload_size_exceeded`

### Elevation of Privilege
- **Threat Scenario**: An attacker attempts to write to the database, seed it, or trigger arbitrary command execution.
- **Security Controls**:
  - Read-only SQLite database connection (`read_only=True` via query_only pragma).
  - Explicit tool allowlist restricting commands to 11 read-only functions.
  - Deletion/mutation keywords disallowed in user queries.
- **Verification Tests**:
  - `test_repository_read_only`
  - `test_input_prohibited_scans`
  - `test_tool_allowlist_global`

---

## 4. Residual Risks

- **LLM Non-Determinism in Intermediate Reasoning**: Since LLM output formatting can vary, the executive model returns a compact draft rather than the authoritative result. *Mitigation*: an agent-level after-model callback reconstructs the canonical `ExecutiveBrief` from validated specialist reports, followed by strict Pydantic, evidence-integrity, and security validation.
- **Denial of Service via Parallel Invocations**: While individual session budgets are isolated and concurrency-safe, rate-limiting is not enforced at the HTTP gateway level. *Mitigation*: Deploy rate-limiting at the reverse proxy/ingress level prior to agent routing.
