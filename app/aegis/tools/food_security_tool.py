"""AEGIS Food Security Tool - Food security and agricultural conditions data.

Focuses on humanitarian food security indicators for North East Nigeria.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

from .shared import AEGIS_FOCUS_STATES
from .shared_grounding import (
    GroundingMetadata,
    grounded_search,
    get_date_range,
)


class FoodSecurityReport(BaseModel):
    """Food security and agricultural conditions data."""

    state: str = Field(default="", description="State being assessed")
    search_date: str = Field(default="", description="Date this search was performed")
    timeframe: str = Field(default="", description="Timeframe of search")

    # IPC Classification (Integrated Food Security Phase Classification)
    ipc_phase: Optional[int] = Field(
        default=None, description="Current IPC phase (1-5) for the state"
    )
    ipc_phase_description: Optional[str] = Field(
        default=None,
        description="Phase description: Minimal(1), Stressed(2), Crisis(3), Emergency(4), Famine(5)",
    )
    ipc_population_affected: Optional[int] = Field(
        default=None, description="Number of people in crisis or worse (Phase 3+)"
    )
    ipc_source: Optional[str] = Field(
        default=None, description="Source of IPC data (Cadre Harmonisé, FEWS NET, etc.)"
    )

    # Acute Food Insecurity
    acute_food_insecurity: Literal[
        "minimal", "stressed", "crisis", "emergency", "famine", "unknown"
    ] = Field(default="unknown", description="Overall acute food insecurity level")
    food_insecurity_drivers: List[str] = Field(
        default_factory=list,
        description="Factors driving food insecurity (conflict, drought, prices, etc.)",
    )

    # Malnutrition Indicators
    gam_rate: Optional[float] = Field(
        default=None, description="Global Acute Malnutrition rate (%) if reported"
    )
    sam_rate: Optional[float] = Field(
        default=None, description="Severe Acute Malnutrition rate (%) if reported"
    )
    malnutrition_hotspots: List[str] = Field(
        default_factory=list, description="LGAs with critical malnutrition levels"
    )

    # Crop and Harvest Conditions
    current_season: Optional[str] = Field(
        default=None, description="Current agricultural season (planting, growing, harvest)"
    )
    crop_conditions: Literal[
        "good", "fair", "poor", "failed", "mixed", "unknown"
    ] = Field(default="unknown", description="Overall crop conditions")
    crop_conditions_detail: Optional[str] = Field(
        default=None, description="Details on crop situation"
    )
    harvest_forecast: Optional[str] = Field(
        default=None, description="Expected harvest outcome"
    )
    crops_affected: List[str] = Field(
        default_factory=list, description="Specific crops facing challenges"
    )

    # Agricultural Challenges
    agricultural_challenges: List[str] = Field(
        default_factory=list,
        description="Challenges: drought, flooding, pests, disease, insecurity, etc.",
    )
    weather_impacts: Optional[str] = Field(
        default=None, description="Weather/climate impacts on agriculture"
    )
    pest_disease_outbreaks: List[str] = Field(
        default_factory=list, description="Reported pest or crop disease outbreaks"
    )

    # Food Availability
    food_availability: Literal[
        "adequate", "tight", "limited", "scarce", "unknown"
    ] = Field(default="unknown", description="Food availability in markets")
    food_sources: List[str] = Field(
        default_factory=list,
        description="Main food sources: own production, markets, aid, etc.",
    )

    # Vulnerable LGAs
    priority_lgas: List[str] = Field(
        default_factory=list, description="LGAs with worst food security situation"
    )

    # Sources
    sources_consulted: List[str] = Field(
        default_factory=list, description="Source URIs from grounding"
    )

    # Grounding metadata
    grounding: Optional[GroundingMetadata] = Field(
        default=None, description="Google Search grounding metadata"
    )
    
    # Error tracking - None means successful collection
    error: Optional[str] = Field(
        default=None, description="Error message if data collection failed"
    )


def search_food_security(
    state: str,
    days_back: int = 14,
) -> FoodSecurityReport:
    """
    Search for food security and agricultural conditions data.

    Focuses on:
    - IPC classifications and food insecurity levels
    - Malnutrition indicators
    - Crop conditions and harvest forecasts
    - Agricultural challenges (weather, pests, disease)
    """
    print(f"[AEGIS] Food Security Search: {state} ({days_back} days)")

    if state not in AEGIS_FOCUS_STATES:
        print(f"   Warning: {state} not in AEGIS focus states")

    date_range, year = get_date_range(days_back)

    # Grounded search prompt
    search_prompt = f"""Search for current food security and agricultural conditions in {state} State, Nigeria.

IMPORTANT: Focus on data from {date_range} (year {year}).

Search for:

