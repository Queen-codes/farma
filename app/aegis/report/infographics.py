"""Infographic Generator - Gemini 3 Pro Image Preview (Nano Banana Pro).

Generates professional humanitarian infographics using Gemini's image generation.
Model: gemini-3-pro-image-preview
"""

import base64
import os
from enum import Enum
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from app.config import GOOGLE_API_KEY
from .data_extractor import ReportData, format_number


class InfographicType(Enum):
    """Types of infographics to generate."""

    SITUATION_OVERVIEW = "situation_overview"
    DISPLACEMENT_FORECAST = "displacement_forecast"
    NEEDS_ASSESSMENT = "needs_assessment"
    RISK_HEATMAP = "risk_heatmap"


@dataclass
class InfographicConfig:
    """Configuration for infographic generation."""

    aspect_ratio: str = "16:9"
    image_size: str = "2K"  # 1K, 2K, or 4K
    output_dir: str = "generated_infographics"


@dataclass
class GeneratedInfographic:
    """Result of infographic generation."""

    infographic_type: InfographicType
    image_data: bytes  # Raw image bytes
    file_path: Optional[str] = None  # Path if saved to disk
    prompt_used: str = ""
    thinking_summary: str = ""  # Summary of model's reasoning


# Humanitarian color palette (UN/IOM aesthetic)
COLOR_PALETTE = """
COLOR PALETTE (UN Humanitarian Standard):
- Primary Blue: #0072BC (UN Blue)
- Accent Orange: #F26522 (Alert/Warning)
- Critical Red: #E63946 (Emergency)
- Safe Green: #2A9D8F (Stable/Safe)
- Neutral Gray: #6C757D (Text/Borders)
- Background: #FFFFFF with subtle #F8F9FA texture
- Dark Text: #212529
"""

STYLE_GUIDELINES = """
STYLE REQUIREMENTS:
- Professional humanitarian sector aesthetic (UN/IOM/WFP style)
- Clean, minimalist design with clear visual hierarchy
- High contrast for print readability
- Sans-serif typography (similar to UN reports)
- Icons should be simple, universally understood
- Data visualizations must be accurate and clear
- Include subtle grid lines for professionalism
- All text must be readable at print size (no text smaller than 10pt equivalent)
- Use the humanitarian color palette consistently
"""


def get_genai_client() -> genai.Client:
    """Create Google GenAI client."""
    return genai.Client(api_key=GOOGLE_API_KEY)


async def generate_infographic(
    report_data: ReportData,
    infographic_type: InfographicType,
    config: Optional[InfographicConfig] = None,
) -> GeneratedInfographic:
    """Generate a single infographic using Gemini 3 Pro Image Preview.

    Args:
        report_data: Structured data from synthesis agent
        infographic_type: Type of infographic to generate
        config: Optional configuration for image settings

    Returns:
        GeneratedInfographic with image data and metadata
    """
    if config is None:
        config = InfographicConfig()

    # Build the prompt based on infographic type
    prompt = _build_infographic_prompt(report_data, infographic_type)

    # Create client and generate
    client = get_genai_client()

    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=config.aspect_ratio,
                image_size=config.image_size,
            ),
        ),
    )

    # Extract image and text from response
    image_data = None
    thinking_summary = ""

    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content:
            for part in candidate.content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    inline = part.inline_data
                    if hasattr(inline, "data"):
                        data = inline.data
                        # Check if it's already bytes or needs decoding
                        if isinstance(data, bytes):
                            image_data = data
                        elif isinstance(data, str):
                            try:
                                image_data = base64.b64decode(data)
                            except Exception:
                                image_data = data.encode()
                elif hasattr(part, "text") and part.text:
                    thinking_summary = part.text

    if image_data is None:
        raise ValueError(f"No image generated for {infographic_type.value}")

    result = GeneratedInfographic(
        infographic_type=infographic_type,
        image_data=image_data,
        prompt_used=prompt,
        thinking_summary=thinking_summary,
    )

    # Optionally save to disk
    if config.output_dir:
        result.file_path = _save_infographic(result, config.output_dir)

    return result


async def generate_all_infographics(
    report_data: ReportData,
    config: Optional[InfographicConfig] = None,
) -> dict[InfographicType, GeneratedInfographic]:
    """Generate all 4 infographics for the report.

    Args:
        report_data: Structured data from synthesis agent
        config: Optional configuration

    Returns:
        Dict mapping InfographicType to GeneratedInfographic
    """
    results = {}

    for infographic_type in InfographicType:
        try:
            result = await generate_infographic(report_data, infographic_type, config)
            results[infographic_type] = result
            print(f"[INFOGRAPHIC] Generated: {infographic_type.value}")
        except Exception as e:
            print(f"[INFOGRAPHIC] Error generating {infographic_type.value}: {e}")
            # Continue with other infographics

    return results


