"""Unit and integration tests for EvidenceCatalog, callbacks, and validate_brief.

This module covers all requested verification checks for callbacks, catalogs,
findings, claims, recommendations, briefs, and lifecycle/architecture.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aeroops.models import (
    ChangeRequestPendingClaim,
    DefectBlocksTestClaim,
    DependencyBlocksTestClaim,
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceRef,
    ExecutiveBrief,
    Finding,
    MaintenanceRequiredClaim,
    MilestoneDelayedClaim,
    PartArrivesAfterNeedDateClaim,
    RecommendedAction,
    RecordType,
    TestAbortedClaim,
)
from aeroops.validation import (
    EvidenceCatalog,
    EvidenceRecordConflictError,
    validate_brief,
)


# ---------------------------------------------------------------------------
# Fixture Builders
# ---------------------------------------------------------------------------
def _ref(source_id: str, record_type: str = "test_event") -> EvidenceRef:
    return EvidenceRef(source_id=source_id, record_type=record_type, summary="test ref")


def _finding(
    finding_id: str = "FIND-TEST-001",
    statement: str = "Test finding",
    classification: str = "test_failure",
    refs: list[EvidenceRef] | None = None,
    rationale: str = "Supported by evidence.",
    claims: list | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        statement=statement,
        classification=classification,  # type: ignore[arg-type]
        source_refs=refs if refs is not None else [_ref("TEST-009-118")],
        rationale=rationale,
        claims=claims or [],
    )


def _action(
    action_id: str = "ACT-001",
    action: str = "Take corrective action",
    classification: str = "maintenance",
    supporting_finding_ids: list[str] | None = None,
    refs: list[EvidenceRef] | None = None,
    owner_role: str = "maintenance_lead",
    rationale: str = "Resolves the root cause.",
    suggested_due_date: str = "2026-06-29",
) -> RecommendedAction:
    return RecommendedAction(
        action_id=action_id,
        action=action,
        classification=classification,  # type: ignore[arg-type]
        supporting_finding_ids=supporting_finding_ids
        if supporting_finding_ids is not None
        else ["FIND-TEST-001"],
        source_refs=refs if refs is not None else [_ref("TEST-009-118")],
        rationale=rationale,
        owner_role=owner_role,  # type: ignore[arg-type]
        suggested_due_date=suggested_due_date,
    )


def _brief(
    aircraft_id: str = "AC-009",
    root_causes: list[Finding] | None = None,
    contributing: list[Finding] | None = None,
    actions: list[RecommendedAction] | None = None,
    planned: str = "2026-06-29",
    forecast: str = "2026-07-05",
    delay_days: int = 6,
    milestone_source_id: str = "MS-009-FTC",
    evidence: list[str] | None = None,
) -> ExecutiveBrief:
    ref = _ref("TEST-009-118")
    default_finding = _finding(refs=[ref])
    default_action = _action(refs=[ref])

    expected_evidence = evidence
    if expected_evidence is None:
        expected_evidence = sorted(["TEST-009-118", milestone_source_id])

    return ExecutiveBrief(
        aircraft_id=aircraft_id,
        overall_status="red",
        planned_milestone_date=date.fromisoformat(planned),
        forecast_milestone_date=date.fromisoformat(forecast),
        delay_days=delay_days,
        executive_summary="Test summary.",
        confirmed_root_causes=root_causes if root_causes is not None else [default_finding],
        contributing_factors=contributing if contributing is not None else [],
        recommended_actions=actions if actions is not None else [default_action],
        assumptions=["All test data is synthetic."],
        unknowns=[],
        confidence="high",
        milestone_source_id=milestone_source_id,
        evidence=expected_evidence,
    )


# ---------------------------------------------------------------------------
# Callback Capture Tests
# ---------------------------------------------------------------------------
class TestCallbackCapture:
    """Callback capture checks."""

    def test_callback_structure_and_safe_append(self) -> None:
        from aeroops.agent import make_after_tool_callback

        class MockTool:
            name = "get_test_events"

        class MockContext:
            invocation_id = "inv-123"
            agent_name = "test_ops_specialist"

            def __init__(self) -> None:
                self.state: dict = {}

        tool = MockTool()
        ctx = MockContext()
        args = {"aircraft_id": "AC-009"}
        response = {"data": [], "source_refs": []}

        cb = make_after_tool_callback("test_ops_mcp_evidence")
        res = cb(tool, args, ctx, response)

        # callback returns None so response is unchanged
        assert res is None

        # Verify key exists and records correctly
        ev = ctx.state["test_ops_mcp_evidence"]
        assert len(ev) == 1
        assert ev[0]["tool_name"] == "get_test_events"
        assert ev[0]["sequence"] == 1
        assert ev[0]["invocation_id"] == "inv-123"


# ---------------------------------------------------------------------------
# Catalog and Conflict Tests
# ---------------------------------------------------------------------------
class TestCatalogAndConflict:
    """Catalog verification."""

    def test_merge_provenance_on_identical_payloads(self) -> None:
        cat = EvidenceCatalog()
        rec1 = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "aborted", "name": "Taxi test"},
            provenance=[
                EvidenceProvenance(
                    originating_agent="test_ops",
                    originating_stage="test_ops_mcp_evidence",
                    originating_tool="get_test_events",
                    invocation_id="inv-1",
                    branch_key="test_ops",
                    branch_sequence=1,
                )
            ],
        )
        rec2 = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "aborted", "name": "Taxi test"},
            provenance=[
                EvidenceProvenance(
                    originating_agent="schedule_risk",
                    originating_stage="schedule_risk_mcp_evidence",
                    originating_tool="get_dependency_graph",
                    invocation_id="inv-2",
                    branch_key="schedule_risk",
                    branch_sequence=1,
                )
            ],
        )

        cat.add_record(rec1)
        cat.add_record(rec2)

        # Should merge provenance
        assert len(cat.records["TEST-009-118"].provenance) == 2

    def test_richer_payload_replaces_dependency_graph_summary(self) -> None:
        cat = EvidenceCatalog()
        graph_node = EvidenceRecord(
            source_id="PART-ACT-774",
            record_type=RecordType.PARTS_CONSTRAINT,
            aircraft_id="AC-009",
            payload={
                "id": "PART-ACT-774",
                "aircraft_id": "AC-009",
                "name_or_title": "Actuator assembly",
                "status": "awaiting_delivery",
            },
        )
        full_record = EvidenceRecord(
            source_id="PART-ACT-774",
            record_type=RecordType.PARTS_CONSTRAINT,
            aircraft_id="AC-009",
            payload={
                "source_id": "PART-ACT-774",
                "aircraft_id": "AC-009",
                "description": "Actuator assembly",
                "status": "awaiting_delivery",
                "needed_by": "2026-06-27",
                "estimated_arrival": "2026-06-30",
                "responsible_org": "Procurement",
            },
        )

        cat.add_record(graph_node)
        cat.add_record(full_record)

        stored = cat.records["PART-ACT-774"].payload
        assert stored["needed_by"] == "2026-06-27"
        assert stored["estimated_arrival"] == "2026-06-30"

    def test_conflicting_payloads_raise_conflict_error(self) -> None:
        cat = EvidenceCatalog()
        rec1 = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "aborted"},
        )
        rec2 = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "completed"},
        )

        cat.add_record(rec1)
        with pytest.raises(EvidenceRecordConflictError):
            cat.add_record(rec2)


# ---------------------------------------------------------------------------
# Findings Tests
# ---------------------------------------------------------------------------
class TestFindingsValidation:
    """Findings checks."""

    def test_duplicate_finding_ids_fail(self) -> None:
        cat = EvidenceCatalog()
        cat.records["TEST-009-118"] = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={},
        )
        cat.records["MS-009-FTC"] = EvidenceRecord(
            source_id="MS-009-FTC",
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": "2026-06-29", "forecast_date": "2026-07-05"},
        )
        cat.specialist_source_ids.add("TEST-009-118")

        f1 = _finding(finding_id="FIND-TEST-001", statement="Stmt 1")
        f2 = _finding(finding_id="FIND-TEST-001", statement="Stmt 2")

        brief = _brief(root_causes=[f1, f2])
        report = validate_brief(brief, cat)

        assert not report.passed
        assert any(v.code == "DUPLICATE_FINDING_ID" for v in report.violations)

    def test_duplicate_statements_fail(self) -> None:
        cat = EvidenceCatalog()
        cat.records["TEST-009-118"] = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={},
        )
        cat.records["MS-009-FTC"] = EvidenceRecord(
            source_id="MS-009-FTC",
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": "2026-06-29", "forecast_date": "2026-07-05"},
        )
        cat.specialist_source_ids.add("TEST-009-118")

        f1 = _finding(finding_id="FIND-TEST-001", statement="Duplicate statement")
        f2 = _finding(finding_id="FIND-TEST-002", statement="Duplicate statement")

        brief = _brief(root_causes=[f1, f2], evidence=["TEST-009-118", "MS-009-FTC"])
        report = validate_brief(brief, cat)

        assert not report.passed
        assert any(v.code == "DUPLICATE_FINDING_STATEMENT" for v in report.violations)


# ---------------------------------------------------------------------------
# Claims Tests
# ---------------------------------------------------------------------------
class TestClaimsValidation:
    """Claims semantic verification checks."""

    @pytest.fixture
    def setup_catalog(self) -> EvidenceCatalog:
        cat = EvidenceCatalog()
        cat.records["TEST-009-118"] = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "aborted"},
        )
        cat.records["DEF-009-042"] = EvidenceRecord(
            source_id="DEF-009-042",
            record_type=RecordType.DEFECT,
            aircraft_id="AC-009",
            payload={"status": "open", "severity": "high"},
        )
        cat.records["DEP-009-001"] = EvidenceRecord(
            source_id="DEP-009-001",
            record_type=RecordType.SCHEDULE_DEPENDENCY,
            aircraft_id="AC-009",
            payload={
                "blocked_test_id": "TEST-009-121",
                "blocker_defect_id": "DEF-009-042",
            },
        )
        cat.records["TEST-009-121"] = EvidenceRecord(
            source_id="TEST-009-121",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={"status": "blocked"},
        )
        cat.records["PART-ACT-774"] = EvidenceRecord(
            source_id="PART-ACT-774",
            record_type=RecordType.PARTS_CONSTRAINT,
            aircraft_id="AC-009",
            payload={"needed_by": "2026-06-27", "estimated_arrival": "2026-06-30"},
        )
        cat.records["CR-184"] = EvidenceRecord(
            source_id="CR-184",
            record_type=RecordType.CHANGE_REQUEST,
            aircraft_id="AC-009",
            payload={"status": "pending_review"},
        )
        cat.records["MNT-009-015"] = EvidenceRecord(
            source_id="MNT-009-015",
            record_type=RecordType.MAINTENANCE_TASK,
            aircraft_id="AC-009",
            payload={"status": "scheduled"},
        )
        cat.records["MS-009-FTC"] = EvidenceRecord(
            source_id="MS-009-FTC",
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": "2026-06-29", "forecast_date": "2026-07-05"},
        )

        # Populate preflight/specialist sets
        cat.retrieved_source_ids |= set(cat.records.keys())
        cat.specialist_source_ids |= {
            "TEST-009-118",
            "DEF-009-042",
            "DEP-009-001",
            "PART-ACT-774",
            "CR-184",
            "MNT-009-015",
        }
        cat.approved_preflight_source_ids |= {"MS-009-FTC"}
        return cat

    def test_test_aborted_claim(self, setup_catalog) -> None:
        # Positive case
        f = _finding(
            refs=[_ref("TEST-009-118")],
            claims=[TestAbortedClaim(test_event_id="TEST-009-118")],
        )
        brief = _brief(root_causes=[f])
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case
        setup_catalog.records["TEST-009-118"].payload["status"] = "completed"
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_defect_blocks_test_claim(self, setup_catalog) -> None:
        f = _finding(
            refs=[_ref("DEF-009-042"), _ref("TEST-009-121")],
            claims=[DefectBlocksTestClaim(defect_id="DEF-009-042", test_event_id="TEST-009-121")],
        )
        # Positive case
        brief = _brief(
            root_causes=[f], evidence=["TEST-009-118", "DEF-009-042", "TEST-009-121", "MS-009-FTC"]
        )
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - remove dependency record
        del setup_catalog.records["DEP-009-001"]
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_part_arrives_after_need_date_claim(self, setup_catalog) -> None:
        f = _finding(
            refs=[_ref("PART-ACT-774")],
            claims=[PartArrivesAfterNeedDateClaim(parts_constraint_id="PART-ACT-774")],
        )
        # Positive case
        brief = _brief(root_causes=[f], evidence=["TEST-009-118", "PART-ACT-774", "MS-009-FTC"])
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - change arrival to before needed
        setup_catalog.records["PART-ACT-774"].payload["estimated_arrival"] = "2026-06-26"
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_change_request_pending_claim(self, setup_catalog) -> None:
        f = _finding(
            refs=[_ref("CR-184")],
            claims=[ChangeRequestPendingClaim(change_request_id="CR-184")],
        )
        # Positive case
        brief = _brief(root_causes=[f], evidence=["TEST-009-118", "CR-184", "MS-009-FTC"])
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - CR approved
        setup_catalog.records["CR-184"].payload["status"] = "approved"
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_maintenance_required_claim(self, setup_catalog) -> None:
        # Create dependency for task
        setup_catalog.records["DEP-009-002"] = EvidenceRecord(
            source_id="DEP-009-002",
            record_type=RecordType.SCHEDULE_DEPENDENCY,
            aircraft_id="AC-009",
            payload={
                "blocked_test_id": "TEST-009-121",
                "blocker_maintenance_task_id": "MNT-009-015",
            },
        )
        setup_catalog.retrieved_source_ids.add("DEP-009-002")

        f = _finding(
            refs=[_ref("MNT-009-015"), _ref("TEST-009-121")],
            claims=[
                MaintenanceRequiredClaim(
                    maintenance_task_id="MNT-009-015", test_event_id="TEST-009-121"
                )
            ],
        )
        # Positive case
        brief = _brief(
            root_causes=[f], evidence=["TEST-009-118", "MNT-009-015", "TEST-009-121", "MS-009-FTC"]
        )
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - task completed
        setup_catalog.records["MNT-009-015"].payload["status"] = "completed"
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_dependency_blocks_test_claim(self, setup_catalog) -> None:
        f = _finding(
            refs=[_ref("DEP-009-001"), _ref("TEST-009-121")],
            claims=[
                DependencyBlocksTestClaim(
                    dependency_id="DEP-009-001", test_event_id="TEST-009-121"
                )
            ],
        )
        # Positive case
        brief = _brief(
            root_causes=[f], evidence=["TEST-009-118", "DEP-009-001", "TEST-009-121", "MS-009-FTC"]
        )
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - mismatch test event
        f_bad = _finding(
            refs=[_ref("DEP-009-001"), _ref("TEST-009-118")],
            claims=[
                DependencyBlocksTestClaim(
                    dependency_id="DEP-009-001", test_event_id="TEST-009-118"
                )
            ],
        )
        brief_bad = _brief(
            root_causes=[f_bad], evidence=["TEST-009-118", "DEP-009-001", "MS-009-FTC"]
        )
        report = validate_brief(brief_bad, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

    def test_milestone_delayed_claim(self, setup_catalog) -> None:
        f = _finding(
            refs=[_ref("MS-009-FTC")],
            claims=[MilestoneDelayedClaim(milestone_id="MS-009-FTC")],
        )
        # Positive case
        brief = _brief(root_causes=[f], evidence=["TEST-009-118", "MS-009-FTC"])
        report = validate_brief(brief, setup_catalog)
        assert not any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)

        # Negative case - milestone on track
        setup_catalog.records["MS-009-FTC"].payload["forecast_date"] = "2026-06-29"
        report = validate_brief(brief, setup_catalog)
        assert any(v.code == "CLAIM_CONTRADICTS_SOURCE" for v in report.violations)


# ---------------------------------------------------------------------------
# Recommendations Tests
# ---------------------------------------------------------------------------
class TestRecommendationsValidation:
    """Recommendations checks."""

    def test_recommendation_supporting_finding_not_found(self) -> None:
        cat = EvidenceCatalog()
        cat.records["TEST-009-118"] = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={},
        )
        cat.records["MS-009-FTC"] = EvidenceRecord(
            source_id="MS-009-FTC",
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": "2026-06-29", "forecast_date": "2026-07-05"},
        )
        cat.specialist_source_ids.add("TEST-009-118")

        f = _finding(finding_id="FIND-TEST-001")
        action = _action(supporting_finding_ids=["FIND-MAINT-999"])  # Unknown ID

        brief = _brief(root_causes=[f], actions=[action])
        report = validate_brief(brief, cat)

        assert not report.passed
        assert any(v.code == "RECOMMENDATION_UNMAPPED_TO_FINDING" for v in report.violations)


# ---------------------------------------------------------------------------
# Brief and Prose Tests
# ---------------------------------------------------------------------------
class TestBriefValidation:
    """Brief check verification."""

    def test_unsupported_id_in_prose_fails(self) -> None:
        cat = EvidenceCatalog()
        cat.records["TEST-009-118"] = EvidenceRecord(
            source_id="TEST-009-118",
            record_type=RecordType.TEST_EVENT,
            aircraft_id="AC-009",
            payload={},
        )
        cat.records["MS-009-FTC"] = EvidenceRecord(
            source_id="MS-009-FTC",
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": "2026-06-29", "forecast_date": "2026-07-05"},
        )
        cat.specialist_source_ids.add("TEST-009-118")

        # DEF-009-999 is in the executive summary prose but not in catalog
        brief = _brief(evidence=["TEST-009-118", "MS-009-FTC"])
        brief.executive_summary = "We saw DEF-009-999 fail during low-speed taxi."

        report = validate_brief(brief, cat)
        assert not report.passed
        assert any(
            v.code == "UNSUPPORTED_SOURCE_ID" and v.location == "prose" for v in report.violations
        )


# ---------------------------------------------------------------------------
# Callback Concurrency Safety Tests
# ---------------------------------------------------------------------------
class TestCallbackConcurrencySafety:
    """Verify callback concurrency safety, immutability, and provenance."""

    def test_concurrent_callbacks_for_same_specialist(self) -> None:
        import threading

        from aeroops.agent import make_after_tool_callback

        class MockTool:
            def __init__(self, name: str) -> None:
                self.name = name

        class MockContext:
            invocation_id = "inv-999"
            agent_name = "test_ops_specialist"
            function_call_id = "fc-1234"

            def __init__(self) -> None:
                self.state: dict = {}

        ctx = MockContext()
        cb = make_after_tool_callback("test_ops_mcp_evidence")

        errors = []

        def run_callback(tool_name: str, response_val: str) -> None:
            try:
                tool = MockTool(tool_name)
                args = {"param": "value"}
                resp = {"result": response_val}

                res = cb(tool, args, ctx, resp)
                assert res is None
                # Verify original response is unchanged
                assert resp == {"result": response_val}
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t = threading.Thread(target=run_callback, args=(f"tool_{i}", f"resp_{i}"))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent callback execution: {errors}"

        evidence = ctx.state["test_ops_mcp_evidence"]
        assert len(evidence) == 5

        tool_names = {entry["tool_name"] for entry in evidence}
        assert len(tool_names) == 5
        assert tool_names == {f"tool_{i}" for i in range(5)}

        for entry in evidence:
            import json

            json.dumps(entry)

            assert "agent_name" in entry
            assert "invocation_id" in entry
            assert "function_call_id" in entry
            assert "branch_key" in entry
            assert "branch_sequence" in entry
            assert "tool_name" in entry
            assert entry["branch_key"] == "test_ops_mcp_evidence"
            assert isinstance(entry["branch_sequence"], int)


# ---------------------------------------------------------------------------
# Architecture Tests
# ---------------------------------------------------------------------------
class TestArchitectureConstraints:
    """Validate architectural design requirements (no direct SQLite/repo)."""

    def test_no_forbidden_imports(self) -> None:
        val_path = Path(__file__).parent.parent / "src" / "aeroops" / "validation.py"
        svc_path = Path(__file__).parent.parent / "src" / "aeroops" / "services.py"

        for p in (val_path, svc_path):
            assert p.exists()
            content = p.read_text()
            assert "sqlite3" not in content, f"sqlite3 imported in {p.name}"
            assert "aeroops.db" not in content or "aeroops.db.repository" not in content, (
                f"repository imported in {p.name}"
            )
