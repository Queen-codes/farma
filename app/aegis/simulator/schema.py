"""Schema contracts for simulation policy-brief generation outputs.

Purpose:
- Define strict JSON structure expected from simulator LLM narrative step.
- Provide Pydantic validation for persisted policy brief content.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    """One actionable recommendation with ownership and citations."""

    title: str
    detail: str
    owner: str = "Partners"
    source_uris: List[str] = Field(default_factory=list)


class PolicyBrief(BaseModel):
    """Structured policy brief returned by simulator LLM node."""

    scan_id: int
    simulation_id: str
    summary: str
    ranked_recommendations: List[Recommendation] = Field(default_factory=list, max_length=8)
    humanitarian_notes: List[str] = Field(default_factory=list, max_length=6)
    farma_policy_notes: List[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)


POLICY_BRIEF_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "scan_id": {"type": "integer"},
        "simulation_id": {"type": "string"},
        "summary": {"type": "string"},
        "ranked_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "owner": {"type": "string"},
                    "source_uris": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "detail", "owner", "source_uris"],
            },
        },
        "humanitarian_notes": {"type": "array", "items": {"type": "string"}},
        "farma_policy_notes": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "scan_id",
        "simulation_id",
        "summary",
        "ranked_recommendations",
        "humanitarian_notes",
        "farma_policy_notes",
        "confidence",
    ],
}
