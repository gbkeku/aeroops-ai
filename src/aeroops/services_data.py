"""Data-layer service helpers for AeroOps MCP aggregations.

This module contains functions that aggregate and process data using the
official database repository layer.  It is separate from the investigation
service (``services.py``) to avoid circular imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aeroops.db import repository


def get_fleet_summary_data(db_path: Path | str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Retrieve summarized fleet metrics and associated source record IDs.

    Args:
        db_path: Optional path to the database.

    Returns:
        A tuple of (summary_dict, source_refs_list).
    """
    aircraft_list = repository.list_aircraft(db_path=db_path)

    status_counts = {"green": 0, "amber": 0, "red": 0}
    aircraft_ids = []
    for ac in aircraft_list:
        status_counts[ac.status] = status_counts.get(ac.status, 0) + 1
        aircraft_ids.append(ac.source_id)

    open_defects_ids = []
    blocked_tests_ids = []
    outstanding_parts_ids = []
    pending_crs_ids = []

    high_crit_defects_count = 0
    blocked_delayed_tests_count = 0
    upcoming_milestones_count = 0

    for ac_id in aircraft_ids:
        # Get open defects
        defects = repository.get_defects(aircraft_id=ac_id, status="open", db_path=db_path)
        open_defects_ids.extend([d.source_id for d in defects])
        high_crit_defects_count += sum(1 for d in defects if d.severity in ("high", "critical"))

        # Get blocked test events
        tests = repository.get_test_events(aircraft_id=ac_id, status="blocked", db_path=db_path)
        blocked_tests_ids.extend([t.source_id for t in tests])

        # Get blocked or aborted test events
        all_tests = repository.get_test_events(aircraft_id=ac_id, db_path=db_path)
        blocked_delayed_tests_count += sum(
            1 for t in all_tests if t.status in ("blocked", "aborted")
        )

        # Get upcoming milestones (status != 'complete')
        milestones = repository.get_milestones(aircraft_id=ac_id, db_path=db_path)
        upcoming_milestones_count += sum(1 for m in milestones if m.status != "complete")

        # Get outstanding parts constraints (awaiting_delivery or delayed)
        parts = repository.get_parts_constraints(aircraft_id=ac_id, db_path=db_path)
        outstanding_parts_ids.extend(
            [p.source_id for p in parts if p.status in ("awaiting_delivery", "delayed")]
        )

        # Get pending change requests (pending_review)
        crs = repository.get_change_requests(aircraft_id=ac_id, db_path=db_path)
        pending_crs_ids.extend([c.source_id for c in crs if c.status == "pending_review"])

    summary = {
        "total_aircraft": len(aircraft_list),
        "status_counts": status_counts,
        "total_open_defects": len(open_defects_ids),
        "total_blocked_tests": len(blocked_tests_ids),
        "total_outstanding_parts_constraints": len(outstanding_parts_ids),
        "total_pending_change_requests": len(pending_crs_ids),
        "total_high_critical_defects": high_crit_defects_count,
        "total_blocked_delayed_tests": blocked_delayed_tests_count,
        "total_upcoming_milestones": upcoming_milestones_count,
    }

    # Aggregate and deduplicate source references
    source_refs = sorted(
        list(
            set(
                aircraft_ids
                + open_defects_ids
                + blocked_tests_ids
                + outstanding_parts_ids
                + pending_crs_ids
            )
        )
    )

    return summary, source_refs
