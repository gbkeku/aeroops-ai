"""Deterministic normalization for specialist-agent model responses.

Live Gemini models may return a useful but slightly non-conforming JSON draft
for a specialist report: source references can be strings, classifications can
use synonyms, claims can be incomplete, or two specialists can describe the
same fact in nearly identical prose.  Rejecting the complete investigation for
those presentation differences is unnecessary because AeroOps already retains
the canonical MCP responses returned to each specialist.

This module provides agent-level ``after_model_callback`` functions that rebuild
``SpecialistReport`` objects from the *actual callback-captured MCP evidence*.
The model can contribute bounded wording when it references only records that
were returned to that specialist.  Classification, source references, claims,
aircraft scope, and raw source IDs are derived deterministically.

The downstream ``ReportValidatorAgent`` and ``EvidenceCatalog`` remain strict;
this normalizer does not invent evidence or make unsupported records admissible.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from aeroops.models import (
    ChangeRequestPendingClaim,
    DefectBlocksTestClaim,
    DependencyBlocksTestClaim,
    EvidenceRef,
    Finding,
    MaintenanceRequiredClaim,
    PartArrivesAfterNeedDateClaim,
    RecordType,
    SpecialistReport,
    TestAbortedClaim,
)
from aeroops.validation import extract_all_ids_from_text, parse_mcp_response

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpecialistSpec:
    """Static normalization policy for one specialist branch."""

    domain: str
    evidence_key: str
    output_key: str
    statement_prefix: str


@dataclass
class CapturedRecord:
    """A record returned by one of the specialist's authorized MCP tools."""

    source_id: str
    record_type: RecordType
    aircraft_id: str
    payload: dict[str, Any]
    tool_names: set[str]


_SPECS: dict[str, SpecialistSpec] = {
    "test_operations": SpecialistSpec(
        domain="test_operations",
        evidence_key="test_ops_mcp_evidence",
        output_key="test_ops_findings",
        statement_prefix="Test operations",
    ),
    "maintenance": SpecialistSpec(
        domain="maintenance",
        evidence_key="maintenance_mcp_evidence",
        output_key="maintenance_findings",
        statement_prefix="Maintenance",
    ),
    "configuration_supply": SpecialistSpec(
        domain="configuration_supply",
        evidence_key="configuration_supply_mcp_evidence",
        output_key="configuration_supply_findings",
        statement_prefix="Configuration and supply",
    ),
    "schedule_risk": SpecialistSpec(
        domain="schedule_risk",
        evidence_key="schedule_risk_mcp_evidence",
        output_key="schedule_risk_findings",
        statement_prefix="Schedule risk",
    ),
}

_DOMAIN_ALLOWED_TYPES: dict[str, frozenset[RecordType]] = {
    "test_operations": frozenset(
        {
            RecordType.TEST_EVENT,
            RecordType.DEFECT,
            RecordType.PARTS_CONSTRAINT,
            RecordType.CHANGE_REQUEST,
            RecordType.MAINTENANCE_TASK,
            RecordType.SCHEDULE_DEPENDENCY,
        }
    ),
    "maintenance": frozenset({RecordType.DEFECT, RecordType.MAINTENANCE_TASK}),
    "configuration_supply": frozenset({RecordType.PARTS_CONSTRAINT, RecordType.CHANGE_REQUEST}),
    "schedule_risk": frozenset(
        {
            RecordType.TEST_EVENT,
            RecordType.DEFECT,
            RecordType.PARTS_CONSTRAINT,
            RecordType.CHANGE_REQUEST,
            RecordType.MAINTENANCE_TASK,
            RecordType.SCHEDULE_DEPENDENCY,
        }
    ),
}


class SpecialistNormalizationError(ValueError):
    """Raised when captured specialist evidence cannot form a report."""


def _clean_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _llm_response_text(response: LlmResponse) -> str:
    if response.content is None or not response.content.parts:
        return ""
    return "".join(
        part.text or ""
        for part in response.content.parts
        if part.text and not getattr(part, "thought", False)
    )


