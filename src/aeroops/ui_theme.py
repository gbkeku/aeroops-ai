"""Aerospace operations command-center dashboard theme and static CSS.

Provides module-level constant stylesheets and styling helper classes to inject
the dark slate and aerospace blue aesthetic without dynamic string interpolation.
"""

from __future__ import annotations

import streamlit as st

# 100% Static CSS string - absolutely no string interpolation of user or dynamic text.
COMMAND_CENTER_CSS = """
<style>
/* Base typography and background hints */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #0B0E14 !important;
}

/* Custom dashboard card styling */
div.stMetric, div[data-testid="metric-container"] {
    background-color: #151B26 !important;
    border: 1px solid #232D3F !important;
    border-radius: 6px !important;
    padding: 12px 16px !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2) !important;
}

/* Sidebar styling overrides */
section[data-testid="stSidebar"] {
    background-color: #0E121A !important;
    border-right: 1px solid #1D2433 !important;
}

/* Command-panel and callout blocks */
div.stAlert, div[data-testid="stNotificationContent"] {
    border-radius: 6px !important;
}

/* Custom styles for tables */
div[data-testid="stTable"] table {
    background-color: #151B26 !important;
    border: 1px solid #232D3F !important;
    color: #FFFFFF !important;
    border-radius: 6px !important;
}

div[data-testid="stTable"] th {
    background-color: #1E2638 !important;
    color: #00A3FF !important;
    font-weight: bold !important;
}

div[data-testid="stTable"] td {
    border-bottom: 1px solid #1D2433 !important;
}

/* Scrollbar styling for high-tech dashboards */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}
::-webkit-scrollbar-track {
    background: #0B0E14;
}
::-webkit-scrollbar-thumb {
    background: #232D3F;
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
    background: #00A3FF;
}

/* Spacing and divider polish */
hr {
    border-color: #1D2433 !important;
    margin-top: 1.5rem !important;
    margin-bottom: 1.5rem !important;
}
</style>
"""


def apply_command_center_theme() -> None:
    """Inject the static aerospace operations dashboard CSS stylesheet safely.

    Strictly uses constant styling. No dynamic inputs are processed.
    """
    st.markdown(COMMAND_CENTER_CSS, unsafe_allow_html=True)
