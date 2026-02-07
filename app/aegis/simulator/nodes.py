"""Node implementations for the simulation graph pipeline.

Purpose:
- Load baseline synthesis artifacts.
- Compute deterministic projections from scenario parameters.
- Generate policy brief, persist simulation row, and emit completion events.

Used by:
- `app.aegis.simulator.graph`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer
from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisScan, StateIntelligence
from app.aegis.simulator.llm import generate_policy_brief
from app.aegis.simulator.persist import create_simulation_row
from app.aegis.simulator.projections import build_uri_whitelist, compute_projections


def _emit(event: str, *, status: str = "running", step: str = "simulator", payload: Optional[dict] = None) -> None:
    """Emit simulator custom event into LangGraph stream writer."""
    writer = get_stream_writer()
    writer({"event": event, "status": status, "step": step, "payload": payload or {}})


async def load_baseline_inputs(state: Dict[str, Any]) -> Dict[str, Any]:
    """Load rollup and assessments required for simulation baseline context."""
    scan_id = int(state["scan_id"])
    _emit("sim.started", payload={"scan_id": scan_id})

    async with get_async_session() as session:
        scan = await session.get(AegisScan, scan_id)
        if not scan or not scan.rollup_json:
            raise RuntimeError("Baseline scan missing rollup_json. Run synthesis first.")
        rollup = dict(scan.rollup_json)

        assessments_by_state: Dict[str, dict] = {}
        res = await session.execute(select(StateIntelligence).where(StateIntelligence.scan_id == scan_id))
        for row in res.scalars().all():
            if row.assessment_json:
                assessments_by_state[row.state_name] = dict(row.assessment_json)

    uri_whitelist = build_uri_whitelist(rollup_json=rollup, assessments_by_state=assessments_by_state)
    _emit("sim.inputs_loaded", status="completed", payload={"scan_id": scan_id, "states": list(assessments_by_state.keys())})
    return {"baseline_rollup_json": rollup, "baseline_assessments_by_state": assessments_by_state, "uri_whitelist": uri_whitelist}


async def compute_projections_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compute deterministic humanitarian and financial projections."""
    scan_id = int(state["scan_id"])
    projections = compute_projections(
        scan_id=scan_id,
        scenario=state.get("scenario") or {},
        rollup_json=state["baseline_rollup_json"],
        assessments_by_state=state["baseline_assessments_by_state"],
    )
    _emit("sim.projections_computed", status="completed", payload={"scan_id": scan_id})
    return {"projections": projections}


async def generate_policy_brief_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate policy brief narrative from deterministic projection outputs."""
    scan_id = int(state["scan_id"])
    sim_id = state["simulation_id"]
    _emit("sim.llm_started", payload={"scan_id": scan_id, "simulation_id": sim_id})
    brief, schema_mode = await generate_policy_brief(
        scan_id=scan_id,
        simulation_id=sim_id,
        scenario=state.get("scenario") or {},
        projections=state.get("projections") or {},
        uri_whitelist=state.get("uri_whitelist") or [],
        config=state.get("config") or {},
    )
    _emit("sim.llm_completed", status="completed", payload={"scan_id": scan_id, "schema_mode": schema_mode})
    return {"policy_brief": brief, "schema_mode": schema_mode}


async def persist_simulation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Persist simulation artifacts and metadata to `AegisSimulation` table."""
    scan_id = int(state["scan_id"])
    sim_id = state["simulation_id"]
    cfg = state.get("config") or {}
    model = str(cfg.get("model") or "")
    thinking_level = str(cfg.get("thinking_level") or "")
    schema_mode = str(state.get("schema_mode") or "json_only")

    row_id = await create_simulation_row(
        simulation_id=sim_id,
        scan_id=scan_id,
        scenario_json=state.get("scenario") or {},
        projections_json=state.get("projections") or {},
        policy_brief_json=state.get("policy_brief"),
        uri_whitelist=state.get("uri_whitelist") or [],
        model=model,
        thinking_level=thinking_level,
        schema_mode=schema_mode,
        status="completed",
        error=None,
    )
    _emit("sim.persisted", status="completed", payload={"simulation_id": sim_id, "id": row_id})
    return {"db_id": row_id}


async def finalize_simulation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Emit completion event and return empty graph delta."""
    _emit("sim.completed", status="completed", payload={"simulation_id": state.get("simulation_id")})
    return {}
