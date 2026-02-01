"""Synthesis Agent Tools - LangChain tool definitions for Gemini.

Tools follow the LangChain @tool pattern with Pydantic schemas.
These are bound to Gemini via ChatGoogleGenerativeAI.bind_tools().

CORE PURPOSE: FOOD SECURITY
- Who needs food (IDP populations, affected communities)
- How much food they need (quantified)
- Where they are (precise locations)
- Safe routes to reach them (avoiding conflict zones)
- Loan adjustment triggers for farmers
"""

import pandas as pd
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import json

from langchain_core.tools import tool

from sqlalchemy import select, desc


# Lazy imports to avoid circular dependencies
def get_db_session():
    from app.aegis.db import async_session

    return async_session


def get_db_models():
    from app.aegis.db import AegisScan, StateIntelligence, ConflictEvent

    return AegisScan, StateIntelligence, ConflictEvent


# tool args schemas


class StateQueryInput(BaseModel):
    """Input for querying state data."""

    state: str = Field(
        description="Nigerian state name (e.g., 'Borno', 'Adamawa', 'Yobe')"
    )


class ConflictQueryInput(BaseModel):
    """Input for querying conflict events."""

    state: str = Field(description="Nigerian state name")
    event_type: Optional[str] = Field(
        default=None,
        description="Filter by event type: armed_attack, kidnapping, banditry, terrorism, communal_clash, military_operation",
    )
    min_fatalities: int = Field(default=0, description="Minimum fatalities to include")
    limit: int = Field(default=20, description="Maximum events to return")


class BaselineQueryInput(BaseModel):
    """Input for querying baseline data."""

    state: str = Field(description="Nigerian state name")
    weeks_back: int = Field(
        default=12, description="Weeks of historical data to analyze"
    )


class FoodSecurityScoreInput(BaseModel):
    """Input for food security scoring."""

    state: str = Field(description="Nigerian state to score")
    ipc_phase: str = Field(description="IPC phase classification")
    idp_population: int = Field(description="IDP population count")
    malnutrition_status: str = Field(description="Malnutrition status description")
    staple_prices: List[dict] = Field(description="Staple food prices")
    access_constraints: str = Field(description="Access constraints description")
    conflict_events: int = Field(description="Number of recent conflict events")
    source_uris: List[str] = Field(description="Source URIs to cite")


class SafeRouteInput(BaseModel):
    """Input for safe route analysis."""

    state: str = Field(description="Nigerian state")
    origin: str = Field(description="Origin location (e.g., state capital, hub)")
    destinations: List[str] = Field(description="Target LGAs or locations needing aid")
    conflict_hotspots: List[str] = Field(
        description="Known conflict hotspot LGAs to avoid"
    )
    access_constraints: str = Field(description="Known access constraints")


