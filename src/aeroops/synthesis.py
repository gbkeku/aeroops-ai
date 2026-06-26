"""Deterministic normalization for the executive synthesis response.

Gemini is asked for a concise executive JSON draft, but live models can
occasionally return a near-miss shape (for example, string source references or
a missing required field). Provider-side ``output_schema`` validation would
abort the entire workflow before AeroOps could apply its evidence controls.

The synthesis agent therefore uses no provider-side schema. An agent-level
``after_model_callback`` converts any returned draft into a canonical
:class:`~aeroops.models.ExecutiveBrief` assembled from the four already-validated
specialist reports. The service then validates that brief with Pydantic and the
EvidenceCatalog. The model remains responsible for executive wording, status,
confidence, and proposed actions; operational findings, claims, dates, and
evidence references always come from validated state.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types
from pydantic import ValidationError

from aeroops.models import (
    EvidenceRef,
    ExecutiveBrief,
    Finding,
    RecommendedAction,
    SpecialistReport,
)
from aeroops.validation import extract_all_ids_from_text

logger = logging.getLogger(__name__)

_DIRECT_CLASSIFICATIONS = frozenset({"test_failure", "defect", "dependency_blocker"})
_VALID_STATUSES = frozenset({"green", "amber", "red", "unknown"})
_VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
_VALID_OWNER_ROLES = frozenset(
    {
        "test_lead",
        "maintenance_lead",
        "supply_chain",
        "engineering",
        "program_management",
        "quality_assurance",
        "unknown",
    }
)
_FINDING_ID_RE = re.compile(r"\bFIND-[A-Z]+-\d{3}\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DELAY_RE = re.compile(r"\b(\d+)\s*(?:-|\s)?days?\b", re.IGNORECASE)

_SPECIALIST_REPORT_KEYS: tuple[str, ...] = (
    "test_ops_findings",
    "maintenance_findings",
    "configuration_supply_findings",
    "schedule_risk_findings",
)


class SynthesisNormalizationError(ValueError):
    """Raised when validated specialist state cannot form an executive brief."""


def _clean_json(text: str) -> str:
    """Remove a single surrounding Markdown JSON fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort extraction of the first JSON object from visible model text."""
    cleaned = _clean_json(text)
    if not cleaned:
        return {}

    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start < 0:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _llm_response_text(response: LlmResponse) -> str:
    """Return visible text parts from an ADK ``LlmResponse``."""
    if response.content is None or not response.content.parts:
        return ""
    return "".join(
        part.text or ""
        for part in response.content.parts
        if part.text and not getattr(part, "thought", False)
    )


def _parse_report(key: str, value: Any) -> SpecialistReport:
    """Parse one normalized specialist report from session state."""
    if isinstance(value, SpecialistReport):
        return value
    if isinstance(value, dict):
        return SpecialistReport.model_validate(value)
    if isinstance(value, str):
        return SpecialistReport.model_validate_json(_clean_json(value))
    raise SynthesisNormalizationError(
        f"Specialist report state {key!r} has unsupported type {type(value).__name__}."
    )


def _load_specialist_findings(state: Any) -> list[Finding]:
    """Load all four validated reports and return their stable findings."""
    findings: list[Finding] = []
    missing: list[str] = []
    for key in _SPECIALIST_REPORT_KEYS:
        raw = state.get(key)
        if raw is None or raw == "":
            missing.append(key)
            continue
        report = _parse_report(key, raw)
        findings.extend(f.model_copy(deep=True) for f in report.findings)

    if missing:
        raise SynthesisNormalizationError(
            "Cannot normalize synthesis because validated specialist reports are missing: "
            + ", ".join(missing)
        )
    if not findings:
        raise SynthesisNormalizationError(
            "Cannot normalize synthesis because no validated specialist findings are available."
        )

    ids = [finding.finding_id for finding in findings]
    if len(ids) != len(set(ids)):
        raise SynthesisNormalizationError(
            "Cannot normalize synthesis because specialist finding IDs are not unique."
        )
    return findings


def _string(value: Any, fallback: str, *, maximum: int = 2000) -> str:
    """Return a bounded, non-empty string or the supplied fallback."""
    if isinstance(value, str):
        cleaned = " ".join(value.split()).strip()
        if cleaned:
            return cleaned[:maximum]
    return fallback


def _safe_text(value: Any, fallback: str, allowed_ids: set[str]) -> str:
    """Use model text only when every AeroOps identifier is evidence-backed."""
    text = _string(value, fallback)
    if extract_all_ids_from_text(text) - allowed_ids:
        return fallback
    return text


def _safe_text_list(value: Any, allowed_ids: set[str], *, maximum_items: int = 8) -> list[str]:
    """Normalize a list of bounded strings and drop unsupported ID references."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).strip()
        if not text or extract_all_ids_from_text(text) - allowed_ids:
            continue
        if text not in result:
            result.append(text[:1000])
        if len(result) >= maximum_items:
            break
    return result


