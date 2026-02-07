"""Tests for report cache key stability and report-data aggregation helpers.

This module validates:
- cache filename normalization behavior,
- infographic cache write/read/exists flow,
- URI whitelist and totals aggregation in report payloads.
"""

from __future__ import annotations

from pathlib import Path

from app.aegis.report.cache import CacheKey, InfographicCache
from app.aegis.report.report_data import ReportData


def test_cache_key_filename_sanitizes_aspect_ratio() -> None:
    """Ensure cache filenames replace `:` in aspect ratio with safe `x`."""
    key = CacheKey(
        scan_id=7,
        infographic_type="risk_heatmap",
        prompt_version="v2",
        aspect_ratio="16:9",
        image_size="4K",
        payload_hash="abcdef1234567890deadbeef",
    )
    name = key.filename()
    assert "16x9" in name
    assert name.endswith(".png")


def test_infographic_cache_write_read_exists(tmp_path: Path) -> None:
    """Verify cache write/read/exists roundtrip for binary infographic bytes."""
    cache = InfographicCache(tmp_path / "cache")
    payload_hash = cache.compute_payload_hash({"a": 1, "b": "two"})
    key = CacheKey(
        scan_id=10,
        infographic_type="needs_assessment",
        prompt_version="v3",
        aspect_ratio="4:3",
        image_size="1K",
        payload_hash=payload_hash,
    )
    data = b"\x89PNG\r\n"
    path = cache.write_bytes(key, data)
    assert path.exists()
    assert cache.exists(key) is True
    assert cache.read_bytes(key) == data


def test_report_data_uri_whitelist_and_totals() -> None:
    """Check URI whitelist deduplication and metric totals aggregation."""
    rd = ReportData(
        report_id="RPT-1",
        scan_id=55,
        generated_at="2026-02-07T00:00:00+00:00",
        states=["Borno", "Yobe"],
        rollup={},
        assessments_by_state={
            "Borno": {
                "key_findings": [{"source_uris": ["u1", "u2"]}],
                "metrics": {"conflict_events": 12, "fatalities": 6},
            },
            "Yobe": {
                "key_findings": [{"source_uris": ["u2", "u3"]}],
                "metrics": {"conflict_events": 8, "fatalities": 3},
            },
        },
    )
    whitelist = rd.build_uri_whitelist()
    assert whitelist == ["u1", "u2", "u3"]

    events, fatalities = rd.totals()
    assert events == 20
    assert fatalities == 9


def test_report_data_totals_ignore_bad_values() -> None:
    """Confirm totals fallback to zero when metric values are non-numeric."""
    rd = ReportData(
        report_id="RPT-2",
        scan_id=56,
        generated_at="2026-02-07T00:00:00+00:00",
        states=["Bauchi"],
        rollup={},
        assessments_by_state={
            "Bauchi": {"metrics": {"conflict_events": "x", "fatalities": None}}
        },
    )
    assert rd.totals() == (0, 0)
