"""Configuration defaults for report narrative/image generation.

Purpose:
- Define output directories and model defaults for narrative and infographic stages.

Used by:
- `app.aegis.report.nodes` and `app.aegis.report.infographics`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "reports"


@dataclass(frozen=True)
class ReportDAGConfig:
    """Runtime knobs for report narrative and infographic generation."""

    narrative_mode: str = "llm"  # "template" | "llm"
    narrative_model: str = "gemini-3-flash-preview"
    thinking_level: str = "low"
    temperature: float = 0.3

    image_model: str = os.getenv("GEMINI_MODEL_REPORT_IMAGE", "gemini-3-pro-preview")
    image_fallback_model: str = os.getenv(
        "GEMINI_MODEL_REPORT_IMAGE_FALLBACK", "gemini-3-pro-image-preview"
    )
    image_aspect_ratio: str = os.getenv("GEMINI_REPORT_IMAGE_ASPECT_RATIO", "16:9")
    image_size: str = os.getenv("GEMINI_REPORT_IMAGE_SIZE", "2K")
    image_concurrency: int = 2

    cache_dir: Path = REPORTS_DIR / "_aegis_cache" / "infographics"
    prompt_version: str = "v2"
