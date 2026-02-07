"""Schema contracts for marathon continuity-note generation.

Purpose:
- Define structured continuity note format produced by marathon LLM step.
- Export JSON schema for constrained model generation.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class KeyChange(BaseModel):
    """One notable change between previous and current scan days."""

    change: str
    why_it_matters: str
    source_uris: List[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    """Action recommendation emitted by marathon continuity note."""

    action: str
    owner: str = "FARMA/Partners"
    source_uris: List[str] = Field(default_factory=list)


class ContinuityNote(BaseModel):
    """Structured day-level continuity note persisted by marathon workflow."""

    track_id: str
    day_date: str  # YYYY-MM-DD
    scan_id: int
    prev_scan_id: Optional[int] = None
    summary: str
    key_changes: List[KeyChange] = Field(default_factory=list)
    recommended_actions: List[RecommendedAction] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    # Marathon-specific: self-correction + predictions
    predictions: List[str] = Field(default_factory=list, max_length=4)
    self_corrections: List[str] = Field(default_factory=list, max_length=4)
    actions_taken: List[str] = Field(default_factory=list)
    next_thinking_level: str = "low"

    # Self-narration: explains why the agent made its decisions in plain language
    decision_explanation: str = Field(
        default="",
        description="1-3 sentence explanation of what the agent observed and why it chose these actions. "
        "Written in first person as the agent narrating its own reasoning.",
    )


# Generated from the Pydantic model — single source of truth
CONTINUITY_NOTE_SCHEMA: dict = ContinuityNote.model_json_schema()
