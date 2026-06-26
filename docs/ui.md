# AeroOps Streamlit UI Application

This document provides a comprehensive reference for the design, architecture, configuration, operation, and verification of the AeroOps Program Operations Manager decision-support dashboard.

## 1. Live & Offline Architecture

The AeroOps UI is designed using a clean, decoupled service architecture to separate Streamlit rendering concerns from operational pipeline execution and data access constraints:

```mermaid
graph TD
    StreamlitUI[Streamlit UI app.py] --> UIController[UI Controller ui_controller.py]
    UIController -->|Offline Mode AEROOPS_OFFLINE_DEMO=1| OfflineFixtures[Offline Fixtures offline_fixtures.py]
    UIController -->|Live Mode| InvestigationService[Investigation Service run_investigation_async]
    UIController -->|Live Mode| McpClient[MCP Client call_mcp_tool_direct]
    InvestigationService -->|ADK Runner| AdkRunner[ADK Runner & Pipeline]
    AdkRunner -->|Stdio Process| McpServer[MCP Server mcp_server.py]
    McpClient -->|Stdio Process| McpServer
    McpServer -->|SQLite Driver| DB[(SQLite Database aeroops.db)]
```

- **Live Mode (Default)**: Executes a new multi-agent pipeline run using the ADK Runner and launches a dedicated `aeroops-data-mcp` stdio MCP server subprocess. No persistent processes are cached across runs.
- **Offline Preview Mode**: Activated by setting `AEROOPS_OFFLINE_DEMO=1`. It completely bypasses ADK, MCP, and Gemini API calls, serving deterministic local fixtures dated `2026-06-24`. It ensures instant renders for demos and localized testing.

---

## 2. No-Direct-Database Guarantee

To enforce strict separation of concerns and auditability, the Streamlit interface, its controllers, and offline preview fixtures are **prohibited from directly importing sqlite3, aeroops.db, or repository functions**.

Live data flows strictly through this path:
`Streamlit UI` → `Typed UI Service (ui_controller.py)` → `Read-only MCP Client / run_investigation_async` → `MCP Server` → `Repository` → `SQLite`

This rule is statically verified and enforced by a dedicated architecture import guard test: `tests/test_ui_architecture.py`.

---

## 3. Environment Variables

The application behavior is controlled using the following environment variables:

| Variable | Description | Allowed Values | Default |
|---|---|---|---|
| `AEROOPS_OFFLINE_DEMO` | Toggles offline preview mode with canned mock fixtures. | `"1"` (offline), other/empty (live) | (Live mode) |
| `AEROOPS_DB_PATH` | Path to the SQLite database. Injected into the stdio MCP server process. | Absolute or relative file path | `data/aeroops.db` |
| `GOOGLE_API_KEY` | Developer key to authenticate with Gemini model endpoints. (Required for Live mode) | String | None |

---

## 4. UI Service Boundary

The interface uses a set of narrowly scoped, strongly typed service functions exposed by [`ui_controller.py`](../src/aeroops/ui_controller.py):

- `get_aircraft_options(db_path_override: str | None = None) -> list[str]`: Retrieves available aircraft IDs via `list_aircraft` MCP tool.
- `get_fleet_dashboard_snapshot(db_path_override: str | None = None) -> FleetDashboardSnapshot`: Aggregates high-level fleet overview metrics using `list_aircraft` and `get_fleet_summary` MCP tools.
- `run_dashboard_investigation(query: str, aircraft_id: str, db_path_override: str | None = None) -> DashboardInvestigationResult`: Validates inputs, coordinates the async pipeline run, and extracts the results into the typed view models.
- `build_dependency_dot(nodes: list[DependencyNode], edges: list[DependencyEdge]) -> str`: Translates structured dependency data into Graphviz DOT syntax using safe string escaping.

No generic UI helper accepting arbitrary MCP tool names or direct SQL queries is exposed.

---

## 5. View Models & Data Policies

All presentation components are constructed from structured Pydantic models defined in [`ui_models.py`](../src/aeroops/ui_models.py):

