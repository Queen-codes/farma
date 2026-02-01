"""Data Extractor - Parse synthesis agent output into structured report data.

Extracts all relevant data from the synthesis agent's final state
and organizes it for report generation.
"""

import re
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from langchain_core.messages import AIMessage


@dataclass
class StateData:
    """Data for a single Nigerian state."""

    name: str

    # Food Security
    ipc_phase: Optional[str] = None
    ipc_description: Optional[str] = None
    malnutrition_status: Optional[str] = None
    gam_rate: Optional[float] = None
    food_aid_operations: List[str] = field(default_factory=list)
    humanitarian_needs: List[str] = field(default_factory=list)

    # Displacement
    idp_count: Optional[int] = None
    idp_source: Optional[str] = None
    camp_count: Optional[int] = None
    camp_locations: List[str] = field(default_factory=list)
    returnees: Optional[int] = None

    # Food Needs
    monthly_food_need_mt: Optional[float] = None
    staple_prices: Dict[str, float] = field(default_factory=dict)
    market_access: Optional[str] = None
    funding_gap: Optional[str] = None

    # Conflict
    conflict_events: Optional[int] = None
    fatalities: Optional[int] = None
    hotspot_lgas: List[str] = field(default_factory=list)
    conflict_trend: Optional[str] = None  # INCREASING, STABLE, DECREASING

    # Safe Routes
    safe_lgas: List[str] = field(default_factory=list)
    avoid_lgas: List[str] = field(default_factory=list)
    access_constraints: List[str] = field(default_factory=list)
    recommended_logistics: Optional[str] = None

    # Loan Adjustments
    loan_adjustment_recommended: bool = False
    loan_adjustment_lgas: List[str] = field(default_factory=list)
    loan_adjustment_reason: Optional[str] = None

    # Priority
    priority_score: Optional[float] = None
    priority_level: Optional[str] = None  # CRITICAL, HIGH, ELEVATED, MODERATE

    # Sources
    source_uris: List[str] = field(default_factory=list)


@dataclass
class RegionalSummary:
    """Aggregated regional summary across all states."""

    region_name: str = "North East Nigeria"
    states_analyzed: List[str] = field(default_factory=list)

    # Aggregates
    total_idps: int = 0
    total_camps: int = 0
    total_monthly_food_need_mt: float = 0
    total_conflict_events: int = 0
    total_fatalities: int = 0

    # Regional metrics
    avg_ipc_phase: Optional[float] = None
    highest_priority_state: Optional[str] = None
    critical_states: List[str] = field(default_factory=list)
    high_risk_states: List[str] = field(default_factory=list)

    # All hotspots
    all_hotspot_lgas: List[str] = field(default_factory=list)
    all_safe_lgas: List[str] = field(default_factory=list)

    # Recommendations
    aid_allocation_proportions: Dict[str, float] = field(default_factory=dict)


@dataclass
class ReportData:
    """Complete structured data for report generation."""

    # Metadata
    report_id: str = ""
    scan_id: int = 0
    generated_at: str = ""
    analysis_period: str = ""

    # Regional summary
    regional: RegionalSummary = field(default_factory=RegionalSummary)

    # Per-state data
    states: Dict[str, StateData] = field(default_factory=dict)

    # Raw analysis text from LLM (for narrative extraction)
    raw_analysis: str = ""

    # All source URIs (deduplicated)
    all_source_uris: List[str] = field(default_factory=list)

    # Audit trail
    audit_entries: List[Dict] = field(default_factory=list)

    # Thinking blocks (for transparency section)
    thinking_blocks: List[Dict] = field(default_factory=list)

    # Metrics
    tool_calls_made: int = 0
    states_count: int = 0


