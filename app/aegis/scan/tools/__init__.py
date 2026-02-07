"""Tool registry and bounded execution wrapper for scan grounded tools.

Purpose:
- Map planner function names to concrete tool coroutines.
- Enforce global and per-state concurrency limits.
- Emit structured custom events around tool execution.

Used by:
- `app.aegis.scan.state_worker`.

Assumptions:
- Registered tools share a common `(*, aclient, state, timeout_s)` signature.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from .conflict import search_conflict_events
from .displacement import search_displacement
from .economic import search_economic_indicators
from .food_security import search_food_security

ToolFn = Callable[..., Awaitable[Dict[str, Any]]]


TOOL_REGISTRY: dict[str, ToolFn] = {
    "search_conflict_events": search_conflict_events,
    "search_displacement": search_displacement,
    "search_food_security": search_food_security,
    "search_economic_indicators": search_economic_indicators,
}


async def run_tool_bounded(
    *,
    tool_name: str,
    state: str,
    aclient: Any,
    writer: Callable[[dict[str, Any]], None] | None,
    global_sem: asyncio.Semaphore,
    state_sem: asyncio.Semaphore,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a grounded tool call with bounded concurrency and custom events.

    Args:
        tool_name: Registry key for the tool to execute.
        state: State label passed into the tool.
        aclient: Authenticated Gemini client instance.
        writer: Optional LangGraph stream writer callback.
        global_sem: Cross-process global concurrency semaphore.
        state_sem: Per-state concurrency semaphore.
        timeout_s: Optional per-call timeout passed to tool function.

    Returns:
        Dict[str, Any]: Tool payload with normalized citation/data fields.

    Raises:
        Does not raise intentionally; execution errors are converted into
        structured error payloads.

    Side Effects:
        Emits custom events through writer callback.
        Makes network/model calls through underlying tool function.

    Latency:
        Network and inference bound; can queue behind semaphores.
    """
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        if writer:
            writer(
                {
                    "event": f"{state}.{tool_name}_failed",
                    "status": "failed",
                    "state": state,
                    "tool": tool_name,
                    "message": f"Unknown tool: {tool_name}",
                }
            )
        return {
            "answer_text": "",
            "sources": [],
            "spans": [],
            "data": {"state": state},
            "errors": f"unknown_tool: {tool_name}",
        }

    if writer:
        writer(
            {
                "event": f"{state}.{tool_name}_started",
                "status": "running",
                "state": state,
                "tool": tool_name,
                "message": f"{tool_name} started for {state}",
            }
        )

    try:
        async with global_sem:
            async with state_sem:
                result = await tool(
                    aclient=aclient,
                    state=state,
                    timeout_s=timeout_s,
                )
        if writer:
            writer(
                {
                    "event": f"{state}.{tool_name}_completed",
                    "status": "completed",
                    "state": state,
                    "tool": tool_name,
                    "message": f"{tool_name} completed for {state}",
                    "payload": {
                        "sources": len(result.get("sources") or []),
                        "errors": result.get("errors"),
                    },
                }
            )
        return result
    except Exception as e:
        if writer:
            writer(
                {
                    "event": f"{state}.{tool_name}_failed",
                    "status": "failed",
                    "state": state,
                    "tool": tool_name,
                    "message": f"{tool_name} failed for {state}: {e}",
                }
            )
        return {
            "answer_text": "",
            "sources": [],
            "spans": [],
            "data": {"state": state},
            "errors": f"tool_exception: {e}",
        }
