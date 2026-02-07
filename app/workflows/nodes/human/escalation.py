"""Human escalation node with Gemini triage and LangGraph interrupts.

This module handles requests that cannot be safely auto-resolved. It:
- classifies escalation severity/category,
- sends a translated acknowledgement to the farmer,
- pauses graph execution via `interrupt(...)` until an agent responds.

Used by:
- `app.workflows.graph` when intent routes to the human branch or when other
  nodes set verification-required status.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

from langgraph.types import interrupt

from app.config import GOOGLE_API_KEY, MODEL_FLASH
from app.workflows.job_events import emit_event
from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.state import FarmaState

logger = logging.getLogger(__name__)


# Gemini triage — classify severity + draft response

_TRIAGE_PROMPT = """\
You are a FARMA triage agent for a Nigerian agricultural SMS service.
Classify this farmer request that could not be handled automatically.

FARMER MESSAGE: {message}
DETECTED INTENT: {intent}
LANGUAGE: {language}
PARSED DATA: {parsed_data}
RISK FLAGS: {risk_flags}
CURRENT STATUS: {status}

Return a JSON object with exactly these fields:
- severity: one of "low", "medium", "high", "critical"
- category: one of "UNRECOGNIZED_INTENT", "FIELD_VERIFICATION", "HIGH_RISK_CASE", "COMPLEX_INQUIRY", "SAFETY_CONCERN"
- reason: 1 sentence explaining why this needs human attention
- draft_response: A short suggested response (in English) for the human agent to review and send to the farmer. Keep under 160 characters.

