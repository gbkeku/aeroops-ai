#!/usr/bin/env python3
"""Offline Streamlit smoke test for AeroOps CI.

Uses Streamlit AppTest to verify that the app initializes and runs in offline
mode without external dependencies or live database access.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Run the Streamlit smoke test."""
    print("Setting AEROOPS_OFFLINE_DEMO=1 for smoke test...")
    os.environ["AEROOPS_OFFLINE_DEMO"] = "1"

    # Avoid loading actual user or dev .env file during CI run
    os.environ["AEROOPS_DB_PATH"] = "data/aeroops.db"

    try:
        from streamlit.testing.v1 import AppTest

        from aeroops.config import get_settings

        get_settings.cache_clear()
        print("Initializing Streamlit AppTest from src/aeroops/app.py...")
        at = AppTest.from_file("src/aeroops/app.py")

        print("Running AppTest...")
        at.run(timeout=30)

        # 1. Verify offline preview banner is present
        print("Verifying warnings/banners...")
        warnings = [w.value for w in at.warning]
        print(f"Banners found: {warnings}")
        assert any("Offline Preview" in w for w in warnings), (
            "Offline Preview warning banner was not found!"
        )

        # 2. Verify selected aircraft default state
        assert at.session_state["selected_aircraft"] == "AC-009", (
            "Default selected aircraft should be AC-009"
        )

        # 3. Input query and click the Analyze button to verify mock result triggers
        print("Inputting query and triggering investigation analysis...")
        at.text_area[0].input("Why is AC-009 delayed? Produce an executive brief.").run(timeout=30)
        analyze_btn = next(b for b in at.button if b.label == "🚀 Analyze")
        analyze_btn.click().run(timeout=30)

        # 4. Verify results are loaded
        result = at.session_state["last_result"]
        assert result is not None, "Investigation result should be populated in session state"
        assert result.response.aircraft_id == "AC-009", "Mock result aircraft ID should be AC-009"
        assert result.response.delay_days == 6, "Mock result delay should be 6 days"

        print("Offline Streamlit smoke test completed successfully!")
        sys.exit(0)
    except Exception as exc:
        print(f"[ERROR] Streamlit smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
