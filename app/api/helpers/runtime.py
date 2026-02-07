"""Runtime utilities shared by API route handlers and scheduler startup.

Key responsibilities:
- Parse boolean feature flags from environment variables.
- Provide a database-friendly UTC timestamp helper.
- Spawn and retain background tasks on FastAPI app state.

Used by:
- `app.api.routes.aegis` and `app.api.routes.farmer` for background job execution.
- `app.api.helpers.aegis_scheduler` for feature flags and UTC timestamps.

Assumptions:
- Functions run inside an active asyncio event loop for task spawning.
- DB timestamp columns expect naive UTC datetimes.

"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Awaitable

from fastapi import FastAPI


def env_bool(name: str, default: bool = False) -> bool:
    """Read an environment variable as a boolean flag.

    Args:
        name: Environment variable key to read.
        default: Value returned when the variable is unset.

    Returns:
        bool: Parsed boolean flag.

    Raises:
        Does not raise intentionally.

    Side Effects:
        Reads process environment variables.

    Latency:
        Constant-time local lookup.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    Args:
        None.

    Returns:
        datetime: Current UTC timestamp with timezone info stripped.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Constant-time clock read.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def spawn_bg_task(app: FastAPI, coro: Awaitable[object]) -> None:
    """Schedule a coroutine in the background and keep a strong task reference.

    This helper stores tasks in ``app.state.bg_tasks`` to prevent premature
    garbage collection while jobs are running.

    Args:
        app: FastAPI application instance that holds background task state.
        coro: Coroutine object to schedule via ``asyncio.create_task``.

    Returns:
        None: The task is scheduled asynchronously and tracked on app state.

    Raises:
        RuntimeError: If called without a running asyncio event loop.

    Side Effects:
        Creates an asyncio task.
        Mutates ``app.state.bg_tasks`` by adding/removing the task.

    Latency:
        Fast scheduling operation; actual runtime depends on the coroutine.
    """
    task = asyncio.create_task(coro)
    try:
        app.state.bg_tasks.add(task)  # type: ignore[attr-defined]
    except Exception:
        pass

    def _done(_t: asyncio.Task[object]) -> None:
        """Remove a completed task from app-level background tracking.

        Args:
            _t: Completed asyncio task reference.

        Returns:
            None.

        Raises:
            Does not raise intentionally.

        Side Effects:
            Mutates `app.state.bg_tasks` by discarding the completed task.

        Latency:
            Constant-time set mutation.
        """
        try:
            app.state.bg_tasks.discard(_t)  # type: ignore[attr-defined]
        except Exception:
            pass

    task.add_done_callback(_done)
