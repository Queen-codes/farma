"""SMS intent parser node and deterministic fallback extractors.

This module is the primary entry-point for text messages in the workflow.
It performs structured parsing (intent + fields) with Gemini and falls back to
rule-based extraction when LLM output is unavailable or invalid.

Outputs from this node drive routing in `app.workflows.graph.intent_gate`.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.job_events import emit_event
from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.loan_schemas import SMSParseOutput, SMS_PARSE_SCHEMA
from app.workflows.state import FarmaState


def _clean(value: Any) -> str:
    """Normalize optional values into trimmed strings.

    Args:
        value: Arbitrary value from state or model output.

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


def _build_prompt(phone: str, message: str) -> str:
    """Build structured-parser prompt for one SMS message.

    Args:
        phone: Farmer phone number (context only).
        message: Raw SMS body.

    Returns:
        Prompt instructing Gemini to output JSON matching parser schema.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time string composition.
    """
    return (
        "You are FARMA, an AI assistant for Nigerian farmers.\n"
        "Task: identify the farmer's intent and extract structured fields from the message.\n"
        "Return JSON ONLY, matching the schema.\n\n"
        "## LANGUAGE & CONTEXT\n"
        "- Messages may be in English, Hausa, Igbo, Yoruba, or Nigerian Pidgin\n"
        "- Detect and set the 'language' field accurately\n"
        "- If you generate a clarifying_question, write it in the DETECTED language\n"
        "- Recognize informal grammar, local terms, and code-switching\n"
        "- Common Nigerian crops: cassava, maize, rice, tomato, yam, pepper, beans\n"
        "- Typical loan amounts: 20,000 - 500,000 naira\n\n"
        "## RULES\n"
        "- intent must be one of: LOAN_REQUEST, DISEASE_REPORT, WEATHER_INQUIRY, HUMAN_ESCALATION\n"
        "- parse_confidence: 0.0-1.0 (be honest about uncertainty)\n"
        "- location.geocode_query should be a cleaned location string for geocoding (e.g., 'Kano Central Market', 'Onitsha', 'Lagos Island')\n"
        "- If location is vague or missing, set location.needs_clarification=true and provide location.clarifying_question\n"
        "- For LOAN_REQUEST: extract loan.amount (number), loan.crop_type, and optionally loan.farm_size and loan.crop_stage\n"
        "- For DISEASE_REPORT: extract disease.crop_type and disease.symptoms (be specific about leaf/stem issues)\n"
        "- For WEATHER_INQUIRY: extract weather.question_type and weather.time_horizon_days\n\n"
        "## EXAMPLES\n\n"
        "Example 1 - Loan Request (English):\n"
        'MESSAGE: "I need 50000 naira for my cassava farm in Kano market"\n'
        "OUTPUT: {\n"
        '  "intent": "LOAN_REQUEST",\n'
        '  "language": "English",\n'
        '  "parse_confidence": 0.95,\n'
        '  "loan": {"amount": 50000, "crop_type": "cassava"},\n'
        '  "location": {"geocode_query": "Kano market", "needs_clarification": false}\n'
        "}\n\n"
        "Example 2 - Disease Report (Pidgin):\n"
        'MESSAGE: "My tomato leaves don get yellow spots for Onitsha"\n'
        "OUTPUT: {\n"
        '  "intent": "DISEASE_REPORT",\n'
        '  "language": "Pidgin",\n'
        '  "parse_confidence": 0.9,\n'
        '  "disease": {"crop_type": "tomato", "symptoms": "yellow spots on leaves"},\n'
        '  "location": {"geocode_query": "Onitsha", "needs_clarification": false}\n'
        "}\n\n"
        "Example 3 - Weather Inquiry (Vague Location):\n"
        'MESSAGE: "Go rain this week?"\n'
        "OUTPUT: {\n"
        '  "intent": "WEATHER_INQUIRY",\n'
        '  "language": "Pidgin",\n'
        '  "parse_confidence": 0.85,\n'
        '  "weather": {"question_type": "rainfall", "time_horizon_days": 7},\n'
        '  "location": {\n'
        '    "needs_clarification": true,\n'
        '    "clarifying_question": "For weather advice, reply with your nearest town/village."\n'
        "  }\n"
        "}\n\n"
        "Example 4 - Loan Request (Hausa):\n"
        'MESSAGE: "Ina bukatar kuɗi 100000 don shuka wake a Kaduna"\n'
        "OUTPUT: {\n"
        '  "intent": "LOAN_REQUEST",\n'
        '  "language": "Hausa",\n'
        '  "parse_confidence": 0.9,\n'
        '  "loan": {"amount": 100000, "crop_type": "beans"},\n'
        '  "location": {"geocode_query": "Kaduna", "needs_clarification": false}\n'
        "}\n\n"
        "Example 5 - Disease Report (English, detailed):\n"
        'MESSAGE: "Help! My maize has brown spots on leaves and some wilting in Ibadan zone 3"\n'
        "OUTPUT: {\n"
        '  "intent": "DISEASE_REPORT",\n'
        '  "language": "English",\n'
        '  "parse_confidence": 0.95,\n'
        '  "disease": {"crop_type": "maize", "symptoms": "brown spots on leaves, wilting"},\n'
        '  "location": {"geocode_query": "Ibadan zone 3", "needs_clarification": false}\n'
        "}\n\n"
        "## NOW PARSE THIS MESSAGE\n"
        f"PHONE: {phone}\n"
        f"MESSAGE: {message}\n"
    )


