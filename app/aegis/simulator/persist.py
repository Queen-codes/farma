"""Persistence helpers for simulator artifacts.

Purpose:
- Upsert simulation rows with projections, policy brief, and runtime metadata.
- Fetch simulation rows for API status endpoints.

Used by:
- `app.aegis.simulator.nodes` and API routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisSimulation


def _utcnow_naive() -> datetime:
    """Return current UTC time as naive datetime for DB writes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_simulation_row(
    *,
    simulation_id: str,
    scan_id: int,
    scenario_json: Dict[str, Any],
    projections_json: Dict[str, Any],
    policy_brief_json: Optional[Dict[str, Any]],
    uri_whitelist: Optional[list[str]],
    model: str,
    thinking_level: str,
    schema_mode: str,
    status: str,
    error: Optional[str] = None,
) -> int:
    """Create or update one simulation persistence row.

    Args:
        simulation_id: External simulation identifier.
        scan_id: Source scan ID.
        scenario_json: Input scenario payload.
        projections_json: Deterministic projections.
        policy_brief_json: Optional LLM narrative output.
        uri_whitelist: Allowed source URIs used by LLM.
        model: LLM model name.
        thinking_level: Effective thinking level.
        schema_mode: Structured-output mode used.
        status: Terminal/in-progress status string.
        error: Optional error message.

    Returns:
        int: Database row primary key.

    Raises:
        SQLAlchemyError: Can propagate on DB write failures.

    Side Effects:
        Inserts or updates `AegisSimulation` row.
    """
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisSimulation).where(AegisSimulation.simulation_id == simulation_id)
        )
        row = res.scalar_one_or_none()
        if not row:
            row = AegisSimulation(
                simulation_id=simulation_id,
                scan_id=scan_id,
                created_at=_utcnow_naive(),
            )
            session.add(row)
            await session.flush()

        row.scan_id = scan_id
        row.scenario_json = scenario_json
        row.projections_json = projections_json
        row.policy_brief_json = policy_brief_json
        row.uri_whitelist = {"uris": uri_whitelist or []}
        row.model = model
        row.thinking_level = thinking_level
        row.schema_mode = schema_mode
        row.status = status
        row.error = error

        return int(row.id)


async def get_simulation(simulation_id: str) -> Optional[dict]:
    """Fetch simulation row and convert it into API-friendly dict payload."""
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisSimulation).where(AegisSimulation.simulation_id == simulation_id)
        )
        row = res.scalar_one_or_none()
        if not row:
            return None
        return {
            "id": row.id,
            "simulation_id": row.simulation_id,
            "scan_id": row.scan_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "scenario_json": row.scenario_json,
            "projections_json": row.projections_json,
            "policy_brief_json": row.policy_brief_json,
            "uri_whitelist": (row.uri_whitelist or {}).get("uris") if isinstance(row.uri_whitelist, dict) else row.uri_whitelist,
            "model": row.model,
            "thinking_level": row.thinking_level,
            "schema_mode": row.schema_mode,
            "status": row.status,
            "error": row.error,
        }
