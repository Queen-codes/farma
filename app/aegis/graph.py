"""AEGIS orchestration entrypoints.

refactor of module which used to contain a legacy LangGraph scan orchestrator. The
scan engine now lives in `app.aegis.scan` (Gemini-native async + grounded tools),
making this file intentionally a thin wrapper that preserves the public API used
by `app/main.py`.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from app.config import AEGIS_FOCUS_STATES, GOOGLE_API_KEY
from app.aegis.scan.runner import run_aegis_scan as run_aegis_scan_engine


async def run_aegis_scan(
    days_back: int = 7,
    force: bool = False,
    states: Optional[list[str]] = None,
    run_id: Optional[str] = None,
    scan_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Run a full AEGIS scan using the canonical scan engine.

    Args:
        days_back: How many days of data to search.
        force: Reserved for legacy checkpointing logic (ignored by the v2 engine).
        states: States to scan (defaults to AEGIS_FOCUS_STATES).
        run_id: Job/run identifier. If not provided, a SCAN-* id is generated.
        scan_id: Optional DB scan row id to link persisted outputs.
    """
    _ = force  # checkpointing is handled at the API layer for demo stability

    engine_run_id = run_id or f"SCAN-{uuid.uuid4().hex[:8].upper()}"
    target_states = states or AEGIS_FOCUS_STATES

    if not GOOGLE_API_KEY:
        return {
            "run_id": engine_run_id,
            "scan_id": scan_id,
            "status": "failed",
            "error": "Missing GOOGLE_API_KEY for AEGIS scan engine",
            "results": [],
            "states_scanned": 0,
            "total_events": 0,
            "total_fatalities": 0,
        }

    return await run_aegis_scan_engine(
        api_key=GOOGLE_API_KEY,
        states=target_states,
        days_back=days_back,
        run_id=engine_run_id,
        scan_id=scan_id,
        emit_job_events=True,
    )