# data access tools
@tool("get_state_intel", args_schema=StateQueryInput)
async def get_state_intel(state: str) -> str:
    """
    Retrieve COMPLETE intelligence for a Nigerian state - ALL data for food security analysis.

    Returns:
    - CONFLICT: events, fatalities, actors, hotspots
    - DISPLACEMENT: IDP estimates, camp populations, locations, movements
    - FOOD SECURITY: IPC phase, malnutrition rates, humanitarian needs
    - ECONOMIC: staple prices, market access, food aid operations, farming status
    - ACCESS: constraints, safe/unsafe routes, military operations
    - SOURCES: All URIs for citation

    Use this FIRST to understand the complete situation for humanitarian aid planning.
    """
    async_session = get_db_session()
    AegisScan, StateIntelligence = get_db_models()

    async with async_session() as session:
        # Get latest completed scan
        scan_result = await session.execute(
            select(AegisScan)
            .where(AegisScan.status == "completed")
            .order_by(desc(AegisScan.completed_at))
            .limit(1)
        )
        scan = scan_result.scalar_one_or_none()
        if not scan:
            return json.dumps({"error": "No completed scans found"})

        # Get state intelligence
        intel_result = await session.execute(
            select(StateIntelligence)
            .where(StateIntelligence.scan_id == scan.id)
            .where(StateIntelligence.state_name == state)
        )
        intel = intel_result.scalar_one_or_none()

        if not intel:
            return json.dumps({"error": f"No data for state: {state}"})

        # extract all raw data
        conflict_raw = intel.conflict_raw or {}
        displacement_raw = intel.displacement_raw or {}
        food_security_raw = intel.food_security_raw or {}
        economic_raw = intel.economic_raw or {}

        # conflict data
        events = conflict_raw.get("events", [])
        total_fatalities = sum(e.get("fatalities", 0) or 0 for e in events)
        total_abducted = sum(e.get("abducted", 0) or 0 for e in events)

        # get all source URIs
        source_uris = set()
        for raw in [conflict_raw, displacement_raw, food_security_raw, economic_raw]:
            for uri in raw.get("sources_consulted", []):
                source_uris.add(uri)

        # Top incidents for context (for safe route planning)
        top_incidents = []
        for e in sorted(
            events, key=lambda x: x.get("fatalities", 0) or 0, reverse=True
        )[:5]:
            top_incidents.append(
                {
                    "date": e.get("date"),
                    "location": e.get("location"),
                    "lga": e.get("lga"),
                    "type": e.get("event_type"),
                    "fatalities": e.get("fatalities", 0),
                    "abducted": e.get("abducted", 0),
                    "summary": e.get("summary", "")[:150],
                }
            )

        # Extract high-risk LGAs from conflict events
        high_risk_lgas = list(
            set(
                e.get("lga")
                for e in events
                if e.get("lga") and (e.get("fatalities", 0) or 0) > 0
            )
        )

        return json.dumps(
            {
                # Metadata
                "state": state,
                "scan_id": scan.id,
                "scan_date": (
                    scan.completed_at.isoformat() if scan.completed_at else None
                ),
                #
                "conflict": {
                    "events_count": intel.conflict_events_count or 0,
                    "total_fatalities": total_fatalities,
                    "total_abducted": total_abducted,
                    "top_incidents": top_incidents,
                    "high_risk_lgas": high_risk_lgas,
                },
                # displacement data - who needs help and where
                "displacement": {
                    "idp_estimate": intel.idp_estimate,
                    "idp_trend": displacement_raw.get("idp_trend"),
                    "active_camps": displacement_raw.get("active_camps", []),
                    "camp_populations": displacement_raw.get("camp_populations"),
                    "origin_lgas": displacement_raw.get("origin_lgas", []),
                    "destination_lgas": displacement_raw.get("destination_lgas", []),
                    "recent_movements": displacement_raw.get("recent_movements"),
                    "humanitarian_needs": displacement_raw.get(
                        "humanitarian_needs", []
                    ),
                },
                # food (in)security data
                "food_security": {
                    "ipc_phase": intel.ipc_phase,
                    "ipc_phase_description": food_security_raw.get(
                        "ipc_phase_description"
                    ),
                    "ipc_population_affected": food_security_raw.get(
                        "ipc_population_affected"
                    ),
                    "acute_food_insecurity": intel.food_insecurity_level,
                    "food_insecurity_drivers": food_security_raw.get(
                        "food_insecurity_drivers", []
                    ),
                    "gam_rate": food_security_raw.get("gam_rate"),
                    "sam_rate": food_security_raw.get("sam_rate"),
                    "malnutrition_hotspots": food_security_raw.get(
                        "malnutrition_hotspots", []
                    ),
                    "crop_conditions": food_security_raw.get("crop_conditions"),
                    "harvest_forecast": food_security_raw.get("harvest_forecast"),
                    "agricultural_challenges": food_security_raw.get(
                        "agricultural_challenges", []
                    ),
                    "weather_impacts": food_security_raw.get("weather_impacts"),
                    "pest_disease_outbreaks": food_security_raw.get(
                        "pest_disease_outbreaks", []
                    ),
                    "food_availability": food_security_raw.get("food_availability"),
                    "priority_lgas": food_security_raw.get("priority_lgas", []),
                },
                # economic and markert data
                "economic": {
                    "markets_operational": intel.markets_operational or "unknown",
                    "closed_markets": economic_raw.get("closed_markets", []),
                    "market_access_issues": economic_raw.get("market_access_issues"),
                    "staple_prices": economic_raw.get("staple_prices", []),
                    "farming_status": economic_raw.get("farming_status"),
                    "farms_abandoned": economic_raw.get("farms_abandoned"),
                    "harvest_reports": economic_raw.get("harvest_reports"),
                    "food_aid_operations": economic_raw.get("food_aid_operations", []),
                    "inflation_rate": economic_raw.get("inflation_rate"),
                    "inflation_observations": economic_raw.get(
                        "inflation_observations"
                    ),
                },
                # access/logistics for safe routes)
                "access": {
                    "constraints": displacement_raw.get("access_constraints"),
                },
                # Sources for citation
                "source_uris": list(source_uris),
                "source_count": len(source_uris),
            },
            indent=2,
        )