Return JSON only, no markdown fences.
"""


async def _triage_with_gemini(state: FarmaState) -> Dict[str, Any]:
    """Classify escalation severity/category using Gemini with safe fallback.

    Args:
        state: Current workflow state.

    Returns:
        Triage dict with `severity`, `category`, `reason`, and `draft_response`.

    Raises:
        None: Any model failure falls back to deterministic triage.

    Side Effects:
        May call Gemini API over the network.

    Latency:
        Dominated by one LLM inference call when API key is configured.
    """
    fallback = _deterministic_triage(state)

    if not GOOGLE_API_KEY:
        return fallback

    try:
        from google import genai
        from google.genai import types

        prompt = _TRIAGE_PROMPT.format(
            message=state.get("message") or "(no message)",
            intent=state.get("intent") or "UNKNOWN",
            language=state.get("language") or "English",
            parsed_data=json.dumps(state.get("parsed_data") or {}, ensure_ascii=False),
            risk_flags=json.dumps(
                list(state.get("risk_flags") or []), ensure_ascii=False
            ),
            status=state.get("status") or "UNKNOWN",
        )

        client = genai.Client(api_key=GOOGLE_API_KEY)
        resp = await client.aio.models.generate_content(
            model=MODEL_FLASH,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        text = (resp.text or "").strip()
        obj = json.loads(text)
        return {
            "severity": str(obj.get("severity", "medium")),
            "category": str(obj.get("category", "COMPLEX_INQUIRY")),
            "reason": str(obj.get("reason", "Requires human review")),
            "draft_response": str(
                obj.get("draft_response", fallback["draft_response"])
            ),
        }
    except Exception as exc:
        logger.warning("Gemini triage failed, using deterministic fallback: %s", exc)
        return fallback


def _deterministic_triage(state: FarmaState) -> Dict[str, Any]:
    """Build rule-based escalation class when LLM triage is unavailable.

    Args:
        state: Current workflow state.

    Returns:
        Conservative triage payload suitable for manual review queueing.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time local checks.
    """
    status = state.get("status") or ""
    risk_flags = list(state.get("risk_flags") or [])
    intent = state.get("intent") or "UNKNOWN"

    severity = "medium"
    category = "COMPLEX_INQUIRY"

    if status == "NEEDS_HUMAN_VERIFICATION":
        category = "FIELD_VERIFICATION"
        severity = "high"
    elif any(f in risk_flags for f in ("SYSTEM_ERROR", "SCORING_ERROR")):
        category = "HIGH_RISK_CASE"
        severity = "high"
    elif any(f in risk_flags for f in ("UNSAFE_ADVICE_REMOVED", "HIGH_DISEASE_RISK")):
        category = "SAFETY_CONCERN"
        severity = "critical"
    elif intent == "HUMAN_ESCALATION":
        category = "UNRECOGNIZED_INTENT"
        severity = "low"

    return {
        "severity": severity,
        "category": category,
        "reason": f"Escalated from status={status}, intent={intent}",
        "draft_response": "We are looking into your request. A FARMA agent will follow up shortly.",
    }


# Farmer acknowledgment
async def _build_acknowledgment(state: FarmaState, triage: Dict[str, Any]) -> str:
    """Create translated acknowledgement SMS sent before human handoff.

    Args:
        state: Current workflow state, including detected language.
        triage: Triage payload for context (currently unused for wording).

    Returns:
        SMS text trimmed to 160 characters.

    Raises:
        None: Translation failures degrade gracefully to English.

    Side Effects:
        May call translation helper/LLM service.

    Latency:
        Dominated by translation network call for non-English languages.
    """
    ref = uuid.uuid4().hex[:6].upper()
    english = (
        f"Your request has been forwarded to a FARMA field officer. "
        f"You'll hear back within 24 hours. Ref: {ref}"
    )

    language = (state.get("language") or "English").strip().lower()
    if language in ("english", ""):
        return english[:160]

    try:
        translated = await translate_to_farmer_language(
            english,
            state.get("language") or "English",
            context="escalation_acknowledgment",
        )
        return translated[:160]
    except Exception:
        return english[:160]


# Main node
async def human_escalation_handler(state: FarmaState) -> dict:
    """Run human handoff flow and pause/resume workflow around agent action.

    Args:
        state: Current workflow state requiring manual intervention.

    Returns:
        Dict with final `farmer_response`, completion status, and escalation
        context after graph resumes from interrupt.

    Raises:
        Exception: Propagates unexpected runtime failures to runner.

    Side Effects:
        Emits escalation telemetry events.
        Performs Gemini triage and translation calls.
        Calls LangGraph `interrupt(...)`, pausing execution until resume input.

    Latency:
        Triage/translation calls are network-bound; end-to-end latency can be
        long because it includes human response waiting time.
    """
    emit_event("human_escalation_started", step="human")

    # 1. Gemini triage — classify severity + draft response
    triage = await _triage_with_gemini(state)

    # 2. send farmer acknowledgment (translated to their language)
    ack_msg = await _build_acknowledgment(state, triage)

    # 3. Build escalation context for human agent dashboard
    escalation = {
        "type": triage["category"],
        "severity": triage["severity"],
        "reason": triage["reason"],
        "draft_response": triage["draft_response"],
        "ack_message": ack_msg,
        "farmer_phone": state.get("phone"),
        "farmer_message": state.get("message"),
        "intent": state.get("intent"),
        "language": state.get("language"),
        "parsed_data": state.get("parsed_data"),
    }

    # 4. Emit triage result + ack for frontend / job event stream.
    #    The ack_message is surfaced in the interrupt payload so the runner
    #    can deliver it to the farmer while the graph is paused.
    emit_event(
        "human_escalation_triaged",
        step="human",
        status="running",
        payload=escalation,
    )

    # 5. Interrupt — graph pauses here. MemorySaver stores the checkpoint.
    #    The value passed to interrupt() is surfaced in the API so the human
    #    agent sees the escalation context + draft response.
    #    When resumed, interrupt() returns the human agent's response text.
    human_response = interrupt(escalation)

    # 6. After resumption — human_response is the agent's reply text
    emit_event(
        "human_escalation_resolved",
        step="human",
        status="completed",
        payload={"human_response": human_response},
    )

    return {
        "farmer_response": str(human_response),
        "status": "COMPLETED",
        "human_task": escalation,
        "analysis_summary": [
            f"Human agent responded to {triage['category']} escalation"
        ],
    }


__all__ = ["human_escalation_handler"]
