"""Async Gemini JSON-call helper utilities shared across workflow nodes.

Purpose:
- Centralize Gemini client reuse, config construction, and JSON parsing.
- Provide one bounded async helper (`call_json`) for structured outputs.

Used by:
- Parser, disease, loan-underwriter, climate-advisory, and translation modules.

Assumptions:
- `GOOGLE_API_KEY` is configured in runtime environment.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY


_CLIENT: genai.Client | None = None


def _get_client() -> genai.Client:
    """Return cached Gemini client instance, creating it lazily if needed.

    Raises:
        RuntimeError: If API key is not configured.
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("Missing GOOGLE_API_KEY")

    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=GOOGLE_API_KEY)
    return _CLIENT


def _thinking_level(level: str) -> str:
    """Normalize thinking level to lowercase as per Gemini API documentation.

    Valid levels: "none", "low", "medium", "high"
    - Gemini 3 Flash supports all four levels
    - Gemini 3 Pro supports "low" and "high"
    """
    normalized = (level or "low").lower().strip()
    valid_levels = {"none", "low", "medium", "high"}

    if normalized not in valid_levels:
        return "low"

    return normalized


def _extract_text(resp: Any) -> str:
    """Extract plain text from Gemini response with candidate fallback."""
    text = getattr(resp, "text", None)
    if text:
        return str(text).strip()

    # Fallback for SDK responses where `.text` is empty.
    try:
        candidates = getattr(resp, "candidates", [])
        if not candidates:
            return ""
        cand = candidates[0]
        parts = getattr(cand.content, "parts", []) or []
        return "\n".join(p.text for p in parts if hasattr(p, "text")).strip()
    except Exception:
        return ""


def _parse_json_response(resp: Any) -> dict:
    """Parse Gemini response into JSON object with robust error messages."""
    # Preferred path for structured outputs in google-genai.
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed

    text = _extract_text(resp)
    if not text:
        raise RuntimeError("Model returned empty response text")

    try:
        obj = json.loads(text)
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse model response as JSON: {e}. Raw output: {text}"
        ) from e

    if not isinstance(obj, dict):
        raise RuntimeError(
            f"Expected JSON object but got {type(obj).__name__}: {obj}"
        )
    return obj


def _make_config(
    *,
    thinking_level: str,
    temperature: float,
    schema: Optional[dict],
) -> types.GenerateContentConfig:
    """Build GenerateContentConfig with proper error handling.

    Tries response_json_schema first (preferred), then response_schema as fallback.
    Raises clear errors if schema is fundamentally invalid.
    """
    kwargs: dict = {
        "thinking_config": types.ThinkingConfig(thinking_level=_thinking_level(thinking_level)),
        "temperature": temperature,
        "response_mime_type": "application/json",
    }

    if schema is not None:
        kwargs["response_json_schema"] = schema

    try:
        return types.GenerateContentConfig(**kwargs)
    except Exception as e:
        error_msg = str(e)

        # Compatibility fallback for SDK variants that only accept `response_schema`.
        if schema is not None and "response_json_schema" in error_msg:
            kwargs.pop("response_json_schema", None)
            kwargs["response_schema"] = schema
            try:
                return types.GenerateContentConfig(**kwargs)
            except Exception as e2:
                # If both schema parameters fail, raise clear error
                raise ValueError(
                    f"Schema validation failed with both response_json_schema and response_schema. "
                    f"Check your Pydantic model definition. Original errors: {error_msg}, {str(e2)}"
                ) from e2

        # If schema-related error but no schema provided, or other config error
        if schema is None:
            # No schema, so error is likely with thinking_config or temperature
            raise ValueError(
                f"GenerateContentConfig creation failed: {error_msg}. "
                f"Check thinking_level (got: {thinking_level}) and temperature (got: {temperature})"
            ) from e

        # Schema is provided but something else is wrong with it
        raise ValueError(
            f"Schema configuration error: {error_msg}. "
            f"Ensure your schema is valid JSON Schema format."
        ) from e


async def call_json(
    *,
    model: str,
    prompt: str = "",
    contents: Any | None = None,
    thinking_level: str = "low",
    temperature: float = 0.2,
    schema: Optional[dict] = None,
    timeout_s: float = 8.0,
) -> dict:
    """Call Gemini asynchronously and return a validated JSON object."""
    client = _get_client()
    payload = contents if contents is not None else prompt
    if payload is None or (isinstance(payload, str) and not payload.strip()):
        raise RuntimeError("Gemini call requires non-empty prompt or contents")

    config = _make_config(
        thinking_level=thinking_level,
        temperature=temperature,
        schema=schema,
    )

    coro = client.aio.models.generate_content(
        model=model,
        contents=payload,
        config=config,
    )

    resp = await asyncio.wait_for(coro, timeout=timeout_s)
    return _parse_json_response(resp)
