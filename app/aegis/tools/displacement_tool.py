from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

from .shared import AEGIS_FOCUS_STATES
from .shared_grounding import (
    GroundingSource,
    GroundingMetadata,
    grounded_search,
    get_date_range,
)


class DisplacementReport(BaseModel):
    """Displacement and humanitarian situation data"""

    state: str = Field(default="", description="State being assessed")
    search_date: str = Field(default="", description="Date this search was performed")
    timeframe: str = Field(default="", description="Timeframe of search")

    # IDP Numbers
    idp_estimate: Optional[int] = Field(
        default=None, description="Estimated IDP population from official sources"
    )
    idp_source: Optional[str] = Field(
        default=None, description="Source of IDP estimate (UNHCR, IOM, NEMA, etc.)"
    )
    idp_trend: Literal["increasing", "stable", "decreasing", "unknown"] = Field(
        default="unknown", description="Whether IDP numbers are rising or falling"
    )

    # Camp and Settlement Data
    active_camps: List[str] = Field(
        default_factory=list, description="Names of active IDP camps"
    )
    camp_populations: Optional[str] = Field(
        default=None, description="Population figures for camps if available"
    )

    # Movement Patterns
    recent_movements: str = Field(
        default="", description="Summary of recent displacement movements"
    )
    origin_lgas: List[str] = Field(
        default_factory=list, description="LGAs people are fleeing FROM"
    )
    destination_lgas: List[str] = Field(
        default_factory=list, description="LGAs people are fleeing TO"
    )

    # Humanitarian Situation (factual observations)
    humanitarian_needs: List[str] = Field(
        default_factory=list, description="Key humanitarian needs reported"
    )
    food_security_phase: Optional[str] = Field(
        default=None, description="IPC phase if reported (Phase 1-5)"
    )
    malnutrition_reported: Optional[str] = Field(
        default=None, description="Malnutrition data if reported"
    )

    # Access
    access_constraints: Optional[str] = Field(
        default=None, description="Any access or security constraints for aid reported"
    )

    # Sources (URIs from grounding metadata
    sources_consulted: List[str] = Field(
        default_factory=list, description="Source URIs from grounding"
    )

    # Grounding metadata for traceability
    grounding: Optional[GroundingMetadata] = Field(
        default=None, description="Google Search grounding metadata"
    )


def search_displacement(
    state: str,
    days_back: int = 7,
) -> Optional[DisplacementReport]:
    """
    Search for displacement and humanitarian data in a state."""

    print(f"Displacement Search: {state} ({days_back} days)")

    if state not in AEGIS_FOCUS_STATES:
        print(f"{state} not in focus states")

    date_range, year = get_date_range(days_back)

    # Grounded search prompt
    search_prompt = f"""Search for current information on internally displaced persons (IDPs) in {state} State, Nigeria.

IMPORTANT: Focus on data from {date_range} (year {year}).

Search for:

1. IDP NUMBERS:
   - Current IDP estimates from UNHCR, IOM, NEMA, DTM, or state government
   - Is displacement increasing, stable, or decreasing?

2. CAMPS AND SETTLEMENTS:
   - Names of active IDP camps or settlements
   - Population figures for camps

3. DISPLACEMENT MOVEMENTS:
   - Which LGAs are people fleeing FROM?
   - Which LGAs are people fleeing TO?
   - What's causing recent displacement?

4. HUMANITARIAN SITUATION:
   - Key humanitarian needs (food, water, shelter, medical, protection)
   - IPC food security phase (Phase 1-5)
   - Malnutrition data

5. ACCESS CONSTRAINTS:
   - Security constraints for humanitarian organizations

Be thorough and cite your sources."""

    # extract data
    extract_prompt = f"""Extract structured displacement data from this report about {state} State, Nigeria.

SOURCE TEXT:
{{grounded_text}}

Extract as JSON with this exact schema:
{{
  "state": "{state}",
  "idp_estimate": number or null,
  "idp_source": "organization name" or null,
  "idp_trend": "increasing" | "stable" | "decreasing" | "unknown",
  "active_camps": ["camp names"],
  "camp_populations": "population info" or null,
  "recent_movements": "summary of movements",
  "origin_lgas": ["LGAs people fleeing FROM"],
  "destination_lgas": ["LGAs people fleeing TO"],
  "humanitarian_needs": ["need1", "need2"],
  "food_security_phase": "Phase X" or null,
  "malnutrition_reported": "data" or null,
  "access_constraints": "constraints" or null
}}

Only include data from {date_range}.
Return ONLY valid JSON."""

    result, grounding = grounded_search(
        search_prompt=search_prompt,
        extract_prompt=extract_prompt,
        result_class=DisplacementReport,
        debug=True,
    )

    if result:
        result.search_date = datetime.now().strftime("%Y-%m-%d")
        result.timeframe = date_range
        result.sources_consulted = [s.uri for s in grounding.sources]
        result.grounding = grounding

        # results
        idp_str = f"{result.idp_estimate:,}" if result.idp_estimate else "Unknown"
        camps_count = len(result.active_camps)
        print(f"\nIDPs: {idp_str} ({result.idp_trend})")
        print(f"Camps: {camps_count}")
        if result.humanitarian_needs:
            print(f"Needs: {', '.join(result.humanitarian_needs)}")
        if result.origin_lgas:
            print(f"Fleeing FROM: {', '.join(result.origin_lgas)}")
        if result.destination_lgas:
            print(f"Fleeing TO: {', '.join(result.destination_lgas)}")
        if result.food_security_phase:
            print(f"IPC Phase: {result.food_security_phase}")

    return result
