"""Pydantic domain models for AeroOps.

This module defines the validation schemas and domain models for AeroOps.
All models use Pydantic v2.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Identifier patterns
# ---------------------------------------------------------------------------
AIRCRAFT_ID_PATTERN = r"^AC-\d{3}$"
MILESTONE_ID_PATTERN = r"^MS-\d{3}-[A-Z0-9-]+$"
DEFECT_ID_PATTERN = r"^DEF-\d{3}-\d{3}$"
TEST_ID_PATTERN = r"^TEST-\d{3}-\d{3}$"
MNT_ID_PATTERN = r"^MNT-\d{3}-\d{3}$"
PART_ID_PATTERN = r"^PART-[A-Z0-9-]+$"
CR_ID_PATTERN = r"^CR-\d{3}$"
DEP_ID_PATTERN = r"^DEP-\d{3}-\d{3}$"

# ---------------------------------------------------------------------------
# Constrained literal types shared across investigation models
# ---------------------------------------------------------------------------

OverallStatus = Literal["green", "amber", "red", "unknown"]
ConfidenceLevel = Literal["high", "medium", "low"]
FindingClassification = Literal[
    "test_failure",
    "defect",
    "maintenance",
    "parts_constraint",
    "change_request",
    "schedule_risk",
    "dependency_blocker",
    "configuration",
    "other",
]
OwnerRole = Literal[
    "test_lead",
    "maintenance_lead",
    "supply_chain",
    "engineering",
    "program_management",
    "quality_assurance",
    "unknown",
]

# ---------------------------------------------------------------------------
# Existing operational database models
# ---------------------------------------------------------------------------


class HealthStatus(BaseModel):
    """Response model for the application health check."""

    status: str
    version: str
    model: str


class Aircraft(BaseModel):
    """Aircraft representation."""

    source_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    name: str
    status: str = Field(pattern="^(green|amber|red)$")
    responsible_org: str
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> Aircraft:
        """Ensure created_at is before or equal to updated_at."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        return self


