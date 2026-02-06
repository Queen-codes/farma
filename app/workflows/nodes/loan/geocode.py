from __future__ import annotations

from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.state import FarmaState
from app.workflows.geocode_provenance import geocode_with_provenance
from app.workflows.job_events import emit_event


async def geocoding_node(state: FarmaState) -> dict:

    emit_event("geocode_started", step="geocode_location")

    # Get detected language for translations
    language = state.get("language") or "English"

    parsed = state.get("parsed_data") or {}
    query = (
        parsed.get("geocode_query")
        or parsed.get("landmark")
        or state.get("location_query")
        or state.get("message")
        or ""
    ).strip()

    if not query:
        q_english = "Please reply with the nearest town/village and a nearby market or junction."
        q = await translate_to_farmer_language(
            q_english,
            language,
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
        q_from_prov = prov.get("clarifying_question")
        if q_from_prov:
            q = await translate_to_farmer_language(
                q_from_prov,
                language,
                context="loan_location_clarification",
            )
        else:
            q_fallback = "Please reply with the nearest town/village and a nearby market or junction."
            q = await translate_to_farmer_language(
                q_fallback,
                language,
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

    coords = {
        "lat": prov.get("lat"),
        "lng": prov.get("lng"),
        "confidence": prov.get("confidence"),
        "uncertainty_radius_m": prov.get("uncertainty_radius_m"),
        "suggested_buffer": max(
            150, min(int(prov.get("uncertainty_radius_m") or 800) // 4, 1000)
        ),
        "state": (prov.get("admin") or {}).get("state"),
        "lga": (prov.get("admin") or {}).get("lga"),
    }

    if prov.get("is_vague") or (prov.get("confidence") or 0) < 0.6:
        q_from_prov = prov.get("clarifying_question")
        if q_from_prov:
            q = await translate_to_farmer_language(
                q_from_prov,
                language,
                context="loan_location_refinement",
            )
        else:
            q_fallback = (
                "Reply with your ward/village and the nearest market or junction."
            )
            q = await translate_to_farmer_language(
                q_fallback,
                language,
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
