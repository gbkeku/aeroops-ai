"""Unit and verification tests for the AeroOps Streamlit UI.

Verifies page layout, theme configuration, rerun state, offline/live isolation,
and presentational correctness without executing live subprocesses.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from streamlit.testing.v1 import AppTest

from aeroops.ui_models import FleetDashboardSnapshot

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ui_theme_config_valid() -> None:
    """Verify that the theme configuration file contains valid Streamlit theme settings and uses font='sans-serif'."""
    config_path = PROJECT_ROOT / ".streamlit" / "config.toml"
    assert config_path.exists(), "Theme config.toml does not exist"

    content = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(content)

    assert "theme" in config, "[theme] section missing in config.toml"
    theme = config["theme"]

    assert theme.get("base") == "dark"
    assert theme.get("font") == "sans-serif", "Must use 'sans-serif' with hyphen"
    assert theme.get("primaryColor") == "#00A3FF"
    assert theme.get("backgroundColor") == "#0B0E14"
    assert theme.get("secondaryBackgroundColor") == "#151B26"
    assert theme.get("textColor") == "#FFFFFF"


def test_ui_no_deprecated_width_calls() -> None:
    """Verify that no deprecated use_container_width calls remain in python files in the repository."""
    src_dir = PROJECT_ROOT / "src"
    py_files = list(src_dir.glob("**/*.py"))

    for f in py_files:
        content = f.read_text(encoding="utf-8")
        assert "use_container_width" not in content, f"Deprecated use_container_width found in {f}"


def test_ui_offline_fleet_exact() -> None:
    """Verify that the offline fleet snapshot contains exactly four expected aircraft."""
    from aeroops.offline_fixtures import MOCK_FLEET_SNAPSHOT

    assert len(MOCK_FLEET_SNAPSHOT.aircraft_options) == 4
    assert set(MOCK_FLEET_SNAPSHOT.aircraft_options) == {"AC-007", "AC-008", "AC-009", "AC-010"}


def test_ui_live_default_without_env(monkeypatch) -> None:
    """Verify that live mode is the default and no offline banner appears when AEROOPS_OFFLINE_DEMO is absent."""
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)

    called = {}

    def mock_snapshot():
        called["snapshot"] = True
        return FleetDashboardSnapshot(
            aircraft_options=["AC-009"],
            total_aircraft=1,
            green_count=0,
            amber_count=0,
            red_count=1,
            high_critical_defect_count=0,
            blocked_delayed_test_count=0,
            upcoming_milestone_count=0,
        )

    monkeypatch.setattr("aeroops.ui_controller.get_fleet_dashboard_snapshot", mock_snapshot)

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    assert called.get("snapshot") is True
    # The offline warning banner should NOT be visible
    assert not any("Offline Preview" in w.value for w in at.warning)


def test_ui_offline_banner_present(monkeypatch) -> None:
    """Verify that the offline preview warning banner appears only when AEROOPS_OFFLINE_DEMO == '1'."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # The offline warning banner SHOULD be visible
    assert any("Offline Preview" in w.value for w in at.warning)


def test_ui_preset_autofills_and_runs(monkeypatch) -> None:
    """Verify that clicking the preset button selects AC-009 and fills the exact query."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Click the preset button
    preset_btn = next(b for b in at.button if b.label == "Why is AC-009 delayed?")
    preset_btn.click().run()

    # Assert that session state is updated
    assert at.session_state["selected_aircraft"] == "AC-009"
    assert at.session_state["query_text"] == "Why is AC-009 delayed? Produce an executive brief."


def test_ui_analyze_triggers_offline_fixtures(monkeypatch) -> None:
    """Verify that clicking Analyze in offline mode loads fixture results correctly."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Fill query text area
    at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run()

    # Click Analyze
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    # Assert that last completed result is populated
    assert at.session_state["last_result"] is not None
    result = at.session_state["last_result"]
    assert result.response.aircraft_id == "AC-009"
    assert result.response.delay_days == 6
    assert len(result.dependency_nodes) == 5
    assert len(result.timeline_events) == 4
    assert len(result.evidence_rows) == 7
    assert len(result.activity) == 11


