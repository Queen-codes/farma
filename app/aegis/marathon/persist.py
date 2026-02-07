"""Persistence helpers for marathon day continuity artifacts.

Purpose:
- Upsert per-day marathon records keyed by `(track_id, day_date)`.
- Retrieve latest day context for continuity replay.

Used by:
- `app.aegis.marathon.nodes.persist_marathon_day_node`.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisMarathonDay
from app.aegis.marathon.llm import _safe_dump


def _utcnow_naive() -> datetime:
    """Return current UTC time as naive datetime for DB timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_b64_signature(sig: Any) -> str | None:
    """Normalize thought signature to base64 string for storage."""
    if sig is None:
        return None
    if isinstance(sig, memoryview):
        return base64.b64encode(sig.tobytes()).decode("ascii")
    if isinstance(sig, (bytes, bytearray)):
        return base64.b64encode(bytes(sig)).decode("ascii")
    if isinstance(sig, dict) and sig.get("__type__") == "bytes" and "__b64__" in sig:
        return str(sig["__b64__"])
    if isinstance(sig, str):
        return sig
    return str(sig)


async def upsert_marathon_day(
    *,
    track_id: str,
    day_date: str,
    scan_id: int,
    prev_scan_id: Optional[int],
    delta_json: Dict[str, Any],
    continuity_note_json: Optional[Dict[str, Any]],
    thought_signature: Optional[str],
    prev_thought_signature: Optional[str],
    model: str,
    thinking_level: str,
    schema_mode: str,
    stored_model_content_json: Optional[dict],
    actions_taken: Optional[List[str]] = None,
    simulation_triggered: Optional[str] = None,
    report_triggered: Optional[str] = None,
    status: str = "completed",
    error: Optional[str] = None,
) -> int:
    """Insert or update one marathon day row with continuity artifacts.

    Args:
        track_id: Continuity track identifier.
        day_date: Day key (`YYYY-MM-DD`).
        scan_id: Current scan ID.
        prev_scan_id: Previous scan ID if available.
        delta_json: Deterministic delta payload.
        continuity_note_json: LLM-generated continuity note.
        thought_signature: Current thought signature.
        prev_thought_signature: Previous day's signature.
        model: LLM model identifier.
        thinking_level: Effective thinking level used.
        schema_mode: Structured-output mode used.
        stored_model_content_json: Serialized model content for replay.
        actions_taken: Marathon autonomous actions taken.
        simulation_triggered: Triggered simulation ID if any.
        report_triggered: Triggered report ID if any.
        status: Row status string.
        error: Optional error text.

    Returns:
        int: Database row primary key.

    Raises:
        SQLAlchemyError: Can propagate on DB write failures.
    """
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisMarathonDay).where(
                AegisMarathonDay.track_id == track_id,
                AegisMarathonDay.day_date == day_date,
            )
        )
        row = res.scalar_one_or_none()
        if not row:
            row = AegisMarathonDay(
                track_id=track_id,
                day_date=day_date,
                scan_id=scan_id,
                prev_scan_id=prev_scan_id,
                created_at=_utcnow_naive(),
            )
            session.add(row)
            await session.flush()

        row.scan_id = scan_id
        row.prev_scan_id = prev_scan_id
        row.delta_json = _safe_dump(delta_json)
        row.continuity_note_json = _safe_dump(continuity_note_json)
        row.thought_signature = _to_b64_signature(thought_signature)
        row.prev_thought_signature = _to_b64_signature(prev_thought_signature)
        row.model = model
        row.thinking_level = thinking_level
        row.schema_mode = schema_mode
        row.stored_model_content_json = _safe_dump(stored_model_content_json)
        row.status = status
        row.error = error

        # New marathon agent fields
        row.actions_taken = actions_taken or []
        row.simulation_triggered = simulation_triggered
        row.report_triggered = report_triggered

        return int(row.id)


async def get_latest_day(*, track_id: str) -> Optional[dict]:
    """Fetch latest persisted marathon day record for a track."""
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisMarathonDay)
            .where(AegisMarathonDay.track_id == track_id)
            .order_by(AegisMarathonDay.day_date.desc())
            .limit(1)
        )
        row = res.scalar_one_or_none()
        if not row:
            return None
        return {
            "id": row.id,
            "track_id": row.track_id,
            "day_date": str(row.day_date),
            "scan_id": row.scan_id,
            "prev_scan_id": row.prev_scan_id,
            "delta_json": row.delta_json,
            "continuity_note_json": row.continuity_note_json,
            "thought_signature": row.thought_signature,
            "stored_model_content_json": row.stored_model_content_json,
            "actions_taken": row.actions_taken,
            "simulation_triggered": row.simulation_triggered,
            "report_triggered": row.report_triggered,
        }
