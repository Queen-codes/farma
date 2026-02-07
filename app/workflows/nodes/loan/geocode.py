"""Loan-flow geocoding node with confidence-based clarification handling.

This module resolves farmer location text into coordinate evidence used by
satellite and underwriting nodes. It is stricter than climate geocoding:
low-confidence results trigger follow-up questions before disbursement steps.
"""

from __future__ import annotations

from app.workflows.geocode_provenance import geocode_with_provenance
from app.workflows.geocode_shared import (
    build_coordinates_from_provenance,
    needs_location_refinement,
    resolve_geocode_query,
    translated_clarifying_question,
)
from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


async def geocoding_node(state: FarmaState) -> dict:
    """Resolve loan request location and gate downstream loan analysis.

    Args:
        state: Workflow state with parsed location text and detected language.

    Returns:
        State update dict containing one of:
        - `coordinates` and `geocode_provenance` when location is sufficient.
        - `AWAITING_FARMER_RESPONSE` plus `pending_question` for missing/vague
          location.

    Raises:
        Exception: Propagates unexpected geocoding/translation errors.

    Side Effects:
        Emits geocoding start/completion events.
        Performs outbound geocoding calls and translation calls when needed.

    Latency:
        Dominated by external geocoding and translation network round-trips.
    """

    emit_event("geocode_started", step="geocode_location")

    # Get detected language for translations
    language = state.get("language") or "English"

    parsed = state.get("parsed_data") or {}
    query = resolve_geocode_query(
        parsed.get("geocode_query"),
        parsed.get("landmark"),
        state.get("location_query"),
        state.get("message"),
    )

    if not query:
        q = await translated_clarifying_question(
            provider_question=None,
            fallback_english="Please reply with the nearest town/village and a nearby market or junction.",
            language=language,
            context="loan_location_request",
        )
        emit_event(
            "geocode_done",
            status="failed",
            step="geocode_location",
            payload={"error": "missing_query"},
        )
        return {
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "farmer_response": q,
            "risk_flags": ["LOCATION_VAGUE"],
        }

    prov = await geocode_with_provenance(query=query)
    if not prov.get("ok"):
        q = await translated_clarifying_question(
            provider_question=prov.get("clarifying_question"),
            fallback_english="Please reply with the nearest town/village and a nearby market or junction.",
            language=language,
            context="loan_location_clarification",
        )
        emit_event(
            "geocode_done",
            status="failed",
            step="geocode_location",
            payload={"error": prov.get("error"), "is_vague": True},
        )
        return {
            "geocode_provenance": prov,
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "farmer_response": q,
            "risk_flags": ["LOCATION_VAGUE"],
        }

    coords = build_coordinates_from_provenance(prov)

    if needs_location_refinement(prov):
        q = await translated_clarifying_question(
            provider_question=prov.get("clarifying_question"),
            fallback_english="Reply with your ward/village and the nearest market or junction.",
            language=language,
            context="loan_location_refinement",
        )
        emit_event(
            "geocode_done",
            status="completed",
            step="geocode_location",
            payload={"confidence": prov.get("confidence"), "is_vague": True},
        )
        return {
            "geocode_provenance": prov,
            "coordinates": coords,
            "location_query": query,
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "farmer_response": q,
            "risk_flags": ["LOCATION_VAGUE"],
        }

    emit_event(
        "geocode_done",
        status="completed",
        step="geocode_location",
        payload={
            "confidence": prov.get("confidence"),
            "state": coords.get("state"),
            "lga": coords.get("lga"),
        },
    )

    return {"geocode_provenance": prov, "coordinates": coords, "location_query": query}


__all__ = ["geocoding_node"]
