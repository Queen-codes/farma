from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisReport


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_report_row(
    *,
    report_id: str,
    scan_id: int,
    states: list[str],
    include_infographics: bool,
    include_annexes: bool,
) -> None:
    async with get_async_session() as session:
        result = await session.execute(select(AegisReport).where(AegisReport.report_id == report_id))
        existing = result.scalar_one_or_none()
        if existing:
            return
        row = AegisReport(
            report_id=report_id,
            scan_id=int(scan_id),
            created_at=utcnow_naive(),
            started_at=utcnow_naive(),
            states={"states": states},
            include_infographics=bool(include_infographics),
            include_annexes=bool(include_annexes),
            status="running",
        )
        session.add(row)
        await session.commit()


async def mark_report_completed(
    *,
    report_id: str,
    pdf_path: str,
    gcs_key: Optional[str] = None,
) -> None:
    async with get_async_session() as session:
        result = await session.execute(select(AegisReport).where(AegisReport.report_id == report_id))
        row = result.scalar_one_or_none()
        if not row:
            return
        row.status = "completed"
        row.pdf_path = pdf_path
        row.gcs_key = gcs_key
        row.error = None
        row.completed_at = utcnow_naive()
        await session.commit()


async def mark_report_failed(*, report_id: str, error: str) -> None:
    async with get_async_session() as session:
        result = await session.execute(select(AegisReport).where(AegisReport.report_id == report_id))
        row = result.scalar_one_or_none()
        if not row:
            return
        row.status = "failed"
        row.error = error
        await session.commit()
