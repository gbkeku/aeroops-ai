"""UI rendering components for the AeroOps Streamlit application.

Contains modular layout functions for the header, fleet overview,
investigation workspace, results, agent activities, and business value panel.
"""

from __future__ import annotations

import streamlit as st

from aeroops.security import AEROOPS_DISCLAIMER
from aeroops.ui_controller import build_dependency_dot
from aeroops.ui_models import (
    DashboardInvestigationResult,
    FleetDashboardSnapshot,
    SafeAgentActivity,
)


def render_header(is_offline: bool = False) -> None:
    """Render the main header with branding, badges, and disclaimer warnings."""
    st.title("✈️ AeroOps")
    st.subheader("Multi-Agent Aircraft Program Operations Manager")

    # Synthetic data label
    st.info("ℹ️ **Dataset Note**: Running on synthetic demonstration data only.")  # noqa: RUF001

    if is_offline:
        st.warning(
            "⚠️ **Offline Preview**: Bypassing ADK, MCP, and live LLM endpoints. Using saved local fixtures."
        )

    # Decision-support disclaimer
    st.caption(f"**Disclaimer**: {AEROOPS_DISCLAIMER}")


def render_fleet_overview(snapshot: FleetDashboardSnapshot) -> None:
    """Render high-level fleet metrics using responsive columns."""
    st.write("---")
    st.markdown("### 📊 Fleet Operations Overview")

    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)

    with col1:
        st.metric(label="Total Aircraft", value=str(snapshot.total_aircraft))
        st.caption("✈️ Active fleet size")

    with col2:
        st.metric(label="Green Readiness", value=str(snapshot.green_count))
        st.caption("🟢 GREEN | On-track")

    with col3:
        st.metric(label="Amber Readiness", value=str(snapshot.amber_count))
        st.caption("🟡 AMBER | Caution")

    with col4:
        st.metric(label="Red Readiness", value=str(snapshot.red_count))
        st.caption("🔴 RED | Blocked")

    with col5:
        st.metric(
            label="High/Critical Defects",
            value=str(snapshot.high_critical_defect_count),
            help="Open defects with High or Critical severity",
        )
        st.caption("🚨 Open blocker items")

    with col6:
        st.metric(
            label="Blocked/Delayed Tests",
            value=str(snapshot.blocked_delayed_test_count),
            help="Test events in Blocked or Aborted status",
        )
        st.caption("🧪 Blocked test events")

    with col7:
        st.metric(
            label="Upcoming Milestones",
            value=str(snapshot.upcoming_milestone_count),
            help="Key milestone events that are not yet Complete",
        )
        st.caption("🏁 Pending gates")


