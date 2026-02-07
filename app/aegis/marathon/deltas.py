"""Deterministic delta and escalation helpers for marathon continuity workflow.

Purpose:
- Compare current and previous synthesis artifacts.
- Derive state/ranking changes and escalation signals.
- Build autonomous simulation scenarios from worst detected changes.

Used by:
- `app.aegis.marathon.nodes`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _by_state(assessments: List[dict]) -> Dict[str, dict]:
    """Index assessment list by normalized state name."""
    out: Dict[str, dict] = {}
    for a in assessments:
        s = (a.get("state") or "").strip()
        if s:
            out[s] = a
    return out


def _risk_level(a: Optional[dict]) -> str:
    """Return uppercase risk level from assessment payload."""
    if not a:
        return "UNKNOWN"
    return (a.get("risk_level") or "UNKNOWN").upper()


def compute_delta(
    *,
    today_rollup: dict,
    today_assessments: List[dict],
    prev_rollup: Optional[dict],
    prev_assessments: List[dict],
) -> Dict[str, Any]:
    """Compute deterministic day-over-day delta payload.

    Args:
        today_rollup: Current scan rollup JSON.
        today_assessments: Current state assessments.
        prev_rollup: Previous scan rollup JSON.
        prev_assessments: Previous state assessments.

    Returns:
        Dict[str, Any]: Delta object used by marathon LLM/action nodes.
    """
    today_by_state = _by_state(today_assessments)
    prev_by_state = _by_state(prev_assessments)

    states = sorted(set(today_by_state.keys()) | set(prev_by_state.keys()))
    state_changes: List[dict] = []
    for s in states:
        t = today_by_state.get(s)
        p = prev_by_state.get(s)

        t_metrics = (t or {}).get("metrics") or {}
        p_metrics = (p or {}).get("metrics") or {}

        def _int(v: Any) -> int:
            """Coerce value to int with zero fallback."""
            try:
                return int(v)
            except Exception:
                return 0

        change = {
            "state": s,
            "risk_level_prev": _risk_level(p),
            "risk_level_today": _risk_level(t),
            "priority_score_prev": _int(p_metrics.get("priority_score")),
            "priority_score_today": _int(t_metrics.get("priority_score")),
            "ipc_phase_prev": _int(p_metrics.get("ipc_phase")),
            "ipc_phase_today": _int(t_metrics.get("ipc_phase")),
            "idp_estimate_prev": _int(p_metrics.get("idp_estimate")),
            "idp_estimate_today": _int(t_metrics.get("idp_estimate")),
            "conflict_events_prev": _int(p_metrics.get("conflict_events_count")),
            "conflict_events_today": _int(t_metrics.get("conflict_events_count")),
        }
        state_changes.append(change)

    rankings_today = today_rollup.get("rankings") or []
    rankings_prev = (prev_rollup or {}).get("rankings") or []

    rank_today = {
        r.get("state"): r.get("rank") for r in rankings_today if r.get("state")
    }
    rank_prev = {r.get("state"): r.get("rank") for r in rankings_prev if r.get("state")}

    rank_moves: List[dict] = []
    for s in sorted(set(rank_today.keys()) | set(rank_prev.keys())):
        rt = rank_today.get(s)
        rp = rank_prev.get(s)
        if rt is None and rp is None:
            continue
        rank_moves.append({"state": s, "rank_prev": rp, "rank_today": rt})

    return {
        "states": states,
        "state_changes": state_changes,
        "rank_moves": rank_moves,
        "rollup_summary_today": today_rollup.get("overall_summary") or "",
        "rollup_summary_prev": (prev_rollup or {}).get("overall_summary") or "",
    }


def uri_whitelist_from_artifacts(*, rollup: dict, assessments: List[dict]) -> List[str]:
    """Build ordered unique URI whitelist from rollup and assessment findings."""
    uris: List[str] = []

    def add(u: Any) -> None:
        """Append normalized URI if not seen yet."""
        if not u:
            return
        s = str(u).strip()
        if s and s not in uris:
            uris.append(s)

    for r in rollup.get("rankings") or []:
        for u in r.get("source_uris") or []:
            add(u)
    for a in rollup.get("allocations") or []:
        for u in a.get("source_uris") or []:
            add(u)

    for ass in assessments:
        for f in ass.get("key_findings") or []:
            for u in f.get("source_uris") or []:
                add(u)

    return uris


# escalation helpers to decide what action to take

_RISK_LEVELS = ["UNKNOWN", "LOW", "MEDIUM", "ELEVATED", "HIGH", "CRITICAL"]


def has_escalation(delta: Dict[str, Any], *, threshold: int = 2) -> bool:
    """True if any state's risk jumped by threshold levels."""
    for ch in delta.get("state_changes") or []:
        prev = ch.get("risk_level_prev", "UNKNOWN")
        today = ch.get("risk_level_today", "UNKNOWN")
        pi = _RISK_LEVELS.index(prev) if prev in _RISK_LEVELS else 0
        ti = _RISK_LEVELS.index(today) if today in _RISK_LEVELS else 0
        if ti - pi >= threshold:
            return True
        if ch.get("ipc_phase_today", 0) >= 4:
            return True
    return False


def critical_states(delta: Dict[str, Any]) -> List[str]:
    """Return state names that are now CRITICAL."""
    out: List[str] = []
    for ch in delta.get("state_changes") or []:
        if ch.get("risk_level_today", "").upper() == "CRITICAL":
            out.append(ch["state"])
    return out


# autonomous trigger of a simulation
def build_auto_scenario(delta: Dict[str, Any]) -> Dict[str, Any]:
    """Build a simulation scenario from the delta's worst escalation."""
    worst_state: Optional[str] = None
    worst_jump = 0
    worst_type = "conflict"

    for ch in delta.get("state_changes") or []:
        prev = ch.get("risk_level_prev", "UNKNOWN")
        today = ch.get("risk_level_today", "UNKNOWN")
        pi = _RISK_LEVELS.index(prev) if prev in _RISK_LEVELS else 0
        ti = _RISK_LEVELS.index(today) if today in _RISK_LEVELS else 0
        jump = ti - pi
        if jump > worst_jump:
            worst_jump = jump
            worst_state = ch["state"]
            # infer crisis type from dominant signal
            conflict_delta = ch.get("conflict_events_today", 0) - ch.get(
                "conflict_events_prev", 0
            )
            idp_delta = ch.get("idp_estimate_today", 0) - ch.get("idp_estimate_prev", 0)
            if idp_delta > conflict_delta * 500:
                worst_type = "flood"
            else:
                worst_type = "conflict"

    # intensity from risk level jump magnitude
    intensity = 1.0 + (worst_jump * 0.3)

    return {
        "crisis_type": worst_type,
        "intensity": round(min(intensity, 2.5), 1),
        "duration_days": 7,
        "geo_scope": {"states": [worst_state] if worst_state else []},
    }
