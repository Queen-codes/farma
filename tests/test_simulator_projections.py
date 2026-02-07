"""Tests for scenario projection outputs in the AEGIS simulator module.

Focus:
- URI whitelist deduplication,
- crisis-type specific policy actions,
- humanitarian IDP delta clamping behavior.
"""

from __future__ import annotations

from app.aegis.simulator.projections import build_uri_whitelist, compute_projections


def _assessments_fixture() -> dict:
    """Return deterministic assessment map reused across projection tests."""
    return {
        "Borno": {
            "risk_level": "HIGH",
            "metrics": {
                "idp_estimate": 500000,
                "conflict_events_count": 50,
                "markets_operational": "partially",
            },
            "key_findings": [{"source_uris": ["u1", "u2"]}],
        },
        "Yobe": {
            "risk_level": "CRITICAL",
            "metrics": {
                "idp_estimate": 300000,
                "conflict_events_count": 70,
                "markets_operational": "closed",
            },
            "key_findings": [{"source_uris": ["u2", "u3"]}],
        },
    }


def test_build_uri_whitelist_dedupes_rollup_and_assessments() -> None:
    """Ensure URI whitelist merges and deduplicates rollup plus assessments."""
    rollup = {
        "rankings": [{"source_uris": ["u1", "u4"]}],
        "allocations": [{"source_uris": ["u4", "u5"]}],
    }
    out = build_uri_whitelist(rollup_json=rollup, assessments_by_state=_assessments_fixture())
    assert out == ["u1", "u4", "u5", "u2", "u3"]


def test_compute_projections_conflict_high_intensity_recommends_pause() -> None:
    """Verify high-intensity conflict scenario emits loan pause actions."""
    projections = compute_projections(
        scan_id=101,
        scenario={"crisis_type": "conflict", "intensity": 2.0, "duration_days": 14, "geo_scope": {"states": ["Borno", "Yobe"]}},
        rollup_json={},
        assessments_by_state=_assessments_fixture(),
    )
    assert projections["scan_id"] == 101
    assert projections["humanitarian"]["route_risk_score"] >= 0.0
    actions = projections["financial"]["loan_policy_actions"]
    assert any(a["action"] == "PAUSE_NEW_LOANS" for a in actions)
    assert any(a["action"] == "EXTEND_GRACE_PERIOD" for a in actions)


def test_compute_projections_flood_includes_insurance_review() -> None:
    """Verify flood scenario includes insurance review policy action."""
    projections = compute_projections(
        scan_id=202,
        scenario={"crisis_type": "flood", "intensity": 1.6, "duration_days": 21},
        rollup_json={},
        assessments_by_state=_assessments_fixture(),
    )
    actions = projections["financial"]["loan_policy_actions"]
    assert any(a["action"] == "ACTIVATE_INSURANCE_REVIEW" for a in actions)


def test_compute_projections_low_intensity_clamps_negative_idp_delta() -> None:
    """Ensure low-intensity scenarios clamp negative IDP deltas to safe floor."""
    projections = compute_projections(
        scan_id=303,
        scenario={"crisis_type": "market_shock", "intensity": 0.2, "duration_days": 30},
        rollup_json={},
        assessments_by_state=_assessments_fixture(),
    )
    baseline = projections["humanitarian"]["baseline_idp"]
    delta = projections["humanitarian"]["idp_delta"]
    assert delta >= int(-0.5 * baseline)