def extract_report_data(synthesis_state: Dict[str, Any]) -> ReportData:
    """Extract structured report data from synthesis agent output.

    Args:
        synthesis_state: Final state dict from run_synthesis()

    Returns:
        ReportData with all structured information
    """
    report_data = ReportData(
        report_id=synthesis_state.get("run_id", ""),
        scan_id=synthesis_state.get("scan_id", 0),
        generated_at=datetime.now(timezone.utc).isoformat(),
        all_source_uris=synthesis_state.get("all_source_uris", []),
        audit_entries=synthesis_state.get("audit_entries", []),
        thinking_blocks=synthesis_state.get("thinking_blocks", []),
        tool_calls_made=synthesis_state.get("tool_call_count", 0),
    )

    # Extract raw analysis text from final message
    messages = synthesis_state.get("messages", [])
    for msg in reversed(messages):
        if (
            isinstance(msg, AIMessage)
            and msg.content
            and not getattr(msg, "tool_calls", None)
        ):
            # Handle content that can be string or list of content blocks
            content = msg.content
            if isinstance(content, list):
                # Extract text from content blocks
                text_parts = []
                for block in content:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                report_data.raw_analysis = "\n".join(text_parts)
            else:
                report_data.raw_analysis = content
            break

    # Parse states from synthesis state
    states_to_analyze = synthesis_state.get("states_to_analyze", [])
    report_data.states_count = len(states_to_analyze)
    report_data.regional.states_analyzed = states_to_analyze

    # Extract structured data by parsing the raw analysis
    # This parses the LLM's final markdown output
    if report_data.raw_analysis:
        _parse_analysis_text(report_data)

    # Build regional summary from state data
    _build_regional_summary(report_data)

    return report_data


