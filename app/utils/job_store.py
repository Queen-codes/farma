"""Job/event store for frontend job contract.

Provides a stable job_id, event stream, and persisted results.
Falls back to in-memory if DB is unavailable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import String, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import select

from app.aegis.db.connection import Base, async_session


class JobRun(Base):
    __tablename__ = "job_runs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    job_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    step: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    progress: Mapped[Optional[float]] = mapped_column(nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class FarmerInteraction(Base):
    __tablename__ = "farmer_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
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


class JobStore:
    _last_db_failure: Optional[datetime] = None
    _DB_RETRY_SECONDS: int = 30  # retry after 30s

    def __init__(self):
        self._db_available = True

    async def _check_db(self) -> bool:
        # if recently failed, wait before retrying.
        if not self._db_available and self._last_db_failure:
            elapsed = (datetime.utcnow() - self._last_db_failure).total_seconds()
            if elapsed < self._DB_RETRY_SECONDS:
                return False
            #  try again.
            self._db_available = True
        if not self._db_available:
            return False
        try:
            async with async_session() as session:
                await session.execute(select(JobRun).limit(1))
            return True
        except Exception:
            self._db_available = False
            self._last_db_failure = datetime.utcnow()
            return False

    async def create_job(
        self, job_id: str, job_type: str, metadata: Optional[dict] = None
    ) -> Dict[str, Any]:
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "running",
            "started_at": datetime.utcnow(),
            "completed_at": None,
            "result": None,
            "metadata": metadata or {},
        }

        if await self._check_db():
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
                self._last_db_failure = datetime.utcnow()

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
                self._last_db_failure = datetime.utcnow()

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
        event = {
            "event_id": uuid.uuid4().hex,
            "job_id": job_id,
            "created_at": datetime.utcnow(),
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

        if await self._check_db():
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
                self._last_db_failure = datetime.utcnow()

        return event

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if await self._check_db():
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
                self._last_db_failure = datetime.utcnow()

        return _memory_jobs.get(job_id)

    async def list_events(self, job_id: str) -> List[Dict[str, Any]]:
        if await self._check_db():
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(JobEvent)
                        .where(JobEvent.job_id == job_id)
                        .order_by(JobEvent.created_at)
                    )
                    events = result.scalars().all()
                    return [
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
            except Exception:
                self._db_available = False
                self._last_db_failure = datetime.utcnow()

        return _memory_events.get(job_id, [])


job_store = JobStore()
