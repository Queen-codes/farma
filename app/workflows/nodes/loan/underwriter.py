"""
Docstring for app.workflows.nodes.loan.underwriter
Responsible for Making the final loan decision using all accumulated evidence using Gemini Call.
"""

from __future__ import annotations

import json
from typing import Any, List

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.job_events import emit_event
from app.workflows.loan_schemas import DECISION_SCHEMA, LoanDecisionOutput
from app.workflows.state import FarmaState


def _compact(obj: Any, max_chars: int = 4000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_chars:
        return s[: max_chars - 20] + "...(truncated)"
    return s


async def loan_underwriter_node(state: FarmaState) -> dict:
    """final underwriting + terms + SMS + follow-ups."""
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
            raise

        # Manual validation with fallback for constraint violations
        try:
            decision = LoanDecisionOutput.model_validate(obj).model_dump()
        except Exception as e:
            import json
            # Log the full LLM output for debugging
            print(f"[UNDERWRITER] Validation error: {e}")
            print(f"[UNDERWRITER] LLM output: {json.dumps(obj, indent=2)}")

            # Check if it's a list length violation we can fix
            if "too_long" in str(e) and "follow_up_questions" in str(e):
                # Truncate follow_up_questions to max 4
                if isinstance(obj.get("follow_up_questions"), list):
                    obj["follow_up_questions"] = obj["follow_up_questions"][:4]
                    print(f"[UNDERWRITER] Auto-truncated follow_up_questions to 4 items")
                    try:
                        decision = LoanDecisionOutput.model_validate(obj).model_dump()
                        print(f"[UNDERWRITER] Validation succeeded after truncation")
                    except Exception as e2:
                        last_err = f"invalid_loan_decision_after_truncate: {e2}"
                        continue
                else:
                    last_err = f"invalid_loan_decision: {e}"
                    continue
            else:
                last_err = f"invalid_loan_decision: {e}"
                continue
        # Only return NEW flags from LLM; operator.add in state merges with existing
        new_flags = decision.get("risk_flags") or []

        emit_event(
            "underwriting_done",
            status="completed",
            step="underwriting",
            payload={
                "decision": decision.get("decision"),
                "approved_amount": decision.get("approved_amount"),
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

    raise RuntimeError("underwriting_failed")


__all__ = ["loan_underwriter_node"]
