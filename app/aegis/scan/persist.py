from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, select

from app.aegis.db.connection import async_session
from app.aegis.db.models import (
    AegisScan,
    ConflictEvent,
    LGARiskScore,
    StateIntelligence,
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _risk_level(score: int) -> str:
    if score >= 25:
        return "CRITICAL"
    if score >= 15:
        return "HIGH"
    if score >= 6:
        return "ELEVATED"
    return "LOW"


async def persist_state_intelligence(
    *,
    scan_id: int,
    state_name: str,
    tool_results: Dict[str, Any],
) -> dict:
    """Persist one state's scan outputs immediately.

    Writes:
    - StateIntelligence row (raw JSONB tool outputs)
    - ConflictEvent rows (from tool_results['search_conflict_events'].data.events if present)

    Idempotent for (scan_id, state_name): replaces prior rows.
    """
    conflict = tool_results.get("search_conflict_events") or {}
    displacement = tool_results.get("search_displacement") or {}
    food = tool_results.get("search_food_security") or {}
    economic = tool_results.get("search_economic_indicators") or {}

    conflict_events = (conflict.get("data") or {}).get("events") or []
    conflict_events_count = 0
    total_fatalities = 0
    if isinstance(conflict_events, list):
        conflict_events_count = len(conflict_events)
        for e in conflict_events:
            try:
                total_fatalities += int((e or {}).get("fatalities") or 0)
            except Exception:
                pass

    async with async_session() as session:
        existing = await session.execute(
            select(StateIntelligence).where(
                StateIntelligence.scan_id == scan_id,
                StateIntelligence.state_name == state_name,
            )
        )
        existing_intel = existing.scalar_one_or_none()
        if existing_intel is not None:
            await session.execute(
                delete(ConflictEvent).where(
                    ConflictEvent.state_intel_id == existing_intel.id
                )
            )
            await session.execute(
                delete(StateIntelligence).where(
                    StateIntelligence.id == existing_intel.id
                )
            )
            await session.flush()

        intel = StateIntelligence(
            scan_id=scan_id,
            state_name=state_name,
            collected_at=_utcnow_naive(),
            conflict_raw=conflict or None,
            displacement_raw=displacement or None,
            food_security_raw=food or None,
            economic_raw=economic or None,
            conflict_events_count=conflict_events_count,
            # Best-effort fields; keep defaults if not present
            idp_estimate=(displacement.get("data") or {}).get("idp_estimate"),
            idp_trend=(displacement.get("data") or {}).get("idp_trend") or "unknown",
            food_insecurity_level=(food.get("data") or {}).get("food_insecurity_level")
            or "unknown",
            ipc_phase=(food.get("data") or {}).get("ipc_phase"),
            markets_operational=(economic.get("data") or {}).get("markets_operational")
            or "unknown",
        )
        session.add(intel)
        await session.flush()

        # insert conflict events
        if isinstance(conflict_events, list):
            for ev in conflict_events:
                if not isinstance(ev, dict):
                    continue
                session.add(
                    ConflictEvent(
                        state_intel_id=intel.id,
                        event_date=str(ev.get("date") or ev.get("event_date") or ""),
                        location=str(ev.get("location") or ""),
                        state=state_name,
                        lga=(str(ev.get("lga")) if ev.get("lga") else None),
                        latitude=ev.get("latitude"),
                        longitude=ev.get("longitude"),
                        event_type=str(ev.get("event_type") or "incident"),
                        actors=(str(ev.get("actors")) if ev.get("actors") else None),
                        fatalities=int(ev.get("fatalities") or 0),
                        injuries=int(ev.get("injuries") or 0),
                        abducted=int(ev.get("abducted") or 0),
                        summary=str(ev.get("summary") or ev.get("description") or ""),
                        source=str(ev.get("source") or ""),
                        ingested_at=_utcnow_naive(),
                    )
                )

        await session.commit()

    return {
        "state_name": state_name,
        "conflict_events_count": conflict_events_count,
        "fatalities": total_fatalities,
    }


async def finalize_scan(
    *,
    scan_id: int,
) -> dict:
    """Finalize scan: mark AegisScan completed and store LGA risk scores."""
    async with async_session() as session:
        # Update scan totals
        intel_rows = await session.execute(
            select(StateIntelligence).where(StateIntelligence.scan_id == scan_id)
        )
        intel_list = intel_rows.scalars().all()
        states_scanned = len(intel_list)

        events_rows = await session.execute(
            select(ConflictEvent).where(
                ConflictEvent.state_intel_id.in_([i.id for i in intel_list] or [-1])
            )
        )
        events_list = events_rows.scalars().all()
        total_events = len(events_list)
        total_fatalities = 0
        for ev in events_list:
            try:
                total_fatalities += int(ev.fatalities or 0)
            except Exception:
                pass

        scan = await session.get(AegisScan, scan_id)
        if scan:
            scan.status = "completed"
            scan.states_scanned = states_scanned
            scan.total_events = total_events
            scan.total_fatalities = total_fatalities
            scan.completed_at = _utcnow_naive()

        # Recompute LGA risk scores
        await session.execute(
            delete(LGARiskScore).where(LGARiskScore.scan_id == scan_id)
        )

        grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"event_count": 0, "fatalities": 0}
        )
        for ev in events_list:
            lga = (ev.lga or "").strip()
            state = (ev.state or "").strip()
            if not lga:
                continue
            key = (state, lga)
            grouped[key]["event_count"] += 1
            grouped[key]["fatalities"] += int(ev.fatalities or 0)

        for (state, lga), agg in grouped.items():
            score = int(agg["event_count"]) * 2 + int(agg["fatalities"]) * 3
            session.add(
                LGARiskScore(
                    scan_id=scan_id,
                    lga=lga,
                    state=state,
                    event_count=int(agg["event_count"]),
                    fatalities=int(agg["fatalities"]),
                    risk_score=score,
                    risk_level=_risk_level(score),
                    computed_at=_utcnow_naive(),
                )
            )

        await session.commit()

    return {
        "scan_id": scan_id,
        "states_scanned": states_scanned,
        "total_events": total_events,
        "total_fatalities": total_fatalities,
    }
