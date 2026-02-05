from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.scan.config import GEMINI_MODEL_GROUNDED, THINKING_LEVEL
from app.aegis.scan.grounding import grounded_call_text, extract_grounding_citations


TOOL_NAME = "search_economic_indicators"

def _parse_kv(answer_text: str) -> dict:
    data: dict = {}
    if not answer_text:
        return data
    for raw in answer_text.splitlines()[:20]:
        line = raw.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().upper().replace(" ", "_")
        data[key] = v.strip()
    return data


async def search_economic_indicators(
    *,
    aclient,
    state: str,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    prompt = f"""Search for current economic and market data in {state} State, Nigeria.

At the VERY TOP, include this KEY: VALUE line (use 'unknown' if not found):
MARKETS_OPERATIONAL: <fully|partially|closed|unknown>

Search for:

1. MARKET ACCESS:
   - Are major markets operational, partially open, or closed?
   - Which specific markets are closed or disrupted?
   - What reasons are given? (security, roads, flooding)

2. COMMODITY PRICES in {state}:
   - Maize/corn: price per kg or bag
   - Rice: price per kg or bag
   - Sorghum/guinea corn: price if available
   - Beans/cowpea: price if available
   - Any another commodity price that's frequently purchased and prices if available
   - Note the source and date of price data
   - Are prices increasing, decreasing, or stable?

3. CURRENCY & INFLATION:
   - Current food inflation rate
   - Naira purchasing power observations

4. AGRICULTURAL SITUATION:
   - Are farmers able to work their fields?
   - Reports of abandoned farms due to insecurity
   - Harvest situation

5. FOOD ASSISTANCE:
   - Ongoing food aid operations
   - Active humanitarian organizations

Output should be plain text (not JSON)."""

    resp = await grounded_call_text(
        aclient=aclient,
        model=GEMINI_MODEL_GROUNDED,
        prompt=prompt,
        thinking_level=THINKING_LEVEL,
        timeout_s=timeout_s,
    )
    extracted = extract_grounding_citations(resp)
    kv = _parse_kv(extracted.get("answer_text", ""))
    markets = (kv.get("MARKETS_OPERATIONAL") or "unknown").strip().lower()
    if markets not in {"fully", "partially", "closed", "unknown"}:
        markets = "unknown"

    extracted["data"] = {
        "state": state,
        "category": "economic",
        "markets_operational": markets,
    }
    return extracted
