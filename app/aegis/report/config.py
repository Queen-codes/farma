from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "reports"


@dataclass(frozen=True)
class ReportDAGConfig:
    narrative_mode: str = "template"  # "template" | "llm"
    thinking_level: str = "LOW"
    temperature: float = 0.2

    image_model: str = "gemini-3-pro-image-preview"
    image_aspect_ratio: str = "16:9"
    image_size: str = "2K"
    image_concurrency: int = 2

    cache_dir: Path = REPORTS_DIR / "_aegis_cache" / "infographics"
    prompt_version: str = "v1"