class Milestone(BaseModel):
    """Milestone representation."""

    source_id: str = Field(pattern=MILESTONE_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    name: str
    planned_date: date
    forecast_date: date
    status: str = Field(pattern="^(complete|on_track|at_risk|delayed)$")
    responsible_role: str
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @property
    def variance_days(self) -> int:
        """Return the schedule variance in days (forecast - planned)."""
        return (self.forecast_date - self.planned_date).days

    @model_validator(mode="after")
    def validate_timestamps(self) -> Milestone:
        """Ensure created_at is before or equal to updated_at."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        return self


class Defect(BaseModel):
    """Defect representation."""

    source_id: str = Field(pattern=DEFECT_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    title: str
    description: str
    severity: str = Field(pattern="^(low|medium|high|critical)$")
    status: str = Field(pattern="^(open|in_progress|closed)$")
    discovered_at: datetime
    closed_at: datetime | None = None
    responsible_role: str
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> Defect:
        """Ensure timeline consistency."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        if self.closed_at is not None and self.discovered_at > self.closed_at:
            raise ValueError("discovered_at must be before or equal to closed_at")
        return self


class TestEvent(BaseModel):
    """TestEvent representation."""

    __test__ = False

    source_id: str = Field(pattern=TEST_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    name: str
    status: str = Field(pattern="^(planned|blocked|in_progress|completed|aborted)$")
    responsible_role: str
    scheduled_date: date
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> TestEvent:
        """Ensure timeline consistency."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.started_at > self.completed_at
        ):
            raise ValueError("started_at must be before or equal to completed_at")
        return self


class MaintenanceTask(BaseModel):
    """MaintenanceTask representation."""

    source_id: str = Field(pattern=MNT_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    title: str
    description: str
    status: str = Field(pattern="^(scheduled|in_progress|completed|deferred)$")
    responsible_role: str
    due_date: date
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> MaintenanceTask:
        """Ensure timeline consistency."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        return self


class PartsConstraint(BaseModel):
    """PartsConstraint representation."""

    source_id: str = Field(pattern=PART_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    part_number: str
    description: str
    status: str = Field(pattern="^(awaiting_delivery|delivered|delayed)$")
    responsible_org: str
    needed_by: date
    estimated_arrival: date | None = None
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> PartsConstraint:
        """Ensure timeline consistency."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        return self


class ChangeRequest(BaseModel):
    """ChangeRequest representation."""

    source_id: str = Field(pattern=CR_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    title: str
    description: str
    status: str = Field(pattern="^(pending_review|approved|rejected|implemented)$")
    responsible_role: str
    submitted_at: datetime
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_timestamps(self) -> ChangeRequest:
        """Ensure timeline consistency."""
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        if self.approved_at is not None and self.submitted_at > self.approved_at:
            raise ValueError("submitted_at must be before or equal to approved_at")
        return self


class ScheduleDependency(BaseModel):
    """ScheduleDependency representation."""

    source_id: str = Field(pattern=DEP_ID_PATTERN)
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    blocked_test_id: str = Field(pattern=TEST_ID_PATTERN)
    blocker_defect_id: str | None = Field(default=None, pattern=DEFECT_ID_PATTERN)
    blocker_parts_constraint_id: str | None = Field(default=None, pattern=PART_ID_PATTERN)
    blocker_change_request_id: str | None = Field(default=None, pattern=CR_ID_PATTERN)
    blocker_maintenance_task_id: str | None = Field(default=None, pattern=MNT_ID_PATTERN)
    created_at: datetime
    updated_at: datetime
    synthetic_data: bool = True

    @model_validator(mode="after")
    def validate_dependency_xor(self) -> ScheduleDependency:
        """Ensure exactly one blocker reference is populated."""
        blockers = [
            self.blocker_defect_id,
            self.blocker_parts_constraint_id,
            self.blocker_change_request_id,
            self.blocker_maintenance_task_id,
        ]
        non_null_count = sum(1 for b in blockers if b is not None)
        if non_null_count != 1:
            raise ValueError("Exactly one blocker reference must be populated")
        if self.created_at > self.updated_at:
            raise ValueError("created_at must be before or equal to updated_at")
        return self


class BlockerRecord(BaseModel):
    """Unified blocker information returned for test event dependencies."""

    blocker_type: str  # 'defect' | 'parts_constraint' | 'change_request' | 'maintenance_task'
    source_id: str
    aircraft_id: str
    title: str
    status: str
    relevant_dates: dict[str, str | None]
    responsible_role_or_org: str


# ---------------------------------------------------------------------------
# Investigation workflow domain models
# ---------------------------------------------------------------------------


class RecordType(StrEnum):
    AIRCRAFT = "aircraft"
    MILESTONE = "milestone"
    DEFECT = "defect"
    TEST_EVENT = "test_event"
    MAINTENANCE_TASK = "maintenance_task"
    PARTS_CONSTRAINT = "parts_constraint"
    CHANGE_REQUEST = "change_request"
    SCHEDULE_DEPENDENCY = "schedule_dependency"


class EvidenceProvenance(BaseModel):
    originating_agent: str | None
    originating_stage: str
    originating_tool: str
    invocation_id: str
    branch_key: str
    branch_sequence: int
    function_call_id: str | None = None


class EvidenceRecord(BaseModel):
    source_id: str
    record_type: RecordType
    aircraft_id: str
    payload: dict
    provenance: list[EvidenceProvenance] = Field(default_factory=list)


class TestAbortedClaim(BaseModel):
    __test__ = False
    claim_type: Literal["test_aborted"] = "test_aborted"
    test_event_id: str = Field(min_length=1)


class DefectBlocksTestClaim(BaseModel):
    claim_type: Literal["defect_blocks_test"] = "defect_blocks_test"
    defect_id: str = Field(min_length=1)
    test_event_id: str = Field(min_length=1)


class PartArrivesAfterNeedDateClaim(BaseModel):
    claim_type: Literal["part_arrives_after_need_date"] = "part_arrives_after_need_date"
    parts_constraint_id: str = Field(min_length=1)


class ChangeRequestPendingClaim(BaseModel):
    claim_type: Literal["change_request_pending"] = "change_request_pending"
    change_request_id: str = Field(min_length=1)


class MaintenanceRequiredClaim(BaseModel):
    claim_type: Literal["maintenance_required"] = "maintenance_required"
    maintenance_task_id: str = Field(min_length=1)
    test_event_id: str | None = Field(default=None, min_length=1)


class DependencyBlocksTestClaim(BaseModel):
    claim_type: Literal["dependency_blocks_test"] = "dependency_blocks_test"
    dependency_id: str = Field(min_length=1)
    test_event_id: str = Field(min_length=1)


class MilestoneDelayedClaim(BaseModel):
    claim_type: Literal["milestone_delayed"] = "milestone_delayed"
    milestone_id: str = Field(min_length=1)


Claim = Annotated[
    TestAbortedClaim
    | DefectBlocksTestClaim
    | PartArrivesAfterNeedDateClaim
    | ChangeRequestPendingClaim
    | MaintenanceRequiredClaim
    | DependencyBlocksTestClaim
    | MilestoneDelayedClaim,
    Field(discriminator="claim_type"),
]


class InvestigationScope(BaseModel):
    """Normalized investigation request parsed by the Intake Agent.

    This object is stored in session state under ``investigation_scope`` and
    acts as the canonical record of what this investigation covers.
    """

    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN, description="Validated aircraft ID.")
    user_intent: str = Field(description="Natural-language restatement of the user's intent.")
    requested_time_horizon: str = Field(
        description="Time horizon for the investigation, e.g. '30 days', '90 days'."
    )
    requested_output_type: str = Field(
        description="Desired output format, e.g. 'executive_brief', 'detailed_report'."
    )
    target_milestone_id: str | None = Field(
        default=None, description="Explicitly requested target milestone ID."
    )


class EvidenceRef(BaseModel):
    """A single piece of evidence cited in a finding or recommendation.

    Each reference points back to a specific operational database record so
    findings can be independently verified against the MCP data layer.
    """

    source_id: str = Field(description="Operational record ID, e.g. 'TEST-009-118'.")
    record_type: str = Field(
        description="Domain type of the record, e.g. 'test_event', 'defect', 'milestone'."
    )
    summary: str = Field(description="One-sentence human-readable summary of the record.")


class Finding(BaseModel):
    """A discrete, evidence-backed observation identified by a specialist agent.

    Findings are the atomic unit of analysis. Each finding must be grounded in
    at least one operational record and classified into a domain category.
    """

    finding_id: str = Field(
        description="Deterministic finding ID assigned by validator or local temp ID."
    )
    statement: str = Field(description="Declarative statement of the finding.", min_length=1)
    classification: FindingClassification = Field(
        description="Domain classification of this finding."
    )
    source_refs: list[EvidenceRef] = Field(
        description="Operational records that support this finding.", min_length=1
    )
    rationale: str = Field(
        description="Brief explanation of why the evidence supports the finding.", min_length=1
    )
    claims: list[Claim] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    """An actionable recommendation derived from specialist findings.

    Every recommendation must be traceable to at least one finding and must
    identify the team or role responsible for execution.
    """

    action_id: str = Field(description="Action identifier matching pattern ACT-xxx.")
    action: str = Field(
        description="Imperative statement of the recommended action.", min_length=1
    )
    classification: FindingClassification = Field(
        description="Domain classification matching the underlying finding."
    )
    supporting_finding_ids: list[str] = Field(
        description="List of finding IDs that support this recommendation.", min_length=1
    )
    source_refs: list[EvidenceRef] = Field(
        description="Evidence references that motivate this recommendation.", min_length=1
    )
    rationale: str = Field(
        description="Why this action will resolve or mitigate the identified issue.", min_length=1
    )
    owner_role: OwnerRole = Field(
        description="Role or team accountable for executing this action."
    )
    suggested_due_date: str = Field(
        description="ISO 8601 date by which this action should be completed."
    )


class SpecialistReport(BaseModel):
    """Structured output produced by each specialist agent.

    Specialist agents store their report under a domain-specific key in session
    state: ``test_ops_findings``, ``maintenance_findings``,
    ``configuration_supply_findings``, or ``schedule_risk_findings``.
    """

    domain: str = Field(description="Specialist domain, e.g. 'test_operations'.")
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    findings: list[Finding] = Field(default_factory=list)
    raw_source_ids: list[str] = Field(
        default_factory=list,
        description="All operational record IDs queried during this investigation.",
    )


class AgentActivity(BaseModel):
    """Lightweight trace record of a single agent's execution step.

    Used to build the ``InvestigationTrace`` for observability and debugging.
    """

    agent_name: str
    action: str = Field(description="Brief description of what the agent did.")
    state_key: str | None = Field(
        default=None, description="Session-state key written by this agent, if any."
    )
    status: Literal["ok", "skipped", "error"] = "ok"
    detail: str | None = None


class InvestigationTrace(BaseModel):
    """Full audit trail for a single investigation run.

    Stored in session state under ``investigation_trace`` and included as a
    metadata field of the ``ExecutiveBrief``.
    """

    run_id: str = Field(description="Unique identifier for this investigation run.")
    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    activities: list[AgentActivity] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Executive synthesis output model
# ---------------------------------------------------------------------------


class ExecutiveBrief(BaseModel):
    """Structured executive investigation brief produced by the synthesis agent.

    This is the canonical output contract of the full investigation workflow.
    A deterministic after-model normalizer builds this model from validated
    specialist state before service-level Pydantic and evidence validation.

    ``delay_days`` is computed deterministically in Python as
    ``forecast_milestone_date - planned_milestone_date`` and validated here.
    """

    aircraft_id: str = Field(pattern=AIRCRAFT_ID_PATTERN)
    overall_status: OverallStatus = Field(
        description="Aggregated readiness color based on all specialist findings."
    )
    planned_milestone_date: date = Field(
        description="Planned completion date of the key milestone (from database)."
    )
    forecast_milestone_date: date = Field(
        description="Current forecast completion date of the key milestone (from database)."
    )
    delay_days: int = Field(
        description="Deterministic schedule variance: forecast_milestone_date - planned_milestone_date (days)."
    )
    executive_summary: str = Field(
        description="Two-to-three sentence summary suitable for leadership consumption."
    )
    confirmed_root_causes: list[Finding] = Field(
        default_factory=list,
        description="Primary root causes with full evidence chains.",
    )
    contributing_factors: list[Finding] = Field(
        default_factory=list,
        description="Secondary factors that amplify the primary root causes.",
    )
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list,
        description="Prioritised actions with owner roles and suggested due dates.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Explicit assumptions made in reaching these conclusions.",
    )
    unknowns: list[str] = Field(
        default_factory=list,
        description="Material uncertainties that could alter conclusions.",
    )
    confidence: ConfidenceLevel = Field(
        description="Analyst confidence in the overall assessment."
    )
    milestone_source_id: str = Field(
        description="Source record ID of the milestone used for date calculations, e.g. 'MS-009-FTC'."
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Expected top-level evidence list as the sorted, deduplicated union of accepted source refs.",
    )

    @model_validator(mode="after")
    def validate_delay_days(self) -> ExecutiveBrief:
        """Validate that delay_days exactly equals forecast - planned (deterministic)."""
        expected = (self.forecast_milestone_date - self.planned_milestone_date).days
        if self.delay_days != expected:
            raise ValueError(
                f"delay_days mismatch: stored {self.delay_days} but "
                f"forecast_milestone_date - planned_milestone_date = {expected}. "
                "delay_days must be computed deterministically from the stored dates."
            )
        return self
