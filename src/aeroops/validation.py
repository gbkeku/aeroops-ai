"""Evidence integrity validator for AeroOps investigation briefs.

This module provides ``validate_brief`` — a deterministic validator that runs
after the LLM synthesis pipeline completes. It validates the brief against
an EvidenceCatalog assembled from specialist MCP captures and preflight calls.
No SQLite or repository functions are imported or called here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from aeroops.models import (
    EvidenceRecord,
    ExecutiveBrief,
    RecordType,
)

# ---------------------------------------------------------------------------
# Centralized AeroOps ID Parser
# ---------------------------------------------------------------------------
AEROOPS_ID_PATTERN = re.compile(
    r"\b("
    r"AC-\d{3}|"
    r"MS-\d{3}-[A-Z0-9-]+|"
    r"DEF-\d{3}-\d{3}|"
    r"TEST-\d{3}-\d{3}|"
    r"MNT-\d{3}-\d{3}|"
    r"PART-[A-Z0-9-]+|"
    r"CR-\d{3}|"
    r"DEP-\d{3}-\d{3}"
    r")\b"
)


def extract_all_ids_from_text(text: str) -> set[str]:
    """Parse text and return all valid AeroOps identifiers found."""
    return set(AEROOPS_ID_PATTERN.findall(text))


# ---------------------------------------------------------------------------
# Canonical Payload for Conflict Checking
# ---------------------------------------------------------------------------
class CanonicalPayload(BaseModel):
    source_id: str
    aircraft_id: str
    status: str | None = None
    name: str | None = None
    severity: str | None = None
    planned_date: str | None = None
    forecast_date: str | None = None


def to_canonical_payload(rt: RecordType, p: dict) -> CanonicalPayload:
    """Normalize payload into a CanonicalPayload model."""
    sid = p.get("source_id") or p.get("id") or ""
    aid = p.get("aircraft_id") or ""
    status = p.get("status")
    name = p.get("name") or p.get("title") or p.get("name_or_title")
    severity = p.get("severity")
    planned = str(p.get("planned_date")) if p.get("planned_date") else None
    forecast = str(p.get("forecast_date")) if p.get("forecast_date") else None
    return CanonicalPayload(
        source_id=sid,
        aircraft_id=aid,
        status=status,
        name=name,
        severity=severity,
        planned_date=planned,
        forecast_date=forecast,
    )


def check_payload_conflict(rt: RecordType, p1: dict, p2: dict) -> bool:
    """Return True if common operational fields differ (excluding metadata/provenance)."""
    c1 = to_canonical_payload(rt, p1)
    c2 = to_canonical_payload(rt, p2)
    fields = ["status", "name", "severity", "planned_date", "forecast_date"]
    for f in fields:
        val1 = getattr(c1, f)
        val2 = getattr(c2, f)
        if val1 is not None and val2 is not None and val1 != val2:
            return True
    return False


# ---------------------------------------------------------------------------
# Exception Taxonomy
# ---------------------------------------------------------------------------
class EvidenceRecordConflictError(ValueError):
    """Raised when normalized payloads for the same source ID differ."""

    def __init__(self, source_id: str, detail: str) -> None:
        super().__init__(f"EVIDENCE_RECORD_CONFLICT: {detail}")
        self.source_id = source_id


class EvidenceIntegrityError(ValueError):
    """Base exception for evidence catalog validation errors."""


# ---------------------------------------------------------------------------
# Explicit Per-Tool Evidence Adapters
# ---------------------------------------------------------------------------
def parse_mcp_response(
    tool_name: str, response: dict, aircraft_id: str
) -> list[tuple[str, RecordType, str, dict]]:
    """Validate MCP envelope and extract structured records."""
    if not isinstance(response, dict):
        return []
    if "error" in response:
        return []

    # Unpack standard MCP content envelope if present
    if "structuredContent" in response:
        response = response["structuredContent"]
    elif "content" in response and isinstance(response["content"], list):
        import json

        for part in response["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                try:
                    response = json.loads(text)
                    break
                except Exception:
                    pass

    if not isinstance(response, dict):
        return []

    records = []

    if tool_name == "get_aircraft_status":
        data = response.get("data")
        if not data or not isinstance(data, dict):
            return []
        sid = data.get("source_id")
        if sid and sid == aircraft_id:
            records.append((sid, RecordType.AIRCRAFT, aircraft_id, data))

    elif tool_name == "get_milestones":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.MILESTONE, aid, item))

    elif tool_name == "get_test_events":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.TEST_EVENT, aid, item))

    elif tool_name == "get_open_defects":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.DEFECT, aid, item))

    elif tool_name == "get_maintenance_tasks":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.MAINTENANCE_TASK, aid, item))

    elif tool_name == "get_parts_constraints":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.PARTS_CONSTRAINT, aid, item))

    elif tool_name == "get_change_requests":
        data_list = response.get("data")
        if not data_list or not isinstance(data_list, list):
            return []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            sid = item.get("source_id")
            aid = item.get("aircraft_id")
            if sid and aid == aircraft_id:
                records.append((sid, RecordType.CHANGE_REQUEST, aid, item))

    elif tool_name == "get_dependency_graph":
        data = response.get("data")
        if not data or not isinstance(data, dict):
            return []
        aid = data.get("aircraft_id")
        if aid != aircraft_id:
            return []

        # Parse nodes
        nodes = data.get("nodes", [])
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                node_type = node.get("type")
                if not node_id or not node_type:
                    continue
                rt_map = {
                    "test_event": RecordType.TEST_EVENT,
                    "defect": RecordType.DEFECT,
                    "maintenance_task": RecordType.MAINTENANCE_TASK,
                    "parts_constraint": RecordType.PARTS_CONSTRAINT,
                    "change_request": RecordType.CHANGE_REQUEST,
                }
                rt = rt_map.get(node_type)
                if rt:
                    records.append((node_id, rt, aid, node))

        # Parse dependencies
        deps = data.get("dependencies", [])
        if isinstance(deps, list):
            for dep in deps:
                if not isinstance(dep, dict):
                    continue
                sid = dep.get("source_id")
                dep_aid = dep.get("aircraft_id")
                if sid and dep_aid == aircraft_id:
                    records.append((sid, RecordType.SCHEDULE_DEPENDENCY, dep_aid, dep))

    return records


# ---------------------------------------------------------------------------
# Evidence Catalog
# ---------------------------------------------------------------------------
def _operational_payload_richness(payload: dict[str, Any]) -> int:
    """Return a stable score that prefers full records over graph-node summaries."""
    ignored = {"snapshot_date", "synthetic_data", "source_refs", "count", "truncated"}
    return sum(
        1
        for key, value in payload.items()
        if key not in ignored and value not in (None, "", [], {})
    )


class EvidenceCatalog:
    """In-memory collection of all validated evidence records retrieved during investigation."""

    def __init__(self) -> None:
        self.records: dict[str, EvidenceRecord] = {}
        self.retrieved_source_ids: set[str] = set()
        self.specialist_source_ids: set[str] = set()
        self.approved_preflight_source_ids: set[str] = set()

    def add_record(self, record: EvidenceRecord) -> None:
        """Add record to catalog, merging provenance or raising on conflict."""
        if record.source_id in self.records:
            existing = self.records[record.source_id]
            if check_payload_conflict(record.record_type, existing.payload, record.payload):
                raise EvidenceRecordConflictError(
                    record.source_id, f"Conflicting payloads for source ID {record.source_id}"
                )
            existing.provenance.extend(record.provenance)
            # Dependency-graph nodes intentionally carry only presentation
            # fields.  When a domain-specific list tool later returns the full
            # record, retain that richer canonical payload for semantic claim
            # validation instead of keeping the first, incomplete snapshot.
            if _operational_payload_richness(record.payload) > _operational_payload_richness(
                existing.payload
            ):
                existing.payload = dict(record.payload)
                existing.record_type = record.record_type
                existing.aircraft_id = record.aircraft_id
        else:
            self.records[record.source_id] = record


# ---------------------------------------------------------------------------
# Semantic Claim Rules Verification
# ---------------------------------------------------------------------------
def verify_claim(claim: Any, catalog: EvidenceCatalog, aircraft_id: str) -> str | None:
    """Verify a single Claim against the catalog records.

    Returns a violation detail string on failure, or None on success.
    """
    from aeroops.models import (
        ChangeRequestPendingClaim,
        DefectBlocksTestClaim,
        DependencyBlocksTestClaim,
        MaintenanceRequiredClaim,
        MilestoneDelayedClaim,
        PartArrivesAfterNeedDateClaim,
        TestAbortedClaim,
    )

    if isinstance(claim, TestAbortedClaim):
        rec = catalog.records.get(claim.test_event_id)
        if not rec:
            return f"Test event {claim.test_event_id} not found in catalog"
        if rec.payload.get("status") != "aborted":
            return f"Test event {claim.test_event_id} status is '{rec.payload.get('status')}', expected 'aborted'"

    elif isinstance(claim, DefectBlocksTestClaim):
        defect_rec = catalog.records.get(claim.defect_id)
        if not defect_rec:
            return f"Defect {claim.defect_id} not found in catalog"
        if defect_rec.aircraft_id != aircraft_id:
            return f"Defect {claim.defect_id} belongs to aircraft {defect_rec.aircraft_id}, expected {aircraft_id}"

        # Find dependency linking defect to test
        found_dep = False
        for rec in catalog.records.values():
            if rec.record_type == RecordType.SCHEDULE_DEPENDENCY and (
                rec.payload.get("blocked_test_id") == claim.test_event_id
                and rec.payload.get("blocker_defect_id") == claim.defect_id
            ):
                found_dep = True
                break
        if not found_dep:
            return f"No dependency connects defect {claim.defect_id} to blocked test {claim.test_event_id}"

    elif isinstance(claim, PartArrivesAfterNeedDateClaim):
        part_rec = catalog.records.get(claim.parts_constraint_id)
        if not part_rec:
            return f"Parts constraint {claim.parts_constraint_id} not found in catalog"
        needed = part_rec.payload.get("needed_by")
        arrival = part_rec.payload.get("estimated_arrival")
        if not needed or not arrival:
            return f"Parts constraint {claim.parts_constraint_id} missing needed_by or estimated_arrival"
        if str(arrival) <= str(needed):
            return f"Part ETA {arrival} is not later than needed date {needed}"

    elif isinstance(claim, ChangeRequestPendingClaim):
        cr_rec = catalog.records.get(claim.change_request_id)
        if not cr_rec:
            return f"Change request {claim.change_request_id} not found in catalog"
        # pending_review states
        if cr_rec.payload.get("status") != "pending_review":
            return f"CR {claim.change_request_id} status is '{cr_rec.payload.get('status')}', expected 'pending_review'"

    elif isinstance(claim, MaintenanceRequiredClaim):
        mnt_rec = catalog.records.get(claim.maintenance_task_id)
        if not mnt_rec:
            return f"Maintenance task {claim.maintenance_task_id} not found in catalog"
        if mnt_rec.payload.get("status") == "completed":
            return (
                f"Maintenance task {claim.maintenance_task_id} is completed, expected incomplete"
            )
        if claim.test_event_id:
            found_dep = False
            for rec in catalog.records.values():
                if rec.record_type == RecordType.SCHEDULE_DEPENDENCY and (
                    rec.payload.get("blocked_test_id") == claim.test_event_id
                    and rec.payload.get("blocker_maintenance_task_id") == claim.maintenance_task_id
                ):
                    found_dep = True
                    break
            if not found_dep:
                return f"No dependency connects task {claim.maintenance_task_id} as blocker for test {claim.test_event_id}"

    elif isinstance(claim, DependencyBlocksTestClaim):
        dep_rec = catalog.records.get(claim.dependency_id)
        if not dep_rec:
            return f"Dependency {claim.dependency_id} not found in catalog"
        if dep_rec.payload.get("blocked_test_id") != claim.test_event_id:
            return f"Dependency {claim.dependency_id} blocks test {dep_rec.payload.get('blocked_test_id')}, expected {claim.test_event_id}"
        # Validate blocker ID matches associated blocker record in catalog
        blocker_id = (
            dep_rec.payload.get("blocker_defect_id")
            or dep_rec.payload.get("blocker_parts_constraint_id")
            or dep_rec.payload.get("blocker_change_request_id")
            or dep_rec.payload.get("blocker_maintenance_task_id")
        )
        if not blocker_id:
            return f"Dependency {claim.dependency_id} has no blocker ID populated"
        if blocker_id not in catalog.records:
            return f"Dependency {claim.dependency_id} blocker {blocker_id} not found in catalog"

    elif isinstance(claim, MilestoneDelayedClaim):
        ms_rec = catalog.records.get(claim.milestone_id)
        if not ms_rec:
            return f"Milestone {claim.milestone_id} not found in catalog"
        planned = ms_rec.payload.get("planned_date")
        forecast = ms_rec.payload.get("forecast_date")
        if not planned or not forecast:
            return f"Milestone {claim.milestone_id} missing planned or forecast dates"
        if str(forecast) <= str(planned):
            return f"Milestone {claim.milestone_id} is not delayed: planned={planned}, forecast={forecast}"

    return None


# ---------------------------------------------------------------------------
# Violation Definitions
# ---------------------------------------------------------------------------
ViolationCode = Literal[
    "FINDING_MISSING_SOURCE_REFS",
    "RECOMMENDATION_MISSING_SOURCE_REFS",
    "UNSUPPORTED_SOURCE_ID",
    "SOURCE_NOT_IN_SPECIALIST_EVIDENCE",
    "WRONG_AIRCRAFT",
    "DUPLICATE_SOURCE_REF",
    "RECOMMENDATION_UNMAPPED_TO_FINDING",
    "CLAIM_CONTRADICTS_SOURCE",
    "MILESTONE_DELAY_MISMATCH",
    "BRIEF_EVIDENCE_MISMATCH",
    "DUPLICATE_FINDING_ID",
    "DUPLICATE_FINDING_STATEMENT",
    "FINDING_ROOT_AND_CONTRIBUTING",
]


@dataclass(frozen=True)
class Violation:
    code: ViolationCode
    source_id: str
    location: str
    detail: str


@dataclass
class ValidationReport:
    aircraft_id: str
    violations: list[Violation] = field(default_factory=list)
    refs_checked: int = 0
    records_verified: int = 0

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0

    def format_violations(self) -> str:
        if self.passed:
            return ""
        lines = [
            f"Evidence integrity validation FAILED for {self.aircraft_id} "
            f"({len(self.violations)} violation(s)):"
        ]
        for i, v in enumerate(self.violations, start=1):
            lines.append(f"  [{i}] {v.code}")
            lines.append(f"       location : {v.location}")
            lines.append(f"       source_id: {v.source_id!r}")
            lines.append(f"       detail   : {v.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public Validator Function
# ---------------------------------------------------------------------------
def validate_brief(
    brief: ExecutiveBrief,
    catalog: EvidenceCatalog,
) -> ValidationReport:
    """Run all evidence integrity invariants against the synthesised brief."""
    report = ValidationReport(aircraft_id=brief.aircraft_id)
    finding_ids = set()
    finding_statements = set()
    finding_source_refs = set()

    # Determine findings mapping to detect root vs contributing duplicates
    root_finding_ids = {f.finding_id for f in brief.confirmed_root_causes}
    contrib_finding_ids = {f.finding_id for f in brief.contributing_factors}
    clash_ids = root_finding_ids & contrib_finding_ids
    for cid in clash_ids:
        report.violations.append(
            Violation(
                code="FINDING_ROOT_AND_CONTRIBUTING",
                source_id=cid,
                location="confirmed_root_causes / contributing_factors",
                detail=f"Finding ID {cid} appears in both root causes and contributing factors",
            )
        )

    # 1. Validate Confirmed Root Causes
    for fi, finding in enumerate(brief.confirmed_root_causes):
        loc_base = f"confirmed_root_causes[{fi}]"

        # Check unique finding ID
        if finding.finding_id in finding_ids:
            report.violations.append(
                Violation(
                    code="DUPLICATE_FINDING_ID",
                    source_id=finding.finding_id,
                    location=loc_base,
                    detail=f"Duplicate finding ID: {finding.finding_id}",
                )
            )
        finding_ids.add(finding.finding_id)

        # Check duplicate statement
        stmt_norm = finding.statement.strip().lower()
        if stmt_norm in finding_statements:
            report.violations.append(
                Violation(
                    code="DUPLICATE_FINDING_STATEMENT",
                    source_id=finding.finding_id,
                    location=loc_base,
                    detail=f"Duplicate statement: '{finding.statement}'",
                )
            )
        finding_statements.add(stmt_norm)

        # Check non-empty source refs
        if not finding.source_refs:
            report.violations.append(
                Violation(
                    code="FINDING_MISSING_SOURCE_REFS",
                    source_id="",
                    location=loc_base,
                    detail="Finding is missing source references",
                )
            )
            continue

        seen_refs = set()
        for ri, ref in enumerate(finding.source_refs):
            loc = f"{loc_base}.source_refs[{ri}]"
            report.refs_checked += 1
            finding_source_refs.add(ref.source_id)

            if ref.source_id in seen_refs:
                report.violations.append(
                    Violation(
                        code="DUPLICATE_SOURCE_REF",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Duplicate reference to {ref.source_id} in finding",
                    )
                )
            seen_refs.add(ref.source_id)

            # Check ID exists in catalog and matches aircraft
            rec = catalog.records.get(ref.source_id)
            if not rec:
                report.violations.append(
                    Violation(
                        code="UNSUPPORTED_SOURCE_ID",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Source ID {ref.source_id} is absent from catalog",
                    )
                )
            else:
                report.records_verified += 1
                if rec.aircraft_id != brief.aircraft_id:
                    report.violations.append(
                        Violation(
                            code="WRONG_AIRCRAFT",
                            source_id=ref.source_id,
                            location=loc,
                            detail=f"Source ID {ref.source_id} belongs to aircraft {rec.aircraft_id}, expected {brief.aircraft_id}",
                        )
                    )
                # Verify admissibility
                if (
                    ref.source_id not in catalog.specialist_source_ids
                    and ref.source_id not in catalog.approved_preflight_source_ids
                ):
                    report.violations.append(
                        Violation(
                            code="SOURCE_NOT_IN_SPECIALIST_EVIDENCE",
                            source_id=ref.source_id,
                            location=loc,
                            detail=f"Source ID {ref.source_id} was retrieved but never cited by specialists or approved in preflight",
                        )
                    )

        # Validate semantic claims
        for ci, claim in enumerate(finding.claims):
            loc = f"{loc_base}.claims[{ci}]"
            claim_err = verify_claim(claim, catalog, brief.aircraft_id)
            if claim_err:
                report.violations.append(
                    Violation(
                        code="CLAIM_CONTRADICTS_SOURCE",
                        source_id="",
                        location=loc,
                        detail=claim_err,
                    )
                )

    # 2. Validate Contributing Factors
    for fi, finding in enumerate(brief.contributing_factors):
        loc_base = f"contributing_factors[{fi}]"

        # Check unique finding ID
        if finding.finding_id in finding_ids:
            report.violations.append(
                Violation(
                    code="DUPLICATE_FINDING_ID",
                    source_id=finding.finding_id,
                    location=loc_base,
                    detail=f"Duplicate finding ID: {finding.finding_id}",
                )
            )
        finding_ids.add(finding.finding_id)

        # Check duplicate statement
        stmt_norm = finding.statement.strip().lower()
        if stmt_norm in finding_statements:
            report.violations.append(
                Violation(
                    code="DUPLICATE_FINDING_STATEMENT",
                    source_id=finding.finding_id,
                    location=loc_base,
                    detail=f"Duplicate statement: '{finding.statement}'",
                )
            )
        finding_statements.add(stmt_norm)

        # Check non-empty source refs
        if not finding.source_refs:
            report.violations.append(
                Violation(
                    code="FINDING_MISSING_SOURCE_REFS",
                    source_id="",
                    location=loc_base,
                    detail="Finding is missing source references",
                )
            )
            continue

        seen_refs = set()
        for ri, ref in enumerate(finding.source_refs):
            loc = f"{loc_base}.source_refs[{ri}]"
            report.refs_checked += 1
            finding_source_refs.add(ref.source_id)

            if ref.source_id in seen_refs:
                report.violations.append(
                    Violation(
                        code="DUPLICATE_SOURCE_REF",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Duplicate reference to {ref.source_id} in finding",
                    )
                )
            seen_refs.add(ref.source_id)

            # Check ID exists in catalog and matches aircraft
            rec = catalog.records.get(ref.source_id)
            if not rec:
                report.violations.append(
                    Violation(
                        code="UNSUPPORTED_SOURCE_ID",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Source ID {ref.source_id} is absent from catalog",
                    )
                )
            else:
                report.records_verified += 1
                if rec.aircraft_id != brief.aircraft_id:
                    report.violations.append(
                        Violation(
                            code="WRONG_AIRCRAFT",
                            source_id=ref.source_id,
                            location=loc,
                            detail=f"Source ID {ref.source_id} belongs to aircraft {rec.aircraft_id}, expected {brief.aircraft_id}",
                        )
                    )
                # Verify admissibility
                if (
                    ref.source_id not in catalog.specialist_source_ids
                    and ref.source_id not in catalog.approved_preflight_source_ids
                ):
                    report.violations.append(
                        Violation(
                            code="SOURCE_NOT_IN_SPECIALIST_EVIDENCE",
                            source_id=ref.source_id,
                            location=loc,
                            detail=f"Source ID {ref.source_id} was retrieved but never cited by specialists or approved in preflight",
                        )
                    )

        # Validate semantic claims
        for ci, claim in enumerate(finding.claims):
            loc = f"{loc_base}.claims[{ci}]"
            claim_err = verify_claim(claim, catalog, brief.aircraft_id)
            if claim_err:
                report.violations.append(
                    Violation(
                        code="CLAIM_CONTRADICTS_SOURCE",
                        source_id="",
                        location=loc,
                        detail=claim_err,
                    )
                )

    # 3. Validate Recommended Actions
    rec_action_ids = set()
    recommendation_source_refs = set()

    for ai, action in enumerate(brief.recommended_actions):
        loc_base = f"recommended_actions[{ai}]"

        # Unique action ID check
        if action.action_id in rec_action_ids:
            report.violations.append(
                Violation(
                    code="DUPLICATE_SOURCE_REF",  # or custom code, let's keep it simple
                    source_id=action.action_id,
                    location=loc_base,
                    detail=f"Duplicate action ID: {action.action_id}",
                )
            )
        rec_action_ids.add(action.action_id)

        # Non-empty source refs check
        if not action.source_refs:
            report.violations.append(
                Violation(
                    code="RECOMMENDATION_MISSING_SOURCE_REFS",
                    source_id="",
                    location=loc_base,
                    detail="Recommendation is missing source references",
                )
            )
            continue

        # Check supporting findings exist in the brief
        linked_finding_source_ids = set()
        for fid in action.supporting_finding_ids:
            found_f = None
            for f in brief.confirmed_root_causes:
                if f.finding_id == fid:
                    found_f = f
                    break
            if not found_f:
                for f in brief.contributing_factors:
                    if f.finding_id == fid:
                        found_f = f
                        break
            if not found_f:
                report.violations.append(
                    Violation(
                        code="RECOMMENDATION_UNMAPPED_TO_FINDING",
                        source_id=fid,
                        location=loc_base,
                        detail=f"Recommendation links to unknown finding ID {fid}",
                    )
                )
            else:
                linked_finding_source_ids |= {ref.source_id for ref in found_f.source_refs}

        # Check recommendation source_refs trace back to the linked findings' evidence pool
        {ref.source_id for ref in action.source_refs}
        for ri, ref in enumerate(action.source_refs):
            loc = f"{loc_base}.source_refs[{ri}]"
            report.refs_checked += 1
            recommendation_source_refs.add(ref.source_id)

            rec = catalog.records.get(ref.source_id)
            if not rec:
                report.violations.append(
                    Violation(
                        code="UNSUPPORTED_SOURCE_ID",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Recommendation source ID {ref.source_id} is absent from catalog",
                    )
                )
            else:
                report.records_verified += 1
                if rec.aircraft_id != brief.aircraft_id:
                    report.violations.append(
                        Violation(
                            code="WRONG_AIRCRAFT",
                            source_id=ref.source_id,
                            location=loc,
                            detail=f"Recommendation source ID {ref.source_id} belongs to aircraft {rec.aircraft_id}, expected {brief.aircraft_id}",
                        )
                    )

            # The source ref must exist in the linked findings' evidence pool
            if linked_finding_source_ids and ref.source_id not in linked_finding_source_ids:
                report.violations.append(
                    Violation(
                        code="RECOMMENDATION_UNMAPPED_TO_FINDING",
                        source_id=ref.source_id,
                        location=loc,
                        detail=f"Recommendation source ID {ref.source_id} is not supported by any linked findings' evidence pool",
                    )
                )

    # 4. Milestone Dates and Delay validation
    ms_rec = catalog.records.get(brief.milestone_source_id)
    if not ms_rec:
        report.violations.append(
            Violation(
                code="UNSUPPORTED_SOURCE_ID",
                source_id=brief.milestone_source_id,
                location="milestone_source_id",
                detail=f"Key milestone {brief.milestone_source_id} is absent from catalog",
            )
        )
    else:
        planned_date = date.fromisoformat(str(ms_rec.payload.get("planned_date")))
        forecast_date = date.fromisoformat(str(ms_rec.payload.get("forecast_date")))
        expected_delay = (forecast_date - planned_date).days

        if (
            brief.planned_milestone_date != planned_date
            or brief.forecast_milestone_date != forecast_date
        ):
            report.violations.append(
                Violation(
                    code="MILESTONE_DELAY_MISMATCH",
                    source_id=brief.milestone_source_id,
                    location="planned_milestone_date / forecast_milestone_date",
                    detail=f"Brief milestone dates ({brief.planned_milestone_date}, {brief.forecast_milestone_date}) "
                    f"do not match milestone record ({planned_date}, {forecast_date})",
                )
            )
        if brief.delay_days != expected_delay:
            report.violations.append(
                Violation(
                    code="MILESTONE_DELAY_MISMATCH",
                    source_id=brief.milestone_source_id,
                    location="delay_days",
                    detail=f"Delay days {brief.delay_days} contradicts source delay {expected_delay}",
                )
            )

    # 5. Top-Level Evidence Union Check
    expected_evidence_union = sorted(
        list(finding_source_refs | recommendation_source_refs | {brief.milestone_source_id})
    )
    if sorted(brief.evidence) != expected_evidence_union:
        report.violations.append(
            Violation(
                code="BRIEF_EVIDENCE_MISMATCH",
                source_id="",
                location="evidence",
                detail=f"Brief evidence list does not match union of cited source refs. "
                f"Got {sorted(brief.evidence)}, expected {expected_evidence_union}",
            )
        )

    # 6. Validate Known AeroOps Identifiers in Prose
    prose_texts = [
        brief.executive_summary,
        *brief.assumptions,
        *brief.unknowns,
    ]
    for root in brief.confirmed_root_causes:
        prose_texts.append(root.statement)
        prose_texts.append(root.rationale)
    for contrib in brief.contributing_factors:
        prose_texts.append(contrib.statement)
        prose_texts.append(contrib.rationale)
    for action in brief.recommended_actions:
        prose_texts.append(action.action)
        prose_texts.append(action.rationale)

    for text in prose_texts:
        found_ids = extract_all_ids_from_text(text)
        for fid in found_ids:
            # Must exist in catalog
            if fid not in catalog.records:
                report.violations.append(
                    Violation(
                        code="UNSUPPORTED_SOURCE_ID",
                        source_id=fid,
                        location="prose",
                        detail=f"Prose contains unsupported AeroOps identifier: {fid}",
                    )
                )

    return report
