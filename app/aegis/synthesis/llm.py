"""Gemini-backed structured generation for synthesis assessments and rollups.

Purpose:
- Build schema-aware Gemini requests for state assessments and scan rollups.
- Validate returned JSON against Pydantic models.
- Enforce URI-whitelist constraints to prevent fabricated citations.

Used by:
- `app.aegis.synthesis.state_worker`.

Assumptions:
- `GOOGLE_API_KEY` is configured.
- Upstream normalization provides allowed URI whitelists.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, Optional

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY

from .schema import ASSESSMENT_SCHEMA, ROLLUP_SCHEMA, Assessment, Rollup


def _thinking_level(level: str) -> Any:
    """Map string thinking level to SDK enum fallback value."""
    lvl = (level or "LOW").upper()
    return getattr(
        types.ThinkingLevel, lvl, getattr(types, "ThinkingLevel", None) or lvl
    )


def _extract_text(resp: Any) -> str:
    """Extract text body from Gemini response object with safe fallbacks."""
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


def _parse_json_response(resp: Any) -> dict:
    """Parse response into a JSON object, using `.parsed` or text fallback."""
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed

    text = _extract_text(resp)
    if not text:
        raise RuntimeError("Model returned empty response text")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise RuntimeError(
            f"Expected JSON object but got {type(obj).__name__}"
        )
    return obj


def _allowed_uri_set(allowed_uris: Iterable[str]) -> set[str]:
    """Normalize iterable of URIs into stripped set."""
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
    """Build SDK config with schema fallback handling across SDK variants."""

    kwargs: dict = {
        "thinking_config": types.ThinkingConfig(
            thinking_level=_thinking_level(thinking_level)
        ),
        "temperature": float(temperature),
        "response_mime_type": "application/json",
    }
    if schema is not None:
        kwargs["response_json_schema"] = schema
    try:
        return types.GenerateContentConfig(**kwargs)
    except Exception:
        if schema is not None:
            kwargs.pop("response_json_schema", None)
            kwargs["response_schema"] = schema
            try:
                return types.GenerateContentConfig(**kwargs)
            except Exception:
                pass
        # fallback: keep JSON mime hint if available
        kwargs.pop("response_json_schema", None)
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
    aclient: Any,
    model: str,
    prompt: str,
    thinking_level: str,
    temperature: float,
    schema: dict | None,
    timeout_s: float,
) -> dict:
    """Execute one Gemini call and parse JSON object response."""
    cfg = _make_config(
        thinking_level=thinking_level, temperature=temperature, schema=schema
    )
    coro = aclient.models.generate_content(model=model, contents=prompt, config=cfg)
    resp = await asyncio.wait_for(coro, timeout=timeout_s)
    return _parse_json_response(resp)


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
    """Generate and validate one state assessment JSON payload.

    Args:
        model: Gemini model name.
        thinking_level: Thinking level string.
        temperature: Sampling temperature.
        timeout_s: Per-call timeout in seconds.
        max_retries: Validation retry count.
        payload: Prompt payload for the state.
        allowed_uris: Citation whitelist.

    Returns:
        dict: Validated assessment JSON.

    Raises:
        RuntimeError: When key requirements are missing or validation never passes.
        Exception: Can propagate Gemini/API failures.

    Side Effects:
        Performs one or more Gemini network calls.

    Latency:
        Model-inference bound with retry overhead.
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    aclient = client.aio

    allowed = _allowed_uri_set(allowed_uris)

    base_rules = (
        "You are AEGIS, an AI humanitarian intelligence analyst for Northeast Nigeria.\n"
        "Your task: produce a structured assessment for ONE state that will feed into\n"
        "a formal humanitarian aid request document (like an OCHA Situation Report).\n\n"
        "Generate ONE JSON object that conforms EXACTLY to the provided schema.\n\n"
        "CONTEXT:\n"
        "- You are analyzing conflict, displacement, food security, and economic data.\n"
        "- The output must be detailed enough for humanitarian organizations (WFP, UNHCR, IOM)\n"
        "  to understand WHO needs help, WHAT they need, WHERE they are, and HOW to reach them.\n"
        "- Each key_finding should be a specific, evidence-backed statement a report writer\n"
        "  could cite with a footnote number, e.g. 'Armed attacks in Maiduguri LGA displaced\n"
        "  an estimated 3,200 people during the reporting period.'\n\n"
        "HARD RULES:\n"
        "1) Do NOT browse.\n"
        "2) Use ONLY source URIs from allowed_uris.\n"
        "3) Every key_findings[i].source_uris must be a non-empty subset of allowed_uris.\n"
        "4) Never invent URLs.\n"
        "5) For metrics.ipc_phase and metrics.idp_estimate, use 0 if unknown.\n"
        "6) metrics.fatalities: total fatalities from conflict events in this state.\n\n"
        "LGA BREAKDOWN (lga_breakdown):\n"
        "- For each LGA that appears in the conflict events data, produce an entry.\n"
        "- population_at_risk: estimate based on displacement + conflict severity.\n"
        "- idp_estimate: estimated IDPs in this LGA (distribute state total proportionally).\n"
        "- needs: list specific needs like 'emergency food', 'shelter', 'medical supplies',\n"
        "  'water/sanitation', 'protection services'. Be specific to each LGA's situation.\n"
        "- access_route: describe how humanitarian convoys can reach this LGA while avoiding\n"
        "  conflict hotspots. Name specific roads, corridors, or staging points where possible.\n"
        "- risk_level: rate the LGA as LOW/MEDIUM/HIGH/CRITICAL based on conflict intensity.\n"
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
    """Generate and validate scan-level rollup JSON payload.

    Args:
        model: Gemini model name.
        thinking_level: Thinking level string.
        temperature: Sampling temperature.
        timeout_s: Per-call timeout in seconds.
        max_retries: Validation retry count.
        payload: Prompt payload aggregating assessments.
        allowed_uris: Citation whitelist.

    Returns:
        dict: Validated rollup JSON.

    Raises:
        RuntimeError: When validation never succeeds.
        Exception: Can propagate Gemini/API failures.

    Side Effects:
        Performs one or more Gemini network calls.

    Latency:
        Model-inference bound with retry overhead.
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    aclient = client.aio

    allowed = _allowed_uri_set(allowed_uris)

    base_rules = (
        "You are AEGIS, an AI humanitarian intelligence analyst for Northeast Nigeria.\n"
        "Your task: produce a cross-state rollup that will serve as the executive overview\n"
        "of a formal humanitarian aid request (OCHA-style Situation Report).\n\n"
        "Generate ONE JSON object that conforms EXACTLY to the provided schema.\n\n"
        "CONTEXT:\n"
        "- You are consolidating per-state assessments into a zonal overview.\n"
        "- overall_summary should read like an OCHA executive brief: concise, authoritative,\n"
        "  covering the most critical humanitarian developments across all states.\n"
        "- rankings should order states by severity of humanitarian need.\n"
        "- allocations should recommend what percentage of humanitarian resources each state\n"
        "  should receive, with a brief justification.\n\n"
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
