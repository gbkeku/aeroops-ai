---
name: aeroops-systems-engineering
description: >
  Provides domain context and safety rules for working within the AeroOps
  aircraft-program operations decision-support codebase. Governs how agents
  and developers interact with synthetic aviation data.
---

# AeroOps Systems Engineering Skill

This skill defines the operational boundaries and safety rules for the AeroOps
project. All contributors and AI agents working within this codebase **must**
follow these rules without exception.

## Mandatory Rules

### 1. All Aviation Information Is Synthetic

Every record in the AeroOps database — aircraft, test events, defects,
maintenance tasks, parts, engineering changes, and schedule dependencies — is
**entirely fabricated** for demonstration purposes. No real aircraft programs,
operators, manufacturers, or regulatory bodies are represented.

### 2. Never Represent the Application as an Authority

AeroOps is **not** an airworthiness authority, certification body, or safety
authority. Never claim, imply, or allow the system to present itself as
providing regulatory guidance, safety determinations, or certification
decisions.

### 3. Decision Support Only

Treat every output of the AeroOps system as **decision support**. Outputs are
intended to inform human leadership — they are not directives, mandates, or
binding recommendations. Final decisions always rest with qualified human
decision-makers.

### 4. Separate Confirmed Causes, Contributing Factors, Assumptions, and Unknowns

When analysing delays or operational issues, always categorise findings into:

- **Confirmed causes** — backed by specific database records.
- **Contributing factors** — correlated but not definitively causal.
- **Assumptions** — reasonable inferences stated explicitly as such.
- **Unknowns** — gaps in the data that prevent a conclusion.

Never conflate these categories.

### 5. Every Operational Claim Must Include Source Record IDs

Any factual statement about aircraft status, test results, defect conditions,
parts availability, or schedule state **must** cite one or more source record
IDs from the synthetic database. Unsourced claims are prohibited.

### 6. Never Invent Missing Records

If a query requires data that does not exist in the database, state that the
information is unavailable. Do **not** fabricate, hallucinate, or synthesise
records to fill gaps.

## Additional Guidelines

- **Secrets**: Never expose API keys, credentials, or proprietary information
  in code, logs, agent responses, or documentation.
- **Read-only access**: Agents must only read from the MCP server. No agent
  may create, update, or delete database records.
- **Traceability**: Maintain a clear audit trail from user question → agent
  reasoning → cited source records → final recommendation.