def render_results(result: DashboardInvestigationResult) -> None:
    """Render the structured investigation results and executive brief."""
    st.write("---")
    st.markdown("### 🔍 Investigation Results")

    res = result.response
    status_upper = res.overall_status.upper()
    emoji = {"RED": "🔴", "AMBER": "🟡", "GREEN": "🟢"}.get(status_upper, "⚪")
    banner_text = f"{emoji} **Overall Program Status**: {status_upper} | **Schedule Variance**: {res.delay_days} days"

    if status_upper == "RED":
        st.error(banner_text)
    elif status_upper == "AMBER":
        st.warning(banner_text)
    elif status_upper == "GREEN":
        st.success(banner_text)
    else:
        st.info(banner_text)

    # Executive Brief Card
    st.markdown("#### ℹ️ Executive Brief Summary")  # noqa: RUF001
    with st.container(border=True):
        st.write(res.executive_summary)
        st.write("---")
        col_planned, col_forecast, col_variance, col_confidence = st.columns(4)
        with col_planned:
            st.markdown(f"**Planned Date**\n\n{res.planned_milestone_date}")
        with col_forecast:
            st.markdown(f"**Forecast Date**\n\n{res.forecast_milestone_date}")
        with col_variance:
            st.markdown(f"**Schedule Variance**\n\n{res.delay_days} days")
        with col_confidence:
            st.markdown(f"**Confidence Indicator**\n\n🎯 {res.confidence.upper()}")

    # Confirmed Root Causes & Contributing Factors (Responsive two columns)
    st.markdown("### 🛠️ Investigation Breakdown")
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### 🚨 Confirmed Root Causes")
        if not res.confirmed_root_causes:
            st.info("No primary root causes confirmed.")
        for i, cause in enumerate(res.confirmed_root_causes):
            with st.container(border=True):
                st.markdown(f"**Root Cause {i + 1}: {cause.statement}**")
                st.markdown(f"**Classification**: `{cause.classification}`")
                st.markdown(f"**Rationale**: {cause.rationale}")
                refs = [ref.source_id for ref in cause.source_refs]
                st.markdown(f"**Supporting Source IDs**: {', '.join(refs)}")
                st.markdown(f"**Evidence Count**: {len(cause.source_refs)}")

    with col_right:
        st.markdown("#### ⚠️ Contributing Factors")
        if not res.contributing_factors:
            st.info("No secondary contributing factors identified.")
        for i, factor in enumerate(res.contributing_factors):
            with st.container(border=True):
                st.markdown(f"**Contributing Factor {i + 1}: {factor.statement}**")
                st.markdown(f"**Classification**: `{factor.classification}`")
                st.markdown(f"**Rationale**: {factor.rationale}")
                refs = [ref.source_id for ref in factor.source_refs]
                st.markdown(f"**Supporting Source IDs**: {', '.join(refs)}")
                st.markdown(f"**Evidence Count**: {len(factor.source_refs)}")

    # Recommended Action Table
    st.markdown("### 📋 Recommended Actions")
    if not res.recommended_actions:
        st.info("No recommended actions.")
    else:
        action_data = []
        for act in res.recommended_actions:
            findings_str = ", ".join(act.supporting_finding_ids)
            evidence_str = ", ".join([ref.source_id for ref in act.source_refs])
            action_data.append(
                {
                    "Action": act.action,
                    "Owner": act.owner_role,
                    "Suggested Due Date": str(act.suggested_due_date),
                    "Rationale": act.rationale,
                    "Supporting Findings": findings_str,
                    "Evidence": evidence_str,
                    "Priority": act.classification,
                }
            )
        st.table(action_data)

    # Dependency Graph & Tabular Fallback
    st.markdown("### 🕸️ Schedule Dependency Graph")
    if not result.dependency_nodes:
        st.info("No dependency data to graph.")
    else:
        dot_src = build_dependency_dot(result.dependency_nodes, result.dependency_edges)
        st.graphviz_chart(dot_src, width="stretch")

        # Tabular fallback underneath the graph for accessibility
        with st.expander("👁️ View Tabular Dependency Fallback", expanded=False):
            st.write("**Dependency Edges**")
            if not result.dependency_edges:
                st.write("_No direct blockers._")
            else:
                edge_data = [
                    {
                        "Dependency ID": e.dependency_id,
                        "Blocker ID": e.source_id,
                        "Blocked Test ID": e.target_id,
                        "Blocker Type": e.relationship,
                    }
                    for e in result.dependency_edges
                ]
                st.table(edge_data)

            st.write("**Graph Nodes Status**")
            node_data = [
                {
                    "Source ID": n.source_id,
                    "Type": n.record_type,
                    "Label": n.label,
                    "Status": n.status,
                }
                for n in result.dependency_nodes
            ]
            st.table(node_data)

    # Milestone & Event Timeline
    st.markdown("### 📅 Program Timeline")
    if not result.timeline_events:
        st.info("No timeline events to display.")
    else:
        timeline_data = [
            {
                "Source ID": ev.source_id,
                "Event Date": ev.date,
                "Event Type": ev.event_type.upper(),
                "Title": ev.title,
                "Status": ev.status.upper(),
            }
            for ev in result.timeline_events
        ]
        st.table(timeline_data)

    # Evidence Verification Table
    st.markdown("### 🔍 Evidence Verification")
    if not result.evidence_rows:
        st.info("No evidence records cited.")
    else:
        evidence_data = [
            {
                "Source ID": r.source_id,
                "Record Type": r.record_type.upper(),
                "Title": r.title,
                "Status": r.status.upper(),
                "Relevant Date": r.relevant_date,
                "Originating Specialist": r.originating_agent,
            }
            for r in result.evidence_rows
        ]
        st.table(evidence_data)

    # Assumptions, Unknowns, and Safety Notice
    st.write("---")
    col_asm, col_unk = st.columns(2)
    with col_asm:
        st.markdown("##### 💡 Assumptions")
        if not res.assumptions:
            st.caption("No explicit assumptions noted.")
        else:
            for asm in res.assumptions:
                st.caption(f"- {asm}")

    with col_unk:
        st.markdown("##### ❓ Unknowns")
        if not res.unknowns:
            st.caption("No material unknowns noted.")
        else:
            for unk in res.unknowns:
                st.caption(f"- {unk}")

    st.caption(f"**Decision-Support Disclaimer**: {res.security_notice}")
    st.caption("**Dataset Note**: This demonstration operates entirely on synthetic data.")


def render_activity(activities: list[SafeAgentActivity]) -> None:
    """Render agent activity logs and execution durations, keeping reasoning hidden."""
    st.write("---")
    st.markdown("### 🤖 Multi-Agent Activity")

    if not activities:
        st.info("No agent activities recorded for this run.")
        return

    # Render a list of agent execution durations
    trace_data = []
    for act in activities:
        trace_data.append(
            {
                "Agent": act.agent_name,
                "Tool": act.tool_name,
                "Duration": f"{act.duration_ms:.2f} ms",
                "Status": "✅ Succeeded" if act.succeeded else "❌ Failed",
                "Source References": act.source_ref_count,
            }
        )

    st.table(trace_data)


def render_business_value() -> None:
    """Render interactive ROI savings calculator inside Streamlit session."""
    st.sidebar.write("---")
    st.sidebar.markdown("### 💡 Business Value Calculator")
    st.sidebar.caption("These inputs are user-defined assumptions, not measured project results.")

    # Non-negative inputs with sensible defaults
    engineers = st.sidebar.number_input(
        "Number of Engineers",
        min_value=0,
        max_value=1000,
        value=50,
        step=1,
        help="Total program engineers affected",
    )
    hours_saved = st.sidebar.number_input(
        "Coordination Hours Saved / Eng / Week",
        min_value=0.0,
        max_value=40.0,
        value=2.0,
        step=0.5,
        help="Estimated hours saved per engineer per week",
    )
    hourly_rate = st.sidebar.number_input(
        "Loaded Hourly Cost ($)",
        min_value=0.0,
        max_value=500.0,
        value=150.0,
        step=5.0,
        help="Fully loaded hourly engineer rate",
    )

    # Transparent ROI calculation formula
    annual_savings = engineers * hours_saved * hourly_rate * 52

    # Labeled as user assumptions inside a bordered highlight card
    with st.sidebar.container(border=True):
        st.markdown(f"**Illustrative Annual Savings**\n\n### ${annual_savings:,.2f}")
        st.caption("This estimate is illustrative and is not a measured AeroOps outcome.")
