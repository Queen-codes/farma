"""AEGIS - Humanitarian/Displacement intelligence for FARMA.

engines/agent orchestration:
- Scan: `app.aegis.scan` (Gemini grounded evidence collection)
- Synthesis: `app.aegis.synthesis`
- Report: `app.aegis.report`

This package exports only stable entrypoints used by the API layer.
"""

from app.config import AEGIS_FOCUS_STATES

from app.aegis.db import (
    init_db,
    close_db,
    async_session,
    AegisScan,
    StateIntelligence,
    ConflictEvent as DBConflictEvent,
    LGARiskScore,
    AegisReport,
)

from app.aegis.graph import run_aegis_scan

__all__ = [
    "AEGIS_FOCUS_STATES",
    "init_db",
    "close_db",
    "async_session",
    "AegisScan",
    "StateIntelligence",
    "DBConflictEvent",
    "LGARiskScore",
    "AegisReport",
    "run_aegis_scan",
]
