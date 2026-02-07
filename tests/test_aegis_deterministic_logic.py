"""Deterministic unit tests for core AEGIS synthesis and delta helpers.

This module verifies pure-data helper functions used by the AEGIS pipeline:
- food security priority scoring,
- conflict hotspot extraction,
- rollup delta and escalation detection,
- scenario auto-selection and URI whitelist deduplication.
"""

from __future__ import annotations

from app.aegis.marathon.deltas import (
    build_auto_scenario,
    compute_delta,
    critical_states,
    has_escalation,
    uri_whitelist_from_artifacts,
)
from app.aegis.synthesis.metrics import (
    analyze_safe_routes_from_events,
    calculate_food_security_score_from_signals,
)


def test_calculate_food_security_score_from_signals_levels() -> None:
    """Verify priority scoring increases with severe food-security signals."""
    low = calculate_food_security_score_from_signals(
        {"ipc_phase": 1, "idp_estimate": 1000, "conflict_events_count": 1}
    )
    high = calculate_food_security_score_from_signals(
        {"ipc_phase": 5, "idp_estimate": 1_200_000, "conflict_events_count": 120}
    )
    assert low["priority_level"] in {"LOW", "MEDIUM"}
    assert high["priority_level"] == "CRITICAL"
    assert high["priority_score"] >= low["priority_score"]


def test_analyze_safe_routes_from_events_dedupes_hotspots() -> None:
    """Ensure repeated LGAs are deduplicated in hotspot recommendations."""
    events = [
        {"lga": "Maiduguri", "fatalities": 2},
        {"lga": "Maiduguri", "fatalities": 5},
        {"lga": "Yola South", "fatalities": 1},
    ]
    out = analyze_safe_routes_from_events(events)
    assert out["conflict_hotspots_to_avoid"] == ["Maiduguri", "Yola South"]
    assert "Avoid hotspot LGAs" in out["recommendation"]


def test_compute_delta_and_rank_moves() -> None:
    """Validate rank-move and rollup-summary fields in computed deltas."""
    today_rollup = {
        "overall_summary": "today",
        "rankings": [{"state": "Borno", "rank": 1}, {"state": "Yobe", "rank": 2}],
    }
    prev_rollup = {
        "overall_summary": "prev",
        "rankings": [{"state": "Borno", "rank": 2}, {"state": "Yobe", "rank": 1}],
    }
    today_assessments = [
        {
            "state": "Borno",
            "risk_level": "CRITICAL",
            "metrics": {
                "priority_score": 90,
                "ipc_phase": 5,
                "idp_estimate": 700000,
                "conflict_events_count": 80,
            },
        }
    ]
    prev_assessments = [
        {
            "state": "Borno",
            "risk_level": "ELEVATED",
            "metrics": {
                "priority_score": 45,
                "ipc_phase": 3,
                "idp_estimate": 300000,
                "conflict_events_count": 20,
            },
        }
    ]
    delta = compute_delta(
        today_rollup=today_rollup,
        today_assessments=today_assessments,
        prev_rollup=prev_rollup,
        prev_assessments=prev_assessments,
    )
    assert delta["rollup_summary_today"] == "today"
    assert any(m["state"] == "Borno" and m["rank_prev"] == 2 and m["rank_today"] == 1 for m in delta["rank_moves"])


def test_escalation_and_critical_state_detection() -> None:
    """Confirm escalation and critical-state helpers flag risk jumps."""
    delta = {
        "state_changes": [
            {"state": "Borno", "risk_level_prev": "LOW", "risk_level_today": "CRITICAL", "ipc_phase_today": 5}
        ]
    }
    assert has_escalation(delta) is True
    assert critical_states(delta) == ["Borno"]


def test_build_auto_scenario_prefers_flood_when_idp_jump_dominates() -> None:
    """Ensure flood scenario is selected when displacement spike dominates."""
    delta = {
        "state_changes": [
            {
                "state": "Yobe",
                "risk_level_prev": "LOW",
                "risk_level_today": "HIGH",
                "conflict_events_prev": 10,
                "conflict_events_today": 11,
                "idp_estimate_prev": 10000,
                "idp_estimate_today": 900000,
            }
        ]
    }
    scenario = build_auto_scenario(delta)
    assert scenario["geo_scope"]["states"] == ["Yobe"]
    assert scenario["crisis_type"] == "flood"
    assert scenario["intensity"] >= 1.0


def test_uri_whitelist_from_artifacts_dedupes() -> None:
    """Check URI whitelist generation deduplicates across all artifacts."""
    rollup = {
        "rankings": [{"source_uris": ["u1", "u2"]}],
        "allocations": [{"source_uris": ["u2", "u3"]}],
    }
    assessments = [
        {"key_findings": [{"source_uris": ["u3", "u4"]}]},
        {"key_findings": [{"source_uris": ["u1"]}]},
    ]
    out = uri_whitelist_from_artifacts(rollup=rollup, assessments=assessments)
    assert out == ["u1", "u2", "u3", "u4"]
