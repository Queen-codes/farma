"""Grounded Gemini call helpers for web-evidence extraction.

Purpose:
- Execute Gemini calls with Google Search grounding enabled.
- Normalize grounded citations/spans into stable dict payloads consumed by
  scan tools and persistence layers.

Used by:
- `app.aegis.scan.tools.*` modules.

Assumptions:
- Caller passes an authenticated Gemini client.
- Response structure follows google-genai candidate/grounding schema.
"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Dict, List, Optional

from google.genai import types


def safe_json_extract(answer_text: str) -> Optional[dict]:
    """Parse a JSON object from free-form model text when possible.

    Args:
        answer_text: Raw model response text.

    Returns:
        Optional[dict]: Parsed JSON object or `None` if parsing fails.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Fast in-memory parsing/string scanning.
    """
    if not answer_text:
        return None
    answer_text = answer_text.strip()
    if not answer_text:
        return None
    # direct json first
    try:
        return json.loads(answer_text)
    except Exception:
        pass
    # extract jSON object substring
    start = answer_text.find("{")
    end = answer_text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(answer_text[start : end + 1])
        except Exception:
            return None
    return None


async def grounded_call_text(
    *,
    aclient: Any,
    model: str,
    prompt: str,
    thinking_level: str = "LOW",
    timeout_s: Optional[float] = None,
) -> Any:
    """Grounded Gemini call using Google Search (Gemini API, google-genai SDK).

    Args:
        aclient: Authenticated Gemini client wrapper.
        model: Grounded model identifier.
        prompt: Prompt text sent to model.
        thinking_level: Gemini thinking level.
        timeout_s: Optional timeout in seconds.

    Returns:
        Any: Raw Gemini response object.

    Raises:
        asyncio.TimeoutError: If timeout elapses before response.
        Exception: Can propagate network/SDK/model errors.

    Side Effects:
        Performs network call to Gemini API with Google Search tool enabled.

    Latency:
        Network and model-inference bound.
    """
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(
            thinking_level=thinking_level,
        ),
    )

    coro = aclient.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )
    if timeout_s and timeout_s > 0:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    return await coro


def extract_grounding_citations(response: Any) -> Dict[str, Any]:
    """Extract report-grade grounding citations and spans from a grounded response.

    Args:
        response: Raw Gemini grounded response object.

    Returns:
        Dict[str, Any]: Normalized payload with answer text, source list, span
        list, optional `data`, and error field.

    Raises:
        Does not raise intentionally; parse failures are returned in `errors`.

    Side Effects:
        None.

    Latency:
        Linear in number of grounding chunks/support spans.
    """
    errors: Optional[str] = None
    answer_text = ""
    sources: List[Dict[str, Any]] = []
    spans: List[Dict[str, Any]] = []

    try:
        candidate = None
        if getattr(response, "candidates", None):
            candidate = response.candidates[0]

        # Extract answer text
        if candidate and getattr(candidate, "content", None):
            parts = getattr(candidate.content, "parts", None) or []
            texts: List[str] = []
            for p in parts:
                t = getattr(p, "text", None)
                if t:
                    texts.append(str(t))
            answer_text = "\n".join(texts).strip()

        grounding = candidate.grounding_metadata if candidate else None
        chunks = getattr(grounding, "grounding_chunks", None)
        supports = getattr(grounding, "grounding_supports", None)

        if not chunks or not supports:

            errors = "missing_grounding_metadata"
            return {
                "answer_text": answer_text,
                "sources": [],
                "spans": [],
                "data": None,
                "errors": errors,
            }

        # Sources
        for i, ch in enumerate(chunks):
            web = getattr(ch, "web", None)
            uri = getattr(web, "uri", None) if web else None
            title = getattr(web, "title", None) if web else None
            if uri:
                sources.append({"idx": i, "uri": str(uri), "title": str(title or "")})

        # Spans: supports map text segments to chunk indices
        for sup in supports:
            seg = getattr(sup, "segment", None)
            if not seg:
                continue
            start = getattr(seg, "start_index", None)
            end = getattr(seg, "end_index", None)
            text = getattr(seg, "text", None)
            chunk_indices = getattr(sup, "grounding_chunk_indices", None) or []

            uris = []
            for idx in chunk_indices:
                if isinstance(idx, int) and 0 <= idx < len(sources):
                    uris.append(sources[idx]["uri"])
            spans.append(
                {
                    "start": int(start or 0),
                    "end": int(end or 0),
                    "text": str(text or ""),
                    "chunk_indices": list(chunk_indices),
                    "uris": uris,
                }
            )

        return {
            "answer_text": answer_text,
            "sources": sources,
            "spans": spans,
            "data": None,
            "errors": None,
        }
    except Exception as e:
        return {
            "answer_text": answer_text,
            "sources": sources,
            "spans": spans,
            "data": None,
            "errors": f"extract_error: {e}",
        }