@tool("get_conflict_events", args_schema=ConflictQueryInput)
async def get_conflict_events(
    state: str,
    event_type: Optional[str] = None,
    min_fatalities: int = 0,
    limit: int = 20,
) -> str:
    """
    Get detailed conflict events for route planning and risk assessment.

    Use this to identify specific incidents, locations to avoid,
    and patterns that affect aid delivery routes.
    """
    async_session = get_db_session()
    StateIntelligence, ConflictEvent = get_db_models()

    async with async_session() as session:
        query = (
            select(ConflictEvent)
            .join(StateIntelligence)
            .where(ConflictEvent.state == state)
        )

        if event_type:
            query = query.where(ConflictEvent.event_type == event_type)
        if min_fatalities > 0:
            query = query.where(ConflictEvent.fatalities >= min_fatalities)

        query = query.order_by(desc(ConflictEvent.fatalities)).limit(limit)

        result = await session.execute(query)
        events = result.scalars().all()

        return json.dumps(
            {
                "state": state,
                "filter": {"event_type": event_type, "min_fatalities": min_fatalities},
                "count": len(events),
                "events": [
                    {
                        "date": e.event_date,
                        "location": e.location,
                        "lga": e.lga,
                        "event_type": e.event_type,
                        "actors": e.actors,
                        "fatalities": e.fatalities or 0,
                        "injuries": e.injuries or 0,
                        "abducted": e.abducted or 0,
                        "summary": e.summary,
                        "source": e.source,
                    }
                    for e in events
                ],
                "high_risk_lgas": list(
                    set(e.lga for e in events if e.lga and (e.fatalities or 0) > 0)
                ),
            },
            indent=2,
        )


# reference data tools - reference data from downloaded dtm and acled xlsx files for more context, baseline
# Cache for reference data
_DTM_DATA = None
_ACLED_DATA = None


def _load_dtm():
    global _DTM_DATA
    if _DTM_DATA is None:
        try:
            _DTM_DATA = pd.read_excel(
                "ref_data/nigeria-site-assessment-round-50-north-east-idps-and-returnees-hdx.xlsx",
                sheet_name="Summary",
                skiprows=1,
            )
        except Exception:
            _DTM_DATA = pd.DataFrame()
    return _DTM_DATA


def _load_acled():
    global _ACLED_DATA
    if _ACLED_DATA is None:
        try:
            _ACLED_DATA = pd.read_excel(
                "ref_data/Africa_aggregated_data_up_to-2026-01-17.xlsx"
            )
            _ACLED_DATA = _ACLED_DATA[_ACLED_DATA["COUNTRY"] == "Nigeria"]
        except Exception:
            _ACLED_DATA = pd.DataFrame()
    return _ACLED_DATA


