from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class Finding(BaseModel):
    finding: str
    source_uris: List[str] = Field(default_factory=list)


class Assessment(BaseModel):
    scan_id: int
    state: str
    summary: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    key_findings: List[Finding] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    audit: dict = Field(default_factory=dict)


class RollupRanking(BaseModel):
    state: str
    rank: int
    rationale: str
    source_uris: List[str] = Field(default_factory=list)


class Rollup(BaseModel):
    scan_id: int
    overall_summary: str
    rankings: List[RollupRanking] = Field(default_factory=list)
    allocations: List[dict] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


ASSESSMENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "scan_id": {"type": "integer"},
        "state": {"type": "string"},
        "summary": {"type": "string"},
        "risk_level": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        },
        "key_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string"},
                    "source_uris": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["finding", "source_uris"],
            },
        },
        "metrics": {
            "type": "object",
            "properties": {
                "priority_score": {"type": "integer"},
                "priority_level": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "ELEVATED", "HIGH", "CRITICAL"],
                },
                # using 0 as "unknown" sentinel for demo stability since Gemini response_schema doesn't support union types like ["integer","null"].
                "ipc_phase": {"type": "integer", "minimum": 0, "maximum": 5},
                "food_insecurity_level": {"type": "string"},
                # using 0 as "unknown" sentinel for demo stability.
                "idp_estimate": {"type": "integer", "minimum": 0},
                "idp_trend": {"type": "string"},
                "markets_operational": {"type": "string"},
                "conflict_events_count": {"type": "integer"},
                "conflict_hotspots_to_avoid": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "route_recommendation": {"type": "string"},
            },
            "required": [
                "priority_score",
                "priority_level",
                "ipc_phase",
                "food_insecurity_level",
                "idp_estimate",
                "idp_trend",
                "markets_operational",
                "conflict_events_count",
                "conflict_hotspots_to_avoid",
                "route_recommendation",
            ],
        },
        "confidence": {"type": "number"},
        "audit": {
            "type": "object",
            "properties": {
                "allowed_uris_count": {"type": "integer"},
                "tool_errors_summary": {"type": "string"},
            },
            "required": ["allowed_uris_count", "tool_errors_summary"],
        },
    },
    "required": [
        "scan_id",
        "state",
        "summary",
        "risk_level",
        "key_findings",
        "metrics",
        "confidence",
        "audit",
    ],
}


ROLLUP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "scan_id": {"type": "integer"},
        "overall_summary": {"type": "string"},
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "rank": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "source_uris": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["state", "rank", "rationale", "source_uris"],
            },
        },
        "allocations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "allocation_pct": {"type": "number"},
                    "note": {"type": "string"},
                    "source_uris": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["state", "allocation_pct", "note", "source_uris"],
            },
        },
        "confidence": {"type": "number"},
    },
    "required": ["scan_id", "overall_summary", "rankings", "allocations", "confidence"],
}
