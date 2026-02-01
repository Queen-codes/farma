"""Database connection for AEGIS."""

import os
from typing import AsyncGenerator, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

# Database URL - async PostgreSQL
# Format: postgresql+asyncpg://user:password@host:port/database
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://farma_app:password@localhost:5432/farma"
)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set True for SQL logging during development
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
    """Get a database session."""
    async with async_session() as session:
        yield session


from contextlib import asynccontextmanager


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, Any]:
    """Get a database session as async context manager."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables initialized")


async def reset_db():
    """Drop all tables and recreate them. Use with caution!"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Tables dropped and recreated")


async def close_db():
    """Close database connections."""
    await engine.dispose()
