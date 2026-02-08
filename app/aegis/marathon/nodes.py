"""Marathon graph node implementations for continuity intelligence orchestration.

Purpose:
- Load prior context and resolve current/previous scan artifacts.
- Compute deterministic deltas and generate continuity note with LLM.
- Decide and trigger autonomous follow-up actions (simulation/report).
- Persist final marathon day artifacts.

Used by:
- `app.aegis.marathon.graph`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langgraph.config import get_stream_writer
from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisScan, StateIntelligence, AegisMarathonDay
from app.aegis.marathon.deltas import (
    build_auto_scenario,
    compute_delta,
    critical_states,
    has_escalation,
    uri_whitelist_from_artifacts,
)
from app.aegis.marathon.llm import generate_continuity_note
from app.aegis.marathon.persist import upsert_marathon_day

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Return current UTC time as naive datetime for timestamp writes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _emit(event: str, payload: Optional[dict] = None, status: str = "running") -> None:
    """Emit marathon-scoped custom event via LangGraph stream writer."""
    writer = get_stream_writer()
    writer(
        {
            "event": event,
            "status": status,
            "step": "marathon",
            "payload": payload or {},
        }
    )


# Node 1: load_context — load previous day's state + choose thinking level
async def load_context_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Load previous marathon-day context and set baseline thinking level.

    Args:
        state: Graph state containing track and optional previous scan identifiers.

    Returns:
        Dict[str, Any]: Previous day metadata and effective thinking level seed.

    Raises:
        SQLAlchemyError: Can propagate DB query failures.

    Side Effects:
        Reads marathon-day context from database and emits custom events.
    """
    _emit(
        "marathon.started",
        {"track_id": state.get("track_id"), "scan_id": state.get("scan_id")},
    )

    track_id = state["track_id"]
    prev_scan_id = state.get("prev_scan_id")

    prev_row: Optional[AegisMarathonDay] = None
    async with get_async_session() as session:
        if prev_scan_id is None:
            # Find the most recent marathon day for this track
            res = await session.execute(
                select(AegisMarathonDay)
                .where(AegisMarathonDay.track_id == track_id)
                .order_by(AegisMarathonDay.day_date.desc())
                .limit(1)
            )
            prev_row = res.scalar_one_or_none()
            prev_scan_id = prev_row.scan_id if prev_row else None
        else:
            res = await session.execute(
                select(AegisMarathonDay).where(
                    AegisMarathonDay.track_id == track_id,
                    AegisMarathonDay.scan_id == int(prev_scan_id),
                )
            )
            prev_row = res.scalar_one_or_none()

    # Extract previous continuity note for self-correction
    prev_continuity_note = None
    if prev_row and prev_row.continuity_note_json:
        prev_continuity_note = prev_row.continuity_note_json

    # Choose thinking level based on context
    effective_thinking_level = "high"  # Day 1 default: deep analysis
    if prev_row is not None:
        effective_thinking_level = "low"  # Routine day default
        # Check if previous predictions exist — will be compared after delta computation
        prev_predictions = (prev_continuity_note or {}).get("predictions") or []
        if prev_predictions:
            # Will upgrade to "medium" after delta computation if corrections needed
            effective_thinking_level = "low"

    _emit(
        "marathon.context_loaded",
        {
            "prev_scan_id": prev_scan_id,
            "has_previous_day": prev_row is not None,
            "thinking_level": effective_thinking_level,
        },
        status="completed",
    )

    return {
        "prev_scan_id": prev_scan_id,
        "prev_thought_signature": (
            getattr(prev_row, "thought_signature", None) if prev_row else None
        ),
        "prior_model_content_json": (
            getattr(prev_row, "stored_model_content_json", None) if prev_row else None
        ),
        "prev_continuity_note": prev_continuity_note,
        "effective_thinking_level": effective_thinking_level,
    }


