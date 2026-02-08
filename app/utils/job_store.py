"""Job/event persistence layer with DB-first and memory fallback modes.

This module supports asynchronous job lifecycle tracking used by API/workflow
execution. It provides:
- Job creation and status updates.
- Event timeline appends for frontend progress streaming.
- Optional strict-DB mode to prevent per-instance divergence in Cloud Run.

Primary data flow:
1. Attempt read/write through SQLAlchemy async session.
2. Maintain in-memory mirrors for fast local access/fallback.
3. In strict mode, raise when database connectivity is unavailable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import String, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import select

from app.aegis.db.connection import Base, async_session


def _utcnow_naive() -> datetime:
    """Return current UTC timestamp as naive datetime for DB defaults.

    Returns:
        Naive UTC `datetime` object.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time system clock call.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobRun(Base):
    """ORM model storing one workflow/job execution record."""

    __tablename__ = "job_runs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    job_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class JobEvent(Base):
    """ORM model storing timeline events associated with a `JobRun`."""

    __tablename__ = "job_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    step: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    progress: Mapped[Optional[float]] = mapped_column(nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class FarmerInteraction(Base):
    """ORM model recording farmer request/response interaction snapshots."""

    __tablename__ = "farmer_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow_naive, index=True
    )

    input_type: Mapped[str] = mapped_column(String(20), default="sms")
    phone: Mapped[str] = mapped_column(String(64), index=True)

    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )

    status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    final_decision: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    climate_score: Mapped[Optional[float]] = mapped_column(nullable=True)

    risk_flags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    farmer_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Ssore extra structured data for debugging/admin drilldown.
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


_memory_jobs: Dict[str, Dict[str, Any]] = {}
_memory_events: Dict[str, List[Dict[str, Any]]] = {}