def _has_function_calls(response: LlmResponse) -> bool:
    """Return whether the model response is an intermediate tool-call turn."""
    if response.content is None or not response.content.parts:
        return False
    return any(getattr(part, "function_call", None) is not None for part in response.content.parts)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Return the first model-visible JSON object, tolerating one wrapper."""
    cleaned = _clean_json(text)
    if not cleaned:
        return {}

    value: Any
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            return {}
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            return {}

    if not isinstance(value, dict):
        return {}
    for key in ("report", "specialist_report", "result", "output"):
        nested = value.get(key)
        if isinstance(nested, dict) and (
            "findings" in nested
            or "confirmed_findings" in nested
            or "contributing_factors" in nested
        ):
            return nested
    return value


def _state_aircraft_id(state: Any, candidate: dict[str, Any]) -> str | None:
    raw_scope = state.get("investigation_scope")
    scope: dict[str, Any] = {}
    if isinstance(raw_scope, dict):
        scope = raw_scope
    elif isinstance(raw_scope, str):
        try:
            parsed = json.loads(_clean_json(raw_scope))
            if isinstance(parsed, dict):
                scope = parsed
        except json.JSONDecodeError:
            pass

    for value in (
        scope.get("aircraft_id"),
        state.get("aircraft_id"),
        candidate.get("aircraft_id"),
    ):
        if isinstance(value, str) and re.fullmatch(r"AC-\d{3}", value.strip()):
            return value.strip()
    return None


def _evidence_entries(state: Any, evidence_key: str) -> list[dict[str, Any]]:
    raw = state.get(evidence_key, [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _payload_richness(payload: dict[str, Any]) -> int:
    """Score a payload so full list-tool records beat graph-node summaries."""
    return sum(1 for value in payload.values() if value not in (None, "", [], {}))


def _collect_records(
    state: Any,
    spec: SpecialistSpec,
    aircraft_id: str,
) -> tuple[dict[str, CapturedRecord], int]:
    records: dict[str, CapturedRecord] = {}
    entries = _evidence_entries(state, spec.evidence_key)
    for entry in entries:
        tool_name = entry.get("tool_name")
        response = entry.get("response", {})
        if not isinstance(tool_name, str) or not isinstance(response, dict):
            continue
        for source_id, record_type, record_aircraft_id, payload in parse_mcp_response(
            tool_name, response, aircraft_id
        ):
            if record_type == RecordType.AIRCRAFT:
                # Aircraft status is useful context, but citing it in a finding
                # would add AC-NNN to the final operational evidence union.
                continue
            if record_type not in _DOMAIN_ALLOWED_TYPES[spec.domain]:
                continue
            existing = records.get(source_id)
            if existing is None:
                records[source_id] = CapturedRecord(
                    source_id=source_id,
                    record_type=record_type,
                    aircraft_id=record_aircraft_id,
                    payload=dict(payload),
                    tool_names={tool_name},
                )
                continue

            existing.tool_names.add(tool_name)
            if _payload_richness(payload) > _payload_richness(existing.payload):
                existing.payload = dict(payload)
                existing.record_type = record_type
                existing.aircraft_id = record_aircraft_id
    return records, len(entries)


def _preflight_context_records(
    state: Any,
    aircraft_id: str,
) -> dict[str, CapturedRecord]:
    """Return approved preflight records available to normalize empty specialist branches.

    Some green/on-track aircraft legitimately have no maintenance tasks, parts
    constraints, change requests, or dependency records.  A specialist branch
    can therefore complete its authorized MCP calls and capture no operational
    records.  In that case we use only the already-approved preflight aircraft
    and target milestone context as evidence for a bounded "no domain blocker"
    finding.  This does not make arbitrary records admissible; both records are
    already added to the EvidenceCatalog as approved preflight evidence.
    """
    records: dict[str, CapturedRecord] = {}

    aircraft_payload = state.get("preflight_aircraft_record")
    if not isinstance(aircraft_payload, dict):
        aircraft_payload = {
            "source_id": aircraft_id,
            "aircraft_id": aircraft_id,
            "status": state.get("overall_status") or "unknown",
            "title": f"{aircraft_id} investigation scope",
            "synthetic_data": True,
        }
    if aircraft_payload.get("source_id") == aircraft_id:
        records[aircraft_id] = CapturedRecord(
            source_id=aircraft_id,
            record_type=RecordType.AIRCRAFT,
            aircraft_id=aircraft_id,
            payload=dict(aircraft_payload),
            tool_names={"preflight:get_aircraft_status"},
        )

    milestone_payload = state.get("preflight_milestone_record")
    milestone_source_id = state.get("milestone_source_id")
    if not isinstance(milestone_payload, dict) and isinstance(milestone_source_id, str):
        milestone_payload = {
            "source_id": milestone_source_id,
            "aircraft_id": aircraft_id,
            "planned_date": state.get("planned_milestone_date"),
            "forecast_date": state.get("forecast_milestone_date"),
            "status": "preflight_context",
            "title": f"Target milestone {milestone_source_id}",
            "synthetic_data": True,
        }
    if isinstance(milestone_payload, dict):
        sid = milestone_payload.get("source_id")
        aid = milestone_payload.get("aircraft_id")
        if isinstance(sid, str) and aid == aircraft_id:
            records[sid] = CapturedRecord(
                source_id=sid,
                record_type=RecordType.MILESTONE,
                aircraft_id=aircraft_id,
                payload=dict(milestone_payload),
                tool_names={"preflight:get_milestones"},
            )

    return records


def _no_domain_finding(
    spec: SpecialistSpec,
    records: dict[str, CapturedRecord],
    index: int,
) -> Finding | None:
    """Build a bounded finding for an empty but successful specialist branch."""
    aircraft_ids = {
        sid for sid, record in records.items() if record.record_type == RecordType.AIRCRAFT
    }
    milestone_ids = {
        sid for sid, record in records.items() if record.record_type == RecordType.MILESTONE
    }
    if not aircraft_ids and not milestone_ids:
        return None

    if spec.domain == "schedule_risk" and milestone_ids:
        source_ids = milestone_ids | aircraft_ids
        classification = "schedule_risk"
        statement = (
            f"{spec.statement_prefix}: no captured schedule dependency blockers were returned "
            "for this aircraft; the approved milestone context remains the schedule baseline."
        )
    else:
        source_ids = aircraft_ids or milestone_ids
        classification = "other" if spec.domain != "configuration_supply" else "configuration"
        domain_label = spec.statement_prefix.lower()
        statement = (
            f"{spec.statement_prefix}: authorized {domain_label} tools returned no active "
            "domain blockers for this aircraft."
        )

    return Finding(
        finding_id=f"FIND-TEMP-{spec.domain.upper().replace('_', '-')}-{index:03d}",
        statement=statement,
        classification=classification,
        source_refs=_evidence_refs(source_ids, records),
        rationale=(
            f"{spec.statement_prefix} completed its authorized MCP reads and found no "
            "domain-specific blocker records; this finding is scoped to approved preflight evidence."
        ),
        claims=[],
    )


def _ids_from_value(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, str):
        ids |= extract_all_ids_from_text(value)
    elif isinstance(value, dict):
        source_id = value.get("source_id")
        if isinstance(source_id, str):
            ids.add(source_id)
        for nested in value.values():
            ids |= _ids_from_value(nested)
    elif isinstance(value, list):
        for nested in value:
            ids |= _ids_from_value(nested)
    return ids


def _raw_findings(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[Any] = []
    for key in ("findings", "confirmed_findings", "contributing_factors"):
        raw = candidate.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif isinstance(raw, dict):
            values.append(raw)
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"statement": item})
    return result


def _dependencies(records: dict[str, CapturedRecord]) -> list[CapturedRecord]:
    return [
        record
        for record in records.values()
        if record.record_type == RecordType.SCHEDULE_DEPENDENCY
    ]


def _blocker_id(dep: CapturedRecord) -> str | None:
    payload = dep.payload
    for key in (
        "blocker_defect_id",
        "blocker_parts_constraint_id",
        "blocker_change_request_id",
        "blocker_maintenance_task_id",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _expand_relationship_sources(
    source_ids: set[str], records: dict[str, CapturedRecord]
) -> set[str]:
    """Add only the dependency relationship directly connected to each source.

    The expansion is intentionally non-transitive.  Two blockers can point to
    the same blocked test without becoming evidence for each other.
    """
    expanded = set(source_ids)
    deps = _dependencies(records)
    original_ids = set(source_ids)

    has_specific_relationship_source = any(
        source_id in records
        and records[source_id].record_type not in {RecordType.TEST_EVENT, RecordType.AIRCRAFT}
        for source_id in original_ids
    )

    for dep in deps:
        dep_id = dep.source_id
        blocked_test_id = dep.payload.get("blocked_test_id")
        blocker_id = _blocker_id(dep)

        related = {dep_id}
        if isinstance(blocked_test_id, str):
            related.add(blocked_test_id)
        if blocker_id:
            related.add(blocker_id)

        direct_match = dep_id in original_ids or blocker_id in original_ids
        test_only_match = blocked_test_id in original_ids and not has_specific_relationship_source
        if direct_match or test_only_match:
            expanded.update(source_id for source_id in related if source_id in records)
    return expanded


def _record_label(record: CapturedRecord) -> str:
    payload = record.payload
    for key in ("name", "title", "name_or_title", "description", "part_number"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:180]
    return record.source_id


def _summary(record: CapturedRecord) -> str:
    status = record.payload.get("status")
    label = _record_label(record)
    if isinstance(status, str) and status:
        return f"{label} — status {status}."[:300]
    if record.record_type == RecordType.SCHEDULE_DEPENDENCY:
        blocked = record.payload.get("blocked_test_id")
        blocker = _blocker_id(record)
        return f"Dependency {record.source_id}: {blocker} blocks {blocked}."[:300]
    return label[:300]


def _evidence_refs(source_ids: set[str], records: dict[str, CapturedRecord]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for source_id in sorted(source_ids):
        record = records.get(source_id)
        if record is None:
            continue
        refs.append(
            EvidenceRef(
                source_id=source_id,
                record_type=record.record_type.value,
                summary=_summary(record),
            )
        )
    return refs


def _classification(
    spec: SpecialistSpec,
    source_ids: set[str],
    records: dict[str, CapturedRecord],
) -> str:
    types = {records[source_id].record_type for source_id in source_ids if source_id in records}
    if spec.domain == "test_operations":
        if RecordType.SCHEDULE_DEPENDENCY in types:
            return "dependency_blocker"
        if RecordType.DEFECT in types:
            return "defect"
        if RecordType.TEST_EVENT in types:
            return "test_failure"
        return "other"
    if spec.domain == "maintenance":
        if RecordType.MAINTENANCE_TASK in types:
            return "maintenance"
        if RecordType.DEFECT in types:
            return "defect"
        return "other"
    if spec.domain == "configuration_supply":
        if RecordType.PARTS_CONSTRAINT in types:
            return "parts_constraint"
        if RecordType.CHANGE_REQUEST in types:
            return "change_request"
        return "configuration"
    if RecordType.SCHEDULE_DEPENDENCY in types:
        return "dependency_blocker"
    return "schedule_risk"


def _claims(source_ids: set[str], records: dict[str, CapturedRecord]) -> list[Any]:
    result: list[Any] = []
    signatures: set[tuple[Any, ...]] = set()
    deps = _dependencies(records)

    def add(signature: tuple[Any, ...], claim: Any) -> None:
        if signature not in signatures:
            signatures.add(signature)
            result.append(claim)

    for source_id in sorted(source_ids):
        record = records.get(source_id)
        if record is None:
            continue
        payload = record.payload
        if record.record_type == RecordType.TEST_EVENT and payload.get("status") == "aborted":
            add(
                ("test_aborted", source_id),
                TestAbortedClaim(test_event_id=source_id),
            )
        elif record.record_type == RecordType.DEFECT:
            for dep in deps:
                blocked = dep.payload.get("blocked_test_id")
                if dep.payload.get("blocker_defect_id") == source_id and isinstance(blocked, str):
                    add(
                        ("defect_blocks_test", source_id, blocked),
                        DefectBlocksTestClaim(defect_id=source_id, test_event_id=blocked),
                    )
        elif record.record_type == RecordType.PARTS_CONSTRAINT:
            needed = payload.get("needed_by")
            arrival = payload.get("estimated_arrival")
            if needed and arrival and str(arrival) > str(needed):
                add(
                    ("part_arrives_after_need_date", source_id),
                    PartArrivesAfterNeedDateClaim(parts_constraint_id=source_id),
                )
        elif (
            record.record_type == RecordType.CHANGE_REQUEST
            and payload.get("status") == "pending_review"
        ):
            add(
                ("change_request_pending", source_id),
                ChangeRequestPendingClaim(change_request_id=source_id),
            )
        elif record.record_type == RecordType.MAINTENANCE_TASK:
            blocked_test_id: str | None = None
            for dep in deps:
                if dep.payload.get("blocker_maintenance_task_id") == source_id:
                    raw_blocked = dep.payload.get("blocked_test_id")
                    if isinstance(raw_blocked, str):
                        blocked_test_id = raw_blocked
                        break
            add(
                ("maintenance_required", source_id, blocked_test_id),
                MaintenanceRequiredClaim(
                    maintenance_task_id=source_id,
                    test_event_id=blocked_test_id,
                ),
            )
        elif record.record_type == RecordType.SCHEDULE_DEPENDENCY:
            blocked = payload.get("blocked_test_id")
            if isinstance(blocked, str):
                add(
                    ("dependency_blocks_test", source_id, blocked),
                    DependencyBlocksTestClaim(
                        dependency_id=source_id,
                        test_event_id=blocked,
                    ),
                )
    return result


def _safe_model_text(
    value: Any,
    fallback: str,
    allowed_ids: set[str],
    prefix: str,
    *,
    maximum: int = 1000,
) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split()).strip()
    if not text or extract_all_ids_from_text(text) - allowed_ids:
        return fallback
    if not text.lower().startswith(prefix.lower() + ":"):
        text = f"{prefix}: {text}"
    return text[:maximum]


def _fallback_statement(
    spec: SpecialistSpec,
    classification: str,
    source_ids: set[str],
    records: dict[str, CapturedRecord],
) -> str:
    deps = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.SCHEDULE_DEPENDENCY
    ]
    tests = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.TEST_EVENT
    ]
    defects = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.DEFECT
    ]
    parts = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.PARTS_CONSTRAINT
    ]
    changes = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.CHANGE_REQUEST
    ]
    maintenance = [
        records[sid]
        for sid in sorted(source_ids)
        if sid in records and records[sid].record_type == RecordType.MAINTENANCE_TASK
    ]

    prefix = spec.statement_prefix
    if classification == "test_failure" and tests:
        test = tests[0]
        return f"{prefix}: {test.source_id} is {test.payload.get('status', 'not complete')}."
    if classification == "defect" and defects:
        defect = defects[0]
        blocked = next(
            (
                dep.payload.get("blocked_test_id")
                for dep in deps or _dependencies(records)
                if dep.payload.get("blocker_defect_id") == defect.source_id
            ),
            None,
        )
        suffix = f" and blocks {blocked}" if blocked else ""
        return f"{prefix}: {defect.source_id} remains open{suffix}."
    if classification == "maintenance" and maintenance:
        task = maintenance[0]
        blocked = next(
            (
                dep.payload.get("blocked_test_id")
                for dep in deps or _dependencies(records)
                if dep.payload.get("blocker_maintenance_task_id") == task.source_id
            ),
            None,
        )
        suffix = f" before {blocked} can proceed" if blocked else ""
        return f"{prefix}: {task.source_id} remains {task.payload.get('status', 'incomplete')}{suffix}."
    if classification == "parts_constraint" and parts:
        part = parts[0]
        return (
            f"{prefix}: {part.source_id} is needed by {part.payload.get('needed_by')} "
            f"and is forecast to arrive on {part.payload.get('estimated_arrival')}."
        )
    if classification == "change_request" and changes:
        change = changes[0]
        return (
            f"{prefix}: {change.source_id} remains {change.payload.get('status', 'unresolved')}."
        )
    if classification == "dependency_blocker" and deps:
        blocked_tests = sorted(
            {
                str(dep.payload.get("blocked_test_id"))
                for dep in deps
                if dep.payload.get("blocked_test_id")
            }
        )
        target = ", ".join(blocked_tests) or "the target test"
        return f"{prefix}: {target} is blocked by {len(deps)} validated dependency record(s)."
    first = next((records[sid] for sid in sorted(source_ids) if sid in records), None)
    if first is not None:
        return (
            f"{prefix}: {first.source_id} requires attention based on validated operational data."
        )
    return f"{prefix}: no evidence-backed finding was available."


def _fallback_rationale(
    spec: SpecialistSpec,
    source_ids: set[str],
    records: dict[str, CapturedRecord],
) -> str:
    ids = ", ".join(sorted(source_ids))
    return (
        f"{spec.statement_prefix} evidence returned by authorized MCP tools supports this finding: "
        f"{ids}."
    )[:1000]


def _make_finding(
    spec: SpecialistSpec,
    source_ids: set[str],
    records: dict[str, CapturedRecord],
    raw: dict[str, Any] | None,
    index: int,
) -> Finding | None:
    primary_ids = {source_id for source_id in source_ids if source_id in records}
    if not primary_ids:
        return None
    classification = _classification(spec, primary_ids, records)
    valid_ids = _expand_relationship_sources(primary_ids, records)
    fallback_statement = _fallback_statement(spec, classification, valid_ids, records)
    fallback_rationale = _fallback_rationale(spec, valid_ids, records)
    raw = raw or {}
    statement = _safe_model_text(
        raw.get("statement") or raw.get("finding") or raw.get("summary"),
        fallback_statement,
        valid_ids,
        spec.statement_prefix,
    )
    rationale = _safe_model_text(
        raw.get("rationale") or raw.get("reason"),
        fallback_rationale,
        valid_ids,
        spec.statement_prefix,
    )

    return Finding(
        finding_id=f"FIND-TEMP-{spec.domain.upper().replace('_', '-')}-{index:03d}",
        statement=statement,
        classification=classification,
        source_refs=_evidence_refs(valid_ids, records),
        rationale=rationale,
        claims=_claims(valid_ids, records),
    )


def _raw_source_ids_for_finding(
    raw: dict[str, Any], records: dict[str, CapturedRecord]
) -> set[str]:
    found = _ids_from_value(raw)
    return {source_id for source_id in found if source_id in records}


def _fallback_groups(spec: SpecialistSpec, records: dict[str, CapturedRecord]) -> list[set[str]]:
    groups: list[set[str]] = []
    deps = _dependencies(records)

    if spec.domain == "test_operations":
        for record in records.values():
            if record.record_type == RecordType.TEST_EVENT and record.payload.get("status") in {
                "aborted",
                "blocked",
            }:
                groups.append(_expand_relationship_sources({record.source_id}, records))
        for record in records.values():
            if record.record_type == RecordType.DEFECT:
                groups.append(_expand_relationship_sources({record.source_id}, records))
        if deps:
            groups.append(_expand_relationship_sources({dep.source_id for dep in deps}, records))

    elif spec.domain == "maintenance":
        for record in records.values():
            if record.record_type in {RecordType.MAINTENANCE_TASK, RecordType.DEFECT}:
                groups.append(_expand_relationship_sources({record.source_id}, records))

    elif spec.domain == "configuration_supply":
        for record in records.values():
            if record.record_type in {RecordType.PARTS_CONSTRAINT, RecordType.CHANGE_REQUEST}:
                groups.append(_expand_relationship_sources({record.source_id}, records))

    elif spec.domain == "schedule_risk":
        by_test: dict[str, set[str]] = {}
        for dep in deps:
            blocked = dep.payload.get("blocked_test_id")
            key = str(blocked or "unknown")
            by_test.setdefault(key, set()).add(dep.source_id)
        for dep_ids in by_test.values():
            groups.append(_expand_relationship_sources(dep_ids, records))
        if not groups:
            for record in records.values():
                if record.record_type == RecordType.TEST_EVENT and record.payload.get(
                    "status"
                ) in {
                    "blocked",
                    "aborted",
                }:
                    groups.append({record.source_id})

    # Stable de-duplication by source-id set.
    unique: list[set[str]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        filtered = {source_id for source_id in group if source_id in records}
        key = tuple(sorted(filtered))
        if filtered and key not in seen:
            seen.add(key)
            unique.append(filtered)
    return unique


def canonicalize_specialist_report(
    spec: SpecialistSpec,
    candidate: dict[str, Any],
    state: Any,
) -> tuple[SpecialistReport, dict[str, Any]]:
    """Build one canonical report from model wording and captured MCP evidence."""
    aircraft_id = _state_aircraft_id(state, candidate)
    if aircraft_id is None:
        raise SpecialistNormalizationError("Validated aircraft scope is unavailable.")

    records, tool_call_count = _collect_records(state, spec, aircraft_id)
    raw_items = _raw_findings(candidate)
    findings: list[Finding] = []
    represented: set[tuple[str, ...]] = set()

    for raw in raw_items:
        source_ids = _raw_source_ids_for_finding(raw, records)
        if not source_ids:
            continue
        finding = _make_finding(spec, source_ids, records, raw, len(findings) + 1)
        if finding is None:
            continue
        key = tuple(sorted(ref.source_id for ref in finding.source_refs))
        if key in represented:
            continue
        represented.add(key)
        findings.append(finding)

    fallback_count = 0
    for source_ids in _fallback_groups(spec, records):
        expanded = _expand_relationship_sources(source_ids, records)
        key = tuple(sorted(expanded))
        if not key or key in represented:
            continue
        finding = _make_finding(spec, expanded, records, None, len(findings) + 1)
        if finding is None:
            continue
        represented.add(key)
        findings.append(finding)
        fallback_count += 1

    if not findings and tool_call_count > 0:
        preflight_records = _preflight_context_records(state, aircraft_id)
        if preflight_records:
            records.update(preflight_records)
            finding = _no_domain_finding(spec, records, len(findings) + 1)
            if finding is not None:
                findings.append(finding)
                represented.add(tuple(sorted(ref.source_id for ref in finding.source_refs)))
                fallback_count += 1

    # Ensure statement uniqueness within a report without changing evidence.
    seen_statements: set[str] = set()
    for finding in findings:
        normalized = finding.statement.strip().lower()
        if normalized in seen_statements:
            first_id = finding.source_refs[0].source_id
            finding.statement = f"{finding.statement.rstrip('.')} [{first_id}]."
            normalized = finding.statement.strip().lower()
        seen_statements.add(normalized)

    raw_source_ids = set(records)
    if spec.domain == "schedule_risk":
        milestone_source_id = state.get("milestone_source_id")
        if isinstance(milestone_source_id, str) and re.fullmatch(
            r"MS-\d{3}-[A-Z0-9-]+", milestone_source_id
        ):
            # The target milestone is obtained through the approved preflight
            # MCP call rather than a specialist tool.  Keep it in raw context
            # without turning it into a specialist finding reference.
            raw_source_ids.add(milestone_source_id)

    report = SpecialistReport(
        domain=spec.domain,
        aircraft_id=aircraft_id,
        findings=findings,
        raw_source_ids=sorted(raw_source_ids),
    )
    diagnostics = {
        "status": "canonicalized",
        "candidate_json_object": bool(candidate),
        "captured_tool_calls": tool_call_count,
        "captured_record_count": len(records),
        "raw_finding_count": len(raw_items),
        "normalized_finding_count": len(findings),
        "fallback_finding_count": fallback_count,
    }
    return report, diagnostics


def canonicalize_specialist_state_output(
    output_key: str,
    raw_output: Any,
    state: Any,
) -> tuple[SpecialistReport, dict[str, Any]]:
    """Canonicalize a stored specialist output after the parallel stage.

    This is a second-line deterministic boundary for ADK versions where a
    callback state delta is committed only after the model response event.
    ``ReportValidatorAgent`` can therefore repair the stored output once all
    parallel branches and their MCP captures have completed.
    """
    spec = next((value for value in _SPECS.values() if value.output_key == output_key), None)
    if spec is None:
        raise KeyError(f"Unknown specialist output key: {output_key}")

    if isinstance(raw_output, dict):
        candidate = raw_output
    elif isinstance(raw_output, str):
        candidate = _extract_json_object(raw_output)
    else:
        candidate = {}
    return canonicalize_specialist_report(spec, candidate, state)


def make_specialist_response_normalizer(domain: str):
    """Return an ADK ``after_model_callback`` for one specialist domain."""
    if domain not in _SPECS:
        raise KeyError(f"Unknown specialist normalization domain: {domain}")
    spec = _SPECS[domain]

    def normalize_specialist_response(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        # ADK invokes after_model_callback for intermediate function-call turns
        # as well as the final textual response.  Never replace a function-call
        # response, otherwise the authorized MCP tools would not execute.
        if _has_function_calls(llm_response):
            return None

        text = _llm_response_text(llm_response)
        if not text.strip():
            return None
        candidate = _extract_json_object(text)
        metadata_key = f"temp:{spec.output_key}_normalization"
        try:
            report, diagnostics = canonicalize_specialist_report(
                spec, candidate, callback_context.state
            )
        except Exception as exc:
            callback_context.state[metadata_key] = {
                "status": "failed",
                "exception_type": type(exc).__name__,
                "candidate_json_object": bool(candidate),
            }
            logger.error(
                "specialist_normalization_failed domain=%s exception_type=%s",
                spec.domain,
                type(exc).__name__,
            )
            return None

        callback_context.state[metadata_key] = diagnostics
        replacement = genai_types.Content(
            role="model",
            parts=[genai_types.Part(text=report.model_dump_json())],
        )
        return llm_response.model_copy(update={"content": replacement}, deep=True)

    # Stable name makes introspection and test diagnostics readable.
    normalize_specialist_response.__name__ = f"normalize_{domain}_specialist_response"
    return normalize_specialist_response




def make_specialist_model_error_fallback(domain: str):
    """Return an ADK ``on_model_error_callback`` for one specialist domain.

    Live provider failures can occur after a specialist has already completed
    its required MCP calls.  When that happens, the canonical evidence is
    already present in the specialist-specific callback state.  Returning a
    deterministic SpecialistReport keeps the investigation available without
    trusting an incomplete model response.  If no evidence was captured, the
    callback returns ``None`` so ADK propagates the original provider error.
    """
    if domain not in _SPECS:
        raise KeyError(f"Unknown specialist normalization domain: {domain}")
    spec = _SPECS[domain]

    async def recover_specialist_response(
        callback_context: CallbackContext,
        llm_request: Any,
        error: Exception,
    ) -> LlmResponse | None:
        del llm_request  # The request can contain prompts and tool payloads.
        metadata_key = f"temp:{spec.output_key}_normalization"
        if not _evidence_entries(callback_context.state, spec.evidence_key):
            callback_context.state[metadata_key] = {
                "status": "failed_no_evidence",
                "exception_type": type(error).__name__,
            }
            return None

        try:
            report, diagnostics = canonicalize_specialist_report(
                spec, {}, callback_context.state
            )
        except Exception as exc:
            callback_context.state[metadata_key] = {
                "status": "failed",
                "exception_type": type(exc).__name__,
                "provider_exception_type": type(error).__name__,
            }
            logger.error(
                "specialist_error_recovery_failed domain=%s provider_exception_type=%s exception_type=%s",
                spec.domain,
                type(error).__name__,
                type(exc).__name__,
            )
            return None

        diagnostics = dict(diagnostics)
        diagnostics["status"] = "recovered_from_model_error"
        diagnostics["provider_exception_type"] = type(error).__name__
        callback_context.state[metadata_key] = diagnostics
        replacement = genai_types.Content(
            role="model",
            parts=[genai_types.Part(text=report.model_dump_json())],
        )
        return LlmResponse(content=replacement)

    recover_specialist_response.__name__ = f"recover_{domain}_specialist_response"
    return recover_specialist_response

__all__ = [
    "SpecialistNormalizationError",
    "canonicalize_specialist_report",
    "canonicalize_specialist_state_output",
    "make_specialist_model_error_fallback",
    "make_specialist_response_normalizer",
]
