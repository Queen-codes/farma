from __future__ import annotations

from typing import Any, Dict, List


def calculate_food_security_score_from_signals(signals: dict) -> dict:
    """Deterministic score for prioritization

    This is made to be intentionally simple and stable for a demo/prototype. It can be and will be replaced with a
    more sophisticated model later.
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
    """Deterministic safe-route heuristics from conflict events list."""
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
