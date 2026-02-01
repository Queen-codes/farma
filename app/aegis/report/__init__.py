"""AEGIS Report Generator - Phase 3.

Generates professional humanitarian PDF reports from synthesis analysis.

Components:
- data_extractor: Parse synthesis agent output
- narrative: Gemini 3 Pro narrative generation
- infographics: Nano Banana Pro infographic generation
- pdf_builder: PDF assembly with reportlab
- agent: Main orchestrator
"""

from .agent import run_report_generation, ReportGeneratorState, get_report_summary
from .data_extractor import extract_report_data, ReportData
from .narrative import generate_narrative, NarrativeSections
from .infographics import generate_infographic, InfographicType
from .pdf_builder import build_pdf, PDFConfig

__all__ = [
    # Main entry point
    "run_report_generation",
    "ReportGeneratorState",
    "get_report_summary",
    # Data extraction
    "extract_report_data",
    "ReportData",
    # Narrative generation
    "generate_narrative",
    "NarrativeSections",
    # Infographic generation
    "generate_infographic",
    "InfographicType",
    # PDF building
    "build_pdf",
    "PDFConfig",
]
