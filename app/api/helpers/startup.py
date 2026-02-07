"""Application lifespan utilities for startup, migration, and shutdown tasks.

Key responsibilities:
- Load and execute lightweight startup SQL migration statements.
- Initialize database connections/tables when auto-init is enabled.
- Start and stop the background AEGIS scheduler loop.
- Ensure graceful shutdown of scheduler and DB resources.

Used by:
- `app.main`, which passes `lifespan` to `FastAPI(...)`.

Assumptions:
- `startup_migrations.sql` contains safe idempotent statements.
- Environment variable `AUTO_INIT_DB` controls DB initialization behavior.
- The app runs with access to AEGIS DB connection utilities.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.helpers.aegis_scheduler import scheduled_aegis_loop
from app.api.helpers.paths import REPORTS_DIR

logger = logging.getLogger(__name__)
_MIGRATIONS_PATH = Path(__file__).with_name("startup_migrations.sql")


def _load_startup_migration_statements() -> list[str]:
    """Load SQL migration statements from the startup migration script.

    Blank lines and comment lines (`-- ...`) are removed before splitting into
    semicolon-delimited statements.

    Args:
        None.

    Returns:
        list[str]: Ordered SQL statements to execute on startup.

    Raises:
        Does not raise intentionally; file-read errors return an empty list.

    Side Effects:
        Reads `startup_migrations.sql` from disk and logs warnings on failure.

    Latency:
        Local file I/O; typically small and fast.
    """
    try:
        script = _MIGRATIONS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[FARMA] Startup migration SQL unavailable: %s", exc)
        return []

    lines: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(line)

    joined = "\n".join(lines)
    return [stmt.strip() for stmt in joined.split(";") if stmt.strip()]


async def _apply_startup_migrations() -> None:
    """Execute startup SQL statements against the application database.

    Args:
        None.

    Returns:
        None.

    Raises:
        SQLAlchemyError: Can propagate when a statement fails to execute.

    Side Effects:
        Performs schema/data mutations in the configured database and commits
        the transaction.

    Latency:
        Depends on number and complexity of SQL statements.
    """
    from sqlalchemy import text

    from app.aegis.db.connection import get_async_session

    statements = _load_startup_migration_statements()
    if not statements:
        return

    async with get_async_session() as session:
        for statement in statements:
            await session.execute(text(statement))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run app startup/shutdown orchestration for FastAPI lifespan events.

    Startup flow:
    1. Initialize database tables (optional, controlled by `AUTO_INIT_DB`).
    2. Apply startup SQL migrations.
    3. Create app-level background task registry.
    4. Start the AEGIS scheduler background loop.

    Shutdown flow:
    1. Cancel and await scheduler task.
    2. Close DB resources.

    Args:
        app: FastAPI application instance whose state is mutated.

    Returns:
        AsyncIterator[None]: Context manager yield point for app runtime.

    Raises:
        Does not raise intentionally for startup/shutdown helper failures; such
        failures are logged and startup continues.

    Side Effects:
        Initializes DB resources, mutates `app.state`, and creates/cancels
        background asyncio tasks.

    Latency:
        Startup latency depends on DB initialization and migration execution.
    """
    logger.info("[FARMA] Starting up...")
    logger.info("[FARMA] Reports directory: %s", REPORTS_DIR)

    try:
        from app.aegis.db.connection import init_db

        auto_init = os.getenv("AUTO_INIT_DB", "true").lower() in ("1", "true", "yes")
        if auto_init:
            await init_db()
            try:
                await _apply_startup_migrations()
                logger.info("[FARMA] DB schema migrations applied.")
            except Exception as exc:
                logger.warning("[FARMA] DB alter skipped: %s", exc)
            logger.info("[FARMA] DB tables ensured.")
    except Exception as exc:
        logger.warning("[FARMA] DB init skipped: %s", exc)

    if not hasattr(app.state, "bg_tasks"):
        app.state.bg_tasks = set()
    app.state.aegis_sched_task = asyncio.create_task(scheduled_aegis_loop())

    yield

    logger.info("[FARMA] Shutting down...")
    task = getattr(app.state, "aegis_sched_task", None)
    if task:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    try:
        from app.aegis.db.connection import close_db

        await close_db()
    except Exception:
        pass
