"""Waiting-state node for farmer follow-up responses.

This module emits an event when the workflow is paused for user clarification.
It does not mutate business state and simply hands control to the SMS sender.
"""

from __future__ import annotations

from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


async def awaiting_response_handler(state: FarmaState) -> dict:
    """Emit waiting-state before sending pending question SMS.

    Args:
        state: Workflow state containing pending clarification fields.

    Returns:
        Empty dict because upstream nodes already prepared outgoing message.

    Raises:
        None: This handler does not intentionally raise.

    Side Effects:
        Emits `awaiting_farmer_response` event for frontend tracking.

    Latency:
        Constant-time local event emission.
    """
    emit_event(
        "awaiting_farmer_response",
        step="awaiting",
        payload={
            "pending_question": state.get("pending_question"),
            "pending_question_type": state.get("pending_question_type"),
            "phone": state.get("phone"),
        },
    )
    return {}


__all__ = ["awaiting_response_handler"]
