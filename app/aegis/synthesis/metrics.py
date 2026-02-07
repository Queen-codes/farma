"""Deterministic metric helpers used before synthesis LLM calls.

Purpose:
- Compute stable priority scoring from normalized scan signals.
- Derive route-risk hints from conflict event lists.

Used by:
- `app.aegis.synthesis.state_worker`.

Assumptions:
- Inputs are partially normalized dictionaries from `normalize_state_intel`.
"""

from __future__ import annotations

from typing import Any, Dict, List


def calculate_food_security_score_from_signals(signals: dict) -> dict:
    """Compute deterministic priority score/level from core state signals.

    Args:
        signals: Dict containing fields like `ipc_phase`, `idp_estimate`,
            and `conflict_events_count`.

    Returns:
        dict: Priority payload with `priority_score` and `priority_level`.

    Raises:
        Does not raise intentionally; malformed values default to zero coercions.

    Side Effects:
        None.

    Latency:
        Constant-time arithmetic.
    """
    ipc = int(signals.get("ipc_phase") or 0)
    idp = int(signals.get("idp_estimate") or 0)
    conflicts = int(signals.get("conflict_events_count") or 0)

    score = 0
    score += min(ipc * 15, 75)
    if idp > 1_000_000:
        score += 35
    elif idp > 500_000:
        score += 25
    elif idp > 200_000:
        score += 15
    elif idp > 50_000:
        score += 8

    if conflicts > 50:
        score += 15
    elif conflicts > 20:
        score += 8
    elif conflicts > 5:
        score += 4

    score = min(score, 100)
    if score >= 80:
        level = "CRITICAL"
    elif score >= 60:
        level = "HIGH"
    elif score >= 40:
        level = "ELEVATED"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {"priority_score": int(score), "priority_level": level}


def analyze_safe_routes_from_events(events: List[dict]) -> dict:
    """Derive hotspot and route recommendation hints from conflict events.

    Args:
        events: Conflict event dictionaries containing LGA and fatalities fields.

    Returns:
        dict: Route metadata with hotspot list and recommendation text.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Linear in number of events.
    """
    hotspots = []
    for e in events:
        lga = (e.get("lga") or "").strip()
        fat = int(e.get("fatalities") or 0)
        if lga and fat > 0:
            hotspots.append(lga)
    hotspots = sorted(set(hotspots))
    return {
        "conflict_hotspots_to_avoid": hotspots[:12],
        "recommendation": (
            "Avoid hotspot LGAs; use staged delivery via calmer corridors."
            if hotspots
            else "No clear hotspots detected from recent events; use standard precautions."
        ),
    }
