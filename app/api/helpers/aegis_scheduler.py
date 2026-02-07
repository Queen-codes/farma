"""Background scheduler for unattended daily AEGIS runs.

Key responsibilities:
- Parse schedule configuration from environment variables.
- Coordinate a once-per-day scan loop.
- Use Postgres advisory locks to avoid duplicate runs in multi-instance setups.
- Optionally chain synthesis and report generation after scans.
- Persist job state/events via the shared job store.

Used by:
- `app.api.helpers.startup.lifespan`, which starts this loop on app startup.

Assumptions:
- Postgres advisory lock functions are available.
- AEGIS runner modules (`scan`, `synthesis`, `report`) are importable.
- Job store and reports directory are configured and writable.

"""

from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import text

from app.api.helpers.paths import REPORTS_DIR
from app.api.helpers.runtime import env_bool, utcnow_naive
from app.config import AEGIS_FOCUS_STATES
from app.utils.job_store import job_store

logger = logging.getLogger(__name__)
_SCHEDULER_LOCK_KEY = int(os.getenv("AEGIS_SCHEDULER_LOCK_KEY", "941607"))


def _seconds_until_next_utc_time(hour: int, minute: int) -> float:
    """Compute seconds until the next occurrence of a UTC clock time.

    Args:
        hour: UTC hour in 24-hour format (0-23).
        minute: UTC minute (0-59).

    Returns:
        float: Number of seconds until the next scheduled instant.

    Raises:
        ValueError: If invalid hour/minute values are passed to `datetime.replace`.

    Side Effects:
        None.

    Latency:
        Constant-time datetime arithmetic.
    """
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


def _parse_hour_minute() -> tuple[int, int] | None:
    """Parse scheduler execution time from environment variables.

    Supports either separate hour/minute variables or a combined
    ``AEGIS_REFRESH_HOUR="HH:MM"`` form.

    Args:
        None.

    Returns:
        tuple[int, int] | None: Parsed `(hour, minute)` pair, or `None` when
        values are missing/invalid.

    Raises:
        Does not raise intentionally.

    Side Effects:
        Reads scheduler environment variables from process configuration.

    Latency:
        Constant-time string parsing.
    """
    raw_hour = os.getenv("AEGIS_REFRESH_HOUR", "6").strip()
    raw_minute = os.getenv("AEGIS_REFRESH_MINUTE", "0").strip()

    if ":" in raw_hour:
        parts = raw_hour.split(":", 1)
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except Exception:
            return None
    else:
        try:
            hour = int(raw_hour)
            minute = int(raw_minute)
        except Exception:
            return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


async def _acquire_scheduler_lock() -> Any | None:
    """Acquire a Postgres advisory lock for single-instance scheduler execution.

    Args:
        None.

    Returns:
        Any | None: Open DB connection holding the lock, or `None` if lock
        acquisition fails or another instance already holds it.

    Raises:
        Does not raise intentionally; failures are converted to `None`.

    Side Effects:
        Opens a database connection and may acquire a global advisory lock.

    Latency:
        Includes network I/O to the database.
    """
    try:
        from app.aegis.db.connection import engine

        conn = await engine.connect()
        res = await conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": _SCHEDULER_LOCK_KEY},
        )
        got_lock = bool(res.scalar())
        if not got_lock:
            await conn.close()
            return None
        return conn
    except Exception:
        return None


async def _release_scheduler_lock(conn: Any | None) -> None:
    """Release scheduler advisory lock and close its database connection.

    Args:
        conn: Connection returned by `_acquire_scheduler_lock`, or `None`.

    Returns:
        None.

    Raises:
        Does not raise intentionally; unlock/close failures are swallowed.

    Side Effects:
        Executes DB unlock SQL and closes an open connection.

    Latency:
        Includes network I/O to the database.
    """
    if conn is None:
        return
    try:
        await conn.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": _SCHEDULER_LOCK_KEY},
        )
    except Exception:
        pass
    try:
        await conn.close()
    except Exception:
        pass


