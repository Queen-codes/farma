"""Gemini helper for marathon continuity-note generation with replay context.

Purpose:
- Generate structured continuity notes from deterministic day-over-day deltas.
- Replay prior model content and preserve thought signatures for continuity.
- Enforce URI whitelist filtering and schema validation.

Used by:
- `app.aegis.marathon.nodes.generate_continuity_note_node`.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from app.config import GEMINI_MODEL_SYNTH, GOOGLE_API_KEY
from app.aegis.marathon.schema import CONTINUITY_NOTE_SCHEMA, ContinuityNote


# serialization convert bytes to json for easy jsonb storage in posgres
def _safe_dump(obj: Any) -> Any:
    """Convert objects to JSON-safe format (bytes → base64 markers)."""
    if obj is None:
        return None
    if isinstance(obj, memoryview):
        return {
            "__type__": "bytes",
            "__b64__": base64.b64encode(obj.tobytes()).decode("ascii"),
        }
    if isinstance(obj, (bytes, bytearray)):
        return {
            "__type__": "bytes",
            "__b64__": base64.b64encode(bytes(obj)).decode("ascii"),
        }
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_safe_dump(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_dump(v) for k, v in obj.items()}
    md = getattr(obj, "model_dump", None)
    if callable(md):
        try:
            return md()
        except Exception:
            pass
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        return {str(k): _safe_dump(v) for k, v in d.items()}
    return str(obj)


def _restore_bytes(obj: Any) -> Any:
    """Reverse _safe_dump for bytes markers (JSONB → google-genai types)."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_restore_bytes(x) for x in obj]
    if isinstance(obj, dict):
        if obj.get("__type__") == "bytes" and "__b64__" in obj:
            try:
                return base64.b64decode(str(obj["__b64__"]))
            except Exception:
                return b""
        return {k: _restore_bytes(v) for k, v in obj.items()}
    return obj


# parsr response
def _extract_text(resp: object) -> str:
    """Extract text response from Gemini object with part fallback."""
    t = getattr(resp, "text", None)
    if t:
        return str(t).strip()
    try:
        parts = getattr(resp.candidates[0].content, "parts", None) or []
        return "\n".join(
            str(getattr(p, "text", "")) for p in parts if getattr(p, "text", None)
        ).strip()
    except Exception:
        return ""


def _parse_json_response(resp: object) -> dict:
    """Parse Gemini response into JSON dict via parsed/text fallback."""
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    text = _extract_text(resp)
    if not text:
        raise RuntimeError("Model returned empty response text")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object but got {type(obj).__name__}")
    return obj


def _extract_thought_signature(content: Any) -> Optional[str]:
    """Extract and normalize thought signature to base64 string."""
    parts = getattr(content, "parts", None) or []
    for p in parts:
        ts = getattr(p, "thought_signature", None) or getattr(
            p, "thoughtSignature", None
        )
        if ts is None:
            continue
        if isinstance(ts, memoryview):
            return base64.b64encode(ts.tobytes()).decode("ascii")
        if isinstance(ts, (bytes, bytearray)):
            return base64.b64encode(bytes(ts)).decode("ascii")
        return str(ts)
    return None


def _filter_uris(note: dict, whitelist: List[str]) -> dict:
    """Filter note citation lists to whitelist URIs only."""
    wl = set(whitelist)
    for kc in note.get("key_changes") or []:
        kc["source_uris"] = [u for u in (kc.get("source_uris") or []) if u in wl]
    for ra in note.get("recommended_actions") or []:
        ra["source_uris"] = [u for u in (ra.get("source_uris") or []) if u in wl]
    return note


