"""Pydantic schema contracts for all `app.api` endpoints.

Purpose:
- Centralize request and response models used by route handlers.
- Keep API payload validation and OpenAPI generation consistent.

Key responsibilities:
- Define enums and typed payload structures for FARMA, AEGIS, system, and job
  endpoints.
- Provide stable contracts consumed by frontend polling and dashboards.
- Capture optional nested payloads for simulation/marathon/report workflows.

Used by:
- `app.api.routes.system`, `app.api.routes.farmer`, `app.api.routes.jobs`,
  and `app.api.routes.aegis`.
- `app.api.__init__`, which re-exports selected models.

Assumptions:
- Pydantic v2 behavior (`ConfigDict`) is available.
- Datetime fields are serialized/deserialized in ISO-friendly formats.
- Some "extra=allow" payload models intentionally accept flexible keys from
  upstream workflow outputs.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# enums
class IntentType(str, Enum):
    """Canonical intent labels produced by FARMA intent classification."""

    LOAN_REQUEST = "LOAN_REQUEST"
    DISEASE_REPORT = "DISEASE_REPORT"
    WEATHER_INQUIRY = "WEATHER_INQUIRY"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"


class LoanDecision(str, Enum):
    """Loan decision outcomes surfaced by lending-related workflow steps."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HELD = "HELD"
    REVIEW = "REVIEW"


class ScanStatus(str, Enum):
    """Lifecycle states for AEGIS scan jobs."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportStatus(str, Enum):
    """Lifecycle states for AEGIS report-generation jobs."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ERROR = "error"


class ConflictEventSummary(BaseModel):
    """Normalized conflict event record included in scan status responses."""

    state: str
    lga: Optional[str] = None
    event_type: str
    fatalities: Optional[int] = None
    date: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class LGARiskEntry(BaseModel):
    """Computed or persisted risk aggregate for one LGA within a state."""

    lga: str
    state: str
    event_count: int
    fatalities: int
    risk_score: int
    risk_level: str  # CRITICAL | HIGH | ELEVATED | LOW


class StateSummaryEntry(BaseModel):
    """State-level risk/intelligence summary used by scan status APIs."""

    state_name: str
    conflict_events: int
    idp_estimate: Optional[int] = None
    idp_trend: str
    food_insecurity_level: str
    ipc_phase: Optional[int] = None
    markets_operational: str
    priority_level: Optional[str] = None
    priority_score: Optional[int] = None


class ParsedFarmerData(BaseModel):
    """Structured farmer information extracted from incoming messages."""

    crop_type: Optional[str] = None
    amount: Optional[float] = None
    landmark: Optional[str] = None
    symptoms: Optional[str] = None


class GeoCoordinates(BaseModel):
    """Geocoding output associated with a farmer or event location."""

    lat: Optional[float] = None
    lng: Optional[float] = None
    confidence: Optional[float] = None
    state: Optional[str] = None
    lga: Optional[str] = None


class SatelliteData(BaseModel):
    """Flexible satellite/remote-sensing payload attached to loan status."""

    model_config = ConfigDict(extra="allow")


class AlertEntry(BaseModel):
    """Flexible dashboard alert payload emitted by AEGIS workflows."""

    model_config = ConfigDict(extra="allow")


class JobEventPayload(BaseModel):
    """Flexible event payload envelope for job timeline entries."""

    model_config = ConfigDict(extra="allow")


class JobResult(BaseModel):
    """Flexible final result payload stored for asynchronous jobs."""

    model_config = ConfigDict(extra="allow")


# loan schemas
class SMSRequest(BaseModel):
    """SMS input from farmer (Twilio webhook format)."""

    From: str = Field(..., description="Phone number of the farmer")
    Body: str = Field(..., description="SMS message content")


class VoiceRequest(BaseModel):
    """Voice input metadata (file uploaded separately)."""

    phone: str = Field(..., description="Phone number of the farmer")


class LoanApplicationRequest(BaseModel):
    """Direct loan application from web UI."""

    phone: str = Field(..., description="Farmer's phone number")
    farmer_name: str = Field(..., description="Farmer's name")
    crop_type: str = Field(..., description="Type of crop (maize, rice, beans, etc.)")
    amount_requested: float = Field(..., description="Loan amount in Naira")
    landmark: str = Field(..., description="Nearest landmark to farm location")
    farm_size_hectares: Optional[float] = Field(
        None, description="Farm size in hectares"
    )
    language: str = Field(
        default="English", description="Preferred language for communication"
    )


class FarmerResponse(BaseModel):
    """Response after processing farmer request."""

    status: str
    intent: Optional[str] = None
    language: Optional[str] = None
    parsed_data: Optional[ParsedFarmerData] = None
    farmer_response: Optional[str] = None
    coordinates: Optional[GeoCoordinates] = None
    climate_score: Optional[float] = None
    final_decision: Optional[str] = None
    risk_flags: Optional[List[str]] = None