async def scheduled_aegis_loop() -> None:
    """Run the perpetual daily AEGIS scheduler loop.

    The loop waits until the configured UTC time, acquires a distributed lock,
    then runs scan and optional synthesis/report pipelines while recording job
    status updates for the UI.

    Args:
        None.

    Returns:
        None: This coroutine is intended to run until cancelled during shutdown.

    Raises:
        asyncio.CancelledError: Propagates when application shutdown cancels
            the scheduler task.

    Side Effects:
        Performs DB reads/writes, advisory locking, and job-store mutations.
        Triggers external AEGIS workflows that can call LLMs, geospatial
        services, and report generation pipelines.

    Latency:
        Long-running daemon loop. Each cycle includes sleep, database I/O, and
        potentially expensive workflow execution.
    """
    import asyncio

    from app.aegis.graph import run_aegis_scan
    from app.aegis.synthesis.runner import run_synthesis_dag
    from app.aegis.report.runner import run_report_dag
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan

    enabled = env_bool("AEGIS_AUTO_REFRESH", default=False)
    if not enabled:
        return

    parsed = _parse_hour_minute()
    if not parsed:
        logger.warning("[AEGIS/SCHED] Disabled: invalid schedule env vars")
        return
    hour, minute = parsed

    days_back = int(os.getenv("AEGIS_SCAN_DAYS_BACK", "7"))
    include_report = env_bool("AEGIS_AUTO_REPORT", default=True)
    include_infographics = env_bool("AEGIS_AUTO_REPORT_INFOGRAPHICS", default=False)
    include_annexes = env_bool("AEGIS_AUTO_REPORT_ANNEXES", default=True)

    while True:
        wait_s = _seconds_until_next_utc_time(hour, minute)
        logger.info(
            f"[AEGIS/SCHED] Next run in {wait_s/3600:.1f}h (UTC {hour:02d}:{minute:02d})"
        )
        await asyncio.sleep(wait_s)

        lock_conn = await _acquire_scheduler_lock()
        if lock_conn is None:
            logger.info(
                "[AEGIS/SCHED] Skipping run: advisory lock unavailable or held by another instance."
            )
            continue

        run_id = f"SCHED-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        logger.info("[AEGIS/SCHED] Starting scheduled scan: %s", run_id)

        scan_db_id = 0
        try:
            async with get_async_session() as session:
                scan = AegisScan(
                    run_id=run_id,
                    started_at=utcnow_naive(),
                    status="running",
                    states_scanned=0,
                    total_events=0,
                    total_fatalities=0,
                )
                session.add(scan)
                await session.flush()
                scan_db_id = scan.id
        except Exception as e:
            logger.warning("[AEGIS/SCHED] Could not create scan record: %s", e)

        await job_store.create_job(
            run_id,
            "aegis_scan",
            metadata={"scan_id": scan_db_id, "days_back": days_back},
        )
        await job_store.add_event(
            run_id,
            "scan_started",
            status="running",
            step="scan_start",
            message="Scheduled scan started",
        )

        try:
            scan_result = await run_aegis_scan(
                days_back=days_back,
                force=False,
                states=AEGIS_FOCUS_STATES,
                run_id=run_id,
                scan_id=scan_db_id or None,
            )
            await job_store.update_job(
                run_id,
                status="completed",
                result=scan_result,
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                "scan_completed",
                status="completed",
                step="scan_complete",
                message="Scheduled scan completed",
            )
        except Exception as e:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e)},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                "scan_failed",
                status="failed",
                step="scan_error",
                message=str(e),
            )
            await _release_scheduler_lock(lock_conn)
            continue

        if include_report and scan_db_id:
            # Stage 1: Synthesis
            synth_id = f"SYNTH-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            logger.info("[AEGIS/SCHED] Starting scheduled synthesis: %s", synth_id)
            await job_store.create_job(
                synth_id,
                "aegis_synthesis",
                metadata={"scan_id": scan_db_id, "states": AEGIS_FOCUS_STATES},
            )
            await job_store.add_event(
                synth_id,
                "synthesis_started",
                status="running",
                step="synthesis_start",
                message="Scheduled synthesis started",
            )
            try:
                synth_result = await run_synthesis_dag(
                    scan_id=int(scan_db_id),
                    states=AEGIS_FOCUS_STATES,
                    run_id=synth_id,
                    emit_job_events=True,
                )
                await job_store.update_job(
                    synth_id,
                    status="completed",
                    result=synth_result,
                    completed_at=utcnow_naive(),
                )
                await job_store.add_event(
                    synth_id,
                    "synthesis_completed",
                    status="completed",
                    step="synthesis_complete",
                    message="Scheduled synthesis completed",
                    payload={"scan_id": scan_db_id},
                )
            except Exception as e:
                await job_store.update_job(
                    synth_id,
                    status="failed",
                    result={"error": str(e), "scan_id": scan_db_id},
                    completed_at=utcnow_naive(),
                )
                await job_store.add_event(
                    synth_id,
                    "synthesis_failed",
                    status="failed",
                    step="synthesis_error",
                    message=str(e),
                    payload={"scan_id": scan_db_id},
                )
                await _release_scheduler_lock(lock_conn)
                continue

            # Stage 2: Report only PDF
            report_id = f"SCHED-RPT-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            logger.info("[AEGIS/SCHED] Starting scheduled report: %s", report_id)
            await job_store.create_job(
                report_id, "aegis_report", metadata={"scan_id": scan_db_id}
            )
            await job_store.add_event(
                report_id,
                "report_started",
                status="running",
                step="report_start",
                message="Scheduled report started",
            )
            try:
                report_result = await run_report_dag(
                    report_id=report_id,
                    scan_id=int(scan_db_id),
                    states=[],
                    include_infographics=include_infographics,
                    include_annexes=include_annexes,
                    output_dir=str(REPORTS_DIR),
                    emit_job_events=True,
                )
                await job_store.update_job(
                    report_id,
                    status="completed",
                    result={"pdf_path": report_result.get("pdf_path")},
                    completed_at=utcnow_naive(),
                )
                await job_store.add_event(
                    report_id,
                    "report_completed",
                    status="completed",
                    step="report_complete",
                    message="Scheduled report ready",
                    payload={"pdf_path": report_result.get("pdf_path")},
                )
            except Exception as e:
                await job_store.update_job(
                    report_id,
                    status="failed",
                    result={"error": str(e)},
                    completed_at=utcnow_naive(),
                )
                await job_store.add_event(
                    report_id,
                    "report_failed",
                    status="failed",
                    step="report_error",
                    message=str(e),
                )
        await _release_scheduler_lock(lock_conn)