def _detect_language(message: str) -> str:
    """Heuristically detect language for fallback parsing path.

    Args:
        message: Raw farmer message.

    Returns:
        One of `English`, `Yoruba`, `Igbo`, `Hausa`, or `Pidgin`.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time token matching.
    """
    m = (message or "").lower()
    if any(token in m for token in ("mo nilo", "owo", "oke", "oko", "ilẹ", "e jowo")):
        return "Yoruba"
    if any(token in m for token in ("ndewo", "biko", "anyị", "anambra")):
        return "Igbo"
    if any(token in m for token in ("ina", "bukatar", "kudi", "kuɗi", "don shuka")):
        return "Hausa"
    if any(token in m for token in ("abeg", "dey", "una", "wetin", "go rain")):
        return "Pidgin"
    return "English"


def _extract_amount(message: str) -> float:
    """Extract probable loan amount from free text.

    Args:
        message: Raw farmer message.

    Returns:
        Parsed numeric amount in naira, or `0.0` when not found/invalid.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Regex scan over message string.
    """
    m = re.search(r"(?:₦|n)?\s*([0-9]{2,3}(?:,[0-9]{3})+|[0-9]{4,6})", message, re.I)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return 0.0


def _extract_crop(message: str) -> str:
    """Extract supported crop label from free text fallback parsing.

    Args:
        message: Raw farmer message.

    Returns:
        Canonical crop name when recognized, otherwise empty string.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Linear scan across small crop vocabulary.
    """
    text = (message or "").lower()
    crops = ("cassava", "maize", "rice", "tomato", "yam", "pepper", "beans")
    for crop in crops:
        if crop in text:
            return crop
    # quick Nigerian-language hint for common terms in tests
    if "oka" in text:
        return "maize"
    if "ji" in text:
        return "yam"
    return ""