def _normalize_status(value: Any, delay_days: int) -> str:
    # A positive, evidence-backed schedule slip must never be understated by
    # model prose. The model may only influence status when no late forecast is
    # present.
    if delay_days > 0:
        return "red"
    if isinstance(value, str) and value.strip().lower() in _VALID_STATUSES:
        return value.strip().lower()
    if delay_days == 0:
        return "green"
    return "amber"


def _normalize_confidence(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in _VALID_CONFIDENCE:
        return value.strip().lower()
    return "high"


def _normalize_due_date(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            pass
    return fallback


def _source_ids_from_action(action: dict[str, Any]) -> set[str]:
    source_ids: set[str] = set()
    refs = action.get("source_refs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, str):
                source_ids.add(ref)
            elif isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
                source_ids.add(ref["source_id"])
    for key in ("action", "rationale"):
        if isinstance(action.get(key), str):
            source_ids |= extract_all_ids_from_text(action[key])
    return source_ids


def _candidate_supporting_ids(
    action: dict[str, Any],
    finding_by_id: dict[str, Finding],
) -> list[str]:
    """Resolve a candidate action to validated finding IDs only."""
    result: list[str] = []
    raw_ids = action.get("supporting_finding_ids")
    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            if isinstance(raw_id, str) and raw_id in finding_by_id and raw_id not in result:
                result.append(raw_id)

    if result:
        return result

    candidate_sources = _source_ids_from_action(action)
    for finding_id, finding in finding_by_id.items():
        finding_sources = {ref.source_id for ref in finding.source_refs}
        if candidate_sources & finding_sources:
            result.append(finding_id)
    return result


def _dedupe_evidence_refs(findings: list[Finding]) -> list[EvidenceRef]:
    refs: dict[str, EvidenceRef] = {}
    for finding in findings:
        for ref in finding.source_refs:
            refs.setdefault(ref.source_id, ref.model_copy(deep=True))
    return list(refs.values())


def _default_action_fields(finding: Finding) -> tuple[str, str, str]:
    """Return evidence-safe (action, rationale, owner_role) defaults."""
    mapping = {
        "test_failure": (
            "Complete troubleshooting and rerun the affected test sequence.",
            "The validated test finding must be resolved before the test campaign can proceed.",
            "test_lead",
        ),
        "defect": (
            "Resolve the cited defect and verify the corrective action before retest.",
            "The defect is supported by specialist evidence and remains a direct blocker.",
            "engineering",
        ),
        "maintenance": (
            "Complete the required maintenance task before test release.",
            "The maintenance task is incomplete and is required before downstream work proceeds.",
            "maintenance_lead",
        ),
        "parts_constraint": (
            "Expedite the constrained part and confirm availability before the need date.",
            "The part constraint is evidence-backed and exposes the program schedule.",
            "supply_chain",
        ),
        "change_request": (
            "Complete the required change review and release the approved configuration.",
            "The pending change request must clear its required review before release.",
            "engineering",
        ),
        "configuration": (
            "Complete configuration review and release the validated baseline.",
            "The configuration finding must be resolved before dependent work proceeds.",
            "engineering",
        ),
        "dependency_blocker": (
            "Clear the cited dependency before releasing the blocked test.",
            "The dependency chain directly blocks the next test activity.",
            "program_management",
        ),
        "schedule_risk": (
            "Track a schedule-recovery plan against the target milestone.",
            "The schedule finding indicates exposure that requires coordinated recovery tracking.",
            "program_management",
        ),
        "other": (
            "Review and resolve the evidence-backed issue.",
            "The validated specialist finding requires accountable follow-up.",
            "program_management",
        ),
    }
    return mapping.get(finding.classification, mapping["other"])


def _normalize_actions(
    candidate: dict[str, Any],
    selected_findings: list[Finding],
    planned_date: str,
    allowed_ids: set[str],
) -> list[RecommendedAction]:
    """Normalize model actions and fill uncovered findings deterministically."""
    finding_by_id = {finding.finding_id: finding for finding in selected_findings}
    actions: list[RecommendedAction] = []
    covered: set[str] = set()
    seen_text: set[str] = set()

    candidate_actions = candidate.get("recommended_actions")
    if isinstance(candidate_actions, list):
        for item in candidate_actions:
            if not isinstance(item, dict):
                continue
            supporting_ids = _candidate_supporting_ids(item, finding_by_id)
            if not supporting_ids:
                continue
            linked = [finding_by_id[fid] for fid in supporting_ids]
            first = linked[0]
            default_action, default_rationale, default_owner = _default_action_fields(first)
            action_text = _safe_text(item.get("action"), default_action, allowed_ids)
            normalized_key = action_text.casefold()
            if normalized_key in seen_text:
                continue
            seen_text.add(normalized_key)
            rationale = _safe_text(item.get("rationale"), default_rationale, allowed_ids)
            owner = item.get("owner_role")
            if not isinstance(owner, str) or owner not in _VALID_OWNER_ROLES:
                owner = default_owner
            refs = _dedupe_evidence_refs(linked)
            actions.append(
                RecommendedAction(
                    action_id=f"ACT-{len(actions) + 1:03d}",
                    action=action_text,
                    classification=first.classification,
                    supporting_finding_ids=supporting_ids,
                    source_refs=refs,
                    rationale=rationale,
                    owner_role=owner,
                    suggested_due_date=_normalize_due_date(
                        item.get("suggested_due_date"), planned_date
                    ),
                )
            )
            covered.update(supporting_ids)

    # Every validated direct blocker and key secondary constraint receives an
    # accountable action even when the model omits or malformed an action.
    actionable = {
        "test_failure",
        "defect",
        "maintenance",
        "parts_constraint",
        "change_request",
        "configuration",
        "dependency_blocker",
        "schedule_risk",
    }
    for finding in selected_findings:
        if finding.finding_id in covered or finding.classification not in actionable:
            continue
        action_text, rationale, owner = _default_action_fields(finding)
        normalized_key = action_text.casefold()
        if normalized_key in seen_text:
            # Link another finding with the same recommended operation to the
            # existing action without duplicating the row.
            for existing in actions:
                if existing.action.casefold() == normalized_key:
                    if finding.finding_id not in existing.supporting_finding_ids:
                        existing.supporting_finding_ids.append(finding.finding_id)
                    existing_refs = {ref.source_id for ref in existing.source_refs}
                    for ref in finding.source_refs:
                        if ref.source_id not in existing_refs:
                            existing.source_refs.append(ref.model_copy(deep=True))
                    covered.add(finding.finding_id)
                    break
            continue
        seen_text.add(normalized_key)
        actions.append(
            RecommendedAction(
                action_id=f"ACT-{len(actions) + 1:03d}",
                action=action_text,
                classification=finding.classification,
                supporting_finding_ids=[finding.finding_id],
                source_refs=[ref.model_copy(deep=True) for ref in finding.source_refs],
                rationale=rationale,
                owner_role=owner,
                suggested_due_date=planned_date,
            )
        )
        covered.add(finding.finding_id)

    # Reassign IDs after any merge so the sequence is always stable.
    for index, action in enumerate(actions, start=1):
        action.action_id = f"ACT-{index:03d}"
    return actions


def canonicalize_executive_synthesis(
    candidate: dict[str, Any],
    state: Any,
) -> ExecutiveBrief:
    """Build a strict ``ExecutiveBrief`` from model prose and validated reports."""
    findings = _load_specialist_findings(state)
    direct = [f for f in findings if f.classification in _DIRECT_CLASSIFICATIONS]
    contributing = [f for f in findings if f.classification not in _DIRECT_CLASSIFICATIONS]

    aircraft_id = _string(state.get("aircraft_id"), "")
    planned_date = _string(state.get("planned_milestone_date"), "")
    forecast_date = _string(state.get("forecast_milestone_date"), "")
    milestone_source_id = _string(state.get("milestone_source_id"), "")
    try:
        delay_days = int(state.get("delay_days"))
    except (TypeError, ValueError) as exc:
        raise SynthesisNormalizationError("Session delay_days is not an integer.") from exc

    if not aircraft_id or not planned_date or not forecast_date or not milestone_source_id:
        raise SynthesisNormalizationError(
            "Authoritative aircraft and milestone context is incomplete in session state."
        )

    selected_findings = [*direct, *contributing]
    allowed_ids = {
        aircraft_id,
        milestone_source_id,
        *(ref.source_id for finding in selected_findings for ref in finding.source_refs),
    }

    fallback_summary = (
        f"{aircraft_id} is forecast {delay_days} days behind milestone "
        f"{milestone_source_id}. Validated specialist reports identified "
        f"{len(direct)} direct blocker(s) and {len(contributing)} contributing factor(s) "
        "requiring coordinated program action."
    )
    summary = _safe_text(candidate.get("executive_summary"), fallback_summary, allowed_ids)
    # Reject model narrative that contradicts the authoritative milestone
    # context even when it contains no unsupported record identifier.
    allowed_dates = {planned_date, forecast_date}
    if not set(_ISO_DATE_RE.findall(summary)).issubset(allowed_dates):
        summary = fallback_summary
    if any(int(value) != abs(delay_days) for value in _DELAY_RE.findall(summary)):
        summary = fallback_summary

    assumptions = _safe_text_list(candidate.get("assumptions"), allowed_ids)
    if not assumptions:
        assumptions = [
            "The assessment is based on the synthetic records retrieved during this investigation."
        ]
    unknowns = _safe_text_list(candidate.get("unknowns"), allowed_ids)

    actions = _normalize_actions(candidate, selected_findings, planned_date, allowed_ids)
    evidence = sorted(
        {
            milestone_source_id,
            *(ref.source_id for finding in selected_findings for ref in finding.source_refs),
            *(ref.source_id for action in actions for ref in action.source_refs),
        }
    )

    return ExecutiveBrief(
        aircraft_id=aircraft_id,
        overall_status=_normalize_status(candidate.get("overall_status"), delay_days),
        planned_milestone_date=planned_date,
        forecast_milestone_date=forecast_date,
        delay_days=delay_days,
        executive_summary=summary,
        confirmed_root_causes=[finding.model_copy(deep=True) for finding in direct],
        contributing_factors=[finding.model_copy(deep=True) for finding in contributing],
        recommended_actions=actions,
        assumptions=assumptions,
        unknowns=unknowns,
        confidence=_normalize_confidence(candidate.get("confidence")),
        milestone_source_id=milestone_source_id,
        evidence=evidence,
    )


def normalize_executive_synthesis_response(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Canonicalize executive model output before it is saved to session state.

    No raw model text is logged or persisted. Session diagnostics contain only
    parse/validation categories and Pydantic error locations/types.
    """
    text = _llm_response_text(llm_response)
    candidate = _extract_json_object(text)
    diagnostics: dict[str, Any] = {
        "candidate_json_object": bool(candidate),
        "original_schema_valid": False,
        "normalization_applied": True,
    }

    if candidate:
        try:
            ExecutiveBrief.model_validate(candidate)
            diagnostics["original_schema_valid"] = True
        except ValidationError as exc:
            diagnostics["original_validation_errors"] = [
                {
                    "location": ".".join(str(part) for part in error.get("loc", ())),
                    "type": error.get("type", "validation_error"),
                }
                for error in exc.errors(include_input=False, include_url=False)[:12]
            ]

    try:
        brief = canonicalize_executive_synthesis(candidate, callback_context.state)
    except Exception as exc:
        callback_context.state["temp:synthesis_normalization"] = {
            **diagnostics,
            "status": "failed",
            "exception_type": type(exc).__name__,
        }
        logger.error(
            "synthesis_normalization_failed exception_type=%s",
            type(exc).__name__,
        )
        return None

    callback_context.state["temp:synthesis_normalization"] = {
        **diagnostics,
        "status": "canonicalized",
        "finding_count": len(brief.confirmed_root_causes) + len(brief.contributing_factors),
        "action_count": len(brief.recommended_actions),
        "evidence_count": len(brief.evidence),
    }
    replacement_content = genai_types.Content(
        role="model",
        parts=[genai_types.Part(text=brief.model_dump_json())],
    )
    return llm_response.model_copy(update={"content": replacement_content}, deep=True)
