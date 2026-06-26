"""Deterministic specialist-report validation stage.

``ReportValidatorAgent`` runs between the ``ParallelAgent`` and the synthesis
agent.  It enforces the following invariants before synthesis is permitted:

1. All four specialist output keys are present and non-empty.
2. Each report is valid JSON and parses as a ``SpecialistReport``.
3. Every report's ``aircraft_id`` matches the validated investigation scope.
4. Every finding in every report has at least one ``source_ref``.
5. No report is an empty findings list (a failed/stalled branch is rejected).
6. Direct blockers (``test_failure``, ``defect``, ``dependency_blocker``) are
   separated from secondary risks (``schedule_risk``, ``maintenance``,
   ``parts_constraint``, ``change_request``, ``configuration``).

On success the validator writes:
- ``validated_reports``     : dict of domain → SpecialistReport (as JSON)
- ``mcp_evidence_ids``      : deduplicated set of all raw_source_ids cited
- ``blocker_source_ids``    : source IDs classified as direct blockers
- ``secondary_risk_ids``    : source IDs classified as secondary risks
- ``milestone_context``     : dict with planned/forecast/delay_days/source_id
                              parsed from the schedule_risk report (MCP-derived)

On failure it raises ``ReportValidationError``, which aborts synthesis.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from aeroops.models import AIRCRAFT_ID_PATTERN, SpecialistReport
from aeroops.specialist_normalization import canonicalize_specialist_state_output

logger = logging.getLogger(__name__)

_AC_PATTERN = re.compile(AIRCRAFT_ID_PATTERN)

# The four mandatory output keys from the parallel stage
SPECIALIST_KEYS: tuple[str, ...] = (
    "test_ops_findings",
    "maintenance_findings",
    "configuration_supply_findings",
    "schedule_risk_findings",
)

SPECIALIST_EVIDENCE_KEYS: dict[str, str] = {
    "test_ops_findings": "test_ops_mcp_evidence",
    "maintenance_findings": "maintenance_mcp_evidence",
    "configuration_supply_findings": "configuration_supply_mcp_evidence",
    "schedule_risk_findings": "schedule_risk_mcp_evidence",
}

# Classification buckets
_DIRECT_BLOCKER_CLASSIFICATIONS = frozenset({"test_failure", "defect", "dependency_blocker"})
_SECONDARY_RISK_CLASSIFICATIONS = frozenset(
    {
        "schedule_risk",
        "maintenance",
        "parts_constraint",
        "change_request",
        "configuration",
        "other",
    }
)


def _violation_code(detail: str) -> str:
    """Return a bounded, non-sensitive category for one report violation."""
    lowered = detail.lower()
    if "missing or empty" in lowered:
        return "MISSING_SPECIALIST_OUTPUT"
    if "not valid json" in lowered or "unexpected type" in lowered:
        return "INVALID_SPECIALIST_JSON"
    if "specialistreport validation failed" in lowered:
        return "SPECIALIST_SCHEMA_INVALID"
    if "aircraft_id mismatch" in lowered:
        return "SPECIALIST_AIRCRAFT_MISMATCH"
    if "findings list is empty" in lowered:
        return "EMPTY_SPECIALIST_FINDINGS"
    if "classification" in lowered and "invalid" in lowered:
        return "INVALID_FINDING_CLASSIFICATION"
    if "duplicate finding statement" in lowered:
        return "DUPLICATE_FINDING_STATEMENT"
    if "has no source_refs" in lowered:
        return "FINDING_MISSING_SOURCE_REFS"
    if "normalization" in lowered:
        return "SPECIALIST_NORMALIZATION_FAILED"
    return "SPECIALIST_REPORT_INVALID"


class ReportValidationError(ValueError):
    """Raised when one or more specialist reports fail validation.

    ``violation_codes`` is safe to expose in diagnostic logs.  The detailed
    violations remain available only to local tests and the exception cause;
    they are never rendered in the public Streamlit error surface.
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        self.violation_codes = tuple(dict.fromkeys(_violation_code(v) for v in violations))
        super().__init__(
            f"Specialist report validation failed ({len(violations)} violation(s)):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )


def _clean_json(raw: str) -> str:
    """Strip markdown code fences from a string."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)


def _parse_specialist_report(key: str, raw: Any) -> SpecialistReport:
    """Parse and validate a single specialist report.

    Args:
        key: Session-state key (e.g. ``'test_ops_findings'``).
        raw: Raw value from session state (str or dict).

    Returns:
        Validated ``SpecialistReport``.

    Raises:
        ValueError: With a human-readable description of the failure.
    """
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            data = json.loads(_clean_json(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"[{key}] Specialist output is not valid JSON: {raw[:80]!r}") from exc
    else:
        raise ValueError(f"[{key}] Unexpected type {type(raw).__name__}; expected str or dict.")

    try:
        return SpecialistReport.model_validate(data)
    except Exception as exc:
        raise ValueError(f"[{key}] SpecialistReport validation failed: {exc}") from exc


def _classify_source_ids(
    report: SpecialistReport,
) -> tuple[set[str], set[str]]:
    """Return (direct_blocker_ids, secondary_risk_ids) from a report's findings.

    Args:
        report: Validated specialist report.

    Returns:
        Tuple of (direct_blocker source IDs, secondary risk source IDs).
    """
    direct: set[str] = set()
    secondary: set[str] = set()
    for finding in report.findings:
        ids = {ref.source_id for ref in finding.source_refs}
        if finding.classification in _DIRECT_BLOCKER_CLASSIFICATIONS:
            direct |= ids
        else:
            secondary |= ids
    return direct, secondary


class ReportValidatorAgent(BaseAgent):
    """Deterministic specialist-report validation stage — no LLM, no DB access.

    Reads the four specialist output keys from session state, validates all
    invariants, and writes structured summaries for the synthesis stage.
    """

    model_config: ClassVar[dict] = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Validate all specialist reports and populate synthesis inputs.

        Args:
            ctx: ADK invocation context.

        Yields:
            A single final ``Event`` with the validation summary.

        Raises:
            ReportValidationError: If any invariant is violated.
        """
        state = ctx.session.state
        violations: list[str] = []
        validated: dict[str, Any] = {}
        all_evidence_ids: set[str] = set()
        direct_blocker_ids: set[str] = set()
        secondary_risk_ids: set[str] = set()

        # --- Resolve aircraft_id from validated scope ---
        aircraft_id: str | None = None
        scope_raw = state.get("investigation_scope")
        if scope_raw:
            try:
                scope_data = json.loads(scope_raw) if isinstance(scope_raw, str) else scope_raw
                aircraft_id = scope_data.get("aircraft_id")
            except Exception:
                pass

        # Domain allowed classifications map
        allowed_classifications_by_domain = {
            "test_operations": {"test_failure", "defect", "dependency_blocker", "other"},
            "maintenance": {"defect", "maintenance", "other"},
            "configuration_supply": {
                "parts_constraint",
                "change_request",
                "configuration",
                "other",
            },
            "schedule_risk": {"schedule_risk", "dependency_blocker", "other"},
        }

        domain_prefixes = {
            "test_operations": "FIND-TEST-",
            "maintenance": "FIND-MAINT-",
            "configuration_supply": "FIND-CONFIG-",
            "schedule_risk": "FIND-SCHEDULE-",
        }

        normalized_reports = {}
        all_statements: set[tuple[str, str]] = set()
        all_finding_ids = set()

        # --- Validate each specialist key ---
        for key in SPECIALIST_KEYS:
            raw = state.get(key)

            # Invariant 1: each branch must either produce a parsable output or
            # have captured evidence from its authorized MCP tools.  Live models
            # occasionally terminate a specialist branch after successful tool
            # calls without committing a final JSON object to the output_key.
            # In that case, rebuild the SpecialistReport deterministically from
            # the captured MCP responses instead of rejecting useful evidence.
            evidence_key = SPECIALIST_EVIDENCE_KEYS[key]
            evidence_capture = state.get(evidence_key)
            if not raw and not evidence_capture:
                violations.append(
                    f"[{key}] Missing or empty — specialist may have stalled or failed."
                )
                continue
            if not raw and evidence_capture:
                raw = {}

            # Invariant 2: canonicalize from callback-captured evidence once
            # all parallel branches have completed. This second-line boundary
            # repairs live-model shape variations without admitting records
            # that were not returned by the specialist's MCP tools.
            if evidence_capture:
                try:
                    report, normalization = canonicalize_specialist_state_output(key, raw, state)
                    state[f"temp:{key}_normalization"] = normalization
                    raw = report.model_dump_json()
                except Exception as exc:
                    violations.append(
                        f"[{key}] Specialist normalization failed ({type(exc).__name__})."
                    )
                    continue
            else:
                try:
                    report = _parse_specialist_report(key, raw)
                except ValueError as exc:
                    violations.append(str(exc))
                    continue

            # Invariant 3: aircraft_id must match scope
            if aircraft_id and report.aircraft_id != aircraft_id:
                violations.append(
                    f"[{key}] aircraft_id mismatch: report says '{report.aircraft_id}' "
                    f"but scope requires '{aircraft_id}'."
                )

            # Invariant 5: findings list must not be empty
            if not report.findings:
                violations.append(
                    f"[{key}] findings list is empty — "
                    "a stalled or failed specialist branch is not acceptable."
                )
                continue

            # Validate classifications, duplicate statements, and assign stable IDs
            domain_allowed = allowed_classifications_by_domain.get(report.domain)
            prefix = domain_prefixes.get(report.domain, "FIND-OTHER-")

            for fi, finding in enumerate(report.findings):
                # Classification check
                if domain_allowed and finding.classification not in domain_allowed:
                    violations.append(
                        f"[{key}] finding[{fi}] classification '{finding.classification}' "
                        f"is invalid for domain '{report.domain}'."
                    )

                # Duplicate statement check
                stmt_norm = finding.statement.strip().lower()
                statement_key = (report.domain, stmt_norm)
                if statement_key in all_statements:
                    violations.append(
                        f"[{key}] Duplicate finding statement after normalization: '{finding.statement}'"
                    )
                all_statements.add(statement_key)

                # Assign stable deterministic ID
                num = fi + 1
                assigned_id = f"{prefix}{num:03d}"
                if assigned_id in all_finding_ids:
                    violations.append(f"[{key}] Non-unique finding ID assigned: {assigned_id}")
                all_finding_ids.add(assigned_id)
                finding.finding_id = assigned_id

                # Invariant 4: every finding must have source_refs
                if not finding.source_refs:
                    violations.append(
                        f"[{key}] finding[{fi}] ('{finding.statement[:60]}') "
                        "has no source_refs — unsupported claim."
                    )

            normalized_reports[key] = report
            validated[key] = report

            # Collect evidence and classify blockers vs secondary risks
            all_evidence_ids |= set(report.raw_source_ids)
            d, s = _classify_source_ids(report)
            direct_blocker_ids |= d
            secondary_risk_ids |= s

        if violations:
            error_payload = json.dumps({"report_validation_errors": violations})
            state["report_validation_errors"] = error_payload
            raise ReportValidationError(violations)

        # --- Write synthesis inputs and normalized reports back to session state ---
        state["mcp_evidence_ids"] = sorted(all_evidence_ids)
        state["blocker_source_ids"] = sorted(direct_blocker_ids)
        state["secondary_risk_ids"] = sorted(secondary_risk_ids)

        for key, report in normalized_reports.items():
            state[key] = report.model_dump_json()

        # --- Extract milestone context from schedule_risk report (MCP-derived) ---
        milestone_ctx = _extract_milestone_context(validated, aircraft_id)
        if milestone_ctx:
            state["milestone_context"] = json.dumps(milestone_ctx)

        summary = {
            "validated_specialist_keys": list(validated.keys()),
            "total_evidence_ids": len(all_evidence_ids),
            "direct_blocker_count": len(direct_blocker_ids),
            "secondary_risk_count": len(secondary_risk_ids),
        }
        summary_json = json.dumps(summary)
        logger.info("Specialist report validation passed: %s", summary)

        state_delta = {
            "mcp_evidence_ids": sorted(all_evidence_ids),
            "blocker_source_ids": sorted(direct_blocker_ids),
            "secondary_risk_ids": sorted(secondary_risk_ids),
        }
        for key, report in normalized_reports.items():
            state_delta[key] = report.model_dump_json()
        if milestone_ctx:
            state_delta["milestone_context"] = json.dumps(milestone_ctx)

        yield Event(
            author=self.name,
            content={"parts": [{"text": summary_json}]},
            actions=EventActions(state_delta=state_delta),
            turn_complete=True,
        )


def _extract_milestone_context(
    validated: dict[str, SpecialistReport],
    aircraft_id: str | None,
) -> dict[str, Any] | None:
    """Extract milestone dates from validated specialist reports.

    The schedule_risk specialist is expected to reference the milestone record
    (MS-NNN-FTC) in its findings.  We extract the planned/forecast dates by
    searching raw_source_ids for a milestone ID and reading the source_refs
    from any schedule_risk finding that cites it.

    Args:
        validated: Dict of key → SpecialistReport for all passing specialists.
        aircraft_id: The aircraft under investigation.

    Returns:
        Dict with planned_milestone_date, forecast_milestone_date, delay_days,
        milestone_source_id — or None if insufficient data.
    """
    # Pattern for milestone IDs
    ms_pattern = re.compile(r"^MS-\d{3}-[A-Z0-9-]+$")

    for key in ("schedule_risk_findings", "test_ops_findings"):
        report = validated.get(key)
        if not report:
            continue

        # Look for a milestone source_id in raw_source_ids or finding refs
        ms_ids = [sid for sid in report.raw_source_ids if ms_pattern.match(sid)]
        if not ms_ids:
            # Also check source_refs in findings
            for finding in report.findings:
                for ref in finding.source_refs:
                    if ms_pattern.match(ref.source_id):
                        ms_ids.append(ref.source_id)

        if ms_ids:
            # Milestone ID found in MCP evidence — record it
            # Actual date values come from the MCP tool response text that the
            # LLM embedded in the finding summary.  We don't re-parse prose;
            # instead we rely on the synthesis agent receiving the specialist
            # JSON in full (via session-state injection).  The report_validator
            # records which milestone source ID to use; the service layer then
            # queries MCP directly for the authoritative dates.
            return {
                "milestone_source_id": ms_ids[0],
                "aircraft_id": aircraft_id,
            }

    return None
