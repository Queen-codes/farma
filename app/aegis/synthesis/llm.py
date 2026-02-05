from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, Optional

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY

from .schema import ASSESSMENT_SCHEMA, ROLLUP_SCHEMA, Assessment, Rollup


def _thinking_level(level: str) -> Any:
    lvl = (level or "LOW").upper()
    return getattr(
        types.ThinkingLevel, lvl, getattr(types, "ThinkingLevel", None) or lvl
    )


def _extract_text(resp: Any) -> str:
    t = getattr(resp, "text", None)
    if t:
        return str(t).strip()
    # Fallback: join text parts
    try:
        cand = resp.candidates[0]
        parts = getattr(cand.content, "parts", None) or []
        out = []
        for p in parts:
            txt = getattr(p, "text", None)
            if txt:
                out.append(str(txt))
        return "\n".join(out).strip()
    except Exception:
        return ""


def _allowed_uri_set(allowed_uris: Iterable[str]) -> set[str]:
    return {u for u in allowed_uris if isinstance(u, str) and u.strip()}


def _validate_uris_subset(obj: Any, *, allowed: set[str]) -> Optional[str]:
    """Return error string if any source_uris contains non-allowed values."""
    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "source_uris" and isinstance(v, list):
                    bad = [u for u in v if u not in allowed]
                    if bad:
                        return f"invalid_source_uris: {bad[:5]}"
                err = _validate_uris_subset(v, allowed=allowed)
                if err:
                    return err
        elif isinstance(obj, list):
            for it in obj:
                err = _validate_uris_subset(it, allowed=allowed)
                if err:
                    return err
    except Exception:
        return "uri_validation_error"
    return None


def _make_config(
    *, thinking_level: str, temperature: float, schema: dict | None
) -> types.GenerateContentConfig:

    kwargs: dict = {
        "thinking_config": types.ThinkingConfig(
            thinking_level=_thinking_level(thinking_level)
        ),
        "temperature": float(temperature),
        "response_mime_type": "application/json",
    }
    if schema is not None:
        kwargs["response_schema"] = schema
    try:
        return types.GenerateContentConfig(**kwargs)
    except Exception:
        # fallback: keep JSON mime hint if available
        kwargs.pop("response_schema", None)
        try:
            return types.GenerateContentConfig(**kwargs)
        except Exception:
            return types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_level=_thinking_level(thinking_level)
                ),
                temperature=float(temperature),
            )


async def _call_json(
    *,
    aclient,
    model: str,
    prompt: str,
    thinking_level: str,
    temperature: float,
    schema: dict | None,
    timeout_s: float,
) -> dict:
    cfg = _make_config(
        thinking_level=thinking_level, temperature=temperature, schema=schema
    )
    coro = aclient.models.generate_content(model=model, contents=prompt, config=cfg)
    resp = await asyncio.wait_for(coro, timeout=timeout_s)
    text = _extract_text(resp)
    return json.loads(text)


async def generate_assessment_json(
    *,
    model: str,
    thinking_level: str,
    temperature: float,
    timeout_s: float,
    max_retries: int,
    payload: dict,
    allowed_uris: list[str],
) -> dict:
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    aclient = client.aio

    allowed = _allowed_uri_set(allowed_uris)

    base_rules = (
        "Generate ONE JSON object that conforms EXACTLY to the provided schema.\n"
        "HARD RULES:\n"
        "1) Do NOT browse.\n"
        "2) Use ONLY source URIs from allowed_uris.\n"
        "3) Every key_findings[i].source_uris must be a non-empty subset of allowed_uris.\n"
        "4) Never invent URLs.\n"
        "5) For metrics.ipc_phase and metrics.idp_estimate, use 0 if unknown.\n"
    )
    prompt = base_rules + "\nINPUT (JSON):\n" + json.dumps(payload, ensure_ascii=False)

    last_err: Optional[str] = None
    schema: dict | None = ASSESSMENT_SCHEMA
    for attempt in range(max_retries + 1):
        try:
            obj = await _call_json(
                aclient=aclient,
                model=model,
                prompt=(
                    prompt
                    if attempt == 0
                    else (
                        prompt
                        + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."
                    )
                ),
                thinking_level=thinking_level,
                temperature=temperature,
                schema=schema,
                timeout_s=timeout_s,
            )
        except Exception as e:
            msg = str(e)
            # Gemini API rejects some schema keywords (e.g. additionalProperties).
            if schema is not None and (
                "additionalProperties" in msg
                or "should be non-empty for OBJECT type" in msg
            ):
                schema = None
                last_err = "schema_keyword_unsupported"
                continue
            raise
        # validate schema via pydantic
        assessment = Assessment.model_validate(obj).model_dump()
        uri_err = _validate_uris_subset(assessment, allowed=allowed)
        if uri_err:
            last_err = uri_err
            continue
        return assessment
    raise RuntimeError(last_err or "assessment_validation_failed")


async def generate_rollup_json(
    *,
    model: str,
    thinking_level: str,
    temperature: float,
    timeout_s: float,
    max_retries: int,
    payload: dict,
    allowed_uris: list[str],
) -> dict:
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    aclient = client.aio

    allowed = _allowed_uri_set(allowed_uris)

    base_rules = (
        "Generate ONE JSON object that conforms EXACTLY to the provided schema.\n"
        "HARD RULES:\n"
        "1) Do NOT browse.\n"
        "2) Use ONLY source URIs from allowed_uris.\n"
        "3) Every rankings[i].source_uris must be a non-empty subset of allowed_uris.\n"
        "4) Never invent URLs.\n"
    )
    prompt = base_rules + "\nINPUT (JSON):\n" + json.dumps(payload, ensure_ascii=False)

    last_err: Optional[str] = None
    schema: dict | None = ROLLUP_SCHEMA
    for attempt in range(max_retries + 1):
        try:
            obj = await _call_json(
                aclient=aclient,
                model=model,
                prompt=(
                    prompt
                    if attempt == 0
                    else (
                        prompt
                        + f"\n\nCORRECTION: {last_err}\nReturn corrected JSON only."
                    )
                ),
                thinking_level=thinking_level,
                temperature=temperature,
                schema=schema,
                timeout_s=timeout_s,
            )
        except Exception as e:
            msg = str(e)
            if schema is not None and (
                "additionalProperties" in msg
                or "should be non-empty for OBJECT type" in msg
            ):
                schema = None
                last_err = "schema_keyword_unsupported"
                continue
            raise
        rollup = Rollup.model_validate(obj).model_dump()
        uri_err = _validate_uris_subset(rollup, allowed=allowed)
        if uri_err:
            last_err = uri_err
            continue
        return rollup
    raise RuntimeError(last_err or "rollup_validation_failed")
