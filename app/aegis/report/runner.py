from __future__ import annotations

from typing import Any, Dict, Optional

from app.aegis.report.graph import report_graph
from app.aegis.report.persist import create_report_row, mark_report_failed


async def run_report_dag(
    *,
    report_id: str,
    scan_id: int,
    states: list[str],
    include_infographics: bool,
    include_annexes: bool,
    output_dir: str,
    emit_job_events: bool = True,
) -> Dict[str, Any]:
    job_store = None
    if emit_job_events:
        try:
            from app.utils.job_store import job_store as _job_store

            job_store = _job_store
        except Exception:
            job_store = None

    try:
        await create_report_row(
            report_id=report_id,
            scan_id=int(scan_id),
            states=states,
            include_infographics=include_infographics,
            include_annexes=include_annexes,
        )
    except Exception:
        pass

    initial_state: dict = {
        "report_id": report_id,
        "scan_id": int(scan_id),
        "states": states,
        "include_infographics": bool(include_infographics),
        "include_annexes": bool(include_annexes),
        "output_dir": output_dir,
    }

    final_state: Optional[dict] = None

    async def _maybe_emit(custom: dict) -> None:
        if not job_store:
            return
        try:
            event_type = custom.get("event") or "custom_event"
            status = custom.get("status") or "running"
            step = custom.get("step") or "report"
            msg = custom.get("message")
            payload = custom.get("payload") or {}
            await job_store.add_event(
                report_id,
                event_type=event_type,
                status=status,
                step=step,
                message=msg,
                payload=payload,
            )
        except Exception:
            pass

    try:
        async for item in report_graph.astream(
            initial_state,
            stream_mode=["custom", "values"],
            config={"max_concurrency": 1},
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
    except Exception as e:
        await mark_report_failed(report_id=report_id, error=str(e))
        if job_store:
            try:
                await job_store.add_event(
                    report_id,
                    event_type="report_failed",
                    status="failed",
                    step="report_error",
                    message=str(e),
                )
            except Exception:
                pass
        raise

    pdf_path = (final_state or {}).get("pdf_path")
    sources_cited = (final_state or {}).get("sources_cited")
    return {
        "report_id": report_id,
        "scan_id": int(scan_id),
        "status": "completed",
        "pdf_path": pdf_path,
        "states_analyzed": states,
        "sources_cited": int(sources_cited or 0),
    }
