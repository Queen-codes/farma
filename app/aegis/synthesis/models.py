"""Synthesis Agent Models - Thought signatures, outputs, and audit structures.

Implements:
- Thought signatures for ReAct reasoning
- Task decomposition structures
- Sourced findings with URI traceability
- Full audit trail models
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime, timezone
from enum import Enum


# react thought/reasoning pattern
class ThoughtType(str, Enum):
    """Types of thoughts in the reasoning process."""

    OBSERVATION = "observation"  # What the agent observes from data
    REASONING = "reasoning"  # Logical inference
    PLANNING = "planning"  # Task decomposition
    HYPOTHESIS = "hypothesis"  # Tentative conclusion
    VERIFICATION = "verification"  # Checking hypothesis
    REFLECTION = "reflection"  # Self-assessment
    CORRECTION = "correction"  # Fixing errors
    CONCLUSION = "conclusion"  # Final decision


class Thought(BaseModel):
    """A single thought in the reasoning chain."""

    thought_type: ThoughtType
    content: str
    confidence: float = Field(
        ge=0.0, le=1.0, description="0-1 confidence in this thought"
    )
    supporting_data: List[str] = Field(
        default_factory=list, description="Data points supporting this thought"
    )
    source_refs: List[str] = Field(
        default_factory=list, description="URIs supporting this thought"
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ReasoningChain(BaseModel):
    """Full chain of thoughts for a reasoning task."""

    task: str
    thoughts: List[Thought] = Field(default_factory=list)
    final_conclusion: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_revision: bool = False
    revision_reason: Optional[str] = None


# task decomposition
class SubTask(BaseModel):
    """A decomposed subtask."""

    task_id: str
    description: str
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    depends_on: List[str] = Field(
        default_factory=list, description="Task IDs this depends on"
    )
    tools_required: List[str] = Field(default_factory=list)
    result: Optional[Dict[str, Any]] = None


class TaskPlan(BaseModel):
    """Decomposed task plan."""

    main_objective: str
    subtasks: List[SubTask]
    current_task_id: Optional[str] = None
    completed_count: int = 0
    total_count: int = 0


# sourced findings
class SourcedFinding(BaseModel):
    """A finding that MUST have source references."""

    finding_id: str
    finding: str = Field(description="The factual finding or assertion")
    finding_type: Literal[
        "food_insecurity_identified",
        "vulnerable_population",
        "humanitarian_need",
        "access_constraint",
        "trend_observation",
        "prediction",
        "anomaly",
        "comparison",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str] = Field(description="Specific data points as evidence")
    source_refs: List[str] = Field(
        description="URIs supporting this finding - REQUIRED"
    )
    reasoning_chain: Optional[ReasoningChain] = None
    verified: bool = False
    verification_notes: Optional[str] = None


class VulnerablePopulation(BaseModel):
    """Identified vulnerable population group needing assistance."""

    group_name: str
    population_type: Literal[
        "idp", "returnee", "host_community", "farmer", "pastoralist", "mixed"
    ]
    estimated_population: Optional[int] = None
    vulnerability_level: Literal["critical", "high", "moderate", "low", "unknown"]
    locations: List[str] = Field(description="LGAs or camps where located")
    primary_needs: List[str] = Field(
        default_factory=list, description="Food, shelter, healthcare, etc."
    )
    source_refs: List[str] = Field(default_factory=list)


# output structure for the final synthesis products( include reasoning and source references)
class HumanitarianAssessment(BaseModel):
    """Complete humanitarian and food security assessment for a state."""

    state: str
    assessment_date: str

    # Vulnerable populations
    vulnerable_populations: List[VulnerablePopulation] = Field(default_factory=list)
    need_severity: Literal["critical", "high", "elevated", "moderate", "low"]

    # Food security status
    ipc_phase: Optional[int] = Field(default=None, description="IPC phase 1-5")
    food_insecurity_level: Literal[
        "famine", "emergency", "crisis", "stressed", "minimal", "unknown"
    ] = "unknown"

    # Findings with sources
    findings: List[SourcedFinding] = Field(default_factory=list)

    # Priority areas
    priority_lgas: List[str] = Field(default_factory=list)
    malnutrition_hotspots: List[str] = Field(default_factory=list)

    # Drivers and constraints
    food_insecurity_drivers: List[str] = Field(default_factory=list)
    access_constraints: List[str] = Field(default_factory=list)

    # Reasoning trace
    reasoning: ReasoningChain

    # All sources used
    all_source_refs: List[str] = Field(default_factory=list)


# risk scorer output
class RiskFactor(BaseModel):
    """A single risk factor with score contribution."""

    factor_name: str
    category: Literal[
        "security", "displacement", "economic", "infrastructure", "humanitarian"
    ]
    score_contribution: float = Field(ge=0.0, le=100.0)
    weight: float = Field(ge=0.0, le=1.0)
    rationale: str
    data_points: List[str]
    source_refs: List[str]


class RiskScore(BaseModel):
    """Complete risk score for a state."""

    state: str
    assessment_date: str

    # Component scores (0-100)
    security_score: float = Field(ge=0.0, le=100.0)
    displacement_score: float = Field(ge=0.0, le=100.0)
    economic_score: float = Field(ge=0.0, le=100.0)
    humanitarian_score: float = Field(ge=0.0, le=100.0)

    # Composite
    composite_score: float = Field(ge=0.0, le=100.0)
    risk_level: Literal["critical", "high", "elevated", "moderate", "low"]

    # Breakdown with sources
    risk_factors: List[RiskFactor] = Field(default_factory=list)

    # Comparison to baseline
    baseline_comparison: Optional[str] = None
    deviation_from_baseline: Optional[float] = None

    # Reasoning trace
    reasoning: ReasoningChain

    # Sources
    all_source_refs: List[str] = Field(default_factory=list)


# prediction engine output
class PredictionFactor(BaseModel):
    """Factor influencing the prediction."""

    factor: str
    direction: Literal["increases_need", "decreases_need", "neutral"]
    weight: float = Field(ge=0.0, le=1.0)
    rationale: str
    source_refs: List[str]


class Prediction(BaseModel):
    """Humanitarian outlook prediction for a state."""

    state: str
    prediction_date: str
    forecast_period: str  # e.g., "7 days", "14 days"

    # Outlook
    outlook: Literal["improving", "stable", "deteriorating", "volatile", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)

    # Specific predictions
    projected_ipc_phase: Optional[int] = Field(
        default=None, description="Expected IPC phase"
    )
    projected_beneficiaries: Optional[int] = Field(
        default=None, description="Estimated people needing aid"
    )
    priority_intervention_areas: List[str] = Field(default_factory=list)
    emerging_concerns: List[str] = Field(default_factory=list)

    # Factors
    prediction_factors: List[PredictionFactor] = Field(default_factory=list)

    # Caveats
    key_uncertainties: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

    # Reasoning trace
    reasoning: ReasoningChain

    # Sources
    all_source_refs: List[str] = Field(default_factory=list)


# audiit - record every  tool call, thought, error - important for auditing


class ToolCall(BaseModel):
    """Record of a tool invocation."""

    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    result_summary: str
    timestamp: str
    duration_ms: int
    success: bool
    error: Optional[str] = None


class AuditEntry(BaseModel):
    """Single audit log entry."""

    entry_id: str
    timestamp: str
    action_type: Literal[
        "task_start",
        "task_decomposition",
        "thought",
        "tool_call",
        "finding_recorded",
        "task_complete",
        "error",
    ]
    description: str
    details: Dict[str, Any] = Field(default_factory=dict)
    source_refs: List[str] = Field(default_factory=list)


class AuditTrail(BaseModel):
    """Complete audit trail for a synthesis run."""

    run_id: str
    scan_id: int
    started_at: str
    completed_at: Optional[str] = None

    # All entries in order
    entries: List[AuditEntry] = Field(default_factory=list)

    # Tool call log
    tool_calls: List[ToolCall] = Field(default_factory=list)

    # Summary stats
    total_thoughts: int = 0
    total_tool_calls: int = 0
    total_findings: int = 0

    # Final outputs
    humanitarian_assessments: List[HumanitarianAssessment] = Field(default_factory=list)
    risk_scores: List[RiskScore] = Field(default_factory=list)
    predictions: List[Prediction] = Field(default_factory=list)

    # All unique sources
    all_source_refs: List[str] = Field(default_factory=list)


# final output of the agent
class SynthesisReport(BaseModel):
    """Complete synthesis report with full traceability."""

    report_id: str
    scan_id: int
    generated_at: str

    # State-level assessments
    humanitarian_assessments: List[HumanitarianAssessment]
    risk_scores: List[RiskScore]
    predictions: List[Prediction]

    # National summary
    national_need_level: Literal["critical", "high", "elevated", "moderate", "low"]
    national_summary: str
    priority_states: List[str]

    # All findings with sources
    key_findings: List[SourcedFinding]

    # Audit trail for transparency
    audit_trail: AuditTrail

    # All sources for citation
    all_sources: List[str]
    source_count: int