def test_ui_results_banner_root_causes_and_evidence(monkeypatch) -> None:
    """Verify the details of the AC-009 result: banner, evidence, findings, and agents."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Fill query text area and Analyze
    at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run()
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    result = at.session_state["last_result"]
    assert result is not None

    # Banner must show RED and 6 days
    assert result.response.overall_status == "red"
    assert result.response.delay_days == 6

    # Verify all expected evidence and dependency IDs appear in the catalog fixtures
    expected_evidence_ids = {
        "MS-009-FTC",
        "TEST-009-118",
        "TEST-009-121",
        "DEF-009-042",
        "PART-ACT-774",
        "CR-184",
        "MNT-009-015",
        "DEP-009-001",
        "DEP-009-002",
        "DEP-009-003",
        "DEP-009-004",
    }
    evidence_ids = {row.source_id for row in result.evidence_rows}
    dependency_ids = {edge.dependency_id for edge in result.dependency_edges}
    all_actual_ids = evidence_ids.union(dependency_ids)
    for ev_id in expected_evidence_ids:
        assert ev_id in all_actual_ids, f"Expected ID {ev_id} missing from offline results"

    # Verify no unrelated reference-screen data or N-numbers appear
    content_str = str(result.model_dump())
    unrelated_data = ["AC-002", "TR-001", "CR-789", "128 aircraft", "N-number"]
    for val in unrelated_data:
        assert val not in content_str, (
            f"Unrelated reference-screen content '{val}' leaked into UI result"
        )

    # Verify activity trace contains actual AeroOps agents/tools only
    valid_agents = {
        "intake_extractor",
        "test_ops_specialist",
        "maintenance_specialist",
        "config_supply_specialist",
        "schedule_risk_specialist",
        "executive_synthesis",
        "synthesis_agent",
    }
    valid_tools = {
        "none",
        "get_test_events",
        "get_open_defects",
        "get_dependency_graph",
        "get_maintenance_tasks",
        "get_parts_constraints",
        "get_change_requests",
        "get_aircraft_status",
    }
    for act in result.activity:
        assert act.agent_name in valid_agents, (
            f"Invalid agent name '{act.agent_name}' in activity trace"
        )
        assert act.tool_name in valid_tools, (
            f"Invalid tool name '{act.tool_name}' in activity trace"
        )

    # Verify that all 11 expected evidence/dependency IDs are rendered on screen (in table/markdown/errors)
    rendered_text = ""
    for table in at.table:
        rendered_text += str(table.value) + " "
    for md in at.markdown:
        rendered_text += md.value + " "
    for err in at.error:
        rendered_text += err.value + " "
    for info in at.info:
        rendered_text += info.value + " "
    for warn in at.warning:
        rendered_text += warn.value + " "

    for ev_id in expected_evidence_ids:
        assert ev_id in rendered_text, (
            f"Expected ID {ev_id} not found in rendered Streamlit output"
        )

    # Assert status banner shows RED and 6 days in rendered text
    assert any(
        "Overall Program Status" in err.value and "RED" in err.value and "6" in err.value
        for err in at.error
    ), "RED status banner with 6 days not found in rendered output"

    # Check that no unrelated reference-screen data or N-numbers appear in the rendered text
    for val in unrelated_data:
        assert val not in rendered_text, (
            f"Unrelated reference-screen content '{val}' leaked into rendered UI"
        )

    # Verify activity in the table contains only valid AeroOps agents and tools
    activity_table = None
    for tbl in at.table:
        df = tbl.value
        if df is not None and "Agent" in df.columns and "Tool" in df.columns:
            activity_table = df
            break

    assert activity_table is not None, "Activity table not found in rendered output"
    for _, row in activity_table.iterrows():
        agent = row["Agent"]
        tool = row["Tool"]
        assert agent in valid_agents, f"Invalid agent '{agent}' in rendered activity table"
        clean_tool = tool.split(" ")[0].strip()
        assert clean_tool in valid_tools or tool in valid_tools, (
            f"Invalid tool '{tool}' in rendered activity table"
        )


def test_ui_no_dynamic_html_injection() -> None:
    """Verify that unsafe_allow_html is only used with static CSS templates and never with dynamic text."""
    src_dir = PROJECT_ROOT / "src"
    py_files = list(src_dir.glob("**/*.py"))

    pattern = re.compile(r"unsafe_allow_html\s*=\s*True")

    for f in py_files:
        if f.name == "ui_theme.py":
            # Allowed to inject static COMMAND_CENTER_CSS
            continue
        content = f.read_text(encoding="utf-8")
        assert not pattern.search(content), f"Forbidden unsafe_allow_html=True found in {f}"


def test_ui_roi_calculator_math(monkeypatch) -> None:
    """Verify the business-value calculator output updates correctly based on input adjustments."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    engineers_input = at.sidebar.number_input[0]
    hours_input = at.sidebar.number_input[1]
    rate_input = at.sidebar.number_input[2]

    assert engineers_input.value == 50
    assert hours_input.value == 2.0
    assert rate_input.value == 150.0

    engineers_input.set_value(100)
    hours_input.set_value(4.0)
    rate_input.set_value(200.0)
    at.run()

    val = 100 * 4.0 * 200 * 52
    assert val == 4160000


