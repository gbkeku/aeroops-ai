"""Main entry point for the AeroOps Streamlit Decision-Support UI."""

from __future__ import annotations

import asyncio

import streamlit as st

from aeroops.config import get_settings
from aeroops.ui_components import (
    render_activity,
    render_business_value,
    render_fleet_overview,
    render_header,
    render_results,
)
from aeroops.ui_controller import (
    get_fleet_dashboard_snapshot,
    run_dashboard_investigation,
)
from aeroops.ui_theme import apply_command_center_theme

# 1. Page Configuration
st.set_page_config(
    page_title="AeroOps Program Manager",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Apply Static CSS Command Center Styling
apply_command_center_theme()

# 2. Offline Detection
is_offline = get_settings().offline_demo

# 3. Session State Initialization
if "selected_aircraft" not in st.session_state:
    st.session_state.selected_aircraft = "AC-009"
if "query_text" not in st.session_state:
    st.session_state.query_text = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "current_error" not in st.session_state:
    st.session_state.current_error = None


def handle_public_error(exc: Exception) -> str:
    """Map python exceptions to safe public errors without disclosing backend paths/details."""
    from aeroops.security import (
        SecurityPolicyViolation,
        ToolAuthorizationError,
        UnsafeResponseError,
    )
    from aeroops.services import LiveInvestigationError
    from aeroops.validation import EvidenceIntegrityError

    if isinstance(exc, SecurityPolicyViolation):
        return f"Security Policy Violation: {exc}"
    elif isinstance(exc, ToolAuthorizationError):
        return "Access Denied: The multi-agent workspace is restricted to authorized tools and read-only operations."
    elif isinstance(exc, UnsafeResponseError):
        return "Safety Warning: The generated response violated security constraints."
    elif isinstance(exc, EvidenceIntegrityError):
        return "Data Integrity Error: The analysis findings could not be validated against the retrieved evidence catalog."
    elif isinstance(exc, LiveInvestigationError):
        return (
            "Live Investigation Error: The secured agent workflow could not complete. "
            f"Diagnostic stage: {exc.stage}."
        )
    elif isinstance(exc, ValueError):
        return f"Request Error: {exc}"
    elif isinstance(exc, asyncio.TimeoutError):
        return "Timeout Error: The investigation timed out before completion."
    else:
        return "An internal system error occurred. The investigation was closed safely."


# 4. Render Layout
render_header(is_offline=is_offline)

# Fetch Fleet metrics
try:
    snapshot = get_fleet_dashboard_snapshot()
    render_fleet_overview(snapshot)
    aircraft_options = snapshot.aircraft_options
except Exception as exc:
    st.error(f"Failed to fetch fleet dashboard overview: {handle_public_error(exc)}")
    aircraft_options = ["AC-009"]

# 5. Workspace Layout - Bordered Command Panel
st.write("---")
st.markdown("### 🛠️ Investigation Workspace")

with st.container(border=True):
    col_ac, col_preset = st.columns([1, 2])

    with col_preset:
        st.write("**Presets**")
        if st.button("Why is AC-009 delayed?", help="Preset natural-language query for AC-009"):
            st.session_state.selected_aircraft = "AC-009"
            st.session_state.target_ac_select = "AC-009"
            st.session_state.query_text = "Why is AC-009 delayed? Produce an executive brief."
            st.session_state.query_text_area = "Why is AC-009 delayed? Produce an executive brief."
            st.session_state.last_result = None
            st.session_state.current_error = None

    with col_ac:
        # Selected aircraft sync
        aircraft_idx = 0
        if st.session_state.selected_aircraft in aircraft_options:
            aircraft_idx = aircraft_options.index(st.session_state.selected_aircraft)

        selected_ac = st.selectbox(
            "Target Aircraft Selector",
            options=aircraft_options,
            index=aircraft_idx,
            key="target_ac_select",
        )
        st.session_state.selected_aircraft = selected_ac

    # NL Query Box
    query_box = st.text_area(
        "Natural-Language Investigation Query",
        value=st.session_state.query_text,
        key="query_text_area",
        help="Enter details for the aircraft program investigation. ID must match selection.",
    )
    st.session_state.query_text = query_box

    # Analyze Button (Styled wide)
    if st.button("🚀 Analyze", type="primary", width="stretch"):
        st.session_state.last_result = None
        st.session_state.current_error = None

        if not st.session_state.query_text.strip():
            st.session_state.current_error = "Request Error: Query text cannot be empty."
        else:
            with st.spinner("Executing multi-agent investigation service..."):
                try:
                    result = run_dashboard_investigation(
                        query=st.session_state.query_text,
                        aircraft_id=st.session_state.selected_aircraft,
                    )
                    st.session_state.last_result = result
                except Exception as exc:
                    st.session_state.current_error = handle_public_error(exc)

# 6. Render Results & Errors
if st.session_state.current_error:
    st.error(st.session_state.current_error)

if st.session_state.last_result:
    render_results(st.session_state.last_result)
    render_activity(st.session_state.last_result.activity)

# 7. Render Sidebar Calculator
render_business_value()
