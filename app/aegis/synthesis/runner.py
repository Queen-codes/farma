"""Runner wrapper for executing synthesis and returning summary output.

Purpose:
- Invoke compiled synthesis graph with concurrency settings.
- Forward custom graph events into job-store timelines.
- Return compact summary for API/job callers.

Used by:
- API route `/api/aegis/synthesis`.
- Scheduler auto-report pipeline.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.synthesis.config import MAX_STATE_WORKERS
from app.aegis.synthesis.graph import synthesis_graph


async def run_synthesis_dag(
    *,
    scan_id: int,
    states: list[str],
    run_id: str,
    emit_job_events: bool = True,
) -> Dict[str, Any]:
    """Execute synthesis for a scan and selected states.

    Args:
        scan_id: Scan ID whose persisted state intelligence will be synthesized.
        states: States to process.
        run_id: Job/run identifier for event forwarding.
        emit_job_events: Whether to mirror custom graph events to job store.

    Returns:
        Dict[str, Any]: Summary with status, assessment count, errors, and rollup.

    Raises:
        Exception: Can propagate graph execution failures.

    Side Effects:
        Performs model calls, DB writes (via worker nodes), and optional job events.

    Latency:
        Potentially high due to per-state LLM synthesis + rollup generation.
    """
    job_store = None
    if emit_job_events:
        try:
            from app.utils.job_store import job_store as _job_store

            job_store = _job_store
        except Exception:
            job_store = None

    initial_state: dict = {
        "scan_id": int(scan_id),
        "states": states,
        "config": {},
        "assessments": [],
        "errors": [],
        "rollup": None,
    }

    final_state: Optional[dict] = None

    async def _maybe_emit(custom: dict) -> None:
        """Forward one custom synthesis event into the async job store."""
        if not job_store:
            return
        try:
            event_type = custom.get("event") or "custom_event"
            status = custom.get("status") or "running"
            step = custom.get("state") or "synthesis"
            msg = None
            payload = custom.get("payload") or {}
            await job_store.add_event(
                run_id,
                event_type=event_type,
                status=status,
                step=step,
                message=msg,
                payload=payload,
            )
        except Exception:
            pass

    async for item in synthesis_graph.astream(
        initial_state,
        stream_mode=["custom", "values"],
        config={"max_concurrency": int(MAX_STATE_WORKERS)},
    ):
        mode = None
        payload = None
        if isinstance(item, tuple) and len(item) == 2:
            mode, payload = item
        else:
            payload = item
            mode = "values"

        if mode == "custom" and isinstance(payload, dict):
            await _maybe_emit(payload)
        elif mode == "values" and isinstance(payload, dict):
            final_state = payload

    assessments = (final_state or {}).get("assessments") or []
    errors = (final_state or {}).get("errors") or []
    rollup = (final_state or {}).get("rollup")

    return {
        "scan_id": int(scan_id),
        "run_id": run_id,
        "status": "completed" if not errors else "completed_with_errors",
        "assessments_count": len(assessments),
        "errors": errors,
        "rollup": rollup,
    }
