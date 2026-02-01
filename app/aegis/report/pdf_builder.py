"""PDF Builder - Assemble final humanitarian report PDF.

Uses ReportLab for professional PDF generation with UN/IOM aesthetic.
"""

import io
import os
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
    ListFlowable,
    ListItem,
)
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .data_extractor import ReportData, format_number
from .narrative import NarrativeSections
from .infographics import GeneratedInfographic, InfographicType


# UN Humanitarian Color Palette
UN_BLUE = colors.HexColor("#0072BC")
UN_ORANGE = colors.HexColor("#F26522")
CRITICAL_RED = colors.HexColor("#E63946")
SAFE_GREEN = colors.HexColor("#2A9D8F")
NEUTRAL_GRAY = colors.HexColor("#6C757D")
LIGHT_GRAY = colors.HexColor("#F8F9FA")
DARK_TEXT = colors.HexColor("#212529")


@dataclass
class PDFConfig:
    """Configuration for PDF generation."""

    output_path: str = "aegis_report.pdf"
    page_size: tuple = A4
    margin: float = 0.75 * inch
    include_cover: bool = True
    include_infographics: bool = True
    include_annexes: bool = True


def build_pdf(
    report_data: ReportData,
    narrative: NarrativeSections,
    infographics: Optional[dict[InfographicType, GeneratedInfographic]] = None,
    config: Optional[PDFConfig] = None,
) -> str:
    """Build the complete PDF report.

    Args:
        report_data: Structured data from synthesis
        narrative: Generated narrative sections
        infographics: Generated infographics (optional)
        config: PDF configuration

    Returns:
        Path to generated PDF file
    """
    if config is None:
        config = PDFConfig()

    # Ensure output directory exists
    Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)

    # Create document
    doc = SimpleDocTemplate(
        config.output_path,
        pagesize=config.page_size,
        leftMargin=config.margin,
        rightMargin=config.margin,
        topMargin=config.margin,
        bottomMargin=config.margin,
    )

    # Build styles
    styles = _create_styles()

    # Build story (content flow)
    story = []

    # Cover page
    if config.include_cover:
        story.extend(_build_cover_page(report_data, styles))
        story.append(PageBreak())

    # Executive Summary
    story.extend(_build_section("EXECUTIVE SUMMARY", narrative.executive_summary, styles))
    story.append(PageBreak())

    # Situation Overview Infographic (full page)
    if config.include_infographics and infographics:
        if InfographicType.SITUATION_OVERVIEW in infographics:
            story.extend(
                _build_infographic_page(
                    infographics[InfographicType.SITUATION_OVERVIEW],
                    "Situation Overview",
                    styles,
                )
            )
            story.append(PageBreak())

    # Situation Analysis
    story.extend(_build_section("SITUATION ANALYSIS", narrative.situation_analysis, styles))
    story.append(Spacer(1, 0.3 * inch))

    # Food Security Assessment
    story.extend(
        _build_section("FOOD SECURITY ASSESSMENT", narrative.food_security_assessment, styles)
    )
    story.append(PageBreak())

    # Risk Heatmap Infographic (full page)
    if config.include_infographics and infographics:
        if InfographicType.RISK_HEATMAP in infographics:
            story.extend(
                _build_infographic_page(
                    infographics[InfographicType.RISK_HEATMAP],
                    "Food Security Risk Assessment",
                    styles,
                )
            )
            story.append(PageBreak())

    # Displacement Analysis
    story.extend(_build_section("DISPLACEMENT ANALYSIS", narrative.displacement_analysis, styles))
    story.append(PageBreak())

    # Displacement Forecast Infographic (full page)
    if config.include_infographics and infographics:
        if InfographicType.DISPLACEMENT_FORECAST in infographics:
            story.extend(
                _build_infographic_page(
                    infographics[InfographicType.DISPLACEMENT_FORECAST],
                    "Displacement Trends & Projections",
                    styles,
                )
            )
            story.append(PageBreak())

    # Risk Assessment
    story.extend(_build_section("RISK ASSESSMENT", narrative.risk_assessment, styles))
    story.append(Spacer(1, 0.3 * inch))

    # Safe Routes Analysis
    story.extend(_build_section("SAFE ROUTES ANALYSIS", narrative.safe_routes_analysis, styles))
    story.append(PageBreak())

    # Recommendations
    story.extend(_build_section("RECOMMENDATIONS", narrative.recommendations, styles))
    story.append(PageBreak())

    # Needs Assessment Infographic (full page)
    if config.include_infographics and infographics:
        if InfographicType.NEEDS_ASSESSMENT in infographics:
            story.extend(
                _build_infographic_page(
                    infographics[InfographicType.NEEDS_ASSESSMENT],
                    "Priority Needs Assessment",
                    styles,
                )
            )
            story.append(PageBreak())

    # Farmer Loan Adjustments
    story.extend(
        _build_section("FARMER LOAN ADJUSTMENTS", narrative.farmer_loan_adjustments, styles)
    )
    story.append(PageBreak())

    # State Annexes
    if config.include_annexes and narrative.state_annexes:
        story.extend(_build_annexes_section(narrative.state_annexes, styles))
        story.append(PageBreak())

    # Data Summary Table
    story.extend(_build_data_summary_table(report_data, styles))
    story.append(PageBreak())

    # Methodology
    story.extend(_build_section("METHODOLOGY", narrative.methodology, styles))
    story.append(PageBreak())

    # References
    story.extend(_build_references_section(narrative.references, report_data, styles))

    # Build PDF
    doc.build(story, onFirstPage=_add_header_footer, onLaterPages=_add_header_footer)

    return config.output_path