# Node 2: resolve_scan — find or validate the scan to analyze
async def resolve_scan_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve target scan (manual/autonomous) and load assessment artifacts.

    Args:
        state: Graph state with mode, scan IDs, and track context.

    Returns:
        Dict[str, Any]: Loaded rollups/assessments for current and previous scans.

    Raises:
        RuntimeError: If required scan artifacts are missing.
        SQLAlchemyError: Can propagate DB query failures.

    Side Effects:
        Reads `AegisScan` and `StateIntelligence` rows; emits custom events.
    """
    scan_id = state.get("scan_id")
    mode = state.get("mode", "manual")
    prev_scan_id = state.get("prev_scan_id")

    async with get_async_session() as session:
        if scan_id is None and mode == "autonomous":
            # Find the latest completed scan
            res = await session.execute(
                select(AegisScan)
                .where(AegisScan.status == "completed")
                .order_by(AegisScan.id.desc())
                .limit(1)
            )
            scan = res.scalar_one_or_none()
            if not scan:
                raise RuntimeError("No completed scans found for autonomous mode")
            scan_id = scan.id
        elif scan_id is not None:
            res = await session.execute(
                select(AegisScan).where(AegisScan.id == int(scan_id))
            )
            scan = res.scalar_one_or_none()
        else:
            raise RuntimeError("scan_id required in manual mode")

        if not scan or not scan.rollup_json:
            raise RuntimeError(
                f"scan_id {scan_id} has no rollup_json (run synthesis first)"
            )

        # Load today's assessments
        intel_res = await session.execute(
            select(StateIntelligence).where(StateIntelligence.scan_id == scan_id)
        )
        assessments = []
        for row in intel_res.scalars().all():
            if row.assessment_json:
                assessments.append(row.assessment_json)

        # Load previous scan data
        prev_rollup = None
        prev_assessments: List[dict] = []
        if prev_scan_id:
            prev_scan_res = await session.execute(
                select(AegisScan).where(AegisScan.id == int(prev_scan_id))
            )
            prev_scan = prev_scan_res.scalar_one_or_none()
            prev_rollup = prev_scan.rollup_json if prev_scan else None

            prev_intel_res = await session.execute(
                select(StateIntelligence).where(
                    StateIntelligence.scan_id == int(prev_scan_id)
                )
            )
            for row in prev_intel_res.scalars().all():
                if row.assessment_json:
                    prev_assessments.append(row.assessment_json)

    _emit(
        "marathon.scan_resolved",
        {"scan_id": scan_id, "mode": mode, "assessments_count": len(assessments)},
        status="completed",
    )

    return {
        "scan_id": scan_id,
        "scan_rollup_json": scan.rollup_json,
        "scan_assessments": assessments,
        "prev_rollup_json": prev_rollup,
        "prev_assessments": prev_assessments,
    }


# Node 3: compute_deltas — deterministic diff (existing logic, enhanced)
async def compute_deltas_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compute deterministic delta payload and URI whitelist.

    Args:
        state: Graph state containing current/previous rollup and assessments.

    Returns:
        Dict[str, Any]: Delta object, URI whitelist, and adjusted thinking level.

    Raises:
        Does not raise intentionally for pure in-memory operations.

    Side Effects:
        Emits custom events.
    """
    scan_rollup = state.get("scan_rollup_json") or {}
    assessments = state.get("scan_assessments") or []
    prev_rollup = state.get("prev_rollup_json")
    prev_assessments = state.get("prev_assessments") or []

    delta_json = compute_delta(
        today_rollup=scan_rollup,
        today_assessments=assessments,
        prev_rollup=prev_rollup,
        prev_assessments=prev_assessments,
    )
    whitelist = uri_whitelist_from_artifacts(
        rollup=scan_rollup, assessments=assessments
    )

    # Upgrade thinking level if escalation detected or self-correction needed
    effective_level = state.get("effective_thinking_level") or "low"
    prev_note = state.get("prev_continuity_note") or {}
    prev_predictions = prev_note.get("predictions") or []

    if has_escalation(delta_json):
        effective_level = "medium"
    elif prev_predictions:
        # Check if any prediction was about stability but state escalated
        for ch in delta_json.get("state_changes") or []:
            prev_risk = ch.get("risk_level_prev", "UNKNOWN")
            today_risk = ch.get("risk_level_today", "UNKNOWN")
            if prev_risk != today_risk and prev_risk != "UNKNOWN":
                effective_level = "medium"
                break

    _emit(
        "marathon.delta_computed",
        {
            "scan_id": state.get("scan_id"),
            "uris": len(whitelist),
            "escalation_detected": has_escalation(delta_json),
            "effective_thinking_level": effective_level,
        },
        status="completed",
    )

    _emit("marathon.thinking_level_chosen", {"level": effective_level})

    return {
        "delta_json": delta_json,
        "uri_whitelist": whitelist,
        "effective_thinking_level": effective_level,
    }


