"""Infographic generation pipeline for AEGIS report visuals.

Purpose:
- Build data-grounded prompts for each infographic type.
- Generate images with Gemini image model and cache artifacts on disk.

Used by:
- `app.aegis.report.nodes.generate_infographics_node`.

Assumptions:
- `GOOGLE_API_KEY` is configured for image generation.
- Cache/output directories are writable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY
from app.aegis.report.cache import CacheKey, InfographicCache
from app.aegis.report.config import ReportDAGConfig
from app.aegis.report.report_data import ReportData


class InfographicType(str, Enum):
    """Supported infographic variants in the report pipeline."""

    situation_overview = "situation_overview"
    displacement_forecast = "displacement_forecast"
    needs_assessment = "needs_assessment"
    risk_heatmap = "risk_heatmap"


@dataclass
class GeneratedInfographic:
    """Metadata describing a generated infographic artifact."""

    infographic_type: InfographicType
    file_path: str
    prompt_used: str
    thinking_summary: str = ""


def _client() -> genai.Client:
    """Create Gemini client for infographic generation."""
    return genai.Client(api_key=GOOGLE_API_KEY)


def _extract_first_image_bytes(resp: Any) -> Optional[bytes]:
    """Extract first inline image payload from Gemini response."""
    try:
        cand = resp.candidates[0]
        for part in cand.content.parts or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
    except Exception:
        return None
    return None


def _extract_text(resp: Any) -> str:
    """Extract text companion output from Gemini response."""
    try:
        return (resp.text or "").strip()
    except Exception:
        return ""


def _prompt_payload(report_data: ReportData) -> Dict[str, Any]:
    """Build sanitized prompt payload from report data.

    Source URIs are stripped to reduce leakage into image prompts.
    """
    total_events, total_fatalities = report_data.totals()

    def _strip_uris(obj: Any) -> Any:
        """Recursively remove URI/source fields from nested payload objects."""
        if isinstance(obj, dict):
            cleaned: Dict[str, Any] = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in {"source_uris", "sources", "uris", "uri"}:
                    continue
                cleaned[k] = _strip_uris(v)
            return cleaned
        if isinstance(obj, list):
            return [_strip_uris(x) for x in obj]
        return obj

    state_summaries = []
    for name, a in report_data.assessments_by_state.items():
        m = a.get("metrics") or {}
        state_summaries.append({
            "state": name,
            "risk_level": a.get("risk_level", "UNKNOWN"),
            "ipc_phase": m.get("ipc_phase", 0),
            "idp_estimate": m.get("idp_estimate", 0),
            "conflict_events": m.get("conflict_events", 0),
            "fatalities": m.get("fatalities", 0),
            "idp_trend": m.get("idp_trend", "stable"),
            "markets_operational": m.get("markets_operational", "unknown"),
        })

    payload: Dict[str, Any] = {
        "scan_id": report_data.scan_id,
        "generated_at": report_data.generated_at,
        "states": report_data.states,
        "state_summaries": state_summaries,
        "rollup": _strip_uris(report_data.rollup),
        "totals": {"events": total_events, "fatalities": total_fatalities},
    }

    if report_data.simulation:
        sim = report_data.simulation
        payload["simulation"] = {
            "projections": sim.get("projections_json"),
            "scenario": sim.get("scenario_json"),
        }

    return payload


def _build_prompt(report_data: ReportData, infographic_type: InfographicType) -> str:
    """Build rich design prompt for a specific infographic type."""
    import json as _json
    payload = _prompt_payload(report_data)
    total_events = payload["totals"]["events"]
    total_fatalities = payload["totals"]["fatalities"]
    states_str = ", ".join(payload["states"])
    date_str = payload["generated_at"][:10]

    # Build a readable state summary for the prompt
    state_lines = []
    for s in payload.get("state_summaries") or []:
        state_lines.append(
            f"  {s['state']}: risk={s['risk_level']}, IPC Phase {s['ipc_phase']}, "
            f"{s['idp_estimate']:,} IDPs ({s['idp_trend']}), "
            f"{s['conflict_events']} conflict events, {s['fatalities']} fatalities"
        )
    state_block = "\n".join(state_lines)

    style_guide = (
        "STYLE REQUIREMENTS:\n"
        "- Professional humanitarian report aesthetic matching UN/OCHA design language\n"
        "- Color palette: primary blue (#009EDB), dark blue (#026CB6), alert red (#CD3A1F), "
        "neutral grays (#333333, #6C757D), white background\n"
        "- Clean sans-serif typography with clear visual hierarchy\n"
        "- High contrast for print readability at A4 size\n"
        "- No external URLs, no watermarks, no stock photography\n"
        "- Output exactly ONE image\n\n"
    )

    if infographic_type == InfographicType.situation_overview:
        return (
            f"{style_guide}"
            f"Create a 'SITUATION OVERVIEW' infographic for Northeast Nigeria ({date_str}).\n\n"
            f"The scene: A single-page dashboard summarizing a humanitarian crisis across {states_str}. "
            f"At the top, a bold title bar reading 'AEGIS Situation Overview — Northeast Nigeria' "
            f"in OCHA blue (#009EDB) with the date below it.\n\n"
            f"LAYOUT (top to bottom):\n"
            f"1. HEADER: Title + date + 'Scan ID: {payload['scan_id']}'\n"
            f"2. KEY FIGURES ROW: Four large KPI cards side by side:\n"
            f"   - '{total_events:,}' labeled 'Conflict Events'\n"
            f"   - '{total_fatalities:,}' labeled 'Fatalities'\n"
            f"   - Total IDPs (sum from data below) labeled 'People Displaced'\n"
            f"   - Worst IPC phase labeled 'Worst Food Crisis Level'\n"
            f"3. MAP: A simplified silhouette map of northeast Nigeria showing states "
            f"colored by risk level: CRITICAL=dark red, HIGH=red, ELEVATED=orange, "
            f"MEDIUM=yellow, LOW=green. Include state name labels on the map.\n"
            f"4. STATE RANKING TABLE: Compact table listing states by severity:\n"
            f"{state_block}\n\n"
            f"Make the KPI numbers very large and prominent. The map should take ~40% of the space.\n"
        )

    if infographic_type == InfographicType.displacement_forecast:
        sim = payload.get("simulation") or {}
        proj = sim.get("projections") or {}
        humanitarian = proj.get("humanitarian") or {} if isinstance(proj, dict) else {}
        idp_delta = humanitarian.get("idp_delta")
        food_mt = humanitarian.get("food_mt")
        funding_gap = humanitarian.get("funding_gap_usd")

        scenario = sim.get("scenario") or {}
        crisis_type = scenario.get("crisis_type", "ongoing conflict") if isinstance(scenario, dict) else "ongoing conflict"

        sim_block = ""
        if idp_delta is not None:
            sim_block = (
                f"\nSIMULATION PROJECTIONS (from AEGIS crisis simulator):\n"
                f"  Crisis type: {crisis_type}\n"
                f"  Projected additional IDPs: {idp_delta:,}\n"
                f"  Food assistance needed: {food_mt or 'N/A'} metric tons\n"
                f"  Estimated funding gap: ${funding_gap:,.0f} USD\n" if funding_gap else ""
            )

        return (
            f"{style_guide}"
            f"Create a 'DISPLACEMENT FORECAST' infographic for Northeast Nigeria ({date_str}).\n\n"
            f"The scene: A forward-looking dashboard showing where displacement is heading. "
            f"This is used by humanitarian planners to pre-position supplies.\n\n"
            f"LAYOUT:\n"
            f"1. HEADER: 'Displacement Forecast — Northeast Nigeria' in OCHA blue\n"
            f"2. CURRENT IDP SNAPSHOT: One card per state showing current IDP count and trend arrow:\n"
            f"{state_block}\n"
            f"   Use upward red arrows for 'increasing', flat yellow arrows for 'stable', "
            f"green downward arrows for 'decreasing'.\n"
            f"3. TREND CHART: A line chart showing projected IDP trajectory over the next "
            f"4-8 weeks for the top 2 highest-risk states. Use dashed lines for projections "
            f"and solid lines for observed data. Include confidence bands.\n"
            f"{sim_block}"
            f"4. If simulation data is available, show a 'PROJECTED IMPACT' box with "
            f"additional IDP displacement, food needs, and funding gap.\n"
            f"5. PLANNING NOTE: A small callout box: 'Pre-position supplies in states with "
            f"upward IDP trends before crisis peaks.'\n"
        )

    if infographic_type == InfographicType.needs_assessment:
        total_idps = sum(
            s.get("idp_estimate", 0) for s in (payload.get("state_summaries") or [])
        )
        worst_ipc = max(
            (s.get("ipc_phase", 0) for s in (payload.get("state_summaries") or [])),
            default=0,
        )
        return (
            f"{style_guide}"
            f"Create a 'NEEDS ASSESSMENT' infographic for Northeast Nigeria ({date_str}).\n\n"
            f"The scene: A dashboard that answers 'What do affected populations need most?' "
            f"Used by WFP, UNHCR, and ICRC to prioritize resource allocation.\n\n"
            f"LAYOUT:\n"
            f"1. HEADER: 'Humanitarian Needs Assessment' in OCHA blue\n"
            f"2. FOUR KPI CARDS in a row, each with a large bold number and small label:\n"
            f"   - '{total_idps:,}' / 'People Displaced'\n"
            f"   - 'Phase {worst_ipc}' / 'Worst IPC Level'\n"
            f"   - '{total_events:,}' / 'Conflict Events'\n"
            f"   - '{total_fatalities:,}' / 'Fatalities Reported'\n"
            f"3. NEEDS MATRIX: A horizontal bar chart showing priority needs by state:\n"
            f"   Categories: Food Security, Shelter, Water/Sanitation, Health, Protection\n"
            f"   Each state gets one row with colored bars showing relative severity.\n"
            f"4. STATE DETAIL:\n"
            f"{state_block}\n"
            f"5. PRIORITY ACTIONS box at the bottom with 3 bullet points:\n"
            f"   - Immediate food distribution to IPC Phase 4+ areas\n"
            f"   - Emergency shelter for newly displaced populations\n"
            f"   - Protection services in conflict-adjacent LGAs\n"
        )

    if infographic_type == InfographicType.risk_heatmap:
        risk_levels = {
            s["state"]: s["risk_level"]
            for s in (payload.get("state_summaries") or [])
        }
        risk_block = "\n".join(
            f"  {st}: {rl}" for st, rl in risk_levels.items()
        )
        return (
            f"{style_guide}"
            f"Create a 'RISK HEATMAP' infographic for Northeast Nigeria ({date_str}).\n\n"
            f"The scene: A map-centered dashboard showing which states are most dangerous "
            f"for both civilians and humanitarian workers. Used for access planning.\n\n"
            f"LAYOUT:\n"
            f"1. HEADER: 'Security Risk Heatmap — Northeast Nigeria' in OCHA blue\n"
            f"2. MAIN MAP: Large silhouette map of northeast Nigeria (Borno, Adamawa, Yobe "
            f"and surrounding states) with each state filled by risk level color:\n"
            f"   CRITICAL = deep red (#8B0000)\n"
            f"   HIGH = red (#CD3A1F)\n"
            f"   ELEVATED = orange (#E67E22)\n"
            f"   MEDIUM = amber (#F39C12)\n"
            f"   LOW = green (#27AE60)\n"
            f"   Risk assignments:\n"
            f"{risk_block}\n"
            f"3. LEGEND: Clear color legend in the bottom-left corner\n"
            f"4. RISK TABLE: Compact table on the right side listing each state with:\n"
            f"   State | Risk Level | Conflict Events | Key Threat\n"
            f"{state_block}\n"
            f"5. ACCESS NOTE: Small callout: 'States at HIGH/CRITICAL require armed escort "
            f"or negotiated access for humanitarian convoys.'\n"
        )

    return (
        f"{style_guide}"
        f"Create a humanitarian infographic of type '{infographic_type.value}' "
        f"for Northeast Nigeria ({date_str}).\n"
        f"Data:\n{state_block}\n"
    )


async def generate_infographic_cached(
    *,
    report_data: ReportData,
    infographic_type: InfographicType,
    config: ReportDAGConfig,
    cache: InfographicCache,
    semaphore: asyncio.Semaphore,
) -> GeneratedInfographic:
    """Generate one infographic with cache lookup/write.

    Args:
        report_data: Report context payload.
        infographic_type: Variant to render.
        config: Report DAG config settings.
        cache: Cache helper for deterministic key lookup.
        semaphore: Concurrency limiter for image generation calls.

    Returns:
        GeneratedInfographic: Metadata for cached or newly generated image.

    Raises:
        RuntimeError: If model response has no image bytes.
        Exception: Can propagate Gemini call failures.

    Side Effects:
        May perform network model call and write image file to cache dir.

    Latency:
        Potentially high due to image generation inference.
    """
    payload = _prompt_payload(report_data)
    payload_hash = cache.compute_payload_hash(payload)
    key = CacheKey(
        scan_id=report_data.scan_id,
        infographic_type=infographic_type.value,
        prompt_version=config.prompt_version,
        aspect_ratio=config.image_aspect_ratio,
        image_size=config.image_size,
        payload_hash=payload_hash,
    )
    cached_path = cache.get_path(key)
    if cached_path.exists():
        return GeneratedInfographic(
            infographic_type=infographic_type,
            file_path=str(cached_path),
            prompt_used="(cached)",
        )

    prompt = _build_prompt(report_data, infographic_type)
    client = _client()

    async with semaphore:
        resp = await client.aio.models.generate_content(
            model=config.image_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=config.image_aspect_ratio,
                    image_size=config.image_size,
                ),
            ),
        )

    image_bytes = _extract_first_image_bytes(resp)
    if not image_bytes:
        raise RuntimeError(f"No image bytes returned for {infographic_type.value}")

    cache.write_bytes(key, image_bytes)
    return GeneratedInfographic(
        infographic_type=infographic_type,
        file_path=str(cached_path),
        prompt_used=prompt,
        thinking_summary=_extract_text(resp),
    )


async def generate_all_infographics(
    *,
    report_data: ReportData,
    config: ReportDAGConfig,
) -> Dict[str, GeneratedInfographic]:
    """Generate all infographic variants concurrently with bounded concurrency."""
    cache = InfographicCache(config.cache_dir)
    sem = asyncio.Semaphore(max(1, int(config.image_concurrency)))

    async def _one(t: InfographicType) -> tuple[str, GeneratedInfographic]:
        """Generate one infographic and return `(type_name, metadata)` tuple."""
        res = await generate_infographic_cached(
            report_data=report_data,
            infographic_type=t,
            config=config,
            cache=cache,
            semaphore=sem,
        )
        return t.value, res

    pairs = await asyncio.gather(
        *[_one(t) for t in InfographicType], return_exceptions=False
    )
    return {k: v for k, v in pairs}