def _parse_analysis_text(report_data: ReportData) -> None:
    """Parse the LLM's markdown analysis into structured state data.

    This handles the format from the synthesis agent's output.
    """
    text = report_data.raw_analysis

    # Extract states mentioned
    for state_name in report_data.regional.states_analyzed:
        state_data = StateData(name=state_name)

        # Try to find state-specific sections
        state_pattern = (
            rf"(?i)(?:^|\n)(?:#+\s*)?{state_name}[:\s]*(.*?)(?=\n#+|\n\n[A-Z]|\Z)"
        )
        state_match = re.search(state_pattern, text, re.DOTALL)

        state_section = state_match.group(0) if state_match else text

        # Extract IPC Phase
        ipc_match = re.search(
            r"IPC\s*(?:Phase)?\s*[:\s]*(\d+\+?|Phase\s*\d+\+?|Crisis|Emergency|Famine)",
            state_section,
            re.IGNORECASE,
        )
        if ipc_match:
            state_data.ipc_phase = ipc_match.group(1)

        # Extract IDP count
        idp_match = re.search(
            r"(\d[\d,\.]*)\s*(?:million|M)?\s*(?:IDPs?|internally displaced)",
            state_section,
            re.IGNORECASE,
        )
        if idp_match:
            idp_str = idp_match.group(1).replace(",", "")
            try:
                idp_val = float(idp_str)
                # Check if it mentions "million"
                if (
                    "million" in idp_match.group(0).lower()
                    or "m" in idp_match.group(0).lower()
                ):
                    idp_val = idp_val * 1_000_000
                state_data.idp_count = int(idp_val)
            except ValueError:
                pass

        # Extract food needs (metric tons)
        food_match = re.search(
            r"(\d[\d,\.]*)\s*(?:MT|metric tons?|tonnes?)",
            state_section,
            re.IGNORECASE,
        )
        if food_match:
            try:
                state_data.monthly_food_need_mt = float(
                    food_match.group(1).replace(",", "")
                )
            except ValueError:
                pass

        # Extract conflict events
        events_match = re.search(
            r"(\d+)\s*(?:conflict\s*)?(?:events?|incidents?)",
            state_section,
            re.IGNORECASE,
        )
        if events_match:
            try:
                state_data.conflict_events = int(events_match.group(1))
            except ValueError:
                pass

        # Extract fatalities
        fatalities_match = re.search(
            r"(\d[\d,]*)\s*(?:fatalities|deaths|killed)",
            state_section,
            re.IGNORECASE,
        )
        if fatalities_match:
            try:
                state_data.fatalities = int(fatalities_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Extract hotspot LGAs
        hotspot_match = re.search(
            r"(?:hotspots?|high[- ]risk|avoid|no-go)[:\s]*([A-Za-z,\s]+?)(?:\.|$|\n)",
            state_section,
            re.IGNORECASE,
        )
        if hotspot_match:
            lgas = [
                lga.strip()
                for lga in hotspot_match.group(1).split(",")
                if lga.strip() and len(lga.strip()) > 2
            ]
            state_data.hotspot_lgas = lgas[:10]  # Limit
            state_data.avoid_lgas = lgas[:10]

        # Extract safe LGAs
        safe_match = re.search(
            r"(?:safe|staging|access)[:\s]*([A-Za-z,\s]+?)(?:\.|$|\n)",
            state_section,
            re.IGNORECASE,
        )
        if safe_match:
            lgas = [
                lga.strip()
                for lga in safe_match.group(1).split(",")
                if lga.strip() and len(lga.strip()) > 2
            ]
            state_data.safe_lgas = lgas[:10]

        # Extract priority score
        score_match = re.search(
            r"(?:priority\s*)?score[:\s]*(\d+(?:\.\d+)?)",
            state_section,
            re.IGNORECASE,
        )
        if score_match:
            try:
                state_data.priority_score = float(score_match.group(1))
            except ValueError:
                pass

        # Extract priority level
        level_match = re.search(
            r"(CRITICAL|HIGH|ELEVATED|MODERATE)\s*(?:priority|risk)?",
            state_section,
            re.IGNORECASE,
        )
        if level_match:
            state_data.priority_level = level_match.group(1).upper()

        # Extract malnutrition status
        mal_match = re.search(
            r"malnutrition[:\s]*(.*?)(?:\.|$|\n)",
            state_section,
            re.IGNORECASE,
        )
        if mal_match:
            state_data.malnutrition_status = mal_match.group(1).strip()[:100]

        # Extract conflict trend
        trend_match = re.search(
            r"(?:violence|conflict)\s*(?:trend)?[:\s]*(INCREASING|STABLE|DECREASING|increasing|stable|decreasing)",
            state_section,
            re.IGNORECASE,
        )
        if trend_match:
            state_data.conflict_trend = trend_match.group(1).upper()

        # Extract loan adjustment recommendation
        loan_match = re.search(
            r"loan\s*(?:adjustment|repayment)[:\s]*(recommended|yes|required)",
            state_section,
            re.IGNORECASE,
        )
        if loan_match:
            state_data.loan_adjustment_recommended = True

        # Extract loan adjustment LGAs
        loan_lga_match = re.search(
            r"loan\s*adjustment[s]?\s*(?:for|in|:)\s*([A-Za-z,\s]+?)(?:\.|$|\n)",
            state_section,
            re.IGNORECASE,
        )
        if loan_lga_match:
            lgas = [
                lga.strip()
                for lga in loan_lga_match.group(1).split(",")
                if lga.strip() and len(lga.strip()) > 2
            ]
            state_data.loan_adjustment_lgas = lgas[:10]

        # Extract source URIs mentioned for this state
        uri_pattern = r"(?:source|uri|ref)[:\s]*(https?://[^\s\)]+)"
        uris = re.findall(uri_pattern, state_section, re.IGNORECASE)
        state_data.source_uris = list(set(uris))

        report_data.states[state_name] = state_data


def _build_regional_summary(report_data: ReportData) -> None:
    """Build regional aggregates from state data."""
    regional = report_data.regional

    for state_name, state_data in report_data.states.items():
        # Sum totals
        if state_data.idp_count:
            regional.total_idps += state_data.idp_count
        if state_data.monthly_food_need_mt:
            regional.total_monthly_food_need_mt += state_data.monthly_food_need_mt
        if state_data.conflict_events:
            regional.total_conflict_events += state_data.conflict_events
        if state_data.fatalities:
            regional.total_fatalities += state_data.fatalities

        # Collect hotspots
        regional.all_hotspot_lgas.extend(state_data.hotspot_lgas)
        regional.all_safe_lgas.extend(state_data.safe_lgas)

        # Categorize by priority
        if state_data.priority_level == "CRITICAL":
            regional.critical_states.append(state_name)
        elif state_data.priority_level == "HIGH":
            regional.high_risk_states.append(state_name)

    # Deduplicate
    regional.all_hotspot_lgas = list(set(regional.all_hotspot_lgas))
    regional.all_safe_lgas = list(set(regional.all_safe_lgas))

    # Find highest priority state
    highest_score = 0
    for state_name, state_data in report_data.states.items():
        if state_data.priority_score and state_data.priority_score > highest_score:
            highest_score = state_data.priority_score
            regional.highest_priority_state = state_name

    # Calculate aid allocation proportions based on priority scores
    total_score = sum(
        s.priority_score for s in report_data.states.values() if s.priority_score
    )
    if total_score > 0:
        for state_name, state_data in report_data.states.items():
            if state_data.priority_score:
                regional.aid_allocation_proportions[state_name] = round(
                    (state_data.priority_score / total_score) * 100, 1
                )


def format_number(num: Optional[int | float], suffix: str = "") -> str:
    """Format large numbers for display (e.g., 1.5M, 25K)."""
    if num is None:
        return "N/A"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M{suffix}"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K{suffix}"
    return f"{num:,.0f}{suffix}"