class LoanStatusResponse(BaseModel):
    """Loan application status."""

    phone: str
    status: str
    decision: Optional[LoanDecision] = None
    climate_score: Optional[float] = None
    satellite_data: Optional[SatelliteData] = None
    created_at: Optional[datetime] = None
    message: Optional[str] = None


# aegis
class AegisScanRequest(BaseModel):
    """Request to trigger an AEGIS data collection scan."""

    states: Optional[List[str]] = Field(
        default=None, description="States to scan. Defaults to North East focus states."
    )
    days_back: int = Field(
        default=7,
        ge=1,
        le=365,
        description="How many days back to search for signals",
    )
    force_refresh: bool = Field(
        default=False, description="Force refresh even if recent data exists"
    )


class AegisScanResponse(BaseModel):
    """Response from scan initiation."""

    scan_id: int
    run_id: str
    status: ScanStatus
    states_to_scan: List[str]
    message: str


class AegisScanStatusResponse(BaseModel):
    """Status of an AEGIS scan."""

    scan_id: int
    run_id: str
    status: ScanStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    states_scanned: int
    total_events: int
    total_fatalities: int
    state_summaries: Optional[List[StateSummaryEntry]] = None
    conflict_events: Optional[List[ConflictEventSummary]] = None
    lga_risk: Optional[List[LGARiskEntry]] = None


class AegisReportRequest(BaseModel):
    """Request to generate an AEGIS report."""

    scan_id: int = Field(..., description="Scan ID to generate report from")
    states: Optional[List[str]] = Field(
        default=None,
        description="Specific states to include. Defaults to all scanned states.",
    )
    include_infographics: bool = Field(
        default=True, description="Whether to generate AI infographics"
    )
    include_annexes: bool = Field(
        default=True, description="Whether to include detailed state annexes"
    )
    simulation_id: Optional[str] = Field(
        default=None,
        description="Optional crisis simulation_id to include in the report (packaging-only).",
    )


class AegisSynthesisRequest(BaseModel):
    """Request to synthesize an AEGIS scan into decision-grade assessments."""

    scan_id: int = Field(..., description="Scan ID to synthesize")
    states: Optional[List[str]] = Field(
        default=None,
        description="Specific states to include. Defaults to all scanned states.",
    )


class AegisSynthesisResponse(BaseModel):
    """Response from synthesis initiation."""

    run_id: str
    status: str
    message: str


class AegisReportResponse(BaseModel):
    """Response from report generation."""

    report_id: str
    status: ReportStatus
    message: str
    pdf_path: Optional[str] = None
    download_url: Optional[str] = None


class AegisReportStatusResponse(BaseModel):
    """Status of report generation."""

    report_id: str
    status: ReportStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    pdf_path: Optional[str] = None
    download_url: Optional[str] = None
    steps_completed: List[str] = []
    timings: Dict[str, float] = {}
    error: Optional[str] = None
    states_analyzed: List[str] = []
    sources_cited: int = 0
    infographics_generated: int = 0


class StateIntelligenceSummary(BaseModel):
    """Summary of intelligence for a single state."""

    state_name: str
    conflict_events: int
    idp_estimate: Optional[int] = None
    idp_trend: str
    food_insecurity_level: str  # minimal, stressed, crisis, emergency, famine
    ipc_phase: Optional[int] = None  # 1-5
    markets_operational: str
    priority_level: Optional[str] = None
    priority_score: Optional[int] = None


class AegisDashboardResponse(BaseModel):
    """Dashboard data for AEGIS overview."""

    latest_scan: Optional[AegisScanStatusResponse] = None
    total_scans: int
    total_reports: int
    focus_states: List[str]
    state_summaries: List[StateIntelligenceSummary]
    recent_alerts: List[AlertEntry] = []


