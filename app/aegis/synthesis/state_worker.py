from __future__ import annotations

import asyncio
from typing import Any, Dict

from langgraph.config import get_stream_writer

from .config import (
    GEMINI_MODEL_SYNTH_ROLLUP,
    GEMINI_MODEL_SYNTH_STATE,
    LLM_CONCURRENCY,
    MAX_RETRIES,
    SYNTHESIS_VERSION,
    TEMPERATURE,
    THINKING_LEVEL,
    TIMEOUT_S,
)
from .llm import generate_assessment_json, generate_rollup_json
from .metrics import (
    calculate_food_security_score_from_signals,
    analyze_safe_routes_from_events,
)
from .normalize import normalize_state_intel
from .persist import persist_rollup, persist_state_assessment


_LLM_SEM: asyncio.Semaphore | None = None


def _llm_sem() -> asyncio.Semaphore:
    global _LLM_SEM
    if _LLM_SEM is None:
        _LLM_SEM = asyncio.Semaphore(max(1, int(LLM_CONCURRENCY)))
    return _LLM_SEM


async def synth_state_worker(inputs: Dict[str, Any]) -> Dict[str, Any]:
    writer = get_stream_writer()
    scan_id = int(inputs["scan_id"])
    state_name = inputs["state_name"]

    def emit(event: str, payload: dict | None = None, status: str = "running"):
        if writer:
            writer(
                {
                    "event": event,
                    "status": status,
                    "state": state_name,
                    "payload": payload or {},
                }
            )

    try:
        emit(f"{state_name}.synth_started", {"scan_id": scan_id})
        normalized = await normalize_state_intel(scan_id=scan_id, state_name=state_name)
        emit(
            f"{state_name}.inputs_loaded",
            {"scan_id": scan_id, "sources": len(normalized.get("allowed_uris") or [])},
            status="completed",
        )

        signals = normalized.get("signals") or {}
        derived = {
            **calculate_food_security_score_from_signals(signals),
            **analyze_safe_routes_from_events(normalized.get("events") or []),
        }
        emit(
            f"{state_name}.metrics_computed",
            {"scan_id": scan_id, **derived},
            status="completed",
        )

        allowed_uris = normalized.get("allowed_uris") or []
        llm_payload = {
            "scan_id": scan_id,
            "state": state_name,
            "signals": signals,
            "derived": derived,
            "recent_events": (normalized.get("events") or [])[:20],
            "allowed_uris": allowed_uris,
        }

        emit(f"{state_name}.llm_started", {"scan_id": scan_id})
        async with _llm_sem():
            assessment = await generate_assessment_json(
                model=GEMINI_MODEL_SYNTH_STATE,
                thinking_level=THINKING_LEVEL,
                temperature=TEMPERATURE,
                timeout_s=TIMEOUT_S,
                max_retries=MAX_RETRIES,
                payload=llm_payload,
                allowed_uris=allowed_uris,
            )
        emit(f"{state_name}.llm_completed", {"scan_id": scan_id}, status="completed")

        await persist_state_assessment(
            scan_id=scan_id,
            state_name=state_name,
            assessment_json=assessment,
            synthesis_version=SYNTHESIS_VERSION,
        )
        emit(f"{state_name}.persisted", {"scan_id": scan_id}, status="completed")
        emit(f"{state_name}.synth_completed", {"scan_id": scan_id}, status="completed")

        return {"assessments": [assessment], "errors": []}
    except Exception as e:
        emit(
            f"{state_name}.synth_failed",
            {"scan_id": scan_id, "error": str(e)},
            status="failed",
        )
        err = {
            "scan_id": scan_id,
            "state": state_name,
            "stage": "synth_state_worker",
            "error": str(e),
        }
        # persist error object so downstream knows synthesis ran but failed.
        try:
            await persist_state_assessment(
                scan_id=scan_id,
                state_name=state_name,
                assessment_json={
                    "scan_id": scan_id,
                    "state": state_name,
                    "error": str(e),
                },
                synthesis_version=SYNTHESIS_VERSION,
            )
        except Exception:
            pass
        return {"assessments": [], "errors": [err]}


async def rollup_worker(state: Dict[str, Any]) -> Dict[str, Any]:
    writer = get_stream_writer()
    scan_id = int(state["scan_id"])
    assessments = state.get("assessments") or []

    def emit(event: str, payload: dict | None = None, status: str = "running"):
        if writer:
            writer({"event": event, "status": status, "payload": payload or {}})

    emit("rollup_started", {"scan_id": scan_id, "n_assessments": len(assessments)})

    allowed = []
    seen = set()
    for a in assessments:
        for f in a.get("key_findings") or []:
            for u in f.get("source_uris") or []:
                if u not in seen:
                    seen.add(u)
                    allowed.append(u)

    payload = {"scan_id": scan_id, "assessments": assessments, "allowed_uris": allowed}
    async with _llm_sem():
        rollup = await generate_rollup_json(
            model=GEMINI_MODEL_SYNTH_ROLLUP,
            thinking_level=THINKING_LEVEL,
            temperature=TEMPERATURE,
            timeout_s=TIMEOUT_S,
            max_retries=MAX_RETRIES,
            payload=payload,
            allowed_uris=allowed,
        )
    emit("rollup_completed", {"scan_id": scan_id}, status="completed")

    await persist_rollup(scan_id=scan_id, rollup_json=rollup)
    emit("rollup_persisted", {"scan_id": scan_id}, status="completed")

    return {"rollup": rollup}


async def finalize_synthesis(state: Dict[str, Any]) -> Dict[str, Any]:
    writer = get_stream_writer()
    if writer:
        writer(
            {
                "event": "synthesis_completed",
                "status": "completed",
                "payload": {"scan_id": int(state["scan_id"])},
            }
        )
    return {}
