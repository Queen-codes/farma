"""Async SQLAlchemy connection primitives for AEGIS persistence.

Purpose:
- Configure async PostgreSQL engine and session factory.
- Provide shared session helpers for API/workflow modules.
- Expose lifecycle helpers for startup/shutdown table management.

Used by:
- `app.aegis.db.models` for declarative base metadata.
- AEGIS scan/synthesis/report/simulator/marathon persistence modules.
- API startup and health checks through `get_async_session`.

Assumptions:
- `DATABASE_URL` points to an `asyncpg`-compatible Postgres DSN.
- Callers use `get_async_session()` context manager for auto commit/rollback.
"""

import os
import logging
from typing import AsyncGenerator, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Database URL - async PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set via environment variable.")

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # set to true for SQL logging during development
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    """Yield a raw async SQLAlchemy session without auto-commit behavior.

    Args:
        None.

    Yields:
        AsyncSession: Open SQLAlchemy async session.

    Raises:
        Does not raise intentionally.

    Side Effects:
        Opens and closes a database session.

    Latency:
        Depends on pool checkout and DB network latency.
    """
    async with async_session() as session:
        yield session


from contextlib import asynccontextmanager


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, Any]:
    """Yield an async session with automatic commit/rollback semantics.

    Args:
        None.

    Yields:
        AsyncSession: Managed session for transactional DB operations.

    Raises:
        Exception: Re-raises caller exceptions after rollback.

    Side Effects:
        Opens a DB session, commits on success, rolls back on failure.

    Latency:
        Includes DB transaction overhead.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all declarative tables if missing.

    Args:
        None.

    Returns:
        None.

    Raises:
        SQLAlchemyError: Can propagate if schema creation fails.

    Side Effects:
        Executes DDL against the configured database.

    Latency:
        Depends on schema size and DB responsiveness.
    """
    # Ensure models are imported so Base.metadata includes all tables.
    try:
        import app.aegis.db.models  # noqa: F401
    except Exception:
        pass
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tables initialized")


async def reset_db() -> None:
    """Drop and recreate all tables for the current metadata.

    Deprecated/unused:
        This helper is intended for local reset workflows and is dangerous in
        shared environments.

    Args:
        None.

    Returns:
        None.

    Raises:
        SQLAlchemyError: Can propagate on drop/create failures.

    Side Effects:
        Destructively drops all mapped tables and recreates them.

    Latency:
        Depends on schema size and DB responsiveness.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tables dropped and recreated")


async def close_db() -> None:
    """Dispose the async engine and release pooled connections.

    Args:
        None.

    Returns:
        None.

    Raises:
        SQLAlchemyError: Can propagate if disposal fails.

    Side Effects:
        Closes active DB pool resources.

    Latency:
        Usually fast; can wait on connection cleanup.
    """
    await engine.dispose()
