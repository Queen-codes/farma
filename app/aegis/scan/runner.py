"""Runner wrapper for executing the compiled scan graph and finalizing scan DB state.

Purpose:
- Execute `aegis_scan_graph` with concurrency limits.
- Forward graph custom events to job-store timeline events.
- Finalize persisted scan aggregates (`states_scanned`, fatalities, LGA risk).

Used by:
- `app.aegis.graph.run_aegis_scan`.
- Scheduler and API layers that trigger scan runs.

Assumptions:
- `scan_id` points to an existing `AegisScan` row when finalization is desired.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.scan.config import MAX_STATE_WORKERS
from app.aegis.scan.graph import aegis_scan_graph
from app.aegis.scan.persist import finalize_scan


async def run_aegis_scan(
    *,
    api_key: str,
    states: list[str],
    days_back: int,
    run_id: str,
    scan_id: int | None = None,
    emit_job_events: bool = True,
) -> Dict[str, Any]:
    """Run a full scan graph invocation and return summary metrics.

    Args:
        api_key: Gemini API key used by per-state workers.
        states: States to process in this run.
        days_back: Lookback window used by grounded tools.
        run_id: External run identifier.
        scan_id: Optional persisted scan row ID for finalize step.
        emit_job_events: Whether to mirror custom events to `job_store`.

    Returns:
        Dict[str, Any]: Run summary with status, totals, and state results.

    Raises:
        Exception: Can propagate graph execution failures.

    Side Effects:
        Reads/writes job events, executes model/network calls through workers,
        and mutates scan DB totals when `scan_id` is provided.

    Latency:
        Potentially high due to parallel grounded model/tool execution.
    """
    job_store = None
    if emit_job_events:
        try:
            from app.utils.job_store import job_store as _job_store

            job_store = _job_store
        except Exception:
            job_store = None

    initial_state = {
        "run_id": run_id,
        "days_back": days_back,
        "states": states,
        "api_key": api_key,
        "scan_id": scan_id,
        "results": [],
    }

    final_state: Optional[dict] = None

    async def _maybe_emit(custom: dict) -> None:
        """Forward one custom graph event to the shared job store."""
        if not job_store:
            return
        try:
            event_type = custom.get("event") or "custom_event"
            status = custom.get("status") or "running"
            state = custom.get("state") or custom.get("region") or ""
            tool = custom.get("tool") or ""
            message = custom.get("message") or event_type
            payload = custom.get("payload") or {}
            step = tool or state or "scan"
            await job_store.add_event(
                run_id,
                event_type=event_type,
                status=status,
                step=step,
                message=message,
                payload=payload,
            )
        except Exception:
            pass

    # Stream custom events + values (final state)
    async for item in aegis_scan_graph.astream(
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

    results = (final_state or {}).get("results") or []

    # Finalize scan in DB
    total_events = 0
    total_fatalities = 0
    if scan_id:
        try:
            summary = await finalize_scan(scan_id=int(scan_id))
            total_events = int(summary.get("total_events") or 0)
            total_fatalities = int(summary.get("total_fatalities") or 0)
            #  emit one job event: runner-level, outside LangGraph
            if job_store:
                await job_store.add_event(
                    run_id,
                    event_type="scan_finalized",
                    status="completed",
                    step="finalize",
                    message="Scan finalized",
                    payload=summary,
                )
        except Exception as e:
            if job_store:
                await job_store.add_event(
                    run_id,
                    event_type="scan_finalize_failed",
                    status="failed",
                    step="finalize",
                    message=str(e),
                )

    return {
        "run_id": run_id,
        "scan_id": scan_id,
        "states_scanned": len(results),
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "status": "completed",
        "results": results,
    }
