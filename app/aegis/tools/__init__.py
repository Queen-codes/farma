"""AEGIS Intelligence Tools - Grounding-based search tools."""

from .shared import AEGIS_FOCUS_STATES, llm_grounding

# Shared grounding utilities (for reuse across tools)
from .shared_grounding import (
    GroundingSource,
    GroundingMetadata,
    extract_grounding_metadata,
    grounded_search,
    get_date_range,
)

from .conflict_tool import (
    search_conflict_events,
    ConflictEvent,
    ConflictSearchResult,
)

from .displacement_tool import (
    search_displacement,
    DisplacementReport,
)

from .food_security_tool import (
    search_food_security,
    FoodSecurityReport,
)

from .economic_tool import (
    search_economic_indicators,
    EconomicReport,
    MarketPrice,
)

__all__ = [
    # Shared
    "AEGIS_FOCUS_STATES",
    "llm_grounding",
    # Grounding utilities
    "GroundingSource",
    "GroundingMetadata",
    "extract_grounding_metadata",
    "grounded_search",
    "get_date_range",
    # Conflict
    "search_conflict_events",
    "ConflictEvent",
    "ConflictSearchResult",
    # Displacement
    "search_displacement",
    "DisplacementReport",
    # Food Security
    "search_food_security",
    "FoodSecurityReport",
    # Economic
    "search_economic_indicators",
    "EconomicReport",
    "MarketPrice",
]