def _build_infographic_prompt(
    report_data: ReportData, infographic_type: InfographicType
) -> str:
    """Build the prompt for a specific infographic type with real data."""

    if infographic_type == InfographicType.SITUATION_OVERVIEW:
        return _build_situation_overview_prompt(report_data)
    elif infographic_type == InfographicType.DISPLACEMENT_FORECAST:
        return _build_displacement_forecast_prompt(report_data)
    elif infographic_type == InfographicType.NEEDS_ASSESSMENT:
        return _build_needs_assessment_prompt(report_data)
    elif infographic_type == InfographicType.RISK_HEATMAP:
        return _build_risk_heatmap_prompt(report_data)
    else:
        raise ValueError(f"Unknown infographic type: {infographic_type}")


def _build_situation_overview_prompt(report_data: ReportData) -> str:
    """Build prompt for Situation Overview infographic."""
    regional = report_data.regional
    states_list = ", ".join(regional.states_analyzed)

    # Get highest priority state data
    highest_state = regional.highest_priority_state
    highest_data = report_data.states.get(highest_state) if highest_state else None

    return f"""Create a professional humanitarian SITUATION OVERVIEW infographic for AEGIS (AI-Enabled Geospatial Intelligence System).

TITLE: "AEGIS Situation Report: {regional.region_name}"
SUBTITLE: "Food Security & Displacement Analysis"
DATE: {report_data.generated_at[:10]}

LAYOUT (16:9 landscape):

LEFT SIDE (40% width):
- Stylized map of northeastern Nigeria showing state boundaries
- States to highlight: {states_list}
- Use heatmap coloring to show severity:
  * CRITICAL states (deep red): {', '.join(regional.critical_states) or 'None'}
  * HIGH RISK states (orange): {', '.join(regional.high_risk_states) or 'None'}
  * Other states (yellow/green gradient)
- Mark key cities: Maiduguri, Yola, Damaturu

RIGHT SIDE (60% width):
KEY STATISTICS (large, bold numbers with icons):

1. TOTAL IDPs: {format_number(regional.total_idps)}
   Icon: Person/family silhouette
   Color: Primary blue

2. MONTHLY FOOD NEED: {format_number(regional.total_monthly_food_need_mt)} MT
   Icon: Wheat/grain
   Color: Orange

3. CONFLICT EVENTS: {regional.total_conflict_events}
   Icon: Warning triangle
   Color: Red

4. PRIORITY STATE: {highest_state or 'N/A'}
   Score: {highest_data.priority_score if highest_data else 'N/A'}/100
   Icon: Location pin
   Color: Deep red

5. STATES ANALYZED: {len(regional.states_analyzed)}
   Icon: Map marker cluster

BOTTOM BAR:
- AEGIS branding (left)
- "Sources: IOM DTM, ACLED, FEWS NET" (center)
- Report ID: {report_data.report_id} (right)

{COLOR_PALETTE}
{STYLE_GUIDELINES}

Generate a clean, professional infographic suitable for humanitarian coordination briefings and official reports."""


def _build_displacement_forecast_prompt(report_data: ReportData) -> str:
    """Build prompt for Displacement Forecast infographic."""
    regional = report_data.regional

    # Build state-by-state IDP data
    state_idp_data = []
    for state_name, state_data in report_data.states.items():
        if state_data.idp_count:
            trend = state_data.conflict_trend or "STABLE"
            state_idp_data.append(
                f"- {state_name}: {format_number(state_data.idp_count)} IDPs (Trend: {trend})"
            )

    return f"""Create a professional DISPLACEMENT ANALYSIS infographic for AEGIS.

TITLE: "Displacement Trends & Projections"
SUBTITLE: "{regional.region_name}"

LAYOUT (16:9 landscape):

TOP SECTION (30%):
Header with total IDP count prominently displayed:
TOTAL DISPLACED: {format_number(regional.total_idps)}

MAIN SECTION (50%):
Horizontal bar chart showing IDP counts by state:
{chr(10).join(state_idp_data)}

Each bar should:
- Be color-coded by trend (Red=INCREASING, Yellow=STABLE, Green=DECREASING)
- Show exact number at end of bar
- Include small trend arrow icon

RIGHT PANEL:
TREND INDICATORS
- States with INCREASING violence: {', '.join([s for s, d in report_data.states.items() if d.conflict_trend == 'INCREASING']) or 'None identified'}
- States with DECREASING violence: {', '.join([s for s, d in report_data.states.items() if d.conflict_trend == 'DECREASING']) or 'None identified'}

BOTTOM SECTION (20%):
HOTSPOT LGAs TO MONITOR:
{', '.join(regional.all_hotspot_lgas[:10]) or 'None identified'}

FOOTER:
- AEGIS branding
- Data sources: IOM DTM Round 50, ACLED
- Report ID: {report_data.report_id}

{COLOR_PALETTE}
{STYLE_GUIDELINES}

Generate a clean, data-focused visualization suitable for humanitarian planning."""


