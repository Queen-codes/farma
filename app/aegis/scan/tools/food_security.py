from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.scan.config import GEMINI_MODEL_GROUNDED, THINKING_LEVEL
from app.aegis.scan.grounding import grounded_call_text, extract_grounding_citations


TOOL_NAME = "search_food_security"

def _parse_kv(answer_text: str) -> dict:
    data: dict = {}
    if not answer_text:
        return data
    for raw in answer_text.splitlines()[:30]:
        line = raw.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().upper().replace(" ", "_")
        data[key] = v.strip()
    return data


async def search_food_security(
    *,
    aclient,
    state: str,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    prompt = f"""Search for current food security and agricultural conditions in {state} State, Nigeria.

At the VERY TOP, include these KEY: VALUE lines (use 'unknown' if not found):
IPC_PHASE: <1-5 or unknown>
FOOD_INSECURITY_LEVEL: <minimal|stressed|crisis|emergency|famine|unknown>
POPULATION_AFFECTED: <integer or unknown>

Search for:

1. IPC CLASSIFICATION (Integrated Food Security Phase):
   - Current IPC phase for {state} (Phase 1-5)
   - Number of people in Crisis (Phase 3) or worse
   - Source: Cadre Harmonisé, FEWS NET, WFP, or government data
   - Which LGAs are in the worst phases?

2. FOOD INSECURITY DRIVERS:
   - What is causing food insecurity? (conflict displacement, drought, high prices, market disruption)
   - How severe is acute food insecurity?

3. MALNUTRITION:
   - Global Acute Malnutrition (GAM) rate if available
   - Severe Acute Malnutrition (SAM) rate if available
   - Which LGAs have critical malnutrition?

4. CROP AND HARVEST CONDITIONS:
   - Current agricultural season (planting, growing, lean season, harvest)
   - How are crops doing? (good, fair, poor, failed)
   - Expected harvest outlook
   - Which crops are affected?

5. AGRICULTURAL CHALLENGES:
   - Drought or flooding impacts
   - Pest outbreaks (locusts, armyworms, etc.)
   - Crop diseases
   - Farmers unable to access fields due to insecurity

6. FOOD AVAILABILITY:
   - Is food available in markets?
   - Where are people getting food? (own production, markets, food aid)

Focus on humanitarian and agricultural data. Output should be plain text (not JSON)."""

    resp = await grounded_call_text(
        aclient=aclient,
        model=GEMINI_MODEL_GROUNDED,
        prompt=prompt,
        thinking_level=THINKING_LEVEL,
        timeout_s=timeout_s,
    )
    extracted = extract_grounding_citations(resp)
    kv = _parse_kv(extracted.get("answer_text", ""))
    ipc_phase = None
    raw_ipc = (kv.get("IPC_PHASE") or "").strip()
    if raw_ipc and raw_ipc.lower() != "unknown":
        digits = "".join(ch for ch in raw_ipc if ch.isdigit())
        if digits:
            try:
                ipc_phase = int(digits)
            except Exception:
                ipc_phase = None

    level = (kv.get("FOOD_INSECURITY_LEVEL") or "unknown").strip().lower()
    if level not in {"minimal", "stressed", "crisis", "emergency", "famine", "unknown"}:
        level = "unknown"

    extracted["data"] = {
        "state": state,
        "category": "food_security",
        "ipc_phase": ipc_phase,
        "food_insecurity_level": level,
        "population_affected": kv.get("POPULATION_AFFECTED") or "unknown",
    }
    return extracted
