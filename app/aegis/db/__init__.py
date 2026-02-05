"""AEGIS Database - PostgreSQL persistence layer for raw intelligence data."""

from .connection import (
    engine,
    async_session,
    Base,
    get_session,
    init_db,
    reset_db,
    close_db,
    DATABASE_URL,
)

from .models import (
    AegisScan,
    StateIntelligence,
    ConflictEvent,
    LGARiskScore,
    AegisReport,
)

__all__ = [
    # Connection
    "engine",
    "async_session",
    "Base",
    "get_session",
    "init_db",
    "reset_db",
    "close_db",
    "DATABASE_URL",
    # Models
    "AegisScan",
    "StateIntelligence",
    "ConflictEvent",
    "LGARiskScore",
    "AegisReport",
]
