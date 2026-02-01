"""Pydantic schemas for API request/response validation.

These schemas define the API contract that AI Studio will use
to build the frontend.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# enums
class IntentType(str, Enum):
    LOAN_REQUEST = "LOAN_REQUEST"
    DISEASE_REPORT = "DISEASE_REPORT"
    WEATHER_INQUIRY = "WEATHER_INQUIRY"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"


class LoanDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HELD = "HELD"
    REVIEW = "REVIEW"


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


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
    parsed_data: Optional[Dict[str, Any]] = None
    farmer_response: Optional[str] = None
    coordinates: Optional[Dict[str, Any]] = None
    climate_score: Optional[float] = None
    final_decision: Optional[str] = None
    risk_flags: Optional[List[str]] = None


class LoanStatusResponse(BaseModel):
    """Loan application status."""

    phone: str
    status: str
    decision: Optional[LoanDecision] = None
    climate_score: Optional[float] = None
    satellite_data: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    message: Optional[str] = None


# aegis
class AegisScanRequest(BaseModel):
    """Request to trigger an AEGIS data collection scan."""

    states: Optional[List[str]] = Field(
        default=None, description="States to scan. Defaults to North East focus states."
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
    state_summaries: Optional[List[Dict[str, Any]]] = None


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
    recent_alerts: List[Dict[str, Any]] = []


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
