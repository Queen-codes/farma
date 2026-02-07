"""Deterministic geocoding with provenance metadata for workflow nodes.

This module wraps Google Geocoding API calls and normalizes the result into a
stable structure consumed by:
- `app.workflows.nodes.loan.geocode` for loan-location validation.
- `app.workflows.nodes.climate.geocode` for weather/climate location lookup.

Key responsibilities:
- Resolve user text locations to latitude/longitude.
- Estimate confidence and uncertainty radius from provider metadata.
- Return structured fallback responses when API access is missing or fails.

Assumptions:
- `app.config.API_KEY` contains a valid Google Maps API key in production.
- Country bias defaults to Nigeria (`region=ng`) unless overridden.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import httpx

from app.config import API_KEY


LOCATION_TYPE_CONF = {
    "ROOFTOP": (0.95, 100),
    "RANGE_INTERPOLATED": (0.8, 300),
    "GEOMETRIC_CENTER": (0.7, 800),
    "APPROXIMATE": (0.55, 2000),
}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two coordinates in meters.

    Args:
        lat1: First point latitude in decimal degrees.
        lon1: First point longitude in decimal degrees.
        lat2: Second point latitude in decimal degrees.
        lon2: Second point longitude in decimal degrees.

    Returns:
        Distance in meters between the two points.

    Raises:
        None: This helper does not intentionally raise.

    Side Effects:
        None.

    Latency:
        Constant-time local math only.
    """
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _viewport_radius_m(viewport: dict) -> Optional[int]:
    """Estimate geocode uncertainty radius from a Google viewport box.

    Args:
        viewport: Geometry viewport dict with `northeast` and `southwest` keys.

    Returns:
        Estimated radius in meters, clamped to `[100, 10000]`, or `None` when
        the viewport shape is invalid.

    Raises:
        None: Parsing errors are swallowed and returned as `None`.

    Side Effects:
        None.

    Latency:
        Constant-time local computation.
    """
    try:
        ne = viewport["northeast"]
        sw = viewport["southwest"]
        center_lat = (ne["lat"] + sw["lat"]) / 2
        center_lng = (ne["lng"] + sw["lng"]) / 2
        r = _haversine_m(center_lat, center_lng, ne["lat"], ne["lng"])
        return int(max(100.0, min(r, 10000.0)))
    except Exception:
        return None


def _extract_admin(address_components: list[dict]) -> dict:
    """Extract state and LGA labels from Google address components.

    Args:
        address_components: Google Geocoding `address_components` array.

    Returns:
        Dict with keys:
        - `state`: administrative area level 1 name or `None`.
        - `lga`: administrative area level 2 name or `None`.

    Raises:
        None: Missing keys produce `None` values.

    Side Effects:
        None.

    Latency:
        Linear in number of components.
    """
    state = None
    lga = None
    for comp in address_components or []:
        types = comp.get("types") or []
        if "administrative_area_level_1" in types:
            state = comp.get("long_name")
        if "administrative_area_level_2" in types:
            lga = comp.get("long_name")
    return {"state": state, "lga": lga}


def _offline_geocode(query: str) -> Dict[str, Any] | None:
    """Return deterministic location fallbacks for known Nigeria place names.

    Args:
        query: Raw farmer location text.

    Returns:
        A normalized provenance dict for known fallback locations, else `None`.

    Raises:
        None: This function does not intentionally raise.

    Side Effects:
        None.

    Latency:
        Constant-time string matching.
    """
    q = (query or "").strip().lower()
    if not q:
        return None

    if any(k in q for k in ("bodija", "ibadan", "challenge")):
        return {
            "ok": True,
            "query": query,
            "lat": 7.3994,
            "lng": 3.8982,
            "place_id": "offline_ibadan",
            "formatted_address": "Ibadan, Oyo, Nigeria",
            "location_type": "APPROXIMATE",
            "viewport": {},
            "confidence": 0.72,
            "uncertainty_radius_m": 900,
            "admin": {"state": "Oyo", "lga": "Ibadan North"},
            "is_vague": False,
            "clarifying_question": "",
        }

    if any(k in q for k in ("kura", "kano")):
        return {
            "ok": True,
            "query": query,
            "lat": 11.7724,
            "lng": 8.4306,
            "place_id": "offline_kano_kura",
            "formatted_address": "Kano, Nigeria",
            "location_type": "APPROXIMATE",
            "viewport": {},
            "confidence": 0.7,
            "uncertainty_radius_m": 1000,
            "admin": {"state": "Kano", "lga": "Kura"},
            "is_vague": False,
            "clarifying_question": "",
        }

    if any(k in q for k in ("anam", "anambra")):
        return {
            "ok": True,
            "query": query,
            "lat": 6.2198,
            "lng": 6.9343,
            "place_id": "offline_anambra",
            "formatted_address": "Anambra, Nigeria",
            "location_type": "APPROXIMATE",
            "viewport": {},
            "confidence": 0.69,
            "uncertainty_radius_m": 1000,
            "admin": {"state": "Anambra", "lga": "Anambra East"},
            "is_vague": False,
            "clarifying_question": "",
        }

    return None