@tool("get_dtm_baseline", args_schema=StateQueryInput)
def get_dtm_baseline(state: str) -> str:
    """
    Get IDP/displacement baseline from DTM Round 50 official survey data.

    Use this for:
    - Verified IDP population counts (who needs food)
    - Returnee numbers
    - Ground-truth data from IOM surveys
    """
    df = _load_dtm()
    if df.empty:
        return json.dumps({"error": "DTM data not available"})

    state_row = df[df["State"].str.lower() == state.lower()]
    if state_row.empty:
        return json.dumps({"error": f"No DTM data for state: {state}"})

    row = state_row.iloc[0]

    idp_individuals = int(row.get("IDP Individuals", 0) or 0)
    returnee_individuals = int(row.get("Returnee Individuals", 0) or 0)

    return json.dumps(
        {
            "state": state,
            "data_source": "IOM DTM Round 50 (2024)",
            "idp_individuals": idp_individuals,
            "idp_households": int(row.get("IDP Households", 0) or 0),
            "returnee_individuals": returnee_individuals,
            "returnee_households": int(row.get("Returnee Households", 0) or 0),
            "total_affected": int(row.get("Total Individuals", 0) or 0),
            # Food needs estimation (rough: 2100 kcal/person/day = ~0.5kg grain equivalent)
            "estimated_monthly_food_need_mt": round(
                (idp_individuals + returnee_individuals) * 15 / 1000, 1
            ),  # 15kg/person/month
        },
        indent=2,
    )


@tool("get_acled_baseline", args_schema=BaselineQueryInput)
def get_acled_baseline(state: str, weeks_back: int = 12) -> str:
    """
    Get historical conflict baseline from ACLED data.

    Use this to:
    - Compare current violence levels to historical averages
    - Identify if situation is improving/worsening
    - Inform loan adjustment triggers for farmers
    """
    df = _load_acled()
    if df.empty:
        return json.dumps({"error": "ACLED data not available"})

    state_df = df[df["ADMIN1"].str.lower() == state.lower()]
    if state_df.empty:
        return json.dumps({"error": f"No ACLED data for state: {state}"})

    state_df = state_df.copy()
    state_df["WEEK_DT"] = pd.to_datetime(state_df["WEEK"])
    cutoff = datetime.now() - timedelta(weeks=weeks_back)
    recent = state_df[state_df["WEEK_DT"] >= cutoff]

    if recent.empty:
        return json.dumps({"error": f"No recent ACLED data for {state}"})

    total_events = int(recent["EVENTS"].sum())
    total_fatalities = int(recent["FATALITIES"].sum())
    weeks_count = recent["WEEK"].nunique()

    avg_events = total_events / max(weeks_count, 1)
    avg_fatalities = total_fatalities / max(weeks_count, 1)

    # Dominant event type
    event_counts = recent.groupby("EVENT_TYPE")["EVENTS"].sum()
    dominant_type = event_counts.idxmax() if not event_counts.empty else "Unknown"

    # Trend comparison
    midpoint = recent["WEEK_DT"].median()
    first_half = recent[recent["WEEK_DT"] < midpoint]["EVENTS"].sum()
    second_half = recent[recent["WEEK_DT"] >= midpoint]["EVENTS"].sum()

    if second_half > first_half * 1.2:
        trend = "increasing"
        loan_adjustment_recommended = True
    elif second_half < first_half * 0.8:
        trend = "decreasing"
        loan_adjustment_recommended = False
    else:
        trend = "stable"
        loan_adjustment_recommended = False

    return json.dumps(
        {
            "state": state,
            "data_source": "ACLED",
            "period": f"Last {weeks_back} weeks",
            "weeks_analyzed": weeks_count,
            "total_events": total_events,
            "total_fatalities": total_fatalities,
            "avg_events_per_week": round(avg_events, 1),
            "avg_fatalities_per_week": round(avg_fatalities, 1),
            "dominant_event_type": dominant_type,
            "historical_trend": trend,
            # For farmer loan adjustments
            "loan_adjustment_recommended": loan_adjustment_recommended,
            "loan_adjustment_reason": (
                f"Violence trend is {trend}" if loan_adjustment_recommended else None
            ),
        },
        indent=2,
    )


