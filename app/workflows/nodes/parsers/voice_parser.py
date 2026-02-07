"""Voice parser node that extracts intent/fields directly from audio input.

This module mirrors SMS parsing schema but sends audio bytes to Gemini as
inline data. The output contract is identical to SMS parser output so the rest
of the workflow can stay unchanged.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from google.genai import types

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.job_events import emit_event
from app.workflows.loan_schemas import SMSParseOutput, SMS_PARSE_SCHEMA
from app.workflows.state import FarmaState


def _clean(value: object) -> str:
    """Normalize optional values into trimmed strings.

    Args:
        value: Arbitrary value from parsed output/state.

    Returns:
        Trimmed string, or empty string for falsy values.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time local conversion.
    """
    return str(value or "").strip()


def _build_prompt() -> str:
    """Build instruction prompt for structured voice-message parsing.

    Returns:
        Prompt string defining schema and extraction rules.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time string literal construction.
    """
    return (
        "You are FARMA, an AI assistant for Nigerian farmers.\n"
        "Task: identify the farmer's intent and extract structured fields from this voice message.\n"
        "Return JSON ONLY, matching the schema.\n\n"
        "Rules:\n"
        "- intent must be one of: LOAN_REQUEST, DISEASE_REPORT, WEATHER_INQUIRY, HUMAN_ESCALATION\n"
        "- parse_confidence: 0..1 (honest)\n"
        "- location.geocode_query should be a cleaned location string suitable for a geocoding API.\n"
        "- If the location is vague or missing, set location.needs_clarification=true and provide "
        "location.clarifying_question that can be answered by SMS.\n"
        "- For LOAN_REQUEST: fill loan.amount (number), loan.crop_type, and optionally loan.farm_size and loan.crop_stage.\n"
        "- For DISEASE_REPORT: fill disease.crop_type and disease.symptoms.\n"
        "- For WEATHER_INQUIRY: fill weather.question_type and weather.time_horizon_days.\n"
    )


def _to_parsed_data(parsed: dict) -> dict:
    """Flatten nested parser output to workflow `parsed_data` format.

    Args:
        parsed: Dict shaped like `SMSParseOutput`.

    Returns:
        Flattened dict consumed by downstream nodes.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time dict transformation.
    """
    location = parsed.get("location") or {}
    loan = parsed.get("loan") or {}
    disease = parsed.get("disease") or {}
    weather = parsed.get("weather") or {}

    return {
        "parse_confidence": parsed.get("parse_confidence"),
        "landmark": _clean(location.get("landmark")) or None,
        "geocode_query": _clean(location.get("geocode_query")) or None,
        "crop_type": _clean(loan.get("crop_type")) or None,
        "amount": loan.get("amount") or None,
        "farm_size": _clean(loan.get("farm_size")) or None,
        "crop_stage": _clean(loan.get("crop_stage")) or None,
        "symptoms": _clean(disease.get("symptoms")) or None,
        "disease_crop_type": _clean(disease.get("crop_type")) or None,
        "weather_question_type": _clean(weather.get("question_type")) or None,
        "weather_time_horizon_days": weather.get("time_horizon_days") or None,
    }


async def voice_parser_node(state: FarmaState) -> dict:
    """Parse farmer audio input into intent and structured workflow fields.

    Request format:
        Reads `state["audio_path"]` plus optional phone for tracing events.

    Response format:
        Returns parser updates matching SMS parser contract:
        `intent`, `language`, `status`, `parsed_data`, and optional clarification.

    Status behavior:
        - `READY_FOR_ANALYSIS` when parse confidence and required fields are OK.
        - `AWAITING_FARMER_RESPONSE` when location clarification is needed.
        - `ESCALATE_TO_HUMAN` on missing/unreadable audio inputs.

    Idempotency and retries:
        Safe to retry for same audio file; no persistent writes.
        Performs up to two model attempts with correction prompt.

    Args:
        state: Workflow state containing path to stored audio file.

    Returns:
        State update dict for intent routing and downstream analysis.

    Raises:
        RuntimeError: If model output remains invalid after retries.
        Exception: Propagates unexpected file/model failures.

    Side Effects:
        Reads audio bytes from local filesystem.
        Performs Gemini API calls.
        Emits parser lifecycle events.

    Latency:
        Dominated by file I/O and LLM audio understanding call.
    """
    emit_event(
        "parse_intent_started",
        step="parse_intent",
        payload={"phone": state.get("phone")},
    )

    audio_path = state.get("audio_path")
    if not audio_path:
        emit_event(
            "parse_intent_done",
            status="failed",
            step="parse_intent",
            payload={"error": "missing_audio"},
        )
        return {"intent": "HUMAN_ESCALATION", "status": "ESCALATE_TO_HUMAN"}

    p = Path(audio_path)
    mime_type, _ = mimetypes.guess_type(str(p))
    if not mime_type:
        mime_type = "audio/mp4"

    if not p.exists():
        emit_event(
            "parse_intent_done",
            status="failed",
            step="parse_intent",
            payload={"error": "audio_file_not_found"},
        )
        return {"intent": "HUMAN_ESCALATION", "status": "ESCALATE_TO_HUMAN"}

    audio_bytes = p.read_bytes()
    base_prompt = _build_prompt()

    last_err: str | None = None
    schema: dict | None = SMS_PARSE_SCHEMA
    for attempt in range(2):
        prompt = (
            base_prompt
            if attempt == 0
            else (
                base_prompt
                + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."
            )
        )
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text=prompt),
                    types.Part(
                        inline_data=types.Blob(data=audio_bytes, mime_type=mime_type)
                    ),
                ],
            )
        ]
        try:
            obj = await call_json(
                model=MODEL_FLASH,
                contents=contents,
                thinking_level="low",
                temperature=0.1,
                schema=schema,
                timeout_s=9.0,
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
        try:
            parsed = SMSParseOutput.model_validate(obj).model_dump()
        except Exception as e:
            last_err = f"invalid_parser_output: {e}"
            continue
        break
    else:
        raise RuntimeError(last_err or "voice_parse_failed")

    emit_event(
        "parse_intent_done",
        status="completed",
        step="parse_intent",
        payload={
            "intent": parsed.get("intent"),
            "confidence": parsed.get("parse_confidence"),
        },
    )
    parsed_data = _to_parsed_data(parsed)
    location = parsed.get("location") or {}

    if location.get("needs_clarification") and location.get("clarifying_question"):
        q = _clean(location.get("clarifying_question"))
        return {
            "intent": parsed.get("intent"),
            "language": parsed.get("language"),
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": q,
            "pending_question_type": "location",
            "parsed_data": parsed_data,
            "farmer_response": q,
        }

    return {
        "intent": parsed.get("intent"),
        "language": parsed.get("language"),
        "status": "READY_FOR_ANALYSIS",
        "parsed_data": parsed_data,
        "farmer_response": None,
    }


__all__ = ["voice_parser_node"]
