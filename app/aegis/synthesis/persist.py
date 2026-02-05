from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.aegis.db.connection import async_session
from app.aegis.db.models import AegisScan, StateIntelligence


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def persist_state_assessment(
    *,
    scan_id: int,
    state_name: str,
    assessment_json: Dict[str, Any],
    synthesis_version: str,
) -> None:
    async with async_session() as session:
        res = await session.execute(
            select(StateIntelligence).where(
                StateIntelligence.scan_id == scan_id,
                StateIntelligence.state_name == state_name,
            )
        )
        intel = res.scalar_one_or_none()
        if intel is None:
            # Create a minimal row if missing (shouldn't happen if scan persisted correctly)
            intel = StateIntelligence(scan_id=scan_id, state_name=state_name)
            session.add(intel)
            await session.flush()

        intel.assessment_json = assessment_json
        intel.synthesized_at = _utcnow_naive()
        intel.synthesis_version = synthesis_version
        await session.commit()


async def persist_rollup(
    *,
    scan_id: int,
    rollup_json: Dict[str, Any],
) -> None:
    async with async_session() as session:
        scan = await session.get(AegisScan, scan_id)
        if scan is None:
            return
        scan.rollup_json = rollup_json
        scan.rollup_at = _utcnow_naive()
        await session.commit()

