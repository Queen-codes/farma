"""Disease analysis module for FARMA.

This module uses Gemini Flash to diagnose crop diseases based on farmer-provided symptoms.
It employs a structured output approach with retry logic and schema fallback.
"""

from __future__ import annotations

from typing import Any, List
from pydantic import BaseModel, Field

from app.config import MODEL_FLASH
from app.workflows.gemini_async import call_json
from app.workflows.job_events import emit_event
from app.workflows.language_utils import translate_to_farmer_language
from app.workflows.state import FarmaState


class DiseaseAnalysisOutput(BaseModel):
    disease_name: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    symptoms_matched: List[str] = Field(default_factory=list, max_length=8)
    treatment_steps: List[str] = Field(default_factory=list, max_length=8)
    risk_score: float = Field(ge=0.0, le=1.0)
    needs_more_info: bool = False
    clarifying_question: str = ""
    sms_160: str = ""


# The JSON schema for the model class.
DISEASE_SCHEMA = DiseaseAnalysisOutput.model_json_schema()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clip_sms(text: str, limit: int = 160) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _build_prompt(*, language: str, crop: str, symptoms: str, message: str) -> str:
    return (
        "You are FARMA, a Nigerian farm extension officer.\n"
        "Task: Based on the farmer message, identify likely crop disease/pest and give practical low-cost steps.\n"
        "Return JSON ONLY matching the schema.\n\n"
        "Rules:\n"
        "- Be honest about uncertainty.\n"
        "- Treatment must be low-cost, locally available, and safe.\n"
        "- Do NOT suggest drinking chemicals, using fuel on crops, or any illegal actions.\n"
        "- sms_160 must be <=160 characters and in the farmer's language/dialect.\n\n"
        f"LANGUAGE: {language}\n"
        f"CROP (if known): {crop}\n"
        f"SYMPTOMS (if known): {symptoms}\n"
        f"FARMER MESSAGE: {message}\n"
    )


async def disease_generate_once(state: FarmaState) -> dict:
    """One Gemini Flash call for DISEASE_REPORT."""
    emit_event("disease_started", step="disease_generate")

    message = _clean(state.get("message"))
    language = _clean(state.get("language")) or "English"
    parsed = state.get("parsed_data") or {}
    crop = _clean(parsed.get("disease_crop_type") or parsed.get("crop_type"))
    symptoms = _clean(parsed.get("symptoms"))

    if not message and not symptoms:
        # Translate the clarifying question to farmer's language
        question_english = "Please tell me the crop and what you see on the leaves/stem (spots, yellowing, holes, or wilting)."
        question = await translate_to_farmer_language(
            question_english,
            language,
            context="disease_initial_clarification",
        )
        emit_event(
            "disease_completed",
            status="completed",
            step="disease_generate",
            payload={"confidence": 0.0, "needs_more_info": True},
        )
        return {
            "disease_analysis": {
                "disease_name": "",
                "confidence": 0.0,
                "symptoms_matched": [],
                "treatment_steps": [],
                "risk_score": 0.0,
                "needs_more_info": True,
                "clarifying_question": question,
                "sms_160": _clip_sms(question),
            },
            "analysis_summary": ["Need more symptom detail before diagnosis."],
            "sms_text": _clip_sms(question),
        }

    prompt = _build_prompt(
        language=language,
        crop=crop,
        symptoms=symptoms,
        message=message,
    )
    # attempt api call twice
    # start with full disease schema and track error btw attempts
    last_err: str | None = None
    schema: dict | None = DISEASE_SCHEMA
    for attempt in range(2):
        try:
            # first api call attempt
            obj = await call_json(
                model=MODEL_FLASH,
                # on first attempt, use original prompt with full schema
                # on second attempt, add error correction to prompt
                prompt=(
                    prompt
                    if attempt == 0
                    else (
                        prompt
                        + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."
                    )
                ),
                thinking_level="low",
                temperature=0.2,
                schema=schema,
                timeout_s=7.0,
            )
            # schema fallback in case of issues with pydantic schema to allow to return json and allow for manual validation
        except Exception as e:
            msg_e = str(e)
            if schema is not None and (
                "additionalProperties" in msg_e
                or "should be non-empty for OBJECT type" in msg_e
            ):
                # disable strict schema validation and retry w/o schema constraints
                schema = None
                last_err = "schema_unsupported"
                continue
            raise
        # manual validation after api is successful
        try:
            # validate json schema matches DISEASEANALYSISOUTPUT
            out = DiseaseAnalysisOutput.model_validate(obj).model_dump()
        except Exception as e:
            last_err = f"invalid_disease_output: {e}"
            continue

        out["sms_160"] = _clip_sms(out.get("sms_160") or "")
        out["clarifying_question"] = _clean(out.get("clarifying_question"))
        out["treatment_steps"] = [
            _clean(s) for s in out.get("treatment_steps") or [] if _clean(s)
        ]
        out["symptoms_matched"] = [
            _clean(s) for s in out.get("symptoms_matched") or [] if _clean(s)
        ]

        emit_event(
            "disease_completed",
            status="completed",
            step="disease_generate",
            payload={
                "confidence": out.get("confidence"),
                "risk_score": out.get("risk_score"),
            },
        )

        summary = []
        if out.get("disease_name"):
            summary.append(
                f"Likely issue: {out['disease_name']} (conf {out.get('confidence'):.2f})"
            )
        if out.get("treatment_steps"):
            summary.append(f"Top step: {out['treatment_steps'][0]}")

        return {
            "disease_analysis": out,
            "analysis_summary": summary,
            "sms_text": out.get("sms_160") or "",
        }

    raise RuntimeError(last_err or "disease_parse_failed")