# main entry point
async def generate_continuity_note(
    *,
    track_id: str,
    day_date: str,
    scan_id: int,
    prev_scan_id: Optional[int],
    delta_json: dict,
    uri_whitelist: List[str],
    prior_model_content_json: Optional[dict],
    prev_continuity_note: Optional[dict] = None,
    effective_thinking_level: str = "low",
    config: Optional[dict] = None,
) -> Tuple[dict, Optional[str], dict, str]:
    """One Gemini call producing ContinuityNote JSON + thought signature.

    Returns: (note_json, thought_signature, model_content_json, schema_mode)
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")

    cfg = config or {}
    model = str(cfg.get("model") or GEMINI_MODEL_SYNTH)

    # Build self-correction context from previous day's predictions
    self_correction_block = ""
    if prev_continuity_note:
        prev_predictions = prev_continuity_note.get("predictions") or []
        prev_summary = prev_continuity_note.get("summary") or ""
        if prev_predictions:
            self_correction_block = (
                "\n\nYESTERDAY'S PREDICTIONS (compare with today's data and self-correct):\n"
                + "\n".join(f"- {p}" for p in prev_predictions)
                + f"\n\nYESTERDAY'S SUMMARY: {prev_summary}"
            )

    prompt = (
        "You are AEGIS Marathon Agent — an autonomous intelligence continuity system.\n"
        "Write a continuity note based ONLY on the provided delta JSON and whitelisted sources.\n"
        "Return JSON ONLY matching the schema.\n\n"
        "HARD RULES:\n"
        "1) Do NOT browse the web.\n"
        "2) source_uris MUST be chosen from uri_whitelist only.\n"
        "3) Keep key_changes <= 6 and recommended_actions <= 5.\n"
        "4) Make 2-4 concrete predictions about what will happen next.\n"
        "   These predictions will be checked against tomorrow's data.\n"
        "5) If yesterday's predictions are provided, compare them with today's data.\n"
        "   Add corrections to self_corrections if any prediction was wrong.\n"
        "6) Recommend a next_thinking_level for tomorrow:\n"
        '   - "high" if the situation is novel or complex\n'
        '   - "medium" if corrections were needed or escalation detected\n'
        '   - "low" if the situation is stable\n'
        "7) Write a decision_explanation (1-3 sentences, first person) explaining:\n"
        "   - What you observed in today's data\n"
        "   - Why you chose these actions and predictions\n"
        '   - Example: "I detected a sharp escalation in Borno (ELEVATED to CRITICAL) with IDP '
        "numbers doubling. I am recommending a simulation to model the displacement "
        'trajectory and flagging this for an emergency report."\n\n'
        f"TRACK_ID: {track_id}\n"
        f"DAY_DATE: {day_date}\n"
        f"SCAN_ID: {scan_id}\n"
        f"PREV_SCAN_ID: {prev_scan_id or 'none (first day)'}\n"
        f"URI_WHITELIST: {json.dumps(uri_whitelist, ensure_ascii=False)}\n"
        f"DELTA_JSON: {json.dumps(delta_json, ensure_ascii=False)}\n"
        f"{self_correction_block}"
    )

    client = genai.Client(api_key=GOOGLE_API_KEY)

    async def _call(
        *, schema: Optional[dict], thinking_level: str
    ) -> Tuple[object, str]:
        """Execute one continuity-note model call with optional schema constraints."""
        cfg_kwargs: Dict[str, Any] = {
            "thinking_config": types.ThinkingConfig(thinking_level=thinking_level),
            "temperature": 0.2,
            "response_mime_type": "application/json",
        }
        schema_mode = "json_only"
        if schema is not None:
            try:
                cfg_kwargs["response_json_schema"] = schema
                schema_mode = "schema"
                gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
            except Exception:
                cfg_kwargs.pop("response_json_schema", None)
                gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
                schema_mode = "json_only"
        else:
            gen_cfg = types.GenerateContentConfig(**cfg_kwargs)

        contents: List[Any] = []
        if prior_model_content_json:
            try:
                prior = types.Content.model_validate(
                    _restore_bytes(prior_model_content_json)
                )
                contents.append(prior)
            except Exception:
                contents.append(
                    types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text=json.dumps(
                                    prior_model_content_json, ensure_ascii=False
                                )
                            )
                        ],
                    )
                )

        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=gen_cfg,
        )
        return resp, schema_mode

    # Attempt 1: schema + effective thinking level
    resp, schema_mode = await _call(
        schema=CONTINUITY_NOTE_SCHEMA, thinking_level=effective_thinking_level
    )

    obj = _parse_json_response(resp)
    try:
        note = ContinuityNote.model_validate(obj).model_dump()
    except Exception:
        # Retry with json_only + medium thinking if schema caused the failure
        resp2, schema_mode2 = await _call(schema=None, thinking_level="medium")
        obj2 = _parse_json_response(resp2)
        note = ContinuityNote.model_validate(obj2).model_dump()
        resp = resp2
        schema_mode = schema_mode2

    note = _filter_uris(note, uri_whitelist)

    # Extract thought signature + model content for next-day replay
    cand = resp.candidates[0] if getattr(resp, "candidates", None) else None
    content = getattr(cand, "content", None) if cand else None
    thought_signature = _extract_thought_signature(content) if content else None
    model_content_json = _safe_dump(content) if content else {}

    return note, thought_signature, model_content_json, schema_mode
