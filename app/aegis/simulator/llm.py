"""Gemini helper for simulation policy-brief generation.

Purpose:
- Convert deterministic projection outputs into narrative recommendations.
- Validate response against policy-brief schema.
- Filter citations to URI whitelist.

Used by:
- `app.aegis.simulator.nodes.generate_policy_brief_node`.

Assumptions:
- `GOOGLE_API_KEY` is configured.
- Projection math is completed upstream; this module does not recompute it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY, GEMINI_MODEL_SYNTH
from app.aegis.simulator.schema import POLICY_BRIEF_SCHEMA, PolicyBrief


def _extract_text(resp: object) -> str:
    """Extract combined text from Gemini response object."""
    t = getattr(resp, "text", None)
    if t:
        return str(t).strip()
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


def _parse_json_response(resp: object) -> dict:
    """Parse response object into JSON dict using parsed/text fallbacks."""
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


def _filter_uris(policy_brief: dict, whitelist: List[str]) -> dict:
    """Drop any recommendation URIs not present in whitelist."""
    wl = set(whitelist)
    for rec in policy_brief.get("ranked_recommendations") or []:
        rec["source_uris"] = [u for u in (rec.get("source_uris") or []) if u in wl]
    return policy_brief


async def generate_policy_brief(
    *,
    scan_id: int,
    simulation_id: str,
    scenario: dict,
    projections: dict,
    uri_whitelist: List[str],
    config: Optional[dict] = None,
) -> Tuple[dict, str]:
    """Generate schema-validated policy brief from simulation context.

    Args:
        scan_id: Source scan ID.
        simulation_id: Simulation identifier.
        scenario: Scenario payload.
        projections: Deterministic projection payload.
        uri_whitelist: Allowed citation URIs.
        config: Optional runtime model/thinking settings.

    Returns:
        Tuple[dict, str]: `(policy_brief_json, schema_mode)` pair.

    Raises:
        RuntimeError: If API key missing or response invalid after bounded retry.
        Exception: Can propagate model/SDK failures.

    Side Effects:
        Makes one or two Gemini API calls.

    Latency:
        Model-inference bound.
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")

    cfg = config or {}
    model = str(cfg.get("model") or GEMINI_MODEL_SYNTH or "gemini-3-flash-preview")
    thinking_level_low = str(cfg.get("thinking_level") or "LOW").upper()

    prompt = (
        "You are AEGIS Crisis Simulator.\n"
        "Your job is to turn deterministic projections into a partner-ready policy brief.\n"
        "Return JSON ONLY matching the schema.\n\n"
        "HARD RULES:\n"
        "1) Do NOT browse the web.\n"
        "2) Do NOT redo the math.\n"
        "3) You may only cite source_uris from uri_whitelist.\n"
        "4) Keep it concise.\n\n"
        f"SCAN_ID: {scan_id}\n"
        f"SIMULATION_ID: {simulation_id}\n"
        f"URI_WHITELIST: {json.dumps(uri_whitelist, ensure_ascii=False)}\n"
        f"SCENARIO_JSON: {json.dumps(scenario, ensure_ascii=False)}\n"
        f"PROJECTIONS_JSON: {json.dumps(projections, ensure_ascii=False)}\n"
    )

    client = genai.Client(api_key=GOOGLE_API_KEY)

    async def _call(
        *, schema: Optional[dict], thinking_level: str
    ) -> Tuple[object, str]:
        """Execute one model call using optional schema-constrained output."""
        cfg_kwargs: dict = {
            "thinking_config": types.ThinkingConfig(thinking_level=thinking_level),
            "temperature": 0.2,
            "response_mime_type": "application/json",
        }
        schema_mode = "json_only"
        if schema is not None:
            cfg_kwargs["response_json_schema"] = schema
            schema_mode = "schema"
        try:
            gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
        except Exception:
            if schema is not None:
                cfg_kwargs.pop("response_json_schema", None)
                cfg_kwargs["response_schema"] = schema
                try:
                    gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
                except Exception:
                    cfg_kwargs.pop("response_schema", None)
                    gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
                    schema_mode = "json_only"
            else:
                gen_cfg = types.GenerateContentConfig(**cfg_kwargs)
        resp = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=gen_cfg,
        )
        return resp, schema_mode

    resp, schema_mode = await _call(
        schema=POLICY_BRIEF_SCHEMA, thinking_level=thinking_level_low
    )
    obj = _parse_json_response(resp)
    try:
        brief = PolicyBrief.model_validate(obj).model_dump()
    except Exception:
        # bounded retry once with MEDIUM, json-only
        resp2, schema_mode2 = await _call(schema=None, thinking_level="MEDIUM")
        obj2 = _parse_json_response(resp2)
        brief = PolicyBrief.model_validate(obj2).model_dump()
        schema_mode = schema_mode2

    brief = _filter_uris(brief, uri_whitelist)
    return brief, schema_mode