# Node 4: generate_continuity_note — LLM with thought signature + self-correction
async def generate_continuity_note_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate continuity note and thought-signature artifacts via Gemini.

    Args:
        state: Graph state containing delta, URI whitelist, and replay context.

    Returns:
        Dict[str, Any]: Continuity note JSON + signature/model-content artifacts.

    Raises:
        Exception: Can propagate LLM generation/validation failures.

    Side Effects:
        Performs Gemini API call(s) and emits custom events.
    """
    _emit("marathon.llm_started", {"scan_id": state.get("scan_id")})

    note, signature, model_content_json, schema_mode = await generate_continuity_note(
        track_id=state["track_id"],
        day_date=state["day_date"],
        scan_id=int(state["scan_id"]),
        prev_scan_id=state.get("prev_scan_id"),
        delta_json=state.get("delta_json") or {},
        uri_whitelist=state.get("uri_whitelist") or [],
        prior_model_content_json=state.get("prior_model_content_json"),
        prev_continuity_note=state.get("prev_continuity_note"),
        effective_thinking_level=state.get("effective_thinking_level") or "low",
        config=state.get("config") or {},
    )

    # Check for self-corrections
    corrections = note.get("self_corrections") or []
    if corrections:
        _emit(
            "marathon.self_correction_detected",
            {"corrections_count": len(corrections), "corrections": corrections},
        )

    _emit(
        "marathon.llm_completed",
        {"scan_id": state.get("scan_id"), "schema_mode": schema_mode},
        status="completed",
    )

    return {
        "continuity_note_json": note,
        "thought_signature": signature,
        "stored_model_content_json": model_content_json,
        "schema_mode": schema_mode,
    }


# Node 5: decide_actions — deterministic rules for sub-agent dispatch
async def decide_actions_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Apply deterministic escalation rules to decide autonomous actions."""
    delta = state.get("delta_json") or {}
    actions: List[str] = []

    if has_escalation(delta):
        actions.append("enqueue_simulation")

    crits = critical_states(delta)
    if crits:
        actions.append("enqueue_report")

    _emit(
        "marathon.actions_decided",
        {
            "actions": actions,
            "critical_states": crits,
            "escalation": has_escalation(delta),
        },
        status="completed",
    )

    return {"actions_taken": actions}