# analysis tools - food security
@tool("calculate_food_security_score", args_schema=FoodSecurityScoreInput)
def calculate_food_security_score(
    state: str,
    ipc_phase: str,
    idp_population: int,
    malnutrition_status: str,
    staple_prices: List[dict],
    access_constraints: str,
    conflict_events: int,
    source_uris: List[str],
) -> str:
    """
    Calculate comprehensive food security score for humanitarian aid prioritization.

    Combines:
    - IPC phase classification
    - IDP population size
    - Malnutrition severity
    - Food price stress
    - Access constraints
    - Conflict intensity

    Returns priority score for aid allocation.
    """
    # IPC Score (0-100)
    ipc_scores = {
        "phase 5": 100,
        "famine": 100,
        "phase 4": 85,
        "emergency": 85,
        "critical": 85,
        "phase 3": 65,
        "crisis": 65,
        "phase 2": 40,
        "stressed": 40,
        "phase 1": 15,
        "minimal": 15,
    }
    ipc_lower = ipc_phase.lower() if ipc_phase else ""
    ipc_score = 50  # default
    for key, score in ipc_scores.items():
        if key in ipc_lower:
            ipc_score = score
            break

    # Population Score (0-100) - more IDPs = higher priority
    if idp_population > 1000000:
        population_score = 100
    elif idp_population > 500000:
        population_score = 85
    elif idp_population > 100000:
        population_score = 65
    elif idp_population > 50000:
        population_score = 45
    else:
        population_score = 25

    # Malnutrition Score (0-100)
    malnutrition_lower = malnutrition_status.lower() if malnutrition_status else ""
    if (
        "phase 4" in malnutrition_lower
        or "critical" in malnutrition_lower
        or "gam" in malnutrition_lower
    ):
        malnutrition_score = 100
    elif "phase 3" in malnutrition_lower or "serious" in malnutrition_lower:
        malnutrition_score = 75
    elif "phase 2" in malnutrition_lower or "alert" in malnutrition_lower:
        malnutrition_score = 50
    else:
        malnutrition_score = 25

    # Access Score (0-100) - harder access = higher urgency but lower feasibility
    access_lower = access_constraints.lower() if access_constraints else ""
    if "inaccessible" in access_lower or "million" in access_lower:
        access_score = 90
    elif "ied" in access_lower or "abduction" in access_lower:
        access_score = 75
    elif "risk" in access_lower or "constraint" in access_lower:
        access_score = 50
    else:
        access_score = 25

    # Conflict Score (0-100)
    conflict_score = min(100, conflict_events * 10)

    # Composite Score (weighted for food security priority)
    composite = (
        ipc_score * 0.30  # Food security classification most important
        + population_score * 0.25  # Number of people affected
        + malnutrition_score * 0.25  # Nutritional urgency
        + access_score * 0.10  # Delivery challenges
        + conflict_score * 0.10  # Security context
    )

    # Priority level
    if composite >= 80:
        priority = "CRITICAL"
        aid_urgency = "Immediate intervention required within 72 hours"
    elif composite >= 60:
        priority = "HIGH"
        aid_urgency = "Intervention required within 1-2 weeks"
    elif composite >= 40:
        priority = "ELEVATED"
        aid_urgency = "Intervention required within 1 month"
    else:
        priority = "MODERATE"
        aid_urgency = "Monitoring and standard programming"

    # Estimated food need
    monthly_food_mt = round(idp_population * 15 / 1000, 1)  # 15kg/person/month

    return json.dumps(
        {
            "state": state,
            "food_security_priority": priority,
            "composite_score": round(composite, 1),
            "aid_urgency": aid_urgency,
            "estimated_beneficiaries": idp_population,
            "estimated_monthly_food_need_mt": monthly_food_mt,
            "score_breakdown": {
                "ipc_phase_score": round(ipc_score, 1),
                "population_score": round(population_score, 1),
                "malnutrition_score": round(malnutrition_score, 1),
                "access_score": round(access_score, 1),
                "conflict_score": round(conflict_score, 1),
            },
            "weights": {
                "ipc_phase": 0.30,
                "population": 0.25,
                "malnutrition": 0.25,
                "access": 0.10,
                "conflict": 0.10,
            },
            "sources_cited": len(source_uris),
        },
        indent=2,
    )


