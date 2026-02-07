"""Weather forecast retrieval node for climate workflow branch.

This module calls Open-Meteo using workflow coordinates and returns the daily
forecast payload used by climate advisory generation.
"""

from __future__ import annotations

import httpx

from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a reusable async HTTP client for weather API requests.

    Returns:
        Shared `httpx.AsyncClient` instance with fixed timeout.

    Raises:
        None: Client creation is lazy and non-throwing under normal conditions.

    Side Effects:
        Mutates module-level `_http_client` cache.

    Latency:
        Constant-time local object creation/check.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=5.0)
    return _http_client


async def fetch_weather_forecast(state: FarmaState) -> dict:
    """Fetch 7-day weather forecast for geocoded farmer coordinates.

    Args:
        state: Workflow state expected to include `coordinates.lat/lng`.

    Returns:
        Dict containing `weather_forecast` JSON on success, or `None` plus
        risk flag when API call fails.

    Raises:
        None: Request/parsing failures are converted into fallback response.

    Side Effects:
        Emits weather fetch events.
        Performs outbound HTTP GET call to Open-Meteo API.

    Latency:
        Dominated by external weather API response time.
    """
    emit_event("weather_fetch_started", step="weather_fetch")

    coords = state.get("coordinates") or {}
    lat = coords.get("lat")
    lng = coords.get("lng")
    if lat is None or lng is None:
        emit_event("weather_fetch_completed", status="failed", step="weather_fetch", payload={"error": "missing_coords"})
        return {"weather_forecast": None}

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lng,
        "daily": "precipitation_probability_max,precipitation_sum,temperature_2m_min,temperature_2m_max",
        "forecast_days": 7,
        "timezone": "Africa/Lagos",
    }

    try:
        resp = await _get_http_client().get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        emit_event("weather_fetch_completed", status="failed", step="weather_fetch", payload={"error": str(e)})
        return {"weather_forecast": None, "risk_flags": ["WEATHER_API_UNAVAILABLE"]}

    emit_event(
        "weather_fetch_completed",
        status="completed",
        step="weather_fetch",
        payload={"days": len((data.get("daily") or {}).get("time") or [])},
    )
    return {"weather_forecast": data}
