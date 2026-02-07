"""Regression tests for runtime helpers and shared geocode utility functions.

Coverage includes environment boolean parsing, background task tracking, and
deterministic geocode helper behavior used by loan/climate nodes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from app.api.helpers.runtime import env_bool, spawn_bg_task, utcnow_naive
from app.workflows import geocode_shared


def test_env_bool_truthy_and_falsey(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure `env_bool` recognizes truthy, falsy, and default fallback values."""
    monkeypatch.setenv("BOOL_A", "true")
    monkeypatch.setenv("BOOL_B", "0")
    monkeypatch.delenv("BOOL_C", raising=False)

    assert env_bool("BOOL_A") is True
    assert env_bool("BOOL_B") is False
    assert env_bool("BOOL_C", default=True) is True


def test_utcnow_naive_is_timezone_naive() -> None:
    """Validate UTC helper returns timezone-naive datetime close to current UTC."""
    ts = utcnow_naive()
    assert isinstance(ts, datetime)
    assert ts.tzinfo is None

    # Timestamp should still roughly represent "now UTC".
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now_utc_naive - ts).total_seconds()) < 5


@pytest.mark.anyio
async def test_spawn_bg_task_tracks_and_discards_completed_task() -> None:
    """Confirm spawned tasks are tracked then removed after completion."""
    app = FastAPI()
    app.state.bg_tasks = set()

    async def _work() -> int:
        """Short async workload used to exercise background task lifecycle."""
        await asyncio.sleep(0.01)
        return 123

    spawn_bg_task(app, _work())
    assert len(app.state.bg_tasks) == 1

    await asyncio.sleep(0.05)
    assert len(app.state.bg_tasks) == 0


def test_resolve_geocode_query_uses_first_non_empty_candidate() -> None:
    """Ensure first non-empty location candidate is selected."""
    out = geocode_shared.resolve_geocode_query(None, "   ", "Kano Market", "Yola")
    assert out == "Kano Market"


def test_build_coordinates_from_provenance_normalizes_fields() -> None:
    """Verify provenance payload is normalized into workflow coordinate shape."""
    prov = {
        "lat": 11.2,
        "lng": 13.1,
        "confidence": 0.72,
        "uncertainty_radius_m": 2000,
        "admin": {"state": "Borno", "lga": "Maiduguri"},
    }
    coords = geocode_shared.build_coordinates_from_provenance(prov)
    assert coords["lat"] == 11.2
    assert coords["lng"] == 13.1
    assert coords["confidence"] == 0.72
    assert coords["state"] == "Borno"
    assert coords["lga"] == "Maiduguri"
    # suggested buffer is bounded [150,1000]
    assert coords["suggested_buffer"] == 500


def test_needs_location_refinement_respects_confidence_and_vague_flag() -> None:
    """Check refinement logic for low confidence and explicit vague flag paths."""
    assert geocode_shared.needs_location_refinement({"confidence": 0.4}) is True
    assert geocode_shared.needs_location_refinement({"confidence": 0.9, "is_vague": True}) is True
    assert geocode_shared.needs_location_refinement({"confidence": 0.9, "is_vague": False}) is False


@pytest.mark.anyio
async def test_translated_clarifying_question_prefers_provider_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure provider-supplied question is preferred over fallback prompt text."""
    seen = {}

    async def _fake_translate(message: str, language: str, context: str) -> str:
        """Capture translation call arguments and return deterministic marker."""
        seen["message"] = message
        seen["language"] = language
        seen["context"] = context
        return f"TX:{message}"

    monkeypatch.setattr(geocode_shared, "translate_to_farmer_language", _fake_translate)

    out = await geocode_shared.translated_clarifying_question(
        provider_question="Nearest junction?",
        fallback_english="Fallback question",
        language="Hausa",
        context="loan_location_request",
    )
    assert out == "TX:Nearest junction?"
    assert seen == {
        "message": "Nearest junction?",
        "language": "Hausa",
        "context": "loan_location_request",
    }


def test_build_climate_query_defaults_and_horizon_bounds() -> None:
    """Validate climate-query defaults plus 1..14 day horizon clamping."""
    # lower bound
    q1 = geocode_shared.build_climate_query({"weather_time_horizon_days": -2})
    assert q1["time_horizon_days"] == 1
    assert q1["question_type"] == "FORECAST"

    # upper bound + crop/location normalization
    q2 = geocode_shared.build_climate_query(
        {
            "weather_time_horizon_days": 30,
            "weather_question_type": "rainfall",
            "crop_type": "maize",
            "geocode_query": "Ibadan Challenge",
        }
    )
    assert q2["time_horizon_days"] == 14
    assert q2["question_type"] == "rainfall"
    assert q2["crop"] == "maize"
    assert q2["location_text"] == "Ibadan Challenge"
