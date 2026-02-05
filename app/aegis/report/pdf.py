from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    PageBreak,
)

from app.aegis.report.narrative import NarrativeSections
from app.aegis.report.report_data import ReportData


UN_BLUE = colors.HexColor("#0072BC")
NEUTRAL_GRAY = colors.HexColor("#6C757D")
DARK_TEXT = colors.HexColor("#212529")
LIGHT_GRAY = colors.HexColor("#F8F9FA")


@dataclass
class PDFConfig:
    output_path: str
    include_infographics: bool = True
    include_annexes: bool = True


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Heading1"],
            fontSize=26,
            textColor=UN_BLUE,
            alignment=TA_CENTER,
            spaceAfter=16,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Heading2"],
            fontSize=12,
            textColor=NEUTRAL_GRAY,
            alignment=TA_CENTER,
            spaceAfter=20,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=13,
            textColor=UN_BLUE,
            spaceBefore=14,
            spaceAfter=8,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=10,
            textColor=DARK_TEXT,
            alignment=TA_JUSTIFY,
            leading=14,
        ),
        "mono": ParagraphStyle(
            "Mono",
            parent=base["Code"],
            fontSize=8,
            leading=10,
        ),
    }


def _maybe_image(path: str, max_width: float = 7.0 * inch, max_height: float = 4.2 * inch) -> Optional[Image]:
    p = Path(path)
    if not p.exists():
        return None
    img = Image(str(p))
    img._restrictSize(max_width, max_height)
    return img


def build_pdf(
    *,
    report_data: ReportData,
    narrative: NarrativeSections,
    infographic_paths: Optional[Dict[str, str]],
    config: PDFConfig,
) -> str:
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
    story = []

    # Cover
    story.append(Paragraph("AEGIS Humanitarian Situation Report", styles["title"]))
    story.append(Paragraph(f"Scan ID: {report_data.scan_id} • Generated: {report_data.generated_at}", styles["subtitle"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Privacy note: outputs are aggregated to LGA/state level; no camp coordinates.", styles["body"]))
    story.append(PageBreak())

    def section(title: str, body: str):
        story.append(Paragraph(title, styles["h2"]))
        for para in (body or "").split("\n\n"):
            story.append(Paragraph(para.replace("\n", "<br/>"), styles["body"]))
            story.append(Spacer(1, 0.12 * inch))

    section("Executive Summary", narrative.executive_summary)
    story.append(PageBreak())

    if config.include_infographics and infographic_paths:
        for key in ("situation_overview", "risk_heatmap", "displacement_forecast", "needs_assessment"):
            path = infographic_paths.get(key)
            if not path:
                continue
            story.append(Paragraph(f"Infographic: {key.replace('_',' ').title()}", styles["h2"]))
            img = _maybe_image(path)
            if img:
                story.append(img)
            story.append(PageBreak())

    section("Situation Analysis", narrative.situation_analysis)
    section("Food Security Assessment", narrative.food_security_assessment)
    section("Displacement Analysis", narrative.displacement_analysis)
    section("Risk Assessment", narrative.risk_assessment)
    section("Safe Routes / Access Constraints", narrative.safe_routes_analysis)
    story.append(PageBreak())

    section("Recommendations", narrative.recommendations)
    section("FARMA Loan Adjustments", narrative.farmer_loan_adjustments)
    story.append(PageBreak())

    if config.include_annexes and narrative.state_annexes:
        story.append(Paragraph("State Annexes", styles["h2"]))
        for state_name, text in narrative.state_annexes.items():
            story.append(Paragraph(state_name, styles["h2"]))
            story.append(Paragraph((text or "").replace("\n", "<br/>"), styles["body"]))
            story.append(PageBreak())

    section("Methodology", narrative.methodology)
    story.append(PageBreak())

    story.append(Paragraph("References (URI whitelist)", styles["h2"]))
    if narrative.references:
        for uri in narrative.references:
            story.append(Paragraph(uri, styles["mono"]))
    else:
        story.append(Paragraph("No references available.", styles["body"]))

    doc.build(story)
    return config.output_path
