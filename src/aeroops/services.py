"""AeroOps async investigation service.

``run_investigation_async`` is the primary entry point. It:
1. Queries the MCP server (``get_aircraft_status``, ``get_milestones``) to validate
   the aircraft and select the target milestone - NO SQLite/repository calls are made here.
2. Computes ``delay_days`` deterministically in Python.
3. Builds and runs the five-stage pipeline with milestone values pre-injected.
4. Assembles the EvidenceCatalog from preflight and specialist callback captures.
5. Validates the synthesised ``ExecutiveBrief`` against the EvidenceCatalog.
6. Closes all MCP toolsets and the ADK ``Runner`` in a ``finally`` block.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from aeroops.agent import create_pipeline
from aeroops.models import EvidenceProvenance, EvidenceRecord, ExecutiveBrief, RecordType
from aeroops.toolsets import _server_params
from aeroops.validation import (
    EvidenceCatalog,
    EvidenceIntegrityError,
    parse_mcp_response,
    validate_brief,
)

logger = logging.getLogger(__name__)

_APP_NAME = "aeroops-investigation"
_INVESTIGATION_TIMEOUT_S: int = 300


class LiveInvestigationError(RuntimeError):
    """Unexpected failure at a named live-runtime stage.

    Only the stage and exception class are retained for diagnostics.  Raw MCP
    payloads, prompts, credentials, and local paths are deliberately excluded.
    """

    def __init__(
        self,
        stage: str,
        cause_type: str,
        *,
        provider_code: int | None = None,
        provider_status: str | None = None,
        agent_name: str | None = None,
        validation_issues: tuple[str, ...] = (),
    ) -> None:
        self.stage = stage
        self.cause_type = cause_type
        self.provider_code = provider_code
        self.provider_status = provider_status
        self.agent_name = agent_name
        self.validation_issues = validation_issues
        super().__init__(f"Live investigation failed during {stage}.")


def _raise_live_stage_error(
    stage: str,
    exc: Exception,
    model_error: dict | None = None,
) -> None:
    """Log a sanitized stage marker and raise a safe runtime exception."""
    model_error = model_error or {}
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    provider_code = code if isinstance(code, int) else model_error.get("code")
    provider_status = status if isinstance(status, str) else model_error.get("status")
    agent_name = model_error.get("agent_name")
    validation_issues = tuple(
        issue for issue in model_error.get("validation_issues", []) if isinstance(issue, str)
    )
    logger.error(
        "live_investigation_failed stage=%s exception_type=%s "
        "provider_code=%s provider_status=%s agent=%s validation_issues=%s",
        stage,
        type(exc).__name__,
        provider_code,
        provider_status,
        agent_name,
        list(validation_issues),
    )
    raise LiveInvestigationError(
        stage,
        type(exc).__name__,
        provider_code=provider_code if isinstance(provider_code, int) else None,
        provider_status=provider_status if isinstance(provider_status, str) else None,
        agent_name=agent_name if isinstance(agent_name, str) else None,
        validation_issues=validation_issues,
    ) from exc


def _synthesis_error_metadata(final_state: dict[str, Any]) -> dict[str, Any]:
    """Return safe synthesis diagnostics without retaining model output."""
    metadata: dict[str, Any] = {
        "agent_name": "executive_synthesis",
        "validation_issues": [],
    }
    normalization = final_state.get("temp:synthesis_normalization")
    if not isinstance(normalization, dict):
        return metadata

    issues: list[str] = []
    for item in normalization.get("original_validation_errors", []) or []:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "root")
        issue_type = str(item.get("type") or "validation_error")
        issues.append(f"{location}:{issue_type}")
    if normalization.get("status") == "failed":
        exception_type = normalization.get("exception_type")
        if isinstance(exception_type, str):
            issues.append(f"normalizer:{exception_type}")
    metadata["validation_issues"] = issues[:12]
    return metadata


def _normalize_synthesis_evidence(payload: dict, milestone_source_id: str) -> None:
    """Derive the top-level evidence union from structured nested references.

    A live model is not trusted to sort or deduplicate this bookkeeping field.
    The evidence validator still verifies every derived identifier against the
    captured MCP EvidenceCatalog.
    """
    source_ids: set[str] = {milestone_source_id}
    for collection_name in (
        "confirmed_root_causes",
        "contributing_factors",
        "recommended_actions",
    ):
        for item in payload.get(collection_name, []) or []:
            if not isinstance(item, dict):
                continue
            for ref in item.get("source_refs", []) or []:
                if isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
                    source_ids.add(ref["source_id"])
    payload["evidence"] = sorted(source_ids)


# ---------------------------------------------------------------------------
# Restricted Stdio MCP Client for Preflight
# ---------------------------------------------------------------------------
async def _call_preflight_tool_via_mcp(
    tool_name: str,
    arguments: dict,
    db_path_override: str | None = None,
) -> dict:
    """Invoke a preflight MCP tool using a transient stdio MCP client.

    Enforces that only get_aircraft_status and get_milestones are callable.
    """
    allowed = {"get_aircraft_status", "get_milestones"}
    if tool_name not in allowed:
        from aeroops.security import SecurityReasonCode, ToolAuthorizationError

        raise ToolAuthorizationError(
            f"Preflight tool '{tool_name}' is not in the allowed list: {allowed}",
            SecurityReasonCode.TOOL_NOT_ALLOWED,
        )

    # Authoritative preflight arguments validation
    from aeroops.security import validate_tool_execution

    validate_tool_execution(tool_name, "preflight", arguments)

    params = _server_params(db_path_override)
    cmd = [params.command, *list(params.args)]
    env = {**params.env}

    import os

    proc_env = {**os.environ, **env}
    proc_env["PYTHONUNBUFFERED"] = "1"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=proc_env,
    )

    try:
        init_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "aeroops-preflight", "version": "1.0"},
                },
            }
        )
        initialized_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        call_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
        )

        input_data = init_msg + "\n" + initialized_msg + "\n" + call_msg + "\n"
        proc.stdin.write(input_data.encode())
        await proc.stdin.drain()

        tool_response = None

        async def read_lines():
            nonlocal tool_response
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                try:
                    obj = json.loads(decoded)
                    if obj.get("id") == 1:
                        tool_response = obj
                        break
                except json.JSONDecodeError:
                    continue

        from aeroops.config import get_settings

        await asyncio.wait_for(read_lines(), timeout=get_settings().mcp_timeout_seconds)

        if not tool_response:
            raise RuntimeError(f"No response received from MCP tool {tool_name}")

        if "error" in tool_response:
            raise ValueError(f"MCP tool {tool_name} returned error: {tool_response['error']}")

        result = tool_response["result"]
        # Validate returned result matches synthetic envelopes/schema
        from aeroops.security import validate_tool_result

        validate_tool_result(tool_name, result)

        return result

    finally:
        if proc.stdin:
            with contextlib.suppress(Exception):
                proc.stdin.close()
                await proc.stdin.wait_closed()
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=1.0)


async def _resolve_milestone_via_mcp(
    aircraft_id: str,
    query: str,
    db_path_override: str | None = None,
) -> dict:
    """Validate aircraft and determine target milestone via preflight stdio MCP client."""
    # 1. Validate aircraft exists
    ac_result = await _call_preflight_tool_via_mcp(
        "get_aircraft_status",
        {"aircraft_id": aircraft_id},
        db_path_override,
    )
    ac_data = ac_result.get("structuredContent", {}).get("data") or ac_result.get("data")
    if not ac_data:
        raise ValueError(f"Aircraft not found: '{aircraft_id}'")

    # 2. Get milestones
    ms_result = await _call_preflight_tool_via_mcp(
        "get_milestones",
        {"aircraft_id": aircraft_id},
        db_path_override,
    )
    milestones = ms_result.get("structuredContent", {}).get("data") or ms_result.get("data", [])
    if not milestones:
        raise ValueError(f"No milestones found for aircraft '{aircraft_id}'")

    # Deterministic target milestone selection
    ms_match = re.search(r"\bMS-\d{3}-[A-Z0-9-]+\b", query)
    requested_id = ms_match.group(0) if ms_match else None

    selected_milestone = None
    if requested_id:
        for m in milestones:
            if m.get("source_id") == requested_id:
                selected_milestone = m
                break

    if not selected_milestone:
        # Documented deterministic ordering: active milestone (status != complete) with earliest planned_date
        active_milestones = [m for m in milestones if m.get("status") != "complete"]
        if active_milestones:
            active_milestones.sort(key=lambda x: x.get("planned_date", ""))
            selected_milestone = active_milestones[0]
        else:
            milestones_sorted = sorted(milestones, key=lambda x: x.get("planned_date", ""))
            selected_milestone = milestones_sorted[-1]

    planned_str = selected_milestone.get("planned_date")
    forecast_str = selected_milestone.get("forecast_date")
    planned = date.fromisoformat(str(planned_str))
    forecast = date.fromisoformat(str(forecast_str))
    delay_days = (forecast - planned).days

    return {
        "planned_milestone_date": planned.isoformat(),
        "forecast_milestone_date": forecast.isoformat(),
        "delay_days": delay_days,
        "milestone_source_id": selected_milestone.get("source_id"),
        "aircraft_record": ac_data,
        "milestone_record": selected_milestone,
    }


# ---------------------------------------------------------------------------
# MCP toolset cleanup helper
# ---------------------------------------------------------------------------
async def _close_all_toolsets(pipeline: object) -> None:
    """Close all MCP toolsets held by specialist agents in the pipeline."""
    for stage in getattr(pipeline, "sub_agents", []):
        for agent in getattr(stage, "sub_agents", [stage]):
            for tool in getattr(agent, "tools", []):
                close_fn = getattr(tool, "close", None)
                if callable(close_fn):
                    try:
                        result = close_fn()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.warning("Error closing MCP toolset: %s", exc)


# ---------------------------------------------------------------------------
# Primary service functions
# ---------------------------------------------------------------------------
def _extract_aircraft_id(query: str) -> str | None:
    """Extract an AC-NNN identifier from a free-text query."""
    m = re.search(r"\bAC-\d{3}\b", query)
    return m.group(0) if m else None


def _clean_json(raw: str) -> str:
    """Strip markdown fences from a string."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL)