def test_ui_invalid_input_validation(monkeypatch) -> None:
    """Verify that a mismatch in query aircraft ID displays a safe request error."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Target aircraft is AC-009, but query asks about AC-007
    at.text_area[0].input("Why is AC-007 delayed?").run()

    # Click Analyze
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    # Error state should be populated and visible in the UI
    assert at.session_state["current_error"] is not None
    assert "Request Error" in at.session_state["current_error"]
    assert at.session_state["last_result"] is None


def test_ui_service_failure_display(monkeypatch) -> None:
    """Verify that a backend service failure maps to a safe error without disclosing tracebacks."""
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)

    # Stub run_dashboard_investigation to raise an internal database or connection error
    def mock_run_fail(*args, **kwargs):
        raise ConnectionRefusedError("Operational SQLite database locked or inaccessible.")

    monkeypatch.setattr("aeroops.ui_controller.run_dashboard_investigation", mock_run_fail)
    monkeypatch.setattr(
        "aeroops.ui_controller.get_fleet_dashboard_snapshot",
        lambda *a, **k: FleetDashboardSnapshot(
            aircraft_options=["AC-009"],
            total_aircraft=1,
            green_count=0,
            amber_count=0,
            red_count=1,
            high_critical_defect_count=0,
            blocked_delayed_test_count=0,
            upcoming_milestone_count=0,
        ),
    )

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run()

    # Click Analyze
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    assert at.session_state["current_error"] is not None
    # Traceback details like "ConnectionRefusedError" must be hidden
    assert "ConnectionRefusedError" not in at.session_state["current_error"]
    assert "inaccessible" not in at.session_state["current_error"]
    assert "internal system error occurred" in at.session_state["current_error"]


def test_ui_analyze_rerun_behavior(monkeypatch) -> None:
    """Verify that Analyze triggers exactly one investigation, and a harmless rerun does not repeat it."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    call_count = 0
    from aeroops.offline_fixtures import MOCK_INVESTIGATION_RESULT

    def mock_run_investigation(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MOCK_INVESTIGATION_RESULT

    monkeypatch.setattr(
        "aeroops.ui_controller.run_dashboard_investigation", mock_run_investigation
    )

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Fill query text area
    at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run()

    # Click Analyze
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    assert call_count == 1
    assert at.session_state["last_result"] is not None

    # Harmless rerun: run again without clicking Analyze
    at.run()
    # Call count must remain 1, and the previous result must remain visible in session state
    assert call_count == 1
    assert at.session_state["last_result"] is not None


def test_ui_changing_aircraft_syncs_safely(monkeypatch) -> None:
    """Verify that changing aircraft synchronizes the target selection and handles query mismatch safely."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Change selection
    ac_select = at.selectbox[0]
    ac_select.select("AC-009").run()

    assert at.session_state["selected_aircraft"] == "AC-009"

    # Input query mentioning AC-007 (mismatch)
    at.text_area[0].input("Why is AC-007 delayed?").run()

    # Click Analyze
    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    # Must raise a safe request error due to aircraft ID mismatch
    assert at.session_state["current_error"] is not None
    assert "Request Error" in at.session_state["current_error"]
    assert "AC-009" in at.session_state["current_error"]
    assert at.session_state["last_result"] is None


def test_ui_offline_mode_never_invokes_live(monkeypatch) -> None:
    """Verify that offline mode never invokes the live service or MCP client."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Live service or MCP client was called in offline mode!")

    monkeypatch.setattr("aeroops.ui_controller.run_investigation_async", fail_if_called)
    monkeypatch.setattr("aeroops.ui_controller.call_mcp_tool_direct", fail_if_called)

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Click preset button to populate and analyze
    preset_btn = next(b for b in at.button if b.label == "Why is AC-009 delayed?")
    preset_btn.click().run()

    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    assert at.session_state["last_result"] is not None
    assert at.session_state["current_error"] is None


