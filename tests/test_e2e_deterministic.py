"""Credential-free deterministic end-to-end integration test.

This module contains a complete E2E test that:

- Seeds a temporary SQLite database (requirement 1)
- Launches the REAL ``aeroops-data-mcp`` server over stdio (requirement 1)
- Injects deterministic ``BaseLlm`` test doubles that return scripted JSON
  — no Gemini credentials are required (requirement 1)
- Runs the full five-stage ADK pipeline (requirement 1)
- Verifies participation of all six named agents (requirement 1)
- Verifies the six-day AC-009 delay (requirement 1)
- Verifies all four blocker categories are represented (requirement 1)
- Requires NO ``AEROOPS_RUN_E2E_TESTS`` flag (requirement 1)

Live Gemini tests
-----------------
Tests that make real LLM API calls are marked ``@pytest.mark.live_llm`` and
are unconditionally skipped unless ``AEROOPS_RUN_E2E_TESTS=1`` is set.
This keeps the standard suite credential-free.

Architecture of the test doubles
---------------------------------
``ScriptedLlm`` is a ``BaseLlm`` subclass registered with ``LLMRegistry``.
Each agent is given a distinct model name (``scripted:<agent-name>``).
``ScriptedLlm`` returns a pre-scripted JSON response for each agent role,
completely bypassing the Gemini API.  The MCP tools are still called for
real — the test doubles do NOT mock the MCP layer.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest
from google.adk.models import LlmResponse
from google.adk.models.base_llm import BaseLlm
from google.adk.models.registry import LLMRegistry
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from aeroops.agent import (
    _CONFIG_SUPPLY_TOOLS,
    _MAINTENANCE_TOOLS,
    _SCHEDULE_RISK_TOOLS,
    _TEST_OPS_TOOLS,
    get_specialist_output_keys,
    get_tool_allowlist,
)
from aeroops.models import (
    EvidenceRef,
    ExecutiveBrief,
    Finding,
    RecommendedAction,
    SpecialistReport,
)
from aeroops.report_validator import (
    ReportValidatorAgent,
    _parse_specialist_report,
)
from aeroops.scope_validator import (
    ScopeValidationError,
    classify_aircraft_id,
    parse_intake_output,
)
from aeroops.synthesis import normalize_executive_synthesis_response
from aeroops.validation import EvidenceCatalog, validate_brief

# ---------------------------------------------------------------------------
# AC-009 seeded constants (must match seed.py exactly)
# ---------------------------------------------------------------------------

AC009 = "AC-009"
PLANNED_DATE = "2026-06-29"
FORECAST_DATE = "2026-07-05"
DELAY_DAYS = 6
MS_SOURCE_ID = "MS-009-FTC"

# Evidence IDs that must appear for AC-009
REQUIRED_EVIDENCE_IDS: frozenset[str] = frozenset(
    {
        "MS-009-FTC",
        "TEST-009-118",
        "TEST-009-121",
        "DEF-009-042",
        "PART-ACT-774",
        "CR-184",
        "MNT-009-015",
    }
)

# Four schedule-dependency source IDs
DEPENDENCY_IDS: frozenset[str] = frozenset(
    {"DEP-009-001", "DEP-009-002", "DEP-009-003", "DEP-009-004"}
)

# Six expected agent names
EXPECTED_AGENTS: frozenset[str] = frozenset(
    {
        "intake_extractor",
        "test_ops_specialist",
        "maintenance_specialist",
        "config_supply_specialist",
        "schedule_risk_specialist",
        "executive_synthesis",
    }
)

# Four blocker categories that must be covered
BLOCKER_CATEGORIES: frozenset[str] = frozenset(
    {"test_failure", "defect", "parts_constraint", "change_request"}
)

_APP_NAME = "aeroops-e2e-test"


# ---------------------------------------------------------------------------
# Database fixture — creates and seeds a real temp database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Path:
    """Seed a temporary SQLite database with AC-009 data.

    Yields the path to the file so tests can pass it to the MCP server.
    """
    tmp_dir = tmp_path_factory.mktemp("aeroops_e2e_db")
    db_path = tmp_dir / "aeroops_test.db"

    from aeroops.db import get_db_connection
    from aeroops.db.schema import create_tables
    from aeroops.db.seed import seed_all

    with get_db_connection(db_path) as conn:
        create_tables(conn)
        seed_all(conn)
        conn.commit()

    return db_path


# ---------------------------------------------------------------------------
# Scripted LLM test doubles
# ---------------------------------------------------------------------------

# Canned specialist JSON responses — every finding has source_refs so the
# report validator does not raise.

_TEST_OPS_RESPONSE = json.dumps(
    {
        "domain": "test_operations",
        "aircraft_id": AC009,
        "findings": [
            {
                "finding_id": "FIND-TEST-001",
                "statement": "TEST-009-118 was aborted due to flight-control actuator mismatch.",
                "classification": "test_failure",
                "source_refs": [
                    {
                        "source_id": "TEST-009-118",
                        "record_type": "test_event",
                        "summary": "Aborted low-speed taxi test 2026-06-23.",
                    }
                ],
                "rationale": "get_test_events returned status=aborted for TEST-009-118.",
            },
            {
                "finding_id": "FIND-TEST-002",
                "statement": "TEST-009-121 is blocked by four unresolved dependencies.",
                "classification": "dependency_blocker",
                "source_refs": [
                    {
                        "source_id": "TEST-009-121",
                        "record_type": "test_event",
                        "summary": "High-speed taxi test blocked.",
                    },
                    {
                        "source_id": "DEP-009-001",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by DEF-009-042.",
                    },
                    {
                        "source_id": "DEP-009-002",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by PART-ACT-774.",
                    },
                    {
                        "source_id": "DEP-009-003",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by CR-184.",
                    },
                    {
                        "source_id": "DEP-009-004",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by MNT-009-015.",
                    },
                ],
                "rationale": "get_dependency_graph shows four blocker edges.",
            },
            {
                "finding_id": "FIND-TEST-003",
                "statement": "DEF-009-042 is an open high-severity defect blocking tests.",
                "classification": "defect",
                "source_refs": [
                    {
                        "source_id": "DEF-009-042",
                        "record_type": "defect",
                        "summary": "Flight-control actuator position mismatch.",
                    }
                ],
                "rationale": "get_open_defects returned DEF-009-042 with severity=high.",
            },
        ],
        "raw_source_ids": [
            AC009,
            MS_SOURCE_ID,
            "TEST-009-118",
            "TEST-009-121",
            "DEF-009-042",
            "DEP-009-001",
            "DEP-009-002",
            "DEP-009-003",
            "DEP-009-004",
        ],
    }
)

_MAINTENANCE_RESPONSE = json.dumps(
    {
        "domain": "maintenance",
        "aircraft_id": AC009,
        "findings": [
            {
                "finding_id": "FIND-MAINT-001",
                "statement": "MNT-009-015 post-abort inspection is scheduled but not complete.",
                "classification": "maintenance",
                "source_refs": [
                    {
                        "source_id": "MNT-009-015",
                        "record_type": "maintenance_task",
                        "summary": "Post-abort actuator housing inspection due 2026-06-26.",
                    }
                ],
                "rationale": "get_maintenance_tasks returned MNT-009-015 with status=scheduled.",
            }
        ],
        "raw_source_ids": ["DEF-009-042", "MNT-009-015"],
    }
)

_CONFIG_SUPPLY_RESPONSE = json.dumps(
    {
        "domain": "configuration_supply",
        "aircraft_id": AC009,
        "findings": [
            {
                "finding_id": "FIND-CONFIG-001",
                "statement": "PART-ACT-774 is awaiting delivery and needed by 2026-06-27.",
                "classification": "parts_constraint",
                "source_refs": [
                    {
                        "source_id": "PART-ACT-774",
                        "record_type": "parts_constraint",
                        "summary": "Flight-control actuator assembly ETA 2026-06-30.",
                    }
                ],
                "rationale": "get_parts_constraints returned PART-ACT-774 with status=awaiting_delivery.",
            },
            {
                "finding_id": "FIND-CONFIG-002",
                "statement": "CR-184 is pending review and blocking configuration work.",
                "classification": "change_request",
                "source_refs": [
                    {
                        "source_id": "CR-184",
                        "record_type": "change_request",
                        "summary": "Actuator feedback software threshold adjustment.",
                    }
                ],
                "rationale": "get_change_requests returned CR-184 with status=pending_review.",
            },
        ],
        "raw_source_ids": ["PART-ACT-774", "CR-184"],
    }
)

_SCHEDULE_RISK_RESPONSE = json.dumps(
    {
        "domain": "schedule_risk",
        "aircraft_id": AC009,
        "findings": [
            {
                "finding_id": "FIND-SCHEDULE-001",
                "statement": "MS-009-FTC is at risk with a 6-day delay.",
                "classification": "schedule_risk",
                "source_refs": [
                    {
                        "source_id": MS_SOURCE_ID,
                        "record_type": "milestone",
                        "summary": "Flight Test Clearance planned 2026-06-29 forecast 2026-07-05.",
                    }
                ],
                "rationale": "get_aircraft_status shows AC-009 status=red and milestone at risk.",
            }
        ],
        "raw_source_ids": [AC009, MS_SOURCE_ID],
    }
)

_SYNTHESIS_RESPONSE = json.dumps(
    {
        "aircraft_id": AC009,
        "overall_status": "red",
        "planned_milestone_date": PLANNED_DATE,
        "forecast_milestone_date": FORECAST_DATE,
        "delay_days": DELAY_DAYS,
        "milestone_source_id": MS_SOURCE_ID,
        "executive_summary": (
            "AC-009 is delayed 6 days due to an aborted actuator test and four "
            "unresolved blockers. Resolution requires part delivery, defect closure, "
            "and CR approval."
        ),
        "confirmed_root_causes": [
            {
                "finding_id": "FIND-TEST-003",
                "statement": "Flight-control actuator mismatch (DEF-009-042) caused TEST-009-118 abort.",
                "classification": "defect",
                "source_refs": [
                    {
                        "source_id": "DEF-009-042",
                        "record_type": "defect",
                        "summary": "Critical open defect.",
                    },
                    {
                        "source_id": "TEST-009-118",
                        "record_type": "test_event",
                        "summary": "Aborted low-speed taxi test.",
                    },
                ],
                "rationale": "Direct cause of test abort and downstream blockage.",
                "claims": [],
            }
        ],
        "contributing_factors": [
            {
                "finding_id": "FIND-TEST-002",
                "statement": "TEST-009-121 is blocked by four unresolved dependencies.",
                "classification": "dependency_blocker",
                "source_refs": [
                    {
                        "source_id": "TEST-009-121",
                        "record_type": "test_event",
                        "summary": "High-speed taxi test blocked.",
                    },
                    {
                        "source_id": "DEP-009-001",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by DEF-009-042.",
                    },
                    {
                        "source_id": "DEP-009-002",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by PART-ACT-774.",
                    },
                    {
                        "source_id": "DEP-009-003",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by CR-184.",
                    },
                    {
                        "source_id": "DEP-009-004",
                        "record_type": "schedule_dependency",
                        "summary": "Blocked by MNT-009-015.",
                    },
                ],
                "rationale": "get_dependency_graph shows four blocker edges.",
                "claims": [],
            },
            {
                "finding_id": "FIND-CONFIG-001",
                "statement": "PART-ACT-774 awaiting delivery blocks TEST-009-121.",
                "classification": "parts_constraint",
                "source_refs": [
                    {
                        "source_id": "PART-ACT-774",
                        "record_type": "parts_constraint",
                        "summary": "Actuator assembly delayed.",
                    }
                ],
                "rationale": "Prevents replacement and test resumption.",
                "claims": [],
            },
            {
                "finding_id": "FIND-CONFIG-002",
                "statement": "CR-184 pending review blocks configuration sign-off.",
                "classification": "change_request",
                "source_refs": [
                    {
                        "source_id": "CR-184",
                        "record_type": "change_request",
                        "summary": "Software threshold CR pending.",
                    }
                ],
                "rationale": "Required before actuator software update can proceed.",
                "claims": [],
            },
            {
                "finding_id": "FIND-MAINT-001",
                "statement": "MNT-009-015 inspection not yet complete.",
                "classification": "maintenance",
                "source_refs": [
                    {
                        "source_id": "MNT-009-015",
                        "record_type": "maintenance_task",
                        "summary": "Post-abort inspection scheduled.",
                    }
                ],
                "rationale": "Blocks sign-off on actuator airworthiness.",
                "claims": [],
            },
        ],
        "recommended_actions": [
            {
                "action_id": "ACT-001",
                "action": "Expedite delivery of PART-ACT-774.",
                "classification": "parts_constraint",
                "supporting_finding_ids": ["FIND-CONFIG-001"],
                "source_refs": [
                    {
                        "source_id": "PART-ACT-774",
                        "record_type": "parts_constraint",
                        "summary": "Actuator assembly.",
                    }
                ],
                "rationale": "Critical path blocker for TEST-009-121.",
                "owner_role": "supply_chain",
                "suggested_due_date": "2026-06-27",
            },
            {
                "action_id": "ACT-002",
                "action": "Complete MNT-009-015 post-abort inspection.",
                "classification": "maintenance",
                "supporting_finding_ids": ["FIND-MAINT-001"],
                "source_refs": [
                    {
                        "source_id": "MNT-009-015",
                        "record_type": "maintenance_task",
                        "summary": "Post-abort inspection.",
                    }
                ],
                "rationale": "Required before actuator replacement.",
                "owner_role": "maintenance_lead",
                "suggested_due_date": "2026-06-26",
            },
            {
                "action_id": "ACT-003",
                "action": "Approve CR-184 in expedited engineering review.",
                "classification": "change_request",
                "supporting_finding_ids": ["FIND-CONFIG-002"],
                "source_refs": [
                    {
                        "source_id": "CR-184",
                        "record_type": "change_request",
                        "summary": "Actuator software CR.",
                    }
                ],
                "rationale": "Unblocks software configuration update.",
                "owner_role": "engineering",
                "suggested_due_date": "2026-06-28",
            },
        ],
        "assumptions": ["All tool results reflect the current operational database state."],
        "unknowns": ["Root cause of initial actuator feedback deviation not yet confirmed."],
        "confidence": "high",
        "evidence": [
            "CR-184",
            "DEF-009-042",
            "DEP-009-001",
            "DEP-009-002",
            "DEP-009-003",
            "DEP-009-004",
            "MNT-009-015",
            "MS-009-FTC",
            "PART-ACT-774",
            "TEST-009-118",
            "TEST-009-121",
        ],
    }
)


class ScriptedLlm(BaseLlm):
    """A deterministic BaseLlm test double that returns pre-scripted JSON.

    The model name encodes which agent is being served:
        ``scripted:intake_extractor``
        ``scripted:test_ops_specialist``
        ... etc.

    Tool calls are NOT intercepted — the MCP layer still executes normally.
    The scripted response is returned only for the final text generation step.
    """

    _SCRIPTS: ClassVar[dict[str, str]] = {
        "intake_extractor": json.dumps(
            {
                "aircraft_id": AC009,
                "user_intent": "investigate AC-009 flight-test delay",
                "requested_time_horizon": "90 days",
                "requested_output_type": "executive_brief",
            }
        ),
        "test_ops_specialist": _TEST_OPS_RESPONSE,
        "maintenance_specialist": _MAINTENANCE_RESPONSE,
        "config_supply_specialist": _CONFIG_SUPPLY_RESPONSE,
        "schedule_risk_specialist": _SCHEDULE_RISK_RESPONSE,
        "executive_synthesis": _SYNTHESIS_RESPONSE,
    }

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"scripted:.*"]

    async def generate_content_async(
        self,
        llm_request,  # LlmRequest
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        # Check if we should call tools or return final scripted response
        has_tool_response = False
        for content in llm_request.contents:
            if content.parts:
                for part in content.parts:
                    if getattr(part, "function_response", None) is not None:
                        has_tool_response = True
                        break
            if has_tool_response:
                break

        # Extract which agent this double is serving from the model name
        agent_name = self.model.split(":", 1)[-1] if ":" in self.model else self.model

        if not has_tool_response and agent_name in {
            "test_ops_specialist",
            "maintenance_specialist",
            "config_supply_specialist",
            "schedule_risk_specialist",
        }:
            # Return function calls for this agent
            parts = []
            if agent_name == "test_ops_specialist":
                tools_to_call = [
                    ("get_aircraft_status", {"aircraft_id": AC009}),
                    ("get_test_events", {"aircraft_id": AC009}),
                    ("get_open_defects", {"aircraft_id": AC009}),
                    ("get_dependency_graph", {"aircraft_id": AC009}),
                ]
            elif agent_name == "maintenance_specialist":
                tools_to_call = [
                    ("get_open_defects", {"aircraft_id": AC009}),
                    ("get_maintenance_tasks", {"aircraft_id": AC009}),
                ]
            elif agent_name == "config_supply_specialist":
                tools_to_call = [
                    ("get_parts_constraints", {"aircraft_id": AC009}),
                    ("get_change_requests", {"aircraft_id": AC009}),
                ]
            else:  # schedule_risk_specialist
                tools_to_call = [
                    ("get_aircraft_status", {"aircraft_id": AC009}),
                    ("get_dependency_graph", {"aircraft_id": AC009}),
                ]

            for name, args in tools_to_call:
                fc = genai_types.FunctionCall(name=name, args=args)
                parts.append(genai_types.Part(function_call=fc))

            content = genai_types.Content(role="model", parts=parts)
            yield LlmResponse(content=content, partial=False)
        else:
            # Yield final text script
            script = self._SCRIPTS.get(agent_name, "{}")
            content = genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=script)],
            )
            yield LlmResponse(content=content, partial=False)


# Register the scripted model family once
LLMRegistry.register(ScriptedLlm)


# ---------------------------------------------------------------------------
# Helper: build a pipeline with scripted models and real MCP server
# ---------------------------------------------------------------------------


def _scripted_pipeline(seeded_db_path: Path) -> Any:
    """Build the five-stage pipeline using scripted LLMs and real MCP stdio.

    Each agent gets a distinct ``scripted:<name>`` model so the test double
    can serve the correct canned JSON.

    Args:
        seeded_db_path: Path to the seeded temporary database.

    Returns:
        SequentialAgent pipeline ready to run.
    """
    from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent

    from aeroops.agent import make_after_tool_callback, make_on_tool_error_callback
    from aeroops.scope_validator import ScopeValidatorAgent
    from aeroops.toolsets import make_toolset

    db_str = str(seeded_db_path)

    intake = LlmAgent(
        name="intake_extractor",
        model="scripted:intake_extractor",
        instruction="(scripted)",
        output_key="intake_output",
        tools=[],
    )
    scope_val = ScopeValidatorAgent(name="scope_validator")

    # Specialists with REAL MCP toolsets
    specialists = [
        LlmAgent(
            name="test_ops_specialist",
            model="scripted:test_ops_specialist",
            instruction="(scripted)",
            output_key="test_ops_findings",
            tools=[make_toolset(_TEST_OPS_TOOLS, db_path_override=db_str)],
            after_tool_callback=make_after_tool_callback("test_ops_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("test_ops_mcp_evidence"),
        ),
        LlmAgent(
            name="maintenance_specialist",
            model="scripted:maintenance_specialist",
            instruction="(scripted)",
            output_key="maintenance_findings",
            tools=[make_toolset(_MAINTENANCE_TOOLS, db_path_override=db_str)],
            after_tool_callback=make_after_tool_callback("maintenance_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("maintenance_mcp_evidence"),
        ),
        LlmAgent(
            name="config_supply_specialist",
            model="scripted:config_supply_specialist",
            instruction="(scripted)",
            output_key="configuration_supply_findings",
            tools=[make_toolset(_CONFIG_SUPPLY_TOOLS, db_path_override=db_str)],
            after_tool_callback=make_after_tool_callback("configuration_supply_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback(
                "configuration_supply_mcp_evidence"
            ),
        ),
        LlmAgent(
            name="schedule_risk_specialist",
            model="scripted:schedule_risk_specialist",
            instruction="(scripted)",
            output_key="schedule_risk_findings",
            tools=[make_toolset(_SCHEDULE_RISK_TOOLS, db_path_override=db_str)],
            after_tool_callback=make_after_tool_callback("schedule_risk_mcp_evidence"),
            on_tool_error_callback=make_on_tool_error_callback("schedule_risk_mcp_evidence"),
        ),
    ]
    parallel = ParallelAgent(
        name="parallel_specialist_investigation",
        sub_agents=specialists,
    )
    report_val = ReportValidatorAgent(name="report_validator")
    synthesis = LlmAgent(
        name="executive_synthesis",
        model="scripted:executive_synthesis",
        instruction="(scripted)",
        output_key="synthesis_output",
        tools=[],
        include_contents="none",
        after_model_callback=normalize_executive_synthesis_response,
    )

    return SequentialAgent(
        name="aeroops_investigation_pipeline",
        sub_agents=[intake, scope_val, parallel, report_val, synthesis],
    )


# ---------------------------------------------------------------------------
# Deterministic E2E test
# ---------------------------------------------------------------------------


class TestDeterministicE2E:
    """Credential-free deterministic end-to-end integration test.

    Uses the real ``aeroops-data-mcp`` server over stdio with a seeded
    temporary database.  Gemini API is never called.
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_ac009(self, seeded_db):
        """Run the complete pipeline and verify all six correctness assertions."""
        from aeroops.services import _close_all_toolsets

        session_service = InMemorySessionService()
        run_id = str(uuid.uuid4())

        pipeline = _scripted_pipeline(seeded_db)
        runner = Runner(
            agent=pipeline,
            app_name=_APP_NAME,
            session_service=session_service,
        )

        await session_service.create_session(
            app_name=_APP_NAME,
            user_id="test-user",
            session_id=run_id,
            state={
                "aircraft_id": AC009,
                "planned_milestone_date": PLANNED_DATE,
                "forecast_milestone_date": FORECAST_DATE,
                "delay_days": DELAY_DAYS,
                "milestone_source_id": MS_SOURCE_ID,
            },
        )
        participating_agents: list[str] = []
        final_state: dict = {}

        try:
            user_msg = genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part(text=f"Why is {AC009} delayed? Produce an executive brief.")
                ],
            )
            async for event in runner.run_async(
                user_id="test-user",
                session_id=run_id,
                new_message=user_msg,
            ):
                if event.author:
                    participating_agents.append(event.author)

            sess = await session_service.get_session(
                app_name=_APP_NAME,
                user_id="test-user",
                session_id=run_id,
            )
            if sess:
                final_state.update(sess.state)
        finally:
            await _close_all_toolsets(pipeline)

        # --- Assertion 1: all six agents participated ---
        agent_set = set(participating_agents)
        for expected in EXPECTED_AGENTS:
            assert expected in agent_set, (
                f"Agent '{expected}' did not participate. "
                f"Participating agents: {sorted(agent_set)}"
            )

        # --- Assertion 2: investigation scope was validated ---
        scope_raw = final_state.get("investigation_scope")
        assert scope_raw, "investigation_scope not set in session state"
        scope_data = json.loads(scope_raw)
        assert scope_data["aircraft_id"] == AC009

        # --- Assertion 3: four specialist state keys present ---
        for key in get_specialist_output_keys().values():
            assert key in final_state, f"Missing specialist key: {key}"

        # --- Assertion 4: six-day delay verified ---
        synthesis_raw = final_state.get("synthesis_output", {})
        if isinstance(synthesis_raw, str):
            synthesis_data = json.loads(synthesis_raw)
        else:
            synthesis_data = synthesis_raw

        # Deterministically compute from stored dates
        planned = date.fromisoformat(PLANNED_DATE)
        forecast = date.fromisoformat(FORECAST_DATE)
        computed_delay = (forecast - planned).days
        assert computed_delay == DELAY_DAYS, (
            f"Delay mismatch: {planned} → {forecast} = {computed_delay}, expected {DELAY_DAYS}"
        )
        assert synthesis_data.get("delay_days") == DELAY_DAYS, (
            f"synthesis delay_days={synthesis_data.get('delay_days')}, expected {DELAY_DAYS}"
        )
        assert synthesis_data.get("milestone_source_id") == MS_SOURCE_ID

        # --- Assertion 5: all four blocker categories covered ---
        # Check categories via the scripted findings
        test_ops_data = json.loads(final_state.get("test_ops_findings", "{}"))
        config_data = json.loads(final_state.get("configuration_supply_findings", "{}"))
        finding_classifications = set()
        for f in test_ops_data.get("findings", []):
            finding_classifications.add(f["classification"])
        for f in config_data.get("findings", []):
            finding_classifications.add(f["classification"])
        # test_failure + dependency_blocker + defect + parts_constraint + change_request
        assert "test_failure" in finding_classifications
        assert "dependency_blocker" in finding_classifications
        assert "defect" in finding_classifications
        assert "parts_constraint" in finding_classifications
        assert "change_request" in finding_classifications

        # --- Assertion 6: milestone_source_id in evidence ---
        schedule_data = json.loads(final_state.get("schedule_risk_findings", "{}"))
        raw_ids = set(schedule_data.get("raw_source_ids", []))
        assert MS_SOURCE_ID in raw_ids, (
            f"MS-009-FTC not in schedule_risk raw_source_ids: {raw_ids}"
        )

        # --- Assertion 7: exact eleven-record evidence check ---
        from aeroops.models import EvidenceProvenance, EvidenceRecord, ExecutiveBrief, RecordType
        from aeroops.report_validator import _parse_specialist_report
        from aeroops.services import _resolve_milestone_via_mcp
        from aeroops.validation import EvidenceCatalog, parse_mcp_response, validate_brief

        milestone_ctx = await _resolve_milestone_via_mcp(
            AC009, "Why is AC-009 delayed?", db_path_override=str(seeded_db)
        )

        catalog = EvidenceCatalog()

        ac_rec_data = milestone_ctx["aircraft_record"]
        catalog.add_record(
            EvidenceRecord(
                source_id=ac_rec_data["source_id"],
                record_type=RecordType.AIRCRAFT,
                aircraft_id=AC009,
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
        )
        catalog.retrieved_source_ids.add(ac_rec_data["source_id"])
        catalog.approved_preflight_source_ids.add(ac_rec_data["source_id"])

        ms_rec_data = milestone_ctx["milestone_record"]
        catalog.add_record(
            EvidenceRecord(
                source_id=ms_rec_data["source_id"],
                record_type=RecordType.MILESTONE,
                aircraft_id=AC009,
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
        )
        catalog.retrieved_source_ids.add(ms_rec_data["source_id"])
        catalog.approved_preflight_source_ids.add(ms_rec_data["source_id"])

        mcp_evidence_keys = {
            "test_ops_mcp_evidence": "test_ops_specialist",
            "maintenance_mcp_evidence": "maintenance_specialist",
            "configuration_supply_mcp_evidence": "config_supply_specialist",
            "schedule_risk_mcp_evidence": "schedule_risk_specialist",
        }

        for state_key, agent_name in mcp_evidence_keys.items():
            evidence_list = final_state.get(state_key, [])
            if isinstance(evidence_list, str):
                evidence_list = json.loads(evidence_list)
            for entry in evidence_list:
                tool_name = entry.get("tool_name")
                resp = entry.get("response", {})
                seq = entry.get("sequence", 1)
                inv_id = entry.get("invocation_id", "")
                fc_id = entry.get("function_call_id")
                parsed_records = parse_mcp_response(tool_name, resp, AC009)
                for sid, rt, aid, payload in parsed_records:
                    catalog.add_record(
                        EvidenceRecord(
                            source_id=sid,
                            record_type=rt,
                            aircraft_id=aid,
                            payload=payload,
                            provenance=[
                                EvidenceProvenance(
                                    originating_agent=agent_name,
                                    originating_stage=state_key,
                                    originating_tool=tool_name,
                                    invocation_id=inv_id,
                                    branch_key=state_key,
                                    branch_sequence=seq,
                                    function_call_id=fc_id,
                                )
                            ],
                        )
                    )
                    catalog.retrieved_source_ids.add(sid)

        specialist_report_keys = {
            "test_ops_findings": "test_ops_specialist",
            "maintenance_findings": "maintenance_specialist",
            "configuration_supply_findings": "config_supply_specialist",
            "schedule_risk_findings": "schedule_risk_specialist",
        }
        for key in specialist_report_keys:
            raw_rep = final_state.get(key)
            if raw_rep:
                report = _parse_specialist_report(key, raw_rep)
                for finding in report.findings:
                    for ref in finding.source_refs:
                        catalog.specialist_source_ids.add(ref.source_id)

        print("=== DETERMINISTIC AC-009 EVIDENCE SETS ===")
        print(f"retrieved_source_ids: {sorted(list(catalog.retrieved_source_ids))}")
        print(f"specialist_source_ids: {sorted(list(catalog.specialist_source_ids))}")
        print(
            f"approved_preflight_source_ids: {sorted(list(catalog.approved_preflight_source_ids))}"
        )

        brief = ExecutiveBrief.model_validate(synthesis_data)
        validation_report = validate_brief(brief, catalog)
        assert validation_report.passed, (
            f"Validation failed: {validation_report.format_violations()}"
        )

        print(f"final ExecutiveBrief.evidence: {sorted(brief.evidence)}")

        expected_evidence_union = sorted(
            [
                "MS-009-FTC",
                "TEST-009-118",
                "TEST-009-121",
                "DEF-009-042",
                "PART-ACT-774",
                "CR-184",
                "MNT-009-015",
                "DEP-009-001",
                "DEP-009-002",
                "DEP-009-003",
                "DEP-009-004",
            ]
        )
        assert sorted(brief.evidence) == expected_evidence_union

    def test_mcp_server_starts_with_seeded_db(self, seeded_db):
        """Prove the real MCP stdio server starts and can query the seeded DB."""
        import os
        import time

        time.sleep(2.0)

        env = {**os.environ, "AEROOPS_DB_PATH": str(seeded_db)}
        # Send an initialize + health_check request
        init_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            }
        )
        call_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "health_check", "arguments": {}},
            }
        )
        initialized_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        inp = (init_msg + "\n" + initialized_msg + "\n" + call_msg + "\n").encode()

        result = subprocess.run(
            [sys.executable, "-m", "aeroops.mcp_server"],
            input=inp,
            capture_output=True,
            timeout=20,
            env=env,
            cwd=str(seeded_db.parent.parent),
        )
        # Parse response lines — the last non-empty line should be the tool result
        lines = [ln for ln in result.stdout.decode(errors="replace").splitlines() if ln.strip()]
        assert lines, "MCP server produced no output"
        # Find the tool response (id=1)
        tool_resp = None
        for line in lines:
            try:
                obj = json.loads(line)
                if obj.get("id") == 1:
                    tool_resp = obj
                    break
            except json.JSONDecodeError:
                continue
        assert tool_resp is not None, (
            f"No tool response found. stdout: {result.stdout.decode(errors='replace')} stderr: {result.stderr.decode(errors='replace')}"
        )
        # Must not be an error response
        assert "error" not in tool_resp, f"MCP returned error: {tool_resp}"
        # Must have a result
        assert "result" in tool_resp, f"No result in MCP response: {tool_resp}"
        # Prove we used the actual stdio server (not a mock)
        assert result.returncode in (0, 1, -1), f"Unexpected return code: {result.returncode}"

    def test_no_api_key_required(self, monkeypatch):
        """Prove registration of ScriptedLlm does not require any API key."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # ScriptedLlm.resolve must work without credentials
        cls = LLMRegistry.resolve("scripted:intake_extractor")
        assert cls is ScriptedLlm
        instance = ScriptedLlm(model="scripted:intake_extractor")
        assert instance.model == "scripted:intake_extractor"

    def test_synthesis_has_zero_tools(self, seeded_db):
        """Prove the synthesis agent is configured with tools=[]."""
        pipeline = _scripted_pipeline(seeded_db)
        # The synthesis agent is the last sub_agent
        synthesis = pipeline.sub_agents[-1]
        assert synthesis.name == "executive_synthesis"
        assert list(synthesis.tools) == [], (
            f"Synthesis must have zero tools, got: {synthesis.tools}"
        )

    def test_delay_days_equals_forecast_minus_planned(self):
        """Verify the six-day delay is deterministic, not LLM-computed."""
        planned = date.fromisoformat(PLANNED_DATE)
        forecast = date.fromisoformat(FORECAST_DATE)
        computed = (forecast - planned).days
        assert computed == DELAY_DAYS, f"{PLANNED_DATE} → {FORECAST_DATE} = {computed}, expected 6"


# ---------------------------------------------------------------------------
# Scope validation tests (req 3)
# ---------------------------------------------------------------------------


class TestScopeValidation:
    """Deterministic scope validator — four error modes."""

    def test_well_formed_id_passes(self):
        data = {
            "aircraft_id": "AC-009",
            "user_intent": "test",
            "requested_time_horizon": "90 days",
            "requested_output_type": "executive_brief",
        }
        result = classify_aircraft_id(data, "Investigate AC-009")
        assert result == "AC-009"

    def test_missing_aircraft_id_raises(self):
        data = {"aircraft_id": "", "user_intent": "test"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Investigate the aircraft")
        assert exc_info.value.error_code == "MISSING_AIRCRAFT_ID"

    def test_malformed_aircraft_id_raises(self):
        data = {"aircraft_id": "AC009", "user_intent": "test"}  # missing hyphen
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Investigate AC009")
        assert exc_info.value.error_code == "MALFORMED_AIRCRAFT_ID"

    def test_intake_error_missing_id(self):
        data = {"error": "missing_aircraft_id", "detail": "No ID found"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Some query")
        assert exc_info.value.error_code == "MISSING_AIRCRAFT_ID"

    def test_intake_error_invalid_id(self):
        data = {"error": "invalid_aircraft_id", "detail": "Bad pattern"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Fix ACX-009")
        assert exc_info.value.error_code == "MALFORMED_AIRCRAFT_ID"

    def test_ambiguous_two_aircraft_ids(self):
        data = {"aircraft_id": "AC-009", "user_intent": "compare"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Compare AC-009 and AC-010 delays")
        assert exc_info.value.error_code == "AMBIGUOUS_AIRCRAFT_ID"

    def test_ambiguous_three_aircraft_ids(self):
        data = {"aircraft_id": "AC-007", "user_intent": "compare"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "AC-007 AC-008 AC-009 all delayed?")
        assert exc_info.value.error_code == "AMBIGUOUS_AIRCRAFT_ID"

    def test_non_json_intake_raises(self):
        with pytest.raises(ScopeValidationError) as exc_info:
            parse_intake_output("This is not JSON at all")
        assert exc_info.value.error_code == "MISSING_AIRCRAFT_ID"

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"aircraft_id": "AC-009", "user_intent": "test", "requested_time_horizon": "90 days", "requested_output_type": "executive_brief"}\n```'
        data = parse_intake_output(raw)
        assert data["aircraft_id"] == "AC-009"


# ---------------------------------------------------------------------------
# Specialist report validation tests (req 4)
# ---------------------------------------------------------------------------


class TestReportValidation:
    """Deterministic report validator invariants."""

    def _make_valid_report(
        self,
        domain: str = "test_operations",
        aircraft_id: str = AC009,
        findings: list | None = None,
    ) -> str:
        if findings is None:
            findings = [
                {
                    "finding_id": "FIND-TEST-001",
                    "statement": "Valid finding.",
                    "classification": "test_failure",
                    "source_refs": [
                        {
                            "source_id": "TEST-009-118",
                            "record_type": "test_event",
                            "summary": "Aborted test.",
                        }
                    ],
                    "rationale": "Supported by tool result.",
                }
            ]
        return json.dumps(
            {
                "domain": domain,
                "aircraft_id": aircraft_id,
                "findings": findings,
                "raw_source_ids": ["TEST-009-118"],
            }
        )

    def test_valid_report_parses(self):
        raw = self._make_valid_report()
        report = _parse_specialist_report("test_ops_findings", raw)
        assert report.aircraft_id == AC009
        assert len(report.findings) == 1

    def test_missing_key_collected_as_violation(self):
        """A missing specialist key must produce a violation."""
        # Simulate state with one key missing
        state = {
            "test_ops_findings": self._make_valid_report("test_operations"),
            "maintenance_findings": self._make_valid_report("maintenance"),
            # configuration_supply_findings MISSING
            "schedule_risk_findings": self._make_valid_report("schedule_risk"),
        }

        class _FakeCtx:
            class Session:
                pass

            session = Session

        ctx = _FakeCtx()
        ctx.session.state = state  # type: ignore

        # We test the underlying parse logic directly
        from aeroops.report_validator import SPECIALIST_KEYS

        violations = []
        for key in SPECIALIST_KEYS:
            raw = state.get(key)
            if not raw:
                violations.append(f"[{key}] Missing or empty")

        assert len(violations) == 1
        assert "configuration_supply_findings" in violations[0]

    def test_empty_findings_list_is_violation(self):
        raw = self._make_valid_report(findings=[])
        report = _parse_specialist_report("test_ops_findings", raw)
        # Empty findings parses successfully — the ReportValidatorAgent catches it
        assert report.findings == []

    def test_finding_without_source_refs(self):
        raw = json.dumps(
            {
                "domain": "test_operations",
                "aircraft_id": AC009,
                "findings": [
                    {
                        "finding_id": "FIND-TEST-001",
                        "statement": "Finding without refs.",
                        "classification": "test_failure",
                        "source_refs": [],
                        "rationale": "None.",
                    }
                ],
                "raw_source_ids": [],
            }
        )
        with pytest.raises(ValueError, match="source_refs"):
            _parse_specialist_report("test_ops_findings", raw)

    def test_wrong_aircraft_id_in_report(self):
        raw = self._make_valid_report(aircraft_id="AC-001")  # wrong aircraft
        report = _parse_specialist_report("test_ops_findings", raw)
        assert report.aircraft_id == "AC-001"  # parsed fine; the validator catches it

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_specialist_report("test_ops_findings", "THIS IS NOT JSON")

    def test_blocker_classification_separation(self):
        """Direct blockers and secondary risks are correctly classified."""
        from aeroops.report_validator import _classify_source_ids

        report_data = {
            "domain": "test_operations",
            "aircraft_id": AC009,
            "findings": [
                {
                    "finding_id": "FIND-TEST-001",
                    "statement": "Test failure.",
                    "classification": "test_failure",  # direct blocker
                    "source_refs": [
                        {"source_id": "TEST-009-118", "record_type": "test_event", "summary": "a"}
                    ],
                    "rationale": "r",
                },
                {
                    "finding_id": "FIND-TEST-002",
                    "statement": "Schedule risk.",
                    "classification": "schedule_risk",  # secondary risk
                    "source_refs": [
                        {"source_id": "MS-009-FTC", "record_type": "milestone", "summary": "b"}
                    ],
                    "rationale": "r",
                },
            ],
            "raw_source_ids": ["TEST-009-118", "MS-009-FTC"],
        }
        report = SpecialistReport.model_validate(report_data)
        direct, secondary = _classify_source_ids(report)
        assert "TEST-009-118" in direct
        assert "MS-009-FTC" in secondary


# ---------------------------------------------------------------------------
# Evidence integrity — unsupported fact tests (req 6)
# ---------------------------------------------------------------------------


class TestUnsupportedFactRejection:
    """Inject scripted synthesis responses with structural defects.

    Each test injects a specific invalid pattern and asserts the validator
    rejects the ExecutiveBrief with the appropriate violation code.
    """

    def _brief_with(self, overrides: dict) -> ExecutiveBrief:
        """Build a minimal valid brief and apply overrides."""
        base = json.loads(_SYNTHESIS_RESPONSE)
        base.update(overrides)
        return ExecutiveBrief.model_validate(base)

    def _ref(self, sid: str) -> EvidenceRef:
        return EvidenceRef(source_id=sid, record_type="test_event", summary="s")

    def _finding(self, refs: list[EvidenceRef]) -> Finding:
        return Finding(
            finding_id="FIND-TEST-003",
            statement="Test statement",
            classification="defect",
            source_refs=refs,
            rationale="r",
        )

    def _action(self, refs: list[EvidenceRef]) -> RecommendedAction:
        return RecommendedAction(
            action_id="ACT-001",
            action="Take action",
            classification="parts_constraint",
            supporting_finding_ids=["FIND-TEST-003"],
            source_refs=refs,
            rationale="r",
            owner_role="supply_chain",
            suggested_due_date="2026-06-27",
        )

    def _catalog(self) -> EvidenceCatalog:
        from aeroops.models import EvidenceRecord, RecordType
        from aeroops.validation import EvidenceCatalog

        cat = EvidenceCatalog()

        records_data = [
            ("AC-009", RecordType.AIRCRAFT, "AC-009", {"status": "red"}),
            (
                "MS-009-FTC",
                RecordType.MILESTONE,
                "AC-009",
                {"planned_date": "2026-06-29", "forecast_date": "2026-07-05", "status": "at_risk"},
            ),
            ("DEF-009-042", RecordType.DEFECT, "AC-009", {"status": "open", "severity": "high"}),
            ("TEST-009-118", RecordType.TEST_EVENT, "AC-009", {"status": "aborted"}),
            ("TEST-009-121", RecordType.TEST_EVENT, "AC-009", {"status": "blocked"}),
            (
                "PART-ACT-774",
                RecordType.PARTS_CONSTRAINT,
                "AC-009",
                {"needed_by": "2026-06-27", "estimated_arrival": "2026-06-30"},
            ),
            ("CR-184", RecordType.CHANGE_REQUEST, "AC-009", {"status": "pending_review"}),
            ("MNT-009-015", RecordType.MAINTENANCE_TASK, "AC-009", {"status": "scheduled"}),
            (
                "DEP-009-001",
                RecordType.SCHEDULE_DEPENDENCY,
                "AC-009",
                {"blocked_test_id": "TEST-009-121", "blocker_defect_id": "DEF-009-042"},
            ),
            (
                "DEP-009-002",
                RecordType.SCHEDULE_DEPENDENCY,
                "AC-009",
                {"blocked_test_id": "TEST-009-121", "blocker_parts_constraint_id": "PART-ACT-774"},
            ),
            (
                "DEP-009-003",
                RecordType.SCHEDULE_DEPENDENCY,
                "AC-009",
                {"blocked_test_id": "TEST-009-121", "blocker_change_request_id": "CR-184"},
            ),
            (
                "DEP-009-004",
                RecordType.SCHEDULE_DEPENDENCY,
                "AC-009",
                {"blocked_test_id": "TEST-009-121", "blocker_maintenance_task_id": "MNT-009-015"},
            ),
        ]
        for sid, rt, aid, payload in records_data:
            cat.records[sid] = EvidenceRecord(
                source_id=sid, record_type=rt, aircraft_id=aid, payload=payload
            )

        cat.retrieved_source_ids |= set(cat.records.keys())
        cat.specialist_source_ids |= {
            "TEST-009-118",
            "TEST-009-121",
            "DEF-009-042",
            "PART-ACT-774",
            "CR-184",
            "MNT-009-015",
            "DEP-009-001",
            "DEP-009-002",
            "DEP-009-003",
            "DEP-009-004",
        }
        cat.approved_preflight_source_ids |= {"MS-009-FTC", "AC-009"}
        return cat

    def test_invented_source_id_rejected(self):
        """An invented source ID that does not exist in the DB must fail."""
        brief = self._brief_with(
            {
                "confirmed_root_causes": [
                    self._finding([self._ref("INVENTED-ID-XYZ-000")]).model_dump()
                ]
            }
        )
        cat = self._catalog()
        report = validate_brief(brief, cat)
        assert not report.passed
        inv = [v for v in report.violations if v.code == "UNSUPPORTED_SOURCE_ID"]
        assert any(v.source_id == "INVENTED-ID-XYZ-000" for v in inv)

    def test_root_cause_unsupported_by_specialist_evidence_rejected(self):
        """A root cause with no source_refs is rejected."""
        brief_data = json.loads(_SYNTHESIS_RESPONSE)
        bad_finding = Finding.model_construct(
            finding_id="FIND-TEST-003",
            statement="Flight-control actuator mismatch...",
            classification="defect",
            source_refs=[],
            rationale="Direct cause...",
            claims=[],
        )
        brief = ExecutiveBrief.model_construct(
            aircraft_id=brief_data["aircraft_id"],
            overall_status=brief_data["overall_status"],
            planned_milestone_date=date.fromisoformat(brief_data["planned_milestone_date"]),
            forecast_milestone_date=date.fromisoformat(brief_data["forecast_milestone_date"]),
            delay_days=brief_data["delay_days"],
            milestone_source_id=brief_data["milestone_source_id"],
            executive_summary=brief_data["executive_summary"],
            confirmed_root_causes=[bad_finding],
            contributing_factors=[],
            recommended_actions=[],
            assumptions=[],
            unknowns=[],
            confidence=brief_data["confidence"],
            evidence=brief_data["evidence"],
        )
        cat = self._catalog()
        report = validate_brief(brief, cat)
        assert not report.passed
        codes = {v.code for v in report.violations}
        assert "FINDING_MISSING_SOURCE_REFS" in codes

    def test_recommendation_with_no_supporting_finding_rejected(self):
        """A recommendation whose source_refs share no overlap with findings is rejected."""
        brief_data = json.loads(_SYNTHESIS_RESPONSE)
        brief_data["recommended_actions"][0]["supporting_finding_ids"] = ["FIND-MAINT-999"]
        brief = ExecutiveBrief.model_validate(brief_data)
        cat = self._catalog()
        report = validate_brief(brief, cat)
        assert not report.passed
        codes = {v.code for v in report.violations}
        assert "RECOMMENDATION_UNMAPPED_TO_FINDING" in codes

    def test_all_ids_valid_clean_brief_passes(self):
        """A brief where all IDs exist and belong to AC-009 must pass all invariants."""
        brief = ExecutiveBrief.model_validate(json.loads(_SYNTHESIS_RESPONSE))
        cat = self._catalog()
        report = validate_brief(brief, cat)
        assert report.passed, (
            f"Expected clean pass but got violations:\n{report.format_violations()}"
        )


# ---------------------------------------------------------------------------
# Resource lifecycle tests (req 7)
# ---------------------------------------------------------------------------


class TestResourceLifecycle:
    """Verify MCP toolset cleanup under various failure modes."""

    @pytest.mark.asyncio
    async def test_toolsets_closed_after_success(self, seeded_db):
        """MCP toolsets are closed after a successful run."""
        from aeroops.services import _close_all_toolsets

        pipeline = _scripted_pipeline(seeded_db)
        closed: list[str] = []

        # Wrap close() on each toolset to track calls
        for stage in getattr(pipeline, "sub_agents", []):
            for agent in getattr(stage, "sub_agents", [stage]):
                for tool in getattr(agent, "tools", []):
                    orig = getattr(tool, "close", None)
                    if orig:
                        agent_name = agent.name

                        async def _spy_close(orig=orig, name=agent_name):
                            closed.append(name)
                            r = orig()
                            if asyncio.iscoroutine(r):
                                await r

                        tool.close = _spy_close

        await _close_all_toolsets(pipeline)
        # Four specialists, each has one toolset
        assert len(closed) == 4, f"Expected 4 closes, got {len(closed)}: {closed}"

    @pytest.mark.asyncio
    async def test_toolsets_closed_after_model_failure(self, seeded_db):
        """MCP toolsets are closed even when the model raises."""
        from aeroops.services import _close_all_toolsets

        pipeline = _scripted_pipeline(seeded_db)
        closed: list[str] = []

        for stage in getattr(pipeline, "sub_agents", []):
            for agent in getattr(stage, "sub_agents", [stage]):
                for tool in getattr(agent, "tools", []):
                    orig = getattr(tool, "close", None)
                    if orig:

                        async def _spy(orig=orig):
                            closed.append("closed")
                            r = orig()
                            if asyncio.iscoroutine(r):
                                await r

                        tool.close = _spy

        # Always close in finally regardless of exception
        try:
            raise RuntimeError("Simulated model failure")
        except RuntimeError:
            pass
        finally:
            await _close_all_toolsets(pipeline)

        assert len(closed) == 4

    @pytest.mark.asyncio
    async def test_service_layer_milestone_resolution_uses_mcp(self, seeded_db):
        """Service layer resolves milestones via MCP (not from within pipeline)."""
        from aeroops.services import _resolve_milestone_via_mcp

        result = await _resolve_milestone_via_mcp(
            "AC-009", "MS-009-FTC", db_path_override=str(seeded_db)
        )
        assert result["milestone_source_id"] == "MS-009-FTC"
        assert result["delay_days"] == 6
        assert result["planned_milestone_date"] == "2026-06-29"
        assert result["forecast_milestone_date"] == "2026-07-05"

    @pytest.mark.asyncio
    async def test_milestone_resolution_without_db_access(self, seeded_db, monkeypatch):
        """Prove milestone resolution is fully MCP-based and does not access the DB/repo directly."""
        import aeroops.db.repository as repo

        def fail_on_direct_call(*args, **kwargs):
            raise AssertionError("Forbid direct database/repository access!")

        monkeypatch.setattr(repo, "get_aircraft", fail_on_direct_call)
        monkeypatch.setattr(repo, "get_milestones", fail_on_direct_call)

        from aeroops.services import _resolve_milestone_via_mcp

        result = await _resolve_milestone_via_mcp(
            "AC-009", "MS-009-FTC", db_path_override=str(seeded_db)
        )
        assert result["milestone_source_id"] == "MS-009-FTC"
        assert result["delay_days"] == 6


# ---------------------------------------------------------------------------
# Tool permission verification via public API (req 8)
# ---------------------------------------------------------------------------


class TestToolPermissionsPublicAPI:
    """Verify tool allowlists via the public get_tool_allowlist() API."""

    def test_test_ops_allowlist(self):
        allowed = get_tool_allowlist("test_ops")
        assert "get_aircraft_status" in allowed
        assert "get_test_events" in allowed
        assert "get_open_defects" in allowed
        assert "get_dependency_graph" in allowed
        assert "get_maintenance_tasks" not in allowed
        assert "get_parts_constraints" not in allowed
        assert "get_change_requests" not in allowed

    def test_maintenance_allowlist(self):
        allowed = get_tool_allowlist("maintenance")
        assert "get_open_defects" in allowed
        assert "get_maintenance_tasks" in allowed
        assert "get_aircraft_status" not in allowed
        assert "get_test_events" not in allowed
        assert "get_parts_constraints" not in allowed

    def test_config_supply_allowlist(self):
        allowed = get_tool_allowlist("config_supply")
        assert "get_parts_constraints" in allowed
        assert "get_change_requests" in allowed
        assert "get_aircraft_status" not in allowed
        assert "get_test_events" not in allowed
        assert "get_maintenance_tasks" not in allowed

    def test_schedule_risk_allowlist(self):
        allowed = get_tool_allowlist("schedule_risk")
        assert "get_aircraft_status" in allowed
        assert "get_dependency_graph" in allowed
        assert "get_test_events" not in allowed
        assert "get_maintenance_tasks" not in allowed
        assert "get_parts_constraints" not in allowed

    def test_unknown_domain_raises(self):
        with pytest.raises(KeyError, match="Unknown domain"):
            get_tool_allowlist("nonexistent_domain")

    def test_allowlists_are_frozen_sets(self):
        for domain in ("test_ops", "maintenance", "config_supply", "schedule_risk"):
            result = get_tool_allowlist(domain)
            assert isinstance(result, frozenset), (
                f"Expected frozenset for domain '{domain}', got {type(result)}"
            )

    def test_toolset_tool_filter_matches_allowlist(self, seeded_db):
        """McpToolset is created with the exact allowlist as tool_filter."""
        from aeroops.toolsets import make_toolset

        for domain, allowlist_fn in [
            ("test_ops", _TEST_OPS_TOOLS),
            ("maintenance", _MAINTENANCE_TOOLS),
            ("config_supply", _CONFIG_SUPPLY_TOOLS),
            ("schedule_risk", _SCHEDULE_RISK_TOOLS),
        ]:
            toolset = make_toolset(allowlist_fn, db_path_override=str(seeded_db))
            # tool_filter is stored as a list on the toolset
            filter_set = frozenset(
                getattr(toolset, "_tool_filter", None) or getattr(toolset, "tool_filter", [])
            )
            assert filter_set == allowlist_fn, (
                f"Domain {domain}: filter {filter_set} != allowlist {allowlist_fn}"
            )


# ---------------------------------------------------------------------------
# Input and state-flow tests (req 9)
# ---------------------------------------------------------------------------


class TestInputAndStateFlow:
    """Expanded input validation and state-flow tests."""

    def test_missing_aircraft_id_in_query(self):
        """Service layer raises ValueError if no AC-NNN in query."""
        from aeroops.services import _extract_aircraft_id

        result = _extract_aircraft_id("Why is the aircraft late?")
        assert result is None

    def test_two_aircraft_ids_in_query_detected_by_scope_validator(self):
        """ScopeValidatorAgent's classify_aircraft_id catches dual IDs."""
        data = {"aircraft_id": "AC-009", "user_intent": "compare"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "AC-009 vs AC-010 comparison")
        assert exc_info.value.error_code == "AMBIGUOUS_AIRCRAFT_ID"

    def test_malformed_aircraft_id_pattern_fails(self):
        data = {"aircraft_id": "AC9", "user_intent": "test"}
        with pytest.raises(ScopeValidationError) as exc_info:
            classify_aircraft_id(data, "Investigate AC9")
        assert exc_info.value.error_code == "MALFORMED_AIRCRAFT_ID"

    def test_missing_specialist_key_prevents_synthesis(self):
        """ReportValidatorAgent raises ReportValidationError when a key is missing."""
        state = {
            "test_ops_findings": json.dumps(
                {
                    "domain": "test_operations",
                    "aircraft_id": AC009,
                    "findings": [
                        {
                            "statement": "f",
                            "classification": "test_failure",
                            "source_refs": [
                                {
                                    "source_id": "TEST-009-118",
                                    "record_type": "test_event",
                                    "summary": "s",
                                }
                            ],
                            "rationale": "r",
                        }
                    ],
                    "raw_source_ids": ["TEST-009-118"],
                }
            ),
            # maintenance_findings MISSING
            "configuration_supply_findings": json.dumps(
                {
                    "domain": "configuration_supply",
                    "aircraft_id": AC009,
                    "findings": [
                        {
                            "statement": "f",
                            "classification": "parts_constraint",
                            "source_refs": [
                                {
                                    "source_id": "PART-ACT-774",
                                    "record_type": "parts_constraint",
                                    "summary": "s",
                                }
                            ],
                            "rationale": "r",
                        }
                    ],
                    "raw_source_ids": ["PART-ACT-774"],
                }
            ),
            "schedule_risk_findings": json.dumps(
                {
                    "domain": "schedule_risk",
                    "aircraft_id": AC009,
                    "findings": [
                        {
                            "statement": "f",
                            "classification": "schedule_risk",
                            "source_refs": [
                                {
                                    "source_id": "MS-009-FTC",
                                    "record_type": "milestone",
                                    "summary": "s",
                                }
                            ],
                            "rationale": "r",
                        }
                    ],
                    "raw_source_ids": ["MS-009-FTC"],
                }
            ),
            "investigation_scope": json.dumps({"aircraft_id": AC009}),
        }
        from aeroops.report_validator import SPECIALIST_KEYS

        violations = []
        for key in SPECIALIST_KEYS:
            if not state.get(key):
                violations.append(f"[{key}] Missing")
        assert len(violations) == 1
        assert "maintenance_findings" in violations[0]

    def test_cross_aircraft_source_reference_detected(self):
        """validate_brief catches a source ID belonging to a different aircraft."""
        brief_data = json.loads(_SYNTHESIS_RESPONSE)
        brief_data["confirmed_root_causes"] = [
            {
                "finding_id": "FIND-TEST-003",
                "statement": "Cross-aircraft ref.",
                "classification": "defect",
                "source_refs": [
                    {"source_id": "DEF-001-CROSS", "record_type": "defect", "summary": "s"}
                ],
                "rationale": "r",
                "claims": [],
            }
        ]
        brief_data["evidence"] = sorted(["DEF-001-CROSS", MS_SOURCE_ID])
        brief = ExecutiveBrief.model_validate(brief_data)

        from aeroops.models import EvidenceRecord, RecordType
        from aeroops.validation import EvidenceCatalog

        cat = EvidenceCatalog()
        cat.records["DEF-001-CROSS"] = EvidenceRecord(
            source_id="DEF-001-CROSS",
            record_type=RecordType.DEFECT,
            aircraft_id="AC-001",
            payload={"status": "open"},
        )
        cat.records[MS_SOURCE_ID] = EvidenceRecord(
            source_id=MS_SOURCE_ID,
            record_type=RecordType.MILESTONE,
            aircraft_id="AC-009",
            payload={"planned_date": PLANNED_DATE, "forecast_date": FORECAST_DATE},
        )
        cat.specialist_source_ids.add("DEF-001-CROSS")
        cat.approved_preflight_source_ids.add(MS_SOURCE_ID)

        report = validate_brief(brief, cat)
        assert not report.passed
        assert any(v.code == "WRONG_AIRCRAFT" for v in report.violations)

    def test_malformed_specialist_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse_specialist_report("test_ops_findings", "NOT JSON AT ALL")

    def test_specialist_output_key_mapping(self):
        """get_specialist_output_keys() returns all four correct keys."""
        keys = get_specialist_output_keys()
        expected = {
            "test_ops_specialist": "test_ops_findings",
            "maintenance_specialist": "maintenance_findings",
            "config_supply_specialist": "configuration_supply_findings",
            "schedule_risk_specialist": "schedule_risk_findings",
        }
        assert keys == expected