async def run_investigation_async(
    query: str,
    db_path: Path | str | None = None,
    timeout: int = _INVESTIGATION_TIMEOUT_S,
    model_override: str | None = None,
) -> ExecutiveBrief:
    """Run the full five-stage investigation pipeline and return an ExecutiveBrief."""
    from aeroops.config import get_settings

    settings = get_settings()
    if settings.offline_demo:
        raise RuntimeError("Cannot run live investigation in offline mode.")
    db_path_str: str | None = str(db_path) if db_path else None

    # 1. Preflight checks
    from aeroops.security import validate_user_query

    normalized_query = validate_user_query(query)

    aircraft_id = _extract_aircraft_id(normalized_query)
    if not aircraft_id:
        raise ValueError(
            "No AC-NNN aircraft identifier found in query. "
            "Please specify the aircraft ID in AC-NNN format."
        )

    try:
        milestone_ctx = await _resolve_milestone_via_mcp(
            aircraft_id, normalized_query, db_path_str
        )
    except (ValueError, TimeoutError):
        raise
    except Exception as exc:
        _raise_live_stage_error("mcp_preflight", exc)

    # 2. Build and run the pipeline. Live credentials are exposed only for
    # this bounded execution and are restored on success or failure.
    from aeroops.activity_collector import ActivityCollectorPlugin
    from aeroops.config import configure_live_model_credentials
    from aeroops.security_plugin import AeroOpsSecurityPlugin

    final_state: dict = {}

    with configure_live_model_credentials(settings):
        session_service = InMemorySessionService()
        run_id = str(uuid.uuid4())
        pipeline = create_pipeline(
            aircraft_id=aircraft_id,
            planned_milestone_date=milestone_ctx["planned_milestone_date"],
            forecast_milestone_date=milestone_ctx["forecast_milestone_date"],
            delay_days=milestone_ctx["delay_days"],
            milestone_source_id=milestone_ctx["milestone_source_id"],
            db_path_override=db_path_str,
            model_override=model_override,
        )
        collector = ActivityCollectorPlugin()
        runner = Runner(
            agent=pipeline,
            app_name=_APP_NAME,
            session_service=session_service,
            plugins=[
                AeroOpsSecurityPlugin(
                    max_model_calls=settings.max_model_calls,
                    max_tool_calls=settings.max_tool_calls,
                ),
                collector,
            ],
        )

        try:
            await session_service.create_session(
                app_name=_APP_NAME,
                user_id="aeroops-system",
                session_id=run_id,
                state={
                    "aircraft_id": aircraft_id,
                    "planned_milestone_date": milestone_ctx["planned_milestone_date"],
                    "forecast_milestone_date": milestone_ctx["forecast_milestone_date"],
                    "delay_days": milestone_ctx["delay_days"],
                    "milestone_source_id": milestone_ctx["milestone_source_id"],
                    "preflight_aircraft_record": milestone_ctx["aircraft_record"],
                    "preflight_milestone_record": milestone_ctx["milestone_record"],
                },
            )

            async def _run() -> None:
                user_msg = genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=normalized_query)],
                )
                async for event in runner.run_async(
                    user_id="aeroops-system",
                    session_id=run_id,
                    new_message=user_msg,
                ):
                    if event.is_final_response():
                        sess = await session_service.get_session(
                            app_name=_APP_NAME,
                            user_id="aeroops-system",
                            session_id=run_id,
                        )
                        if sess is not None:
                            final_state.update(sess.state)

            try:
                await asyncio.wait_for(_run(), timeout=timeout)
            except TimeoutError:
                raise
            except Exception as exc:
                model_error: dict | None = None
                with contextlib.suppress(Exception):
                    failed_session = await session_service.get_session(
                        app_name=_APP_NAME,
                        user_id="aeroops-system",
                        session_id=run_id,
                    )
                    if failed_session is not None:
                        candidate = failed_session.state.get("temp:last_model_error")
                        if isinstance(candidate, dict):
                            model_error = candidate

                stage = "agent_execution"
                from aeroops.report_validator import ReportValidationError

                if isinstance(exc, ReportValidationError):
                    stage = "specialist_report_validation"
                    model_error = {
                        "agent_name": "report_validator",
                        "validation_issues": list(exc.violation_codes),
                    }
                _raise_live_stage_error(stage, exc, model_error)

            # Fetch the committed state after the runner finishes even if an
            # ADK version does not label the workflow's last event as final.
            sess = await session_service.get_session(
                app_name=_APP_NAME,
                user_id="aeroops-system",
                session_id=run_id,
            )
            if sess is not None:
                final_state.update(sess.state)
        finally:
            await _close_all_toolsets(pipeline)
            with contextlib.suppress(Exception):
                await runner.close()

    # 3. Parse the callback-normalized synthesis output. Raw model text is never
    # included in logs or public exceptions.
    synthesis_metadata = _synthesis_error_metadata(final_state)
    synthesis_raw = final_state.get("synthesis_output", "")
    if not synthesis_raw:
        _raise_live_stage_error(
            "synthesis_output",
            RuntimeError("Synthesis agent produced no output."),
            synthesis_metadata,
        )

    if isinstance(synthesis_raw, dict):
        synthesis_data = dict(synthesis_raw)
    else:
        cleaned = _clean_json(str(synthesis_raw))
        try:
            loaded = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            _raise_live_stage_error("synthesis_output", exc, synthesis_metadata)
        if not isinstance(loaded, dict):
            _raise_live_stage_error(
                "synthesis_output",
                TypeError("Synthesis output must be a JSON object."),
                synthesis_metadata,
            )
        synthesis_data = loaded

    # Overwrite milestone fields with authoritative preflight values.
    synthesis_data["aircraft_id"] = aircraft_id
    synthesis_data["planned_milestone_date"] = milestone_ctx["planned_milestone_date"]
    synthesis_data["forecast_milestone_date"] = milestone_ctx["forecast_milestone_date"]
    synthesis_data["delay_days"] = milestone_ctx["delay_days"]
    synthesis_data["milestone_source_id"] = milestone_ctx["milestone_source_id"]
    _normalize_synthesis_evidence(synthesis_data, milestone_ctx["milestone_source_id"])

    try:
        brief = ExecutiveBrief.model_validate(synthesis_data)
    except Exception as exc:
        _raise_live_stage_error("synthesis_validation", exc, synthesis_metadata)

    # 4. Assemble the EvidenceCatalog
    from aeroops.report_validator import _parse_specialist_report

    catalog = EvidenceCatalog()

    # Aircraft preflight record
    ac_rec_data = milestone_ctx["aircraft_record"]
    ac_record = EvidenceRecord(
        source_id=ac_rec_data["source_id"],
        record_type=RecordType.AIRCRAFT,
        aircraft_id=aircraft_id,
        payload=ac_rec_data,
        provenance=[
            EvidenceProvenance(
                originating_agent=None,
                originating_stage="preflight",
                originating_tool="get_aircraft_status",
                invocation_id="preflight-status",
                branch_key="preflight",
                branch_sequence=1,
            )
        ],
    )
    catalog.add_record(ac_record)
    catalog.retrieved_source_ids.add(ac_record.source_id)
    catalog.approved_preflight_source_ids.add(ac_record.source_id)

    # Milestone preflight record
    ms_rec_data = milestone_ctx["milestone_record"]
    ms_record = EvidenceRecord(
        source_id=ms_rec_data["source_id"],
        record_type=RecordType.MILESTONE,
        aircraft_id=aircraft_id,
        payload=ms_rec_data,
        provenance=[
            EvidenceProvenance(
                originating_agent=None,
                originating_stage="preflight",
                originating_tool="get_milestones",
                invocation_id="preflight-milestones",
                branch_key="preflight",
                branch_sequence=2,
            )
        ],
    )
    catalog.add_record(ms_record)
    catalog.retrieved_source_ids.add(ms_record.source_id)
    catalog.approved_preflight_source_ids.add(ms_record.source_id)

    # Specialist callback captures
    mcp_evidence_keys = {
        "test_ops_mcp_evidence": "test_ops_specialist",
        "maintenance_mcp_evidence": "maintenance_specialist",
        "configuration_supply_mcp_evidence": "config_supply_specialist",
        "schedule_risk_mcp_evidence": "schedule_risk_specialist",
    }

    for state_key, agent_name in mcp_evidence_keys.items():
        evidence_list = final_state.get(state_key, [])
        if isinstance(evidence_list, str):
            try:
                evidence_list = json.loads(evidence_list)
            except Exception:
                evidence_list = []

        for entry in evidence_list:
            tool_name = entry.get("tool_name")
            entry.get("args", {})
            response = entry.get("response", {})
            sequence = entry.get("sequence", 1)
            inv_id = entry.get("invocation_id", "")
            fc_id = entry.get("function_call_id")

            parsed_records = parse_mcp_response(tool_name, response, aircraft_id)
            for sid, rt, aid, payload in parsed_records:
                prov = EvidenceProvenance(
                    originating_agent=agent_name,
                    originating_stage=state_key,
                    originating_tool=tool_name,
                    invocation_id=inv_id,
                    branch_key=state_key,
                    branch_sequence=sequence,
                    function_call_id=fc_id,
                )
                rec = EvidenceRecord(
                    source_id=sid,
                    record_type=rt,
                    aircraft_id=aid,
                    payload=payload,
                    provenance=[prov],
                )
                catalog.add_record(rec)
                catalog.retrieved_source_ids.add(sid)

    # Extract specialist_source_ids from SpecialistReports
    specialist_report_keys = {
        "test_ops_findings": "test_ops_specialist",
        "maintenance_findings": "maintenance_specialist",
        "configuration_supply_findings": "config_supply_specialist",
        "schedule_risk_findings": "schedule_risk_specialist",
    }
    for key in specialist_report_keys:
        raw_rep = final_state.get(key)
        if raw_rep:
            try:
                report = _parse_specialist_report(key, raw_rep)
                for finding in report.findings:
                    for ref in finding.source_refs:
                        catalog.specialist_source_ids.add(ref.source_id)
            except Exception:
                pass

    # 5. Run validation against EvidenceCatalog
    report = validate_brief(brief, catalog)
    if not report.passed:
        raise EvidenceIntegrityError(
            "Evidence integrity validation failed after synthesis:\n" + report.format_violations()
        )

    # 6. Run security validation on the synthesised brief
    from aeroops.security import AeroOpsResponse, validate_security_response

    validate_security_response(brief, aircraft_id)

    logger.info(
        "Investigation complete — aircraft=%s delay_days=%d status=%s confidence=%s",
        brief.aircraft_id,
        brief.delay_days,
        brief.overall_status,
        brief.confidence,
    )
    res = AeroOpsResponse(session_id=run_id, **brief.model_dump())
    res._evidence_catalog = catalog
    res._activities = collector.activities
    return res


def run_investigation(
    query: str,
    db_path: Path | str | None = None,
    timeout: int = _INVESTIGATION_TIMEOUT_S,
) -> ExecutiveBrief:
    """Synchronous wrapper around ``run_investigation_async``."""
    return asyncio.run(run_investigation_async(query=query, db_path=db_path, timeout=timeout))
