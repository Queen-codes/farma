"""Query helpers for building AEGIS scan status payloads.

Key responsibilities:
- Compute state-level priority labels from intelligence rows.
- Assemble scan summaries, conflict events, and LGA risk data from the DB.
- Provide fallback in-memory LGA aggregation while scan finalization is pending.

Used by:
- `app.api.routes.aegis.get_aegis_dashboard` for priority scoring.
- `app.api.routes.aegis.get_scan_status` for status payload enrichment.

Assumptions:
- AEGIS database models/tables are initialized and reachable.
- `scan_id` maps to records in `state_intelligence`, `conflict_event`, and
  optionally `lga_risk_score`.
"""

from __future__ import annotations

from typing import Any


def priority_from_intel(intel: Any) -> tuple[str, int]:
    """Calculate a priority label and score for a state intelligence record.

    Args:
        intel: Object exposing attributes used for scoring
            (`ipc_phase`, `idp_estimate`, `conflict_events_count`).

    Returns:
        tuple[str, int]: Priority level (`LOW`, `ELEVATED`, `HIGH`, `CRITICAL`)
        and numeric score in the range 0-100.

    Raises:
        ValueError: If a non-integer value cannot be coerced when parsing
            ``ipc_phase``.
        TypeError: If numeric comparisons fail on malformed attribute values.

    Side Effects:
        None.

    Latency:
        Constant-time in-memory scoring.
    """
    score = 0
    ipc = getattr(intel, "ipc_phase", None) or 0
    score += min(int(ipc) * 15, 75)

    idp = getattr(intel, "idp_estimate", None) or 0
    if idp > 1_000_000:
        score += 35
    elif idp > 500_000:
        score += 25
    elif idp > 200_000:
        score += 15

    conflicts = getattr(intel, "conflict_events_count", None) or 0
    if conflicts > 100:
        score += 25
    elif conflicts > 50:
        score += 15
    elif conflicts > 20:
        score += 8

    score = min(score, 100)
    if score >= 80:
        return "CRITICAL", score
    if score >= 60:
        return "HIGH", score
    if score >= 40:
        return "ELEVATED", score
    return "LOW", score


async def summaries_for_scan(
    scan_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build API-ready summary payloads for a completed or running scan.

    Args:
        scan_id: Primary key of the `AegisScan` database row.

    Returns:
        tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        A tuple of `(state_summaries, conflict_events, lga_risk_entries)`.

    Raises:
        SQLAlchemyError: Can propagate from DB reads (imported lazily at runtime).
        ValueError: Can propagate if stored numeric fields cannot be coerced.

    Side Effects:
        Performs multiple database read queries.

    Latency:
        Potentially slow for large scans due to iterative state/event queries and
        optional in-memory LGA aggregation.
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import StateIntelligence, ConflictEvent, LGARiskScore
    from sqlalchemy import select, desc

    summaries: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    lga_risk: list[dict[str, Any]] = []
    async with get_async_session() as session:
        result = await session.execute(
            select(StateIntelligence).where(StateIntelligence.scan_id == scan_id)
        )
        state_rows = result.scalars().all()
        for intel in state_rows:
            level, score = priority_from_intel(intel)
            summaries.append(
                {
                    "state_name": intel.state_name,
                    "conflict_events": intel.conflict_events_count,
                    "idp_estimate": intel.idp_estimate,
                    "idp_trend": intel.idp_trend,
                    "food_insecurity_level": intel.food_insecurity_level,
                    "ipc_phase": intel.ipc_phase,
                    "markets_operational": intel.markets_operational,
                    "priority_level": level,
                    "priority_score": score,
                }
            )

            conflict_result = await session.execute(
                select(ConflictEvent)
                .where(ConflictEvent.state_intel_id == intel.id)
                .limit(200)
            )
            for event in conflict_result.scalars().all():
                events.append(
                    {
                        "state": event.state,
                        "lga": event.lga,
                        "event_type": event.event_type,
                        "fatalities": event.fatalities,
                        "date": event.event_date,
                        "summary": event.summary,
                        "location": event.location,
                        "lat": event.latitude,
                        "lon": event.longitude,
                    }
                )

        # stored LGA risk scores
        try:
            lga_result = await session.execute(
                select(LGARiskScore)
                .where(LGARiskScore.scan_id == scan_id)
                .order_by(desc(LGARiskScore.risk_score))
            )
            lga_risk = [
                {
                    "lga": r.lga,
                    "state": r.state,
                    "event_count": r.event_count,
                    "fatalities": r.fatalities,
                    "risk_score": r.risk_score,
                    "risk_level": r.risk_level,
                }
                for r in lga_result.scalars().all()
            ]
            # while a scan is still running, finalize may not have computed/stored LGA
            # scores yet. This fallbacks to on-the-fly aggregation so maps can update
            # incrementally as states finish.
            if not lga_risk and events:
                agg: dict[tuple[str, str], dict] = {}
                for e in events:
                    lga = (e.get("lga") or "").strip()
                    state = (e.get("state") or "").strip()
                    if not lga or not state:
                        continue
                    key = (state, lga)
                    entry = agg.setdefault(
                        key,
                        {"lga": lga, "state": state, "event_count": 0, "fatalities": 0},
                    )
                    entry["event_count"] += 1
                    entry["fatalities"] += int(e.get("fatalities") or 0)

                lga_risk = []
                for entry in agg.values():
                    score = entry["event_count"] * 2 + entry["fatalities"] * 3
                    if score >= 25:
                        level = "CRITICAL"
                    elif score >= 15:
                        level = "HIGH"
                    elif score >= 6:
                        level = "ELEVATED"
                    else:
                        level = "LOW"
                    lga_risk.append({**entry, "risk_score": score, "risk_level": level})
                lga_risk.sort(key=lambda x: x.get("risk_score", 0), reverse=True)
        except Exception:
            lga_risk = []

    return summaries, events, lga_risk
