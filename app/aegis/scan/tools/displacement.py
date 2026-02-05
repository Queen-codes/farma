from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.scan.config import GEMINI_MODEL_GROUNDED, THINKING_LEVEL
from app.aegis.scan.grounding import grounded_call_text, extract_grounding_citations


TOOL_NAME = "search_displacement"

def _parse_kv(answer_text: str) -> dict:
    data: dict = {}
    if not answer_text:
        return data
    for raw in answer_text.splitlines()[:25]:
        line = raw.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().upper().replace(" ", "_")
        data[key] = v.strip()
    return data


async def search_displacement(
    *,
    aclient,
    state: str,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    prompt = f"""Search for current information on internally displaced persons (IDPs) in {state} State, Nigeria.

At the VERY TOP, include these KEY: VALUE lines (use 'unknown' if not found):
IDP_ESTIMATE: <integer or unknown>
IDP_TREND: <increasing|stable|decreasing|unknown>
FLEEING_FROM_LGAS: <comma-separated or unknown>
FLEEING_TO_LGAS: <comma-separated or unknown>

Search for:

1. IDP NUMBERS:
   - Current IDP estimates from UNHCR, IOM, NEMA, DTM, or state government
   - Is displacement increasing, stable, or decreasing?

2. CAMPS AND SETTLEMENTS:
   - Names of active IDP camps or settlements
   - Population figures for camps

3. DISPLACEMENT MOVEMENTS:
   - Which LGAs are people fleeing FROM?
   - Which LGAs are people fleeing TO?
   - What's causing recent displacement?

4. HUMANITARIAN SITUATION:
   - Key humanitarian needs (food, water, shelter, medical, protection)
   - IPC food security phase (Phase 1-5)
   - Malnutrition data

5. ACCESS CONSTRAINTS:
   - Security constraints for humanitarian organizations

Output should be plain text (not JSON)"""

    resp = await grounded_call_text(
        aclient=aclient,
        model=GEMINI_MODEL_GROUNDED,
        prompt=prompt,
        thinking_level=THINKING_LEVEL,
        timeout_s=timeout_s,
    )
    extracted = extract_grounding_citations(resp)
    kv = _parse_kv(extracted.get("answer_text", ""))
    idp_estimate = None
    raw_est = (kv.get("IDP_ESTIMATE") or "").strip()
    if raw_est and raw_est.lower() != "unknown":
        digits = "".join(ch for ch in raw_est if ch.isdigit())
        if digits:
            try:
                idp_estimate = int(digits)
            except Exception:
                idp_estimate = None
    idp_trend = (kv.get("IDP_TREND") or "unknown").strip().lower()
    if idp_trend not in {"increasing", "stable", "decreasing", "unknown"}:
        idp_trend = "unknown"

    extracted["data"] = {
        "state": state,
        "category": "displacement",
        "idp_estimate": idp_estimate,
        "idp_trend": idp_trend,
        "fleeing_from_lgas": kv.get("FLEEING_FROM_LGAS") or "unknown",
        "fleeing_to_lgas": kv.get("FLEEING_TO_LGAS") or "unknown",
    }
    return extracted