# analysis - safe routes to get food/needs delivered
@tool("analyze_safe_routes", args_schema=SafeRouteInput)
def analyze_safe_routes(
    state: str,
    origin: str,
    destinations: List[str],
    conflict_hotspots: List[str],
    access_constraints: str,
) -> str:
    """
    Analyze and recommend safe routes for humanitarian aid delivery.

    Based on:
    - Known conflict hotspots to avoid
    - Access constraints
    - Recent incident patterns

    Returns route recommendations with risk assessments.
    """
    # Analyze each destination
    route_assessments = []
    hotspots_lower = [h.lower() for h in conflict_hotspots]

    for dest in destinations:
        dest_lower = dest.lower()

        # Check if destination is in hotspot
        is_hotspot = any(h in dest_lower or dest_lower in h for h in hotspots_lower)

        # Risk level
        if is_hotspot:
            risk_level = "HIGH"
            recommendation = f"AVOID direct route to {dest}. Consider staging from safer adjacent LGA."
        elif any(
            h in access_constraints.lower() for h in [dest_lower, "ied", "abduction"]
        ):
            risk_level = "ELEVATED"
            recommendation = f"Use convoy system with security escort for {dest}."
        else:
            risk_level = "MODERATE"
            recommendation = f"Standard precautions for {dest}. Daylight movement only."

        route_assessments.append(
            {
                "destination": dest,
                "risk_level": risk_level,
                "is_conflict_hotspot": is_hotspot,
                "recommendation": recommendation,
            }
        )

    # Overall logistics recommendation
    high_risk_count = sum(1 for r in route_assessments if r["risk_level"] == "HIGH")

    if high_risk_count > len(destinations) / 2:
        logistics_mode = "AIR_DROP"
        logistics_note = "Majority of destinations are high-risk. Consider helicopter or air drop for critical supplies."
    elif high_risk_count > 0:
        logistics_mode = "STAGED_CONVOY"
        logistics_note = "Use staged approach: deliver to safe hubs, then redistribute with local partners."
    else:
        logistics_mode = "GROUND_CONVOY"
        logistics_note = "Ground convoy feasible with standard security protocols."

    return json.dumps(
        {
            "state": state,
            "origin": origin,
            "route_assessments": route_assessments,
            "conflict_hotspots_to_avoid": conflict_hotspots,
            "access_constraints_noted": (
                access_constraints[:200] if access_constraints else None
            ),
            "recommended_logistics_mode": logistics_mode,
            "logistics_note": logistics_note,
            "general_precautions": [
                "Movement during daylight hours only (0600-1600)",
                "Notify local authorities and military before movement",
                "Maintain communication with security operations center",
                "Avoid predictable patterns and schedules",
            ],
        },
        indent=2,
    )


SYNTHESIS_TOOLS = [
    # Data Access (async) - returns ALL data
    get_state_intel,
    get_conflict_events,
    # Reference Data (sync)
    get_dtm_baseline,
    get_acled_baseline,
    # Food Security Analysis (sync)
    calculate_food_security_score,
    analyze_safe_routes,
]
