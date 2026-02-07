"""Loan underwriting node combining parser, geocode, satellite, and AEGIS data.

This module produces the final loan decision payload used for farmer messaging.
Primary path uses Gemini structured output with schema validation; deterministic
fallback logic is used when LLM output is unavailable/invalid.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.job_events import emit_event
from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.loan_schemas import DECISION_SCHEMA, LoanDecisionOutput
from app.workflows.state import FarmaState

logger = logging.getLogger(__name__)


def _compact(obj: Any, max_chars: int = 4000) -> str:
    """Serialize nested evidence safely for prompt inclusion.

    Args:
        obj: Arbitrary object to serialize.
        max_chars: Maximum prompt-safe character length.

    Returns:
        JSON string when possible; otherwise `str(obj)`, truncated as needed.

    Raises:
        None: Serialization errors fall back to `str`.

    Side Effects:
        None.

    Latency:
        Local serialization only; depends on object size.
    """
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_chars:
        return s[: max_chars - 20] + "...(truncated)"
    return s


async def _fallback_underwriting_decision(
    *,
    parsed: dict,
    existing_flags: List[str],
    language: str,
    policy: dict,
) -> dict:
    """Generate deterministic underwriting result when LLM path fails.

    Args:
        parsed: Parsed farmer request fields.
        existing_flags: Current risk flags accumulated by prior nodes.
        language: Farmer language for translated prompts.
        policy: Underwriting threshold/term configuration.

    Returns:
        Loan decision dict aligned with `LoanDecisionOutput` shape.

    Raises:
        Exception: Propagates translation helper failures.

    Side Effects:
        May call translation helper/LLM service for farmer-facing text.

    Latency:
        Mostly local logic; includes translation call latency when needed.
    """
    amount_raw = parsed.get("amount") or 0
    try:
        requested_amount = int(float(amount_raw))
    except Exception:
        requested_amount = 0

    risky_flags = {"SYSTEM_ERROR", "SCORING_ERROR", "LOCATION_VAGUE", "LOCATION_REVIEW_REQUIRED"}
    has_high_risk = any(flag in risky_flags for flag in existing_flags)

    if requested_amount <= 0:
        question = await translate_to_farmer_language(
            "Please reply with the amount of loan you need in naira and your nearest town/market.",
            language,
            context="loan_verification",
        )
        return {
            "decision": "HOLD_FOR_VERIFICATION",
            "approved_amount": 0,
            "terms": {
                "grace_days": policy["verification_grace_days"],
                "tenor_days": policy["standard_tenor_days"],
                "repayment_schedule": "weekly",
                "requires_field_verification": True,
            },
            "reasoning": ["Loan amount missing or unclear."],
            "risk_flags": list(dict.fromkeys(existing_flags + ["MISSING_LOAN_AMOUNT"])),
            "follow_up_questions": [question[:160]],
            "sms_160": question[:160],
        }

    if has_high_risk:
        question = await translate_to_farmer_language(
            "We need one more location detail before disbursement. Reply with your ward/village and nearest market.",
            language,
            context="loan_verification",
        )
        return {
            "decision": "HOLD_FOR_VERIFICATION",
            "approved_amount": 0,
            "terms": {
                "grace_days": policy["verification_grace_days"],
                "tenor_days": policy["standard_tenor_days"],
                "repayment_schedule": "weekly",
                "requires_field_verification": True,
            },
            "reasoning": ["Risk flags require human/location verification."],
            "risk_flags": list(dict.fromkeys(existing_flags + ["VERIFICATION_REQUIRED"])),
            "follow_up_questions": [question[:160]],
            "sms_160": question[:160],
        }

    approved = min(max(requested_amount, policy["min_amount_naira"]), policy["max_amount_naira"])
    sms = await translate_to_farmer_language(
        f"Pre-approved: N{approved:,}. Reply YES to continue verification and disbursement steps.",
        language,
        context="loan_status",
    )
    return {
        "decision": "APPROVE_SMALL",
        "approved_amount": int(approved),
        "terms": {
            "grace_days": policy["standard_grace_days"],
            "tenor_days": policy["standard_tenor_days"],
            "repayment_schedule": "monthly",
            "requires_field_verification": False,
        },
        "reasoning": ["Deterministic fallback underwriting used due temporary LLM unavailability."],
        "risk_flags": existing_flags,
        "follow_up_questions": [],
        "sms_160": sms[:160],
    }


async def loan_underwriter_node(state: FarmaState) -> dict:
    """Produce final loan decision, terms, and farmer-facing response.

    Args:
        state: Workflow state with parsed request, geocode, satellite report,
            AEGIS context, and existing risk flags.

    Returns:
        Dict containing decision outputs (`final_decision`, `approved_amount`,
        `loan_terms`, `risk_flags`) plus status/pending-question transitions.

    Raises:
        Exception: Propagates unrecoverable errors after fallback attempts.

    Side Effects:
        Emits underwriting lifecycle events.
        Performs one or two Gemini calls for structured decisioning.
        May call translation helper in fallback path.

    Latency:
        Dominated by LLM inference and response validation/retry.
    """
    emit_event("underwriting_started", step="underwriting")

    parsed = state.get("parsed_data") or {}
    geo = state.get("geocode_provenance") or {}
    sat = state.get("satellite_report") or {}
    aegis = state.get("aegis_context") or {}
    # Read existing flags for the LLM prompt context only
    existing_flags: List[str] = list(dict.fromkeys((state.get("risk_flags") or [])))

    language = state.get("language") or "English"

    policy = {
        "max_sms_chars": 160,
        "min_amount_naira": 10000,
        "max_amount_naira": 500000,
        "standard_grace_days": 30,
        "standard_tenor_days": 180,
        "crisis_grace_days": 90,
        "verification_grace_days": 0,
    }

    prompt = (
        "You are FARMA, a Nigerian agricultural credit officer.\n"
        "You must make a loan decision under uncertainty using the provided evidence.\n"
        "Return JSON ONLY matching the schema.\n\n"
        "HARD RULES:\n"
        "1) Do NOT browse.\n"
        "2) If location confidence is low OR satellite evidence is missing/contradictory, choose HOLD_FOR_VERIFICATION.\n"
        "3) Follow-up questions must be answerable by SMS (no rainfall requests, no GPS-only requirements).\n"
        "4) sms_160 must be <=160 characters and in the farmer's language/dialect.\n"
        "5) Keep reasoning to max 6 short bullets.\n\n"
        f"LANGUAGE: {language}\n"
        f"POLICY: {_compact(policy)}\n"
        f"PARSED_REQUEST: {_compact(parsed)}\n"
        f"GEOCODE_PROVENANCE: {_compact(geo)}\n"
        f"SATELLITE_EVIDENCE: {_compact(sat)}\n"
        f"AEGIS_CONTEXT: {_compact(aegis)}\n"
        f"EXISTING_RISK_FLAGS: {_compact(existing_flags)}\n"
    )

    last_err: str | None = None
    schema: dict | None = DECISION_SCHEMA
    decision: dict | None = None
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
                timeout_s=8.5,
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
            last_err = f"llm_underwriter_failed: {msg_e}"
            if attempt == 1:
                decision = await _fallback_underwriting_decision(
                    parsed=parsed,
                    existing_flags=existing_flags,
                    language=language,
                    policy=policy,
                )
                break
            continue

        # Manual validation with fallback for constraint violations
        try:
            decision = LoanDecisionOutput.model_validate(obj).model_dump()
        except Exception as e:
            # Log the full LLM output for debugging
            logger.warning("[UNDERWRITER] Validation error: %s", e)
            logger.info("[UNDERWRITER] LLM output: %s", json.dumps(obj, indent=2))

            # Check if it's a list length violation we can fix
            if "too_long" in str(e) and "follow_up_questions" in str(e):
                # Truncate follow_up_questions to max 4
                if isinstance(obj.get("follow_up_questions"), list):
                    obj["follow_up_questions"] = obj["follow_up_questions"][:4]
                    logger.info(
                        "[UNDERWRITER] Auto-truncated follow_up_questions to 4 items"
                    )
                    try:
                        decision = LoanDecisionOutput.model_validate(obj).model_dump()
                        logger.info(
                            "[UNDERWRITER] Validation succeeded after truncation"
                        )
                    except Exception as e2:
                        last_err = f"invalid_loan_decision_after_truncate: {e2}"
                        continue
                else:
                    last_err = f"invalid_loan_decision: {e}"
                    continue
            else:
                last_err = f"invalid_loan_decision: {e}"
                if attempt == 1:
                    decision = await _fallback_underwriting_decision(
                        parsed=parsed,
                        existing_flags=existing_flags,
                        language=language,
                        policy=policy,
                    )
                    break
                continue
        if decision:
            break

    if not decision:
        decision = await _fallback_underwriting_decision(
            parsed=parsed,
            existing_flags=existing_flags,
            language=language,
            policy=policy,
        )
        # Only return NEW flags from LLM; operator.add in state merges with existing
    new_flags = decision.get("risk_flags") or []

    emit_event(
        "underwriting_done",
        status="completed",
        step="underwriting",
        payload={
            "decision": decision.get("decision"),
            "approved_amount": decision.get("approved_amount"),
            "fallback": bool(last_err),
        },
    )

    status = "COMPLETED"
    farmer_response = decision.get("sms_160") or ""
    pending_question = None
    pending_question_type = None
    human_task = None

    if decision.get("decision") == "HOLD_FOR_VERIFICATION":
        status = "AWAITING_FARMER_RESPONSE"
        qs = decision.get("follow_up_questions") or []
        if qs:
            pending_question = qs[0]
            pending_question_type = "verification"
            farmer_response = qs[0]

    if decision.get("terms", {}).get("requires_field_verification"):
        status = "NEEDS_HUMAN_VERIFICATION"
        human_task = {
            "type": "FIELD_VERIFICATION",
            "reason": "Location or risk uncertainty requires human verification before disbursement.",
            "suggested_action": "Verify farm location and identity via local agent/community leader.",
        }

    return {
        "final_decision": decision.get("decision"),
        "approved_amount": decision.get("approved_amount"),
        "loan_terms": decision.get("terms"),
        "risk_flags": new_flags,
        "analysis_summary": decision.get("reasoning") or [],
        "farmer_response": farmer_response,
        "status": status,
        "pending_question": pending_question,
        "pending_question_type": pending_question_type,
        "human_task": human_task,
    }


__all__ = ["loan_underwriter_node"]