1. IPC CLASSIFICATION (Integrated Food Security Phase):
   - Current IPC phase for {state} (Phase 1-5)
   - Number of people in Crisis (Phase 3) or worse
   - Source: Cadre Harmonisé, FEWS NET, WFP, or government data
   - Which LGAs are in the worst phases?

2. FOOD INSECURITY DRIVERS:
   - What is causing food insecurity? (conflict displacement, drought, high prices, market disruption)
   - How severe is acute food insecurity?

3. MALNUTRITION:
   - Global Acute Malnutrition (GAM) rate if available
   - Severe Acute Malnutrition (SAM) rate if available
   - Which LGAs have critical malnutrition?

4. CROP AND HARVEST CONDITIONS:
   - Current agricultural season (planting, growing, lean season, harvest)
   - How are crops doing? (good, fair, poor, failed)
   - Expected harvest outlook
   - Which crops are affected?

5. AGRICULTURAL CHALLENGES:
   - Drought or flooding impacts
   - Pest outbreaks (locusts, armyworms, etc.)
   - Crop diseases
   - Farmers unable to access fields due to insecurity

6. FOOD AVAILABILITY:
   - Is food available in markets?
   - Where are people getting food? (own production, markets, food aid)

Be thorough and cite your sources. Focus on humanitarian and agricultural data."""

    # Extract prompt
    extract_prompt = f"""Extract structured food security data from this report about {state} State, Nigeria.

SOURCE TEXT:
{{grounded_text}}

Extract as JSON with this exact schema:
{{
  "state": "{state}",
  "ipc_phase": 1-5 or null,
  "ipc_phase_description": "Minimal/Stressed/Crisis/Emergency/Famine" or null,
  "ipc_population_affected": number or null,
  "ipc_source": "source name" or null,
  "acute_food_insecurity": "minimal" | "stressed" | "crisis" | "emergency" | "famine" | "unknown",
  "food_insecurity_drivers": ["driver1", "driver2"],
  "gam_rate": percentage or null,
  "sam_rate": percentage or null,
  "malnutrition_hotspots": ["LGA names"],
  "current_season": "season description" or null,
  "crop_conditions": "good" | "fair" | "poor" | "failed" | "mixed" | "unknown",
  "crop_conditions_detail": "details" or null,
  "harvest_forecast": "forecast" or null,
  "crops_affected": ["crop names"],
  "agricultural_challenges": ["challenge1", "challenge2"],
  "weather_impacts": "description" or null,
  "pest_disease_outbreaks": ["outbreak1", "outbreak2"],
  "food_availability": "adequate" | "tight" | "limited" | "scarce" | "unknown",
  "food_sources": ["source1", "source2"],
  "priority_lgas": ["LGA names with worst situation"]
}}

Only include data from {date_range}.
Return ONLY valid JSON."""

    result, grounding = grounded_search(
        search_prompt=search_prompt,
        extract_prompt=extract_prompt,
        result_class=FoodSecurityReport,
        debug=True,
    )

    # Handle error dict from grounded_search
    if isinstance(result, dict) and "_collection_error" in result:
        return FoodSecurityReport(
            state=state,
            search_date=datetime.now().strftime("%Y-%m-%d"),
            timeframe=date_range,
            sources_consulted=[s.uri for s in grounding.sources] if grounding.sources else [],
            grounding=grounding,
            error=result["_collection_error"],
        )

    if result:
        result.search_date = datetime.now().strftime("%Y-%m-%d")
        result.timeframe = date_range
        result.sources_consulted = [s.uri for s in grounding.sources]
        result.grounding = grounding

        # Log results
        phase_icons = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴", 5: "⚫"}
        if result.ipc_phase:
            icon = phase_icons.get(result.ipc_phase, "❓")
            print(f"\n   IPC Phase: {icon} {result.ipc_phase} ({result.ipc_phase_description or 'N/A'})")
            if result.ipc_population_affected:
                print(f"   Population affected: {result.ipc_population_affected:,}")

        print(f"   Food insecurity: {result.acute_food_insecurity.upper()}")
        print(f"   Crop conditions: {result.crop_conditions}")
        print(f"   Food availability: {result.food_availability}")

        if result.food_insecurity_drivers:
            print(f"   Drivers: {', '.join(result.food_insecurity_drivers[:3])}")
        if result.agricultural_challenges:
            print(f"   Ag challenges: {', '.join(result.agricultural_challenges[:3])}")
        if result.priority_lgas:
            print(f"   Priority LGAs: {', '.join(result.priority_lgas[:4])}")
        
        return result

    # Fallback if result is None (shouldn't happen with new grounded_search)
    return FoodSecurityReport(
        state=state,
        search_date=datetime.now().strftime("%Y-%m-%d"),
        timeframe=date_range,
        error="Unknown error: grounded_search returned None",
    )