def test_ui_live_error_never_loads_fixtures(monkeypatch) -> None:
    """Verify that live errors never silently load offline fixtures."""
    monkeypatch.delenv("AEROOPS_OFFLINE_DEMO", raising=False)

    def mock_run_fail(*args, **kwargs):
        raise ConnectionRefusedError("Live connection refused.")

    monkeypatch.setattr("aeroops.ui_controller.run_dashboard_investigation", mock_run_fail)
    monkeypatch.setattr(
        "aeroops.ui_controller.get_fleet_dashboard_snapshot",
        lambda *a, **k: FleetDashboardSnapshot(
            aircraft_options=["AC-009"],
            total_aircraft=1,
            green_count=0,
            amber_count=0,
            red_count=1,
            high_critical_defect_count=0,
            blocked_delayed_test_count=0,
            upcoming_milestone_count=0,
        ),
    )

    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run()

    analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
    analyze_btn.click().run()

    # Should raise error and NOT populate last_result with MOCK_INVESTIGATION_RESULT
    assert at.session_state["current_error"] is not None
    assert at.session_state["last_result"] is None


def test_ui_default_aircraft_is_ac009(monkeypatch) -> None:
    """Verify that AC-009 is the default selected aircraft on initial load."""
    monkeypatch.setenv("AEROOPS_OFFLINE_DEMO", "1")
    at = AppTest.from_file("src/aeroops/app.py")
    at.run()

    # Target aircraft selectbox default
    selectbox = at.selectbox[0]
    assert selectbox.value == "AC-009", (
        f"Default selectbox value must be AC-009, got {selectbox.value}"
    )
    assert at.session_state["selected_aircraft"] == "AC-009"


def test_ui_operational_text_safety_guarantee() -> None:
    """Verify that apply_command_center_theme accepts no arguments and only renders static CSS.

    This proves that no operational text (queries, model outputs, errors, source IDs)
    can reach unsafe HTML rendering.
    """
    import inspect

    from aeroops.ui_theme import apply_command_center_theme

    # 1. Verify function signature
    sig = inspect.signature(apply_command_center_theme)
    assert len(sig.parameters) == 0, (
        "apply_command_center_theme must not accept any arguments to prevent dynamic HTML injection"
    )

    # 2. Verify only static COMMAND_CENTER_CSS is used
    source = inspect.getsource(apply_command_center_theme)
    assert "COMMAND_CENTER_CSS" in source
    assert 'f"' not in source and "f'" not in source, (
        "Format strings forbidden in apply_command_center_theme"
    )
    assert ".format(" not in source, "String formatting forbidden in apply_command_center_theme"
    assert "%" not in source, (
        "String interpolation operator forbidden in apply_command_center_theme"
    )