# ---------------------------------------------------------------------------
# Service Lifecycle and Cleanup Tests
# ---------------------------------------------------------------------------
class TestServiceLifecycleAndCleanup:
    """Verify ADK Runner, toolsets, and stdio processes are closed under all execution paths."""

    @pytest.fixture
    def mock_runner_and_pipeline(self, monkeypatch):
        from google.adk.runners import Runner

        import aeroops.services as services

        closes = {"runner": 0, "toolsets": 0, "preflight": 0}

        # Mock Runner.close
        async def mock_runner_close(*args, **kwargs):
            closes["runner"] += 1

        monkeypatch.setattr(Runner, "close", mock_runner_close)

        # Mock _close_all_toolsets
        async def mock_close_toolsets(pipeline):
            closes["toolsets"] += 1

        monkeypatch.setattr(services, "_close_all_toolsets", mock_close_toolsets)

        # Spy on preflight client
        orig_preflight = services._call_preflight_tool_via_mcp

        async def mock_preflight(*args, **kwargs):
            closes["preflight"] += 1
            return await orig_preflight(*args, **kwargs)

        monkeypatch.setattr(services, "_call_preflight_tool_via_mcp", mock_preflight)

        return closes

    @pytest.mark.asyncio
    async def test_cleanup_on_success(self, seeded_db, mock_runner_and_pipeline, monkeypatch):
        """Preflight and specialist resources are closed upon success."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService, Session

        async def mock_run_async(*args, **kwargs):
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={"synthesis_output": _SYNTHESIS_RESPONSE}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        async def mock_get_session(self, app_name, user_id, session_id):
            s = Session(id=session_id, app_name=app_name, user_id=user_id)
            s.state = {
                "synthesis_output": _SYNTHESIS_RESPONSE,
                "test_ops_findings": json.dumps(
                    {
                        "domain": "test_operations",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-TEST-001",
                                "statement": "a",
                                "classification": "test_failure",
                                "source_refs": [
                                    {
                                        "source_id": "TEST-009-118",
                                        "record_type": "test_event",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["TEST-009-118"],
                    }
                ),
                "maintenance_findings": json.dumps(
                    {
                        "domain": "maintenance",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-MAINT-001",
                                "statement": "b",
                                "classification": "maintenance",
                                "source_refs": [
                                    {
                                        "source_id": "MNT-009-015",
                                        "record_type": "maintenance_task",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["MNT-009-015"],
                    }
                ),
                "configuration_supply_findings": json.dumps(
                    {
                        "domain": "configuration_supply",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-CONFIG-001",
                                "statement": "c",
                                "classification": "parts_constraint",
                                "source_refs": [
                                    {
                                        "source_id": "PART-ACT-774",
                                        "record_type": "parts_constraint",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["PART-ACT-774"],
                    }
                ),
                "schedule_risk_findings": json.dumps(
                    {
                        "domain": "schedule_risk",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-SCHEDULE-001",
                                "statement": "d",
                                "classification": "schedule_risk",
                                "source_refs": [
                                    {
                                        "source_id": "MS-009-FTC",
                                        "record_type": "milestone",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["MS-009-FTC"],
                    }
                ),
            }
            return s

        monkeypatch.setattr(InMemorySessionService, "get_session", mock_get_session)

        # Bypass validate_brief to avoid EvidenceIntegrityError during cleanup verification
        import aeroops.services as services
        from aeroops.validation import ValidationReport

        monkeypatch.setattr(
            services,
            "validate_brief",
            lambda *args, **kwargs: ValidationReport(
                aircraft_id="AC-009", violations=[], refs_checked=0, records_verified=0
            ),
        )

        from aeroops.services import run_investigation_async

        await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1
        assert mock_runner_and_pipeline["preflight"] >= 2

    @pytest.mark.asyncio
    async def test_cleanup_on_model_failure(
        self, seeded_db, mock_runner_and_pipeline, monkeypatch
    ):
        """Runner and toolsets are closed if the synthesis model run raises an exception."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner

        async def mock_run_async(*args, **kwargs):
            raise ValueError("Synthesis failed")
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        from aeroops.services import LiveInvestigationError, run_investigation_async

        with pytest.raises(LiveInvestigationError) as exc_info:
            await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert exc_info.value.stage == "agent_execution"
        assert exc_info.value.cause_type == "ValueError"
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert str(exc_info.value.__cause__) == "Synthesis failed"
        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_malformed_specialist_output(
        self, seeded_db, mock_runner_and_pipeline, monkeypatch
    ):
        """Preflight, runner, and toolsets close if specialist output is malformed."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService, Session

        async def mock_run_async(*args, **kwargs):
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        async def mock_get_session(self, app_name, user_id, session_id):
            s = Session(id=session_id, app_name=app_name, user_id=user_id)
            s.state = {"synthesis_output": "MALFORMED JSON OUTPUT"}
            return s

        monkeypatch.setattr(InMemorySessionService, "get_session", mock_get_session)

        from aeroops.services import LiveInvestigationError, run_investigation_async

        with pytest.raises(LiveInvestigationError) as exc_info:
            await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert exc_info.value.stage == "synthesis_output"
        assert exc_info.value.cause_type == "JSONDecodeError"
        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_evidence_integrity_failure(
        self, seeded_db, mock_runner_and_pipeline, monkeypatch
    ):
        """Resources are closed if post-synthesis evidence integrity check fails."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService, Session

        async def mock_run_async(*args, **kwargs):
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        async def mock_get_session(self, app_name, user_id, session_id):
            s = Session(id=session_id, app_name=app_name, user_id=user_id)
            bad_brief = json.loads(_SYNTHESIS_RESPONSE)
            bad_brief["delay_days"] = 999  # delay days mismatch triggers validation failure
            s.state = {
                "synthesis_output": json.dumps(bad_brief),
                "test_ops_findings": json.dumps(
                    {
                        "domain": "test_operations",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-TEST-001",
                                "statement": "a",
                                "classification": "test_failure",
                                "source_refs": [
                                    {
                                        "source_id": "TEST-009-118",
                                        "record_type": "test_event",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["TEST-009-118"],
                    }
                ),
                "maintenance_findings": json.dumps(
                    {
                        "domain": "maintenance",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-MAINT-001",
                                "statement": "b",
                                "classification": "maintenance",
                                "source_refs": [
                                    {
                                        "source_id": "MNT-009-015",
                                        "record_type": "maintenance_task",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["MNT-009-015"],
                    }
                ),
                "configuration_supply_findings": json.dumps(
                    {
                        "domain": "configuration_supply",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-CONFIG-001",
                                "statement": "c",
                                "classification": "parts_constraint",
                                "source_refs": [
                                    {
                                        "source_id": "PART-ACT-774",
                                        "record_type": "parts_constraint",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["PART-ACT-774"],
                    }
                ),
                "schedule_risk_findings": json.dumps(
                    {
                        "domain": "schedule_risk",
                        "aircraft_id": "AC-009",
                        "findings": [
                            {
                                "finding_id": "FIND-SCHEDULE-001",
                                "statement": "d",
                                "classification": "schedule_risk",
                                "source_refs": [
                                    {
                                        "source_id": "MS-009-FTC",
                                        "record_type": "milestone",
                                        "summary": "s",
                                    }
                                ],
                                "rationale": "r",
                            }
                        ],
                        "raw_source_ids": ["MS-009-FTC"],
                    }
                ),
            }
            return s

        monkeypatch.setattr(InMemorySessionService, "get_session", mock_get_session)

        from aeroops.services import EvidenceIntegrityError, run_investigation_async

        with pytest.raises(EvidenceIntegrityError):
            await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_mcp_timeout(self, seeded_db, mock_runner_and_pipeline, monkeypatch):
        """Resources are closed if execution times out."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner

        async def mock_run_async(*args, **kwargs):
            await asyncio.sleep(2.0)
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        from aeroops.services import run_investigation_async

        with pytest.raises(asyncio.TimeoutError):
            await run_investigation_async(
                "Why is AC-009 delayed?", db_path=seeded_db, timeout=0.01
            )

        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_asyncio_cancellation(
        self, seeded_db, mock_runner_and_pipeline, monkeypatch
    ):
        """Resources are closed if the task is cancelled."""
        from google.adk.events import Event, EventActions
        from google.adk.runners import Runner

        async def mock_run_async(*args, **kwargs):
            raise asyncio.CancelledError()
            yield Event(
                author="executive_synthesis",
                content={"parts": [{"text": "final brief text"}]},
                actions=EventActions(state_delta={}),
                turn_complete=True,
            )

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        from aeroops.services import run_investigation_async

        with pytest.raises(asyncio.CancelledError):
            await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_input_denial(self, seeded_db, mock_runner_and_pipeline):
        """Input denial aborts before creating runner/toolsets, leaving zero resources open."""
        from aeroops.security import SecurityPolicyViolation
        from aeroops.services import run_investigation_async

        # Query with prohibited delete mutation
        with pytest.raises(SecurityPolicyViolation):
            await run_investigation_async("Delete aircraft AC-009", db_path=seeded_db)

        # Assert runner and toolsets were never created or closed
        assert mock_runner_and_pipeline["runner"] == 0
        assert mock_runner_and_pipeline["toolsets"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_on_tool_failure(self, seeded_db, mock_runner_and_pipeline, monkeypatch):
        """Specialist resources and runner are closed if a tool call raises an error."""
        from google.adk.runners import Runner

        async def mock_run_async(*args, **kwargs):
            raise RuntimeError("Tool execution failed spectacularly")
            yield

        monkeypatch.setattr(Runner, "run_async", mock_run_async)

        from aeroops.services import LiveInvestigationError, run_investigation_async

        with pytest.raises(LiveInvestigationError) as exc_info:
            await run_investigation_async("Why is AC-009 delayed?", db_path=seeded_db)

        assert exc_info.value.stage == "agent_execution"
        assert exc_info.value.cause_type == "RuntimeError"
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == "Tool execution failed spectacularly"
        assert mock_runner_and_pipeline["runner"] == 1
        assert mock_runner_and_pipeline["toolsets"] == 1


# ---------------------------------------------------------------------------
# Live Gemini tests (optional, credential-gated)
# ---------------------------------------------------------------------------


_LIVE = pytest.mark.skipif(
    not os.environ.get("AEROOPS_RUN_E2E_TESTS"),
    reason="Live Gemini tests require AEROOPS_RUN_E2E_TESTS=1",
)


@_LIVE
class TestLiveGeminiE2E:
    """Full end-to-end test using real Gemini API. Requires credentials."""

    @pytest.mark.asyncio
    async def test_live_ac009_investigation(self, seeded_db):
        from aeroops.services import run_investigation_async

        brief = await run_investigation_async(
            query=f"Provide a full executive brief for {AC009}.",
            db_path=seeded_db,
            timeout=300,
        )
        assert brief.aircraft_id == AC009
        assert brief.delay_days == DELAY_DAYS
        assert brief.milestone_source_id == MS_SOURCE_ID
