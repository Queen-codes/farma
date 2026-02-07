"""Config for AEGIS scan.

Purpose:
- Re-export scan-related model, concurrency, timeout, and retry settings from
  central application config.

Used by:
- `app.aegis.scan.state_worker` and runner modules to control execution.
- Tool modules to select grounded model and thinking level.

Assumptions:
- Environment variables in `app.config` are preloaded at process startup.
"""

from __future__ import annotations

from app.config import (
    GEMINI_MODEL_PLANNER,
    GEMINI_MODEL_GROUNDED,
    GEMINI_MODEL_SYNTH,
    THINKING_LEVEL,
    MAX_STATE_WORKERS,
    GLOBAL_TOOL_CONCURRENCY,
    PER_STATE_TOOL_CONCURRENCY,
    GEMINI_TIMEOUT_S,
    GEMINI_MAX_RETRIES,
)

__all__ = [
    "GEMINI_MODEL_PLANNER",
    "GEMINI_MODEL_GROUNDED",
    "GEMINI_MODEL_SYNTH",
    "THINKING_LEVEL",
    "MAX_STATE_WORKERS",
    "GLOBAL_TOOL_CONCURRENCY",
    "PER_STATE_TOOL_CONCURRENCY",
    "GEMINI_TIMEOUT_S",
    "GEMINI_MAX_RETRIES",
]
