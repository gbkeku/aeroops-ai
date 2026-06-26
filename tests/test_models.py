"""Unit tests for Pydantic domain models in AeroOps."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from aeroops.models import (
    Aircraft,
    Defect,
    Milestone,
    ScheduleDependency,
    TestEvent,
)


def test_aircraft_validation() -> None:
    """Test Aircraft model validation rules."""
    now = datetime.now(UTC)

    # Valid construction
    ac = Aircraft(
        source_id="AC-009",
        name="Avionics Testbed",
        status="red",
        responsible_org="Flight Controls",
        created_at=now,
        updated_at=now,
        synthetic_data=True,
    )
    assert ac.source_id == "AC-009"

    # Invalid ID
    with pytest.raises(ValidationError):
        Aircraft(
            source_id="AC-99",
            name="Bad ID",
            status="red",
            responsible_org="Flight Controls",
            created_at=now,
            updated_at=now,
        )

    # Invalid Status
    with pytest.raises(ValidationError):
        Aircraft(
            source_id="AC-009",
            name="Bad Status",
            status="blue",
            responsible_org="Flight Controls",
            created_at=now,
            updated_at=now,
        )

    # Invalid timestamps
    past = datetime(2026, 1, 1, tzinfo=UTC)
    future = datetime(2026, 1, 2, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Aircraft(
            source_id="AC-009",
            name="Bad Timestamps",
            status="red",
            responsible_org="Flight Controls",
            created_at=future,
            updated_at=past,
        )


def test_milestone_variance() -> None:
    """Test Milestone schedule variance calculation."""
    now = datetime.now(UTC)
    ms = Milestone(
        source_id="MS-009-FTC",
        aircraft_id="AC-009",
        name="FTC",
        planned_date=date(2026, 6, 29),
        forecast_date=date(2026, 7, 5),
        status="at_risk",
        responsible_role="Program Manager",
        created_at=now,
        updated_at=now,
    )
    assert ms.variance_days == 6


def test_defect_validation() -> None:
    """Test Defect chronological constraints."""
    datetime.now(UTC)
    past = datetime(2026, 6, 23, 10, 0, 0, tzinfo=UTC)
    future = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)

    # Discovered after closed should fail
    with pytest.raises(ValidationError):
        Defect(
            source_id="DEF-009-042",
            aircraft_id="AC-009",
            title="Actuator dev",
            description="Dev",
            severity="high",
            status="closed",
            discovered_at=future,
            closed_at=past,
            responsible_role="Engineer",
            created_at=past,
            updated_at=future,
        )


def test_test_event_validation() -> None:
    """Test TestEvent chronological constraints."""
    datetime.now(UTC)
    past = datetime(2026, 6, 23, 10, 0, 0, tzinfo=UTC)
    future = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)

    # Started after completed should fail
    with pytest.raises(ValidationError):
        TestEvent(
            source_id="TEST-009-118",
            aircraft_id="AC-009",
            name="Taxi",
            status="aborted",
            responsible_role="Conductor",
            scheduled_date=date(2026, 6, 23),
            started_at=future,
            completed_at=past,
            created_at=past,
            updated_at=future,
        )


def test_schedule_dependency_blockers() -> None:
    """Test ScheduleDependency XOR validation on blockers."""
    now = datetime.now(UTC)

    # Valid: exactly one blocker
    dep = ScheduleDependency(
        source_id="DEP-009-001",
        aircraft_id="AC-009",
        blocked_test_id="TEST-009-121",
        blocker_defect_id="DEF-009-042",
        created_at=now,
        updated_at=now,
    )
    assert dep.blocker_defect_id == "DEF-009-042"

    # Invalid: no blocker
    with pytest.raises(ValidationError):
        ScheduleDependency(
            source_id="DEP-009-001",
            aircraft_id="AC-009",
            blocked_test_id="TEST-009-121",
            created_at=now,
            updated_at=now,
        )

    # Invalid: multiple blockers
    with pytest.raises(ValidationError):
        ScheduleDependency(
            source_id="DEP-009-001",
            aircraft_id="AC-009",
            blocked_test_id="TEST-009-121",
            blocker_defect_id="DEF-009-042",
            blocker_parts_constraint_id="PART-ACT-774",
            created_at=now,
            updated_at=now,
        )
