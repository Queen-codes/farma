"""CHIRPS rainfall retrieval node for climate advisory flow.

This node pulls recent rainfall totals (last 30 days) from Google Earth Engine
using coordinates prepared by climate geocoding. Output is consumed by
`app.workflows.nodes.climate.advisory`.
"""

from __future__ import annotations

import asyncio

import ee

from app.workflows.gee_signals import ensure_gee_initialized, get_chirps_rainfall_30d_mm
from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


async def fetch_recent_rainfall_chirps(state: FarmaState) -> dict:
    """Fetch 30-day rainfall total in millimeters from CHIRPS.

    Args:
        state: Workflow state expected to contain geocoded coordinates.

    Returns:
        Dict with key `chirps_rainfall_30d` set to float rainfall value or
        `None` when unavailable.

    Raises:
        None: Errors are converted to `None` output and completion events.

    Side Effects:
        Emits CHIRPS progress events.
        Calls Earth Engine APIs (via `asyncio.to_thread`) for data retrieval.

    Latency:
        Dominated by Earth Engine server computation and network latency.
    """
    emit_event("chirps_started", step="chirps_rainfall")

    coords = state.get("coordinates") or {}
    lat = coords.get("lat")
    lng = coords.get("lng")
    if lat is None or lng is None:
        emit_event("chirps_completed", status="failed", step="chirps_rainfall", payload={"error": "missing_coords"})
        return {"chirps_rainfall_30d": None}

    if not ensure_gee_initialized():
        emit_event("chirps_completed", status="failed", step="chirps_rainfall", payload={"error": "gee_not_initialized"})
        return {"chirps_rainfall_30d": None}

    radius_m = int(coords.get("uncertainty_radius_m") or coords.get("suggested_buffer") or 800)
    radius_m = max(150, min(radius_m, 1200))
    area = ee.Geometry.Point([float(lng), float(lat)]).buffer(radius_m)

    val, err = await asyncio.to_thread(get_chirps_rainfall_30d_mm, area)
    if err:
        emit_event("chirps_completed", status="completed", step="chirps_rainfall", payload={"available": False})
        return {"chirps_rainfall_30d": None}

    emit_event("chirps_completed", status="completed", step="chirps_rainfall", payload={"available": val is not None})
    return {"chirps_rainfall_30d": val}