def _collapse_repeated_failures(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse consecutive duplicate failure events for cleaner UI timelines."""
    if not events:
        return []

    collapsed: List[Dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        if not collapsed:
            event["_repeat_count"] = 1
            collapsed.append(event)
            continue

        prev = collapsed[-1]
        same_failure = (
            str(prev.get("status") or "") == "failed"
            and str(event.get("status") or "") == "failed"
            and str(prev.get("event_type") or "") == str(event.get("event_type") or "")
            and str(prev.get("step") or "") == str(event.get("step") or "")
            and str(prev.get("message") or "") == str(event.get("message") or "")
        )
        if same_failure:
            prev["_repeat_count"] = int(prev.get("_repeat_count") or 1) + 1
            continue

        event["_repeat_count"] = 1
        collapsed.append(event)

    out: List[Dict[str, Any]] = []
    for event in collapsed:
        repeat_count = int(event.pop("_repeat_count", 1) or 1)
        payload = dict(event.get("payload") or {})
        if repeat_count > 1:
            payload["repeat_count"] = repeat_count
            base_msg = event.get("message") or event.get("event_type") or "failed_event"
            event["message"] = f"{base_msg} (repeated x{repeat_count})"
        event["payload"] = payload
        out.append(event)

    return out


class JobStore:
    """Facade for job and event operations with health-aware DB fallback.

    The store keeps small in-memory mirrors (`_memory_jobs`, `_memory_events`)
    and uses periodic DB health checks to decide whether to use persistent
    storage or fallback behavior.
    """

    _last_db_failure: Optional[datetime] = None
    _last_db_success: Optional[datetime] = None
    _DB_RETRY_SECONDS: int = 30  # retry after 30s
    _DB_SUCCESS_CACHE_SECONDS: int = 5

    def __init__(self) -> None:
        """Configure store mode and strict DB behavior from environment.

        Returns:
            None.

        Raises:
            None: This initializer does not intentionally raise.

        Side Effects:
            Reads environment variables and initializes availability flags.

        Latency:
            Constant-time environment parsing.
        """
        self._db_available = True
        # In Cloud Run, default to strict DB mode to avoid per-instance memory divergence.
        strict_default = bool(os.getenv("K_SERVICE"))
        raw = os.getenv("JOB_STORE_REQUIRE_DB")
        if raw is None:
            self._require_db = strict_default
        else:
            self._require_db = raw.strip().lower() in {"1", "true", "yes", "on"}

    async def _check_db(self) -> bool:
        """Probe DB availability with short success/failure caching.

        Returns:
            `True` when DB is considered available for current operation;
            `False` when recent failures indicate fallback mode.

        Raises:
            None: Exceptions are captured and converted to availability flags.

        Side Effects:
            Performs lightweight DB query (`select(JobRun).limit(1)`).
            Updates internal health timestamps and availability state.

        Latency:
            Small DB round-trip when cache windows expire.
        """
        now = datetime.now(timezone.utc)
        if self._db_available and self._last_db_success:
            elapsed_ok = (now - self._last_db_success).total_seconds()
            if elapsed_ok < self._DB_SUCCESS_CACHE_SECONDS:
                return True

        # if recently failed, wait before retrying.
        if not self._db_available and self._last_db_failure:
            elapsed = (now - self._last_db_failure).total_seconds()
            if elapsed < self._DB_RETRY_SECONDS:
                return False
            #  try again.
            self._db_available = True
        if not self._db_available:
            return False
        try:
            async with async_session() as session:
                await session.execute(select(JobRun).limit(1))
            self._last_db_success = now
            return True
        except Exception:
            self._db_available = False
            self._last_db_failure = now
            self._last_db_success = None
            return False

    async def create_job(
        self, job_id: str, job_type: str, metadata: Optional[dict] = None
    ) -> Dict[str, Any]:
        """Create a new job record and initialize its event buffer.

        Args:
            job_id: Stable externally visible job identifier.
            job_type: Logical job type label (for example `farma_workflow`).
            metadata: Optional arbitrary metadata associated with the job.

        Returns:
            Dictionary representation of the created job.

        Raises:
            RuntimeError: When strict DB mode is enabled and DB is unavailable.

        Side Effects:
            Writes to database when available.
            Writes to in-memory job/event stores in all modes.

        Latency:
            Dominated by optional DB insert/commit round-trip.
        """
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
            "result": None,
            "metadata": metadata or {},
        }

        db_ok = await self._check_db()
        if self._require_db and not db_ok:
            raise RuntimeError("Job store database unavailable (strict mode enabled)")

        if db_ok:
            try:
                async with async_session() as session:
                    session.add(
                        JobRun(
                            job_id=job_id,
                            job_type=job_type,
                            status="running",
                            job_metadata=metadata or {},
                        )
                    )
                    await session.commit()
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.now(timezone.utc)

        _memory_jobs[job_id] = job
        _memory_events.setdefault(job_id, [])
        return job

    async def update_job(
        self,
        job_id: str,
        status: str,
        result: Optional[dict] = None,
        completed_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update status/result fields for an existing job.

        Args:
            job_id: Identifier of job to update.
            status: New lifecycle status string.
            result: Optional final/intermediate result payload.
            completed_at: Optional completion timestamp.

        Returns:
            Updated in-memory job dict when present, otherwise `None`.

        Raises:
            RuntimeError: When strict DB mode requires persistence but DB fails.

        Side Effects:
            Mutates in-memory job snapshot.
            Persists updates to DB when available.

        Latency:
            Dominated by optional DB read+commit operation.
        """
        job = _memory_jobs.get(job_id)
        if job:
            job["status"] = status
            if result is not None:
                job["result"] = result
            if completed_at:
                job["completed_at"] = completed_at

        if await self._check_db():
            try:
                async with async_session() as session:
                    db_job = await session.get(JobRun, job_id)
                    if db_job:
                        db_job.status = status
                        if result is not None:
                            db_job.result = result
                        if completed_at:
                            db_job.completed_at = completed_at
                        await session.commit()
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.now(timezone.utc)
                if self._require_db:
                    raise RuntimeError("Job store database unavailable (strict mode enabled)")
        elif self._require_db:
            raise RuntimeError("Job store database unavailable (strict mode enabled)")

        return job

    async def add_event(
        self,
        job_id: str,
        event_type: str,
        status: str = "running",
        step: Optional[str] = None,
        message: Optional[str] = None,
        progress: Optional[float] = None,
        payload: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Append timeline event for a job and optionally persist it.

        Args:
            job_id: Target job identifier.
            event_type: Event name/category.
            status: Event status (`running`, `completed`, `failed`, etc.).
            step: Optional pipeline step label.
            message: Optional human-readable event message.
            progress: Optional numeric progress marker.
            payload: Optional structured event details.

        Returns:
            Dictionary representation of the created event.

        Raises:
            RuntimeError: When strict DB mode is enabled and DB is unavailable.

        Side Effects:
            Appends in-memory event list.
            Broadcasts lightweight message to thinking websocket bus.
            Persists event row to DB when available.

        Latency:
            Dominated by DB insert/commit and websocket broadcast fanout.
        """
        event = {
            "event_id": uuid.uuid4().hex,
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc),
            "event_type": event_type,
            "status": status,
            "step": step,
            "message": message,
            "progress": progress,
            "payload": payload or {},
        }

        _memory_events.setdefault(job_id, []).append(event)

        try:
            from app.utils.thinking_bus import thinking_bus

            msg = f"[{job_id}] {event_type}"
            if step:
                msg += f" ({step})"
            if message:
                msg += f": {message}"
            await thinking_bus.broadcast(msg)
        except Exception:
            pass

        db_ok = await self._check_db()
        if self._require_db and not db_ok:
            raise RuntimeError("Job store database unavailable (strict mode enabled)")

        if db_ok:
            try:
                async with async_session() as session:
                    session.add(
                        JobEvent(
                            event_id=event["event_id"],
                            job_id=job_id,
                            event_type=event_type,
                            status=status,
                            step=step,
                            message=message,
                            progress=progress,
                            payload=payload or {},
                        )
                    )
                    await session.commit()
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.now(timezone.utc)
                if self._require_db:
                    raise RuntimeError("Job store database unavailable (strict mode enabled)")

        return event

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve job record from DB (preferred) or in-memory fallback.

        Args:
            job_id: Identifier of job to fetch.

        Returns:
            Job dictionary when found, otherwise `None`.

        Raises:
            RuntimeError: When strict DB mode is enabled and DB is unavailable.

        Side Effects:
            May update internal DB health tracking flags.

        Latency:
            Small DB lookup when available; constant-time dict lookup fallback.
        """
        db_ok = await self._check_db()
        if self._require_db and not db_ok:
            raise RuntimeError("Job store database unavailable (strict mode enabled)")

        if db_ok:
            try:
                async with async_session() as session:
                    db_job = await session.get(JobRun, job_id)
                    if db_job:
                        return {
                            "job_id": db_job.job_id,
                            "job_type": db_job.job_type,
                            "status": db_job.status,
                            "started_at": db_job.started_at,
                            "completed_at": db_job.completed_at,
                            "result": db_job.result,
                            "metadata": db_job.job_metadata,
                        }
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.now(timezone.utc)
                if self._require_db:
                    raise RuntimeError("Job store database unavailable (strict mode enabled)")

        return _memory_jobs.get(job_id)

    async def list_events(self, job_id: str) -> List[Dict[str, Any]]:
        """List all events for a job in chronological order.

        Args:
            job_id: Identifier of job whose events should be returned.

        Returns:
            Ordered list of event dictionaries.

        Raises:
            RuntimeError: When strict DB mode is enabled and DB is unavailable.

        Side Effects:
            May query DB and update internal health state.

        Latency:
            Depends on event count; DB path includes query round-trip.
        """
        db_ok = await self._check_db()
        if self._require_db and not db_ok:
            raise RuntimeError("Job store database unavailable (strict mode enabled)")

        if db_ok:
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(JobEvent)
                        .where(JobEvent.job_id == job_id)
                        .order_by(JobEvent.created_at)
                    )
                    events = result.scalars().all()
                    normalized = [
                        {
                            "event_id": e.event_id,
                            "job_id": e.job_id,
                            "created_at": e.created_at,
                            "event_type": e.event_type,
                            "status": e.status,
                            "step": e.step,
                            "message": e.message,
                            "progress": e.progress,
                            "payload": e.payload,
                        }
                        for e in events
                    ]
                    return _collapse_repeated_failures(normalized)
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.now(timezone.utc)
                if self._require_db:
                    raise RuntimeError("Job store database unavailable (strict mode enabled)")

        return _collapse_repeated_failures(_memory_events.get(job_id, []))


job_store = JobStore()
