from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

from .shared import AEGIS_FOCUS_STATES
from .shared_grounding import (
    GroundingMetadata,
    grounded_search,
    get_date_range,
)


class MarketPrice(BaseModel):
    """Price data for a commodity"""

    commodity: str = Field(
        description="Name of the commodity (e.g., maize, rice, sorghum)"
    )
    price_naira: Optional[float] = Field(
        default=None, description="Current price in Naira"
    )
    unit: str = Field(default="kg", description="Unit of measurement")
    price_change: Literal[
        "stable", "increasing", "decreasing", "volatile", "unknown"
    ] = Field(default="unknown", description="Price trend direction as reported")
    percent_change: Optional[float] = Field(
        default=None, description="Percentage change if reported"
    )
    source: Optional[str] = Field(default=None, description="Source of price data")


class EconomicReport(BaseModel):
    """Economic and food security data"""

    state: str = Field(default="", description="State being assessed")
    search_date: str = Field(default="", description="Date this search was performed")
    timeframe: str = Field(default="", description="Timeframe of search")

    # Market Access
    markets_operational: Literal["fully", "partially", "closed", "unknown"] = Field(
        default="unknown", description="Status of major markets as reported"
    )
    closed_markets: List[str] = Field(
        default_factory=list, description="Names of closed or disrupted markets"
    )
    market_access_issues: List[str] = Field(
        default_factory=list, description="Reported issues affecting market access"
    )

    # Commodity Prices
    staple_prices: List[MarketPrice] = Field(
        default_factory=list, description="Prices for key staples"
    )
    price_data_date: Optional[str] = Field(
        default=None, description="Date of price data if specified"
    )

    # Currency and Inflation
    inflation_rate: Optional[float] = Field(
        default=None, description="Current inflation rate if reported"
    )
    naira_exchange_rate: Optional[str] = Field(
        default=None, description="Exchange rate if reported"
    )
    inflation_observations: Optional[str] = Field(
        default=None, description="What sources say about inflation impact"
    )

    # Agricultural Situation
    farming_status: Optional[str] = Field(
        default=None, description="Status of farming activities"
    )
    farms_abandoned: Optional[str] = Field(
        default=None, description="Reports of abandoned farms if any"
    )
    harvest_reports: Optional[str] = Field(
        default=None, description="Harvest situation as reported"
    )

    # Food Assistance
    food_aid_operations: List[str] = Field(
        default_factory=list, description="Ongoing food assistance programs"
    )

    # Sources (URIs from grounding)
    sources_consulted: List[str] = Field(
        default_factory=list, description="Source URIs from grounding"
    )

    # Grounding metadata for traceability
    grounding: Optional[GroundingMetadata] = Field(
        default=None, description="Google Search grounding metadata"
    )


def search_economic_indicators(
    state: str,
    days_back: int = 7,
) -> Optional[EconomicReport]:
    """
    Search for economic and market data affecting food security.
    """
    print(f"Economic Search: {state} ({days_back} days)")

    if state not in AEGIS_FOCUS_STATES:
        print(f"{state} not in focus states")

    date_range, year = get_date_range(days_back)

    search_prompt = f"""Search for current economic and market data in {state} State, Nigeria.

IMPORTANT: Focus on data from {date_range} (year {year}).

Search for:

1. MARKET ACCESS:
   - Are major markets operational, partially open, or closed?
   - Which specific markets are closed or disrupted?
   - What reasons are given? (security, roads, flooding)

2. COMMODITY PRICES in {state}:
   - Maize/corn: price per kg or bag
   - Rice: price per kg or bag
   - Sorghum/guinea corn: price if available
   - Beans/cowpea: price if available
   - Any another commodity price that's frequently purchased and prices if available
   - Note the source and date of price data
   - Are prices increasing, decreasing, or stable?

3. CURRENCY & INFLATION:
   - Current food inflation rate
   - Naira purchasing power observations

4. AGRICULTURAL SITUATION:
   - Are farmers able to work their fields?
   - Reports of abandoned farms due to insecurity
   - Harvest situation

5. FOOD ASSISTANCE:
   - Ongoing food aid operations
   - Active humanitarian organizations

Be thorough and cite your sources."""

    extract_prompt = f"""Extract structured economic data from this report about {state} State, Nigeria.

SOURCE TEXT:
{{grounded_text}}

Extract as JSON with this exact schema:
{{
  "state": "{state}",
  "markets_operational": "fully" | "partially" | "closed" | "unknown",
  "closed_markets": ["market names"],
  "market_access_issues": ["issue1", "issue2"],
  "staple_prices": [
    {{
      "commodity": "name",
      "price_naira": number or null,
      "unit": "kg" or "bag",
      "price_change": "stable" | "increasing" | "decreasing" | "volatile" | "unknown",
      "percent_change": number or null,
      "source": "source name" or null
    }}
  ],
  "price_data_date": "date" or null,
  "inflation_rate": number or null,
  "naira_exchange_rate": "rate" or null,
  "inflation_observations": "observations" or null,
  "farming_status": "description" or null,
  "farms_abandoned": "description" or null,
  "harvest_reports": "description" or null,
  "food_aid_operations": ["operation1", "operation2"]
}}

Only include data from {date_range}.
Return ONLY valid JSON."""

    result, grounding = grounded_search(
        search_prompt=search_prompt,
        extract_prompt=extract_prompt,
        result_class=EconomicReport,
        debug=True,
    )

    if result:
        result.search_date = datetime.now().strftime("%Y-%m-%d")
        result.timeframe = date_range
        result.sources_consulted = [s.uri for s in grounding.sources]
        result.grounding = grounding

        #  results
        print(f"\nMarkets: {result.markets_operational}")

        if result.staple_prices:
            prices_str = ", ".join(
                [
                    f"{p.commodity}: ₦{p.price_naira:,.0f}/{p.unit}"
                    for p in result.staple_prices
                    if p.price_naira
                ]
            )
            if prices_str:
                print(f"Prices: {prices_str}")

        if result.market_access_issues:
            print(f"Issues: {', '.join(result.market_access_issues)}")

        if result.farms_abandoned:
            print(f"Farms: {result.farms_abandoned}...")

        if result.food_aid_operations:
            print(f"Aid: {', '.join(result.food_aid_operations)}")

    return result
