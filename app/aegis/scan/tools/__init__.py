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
    aclient,
    writer,
    global_sem: asyncio.Semaphore,
    state_sem: asyncio.Semaphore,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a grounded tool call with bounded concurrency and custom events.

    Never raises. Returns a dict payload with {answer_text,sources,spans,data,errors}.
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
