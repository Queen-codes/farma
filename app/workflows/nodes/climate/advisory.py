"""Climate advisory generation node for weather inquiries.

This module converts forecast/rainfall/geocode evidence into one structured
farmer recommendation payload via Gemini. It is called from the climate branch
of `app.workflows.graph` after weather and CHIRPS nodes complete.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.geocode_shared import build_climate_query
from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


class ClimateAdvisoryOutput(BaseModel):
    answer_type: Literal["FORECAST_ONLY", "PLANTING_GUIDANCE", "SPRAYING_GUIDANCE"]
    summary_simple: str = ""
    recommendations: List[str] = Field(default_factory=list, max_length=4)
    risks: List[str] = Field(default_factory=list, max_length=3)
    follow_up_question: str = ""
    sms_160: str = ""


CLIMATE_SCHEMA: dict = ClimateAdvisoryOutput.model_json_schema()


async def gemini_climate_advisory(state: FarmaState) -> dict:
    """Generate localized climate advice from forecast and rainfall inputs.

    Args:
        state: Workflow state containing language, parsed question, geocode
            provenance, 7-day weather forecast, and CHIRPS rainfall.

    Returns:
        State update dict containing either:
        - `sms_text` and `analysis_summary` when advice is complete, or
        - `AWAITING_FARMER_RESPONSE` plus follow-up prompt when data is vague.

    Raises:
        RuntimeError: If both model attempts fail to produce valid output.
        Exception: Propagates unrecoverable Gemini/API failures.

    Side Effects:
        Emits climate advisory start/completion events.
        Performs one or two outbound Gemini API calls.

    Latency:
        Dominated by LLM inference and network round-trips.
    """
    emit_event("climate_advisory_started", step="climate_advisory")

    lang = (state.get("language") or "").strip() or "English"
    msg = (state.get("message") or "").strip()
    cq = state.get("climate_query") or build_climate_query(state.get("parsed_data") or {})
    crop = (cq.get("crop") or "").strip()
    forecast = state.get("weather_forecast") or {}
    chirps_30d = state.get("chirps_rainfall_30d")
    geo = state.get("geocode_provenance") or {}

    prompt = (
        "You are FARMA, an on-demand climate advisory service for Nigerian farmers.\n"
        "Use the provided forecast and recent rainfall to answer the farmer.\n"
        "Return JSON ONLY matching the schema.\n\n"
        "Rules:\n"
        "- Keep language simple (low literacy), short sentences.\n"
        "- Use the farmer's language/dialect.\n"
        "- sms_160 must be <=160 characters.\n"
        "- If missing crop or location is vague, ask ONE follow-up question.\n\n"
        f"LANGUAGE: {lang}\n"
        f"FARMER QUESTION: {msg}\n"
        f"CROP (optional): {crop}\n"
        f"LOCATION_CONFIDENCE: {geo.get('confidence')}\n"
        f"FORECAST_JSON: {forecast}\n"
        f"RECENT_RAINFALL_30D_MM: {chirps_30d}\n"
    )

    last_err: str | None = None
    schema: dict | None = CLIMATE_SCHEMA
    for attempt in range(2):
        try:
            obj = await call_json(
                model=MODEL_FLASH,
                prompt=prompt
                if attempt == 0
                else (prompt + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."),
                thinking_level="low",
                temperature=0.2,
                schema=schema,
                timeout_s=15.0,
            )
        except Exception as e:
            msg_e = str(e)
            if schema is not None and (
                "additionalProperties" in msg_e
                or "should be non-empty for OBJECT type" in msg_e
            ):
                schema = None
                last_err = "schema_unsupported"
                continue
            raise

        out = ClimateAdvisoryOutput.model_validate(obj).model_dump()
        emit_event("climate_advisory_completed", status="completed", step="climate_advisory")

        status = state.get("status") or "READY_FOR_ANALYSIS"

        sms = (out.get("sms_160") or "").strip()
        follow_up = (out.get("follow_up_question") or "").strip()

        if follow_up and (not crop or (geo.get("is_vague") is True)):
            status = "AWAITING_FARMER_RESPONSE"
            sms = follow_up[:160]
            return {
                "status": status,
                "pending_question": follow_up,
                "pending_question_type": "climate",
                "farmer_response": follow_up,
                "sms_text": sms,
                "analysis_summary": [
                    out.get("summary_simple") or "Climate advice generated.",
                    *(out.get("recommendations") or [])[:2],
                ],
            }

        return {
            "status": "READY_FOR_ANALYSIS",
            "sms_text": sms,
            "analysis_summary": [
                out.get("summary_simple") or "Climate advice generated.",
                *(out.get("recommendations") or [])[:3],
            ],
        }

    raise RuntimeError("climate_advisory_failed")