def _build_needs_assessment_prompt(report_data: ReportData) -> str:
    """Build prompt for Needs Assessment infographic."""
    regional = report_data.regional

    # Calculate aid allocation
    allocation_items = []
    for state, pct in regional.aid_allocation_proportions.items():
        allocation_items.append(f"- {state}: {pct}%")

    # Collect humanitarian needs across states
    all_needs = set()
    for state_data in report_data.states.values():
        all_needs.update(state_data.humanitarian_needs)

    return f"""Create a professional NEEDS ASSESSMENT infographic for AEGIS.

TITLE: "Priority Needs Assessment"
SUBTITLE: "{regional.region_name} - Humanitarian Aid Allocation"

LAYOUT (Portrait, 4:5 aspect ratio suitable for A4):

TOP SECTION:
Large donut chart showing recommended aid allocation by state:
{chr(10).join(allocation_items) if allocation_items else '- Data pending'}

Each segment should:
- Be color-coded distinctly
- Show percentage and state name
- Use humanitarian color palette

CENTER SECTION:
PRIORITY NEEDS IDENTIFIED:
{', '.join(list(all_needs)[:8]) if all_needs else 'Food, Shelter, Protection, Health, WASH'}

Display as icon grid with:
- Food Security (wheat icon)
- Shelter (house icon)
- Protection (shield icon)
- Health (medical cross)
- WASH (water drop)

BOTTOM SECTION:
KEY METRICS TABLE:
| Metric | Value |
|--------|-------|
| Total IDPs | {format_number(regional.total_idps)} |
| Monthly Food Need | {format_number(regional.total_monthly_food_need_mt)} MT |
| Critical States | {len(regional.critical_states)} |
| High Risk States | {len(regional.high_risk_states)} |

FOOTER:
- AEGIS branding
- "Allocation based on Food Security Priority Scores"
- Report ID: {report_data.report_id}

{COLOR_PALETTE}
{STYLE_GUIDELINES}

Generate a clean infographic showing humanitarian needs breakdown and recommended aid allocation."""


def _build_risk_heatmap_prompt(report_data: ReportData) -> str:
    """Build prompt for Risk Heatmap infographic."""
    regional = report_data.regional

    # Build state risk data
    state_risk_items = []
    for state_name, state_data in report_data.states.items():
        level = state_data.priority_level or "MODERATE"
        score = state_data.priority_score or 0
        state_risk_items.append(f"- {state_name}: {level} ({score}/100)")

    return f"""Create a professional RISK HEATMAP infographic for AEGIS.

TITLE: "Food Security Risk Assessment"
SUBTITLE: "{regional.region_name} - Priority Classification"

LAYOUT (16:9 landscape):

MAIN SECTION (70%):
Large choropleth map of northeastern Nigeria showing:

STATES TO DISPLAY:
{chr(10).join(state_risk_items)}

COLOR CODING BY RISK LEVEL:
- CRITICAL (>80): Deep Red (#E63946)
- HIGH (60-80): Orange (#F26522)
- ELEVATED (40-60): Yellow (#FFD166)
- MODERATE (<40): Light Green (#2A9D8F)

Map should show:
- Clear state boundaries
- State names labeled
- Key cities marked (Maiduguri, Yola, Damaturu, Gombe)

CONFLICT MARKERS:
Hotspot LGAs (small warning icons): {', '.join(regional.all_hotspot_lgas[:8]) or 'None'}

SIDE PANEL (30%):
RISK LEGEND:
Visual scale from CRITICAL to MODERATE with color gradient

PRIORITY RANKING:
1. {regional.highest_priority_state or 'N/A'} - CRITICAL
{chr(10).join([f"{i+2}. {s}" for i, s in enumerate(regional.critical_states[:4])])}

SAFE ACCESS POINTS:
{', '.join(regional.all_safe_lgas[:6]) or 'None identified'}

FOOTER:
- AEGIS branding
- "Risk scores based on IPC Phase, displacement, conflict, and access"
- Data attribution: IOM DTM, ACLED, FEWS NET
- Report ID: {report_data.report_id}

{COLOR_PALETTE}
{STYLE_GUIDELINES}

Generate a cartographic-style heatmap suitable for humanitarian coordination and logistics planning."""


def _save_infographic(infographic: GeneratedInfographic, output_dir: str) -> str:
    """Save infographic to disk."""
    # Create output directory if needed
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Generate filename
    filename = f"aegis_{infographic.infographic_type.value}.png"
    filepath = os.path.join(output_dir, filename)

    # Write image data
    with open(filepath, "wb") as f:
        f.write(infographic.image_data)

    return filepath
