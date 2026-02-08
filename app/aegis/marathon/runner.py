"""Runner wrapper for marathon continuity pipeline execution.

Purpose:
- Execute compiled marathon graph for one day/track.
- Forward custom events to job timeline.
- Normalize success/failure job-store updates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.utils.job_store import job_store
from app.aegis.marathon.graph import marathon_graph


def _utcnow_naive() -> datetime:
    """Return current UTC time as naive datetime for job timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _compact_result(
    *,
    track_id: str,
    day_date: str,
    state: Optional[dict],
) -> Dict[str, Any]:
    """Build compact JSON-safe result payload for marathon job status."""
    st = state or {}
    return {
        "track_id": track_id,
        "day_date": day_date,
        "scan_id": st.get("scan_id"),
        "prev_scan_id": st.get("prev_scan_id"),
        "mode": st.get("mode"),
        "thinking_level": st.get("effective_thinking_level"),
        "actions_taken": list(st.get("actions_taken") or []),
        "simulation_triggered": st.get("simulation_triggered"),
        "report_triggered": st.get("report_triggered"),
        "status": "completed",
    }


async def run_marathon_day(
    *,
    run_id: str,
    track_id: str,
    day_date: str,
    scan_id: Optional[int] = None,
    prev_scan_id: Optional[int] = None,
    mode: str = "manual",
    config: Optional[dict] = None,
    emit_job_events: bool = True,
) -> Dict[str, Any]:
    """Run Marathon continuity pipleline and forward custom events to job_store."""
    await job_store.add_event(
        run_id,
        event_type="marathon_started",
        status="running",
        step="marathon_start",
        payload={
            "track_id": track_id,
            "day_date": day_date,
            "scan_id": scan_id,
            "mode": mode,
        },
    )

    final_state: Optional[dict] = None

    async def _emit(custom: dict) -> None:
        """Forward one custom marathon event to job store."""
        if not emit_job_events:
            return
        try:
            await job_store.add_event(
                run_id,
                event_type=str(custom.get("event") or "custom_event"),
                status=str(custom.get("status") or "running"),
                step=str(custom.get("step") or "marathon"),
                message=str(custom.get("message")) if custom.get("message") else None,
                payload=custom.get("payload") or {},
            )
        except Exception:
            return

    try:
        initial: Dict[str, Any] = {
            "track_id": track_id,
            "day_date": day_date,
            "config": config or {},
            "mode": mode,
            # Accumulator fields
            "scan_assessments": [],
            "prev_assessments": [],
            "uri_whitelist": [],
            "actions_taken": [],
            "errors": [],
        }

        # scan_id is optional in autonomous mode
        if scan_id is not None:
            initial["scan_id"] = int(scan_id)

        if prev_scan_id is not None:
            initial["prev_scan_id"] = int(prev_scan_id)

        async for item in marathon_graph.astream(
            initial,
            stream_mode=["custom", "values"],
            config={"configurable": {"thread_id": run_id}},
        ):
            if isinstance(item, tuple) and len(item) == 2:
                stream_mode, payload = item
            else:
                stream_mode, payload = "values", item

            if stream_mode == "custom" and isinstance(payload, dict):
                await _emit(payload)
            elif stream_mode == "values" and isinstance(payload, dict):
                final_state = payload

        result_payload = _compact_result(
            track_id=track_id,
            day_date=day_date,
            state=final_state,
        )
        await job_store.update_job(
            run_id,
            status="completed",
            result=result_payload,
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            run_id,
            event_type="marathon_completed",
            status="completed",
            step="marathon_complete",
            payload={
                "track_id": track_id,
                "day_date": day_date,
                "actions_taken": result_payload.get("actions_taken") or [],
            },
        )
        return final_state or {}

    except Exception as e:
        try:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e), "track_id": track_id, "day_date": day_date},
                completed_at=_utcnow_naive(),
            )
        except Exception:
            pass
        try:
            await job_store.add_event(
                run_id,
                event_type="marathon_failed",
                status="failed",
                step="marathon_error",
                message=str(e),
            )
        except Exception:
            pass
        raise
