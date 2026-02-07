"""Parser unit tests for AEGIS scan tool text-normalization helpers.

These tests validate deterministic parsing for conflict event lines and key/value
extractors used in displacement, food security, and economic scan tools.
"""

from __future__ import annotations

from app.aegis.scan.tools.conflict import _parse_pipe_events
from app.aegis.scan.tools.displacement import _parse_kv as displacement_parse_kv
from app.aegis.scan.tools.economic import _parse_kv as economic_parse_kv
from app.aegis.scan.tools.food_security import _parse_kv as food_parse_kv


def test_parse_pipe_events_extracts_structured_conflict_events() -> None:
    """Ensure valid pipe-delimited lines become structured conflict events."""
    text = """
    - 2026-01-01 | Maiduguri | Monday Market | Attack | 5 fatalities | Armed clash reported
    - 2026-01-02 | Unknown | Highway | Ambush | 0 | Convoy delayed
    """
    events = _parse_pipe_events(text, state="Borno")
    assert len(events) == 2
    assert events[0]["lga"] == "Maiduguri"
    assert events[0]["fatalities"] == 5
    assert events[1]["lga"] == "Unknown"


def test_parse_pipe_events_skips_malformed_lines() -> None:
    """Ensure malformed conflict lines are ignored instead of crashing parser."""
    text = "- not enough fields"
    assert _parse_pipe_events(text, state="Yobe") == []


def test_displacement_kv_parser_normalizes_keys() -> None:
    """Validate displacement parser preserves normalized uppercase keys."""
    parsed = displacement_parse_kv(
        "IDP_ESTIMATE: 120,000\nIDP_TREND: increasing\nFLEEING_TO_LGAS: Damaturu"
    )
    assert parsed["IDP_ESTIMATE"] == "120,000"
    assert parsed["IDP_TREND"] == "increasing"
    assert parsed["FLEEING_TO_LGAS"] == "Damaturu"


def test_food_kv_parser_normalizes_keys() -> None:
    """Validate food-security parser emits expected normalized key/value pairs."""
    parsed = food_parse_kv("IPC_PHASE: 4\nFOOD_INSECURITY_LEVEL: emergency")
    assert parsed["IPC_PHASE"] == "4"
    assert parsed["FOOD_INSECURITY_LEVEL"] == "emergency"


def test_economic_kv_parser_normalizes_keys() -> None:
    """Validate economic parser normalizes key names consistently."""
    parsed = economic_parse_kv("MARKETS_OPERATIONAL: partially")
    assert parsed["MARKETS_OPERATIONAL"] == "partially"