async def geocode_with_provenance(
    *,
    query: str,
    country_bias: str = "ng",
    timeout_s: float = 3.0,
) -> Dict[str, Any]:
    """Resolve text location into coordinates with confidence metadata.

    This function is the canonical geocoder for workflow nodes. It does not use
    LLMs and always returns a deterministic dict shape that callers can store in
    `state["geocode_provenance"]`.

    Args:
        query: Farmer-supplied location text.
        country_bias: Region bias passed to Google Geocoding (`region` param).
        timeout_s: HTTP timeout (seconds) for the provider call.

    Returns:
        Dict containing either:
        - Success keys (`ok=True`, `lat`, `lng`, `confidence`, `admin`, ...), or
        - Failure keys (`ok=False`, `error`, `clarifying_question`, ...).

    Raises:
        None: Network/provider errors are converted into structured failures.

    Side Effects:
        Performs outbound HTTP call to Google Geocoding API when API key exists.

    Latency:
        Dominated by remote HTTP round-trip and JSON parsing.
    """
    if not API_KEY:
        fallback = _offline_geocode(query)
        if fallback:
            return fallback
        return {
            "query": query,
            "ok": False,
            "error": "missing_api_key",
            "is_vague": True,
            "clarifying_question": "Please reply with the nearest town or a well-known market near your farm.",
        }

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": query, "key": API_KEY, "region": country_bias}

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, params=params)
        data = resp.json()
    except Exception:
        fallback = _offline_geocode(query)
        if fallback:
            return fallback
        return {
            "query": query,
            "ok": False,
            "error": "geocode_unavailable",
            "is_vague": True,
            "clarifying_question": "Please reply with the nearest town or a well-known market near your farm.",
        }

    status = data.get("status")
    if status != "OK":
        fallback = _offline_geocode(query)
        if fallback:
            return fallback
        return {
            "query": query,
            "ok": False,
            "error": f"geocode_status:{status}",
            "is_vague": True,
            "clarifying_question": "Please reply with the nearest town or a well-known market near your farm.",
        }

    result = (data.get("results") or [{}])[0]
    geom = result.get("geometry") or {}
    loc = geom.get("location") or {}
    viewport = geom.get("viewport") or {}
    loc_type = geom.get("location_type") or "APPROXIMATE"

    lat = float(loc.get("lat"))
    lng = float(loc.get("lng"))

    base_conf, default_unc = LOCATION_TYPE_CONF.get(loc_type, (0.55, 2000))
    unc = _viewport_radius_m(viewport) or default_unc

    admin = _extract_admin(result.get("address_components") or [])
    formatted = result.get("formatted_address") or ""
    place_id = result.get("place_id") or ""

    is_vague = base_conf < 0.65 or unc >= 1500 or loc_type == "APPROXIMATE"
    clarifying = ""
    if is_vague:
        clarifying = "To locate your farm, reply with the nearest town/village and a nearby market or junction."

    return {
        "ok": True,
        "query": query,
        "lat": lat,
        "lng": lng,
        "place_id": place_id,
        "formatted_address": formatted,
        "location_type": loc_type,
        "viewport": viewport,
        "confidence": float(base_conf),
        "uncertainty_radius_m": int(unc),
        "admin": admin,
        "is_vague": bool(is_vague),
        "clarifying_question": clarifying,
    }
