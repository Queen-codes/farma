"""PDF rendering utilities for AEGIS humanitarian situation reports.

Purpose:
- Render branded OCHA-style PDF document from narrative and aggregated data.
- Embed optional infographic assets and annex sections.

Used by:
- `app.aegis.report.nodes.build_pdf_node`.

Assumptions:
- ReportLab is installed and output path is writable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    PageBreak,
    HRFlowable,
)

from app.aegis.report.narrative import NarrativeSections
from app.aegis.report.report_data import ReportData

logger = logging.getLogger(__name__)


# OCHA brand palette (primarily/ to be changed to products own brand colors after)
OCHA_BLUE = colors.HexColor("#009EDB")
OCHA_DARK_BLUE = colors.HexColor("#026CB6")
OCHA_RED = colors.HexColor("#CD3A1F")
DARK_TEXT = colors.HexColor("#333333")
LIGHT_GRAY = colors.HexColor("#F2F2F2")
MEDIUM_GRAY = colors.HexColor("#6C757D")
WHITE = colors.white


# Font — Roboto with Helvetica fallback

_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"


def _try_register_roboto() -> bool:
    """Attempt to register Roboto font family; fall back if unavailable."""
    search_paths = [
        Path.home() / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/usr/share/fonts/truetype/roboto"),
        Path.home() / ".fonts",
        Path(__file__).resolve().parents[3] / "assets" / "fonts",
    ]
    for base in search_paths:
        regular = base / "Roboto-Regular.ttf"
        bold = base / "Roboto-Bold.ttf"
        if regular.exists() and bold.exists():
            try:
                pdfmetrics.registerFont(TTFont("Roboto", str(regular)))
                pdfmetrics.registerFont(TTFont("Roboto-Bold", str(bold)))
                return True
            except Exception:
                continue
    return False


_ROBOTO_AVAILABLE = _try_register_roboto()
if _ROBOTO_AVAILABLE:
    _FONT_REGULAR = "Roboto"
    _FONT_BOLD = "Roboto-Bold"
    logger.info("PDF: Using Roboto font family")
else:
    logger.info("PDF: Roboto not found, using Helvetica fallback")


@dataclass
class PDFConfig:
    output_path: str
    include_infographics: bool = True
    include_annexes: bool = True


# Styles — OCHA brand hierarchy(just for demo sake)
def _styles() -> Dict[str, ParagraphStyle]:
    """Return named paragraph style map used by PDF sections."""
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            fontName=_FONT_BOLD,
            fontSize=28,
            textColor=OCHA_BLUE,
            alignment=TA_CENTER,
            spaceAfter=8,
            leading=34,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName=_FONT_REGULAR,
            fontSize=12,
            textColor=MEDIUM_GRAY,
            alignment=TA_CENTER,
            spaceAfter=20,
            leading=16,
        ),
        "h1": ParagraphStyle(
            "H1",
            fontName=_FONT_BOLD,
            fontSize=22,
            textColor=OCHA_BLUE,
            spaceBefore=20,
            spaceAfter=10,
            leading=28,
        ),
        "h2": ParagraphStyle(
            "H2",
            fontName=_FONT_BOLD,
            fontSize=16,
            textColor=OCHA_BLUE,
            spaceBefore=14,
            spaceAfter=8,
            leading=21,
        ),
        "h3": ParagraphStyle(
            "H3",
            fontName=_FONT_BOLD,
            fontSize=13,
            textColor=DARK_TEXT,
            spaceBefore=10,
            spaceAfter=6,
            leading=17,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=_FONT_REGULAR,
            fontSize=10.5,
            textColor=DARK_TEXT,
            alignment=TA_JUSTIFY,
            leading=15.75,
            spaceAfter=6,
        ),
        "body_small": ParagraphStyle(
            "BodySmall",
            fontName=_FONT_REGULAR,
            fontSize=9,
            textColor=DARK_TEXT,
            alignment=TA_LEFT,
            leading=13.5,
            spaceAfter=4,
        ),
        "kpi_value": ParagraphStyle(
            "KPIValue",
            fontName=_FONT_BOLD,
            fontSize=24,
            textColor=OCHA_BLUE,
            alignment=TA_CENTER,
            leading=30,
        ),
        "kpi_label": ParagraphStyle(
            "KPILabel",
            fontName=_FONT_REGULAR,
            fontSize=9,
            textColor=MEDIUM_GRAY,
            alignment=TA_CENTER,
            leading=12,
        ),
        "ref": ParagraphStyle(
            "Ref",
            fontName=_FONT_REGULAR,
            fontSize=8,
            textColor=MEDIUM_GRAY,
            leading=11,
            leftIndent=18,
            firstLineIndent=-18,
        ),
        "annex_title": ParagraphStyle(
            "AnnexTitle",
            fontName=_FONT_BOLD,
            fontSize=14,
            textColor=OCHA_DARK_BLUE,
            spaceBefore=12,
            spaceAfter=6,
            leading=18,
        ),
    }


def _maybe_image(
    path: str, max_width: float = 6.5 * inch, max_height: float = 4.0 * inch
) -> Optional[Image]:
    """Load and size image if path exists, else return `None`."""
    p = Path(path)
    if not p.exists():
        return None
    img = Image(str(p))
    img._restrictSize(max_width, max_height)
    return img


def _hr() -> HRFlowable:
    """Return horizontal-rule flowable matching report visual style."""
    return HRFlowable(
        width="100%",
        thickness=0.5,
        color=OCHA_BLUE,
        spaceAfter=8,
        spaceBefore=4,
    )


def _kpi_row(
    styles: Dict[str, ParagraphStyle],
    kpis: List[tuple],
) -> Table:
    """Build KPI table row block for cover-page metrics."""
    value_cells = [Paragraph(str(v), styles["kpi_value"]) for v, _ in kpis]
    label_cells = [Paragraph(lbl, styles["kpi_label"]) for _, lbl in kpis]
    col_width = 6.5 * inch / max(len(kpis), 1)
    t = Table(
        [value_cells, label_cells],
        colWidths=[col_width] * len(kpis),
    )
    t.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                ("BOX", (0, 0), (-1, -1), 0.5, OCHA_BLUE),
            ]
        )
    )
    return t


def _normalize_section_text(value: object) -> str:
    """Normalize potentially non-string narrative payloads to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(str(item) for item in value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return str(value)
    return str(value)


# Main PDF builder
def build_pdf(
    *,
    report_data: ReportData,
    narrative: NarrativeSections,
    infographic_paths: Optional[Dict[str, str]],
    config: PDFConfig,
) -> str:
    """Render full AEGIS report PDF to configured output path.

    Args:
        report_data: Aggregated report metrics and state outputs.
        narrative: Structured narrative sections.
        infographic_paths: Optional mapping of infographic type to file path.
        config: PDF rendering options and output destination.

    Returns:
        str: Absolute/relative path to generated PDF file.

    Raises:
        Exception: Can propagate ReportLab rendering/file I/O errors.

    Side Effects:
        Writes PDF file to disk and reads optional infographic files.

    Latency:
        Depends on document length and embedded image sizes.
    """
    Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        config.output_path,
        pagesize=A4,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = _styles()
    story: list = []

    total_events, total_fatalities = report_data.totals()

    # cover page
    story.append(Spacer(1, 1.2 * inch))
    story.append(
        Paragraph(
            "AEGIS Humanitarian<br/>Situation Report",
            styles["cover_title"],
        )
    )
    story.append(Spacer(1, 0.15 * inch))
    story.append(_hr())
    story.append(
        Paragraph(
            f"Northeast Nigeria &bull; {report_data.generated_at[:10]}",
            styles["cover_subtitle"],
        )
    )
    story.append(
        Paragraph(
            f"Scan ID: {report_data.scan_id} &bull; "
            f"States analyzed: {', '.join(report_data.states)}",
            styles["cover_subtitle"],
        )
    )
    story.append(Spacer(1, 0.4 * inch))

    # KPI banner
    total_idps = 0
    max_ipc = 0
    for a in report_data.assessments_by_state.values():
        m = a.get("metrics") or {}
        total_idps += int(m.get("idp_estimate") or 0)
        ipc = int(m.get("ipc_phase") or 0)
        if ipc > max_ipc:
            max_ipc = ipc

    story.append(
        _kpi_row(
            styles,
            [
                (f"{total_events:,}", "Conflict Events"),
                (f"{total_fatalities:,}", "Fatalities"),
                (f"{total_idps:,}", "IDPs Estimated"),
                (f"Phase {max_ipc}" if max_ipc else "N/A", "Worst IPC Phase"),
            ],
        )
    )
    story.append(Spacer(1, 0.3 * inch))
    story.append(
        Paragraph(
            "Privacy: All outputs aggregated to LGA/state level. "
            "No camp-level coordinates included.",
            styles["body_small"],
        )
    )
    story.append(PageBreak())

    # Section helper
    def section(title: str, body: object, level: str = "h1") -> None:
        """Append one narrative section block to story flowables."""
        story.append(Paragraph(title, styles[level]))
        story.append(_hr())
        normalized = _normalize_section_text(body)
        for para in normalized.split("\n\n"):
            cleaned = para.strip()
            if not cleaned:
                continue
            story.append(
                Paragraph(
                    cleaned.replace("\n", "<br/>"),
                    styles["body"],
                )
            )
        story.append(Spacer(1, 0.1 * inch))

    # Executive Summary
    section("Executive Summary", narrative.executive_summary)
    story.append(PageBreak())

    # Infographics
    if config.include_infographics and infographic_paths:
        display_names = {
            "situation_overview": "Situation Overview",
            "risk_heatmap": "Risk Heatmap",
            "displacement_forecast": "Displacement Forecast",
            "needs_assessment": "Needs Assessment",
        }
        for key in (
            "situation_overview",
            "risk_heatmap",
            "displacement_forecast",
            "needs_assessment",
        ):
            path = infographic_paths.get(key)
            if not path:
                continue
            story.append(
                Paragraph(
                    f"Figure: {display_names.get(key, key)}",
                    styles["h2"],
                )
            )
            img = _maybe_image(path)
            if img:
                story.append(img)
            story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

    # Main body sections
    section("Situation Analysis", narrative.situation_analysis)
    section("Food Security Assessment", narrative.food_security_assessment)
    section("Displacement Analysis", narrative.displacement_analysis)
    section("Risk Assessment", narrative.risk_assessment)
    story.append(PageBreak())

    section("Safe Routes &amp; Access Constraints", narrative.safe_routes_analysis)
    section("Recommendations", narrative.recommendations)
    section("FARMA Loan Adjustments", narrative.farmer_loan_adjustments)
    story.append(PageBreak())

    # State Annexes
    if config.include_annexes and narrative.state_annexes:
        story.append(Paragraph("State Annexes", styles["h1"]))
        story.append(_hr())
        for state_name, text in narrative.state_annexes.items():
            story.append(Paragraph(state_name, styles["annex_title"]))
            normalized = _normalize_section_text(text)
            for para in normalized.split("\n\n"):
                cleaned = para.strip()
                if not cleaned:
                    continue
                story.append(
                    Paragraph(
                        cleaned.replace("\n", "<br/>"),
                        styles["body"],
                    )
                )
            story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

    # Methodology
    section("Methodology", narrative.methodology)
    story.append(PageBreak())

    # References (numbered)
    story.append(Paragraph("References", styles["h1"]))
    story.append(_hr())
    if narrative.references:
        for i, uri in enumerate(narrative.references, 1):
            story.append(Paragraph(f"[{i}] {uri}", styles["ref"]))
    else:
        story.append(Paragraph("No references available.", styles["body"]))

    doc.build(story)
    return config.output_path