- `FleetDashboardSnapshot`: Represents overall fleet health counts and defect metrics.
- `DependencyNode` & `DependencyEdge`: Model elements of the schedule dependency graph.
- `TimelineEvent`: Chronologically represents forecast and milestone dates.
- `EvidenceTableRow`: Validated rows of the evidence catalog.
- `SafeAgentActivity`: Execution metadata (agent, tool, and duration) without exposing raw results or reasoning.
- `DashboardInvestigationResult`: Aggregates the executive brief and all structured view models.

### Activity Trace and Security Policies
- The UI activity tracker reads execution times from `SafeAgentActivity` records populated by a dedicated `ActivityCollector` plugin.
- Reasoning steps, system instructions, environment variables, raw session state, and raw tool result contents are strictly hidden from the user.
- Security and validation errors are mapped to generic, safe public-facing messages. Raw stack traces are never printed.

---

## 6. Streamlit Session State Behavior

The application utilizes `st.session_state` to store user session configurations safely across reruns:

- `st.session_state.selected_aircraft`: Currently selected aircraft ID.
- `st.session_state.query_text`: Current text input in the query workspace.
- `st.session_state.last_result`: The last successfully processed `DashboardInvestigationResult`.
- `st.session_state.current_error`: The last generic error message to display.

No Runner, McpToolset, subprocess, or session-service instances are cached in session state or streamlit cache.

---

## 7. Business-Value Calculator

The Business Value panel allows program managers to perform interactive ROI coordination savings calculations using a transparent annual savings formula:

\[\text{Annual Savings} = \text{Engineers} \times \text{Coordination Hours Saved per Engineer per Week} \times \text{Loaded Hourly Cost} \times 52\]

- **Inputs**: Bounded, non-negative numbers with default parameters (50 engineers, 2.0 hours saved, \$150 hourly cost).
- **Presentation**: Formatted as currency. Inputs are clearly labeled as user assumptions, and a disclaimer notes that the calculation is illustrative and not a measured outcome of AeroOps.

---

## 8. Accessibility Features

- **Text & Status Markers**: Green, amber, and red statuses are accompanied by readable text labels (e.g. `🟢 GREEN`, `🔴 BLOCKED`) and textual descriptions, ensuring usability for color-blind users.
- **Tabular Graph Fallback**: The Graphviz schedule dependency visualization is supplemented by an expander containing a screen-reader-friendly table displaying all graph nodes, edges, and statuses.
- **Contrast & Structure**: High-contrast markdown styling and limited nested columns ensure usability across different viewport sizes, including narrow screens.

---

## 9. Launch & Testing Commands

### Launching Streamlit Locally
```bash
uv sync --locked --all-groups
uv run streamlit run src/aeroops/app.py
```

### Running Verification Tests
```bash
# Run unit and AppTest suite
uv run pytest tests/test_ui.py -v

# Run UI controller model-boundary integration test
uv run pytest tests/test_ui_integration.py -v

# Run entire test suite
uv run pytest tests/ -v -ra

# Run test suite checking for ResourceWarnings (e.g., leaked file descriptors or sockets)
uv run pytest tests/ -W error::ResourceWarning
```

---

## 10. Genuine Browser Verification Screenshots

Below are the genuine browser screenshots captured directly from the running Streamlit dashboard:

### 1. Landing Page View
![Landing Page View](images/aeroops_landing_offline.png)

### 2. AC-009 Investigation Results
![AC-009 Results View](images/aeroops_ac009_executive.png)

### 3. Investigation Breakdown & Recommended Actions
![Breakdown & Actions](images/aeroops_breakdown_actions.png)

### 4. Dependency Graph & Evidence Verification
![Dependency Graph & Evidence](images/aeroops_dependency_graph.png)

### 5. Safe Agent Activities & ROI Savings Calculator
![Activity Trace & ROI](images/aeroops_activity_roi.png)

### 6. Safe Request and System Error Display
![Safe Generic Error](images/aeroops_safe_security_error.png)

### 7. Narrow Viewport Mobile Layout
![Responsive Mobile View](images/aeroops_narrow_layout.png)
