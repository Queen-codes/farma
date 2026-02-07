"""Pydantic schemas for parser and underwriting structured JSON contracts.

This module defines strict data models used in two places:
- Prompted JSON outputs from Gemini parser/underwriter nodes.
- Runtime validation before values are written into workflow state.

Used by:
- `app.workflows.nodes.parsers.sms_parser`
- `app.workflows.nodes.parsers.voice_parser`
- `app.workflows.nodes.loan.underwriter`
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class LocationParse(BaseModel):
    """Structured location extraction from incoming farmer messages."""

    landmark: str = ""
    geocode_query: str = ""
    needs_clarification: bool = False
    clarifying_question: str = ""


class LoanParse(BaseModel):
    """Loan-request fields extracted by parser nodes."""

    amount: float = 0.0
    crop_type: str = ""
    farm_size: str = ""
    crop_stage: str = ""


class DiseaseParse(BaseModel):
    """Disease-report fields extracted by parser nodes."""

    crop_type: str = ""
    symptoms: str = ""


class WeatherParse(BaseModel):
    """Weather-inquiry fields extracted by parser nodes."""

    question_type: str = ""
    time_horizon_days: int = Field(default=0, ge=0)


class SMSParseOutput(BaseModel):
    """Top-level parser output contract for SMS/voice input parsing."""

    intent: Literal[
        "LOAN_REQUEST", "DISEASE_REPORT", "WEATHER_INQUIRY", "HUMAN_ESCALATION"
    ]
    language: str
    parse_confidence: float = Field(ge=0.0, le=1.0)
    location: LocationParse
    loan: LoanParse
    disease: DiseaseParse
    weather: WeatherParse


class LoanTerms(BaseModel):
    """Normalized loan terms attached to underwriting decisions."""

    grace_days: int = Field(ge=0)
    tenor_days: int = Field(ge=0)
    repayment_schedule: str = ""
    requires_field_verification: bool = False


class LoanDecisionOutput(BaseModel):
    """Final underwriting decision contract returned by loan node."""

    decision: Literal[
        "APPROVE_SMALL",
        "APPROVE_WITH_TERMS",
        "HOLD_FOR_VERIFICATION",
        "REJECT",
    ]
    approved_amount: int = Field(ge=0)
    terms: LoanTerms
    reasoning: List[str] = Field(default_factory=list, max_length=6)
    risk_flags: List[str] = Field(default_factory=list)
    follow_up_questions: List[str] = Field(default_factory=list, max_length=4)
    sms_160: str = Field(default_factory=str)


SMS_PARSE_SCHEMA: dict = SMSParseOutput.model_json_schema()
DECISION_SCHEMA: dict = LoanDecisionOutput.model_json_schema()
