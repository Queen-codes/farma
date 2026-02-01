"""AEGIS - Data Intelligence Module for FARMA.

This module COLLECTS humanitarian and security data.
Analysis and risk assessment are handled by a separate Analysis Agent.
"""

from .tools import (
    # Shared
    AEGIS_FOCUS_STATES,
    # Tools
    search_conflict_events,
    search_displacement,
    search_food_security,
    search_economic_indicators,
    # Models
    ConflictSearchResult,
    ConflictEvent,
    DisplacementReport,
    FoodSecurityReport,
    EconomicReport,
    MarketPrice,
)

from .db import (
    init_db,
    close_db,
    async_session,
    AegisScan,
    StateIntelligence,
    ConflictEvent as DBConflictEvent,
)

from .graph import (
    aegis_graph,
    run_aegis_scan,
    AegisGraphState,
    StateWorkerResult,
)

__all__ = [
    # Focus States
    "AEGIS_FOCUS_STATES",
    # Tools
    "search_conflict_events",
    "search_displacement",
    "search_food_security",
    "search_economic_indicators",
    # Tool Result Models
    "ConflictSearchResult",
    "ConflictEvent",
    "DisplacementReport",
    "FoodSecurityReport",
    "EconomicReport",
    "MarketPrice",
    # Database
    "init_db",
    "close_db",
    "async_session",
    "AegisScan",
    "StateIntelligence",
    "DBConflictEvent",
    # Graph
    "aegis_graph",
    "run_aegis_scan",
    "AegisGraphState",
    "StateWorkerResult",
]
