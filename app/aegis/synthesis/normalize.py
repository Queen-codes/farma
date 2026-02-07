"""Normalization layer bridging scan raw payloads to synthesis-ready inputs.

Purpose:
- Resolve schema drift between historical and current scan payload shapes.
- Collect deduped allowed URIs and normalized signals/events per state.

Used by:
- `app.aegis.synthesis.state_worker`.

Assumptions:
- `StateIntelligence` rows exist for requested `(scan_id, state_name)` pairs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlalchemy import select

from app.aegis.db.connection import async_session
from app.aegis.db.models import StateIntelligence


def _dedupe_sources(tool_payload: dict) -> List[dict]:
    """Return deduplicated `{uri,title}` source entries from one tool payload."""
    sources = tool_payload.get("sources") or []
    seen = set()
    out = []
    for s in sources:
        uri = (s or {}).get("uri")
        if not uri:
            continue
        if uri in seen:
            continue
        seen.add(uri)
        out.append({"uri": uri, "title": (s or {}).get("title") or ""})
    return out


def _collect_allowed_uris(tool_payloads: List[dict]) -> List[str]:
    """Collect ordered unique URI whitelist across all tool payloads."""
    allowed: List[str] = []
    seen = set()
    for p in tool_payloads:
        for s in _dedupe_sources(p):
            uri = s["uri"]
            if uri not in seen:
                seen.add(uri)
                allowed.append(uri)
    return allowed


def _conflict_events_from_raw(conflict_raw: dict) -> List[dict]:
    """Extract conflict event list from new or legacy conflict payload formats."""
    # new : conflict_raw["data"]["events"]
    data = conflict_raw.get("data") or {}
    events = data.get("events")
    if isinstance(events, list):
        return [e for e in events if isinstance(e, dict)]
    # fallback (older schema)
    events2 = conflict_raw.get("events")
    if isinstance(events2, list):
        return [e for e in events2 if isinstance(e, dict)]
    return []


async def normalize_state_intel(*, scan_id: int, state_name: str) -> Dict[str, Any]:
    """Fetch scan outputs for one state and normalize into a compact payload.

    Args:
        scan_id: Scan ID to query.
        state_name: State to normalize.

    Returns:
        Dict[str, Any]: Normalized synthesis input payload for one state.

    Raises:
        SQLAlchemyError: Can propagate on DB read failures.

    Side Effects:
        Reads `StateIntelligence` from database.

    Latency:
        DB query latency plus in-memory normalization.
    """
    async with async_session() as session:
        res = await session.execute(
            select(StateIntelligence).where(
                StateIntelligence.scan_id == scan_id,
                StateIntelligence.state_name == state_name,
            )
        )
        intel = res.scalar_one_or_none()
        if not intel:
            return {
                "scan_id": scan_id,
                "state": state_name,
                "error": "missing_state_intelligence",
                "tools": {},
                "events": [],
                "sources": [],
                "allowed_uris": [],
            }

        conflict_raw = intel.conflict_raw or {}
        displacement_raw = intel.displacement_raw or {}
        food_raw = intel.food_security_raw or {}
        econ_raw = intel.economic_raw or {}

        tool_payloads = [conflict_raw, displacement_raw, food_raw, econ_raw]
        allowed_uris = _collect_allowed_uris(tool_payloads)

        # collect deduped sources with titles for display / audit
        sources_map = {}
        for p in tool_payloads:
            for s in _dedupe_sources(p):
                sources_map[s["uri"]] = s
        sources = list(sources_map.values())

        events = _conflict_events_from_raw(conflict_raw)

        tool_errors = {
            "conflict": conflict_raw.get("errors"),
            "displacement": displacement_raw.get("errors"),
            "food_security": food_raw.get("errors"),
            "economic": econ_raw.get("errors"),
        }

        return {
            "scan_id": scan_id,
            "state": state_name,
            "signals": {
                "conflict_events_count": int(intel.conflict_events_count or 0),
                "idp_estimate": intel.idp_estimate,
                "idp_trend": intel.idp_trend or "unknown",
                "food_insecurity_level": intel.food_insecurity_level or "unknown",
                "ipc_phase": intel.ipc_phase,
                "markets_operational": intel.markets_operational or "unknown",
            },
            "tools": {
                "conflict": conflict_raw,
                "displacement": displacement_raw,
                "food_security": food_raw,
                "economic": econ_raw,
            },
            "events": events,
            "sources": sources,
            "allowed_uris": allowed_uris,
            "tool_errors": tool_errors,
        }