# system


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    database: str
    services: Dict[str, str]


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# job contract
class JobStatus(str, Enum):
    """Canonical lifecycle states for async workflow jobs."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_HUMAN = "awaiting_human"


class JobEvent(BaseModel):
    """Single progress event emitted while a job is executing."""

    event_id: str
    job_id: str
    created_at: datetime
    event_type: str
    status: str
    step: Optional[str] = None
    message: Optional[str] = None
    progress: Optional[float] = None
    payload: Optional[JobEventPayload] = None


class JobResponse(BaseModel):
    """Top-level job status payload returned by job/status endpoints."""

    job_id: str
    job_type: str
    status: JobStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[JobResult] = None


class JobEventsResponse(BaseModel):
    """Container for a job ID and its ordered event timeline."""

    job_id: str
    events: List[JobEvent]


class ResumeRequest(BaseModel):
    """Payload used by human agents to resume interrupted farmer workflows."""

    response_text: str = Field(
        min_length=1,
        description="The human agent's response text to send to the farmer",
    )


class EscalationTriage(BaseModel):
    """Structured triage details for human-escalation workflow branches."""

    severity: str  # low | medium | high | critical
    category: str  # UNRECOGNIZED_INTENT | FIELD_VERIFICATION | HIGH_RISK_CASE | COMPLEX_INQUIRY | SAFETY_CONCERN
    reason: str
    draft_response: str
    farmer_phone: Optional[str] = None
    farmer_message: Optional[str] = None
    intent: Optional[str] = None
    language: Optional[str] = None


# marathon
class AegisMarathonRunRequest(BaseModel):
    """Input payload for a single marathon-day continuity execution."""

    track_id: str = Field(min_length=3)
    scan_id: Optional[int] = Field(default=None, ge=1, description="Required for manual mode; autonomous mode finds latest scan")
    day_date: Optional[str] = Field(
        default=None, description="YYYY-MM-DD; defaults to today (UTC)"
    )
    prev_scan_id: Optional[int] = Field(default=None, ge=1)
    mode: str = Field(default="manual", pattern=r"^(manual|autonomous)$")


class AegisMarathonRunResponse(BaseModel):
    """Immediate response returned when a marathon run is queued."""

    run_id: str
    status: JobStatus
    track_id: str
    scan_id: Optional[int] = None
    day_date: str
    mode: str = "manual"
    actions_taken: List[str] = Field(default_factory=list)


class AegisDemoRunRequest(BaseModel):
    """Input payload for one-click end-to-end demo orchestration."""

    track_id: Optional[str] = None
    states: Optional[List[str]] = None
    days_back: int = Field(default=7, ge=1, le=365)
    force_refresh: bool = False
    include_infographics: bool = False
    include_annexes: bool = True
    simulation_scenario: Optional[Dict[str, Any]] = None


class AegisDemoRunResponse(BaseModel):
    """Immediate response returned when demo orchestrator run is queued."""

    run_id: str
    status: JobStatus
    track_id: str
    period_key: str
    message: str


class AegisMarathonDayResponse(BaseModel):
    """Persisted per-day marathon record exposed in timeline responses."""

    id: int
    track_id: str
    day_date: str
    scan_id: int
    prev_scan_id: Optional[int] = None
    delta_json: Optional[dict] = None
    continuity_note_json: Optional[dict] = None
    thought_signature: Optional[str] = None
    prev_thought_signature: Optional[str] = None
    signature_short: Optional[str] = None
    prev_signature_short: Optional[str] = None
    thinking_level: Optional[str] = None
    actions_taken: Optional[List[str]] = None
    simulation_triggered: Optional[str] = None
    report_triggered: Optional[str] = None
    created_at: Optional[str] = None


class ContinuityChainEntry(BaseModel):
    """Derived continuity summary entry for one marathon day."""

    day_date: str
    thinking_level: Optional[str] = None
    summary: str = ""
    decision_explanation: str = ""
    predictions: List[str] = Field(default_factory=list)
    self_corrections: List[str] = Field(default_factory=list)
    actions_taken: List[str] = Field(default_factory=list)
    signature_linked: bool = False  # True if prev_thought_signature matches prior day


class AegisMarathonTimelineResponse(BaseModel):
    """Timeline response bundling raw marathon days and derived continuity data."""

    track_id: str
    days: List[AegisMarathonDayResponse] = Field(default_factory=list)
    continuity_chain: List[ContinuityChainEntry] = Field(default_factory=list)
    total_days: int = 0
    total_self_corrections: int = 0
    total_actions: int = 0


class AegisPipelineReadinessResponse(BaseModel):
    """Readiness snapshot for stage gating by scan ID."""

    scan_id: int
    scan_exists: bool = False
    scan_status: str = "unknown"
    has_rollup_json: bool = False
    assessments_count: int = 0
    synthesis_ready: bool = False
    simulation_ready: bool = False
    report_ready: bool = False
    marathon_ready: bool = False
    missing_requirements: List[str] = Field(default_factory=list)


class AegisSimulationRequest(BaseModel):
    """Input payload for launching an AEGIS simulation run."""

    scan_id: int = Field(..., ge=1)
    scenario: Dict[str, Any]


class AegisSimulationResponse(BaseModel):
    """Immediate response returned after simulation job creation."""

    simulation_id: str
    status: str
    message: str


class AegisSimulationStatusResponse(BaseModel):
    """Persisted status/details payload for a simulation run."""

    simulation_id: str
    status: str
    scan_id: int
    created_at: Optional[str] = None
    scenario_json: Optional[dict] = None
    projections_json: Optional[dict] = None
    policy_brief_json: Optional[dict] = None
    error: Optional[str] = None
