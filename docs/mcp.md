# AeroOps Read-Only MCP Server

`aeroops-data-mcp` is a local Model Context Protocol server exposing typed, read-only access to the synthetic AeroOps database. It is implemented with the official Python MCP SDK and communicates over standard input/output.

## Start and test

```bash
uv run aeroops-data-mcp
```

A terminal appears to wait because stdout is reserved for MCP JSON-RPC. Use the smoke client for an interactive verification:

```bash
uv run python scripts/smoke_test_mcp.py
```

## Database configuration

`AEROOPS_DB_PATH` selects an existing SQLite file. The server opens it read-only and never creates, resets, seeds, migrates, or modifies the database.

## Standard response envelope

Successful tools return typed data containing `snapshot_date`, `synthetic_data`, `source_refs`, `data`, and `count` or `truncated` where applicable. Source references are stable operational record IDs.

## Eleven registered tools

| Tool | Inputs | Purpose |
|---|---|---|
| `health_check` | None | Verify server and database availability |
| `list_aircraft` | Optional `status` | List aircraft by readiness |
| `get_aircraft_status` | `aircraft_id` | Retrieve one aircraft status record |
| `get_milestones` | `aircraft_id` | Retrieve planned and forecast milestones |
| `get_open_defects` | `aircraft_id`, optional `severity` | Retrieve open defects |
| `get_test_events` | `aircraft_id`, optional `status` | Retrieve test events |
| `get_maintenance_tasks` | `aircraft_id`, optional `status` | Retrieve maintenance work |
| `get_parts_constraints` | `aircraft_id` | Retrieve material constraints |
| `get_change_requests` | `aircraft_id` | Retrieve engineering changes |
| `get_dependency_graph` | `aircraft_id` | Retrieve blocker nodes, edges, and dependency records |
| `get_fleet_summary` | None | Retrieve aggregate readiness metrics |

Aircraft identifiers must match `AC-NNN`. Unsupported filters fail validation instead of returning a misleading empty result.

## Read-only and safety controls

- SQLite URI `mode=ro` where supported.
- `PRAGMA query_only = ON` and foreign-key enforcement.
- Parameterized repository queries.
- No generic SQL tool.
- No mutation, approval, or release tool.
- Maximum 50 list records with truncation metadata.
- stdout reserved for protocol traffic; diagnostics use stderr.
- Sensitive paths, SQL, environment values, and stack traces are removed from public errors.

## Error categories

| Category | Meaning |
|---|---|
| `VALIDATION_ERROR` | Malformed identifier, unsupported filter, or invalid argument |
| `NOT_FOUND` | Well-formed identifier with no matching record |
| `DATABASE_UNAVAILABLE` | Missing or inaccessible configured database |
| `INTERNAL_ERROR` | Unexpected failure with sensitive details removed |

## Verification

```bash
uv run pytest tests/test_mcp_server.py -v
uv run pytest tests/test_mcp_contract.py -v
uv run python scripts/smoke_test_mcp.py
```

The contract tests use an actual stdio `ClientSession`, inspect all tool schemas, invoke the server, and verify safe protocol errors.
