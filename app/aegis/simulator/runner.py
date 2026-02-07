"""Runner wrapper for crisis simulation execution and job events.

Purpose:
- Execute compiled simulator graph.
- Forward custom graph events into job timelines.
- Normalize final success/failure job contract updates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.utils.job_store import job_store
from app.aegis.simulator.graph import simulator_graph


def _utcnow_naive() -> datetime:
    """Return current UTC time as naive datetime for job timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run_simulation_dag(
    *,
    scan_id: int,
    simulation_id: str,
    scenario: dict,
    run_id: str,
    emit_job_events: bool = True,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Run simulation DAG and mirror lifecycle events to job store.

    Args:
        scan_id: Source scan ID.
        simulation_id: External simulation identifier.
        scenario: Scenario payload for deterministic projections.
        run_id: Job-store run ID.
        emit_job_events: Whether to forward custom events.
        config: Optional runtime config (model/thinking knobs).

    Returns:
        Dict[str, Any]: Final graph state payload.

    Raises:
        Exception: Re-raises graph execution errors after writing failed job state.

    Side Effects:
        Writes job events/status and triggers DB/model operations via graph nodes.

    Latency:
        Potentially high due to LLM generation and persistence work.
    """
    await job_store.add_event(
        run_id,
        event_type="sim.started",
        status="running",
        step="simulator",
        payload={"scan_id": scan_id, "simulation_id": simulation_id},
    )

    final_state: Optional[dict] = None

    async def _emit(custom: dict) -> None:
        """Forward one custom graph event into job-store timeline."""
        if not emit_job_events:
            return
        try:
            await job_store.add_event(
                run_id,
                event_type=str(custom.get("event") or "custom_event"),
                status=str(custom.get("status") or "running"),
                step=str(custom.get("step") or "simulator"),
                message=str(custom.get("message")) if custom.get("message") else None,
                payload=custom.get("payload") or {},
            )
        except Exception:
            return

    try:
        init = {
            "scan_id": int(scan_id),
            "simulation_id": simulation_id,
            "scenario": scenario,
            "config": config or {},
        }

        async for item in simulator_graph.astream(
            init,
            stream_mode=["custom", "values"],
        ):
            if isinstance(item, tuple) and len(item) == 2:
                mode, payload = item
            else:
                mode, payload = "values", item

            if mode == "custom" and isinstance(payload, dict):
                await _emit(payload)
            elif mode == "values" and isinstance(payload, dict):
                final_state = payload

        await job_store.update_job(
            run_id,
            status="completed",
            result=final_state or {},
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            run_id,
            event_type="sim.completed",
            status="completed",
            step="simulator",
            payload={"scan_id": scan_id, "simulation_id": simulation_id},
        )
        return final_state or {}

    except Exception as e:
        await job_store.update_job(
            run_id,
            status="failed",
            result={
                "error": str(e),
                "scan_id": scan_id,
                "simulation_id": simulation_id,
            },
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            run_id,
            event_type="sim.failed",
            status="failed",
            step="simulator",
            message=str(e),
        )
        raise