# Node 6: enqueue_simulation — fire-and-forget sub-agent
async def enqueue_simulation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger simulation sub-agent when escalation rules require it.

    Side Effects:
        Creates job-store record and executes simulator DAG.
    """
    from app.aegis.simulator.runner import run_simulation_dag
    from app.utils.job_store import job_store

    delta = state.get("delta_json") or {}
    scan_id = int(state["scan_id"])
    scenario = build_auto_scenario(delta)
    simulation_id = f"SIM-{uuid.uuid4().hex[:8].upper()}"
    run_id = f"MSIM-{uuid.uuid4().hex[:8].upper()}"

    await job_store.create_job(
        run_id,
        "marathon_simulation",
        metadata={
            "simulation_id": simulation_id,
            "scan_id": scan_id,
            "scenario": scenario,
        },
    )

    # Fire-and-forget: don't block marathon graph on full simulation pipeline
    asyncio.create_task(
        run_simulation_dag(
            scan_id=scan_id,
            simulation_id=simulation_id,
            scenario=scenario,
            run_id=run_id,
            emit_job_events=True,
        )
    )

    _emit(
        "marathon.simulation_enqueued",
        {"simulation_id": simulation_id, "run_id": run_id, "scenario": scenario},
        status="completed",
    )

    return {"simulation_triggered": simulation_id}


# Node 7: enqueue_report — fire-and-forget sub-agent
async def enqueue_report_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger report sub-agent to package current high-risk scan outputs.

    Side Effects:
        Creates report job, runs report pipeline, updates job status/events.
    """
    from app.aegis.report.runner import run_report_dag
    from app.api.helpers.paths import REPORTS_DIR
    from app.utils.job_store import job_store

    scan_id = int(state["scan_id"])
    delta = state.get("delta_json") or {}
    crits = critical_states(delta)
    report_id = f"MRPT-{uuid.uuid4().hex[:8].upper()}"

    # Use all states from the delta, not just critical ones
    report_states = delta.get("states") or crits

    await job_store.create_job(
        report_id,
        "marathon_report",
        metadata={"scan_id": scan_id, "states": report_states},
    )

    sim_id = state.get("simulation_triggered")

    # Fire-and-forget: don't block marathon graph on full report pipeline
    async def _run_report() -> None:
        try:
            report_result = await run_report_dag(
                report_id=report_id,
                scan_id=scan_id,
                states=report_states,
                include_infographics=True,
                include_annexes=False,
                simulation_id=sim_id,
                output_dir=str(REPORTS_DIR),
                emit_job_events=True,
            )
            await job_store.update_job(
                report_id,
                status="completed",
                result=report_result,
                completed_at=_utcnow_naive(),
            )
        except Exception as exc:
            await job_store.update_job(
                report_id,
                status="failed",
                result={"error": str(exc)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                report_id,
                event_type="report_failed",
                status="failed",
                step="report_error",
                message=str(exc),
            )

    asyncio.create_task(_run_report())

    _emit(
        "marathon.report_enqueued",
        {"report_id": report_id, "states": report_states, "simulation_id": sim_id},
        status="completed",
    )

    return {"report_triggered": report_id}


# Node 8: persist — store everything + emit completion
async def persist_marathon_day_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Persist marathon-day artifacts and emit completion events."""
    track_id = state["track_id"]
    day_date = state["day_date"]
    scan_id = int(state["scan_id"])
    prev_scan_id = state.get("prev_scan_id")

    cfg = state.get("config") or {}
    model = str(cfg.get("model") or "")
    thinking_level = str(
        state.get("effective_thinking_level") or cfg.get("thinking_level") or ""
    )
    schema_mode = str(state.get("schema_mode") or "json_only")

    row_id = await upsert_marathon_day(
        track_id=track_id,
        day_date=day_date,
        scan_id=scan_id,
        prev_scan_id=int(prev_scan_id) if prev_scan_id else None,
        delta_json=state.get("delta_json") or {},
        continuity_note_json=state.get("continuity_note_json"),
        thought_signature=state.get("thought_signature"),
        prev_thought_signature=state.get("prev_thought_signature"),
        model=model,
        thinking_level=thinking_level,
        schema_mode=schema_mode,
        stored_model_content_json=state.get("stored_model_content_json"),
        actions_taken=state.get("actions_taken") or [],
        simulation_triggered=state.get("simulation_triggered"),
        report_triggered=state.get("report_triggered"),
    )

    _emit(
        "marathon.persisted",
        {"id": row_id, "track_id": track_id, "day_date": day_date},
        status="completed",
    )

    _emit(
        "marathon.completed",
        {
            "track_id": track_id,
            "day_date": day_date,
            "thinking_level": thinking_level,
            "actions_taken": state.get("actions_taken") or [],
            "simulation_triggered": state.get("simulation_triggered"),
            "report_triggered": state.get("report_triggered"),
        },
        status="completed",
    )

    return {}
