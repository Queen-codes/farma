"""Shared geocoding normalization helpers for loan and climate flows.

This module centralizes small, deterministic transforms used by:
- `app.workflows.nodes.loan.geocode`.
- `app.workflows.nodes.climate.geocode`.

Responsibilities:
- Select best user-supplied location query.
- Normalize provider provenance output into `state["coordinates"]`.
- Build climate-query defaults from parser output.
- Translate clarifying prompts into farmer language.
"""

from __future__ import annotations

from typing import Any

from app.workflows.language_utils import translate_to_farmer_language


def resolve_geocode_query(*candidates: Any) -> str:
    """Return the first non-empty location string from candidate inputs.

    Args:
        *candidates: Possible location values (strings or any castable object).

    Returns:
        First non-empty trimmed string, else empty string.

    Raises:
        None: This helper does not intentionally raise.

    Side Effects:
        None.

    Latency:
        Linear in number of candidates; local-only.
    """
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def build_coordinates_from_provenance(prov: dict) -> dict:
    """Convert provenance payload into workflow coordinate shape.

    Args:
        prov: Dict returned by `geocode_with_provenance`.

    Returns:
        Dict with normalized coordinate fields used across nodes:
        `lat`, `lng`, `confidence`, `uncertainty_radius_m`,
        `suggested_buffer`, `state`, and `lga`.

    Raises:
        None: Missing keys are tolerated and mapped to defaults.

    Side Effects:
        None.

    Latency:
        Constant-time local transformation.
    """
    uncertainty_m = int(prov.get("uncertainty_radius_m") or 800)
    return {
        "lat": prov.get("lat"),
        "lng": prov.get("lng"),
        "confidence": prov.get("confidence"),
        "uncertainty_radius_m": uncertainty_m,
        "suggested_buffer": max(150, min(uncertainty_m // 4, 1000)),
        "state": (prov.get("admin") or {}).get("state"),
        "lga": (prov.get("admin") or {}).get("lga"),
    }


def needs_location_refinement(prov: dict, min_confidence: float = 0.6) -> bool:
    """Decide whether location certainty is too weak for loan decisions.

    Args:
        prov: Geocode provenance dict.
        min_confidence: Minimum acceptable confidence before clarification.

    Returns:
        `True` when provider marked the location vague or confidence is below
        `min_confidence`; otherwise `False`.

    Raises:
        None: Invalid/missing values default to low confidence.

    Side Effects:
        None.

    Latency:
        Constant-time local checks.
    """
    confidence = float(prov.get("confidence") or 0.0)
    return bool(prov.get("is_vague")) or confidence < min_confidence


async def translated_clarifying_question(
    *,
    provider_question: str | None,
    fallback_english: str,
    language: str,
    context: str,
) -> str:
    """Build and translate a location clarification question for the farmer.

    Args:
        provider_question: Provider-suggested clarification text, if any.
        fallback_english: Default English prompt when provider text is missing.
        language: Farmer language label from parsed intent step.
        context: Translation context label for quality steering.

    Returns:
        Farmer-facing question translated via language utilities.

    Raises:
        Exception: Propagates translation errors from downstream helper.

    Side Effects:
        May call Gemini translation service through `translate_to_farmer_language`.

    Latency:
        Dominated by LLM translation call.
    """
    question = str(provider_question or "").strip() or fallback_english
    return await translate_to_farmer_language(question, language, context=context)


def build_climate_query(parsed: dict) -> dict:
    """Normalize parser output into climate node query contract.

    Args:
        parsed: Parser output dict from SMS/voice parsing nodes.

    Returns:
        Dict with `question_type`, bounded `time_horizon_days`, optional `crop`,
        and optional `location_text`.

    Raises:
        None: Invalid horizon values are coerced to safe defaults.

    Side Effects:
        None.

    Latency:
        Constant-time local normalization.
    """
    raw_horizon = parsed.get("weather_time_horizon_days")
    try:
        horizon = int(raw_horizon) if raw_horizon is not None else 7
    except Exception:
        horizon = 7
    horizon = max(1, min(horizon, 14))

    question_type = (parsed.get("weather_question_type") or "").strip() or "FORECAST"
    crop = (parsed.get("crop_type") or "").strip() or None
    location_text = resolve_geocode_query(
        parsed.get("geocode_query"),
        parsed.get("landmark"),
    ) or None

    return {
        "question_type": question_type,
        "time_horizon_days": horizon,
        "crop": crop,
        "location_text": location_text,
    }
