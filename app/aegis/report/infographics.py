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
    situation_overview = "situation_overview"
    displacement_forecast = "displacement_forecast"
    needs_assessment = "needs_assessment"
    risk_heatmap = "risk_heatmap"


@dataclass
class GeneratedInfographic:
    infographic_type: InfographicType
    file_path: str
    prompt_used: str
    thinking_summary: str = ""


def _client() -> genai.Client:
    return genai.Client(api_key=GOOGLE_API_KEY)


def _extract_first_image_bytes(resp: Any) -> Optional[bytes]:
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
    try:
        return (resp.text or "").strip()
    except Exception:
        return ""


def _prompt_payload(report_data: ReportData) -> Dict[str, Any]:
    total_events, total_fatalities = report_data.totals()

    # Infographics must not embed URLs. Strip any source_uris or uri-like fields.
    def _strip_uris(obj: Any) -> Any:
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

    return {
        "scan_id": report_data.scan_id,
        "generated_at": report_data.generated_at,
        "states": report_data.states,
        "rollup": _strip_uris(report_data.rollup),
        "totals": {"events": total_events, "fatalities": total_fatalities},
    }


def _build_prompt(report_data: ReportData, infographic_type: InfographicType) -> str:
    payload = _prompt_payload(report_data)
    # keep prompts compact and deterministic; visuals are handled by the image model.
    # TODO: REVIEW AND LOCK-IN PROMPT VIA AI STUDIO TESTING
    base = (
        "You are generating a professional humanitarian infographic for AEGIS (Nigeria).\n"
        "Style: UN/OCHA report aesthetic, clean layout, high contrast, print-ready.\n"
        "Do not include any external URLs in the image.\n"
        "Output ONE image only.\n\n"
        f"INPUT DATA (JSON): {payload}\n\n"
    )

    if infographic_type == InfographicType.situation_overview:
        return (
            base
            + "Create a 'Situation Overview' infographic summarizing displacement + food security risk.\n"
            "Include: title, date, total IDPs (from rollup if available), and a small ranked list of top 3 highest-risk states.\n"
            "Include a simple Nigeria northeast map silhouette with highlighted states (no precise coordinates).\n"
        )
    if infographic_type == InfographicType.displacement_forecast:
        return (
            base + "Create a 'Displacement Forecast' infographic.\n"
            "Include: a small timeline chart (4-8 weeks) showing projected IDP trend direction per top 2 states.\n"
            "If forecast numbers are unavailable, show directional arrows and confidence.\n"
        )
    if infographic_type == InfographicType.needs_assessment:
        return (
            base + "Create a 'Needs Assessment' infographic.\n"
            "Include: 4 KPI cards (IDPs, IPC phase, market disruption, conflict events) and a priority needs bullet list.\n"
        )
    if infographic_type == InfographicType.risk_heatmap:
        return (
            base + "Create a 'Risk Heatmap' infographic.\n"
            "Include: a state-level heatmap (not LGA) colored by risk_level; include legend.\n"
            "Include a compact table of top 5 risk states with short rationales.\n"
        )
    return base + f"Create an infographic of type: {infographic_type.value}"


async def generate_infographic_cached(
    *,
    report_data: ReportData,
    infographic_type: InfographicType,
    config: ReportDAGConfig,
    cache: InfographicCache,
    semaphore: asyncio.Semaphore,
) -> GeneratedInfographic:
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
    cache = InfographicCache(config.cache_dir)
    sem = asyncio.Semaphore(max(1, int(config.image_concurrency)))

    async def _one(t: InfographicType) -> tuple[str, GeneratedInfographic]:
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
