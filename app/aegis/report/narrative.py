"""Narrative Generator - Gemini 3 Pro integration for report text.

Generates professional humanitarian report sections with proper citations.
"""

import json
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import GOOGLE_API_KEY
from .data_extractor import ReportData, format_number


@dataclass
class NarrativeSections:
    """Generated narrative sections for the report."""

    executive_summary: str = ""
    situation_analysis: str = ""
    food_security_assessment: str = ""
    displacement_analysis: str = ""
    risk_assessment: str = ""
    safe_routes_analysis: str = ""
    recommendations: str = ""
    farmer_loan_adjustments: str = ""
    methodology: str = ""

    # State-specific annexes (state_name -> narrative)
    state_annexes: dict = field(default_factory=dict)

    # Formatted reference list
    references: str = ""


NARRATIVE_SYSTEM_PROMPT = """You are a professional humanitarian report writer for AEGIS (AI-Enabled Geospatial Intelligence System).

STYLE REQUIREMENTS:
- Write in formal, professional humanitarian sector language
- Use active voice and clear, direct statements
- Include specific numbers, dates, and locations
- Every claim MUST cite its source using [Source: URI] format
- Follow UN/IOM report writing standards
- Be concise but comprehensive
- Use proper humanitarian terminology (IDPs, IPC Phase, GAM, etc.)

CITATION FORMAT (Numbered, Clickable)
- Use numbered inline citations in square brackets, starting at [1] and increasing sequentially.
- Each number corresponds to one unique source URL.
- The same source should reuse the same number throughout the document.
- Inline citations should contain only the number, not the URL.
- The full source URL should be listed separately in a References / Sources section, matching the numbers.
- Each citation number should be clickable, linking to its corresponding source URL.

OUTPUT: Write only the requested section. Do not include section headers in your output - they will be added by the report generator.
"""


def create_narrative_llm():
    """Create Gemini 3 Pro LLM for narrative generation."""
    return ChatGoogleGenerativeAI(
        model="gemini-3-pro-preview",
        google_api_key=GOOGLE_API_KEY,
        temperature=0.7,
        max_retries=2,
    )


async def generate_narrative(report_data: ReportData) -> NarrativeSections:
    """Generate all narrative sections for the report.

    Args:
        report_data: Structured data extracted from synthesis agent

    Returns:
        NarrativeSections with all generated text
    """
    llm = create_narrative_llm()
    sections = NarrativeSections()

    # Build data context for all prompts
    data_context = _build_data_context(report_data)

    # Generate each section
    sections.executive_summary = await _generate_section(
        llm,
        "EXECUTIVE SUMMARY",
        _get_executive_summary_prompt(data_context),
    )

    sections.situation_analysis = await _generate_section(
        llm,
        "SITUATION ANALYSIS",
        _get_situation_analysis_prompt(data_context),
    )

    sections.food_security_assessment = await _generate_section(
        llm,
        "FOOD SECURITY ASSESSMENT",
        _get_food_security_prompt(data_context),
    )

    sections.displacement_analysis = await _generate_section(
        llm,
        "DISPLACEMENT ANALYSIS",
        _get_displacement_prompt(data_context),
    )

    sections.risk_assessment = await _generate_section(
        llm,
        "RISK ASSESSMENT",
        _get_risk_assessment_prompt(data_context),
    )

    sections.safe_routes_analysis = await _generate_section(
        llm,
        "SAFE ROUTES ANALYSIS",
        _get_safe_routes_prompt(data_context),
    )

    sections.recommendations = await _generate_section(
        llm,
        "RECOMMENDATIONS",
        _get_recommendations_prompt(data_context),
    )

    sections.farmer_loan_adjustments = await _generate_section(
        llm,
        "FARMER LOAN ADJUSTMENTS",
        _get_loan_adjustments_prompt(data_context),
    )

    # Generate state annexes
    for state_name, state_data in report_data.states.items():
        state_context = _build_state_context(state_name, state_data, report_data)
        sections.state_annexes[state_name] = await _generate_section(
            llm,
            f"STATE ANNEX: {state_name}",
            _get_state_annex_prompt(state_context),
        )

    # Format references
    sections.references = _format_references(report_data.all_source_uris)

    # Generate methodology
    sections.methodology = _get_methodology_text(report_data)

    return sections


