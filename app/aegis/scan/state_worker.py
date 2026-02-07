"""Per-state scan worker node: planner, bounded tools, synthesis, persistence.

Purpose:
- Execute one state's scan lifecycle inside LangGraph node execution.
- Emit granular custom events for UI/job timelines.
- Persist incremental state intelligence for near-real-time API updates.

Used by:
- `app.aegis.scan.graph` as the only worker node.

Assumptions:
- Inputs contain `state`, `api_key`, and `days_back`.
- Tool registry functions return normalized grounding payloads.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from google import genai
from langgraph.config import get_stream_writer

from app.aegis.scan.config import (
    GEMINI_MODEL_PLANNER,
    GEMINI_MODEL_GROUNDED,
    GEMINI_MODEL_SYNTH,
    THINKING_LEVEL,
    GLOBAL_TOOL_CONCURRENCY,
    PER_STATE_TOOL_CONCURRENCY,
    GEMINI_TIMEOUT_S,
    GEMINI_MAX_RETRIES,
)
from app.aegis.scan.gemini_adapter import Gemini3Adapter
from app.aegis.scan.tool_declarations import all_declarations
from app.aegis.scan.tools import run_tool_bounded
from app.aegis.scan.persist import persist_state_intelligence


_GLOBAL_TOOL_SEM: asyncio.Semaphore | None = None


def _global_tool_sem() -> asyncio.Semaphore:
    """Lazily initialize process-wide semaphore for total tool concurrency."""
    global _GLOBAL_TOOL_SEM
    if _GLOBAL_TOOL_SEM is None:
        _GLOBAL_TOOL_SEM = asyncio.Semaphore(max(1, int(GLOBAL_TOOL_CONCURRENCY)))
    return _GLOBAL_TOOL_SEM


async def aegis_state_worker(inputs: dict) -> dict:
    """Run one state worker pass: planning, tool collection, and synthesis.

    Args:
        inputs: Node input payload including state, api key, days_back, and scan_id.

    Returns:
        dict: Node output with single-entry `results` list.

    Raises:
        Does not raise intentionally for planner/tool errors; errors are returned
        as result payloads and custom events.

    Side Effects:
        Emits custom stream events, performs model/network calls, and optionally
        writes state intelligence rows to DB.

    Latency:
        Potentially high due to planner + multiple grounded tool calls.
    """
    writer = get_stream_writer()

    state = (
        inputs.get("state") or inputs.get("region") or inputs.get("state_name") or ""
    )
    api_key = inputs.get("api_key")
    days_back = int(inputs.get("days_back") or 7)
    scan_id = inputs.get("scan_id")

    if not state:
        if writer:
            writer(
                {
                    "event": "worker_failed",
                    "status": "failed",
                    "message": "missing_state",
                }
            )
        return {"results": [{"state": state, "error": "missing_state"}]}
    if not api_key:
        if writer:
            writer(
                {
                    "event": f"{state}.planning_failed",
                    "status": "failed",
                    "state": state,
                    "message": "missing_api_key",
                }
            )
        return {"results": [{"state": state, "error": "missing_api_key"}]}

    if writer:
        writer(
            {
                "event": f"{state}.planning_started",
                "status": "running",
                "state": state,
                "message": f"Planning tool calls for {state}",
            }
        )

    adapter = Gemini3Adapter(
        api_key=api_key,
        model_planner=GEMINI_MODEL_PLANNER,
        model_grounded=GEMINI_MODEL_GROUNDED,
        model_synth=GEMINI_MODEL_SYNTH,
        thinking_level=THINKING_LEVEL,
        timeout_s=GEMINI_TIMEOUT_S,
        max_retries=GEMINI_MAX_RETRIES,
    )

    plan_prompt = (
        f"You are an intelligence collection planner for humanitarian displacement risk in {state}, Nigeria.\n"
        f"Time window: last {days_back} days.\n"
        "Call the available tools to collect: conflict/security, displacement/IDPs, food security/IPC, and economic/market signals.\n"
        "Use parallel function calling if possible."
    )

    try:
        plan = await adapter.plan_tools(
            prompt=plan_prompt,
            function_declarations=all_declarations(),
        )
    except Exception as e:
        if writer:
            writer(
                {
                    "event": f"{state}.planning_failed",
                    "status": "failed",
                    "state": state,
                    "message": f"Planner failed: {e}",
                }
            )
        return {"results": [{"state": state, "error": f"planner_failed: {e}"}]}

    if writer:
        writer(
            {
                "event": f"{state}.planning_completed",
                "status": "completed",
                "state": state,
                "message": f"Planner returned {len(plan.function_calls)} tool calls",
                "payload": {"tool_calls": [fc.name for fc in plan.function_calls]},
            }
        )

    # tools: bounded parallelism per state + zonally.
    state_sem = asyncio.Semaphore(max(1, int(PER_STATE_TOOL_CONCURRENCY)))
    global_sem = _global_tool_sem()

    aclient = genai.Client(api_key=api_key)

    async def _run_call(fc: Any) -> tuple[str | None, str, Dict[str, Any]]:
        """Execute one planned function call via bounded tool wrapper."""
        call_id = getattr(fc, "id", None)
        tool_name = fc.name or ""
        args = getattr(fc, "args", None) or getattr(fc, "arguments", None) or {}
        call_state = args.get("state") or args.get("region") or state
        result = await run_tool_bounded(
            tool_name=tool_name,
            state=call_state,
            aclient=aclient,
            writer=writer,
            global_sem=global_sem,
            state_sem=state_sem,
            timeout_s=GEMINI_TIMEOUT_S,
        )
        return str(call_id) if call_id is not None else None, tool_name, result

    tasks = [_run_call(fc) for fc in plan.function_calls]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    tool_results: Dict[str, Any] = {}
    for item in gathered:
        if isinstance(item, Exception):
            continue
        call_id, name, result = item
        if call_id:
            tool_results[call_id] = result
        if name and name not in tool_results:
            tool_results[name] = result

    # persist immediately for incremental UI updates (optional, requires scan_id)
    if scan_id:
        try:
            await persist_state_intelligence(
                scan_id=int(scan_id),
                state_name=state,
                tool_results=tool_results,
            )
            if writer:
                writer(
                    {
                        "event": f"{state}.persisted",
                        "status": "completed",
                        "state": state,
                        "message": f"Persisted state intelligence for {state}",
                    }
                )
        except Exception as e:
            if writer:
                writer(
                    {
                        "event": f"{state}.persist_failed",
                        "status": "failed",
                        "state": state,
                        "message": f"Persist failed for {state}: {e}",
                    }
                )

    if writer:
        writer(
            {
                "event": f"{state}.synthesis_started",
                "status": "running",
                "state": state,
                "message": f"Synthesis started for {state}",
            }
        )

    synth_prompt = (
        f"Produce a concise, actionable intelligence summary for {state}, Nigeria (last {days_back} days).\n"
        "Structure:\n"
        "- Risk level (LOW/ELEVATED/HIGH/CRITICAL) with brief rationale\n"
        "- Key conflict events (bullets)\n"
        "- Displacement signals (bullets)\n"
        "- Food security/IPC signals (bullets)\n"
        "- Market/economic signals (bullets)\n"
        "- Farmer loan policy notes (how terms should adjust)\n"
        "Do not invent facts beyond tool responses."
    )

    analysis_text = ""
    try:
        analysis_text = await adapter.synthesize(
            plan=plan,
            tool_results=tool_results,
            synth_prompt=synth_prompt,
        )
    except Exception as e:
        analysis_text = f"Synthesis failed: {e}"

    if writer:
        writer(
            {
                "event": f"{state}.completed",
                "status": "completed",
                "state": state,
                "message": f"{state} completed",
            }
        )

    return {
        "results": [
            {
                "state": state,
                "days_back": days_back,
                "analysis": analysis_text,
                "tool_results": tool_results,
            }
        ]
    }