def _extract_location_query(message: str) -> str:
    """Extract simple location phrase from message using preposition heuristic.

    Args:
        message: Raw farmer message.

    Returns:
        Location substring (for example after `in/near/at`) or empty string.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        One regex scan over message text.
    """
    text = (message or "").strip()
    if not text:
        return ""
    m = re.search(r"\b(?:at|near|in)\s+([^.,;]+)", text, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _fallback_parse(message: str) -> dict:
    """Produce deterministic parser output when LLM parser is unavailable.

    Args:
        message: Raw SMS text.

    Returns:
        Dict matching `SMSParseOutput` structure with conservative confidence.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Local rule evaluation only.
    """
    text = (message or "").strip()
    text_l = text.lower()
    intent = "HUMAN_ESCALATION"
    if any(t in text_l for t in ("loan", "borrow", "credit", "naira", "n", "owo", "kudi", "kuɗi")):
        intent = "LOAN_REQUEST"
    elif any(t in text_l for t in ("disease", "spots", "yellow", "wilt", "leaf", "symptom", "pest")):
        intent = "DISEASE_REPORT"
    elif any(t in text_l for t in ("weather", "rain", "forecast", "go rain", "climate")):
        intent = "WEATHER_INQUIRY"

    location_query = _extract_location_query(text)
    crop = _extract_crop(text)
    amount = _extract_amount(text)
    needs_clarification = intent in {"LOAN_REQUEST", "DISEASE_REPORT"} and not location_query

    return {
        "intent": intent,
        "language": _detect_language(text),
        "parse_confidence": 0.62 if intent != "HUMAN_ESCALATION" else 0.4,
        "location": {
            "landmark": location_query,
            "geocode_query": location_query,
            "needs_clarification": needs_clarification,
            "clarifying_question": "Please reply with the nearest town/village and a nearby market or junction."
            if needs_clarification
            else "",
        },
        "loan": {
            "amount": amount,
            "crop_type": crop,
            "farm_size": "",
            "crop_stage": "",
        },
        "disease": {
            "crop_type": crop,
            "symptoms": text if intent == "DISEASE_REPORT" else "",
        },
        "weather": {
            "question_type": "rainfall" if "rain" in text_l else "forecast",
            "time_horizon_days": 7,
        },
    }


def _to_parsed_data(parsed: dict) -> dict:
    """Flatten nested parser object into workflow `parsed_data` structure.

    Args:
        parsed: Dict shaped like `SMSParseOutput`.

    Returns:
        Flattened dict written into `state["parsed_data"]`.

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


async def sms_parser_node(state: FarmaState) -> dict:
    """Parse SMS into intent and structured fields for downstream routing.

    Request format:
        Reads `state["message"]` and `state["phone"]`.

    Response format:
        Returns updates for `intent`, `language`, `status`, and `parsed_data`.
        May include `pending_question` when additional location detail is needed.

    Status behavior:
        - `READY_FOR_ANALYSIS` when parse is sufficient.
        - `AWAITING_FARMER_RESPONSE` for missing critical fields.
        - `HUMAN_ESCALATION` intent on empty/unsupported input.

    Idempotency and retries:
        Safe to retry with same message; node has no persistent writes.
        Performs up to two Gemini attempts before deterministic fallback parse.

    Args:
        state: Workflow state for an inbound SMS request.

    Returns:
        State update dict used by `graph.intent_gate` and downstream nodes.

    Raises:
        Exception: Propagates unrecoverable translation/model errors.

    Side Effects:
        Emits parse start/completion events.
        Performs Gemini and translation calls over network.

    Latency:
        Dominated by LLM inference; fallback path is fast/local.
    """
    emit_event(
        "parse_intent_started",
        step="parse_intent",
        payload={"phone": state.get("phone")},
    )

    message = _clean(state.get("message"))
    phone = _clean(state.get("phone"))

    if not message:
        # No language detected yet, so default to English
        # The farmer will respond and we'll detect language on next message
        question_english = "Please send your request in one message: loan need, crop disease symptoms, or weather question."
        question = await translate_to_farmer_language(
            question_english,
            "English",  # Default since no message to detect from
            context="initial_prompt",
        )
        emit_event(
            "parse_intent_done",
            status="failed",
            step="parse_intent",
            payload={"error": "empty_message"},
        )
        return {
            "intent": "HUMAN_ESCALATION",
            "status": "AWAITING_FARMER_RESPONSE",
            "pending_question": question,
            "pending_question_type": "clarification",
            "farmer_response": question,
            "parsed_data": {"parse_confidence": 0.0},
        }

    prompt = _build_prompt(phone=phone, message=message)

    last_err: str | None = None
    schema: dict | None = SMS_PARSE_SCHEMA
    parsed: dict | None = None
    for attempt in range(2):
        try:
            obj = await call_json(
                model=MODEL_FLASH,
                prompt=(
                    prompt
                    if attempt == 0
                    else (
                        prompt
                        + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."
                    )
                ),
                thinking_level="low",
                temperature=0.1,
                schema=schema,
                timeout_s=6.5,
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
            last_err = f"llm_call_failed: {msg_e}"
            if attempt == 1:
                parsed = _fallback_parse(message)
                break
            continue

        try:
            parsed = SMSParseOutput.model_validate(obj).model_dump()
        except Exception as e:
            last_err = f"invalid_parser_output: {e}"
            if attempt == 1:
                parsed = _fallback_parse(message)
                break
            continue

        break

    if parsed is None:
        parsed = _fallback_parse(message)

    emit_event(
        "parse_intent_done",
        status="completed",
        step="parse_intent",
        payload={
            "intent": parsed.get("intent"),
            "confidence": parsed.get("parse_confidence"),
            "fallback": bool(last_err),
        },
    )

    parsed_data = _to_parsed_data(parsed)
    location = parsed.get("location") or {}

    # Weather queries work fine with approximate locations (state-level),
    # so only ask for location clarification on intents that need precision.
    intent = parsed.get("intent")
    if (
        intent != "WEATHER_INQUIRY"
        and location.get("needs_clarification")
        and location.get("clarifying_question")
    ):
        q = _clean(location.get("clarifying_question"))[:160]
        return {
            "intent": intent,
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


__all__ = ["sms_parser_node"]
