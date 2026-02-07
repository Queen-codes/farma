"""LangGraph custom event helpers for workflow progress streaming.

This module exposes a small wrapper (`emit_event`) around LangGraph's
stream-writer API so nodes can emit consistent progress events that are later
forwarded by `app.workflows.runner` into the job store/frontend.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer


def emit_event(
    event: str,
    *,
    status: str = "running",
    step: str,
    message: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a workflow event payload with a stable schema.

    Args:
        event: Event name (for example `parse_intent_started`).
        status: Event lifecycle status (`running`, `completed`, `failed`, etc.).
        step: Pipeline stage label used by UI timelines.
        message: Optional short human-readable message.
        payload: Optional structured metadata attached to the event.

    Returns:
        None.

    Raises:
        None: This helper does not intentionally raise.

    Side Effects:
        Writes one custom event to the active LangGraph stream writer.

    Latency:
        Constant-time local serialization and callback invocation.
    """
    writer = get_stream_writer()
    writer(
        {
            "event": event,
            "status": status,
            "step": step,
            "message": message,
            "payload": payload or {},
        }
    )