def _create_styles() -> dict:
    """Create custom paragraph styles for the report."""
    base_styles = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "Title",
            parent=base_styles["Heading1"],
            fontSize=28,
            textColor=UN_BLUE,
            alignment=TA_CENTER,
            spaceAfter=20,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base_styles["Heading2"],
            fontSize=16,
            textColor=NEUTRAL_GRAY,
            alignment=TA_CENTER,
            spaceAfter=30,
            fontName="Helvetica",
        ),
        "section_header": ParagraphStyle(
            "SectionHeader",
            parent=base_styles["Heading2"],
            fontSize=14,
            textColor=UN_BLUE,
            spaceBefore=20,
            spaceAfter=10,
            fontName="Helvetica-Bold",
            borderWidth=0,
            borderColor=UN_BLUE,
            borderPadding=5,
        ),
        "subsection_header": ParagraphStyle(
            "SubsectionHeader",
            parent=base_styles["Heading3"],
            fontSize=12,
            textColor=DARK_TEXT,
            spaceBefore=15,
            spaceAfter=8,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base_styles["Normal"],
            fontSize=10,
            textColor=DARK_TEXT,
            alignment=TA_JUSTIFY,
            spaceBefore=6,
            spaceAfter=6,
            leading=14,
            fontName="Helvetica",
        ),
        "citation": ParagraphStyle(
            "Citation",
            parent=base_styles["Normal"],
            fontSize=8,
            textColor=NEUTRAL_GRAY,
            fontName="Helvetica-Oblique",
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base_styles["Normal"],
            fontSize=8,
            textColor=NEUTRAL_GRAY,
            alignment=TA_CENTER,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base_styles["Heading1"],
            fontSize=36,
            textColor=UN_BLUE,
            alignment=TA_CENTER,
            spaceAfter=10,
            fontName="Helvetica-Bold",
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base_styles["Heading2"],
            fontSize=18,
            textColor=DARK_TEXT,
            alignment=TA_CENTER,
            spaceAfter=40,
            fontName="Helvetica",
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base_styles["Normal"],
            fontSize=9,
            textColor=colors.white,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            parent=base_styles["Normal"],
            fontSize=9,
            textColor=DARK_TEXT,
            alignment=TA_LEFT,
            fontName="Helvetica",
        ),
        "reference": ParagraphStyle(
            "Reference",
            parent=base_styles["Normal"],
            fontSize=8,
            textColor=DARK_TEXT,
            spaceBefore=4,
            spaceAfter=4,
            leftIndent=20,
            firstLineIndent=-20,
            fontName="Helvetica",
        ),
    }

    return styles


