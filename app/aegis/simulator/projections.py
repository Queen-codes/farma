"""Deterministic projection engine for AEGIS crisis simulations.

Purpose:
- Build URI whitelist from synthesis artifacts for downstream policy-brief LLM.
- Apply scenario multipliers to baseline humanitarian and portfolio metrics.
- Produce reproducible, non-LLM projection outputs.

Used by:
- `app.aegis.simulator.nodes.compute_projections_node`.

Assumptions:
- Assessments contain metrics populated by synthesis stage.
- Scenario payload uses expected keys (`crisis_type`, `intensity`, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_uri_whitelist(
    *, rollup_json: dict, assessments_by_state: Dict[str, dict]
) -> List[str]:
    """Collect unique citation URIs from rollup + state assessments."""
    uris: List[str] = []

    def add(u: Any) -> None:
        """Append normalized URI while preserving first-seen ordering."""
        if not u:
            return
        s = str(u).strip()
        if s and s not in uris:
            uris.append(s)

    for r in rollup_json.get("rankings") or []:
        for u in r.get("source_uris") or []:
            add(u)
    for a in rollup_json.get("allocations") or []:
        for u in a.get("source_uris") or []:
            add(u)

    for ass in assessments_by_state.values():
        for f in ass.get("key_findings") or []:
            for u in f.get("source_uris") or []:
                add(u)

    return uris


def _int(v: Any) -> int:
    """Coerce value to int with zero fallback."""
    try:
        return int(v)
    except Exception:
        return 0


def _float(v: Any) -> float:
    """Coerce value to float with zero fallback."""
    try:
        return float(v)
    except Exception:
        return 0.0


def _risk_level(ass: dict) -> str:
    """Extract uppercase risk level from assessment payload."""
    return str(ass.get("risk_level") or "UNKNOWN").upper()


def compute_projections(
    *,
    scan_id: int,
    scenario: dict,
    rollup_json: dict,
    assessments_by_state: Dict[str, dict],
) -> Dict[str, Any]:
    """Deterministic counterfactual projection engine.

    Args:
        scan_id: Source scan ID.
        scenario: Scenario modifiers and geo scope.
        rollup_json: Scan-level rollup payload.
        assessments_by_state: State assessment map.

    Returns:
        Dict[str, Any]: Deterministic humanitarian + financial projection bundle.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Linear in number of scoped states.
    """
    crisis_type = str(scenario.get("crisis_type") or "conflict")
    intensity = max(0.1, float(scenario.get("intensity") or 1.0))
    duration_days = max(1, int(scenario.get("duration_days") or 7))

    geo_scope = scenario.get("geo_scope") or {}
    states_scope = geo_scope.get("states") or list(assessments_by_state.keys())
    states_scope = [s for s in states_scope if s in assessments_by_state]

    # Baseline aggregates for selected states.
    baseline_idp = 0
    baseline_conflict_events = 0
    baseline_high_risk_states = 0
    baseline_markets_disrupted = 0

    for st in states_scope:
        ass = assessments_by_state.get(st) or {}
        metrics = ass.get("metrics") or {}
        baseline_idp += _int(metrics.get("idp_estimate"))
        baseline_conflict_events += _int(metrics.get("conflict_events_count"))
        if _risk_level(ass) in {"HIGH", "CRITICAL"}:
            baseline_high_risk_states += 1
        if str(metrics.get("markets_operational") or "").lower() in {
            "partially",
            "closed",
        }:
            baseline_markets_disrupted += 1

    # Multipliers by crisis type deterministic; scenario may override.
    type_weights = {
        "conflict": {"idp_factor": 0.12, "market_factor": 0.10, "route_factor": 0.15},
        "flood": {"idp_factor": 0.08, "market_factor": 0.06, "route_factor": 0.12},
        "market_shock": {
            "idp_factor": 0.03,
            "market_factor": 0.18,
            "route_factor": 0.04,
        },
        "epidemic": {"idp_factor": 0.02, "market_factor": 0.08, "route_factor": 0.06},
    }
    w = type_weights.get(crisis_type, type_weights["conflict"])

    # Optional driver multipliers.
    market_mult = _float(scenario.get("market_disruption_multiplier") or 1.0)
    route_mult = _float(scenario.get("route_safety_multiplier") or 1.0)

    # IDP delta: proportional to baseline IDPs and intensity above 1, scaled by duration.
    # If intensity <1, treat as mild improvement (negative delta) but clamp to -50% baseline.
    intensity_delta = intensity - 1.0
    duration_scale = min(2.0, duration_days / 30.0)  # capped at 2x for stability

    idp_delta = int(baseline_idp * w["idp_factor"] * intensity_delta * duration_scale)
    idp_delta = max(int(-0.5 * baseline_idp), min(idp_delta, int(2.0 * baseline_idp)))

    # Food need (MT): simple conversion from additional people needing assistance.
    # Assume 0.5 MT per person-month equivalent (very rough, deterministic).
    food_mt = max(0, int((max(0, idp_delta) / 1000.0) * 15 * duration_scale))

    # Funding gap: 1 MT ≈ $700 for procurement/logistics for demo constant.
    funding_gap_usd = int(food_mt * 700)

    # Route risk and no-go flags: based on high-risk states + intensity.
    route_risk_score = min(
        1.0,
        (baseline_high_risk_states / max(1, len(states_scope)))
        * w["route_factor"]
        * max(0.8, intensity)
        * route_mult,
    )
    no_go = route_risk_score >= 0.18 or intensity >= 1.8

    # Financial: portfolio risk delta anchored to market disruption + no-go.
    portfolio_risk_delta = min(
        1.0,
        (baseline_markets_disrupted / max(1, len(states_scope)))
        * w["market_factor"]
        * max(0.8, intensity)
        * market_mult
        + (0.25 if no_go else 0.0),
    )

    loan_policy_actions: List[dict] = []
    if no_go or intensity >= 1.8:
        loan_policy_actions.append(
            {
                "action": "PAUSE_NEW_LOANS",
                "scope_states": states_scope,
                "reason": "Projected route/no-go constraints raise operational and repayment risk.",
                "threshold_trigger": {"no_go": bool(no_go), "intensity": intensity},
            }
        )
    if portfolio_risk_delta >= 0.25:
        loan_policy_actions.append(
            {
                "action": "EXTEND_GRACE_PERIOD",
                "scope_states": states_scope,
                "reason": "High disruption risk; extend grace to prevent default due to displacement/market shocks.",
                "grace_days": 90,
            }
        )
    if crisis_type in {"flood", "epidemic"}:
        loan_policy_actions.append(
            {
                "action": "ACTIVATE_INSURANCE_REVIEW",
                "scope_states": states_scope,
                "reason": f"Crisis type={crisis_type} increases covariate risk; flag portfolio for insurance review.",
            }
        )

    return {
        "scan_id": scan_id,
        "scenario": {
            "crisis_type": crisis_type,
            "intensity": intensity,
            "duration_days": duration_days,
            "geo_scope": {"states": states_scope},
            "market_disruption_multiplier": market_mult,
            "route_safety_multiplier": route_mult,
        },
        "humanitarian": {
            "baseline_idp": baseline_idp,
            "idp_delta": idp_delta,
            "food_mt": food_mt,
            "funding_gap_usd": funding_gap_usd,
            "route_risk_score": route_risk_score,
            "no_go": bool(no_go),
        },
        "financial": {
            "portfolio_risk_delta": portfolio_risk_delta,
            "loan_policy_actions": loan_policy_actions,
        },
    }
