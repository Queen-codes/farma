"""Deterministic location resolution for the climate workflow branch.

This node resolves or reuses coordinates for weather inquiries, then stores:
- `climate_query` (normalized weather question fields).
- `coordinates` and `geocode_provenance` when geocoding succeeds.

Used by:
- `app.workflows.graph` climate path before weather/CHIRPS nodes.
"""

from __future__ import annotations

from app.workflows.geocode_provenance import geocode_with_provenance
from app.workflows.geocode_shared import (
    build_climate_query,
    build_coordinates_from_provenance,
    resolve_geocode_query,
    translated_clarifying_question,
)
from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


async def geocode_location_deterministic(state: FarmaState) -> dict:
    """Resolve weather-inquiry location using deterministic geocoder only.

    Args:
        state: Workflow state with parsed location hints and detected language.

    Returns:
        Dict containing:
        - `climate_query` always.
        - `coordinates`/`geocode_provenance` on success.
        - `AWAITING_FARMER_RESPONSE` plus translated clarification when missing
          or low-quality location text.

    Raises:
        Exception: Propagates unexpected translation/geocoding failures.

    Side Effects:
        Emits geocoding lifecycle events.
        Calls external geocoding API through shared provenance helper.
        May call translation helper for clarifying questions.

    Latency:
        Dominated by geocoding and translation network calls.
    """
    emit_event("climate_geocode_started", step="climate_geocode")

    # Get detected language for translations
    language = state.get("language") or "English"
    parsed = state.get("parsed_data") or {}
    climate_query = build_climate_query(parsed)

    coords = state.get("coordinates") or {}
    if coords.get("lat") is not None and coords.get("lng") is not None:
        emit_event(
            "climate_geocode_completed",
            status="completed",
            step="climate_geocode",
            payload={"reused": True},
        )
        return {"climate_query": climate_query}

    query = resolve_geocode_query(
        parsed.get("geocode_query"),
        parsed.get("landmark"),
        (state.get("climate_query") or {}).get("location_text"),
        climate_query.get("location_text"),
        state.get("message"),
    )

    if not query:
        q = await translated_clarifying_question(
            provider_question=None,
            fallback_english="For weather advice, reply with your nearest town/village and a nearby market or junction.",
            language=language,
            context="climate_location_request",
        )
        emit_event(
            "climate_geocode_completed",
            status="failed",
            step="climate_geocode",
            payload={"error": "missing_query"},
        )
        return {
            "climate_query": climate_query,
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "farmer_response": q,
        }

    prov = await geocode_with_provenance(query=query)
    if not prov.get("ok"):
        q = await translated_clarifying_question(
            provider_question=prov.get("clarifying_question"),
            fallback_english="Reply with your nearest town/village and a nearby market or junction.",
            language=language,
            context="climate_location_clarification",
        )
        emit_event(
            "climate_geocode_completed",
            status="failed",
            step="climate_geocode",
            payload={"error": prov.get("error")},
        )
        return {
            "climate_query": climate_query,
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "farmer_response": q,
            "geocode_provenance": prov,
        }

    coords_out = build_coordinates_from_provenance(prov)

    # Weather/climate queries don't need precise coordinates —
    # state-level is fine for rainfall and forecast data.

    emit_event(
        "climate_geocode_completed",
        status="completed",
        step="climate_geocode",
        payload={"state": coords_out.get("state"), "lga": coords_out.get("lga")},
    )
    return {
        "climate_query": climate_query,
        "coordinates": coords_out,
        "geocode_provenance": prov,
    }