def _build_cover_page(report_data: ReportData, styles: dict) -> list:
    """Build cover page elements."""
    elements = []

    # Spacer for top margin
    elements.append(Spacer(1, 2 * inch))

    # AEGIS Logo/Title
    elements.append(Paragraph("A E G I S", styles["cover_title"]))
    elements.append(
        Paragraph(
            "AI-Enabled Geospatial Intelligence System",
            styles["cover_subtitle"],
        )
    )

    elements.append(Spacer(1, 0.5 * inch))

    # Horizontal line
    elements.append(_create_horizontal_line())

    elements.append(Spacer(1, 0.5 * inch))

    # Report type
    elements.append(
        Paragraph(
            "HUMANITARIAN SITUATION REPORT",
            ParagraphStyle(
                "ReportType",
                fontSize=20,
                textColor=UN_ORANGE,
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
            ),
        )
    )

    elements.append(Spacer(1, 0.3 * inch))

    # Region
    elements.append(
        Paragraph(
            report_data.regional.region_name,
            ParagraphStyle(
                "Region",
                fontSize=24,
                textColor=DARK_TEXT,
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
            ),
        )
    )

    elements.append(Spacer(1, 1 * inch))

    # Metadata table
    metadata = [
        ["Report Date:", datetime.now().strftime("%d %B %Y")],
        ["Report ID:", report_data.report_id],
        ["States Analyzed:", ", ".join(report_data.regional.states_analyzed)],
        ["Data Sources:", f"{len(report_data.all_source_uris)} verified sources"],
    ]

    metadata_table = Table(metadata, colWidths=[2 * inch, 4 * inch])
    metadata_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("TEXTCOLOR", (0, 0), (-1, -1), DARK_TEXT),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (1, 0), (1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(metadata_table)

    elements.append(Spacer(1, 1.5 * inch))

    # Footer note
    elements.append(
        Paragraph(
            "This report was generated by AEGIS using AI-powered analysis of humanitarian data sources.",
            styles["citation"],
        )
    )

    return elements


def _build_section(title: str, content: str, styles: dict) -> list:
    """Build a standard section with title and content."""
    elements = []

    # Section header
    elements.append(Paragraph(title, styles["section_header"]))

    # Parse content for paragraphs and format
    paragraphs = content.split("\n\n") if content else ["Content not available."]

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Check if it's a subsection header (starts with ##)
        if para.startswith("##"):
            header_text = para.lstrip("#").strip()
            elements.append(Paragraph(header_text, styles["subsection_header"]))
        # Check if it's a bullet list
        elif para.startswith("- ") or para.startswith("* "):
            items = para.split("\n")
            list_items = []
            for item in items:
                item_text = item.lstrip("-* ").strip()
                if item_text:
                    list_items.append(ListItem(Paragraph(item_text, styles["body"])))
            if list_items:
                elements.append(ListFlowable(list_items, bulletType="bullet"))
        # Check if it's a numbered list
        elif para[0].isdigit() and ". " in para[:4]:
            items = para.split("\n")
            list_items = []
            for item in items:
                # Remove numbering
                if ". " in item[:4]:
                    item_text = item.split(". ", 1)[1].strip()
                else:
                    item_text = item.strip()
                if item_text:
                    list_items.append(ListItem(Paragraph(item_text, styles["body"])))
            if list_items:
                elements.append(ListFlowable(list_items, bulletType="1"))
        else:
            # Regular paragraph - handle inline citations
            formatted_para = _format_citations(para)
            elements.append(Paragraph(formatted_para, styles["body"]))

    return elements


def _build_infographic_page(
    infographic: GeneratedInfographic, title: str, styles: dict
) -> list:
    """Build a full-page infographic."""
    elements = []

    # Title
    elements.append(Paragraph(title, styles["section_header"]))
    elements.append(Spacer(1, 0.2 * inch))

    # Image - scale to fit page width
    if infographic.image_data:
        img_reader = ImageReader(io.BytesIO(infographic.image_data))
        img_width, img_height = img_reader.getSize()

        # Scale to page width (with margins)
        max_width = A4[0] - 1.5 * inch
        max_height = A4[1] - 3 * inch  # Leave room for header/footer

        scale = min(max_width / img_width, max_height / img_height)
        display_width = img_width * scale
        display_height = img_height * scale

        img = Image(io.BytesIO(infographic.image_data), width=display_width, height=display_height)
        elements.append(img)

    elements.append(Spacer(1, 0.2 * inch))

    # Caption
    elements.append(
        Paragraph(
            f"Figure: {title} - Generated by AEGIS",
            styles["citation"],
        )
    )

    return elements


def _build_annexes_section(state_annexes: dict, styles: dict) -> list:
    """Build state annexes section."""
    elements = []

    elements.append(Paragraph("STATE ANNEXES", styles["section_header"]))
    elements.append(Spacer(1, 0.2 * inch))

    for state_name, annex_content in state_annexes.items():
        # State header
        elements.append(Paragraph(f"ANNEX: {state_name.upper()}", styles["subsection_header"]))

        # Parse and add content
        paragraphs = annex_content.split("\n\n") if annex_content else []
        for para in paragraphs:
            para = para.strip()
            if para:
                formatted_para = _format_citations(para)
                elements.append(Paragraph(formatted_para, styles["body"]))

        elements.append(Spacer(1, 0.3 * inch))

    return elements


def _build_data_summary_table(report_data: ReportData, styles: dict) -> list:
    """Build data summary table."""
    elements = []

    elements.append(Paragraph("DATA SUMMARY", styles["section_header"]))
    elements.append(Spacer(1, 0.2 * inch))

    # Build table data
    header = ["State", "IDPs", "Food Need (MT)", "Priority", "Score", "Trend"]
    rows = [header]

    for state_name, state_data in report_data.states.items():
        rows.append(
            [
                state_name,
                format_number(state_data.idp_count) if state_data.idp_count else "N/A",
                format_number(state_data.monthly_food_need_mt) if state_data.monthly_food_need_mt else "N/A",
                state_data.priority_level or "N/A",
                f"{state_data.priority_score:.1f}" if state_data.priority_score else "N/A",
                state_data.conflict_trend or "N/A",
            ]
        )

    # Create table
    col_widths = [1.2 * inch, 1 * inch, 1.2 * inch, 1 * inch, 0.8 * inch, 1 * inch]
    table = Table(rows, colWidths=col_widths)

    table.setStyle(
        TableStyle(
            [
                # Header style
                ("BACKGROUND", (0, 0), (-1, 0), UN_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                # Body style
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("ALIGN", (0, 1), (0, -1), "LEFT"),
                # Alternating rows
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
                # Grid
                ("GRID", (0, 0), (-1, -1), 0.5, NEUTRAL_GRAY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    elements.append(table)

    return elements


def _build_references_section(references: str, report_data: ReportData, styles: dict) -> list:
    """Build references section."""
    elements = []

    elements.append(Paragraph("REFERENCES & DATA SOURCES", styles["section_header"]))
    elements.append(Spacer(1, 0.2 * inch))

    # List all source URIs
    for i, uri in enumerate(report_data.all_source_uris, 1):
        ref_text = f"[{i}] {uri}"
        elements.append(Paragraph(ref_text, styles["reference"]))

    if not report_data.all_source_uris:
        elements.append(Paragraph("No sources available.", styles["body"]))

    return elements


def _create_horizontal_line():
    """Create a horizontal line element."""
    from reportlab.platypus import HRFlowable

    return HRFlowable(
        width="80%",
        thickness=2,
        color=UN_BLUE,
        spaceBefore=10,
        spaceAfter=10,
    )


def _format_citations(text: str) -> str:
    """Format inline citations for display."""
    import re

    # Convert [Source: URL] to styled footnote
    pattern = r"\[Source:\s*(https?://[^\]]+)\]"

    def replace_citation(match):
        url = match.group(1)
        # Truncate long URLs for display
        display_url = url[:50] + "..." if len(url) > 50 else url
        return f'<font color="{NEUTRAL_GRAY.hexval()}" size="8">[{display_url}]</font>'

    return re.sub(pattern, replace_citation, text)


def _add_header_footer(canvas: canvas.Canvas, doc):
    """Add header and footer to each page."""
    canvas.saveState()

    page_width, page_height = doc.pagesize

    # Header
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(UN_BLUE)
    canvas.drawString(doc.leftMargin, page_height - 0.4 * inch, "AEGIS Situation Report")

    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(NEUTRAL_GRAY)
    canvas.drawRightString(
        page_width - doc.rightMargin,
        page_height - 0.4 * inch,
        datetime.now().strftime("%d %B %Y"),
    )

    # Header line
    canvas.setStrokeColor(UN_BLUE)
    canvas.setLineWidth(1)
    canvas.line(
        doc.leftMargin,
        page_height - 0.5 * inch,
        page_width - doc.rightMargin,
        page_height - 0.5 * inch,
    )

    # Footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(NEUTRAL_GRAY)

    # Page number
    canvas.drawCentredString(page_width / 2, 0.4 * inch, f"Page {doc.page}")

    # Footer line
    canvas.line(doc.leftMargin, 0.55 * inch, page_width - doc.rightMargin, 0.55 * inch)

    # Confidentiality notice
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.drawString(
        doc.leftMargin,
        0.25 * inch,
        "AEGIS - AI-Enabled Geospatial Intelligence System | For humanitarian coordination use",
    )

    canvas.restoreState()