async def _generate_section(
    llm: ChatGoogleGenerativeAI, section_name: str, prompt: str
) -> str:
    """Generate a single narrative section."""
    messages = [
        SystemMessage(content=NARRATIVE_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        content = response.content

        # Handle content that can be string or list of content blocks
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            return "\n".join(text_parts).strip()
        else:
            return content.strip()
    except Exception as e:
        return f"[Error generating {section_name}: {str(e)}]"


def _build_data_context(report_data: ReportData) -> str:
    """Build comprehensive data context string for prompts."""
    regional = report_data.regional

    context = f"""
REPORT METADATA:
- Report ID: {report_data.report_id}
- Generated: {report_data.generated_at}
- Region: {regional.region_name}
- States Analyzed: {', '.join(regional.states_analyzed)}

REGIONAL SUMMARY:
- Total IDPs: {format_number(regional.total_idps)}
- Total Monthly Food Need: {format_number(regional.total_monthly_food_need_mt)} metric tons
- Total Conflict Events: {regional.total_conflict_events}
- Total Fatalities: {regional.total_fatalities}
- Critical States: {', '.join(regional.critical_states) or 'None identified'}
- High Risk States: {', '.join(regional.high_risk_states) or 'None identified'}
- Highest Priority State: {regional.highest_priority_state or 'N/A'}

HOTSPOT LGAS (AVOID): {', '.join(regional.all_hotspot_lgas[:15]) or 'None identified'}
SAFE LGAS (ACCESS): {', '.join(regional.all_safe_lgas[:15]) or 'None identified'}

AID ALLOCATION PROPORTIONS:
"""
    for state, pct in regional.aid_allocation_proportions.items():
        context += f"- {state}: {pct}%\n"

    context += "\nPER-STATE DATA:\n"

    for state_name, state_data in report_data.states.items():
        context += f"""
--- {state_name.upper()} ---
- IPC Phase: {state_data.ipc_phase or 'N/A'}
- IDPs: {format_number(state_data.idp_count)}
- Monthly Food Need: {format_number(state_data.monthly_food_need_mt)} MT
- Conflict Events: {state_data.conflict_events or 'N/A'}
- Fatalities: {state_data.fatalities or 'N/A'}
- Malnutrition: {state_data.malnutrition_status or 'N/A'}
- Priority Score: {state_data.priority_score or 'N/A'}
- Priority Level: {state_data.priority_level or 'N/A'}
- Conflict Trend: {state_data.conflict_trend or 'N/A'}
- Loan Adjustment Recommended: {'Yes' if state_data.loan_adjustment_recommended else 'No'}
- Loan Adjustment LGAs: {', '.join(state_data.loan_adjustment_lgas) or 'N/A'}
- Hotspot LGAs: {', '.join(state_data.hotspot_lgas[:5]) or 'N/A'}
- Safe LGAs: {', '.join(state_data.safe_lgas[:5]) or 'N/A'}
"""

    context += f"""
SOURCE URIS (use these for citations):
{chr(10).join(report_data.all_source_uris[:30])}

RAW ANALYSIS (for additional context):
{report_data.raw_analysis[:3000]}...
"""
    return context


def _build_state_context(state_name: str, state_data, report_data: ReportData) -> str:
    """Build context for a specific state annex."""
    return f"""
STATE: {state_name}

DATA:
- IPC Phase: {state_data.ipc_phase or 'N/A'}
- IDPs: {format_number(state_data.idp_count)}
- Monthly Food Need: {format_number(state_data.monthly_food_need_mt)} MT
- Conflict Events: {state_data.conflict_events or 'N/A'}
- Fatalities: {state_data.fatalities or 'N/A'}
- Malnutrition Status: {state_data.malnutrition_status or 'N/A'}
- Priority Score: {state_data.priority_score or 'N/A'}
- Priority Level: {state_data.priority_level or 'N/A'}
- Conflict Trend: {state_data.conflict_trend or 'N/A'}
- Loan Adjustment Recommended: {'Yes' if state_data.loan_adjustment_recommended else 'No'}
- Loan Adjustment LGAs: {', '.join(state_data.loan_adjustment_lgas) or 'N/A'}
- Hotspot LGAs (Avoid): {', '.join(state_data.hotspot_lgas) or 'N/A'}
- Safe LGAs (Access): {', '.join(state_data.safe_lgas) or 'N/A'}
- Access Constraints: {', '.join(state_data.access_constraints) or 'N/A'}
- Recommended Logistics: {state_data.recommended_logistics or 'N/A'}

STATE-SPECIFIC SOURCES:
{chr(10).join(state_data.source_uris[:10]) if state_data.source_uris else 'See main references'}

ALL AVAILABLE SOURCES:
{chr(10).join(report_data.all_source_uris[:20])}
"""


def _get_executive_summary_prompt(data_context: str) -> str:
    return f"""Write an EXECUTIVE SUMMARY (300-400 words) for this AEGIS humanitarian situation report.

{data_context}

The executive summary must include:
1. Opening statement on the humanitarian situation
2. Key findings (3-4 bullet points with specific numbers)
3. Priority states requiring immediate attention
4. Top 3 recommendations
5. Overall risk assessment

Cite sources for every statistical claim."""


def _get_situation_analysis_prompt(data_context: str) -> str:
    return f"""Write a SITUATION ANALYSIS section (400-500 words) covering the current humanitarian context.

{data_context}

Include:
1. Overview of the crisis affecting the region
2. Key drivers (conflict, economic, environmental)
3. Recent developments and trends
4. Comparison between states
5. Outlook for the coming period

Cite sources for all claims."""


def _get_food_security_prompt(data_context: str) -> str:
    return f"""Write a FOOD SECURITY ASSESSMENT section (400-500 words).

{data_context}

Include:
1. IPC Phase classifications by state
2. Malnutrition status and GAM rates where available
3. Food needs quantification (metric tons)
4. Market access and staple prices
5. Food aid operations and funding gaps
6. Populations requiring food assistance

Cite sources for all data points."""


def _get_displacement_prompt(data_context: str) -> str:
    return f"""Write a DISPLACEMENT ANALYSIS section (350-450 words).

{data_context}

Include:
1. Total IDP figures by state
2. Camp locations and populations
3. Displacement trends
4. Returnee situations
5. Protection concerns
6. Humanitarian needs of displaced populations

Cite DTM and other sources."""


def _get_risk_assessment_prompt(data_context: str) -> str:
    return f"""Write a RISK ASSESSMENT section (350-400 words).

{data_context}

Include:
1. Priority ranking of states (CRITICAL/HIGH/ELEVATED/MODERATE)
2. Risk scores and justification
3. Conflict trends by state
4. Areas of deterioration
5. Emerging risks
6. Risk mitigation recommendations

Cite sources for all risk assessments."""


def _get_safe_routes_prompt(data_context: str) -> str:
    return f"""Write a SAFE ROUTES ANALYSIS section (300-400 words) for humanitarian logistics.

{data_context}

Include:
1. LGAs to AVOID (hotspots, active conflict)
2. SAFE LGAs for staging and access
3. Access constraints (IEDs, abductions, military operations)
4. Recommended logistics modes (ground convoy, air drop, staged delivery)
5. Route-specific recommendations by state
6. Security considerations for aid workers

Cite sources for conflict and access data."""


def _get_recommendations_prompt(data_context: str) -> str:
    return f"""Write a RECOMMENDATIONS section (400-500 words) with actionable humanitarian recommendations.

{data_context}

Structure as:
1. IMMEDIATE ACTIONS (next 7 days)
   - Pre-positioning, emergency response
2. SHORT-TERM ACTIONS (next 30 days)
   - Scale-up, coordination
3. MEDIUM-TERM ACTIONS (next 90 days)
   - Sustained response, planning

For each recommendation:
- Be specific about WHO should act (WFP, IOM, protection cluster, etc.)
- Be specific about WHAT they should do
- Be specific about WHERE (states, LGAs)
- Quantify WHERE POSSIBLE (metric tons, beneficiaries)

Base recommendations on the data provided. Cite sources."""


def _get_loan_adjustments_prompt(data_context: str) -> str:
    return f"""Write a FARMER LOAN ADJUSTMENTS section (300-350 words) for the FARMA agricultural finance system.

{data_context}

Include:
1. States where loan adjustment is recommended (based on violence trends)
2. Specific LGAs requiring adjustment
3. Justification based on conflict data and trends
4. Recommended adjustment type (extended repayment, grace period, etc.)
5. Number of farmers potentially affected (if available)
6. Risk to agricultural livelihoods

This section informs the FARMA system about areas where farmers should receive adjusted loan terms due to conflict-related hardship.

Cite sources for conflict trend data."""


def _get_state_annex_prompt(state_context: str) -> str:
    return f"""Write a detailed STATE ANNEX (500-600 words) for this state.

{state_context}

Include:
1. State Overview
   - Geographic context
   - Current security situation
   - Humanitarian presence

2. Food Security
   - IPC classification
   - Malnutrition indicators
   - Food needs and market access

3. Displacement
   - IDP figures and locations
   - Camp conditions
   - Returnees

4. Conflict Dynamics
   - Recent events
   - Hotspot LGAs
   - Actor analysis

5. Access and Logistics
   - Safe areas
   - No-go zones
   - Recommended routes

6. Recommendations
   - State-specific actions
   - Loan adjustment recommendations

Cite sources throughout."""


def _format_references(source_uris: list) -> str:
    """Format source URIs as a numbered reference list."""
    if not source_uris:
        return "No sources available."

    references = []
    for i, uri in enumerate(source_uris, 1):
        # Try to extract domain for display
        domain = uri.split("/")[2] if len(uri.split("/")) > 2 else uri
        references.append(f"[{i}] {domain}\n    {uri}")

    return "\n\n".join(references)


def _get_methodology_text(report_data: ReportData) -> str:
    """Generate methodology section text."""
    return f"""AEGIS Methodology

This report was generated by the AEGIS (AI-Enabled Geospatial Intelligence System) using the following methodology:

DATA COLLECTION
- Conflict data sourced from ACLED (Armed Conflict Location & Event Data Project)
- Displacement data from IOM DTM (Displacement Tracking Matrix)
- Food security data from FEWS NET and humanitarian partners
- Economic data from market monitoring systems
- Google Search grounding for real-time information

ANALYSIS PROCESS
- Total tool calls: {report_data.tool_calls_made}
- States analyzed: {report_data.states_count}
- Source documents: {len(report_data.all_source_uris)}

FOOD SECURITY SCORING
Priority scores calculated based on:
- IPC Phase classification (weight: 30%)
- Affected population (weight: 25%)
- Malnutrition indicators (weight: 20%)
- Access constraints (weight: 15%)
- Conflict intensity (weight: 10%)

LIMITATIONS
- Data reflects most recent available information
- Real-time security conditions may vary
- Population figures are estimates based on surveys
- Market prices subject to rapid fluctuation

For questions about methodology, contact the AEGIS team.
"""
