"""Runtime orchestration bridge between API jobs and LangGraph execution.

This module runs/resumes `farma_graph` in the background and mirrors node-level
custom events into `job_store` so clients can track progress in real time.

Used by:
- API job handlers that create and resume workflow runs.

Responsibilities:
- Start pipeline runs and collect final state snapshots.
- Persist completion/failure/awaiting-human job state.
- Forward LangGraph custom events into job timeline records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import logging

from app.utils.job_store import job_store
from app.workflows.graph import farma_graph

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Return current UTC timestamp without timezone info.

    Returns:
        Naive UTC datetime used by existing job-store schema.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time system clock read.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _stream_and_collect(
    input_val: Any,
    config: dict,
    job_id: str,
    emit_job_events: bool,
) -> Optional[dict]:
    """Stream graph events and capture the latest state snapshot.

    Args:
        input_val: Initial graph input or resume command.
        config: LangGraph execution config containing thread id/concurrency.
        job_id: Job identifier for event forwarding.
        emit_job_events: Whether to mirror custom events into job store.

    Returns:
        Final state dict observed from `values` stream mode, or `None` if no
        value payload was emitted.

    Raises:
        Exception: Propagates graph execution/runtime failures.

    Side Effects:
        Streams graph execution and optionally writes job events.

    Latency:
        Dominated by graph node execution and network/DB calls inside nodes.
    """
    final_state: Optional[dict] = None

    async def _maybe_emit(custom: dict) -> None:
        """Forward one custom event payload to job store when enabled.

        Args:
            custom: Custom payload from LangGraph `stream_mode="custom"`.

        Returns:
            None.

        Raises:
            None: Errors are swallowed to avoid breaking pipeline execution.

        Side Effects:
            Writes an event row to `job_store` when enabled.

        Latency:
            Includes async job-store write latency.
        """
        if not emit_job_events:
            return
        try:
            event_type = custom.get("event") or "custom_event"
            status = custom.get("status") or "running"
            step = custom.get("step") or custom.get("state") or "pipeline"
            msg = custom.get("message")
            payload = custom.get("payload") or {}
            await job_store.add_event(
                job_id,
                event_type=str(event_type),
                status=str(status),
                step=str(step) if step is not None else None,
                message=str(msg) if msg else None,
                payload=payload,
            )
        except Exception:
            # Never break the pipeline due to event emission.
            return

    async for item in farma_graph.astream(
        input_val,
        stream_mode=["custom", "values"],
        config=config,
    ):
        mode = None
        payload = None
        if isinstance(item, tuple) and len(item) == 2:
            mode, payload = item
        else:
            mode = "values"
            payload = item

        if mode == "custom" and isinstance(payload, dict):
            await _maybe_emit(payload)
        elif mode == "values" and isinstance(payload, dict):
            final_state = payload

    return final_state


def _is_interrupted(thread_id: str) -> bool:
    """Check whether a thread currently has pending LangGraph interrupts.

    Args:
        thread_id: Workflow thread identifier used by checkpointer.

    Returns:
        `True` if any task has interrupt payloads, otherwise `False`.

    Raises:
        None: Snapshot inspection failures return `False`.

    Side Effects:
        Reads graph state snapshot from checkpointer.

    Latency:
        Small checkpointer lookup.
    """
    try:
        snapshot = farma_graph.get_state({"configurable": {"thread_id": thread_id}})
        tasks = getattr(snapshot, "tasks", None) or ()
        for task in tasks:
            if getattr(task, "interrupts", None):
                return True
        return False
    except Exception:
        return False


def _get_interrupt_data(thread_id: str) -> Optional[dict]:
    """Extract first interrupt payload from a paused workflow thread.

    Args:
        thread_id: Workflow thread identifier.

    Returns:
        Interrupt value dict, or `None` when not interrupted/unavailable.

    Raises:
        None: Snapshot/parsing errors return `None`.

    Side Effects:
        Reads graph state snapshot from checkpointer.

    Latency:
        Small checkpointer lookup.
    """
    try:
        snapshot = farma_graph.get_state({"configurable": {"thread_id": thread_id}})
        for task in getattr(snapshot, "tasks", ()) or ():
            for intr in getattr(task, "interrupts", ()) or ():
                val = getattr(intr, "value", None)
                if isinstance(val, dict):
                    return val
        return None
    except Exception:
        return None


async def run_farma_job(
    *,
    job_id: str,
    initial_state: Dict[str, Any],
    thread_id: str,
    emit_job_events: bool = True,
    max_concurrency: int = 8,
) -> Dict[str, Any]:
    """Run a new FARMA workflow job to completion or human-interrupt state.

    Args:
        job_id: Job-store identifier to update and emit events against.
        initial_state: Initial workflow state payload.
        thread_id: LangGraph checkpointer thread id.
        emit_job_events: Whether custom node events are persisted to job store.
        max_concurrency: LangGraph node concurrency limit.

    Returns:
        Final or latest workflow state dict.

    Raises:
        Exception: Re-raises graph/runtime errors after marking job failed.

    Side Effects:
        Writes job status transitions and timeline events to job store.
        Executes `farma_graph` stream and may leave job in awaiting-human state.

    Latency:
        End-to-end runtime of all workflow nodes plus persistence writes.
    """
    await job_store.add_event(
        job_id,
        event_type="pipeline_started",
        status="running",
        step="start",
        message="FARMA workflow started",
    )

    config = {
        "max_concurrency": int(max_concurrency),
        "configurable": {"thread_id": thread_id},
    }

    try:
        final_state = await _stream_and_collect(
            initial_state, config, job_id, emit_job_events,
        )

        # Check if graph paused at an interrupt (human-in-the-loop)
        if _is_interrupted(thread_id):
            interrupt_data = _get_interrupt_data(thread_id)

            # Deliver the farmer acknowledgment SMS while the graph is paused.
            ack = (interrupt_data or {}).get("ack_message")
            if ack:
                logger.info(
                    "SMS ack sent (pre-interrupt): phone=%s message=%s",
                    thread_id, ack,
                )
                await job_store.add_event(
                    job_id,
                    event_type="ack_sms_sent",
                    status="running",
                    step="human",
                    message=ack,
                    payload={"phone": thread_id},
                )

            await job_store.update_job(
                job_id,
                status="awaiting_human",
                result={"interrupt": interrupt_data, "state": final_state or {}},
                completed_at=None,
            )
            await job_store.add_event(
                job_id,
                event_type="pipeline_awaiting_human",
                status="awaiting_human",
                step="human",
                message="Pipeline paused — awaiting human agent response",
                payload=interrupt_data or {},
            )
            return final_state or {}

        # Normal completion
        await job_store.update_job(
            job_id,
            status="completed",
            result=final_state or {},
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            job_id,
            event_type="pipeline_completed",
            status="completed",
            step="complete",
            message="FARMA workflow completed",
        )
        return final_state or {}

    except Exception as e:
        await job_store.update_job(
            job_id,
            status="failed",
            result={"error": str(e)},
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            job_id,
            event_type="pipeline_failed",
            status="failed",
            step="error",
            message=str(e),
        )
        raise


async def resume_farma_job(
    *,
    job_id: str,
    thread_id: str,
    human_response: str,
    emit_job_events: bool = True,
    max_concurrency: int = 8,
) -> Dict[str, Any]:
    """Resume an interrupted workflow using human-agent response text.

    Args:
        job_id: Existing paused job identifier.
        thread_id: LangGraph thread id for interrupted checkpoint.
        human_response: Message entered by human agent for farmer reply.
        emit_job_events: Whether to persist custom node events.
        max_concurrency: LangGraph node concurrency limit for resumed run.

    Returns:
        Final workflow state after resume completes.

    Raises:
        Exception: Re-raises graph/runtime errors after marking job failed.

    Side Effects:
        Updates job status/events and executes resumed graph stream.

    Latency:
        Depends on remaining nodes after interrupt plus store write latency.
    """
    from langgraph.types import Command

    await job_store.update_job(job_id, status="running")
    await job_store.add_event(
        job_id,
        event_type="pipeline_resumed",
        status="running",
        step="human",
        message="Human agent provided response — resuming pipeline",
        payload={"human_response": human_response},
    )

    config = {
        "max_concurrency": int(max_concurrency),
        "configurable": {"thread_id": thread_id},
    }

    try:
        final_state = await _stream_and_collect(
            Command(resume=human_response), config, job_id, emit_job_events,
        )

        await job_store.update_job(
            job_id,
            status="completed",
            result=final_state or {},
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            job_id,
            event_type="pipeline_completed",
            status="completed",
            step="complete",
            message="FARMA workflow completed after human resume",
        )
        return final_state or {}

    except Exception as e:
        await job_store.update_job(
            job_id,
            status="failed",
            result={"error": str(e)},
            completed_at=_utcnow_naive(),
        )
        await job_store.add_event(
            job_id,
            event_type="pipeline_failed",
            status="failed",
            step="error",
            message=str(e),
        )
        raise
