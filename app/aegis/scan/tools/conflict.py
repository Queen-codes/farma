from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.scan.config import GEMINI_MODEL_GROUNDED, THINKING_LEVEL
from app.aegis.scan.grounding import grounded_call_text, extract_grounding_citations


TOOL_NAME = "search_conflict_events"

def _parse_pipe_events(text: str, *, state: str) -> list[dict]:
    events: list[dict] = []
    if not text:
        return events
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith(("-", "*")):
            continue
        line = line.lstrip("-*").strip()
        # Expected: DATE | LGA | LOCATION | EVENT_TYPE | FATALITIES_INT | DESCRIPTION
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        date, lga, location, event_type, fatalities_raw, desc = parts[:6]
        try:
            fatalities = int("".join(ch for ch in fatalities_raw if ch.isdigit()) or "0")
        except Exception:
            fatalities = 0
        events.append(
            {
                "date": date,
                "lga": lga or None,
                "location": location,
                "event_type": event_type or "incident",
                "fatalities": fatalities,
                "summary": desc,
                "source": state,  # placeholder; real URIs are stored in conflict_raw.citations
            }
        )
    return events


async def search_conflict_events(
    *,
    aclient,
    state: str,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    prompt = (
        f"Using Google Search, find recent conflict/security incidents in {state}, Nigeria. "
        "Return a concise bullet list of 5-10 key incidents.\n"
        "FORMAT STRICTLY as: DATE | LGA | LOCATION | EVENT_TYPE | FATALITIES_INT | DESCRIPTION\n"
        "Use LGA names where possible. If unknown, write 'Unknown'. For fatalities use 0 if unknown.\n"
        "Do NOT invent sources. Ensure statements are grounded in search results.\n"
        "Output should be plain text (not JSON)."
    )

    resp = await grounded_call_text(
        aclient=aclient,
        model=GEMINI_MODEL_GROUNDED,
        prompt=prompt,
        thinking_level=THINKING_LEVEL,
        timeout_s=timeout_s,
    )
    extracted = extract_grounding_citations(resp)
    events = _parse_pipe_events(extracted.get("answer_text", ""), state=state)
    extracted["data"] = {
        "state": state,
        "category": "conflict",
        "events": events,
        "total_events": len(events),
        "total_fatalities": sum(int(e.get("fatalities") or 0) for e in events),
    }
    return extracted
