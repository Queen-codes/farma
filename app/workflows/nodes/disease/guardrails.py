"""Docstring for app.workflows.nodes.disease.guardrails
This module:
Detects and blocks harmful or dangerous advice, replacing it with a safe fallback message.
Uses thresholds to decide when to ask for more information or prompt clarifying questions.
Flags responses for human verification when risk is high and confidence is low.
Maps analysis results into system states (e.g., AWAITING_FARMER_RESPONSE) to guide conversation flow.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.workflows.job_events import emit_event
from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.state import FarmaState


_UNSAFE_PHRASES = [
    "drink",
    "bleach",
    "kerosene",
    "diesel",
    "petrol",
    "battery acid",
    "acid",
]


def _contains_unsafe(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _UNSAFE_PHRASES)


async def disease_guardrails(state: FarmaState) -> dict:
    """Deterministic policy checks + state machine mapping for DISEASE_REPORT and includes LLM-based translation for all farmer-facing messages."""
    emit_event("disease_guardrails_started", step="disease_guardrails")

    analysis = state.get("disease_analysis") or {}
    confidence = float(analysis.get("confidence") or 0.0)
    risk_score = float(analysis.get("risk_score") or 0.0)
    needs_more_info = bool(analysis.get("needs_more_info") or False)
    clarifying = (analysis.get("clarifying_question") or "").strip()

    # Get detected language for translation
    language = state.get("language") or "English"

    risk_flags: List[str] = list(state.get("risk_flags") or [])
    treatment_steps = analysis.get("treatment_steps") or []
    sms_text = (state.get("sms_text") or state.get("farmer_response") or "").strip()

    unsafe = False
    if _contains_unsafe(sms_text):
        unsafe = True
    for step in treatment_steps:
        if _contains_unsafe(str(step)):
            unsafe = True
            break

    if unsafe:
        risk_flags.append("UNSAFE_ADVICE_REMOVED")
        # Translate safety message to farmer's language
        safe_sms_english = "Avoid harsh chemicals. Remove affected leaves, improve spacing, and use approved pesticide only with label instructions."
        safe_sms = await translate_to_farmer_language(
            safe_sms_english,
            language,
            context="disease_safety",
        )
        sms_text = safe_sms[:160]

    status = state.get("status") or "READY_FOR_ANALYSIS"
    pending_q = None
    pending_q_type = None

    if confidence < 0.55 or needs_more_info:
        status = "AWAITING_FARMER_RESPONSE"
        # Use LLM-generated clarifying question if available, otherwise translate fallback
        if clarifying:
            pending_q = clarifying
        else:
            fallback_english = "What crop is it, and what changes do you see on the leaves (spots, yellowing, holes)?"
            pending_q = await translate_to_farmer_language(
                fallback_english,
                language,
                context="disease_clarification",
            )
        pending_q_type = "disease"
        sms_text = pending_q[:160]

    if risk_score >= 0.8 and confidence < 0.65:
        status = "NEEDS_HUMAN_VERIFICATION"
        risk_flags.append("HIGH_DISEASE_RISK")
        if not sms_text:
            verification_english = (
                "Your case needs local expert verification. A field officer should "
                "inspect the crop before treatment."
            )
            sms_text = await translate_to_farmer_language(
                verification_english,
                language,
                context="disease_verification",
            )
            sms_text = sms_text[:160]

    emit_event(
        "disease_guardrails_completed",
        status="completed",
        step="disease_guardrails",
        payload={"status": status, "unsafe": unsafe},
    )

    out: Dict[str, Any] = {
        "status": status,
        "risk_flags": list(dict.fromkeys(risk_flags)),
        "sms_text": sms_text,
        "farmer_response": sms_text if sms_text else None,
    }
    if status == "AWAITING_FARMER_RESPONSE":
        out["pending_question"] = pending_q
        out["pending_question_type"] = pending_q_type
        out["farmer_response"] = pending_q
    return out
