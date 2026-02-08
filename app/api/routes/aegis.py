"""AEGIS API endpoints for scans, synthesis, simulations, marathon, and reports.

Key responsibilities:
- Start and track asynchronous AEGIS pipeline jobs.
- Expose dashboard/timeline/status endpoints for UI consumption.
- Provide report download/listing endpoints backed by local storage or GCS.
- Translate internal job-store and DB data into stable API response schemas.

Used by:
- `app.main` through router inclusion.
- Frontend dashboards and operator tools that trigger AEGIS workflows.

Assumptions:
- API auth dependency protects all routes in this module.
- AEGIS workflow runners (`scan`, `synthesis`, `simulation`, `report`,
  `marathon`) are importable and configured.
- Database connectivity exists for status/timeline endpoints.
- GCS configuration may be present for report retrieval fallback.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.helpers.aegis_queries import priority_from_intel, summaries_for_scan
from app.api.helpers.paths import REPORTS_DIR
from app.api.helpers.runtime import spawn_bg_task, utcnow_naive
from app.api.helpers.security import require_api_auth
from app.api.schemas import (
    AegisDashboardResponse,
    AegisDemoRunRequest,
    AegisDemoRunResponse,
    AegisMarathonDayResponse,
    AegisMarathonRunRequest,
    AegisMarathonRunResponse,
    AegisMarathonTimelineResponse,
    AegisPipelineReadinessResponse,
    AegisReportRequest,
    AegisReportResponse,
    AegisReportStatusResponse,
    AegisScanRequest,
    AegisScanResponse,
    AegisScanStatusResponse,
    AegisSimulationRequest,
    AegisSimulationResponse,
    AegisSimulationStatusResponse,
    AegisSynthesisRequest,
    AegisSynthesisResponse,
    ContinuityChainEntry,
    JobStatus,
    ReportStatus,
    ScanStatus,
    StateIntelligenceSummary,
)
from app.config import AEGIS_FOCUS_STATES
from app.utils.job_store import JobRun, job_store

router = APIRouter(
    prefix="/api/aegis",
    tags=["AEGIS"],
    dependencies=[Depends(require_api_auth)],
)


def _normalize_states(states: list[str]) -> list[str]:
    """Return sorted unique normalized state labels."""
    out = []
    for s in states:
        ss = str(s or "").strip()
        if ss:
            out.append(ss)
    return sorted(set(out))


def _scan_period_key(days_back: int) -> str:
    """Build deterministic period key used for scan idempotency."""
    now = datetime.now(timezone.utc)
    if int(days_back) >= 7:
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return now.strftime("%Y-%m-%d")


def _period_day_date(days_back: int) -> str:
    """Return canonical day_date label used by marathon continuity."""
    now = datetime.now(timezone.utc)
    if int(days_back) >= 7:
        monday = now.date().fromordinal(now.date().toordinal() - now.weekday())
        return monday.isoformat()
    return now.strftime("%Y-%m-%d")


async def _next_marathon_day_date_for_demo(
    *,
    track_id: str,
    base_day_date: str,
    days_back: int,
) -> str:
    """Return next day_date for demo continuity appends on same track.

    Demo runs should show visible timeline growth (Day 1/2/3...) even when
    executed repeatedly within the same week window.
    """
    from datetime import date, timedelta

    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisMarathonDay
    from sqlalchemy import desc, select

    step_days = 7 if int(days_back) >= 7 else 1
    try:
        base = date.fromisoformat(str(base_day_date))
    except Exception:
        return str(base_day_date)

    async with get_async_session() as session:
        res = await session.execute(
            select(AegisMarathonDay.day_date)
            .where(AegisMarathonDay.track_id == str(track_id))
            .order_by(desc(AegisMarathonDay.day_date))
            .limit(1)
        )
        latest = res.scalar_one_or_none()

    if not latest:
        return base.isoformat()

    try:
        latest_date = date.fromisoformat(str(latest))
    except Exception:
        return base.isoformat()

    if latest_date >= base:
        return (latest_date + timedelta(days=step_days)).isoformat()
    return base.isoformat()


async def _scan_readiness_snapshot(scan_id: int) -> dict[str, Any]:
    """Compute readiness booleans for a scan-backed pipeline cycle."""
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan, StateIntelligence
    from sqlalchemy import select

    missing: list[str] = []
    scan_exists = False
    scan_status = "unknown"
    has_rollup_json = False
    assessments_count = 0

    async with get_async_session() as session:
        scan = await session.get(AegisScan, int(scan_id))
        if scan:
            scan_exists = True
            scan_status = str(scan.status or "unknown").lower()
            has_rollup_json = bool(scan.rollup_json)

            res = await session.execute(
                select(StateIntelligence).where(StateIntelligence.scan_id == int(scan_id))
            )
            assessments = [
                row
                for row in res.scalars().all()
                if getattr(row, "assessment_json", None)
            ]
            assessments_count = len(assessments)

    if not scan_exists:
        missing.append("scan_not_found")
    else:
        if scan_status != "completed":
            missing.append("scan_not_completed")
        if not has_rollup_json:
            missing.append("missing_rollup_json")
        if assessments_count <= 0:
            missing.append("missing_state_assessments")

    synthesis_ready = scan_exists and not missing
    return {
        "scan_id": int(scan_id),
        "scan_exists": scan_exists,
        "scan_status": scan_status,
        "has_rollup_json": has_rollup_json,
        "assessments_count": assessments_count,
        "synthesis_ready": synthesis_ready,
        "simulation_ready": synthesis_ready,
        "report_ready": synthesis_ready,
        "marathon_ready": synthesis_ready,
        "missing_requirements": missing,
    }


async def _find_reusable_scan(
    *,
    states: list[str],
    days_back: int,
    period_key: str,
) -> Optional[dict[str, Any]]:
    """Find a completed scan job in the same period and state set."""
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan, StateIntelligence
    from sqlalchemy import desc, select

    target_states = _normalize_states(states)
    async with get_async_session() as session:
        res = await session.execute(
            select(JobRun)
            .where(
                JobRun.job_type == "aegis_scan",
                JobRun.status == "completed",
            )
            .order_by(desc(JobRun.started_at))
            .limit(40)
        )
        rows = res.scalars().all()

        for row in rows:
            metadata = row.job_metadata or {}
            row_days_back = int(metadata.get("days_back") or 0)
            row_period_key = str(metadata.get("period_key") or "")
            row_states = _normalize_states(metadata.get("states") or [])

            if row_days_back != int(days_back):
                continue
            if row_period_key != str(period_key):
                continue
            if row_states != target_states:
                continue

            result = row.result or {}
            candidate_scan_id = int(result.get("scan_id") or metadata.get("scan_id") or 0)
            if candidate_scan_id <= 0:
                continue

            scan = await session.get(AegisScan, candidate_scan_id)
            if not scan or str(scan.status or "").lower() != "completed":
                continue

            st_res = await session.execute(
                select(StateIntelligence.state_name).where(
                    StateIntelligence.scan_id == candidate_scan_id
                )
            )
            found_states = _normalize_states(
                [r[0] for r in st_res.all() if r and r[0]]
            )
            if found_states and found_states != target_states:
                continue

            return {
                "scan_id": int(candidate_scan_id),
                "run_id": str(row.job_id),
                "period_key": str(period_key),
            }

    return None


@router.post("/seed-demo")
async def seed_marathon_demo() -> dict[str, Any]:
    """One-time endpoint to seed marathon demo data into the database."""
    from scripts.seed_marathon_demo import seed
    try:
        await seed()
        return {"status": "ok", "message": "Marathon demo data seeded successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard", response_model=AegisDashboardResponse)
async def get_aegis_dashboard() -> AegisDashboardResponse:
    """Return high-level AEGIS dashboard aggregates.

    Request:
        No body, query, or path parameters.

    Response:
        `AegisDashboardResponse` containing latest scan summary, scan/report
        counters, focus states, and state-level priority summaries.

    Status Codes:
        200: Dashboard payload returned. Internal read failures are degraded to
            empty/fallback values instead of non-200 errors.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read-only endpoint.

    Args:
        None.

    Returns:
        AegisDashboardResponse: Snapshot of dashboard metrics.

    Raises:
        Does not raise intentionally; exceptions are converted into fallback
        responses.

    Side Effects:
        Performs database reads and local filesystem glob on `reports/*.pdf`.

    Latency:
        DB-backed reads can be slow for large scan history tables.
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan, StateIntelligence
    from sqlalchemy import select, desc

    try:
        async with get_async_session() as session:
            latest_scan_result = await session.execute(
                select(AegisScan).order_by(desc(AegisScan.started_at)).limit(1)
            )
            latest_scan = latest_scan_result.scalar_one_or_none()

            scan_count_result = await session.execute(select(AegisScan))
            total_scans = len(scan_count_result.scalars().all())

            state_summaries = []
            if latest_scan:
                state_intel_result = await session.execute(
                    select(StateIntelligence).where(
                        StateIntelligence.scan_id == latest_scan.id
                    )
                )
                for intel in state_intel_result.scalars().all():
                    level, score = priority_from_intel(intel)
                    state_summaries.append(
                        StateIntelligenceSummary(
                            state_name=intel.state_name,
                            conflict_events=intel.conflict_events_count,
                            idp_estimate=intel.idp_estimate,
                            idp_trend=intel.idp_trend,
                            food_insecurity_level=intel.food_insecurity_level,
                            ipc_phase=intel.ipc_phase,
                            markets_operational=intel.markets_operational,
                            priority_level=level,
                            priority_score=score,
                        )
                    )

            latest_scan_response = None
            if latest_scan:
                latest_scan_response = AegisScanStatusResponse(
                    scan_id=latest_scan.id,
                    run_id=latest_scan.run_id,
                    status=ScanStatus(latest_scan.status),
                    started_at=latest_scan.started_at,
                    completed_at=latest_scan.completed_at,
                    states_scanned=latest_scan.states_scanned,
                    total_events=latest_scan.total_events,
                    total_fatalities=latest_scan.total_fatalities,
                )

            return AegisDashboardResponse(
                latest_scan=latest_scan_response,
                total_scans=total_scans,
                total_reports=len(list(REPORTS_DIR.glob("*.pdf"))),
                focus_states=AEGIS_FOCUS_STATES,
                state_summaries=state_summaries,
                recent_alerts=[],
            )
    except Exception:
        return AegisDashboardResponse(
            latest_scan=None,
            total_scans=0,
            total_reports=len(list(REPORTS_DIR.glob("*.pdf"))),
            focus_states=AEGIS_FOCUS_STATES,
            state_summaries=[],
            recent_alerts=[],
        )


@router.post("/scan", response_model=AegisScanResponse)
async def trigger_aegis_scan(
    payload: AegisScanRequest, request: Request
) -> AegisScanResponse:
    """Start an asynchronous AEGIS scan job.

    Request:
        JSON body (`AegisScanRequest`):
        - `states` (optional list[str]): defaults to configured focus states.
        - `days_back` (int): lookback window (1-365).
        - `force_refresh` (bool): bypass recent-data caching when true.

    Response:
        `AegisScanResponse` with job/run identifiers and immediate `running`
        status.

    Status Codes:
        200: Scan accepted and background execution scheduled.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each request creates a new scan run/job.

    Args:
        payload: Scan configuration parameters from request body.
        request: FastAPI request object used for background task scheduling.

    Returns:
        AegisScanResponse: Accepted scan run contract for polling.

    Raises:
        Does not raise intentionally for scan-start failures in this handler;
        background failures are written to job events/status.

    Side Effects:
        Writes scan/job records to DB and job store.
        Spawns background scan workflow (`run_aegis_scan`).

    Latency:
        Fast request path; heavy scan logic runs asynchronously and may involve
        geospatial and intelligence data processing.
    """
    from app.aegis.graph import run_aegis_scan

    states = payload.states or AEGIS_FOCUS_STATES
    days_back = getattr(payload, "days_back", 7) or 7
    period_key = _scan_period_key(int(days_back))
    normalized_states = _normalize_states(states)

    if not bool(payload.force_refresh):
        reusable = await _find_reusable_scan(
            states=normalized_states,
            days_back=int(days_back),
            period_key=period_key,
        )
        if reusable:
            return AegisScanResponse(
                scan_id=int(reusable["scan_id"]),
                run_id=str(reusable["run_id"]),
                status="completed",
                states_to_scan=normalized_states,
                message=(
                    f"Reusing completed scan for period {period_key}. "
                    "Use force_refresh=true to create a fresh scan."
                ),
            )

    run_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"
    scan_db_id = 0
    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisScan

        async with get_async_session() as session:
            scan = AegisScan(
                run_id=run_id,
                started_at=utcnow_naive(),
                status="running",
                states_scanned=0,
                total_events=0,
                total_fatalities=0,
            )
            session.add(scan)
            await session.flush()
            scan_db_id = scan.id
    except Exception:
        scan_db_id = 0

    await job_store.create_job(
        run_id,
        "aegis_scan",
        metadata={
            "states": normalized_states,
            "scan_id": scan_db_id,
            "days_back": int(days_back),
            "period_key": period_key,
        },
    )
    await job_store.add_event(
        run_id,
        event_type="scan_started",
        status="running",
        step="scan_start",
        message="AEGIS scan started",
        payload={"states": normalized_states, "period_key": period_key},
    )

    async def run_scan_background() -> None:
        """Execute scan workflow in background and persist terminal state.

        Args:
            None.

        Returns:
            None.

        Raises:
            Does not raise intentionally; errors are captured in job status/events.

        Side Effects:
            Calls scan DAG and updates `job_store` status/event rows.

        Latency:
            Potentially high due to scan collection and analysis work.
        """
        try:
            result = await run_aegis_scan(
                states=normalized_states,
                days_back=days_back,
                force=payload.force_refresh,
                run_id=run_id,
                scan_id=scan_db_id,
            )
            if (result.get("status") or "").lower() == "failed":
                err = result.get("error") or "AEGIS scan failed"
                await job_store.update_job(
                    run_id,
                    status="failed",
                    result=result,
                    completed_at=utcnow_naive(),
                )
                await job_store.add_event(
                    run_id,
                    event_type="scan_failed",
                    status="failed",
                    step="scan_error",
                    message=str(err),
                    payload={"scan_id": scan_db_id},
                )
                return
            await job_store.update_job(
                run_id,
                status="completed",
                result=result,
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="scan_completed",
                status="completed",
                step="scan_complete",
                message="AEGIS scan completed",
                payload={"scan_id": scan_db_id},
            )
        except Exception as e:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e), "scan_id": scan_db_id},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="scan_failed",
                status="failed",
                step="scan_error",
                message=str(e),
                payload={"scan_id": scan_db_id},
            )

    # run in background (captures job events inside scan engine)
    spawn_bg_task(request.app, run_scan_background())

    return AegisScanResponse(
        scan_id=scan_db_id,
        run_id=run_id,
        status="running",
        states_to_scan=normalized_states,
        message=f"Scan initiated. Poll /api/aegis/scan/{run_id} for status.",
    )


@router.post("/synthesis", response_model=AegisSynthesisResponse)
async def trigger_aegis_synthesis(
    payload: AegisSynthesisRequest, request: Request
) -> AegisSynthesisResponse:
    """Start asynchronous synthesis for an existing scan.

    Request:
        JSON body (`AegisSynthesisRequest`):
        - `scan_id` (int): scan to synthesize.
        - `states` (optional list[str]): defaults to configured focus states.

    Response:
        `AegisSynthesisResponse` with synthesis run ID and `running` status.

    Status Codes:
        200: Synthesis job accepted and queued.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each call creates a new synthesis run.

    Args:
        payload: Synthesis request parameters.
        request: FastAPI request object used for background task scheduling.

    Returns:
        AegisSynthesisResponse: Run metadata for job/event polling.

    Raises:
        Does not raise intentionally during request handling in normal flow.

    Side Effects:
        Writes job metadata/events and spawns background synthesis DAG execution.

    Latency:
        Fast request path; synthesis DAG can be slow due to LLM and data fusion
        steps.
    """
    from app.aegis.synthesis.runner import run_synthesis_dag

    scan_id = int(payload.scan_id)
    states = payload.states or AEGIS_FOCUS_STATES
    run_id = f"SYNTH-{uuid.uuid4().hex[:8].upper()}"

    await job_store.create_job(
        run_id,
        "aegis_synthesis",
        metadata={"scan_id": scan_id, "states": states},
    )
    await job_store.add_event(
        run_id,
        "synthesis_started",
        status="running",
        step="synthesis_start",
        message="AEGIS synthesis started",
        payload={"scan_id": scan_id, "states": states},
    )

    async def run_synthesis_background() -> None:
        """Run synthesis DAG and normalize completion/failure job updates.

        Args:
            None.

        Returns:
            None.

        Raises:
            Does not raise intentionally; exceptions are persisted as job failure.

        Side Effects:
            Calls synthesis DAG and writes job status/event updates.

        Latency:
            Potentially high due to model-driven synthesis steps.
        """
        try:
            result = await run_synthesis_dag(
                scan_id=scan_id,
                states=states,
                run_id=run_id,
                emit_job_events=True,
            )
            await job_store.update_job(
                run_id,
                status="completed",
                result=result,
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="synthesis_completed",
                status="completed",
                step="synthesis_complete",
                message="AEGIS synthesis completed",
                payload={"scan_id": scan_id},
            )
        except Exception as e:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e), "scan_id": scan_id},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="synthesis_failed",
                status="failed",
                step="synthesis_error",
                message=str(e),
                payload={"scan_id": scan_id},
            )

    spawn_bg_task(request.app, run_synthesis_background())
    return AegisSynthesisResponse(
        run_id=run_id,
        status="running",
        message=f"Synthesis initiated. Poll /api/jobs/{run_id}/events for status.",
    )


@router.get(
    "/pipeline/readiness/{scan_id}", response_model=AegisPipelineReadinessResponse
)
async def get_pipeline_readiness(scan_id: int) -> AegisPipelineReadinessResponse:
    """Return stage-readiness booleans for a scan-backed pipeline cycle.

    Request:
        Path parameter `scan_id` is required.

    Response:
        `AegisPipelineReadinessResponse` indicating whether synthesis-backed
        stages (simulation/report/marathon) can run safely.

    Status Codes:
        200: Readiness returned (including scan-not-found cases).

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        scan_id: Numeric scan identifier.

    Returns:
        AegisPipelineReadinessResponse: Readiness snapshot.
    """
    snap = await _scan_readiness_snapshot(int(scan_id))
    return AegisPipelineReadinessResponse(**snap)


@router.post("/demo/run", response_model=AegisDemoRunResponse)
async def run_demo_orchestrator(
    payload: AegisDemoRunRequest, request: Request
) -> AegisDemoRunResponse:
    """Run a one-click end-to-end demo cycle with a single parent job stream.

    Flow:
        seed-if-needed -> bootstrap baseline continuity -> scan(reuse-or-run) ->
        synthesis(check-or-run) -> marathon append -> timeline snapshot.
    """
    from app.aegis.graph import run_aegis_scan
    from app.aegis.marathon.runner import run_marathon_day
    from app.aegis.synthesis.runner import run_synthesis_dag

    states = _normalize_states(payload.states or AEGIS_FOCUS_STATES)
    days_back = int(payload.days_back or 7)
    period_key = _scan_period_key(days_back)
    base_day_date = _period_day_date(days_back)
    track_id = str(payload.track_id or "demo-track")
    run_id = f"DEMO-{uuid.uuid4().hex[:8].upper()}"

    await job_store.create_job(
        run_id,
        "aegis_demo",
        metadata={
            "states": states,
            "days_back": days_back,
            "period_key": period_key,
            "track_id": track_id,
        },
    )
    await job_store.add_event(
        run_id,
        event_type="demo_started",
        status="running",
        step="demo_start",
        message="AEGIS one-click demo orchestration started",
        payload={
            "states": states,
            "days_back": days_back,
            "period_key": period_key,
            "track_id": track_id,
        },
    )

    async def _bg() -> None:
        from sqlalchemy import select

        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisMarathonDay, AegisScan

        scan_id = 0
        scan_run_id = ""
        simulation_id = ""
        report_id = ""
        marathon_run_id = ""
        day_date = str(base_day_date)
        seeded_weeks = [
            ("2026-01-19", "SCAN-DEMO-W1"),
            ("2026-01-26", "SCAN-DEMO-W2"),
            ("2026-02-02", "SCAN-DEMO-W3"),
        ]

        try:
            # 1) Seed demo baseline if missing
            async with get_async_session() as session:
                seeded = await session.execute(
                    select(AegisScan.run_id).where(
                        AegisScan.run_id.in_(
                            ["SCAN-DEMO-W1", "SCAN-DEMO-W2", "SCAN-DEMO-W3"]
                        )
                    )
                )
                found = {str(r[0]) for r in seeded.all() if r and r[0]}
            if len(found) < 3:
                await job_store.add_event(
                    run_id,
                    event_type="demo.seed_started",
                    status="running",
                    step="seed",
                    message="Seeding baseline demo scans",
                )
                from scripts.seed_marathon_demo import seed

                await seed()
                await job_store.add_event(
                    run_id,
                    event_type="demo.seed_completed",
                    status="completed",
                    step="seed",
                    message="Baseline demo scans seeded",
                )
            else:
                await job_store.add_event(
                    run_id,
                    event_type="demo.seed_skipped",
                    status="completed",
                    step="seed",
                    message="Baseline demo scans already present",
                )

            # 2) Ensure seeded baseline continuity exists on the selected track
            async with get_async_session() as session:
                res = await session.execute(
                    select(AegisMarathonDay.day_date).where(
                        AegisMarathonDay.track_id == track_id,
                        AegisMarathonDay.day_date.in_([d for d, _ in seeded_weeks]),
                    )
                )
                present_seed_days = {str(r[0]) for r in res.all() if r and r[0]}

            if len(present_seed_days) < len(seeded_weeks):
                missing_seed_days = [
                    d for d, _ in seeded_weeks if d not in present_seed_days
                ]
                await job_store.add_event(
                    run_id,
                    event_type="demo.baseline_marathon_bootstrap_started",
                    status="running",
                    step="marathon_baseline",
                    message="Bootstrapping seeded continuity weeks on demo track",
                    payload={
                        "track_id": track_id,
                        "missing_seed_weeks": missing_seed_days,
                    },
                )
                async with get_async_session() as session:
                    seeded_scans_res = await session.execute(
                        select(AegisScan).where(
                            AegisScan.run_id.in_([rid for _, rid in seeded_weeks])
                        )
                    )
                    seeded_scans = seeded_scans_res.scalars().all()
                by_run_id = {str(s.run_id): s for s in seeded_scans}
                missing_seed_scans = [
                    rid for _, rid in seeded_weeks if rid not in by_run_id
                ]
                if missing_seed_scans:
                    raise RuntimeError(
                        "Missing seeded scans required for baseline continuity: "
                        + ", ".join(missing_seed_scans)
                    )

                for idx, (seed_day_date, seed_run_id) in enumerate(seeded_weeks):
                    if seed_day_date in present_seed_days:
                        continue
                    seed_scan = by_run_id[str(seed_run_id)]
                    seed_run = f"MARA-SEED-{uuid.uuid4().hex[:8].upper()}"
                    prev_seed_scan_id: Optional[int] = None
                    if idx > 0:
                        prev_seed_scan_id = int(
                            by_run_id[str(seeded_weeks[idx - 1][1])].id
                        )
                    await job_store.create_job(
                        seed_run,
                        "aegis_marathon",
                        metadata={
                            "track_id": track_id,
                            "scan_id": int(seed_scan.id),
                            "day_date": seed_day_date,
                            "seeded": True,
                        },
                    )
                    await run_marathon_day(
                        run_id=seed_run,
                        track_id=track_id,
                        day_date=seed_day_date,
                        scan_id=int(seed_scan.id),
                        prev_scan_id=prev_seed_scan_id,
                        mode="manual",
                        config={
                            "model": os.getenv(
                                "GEMINI_MODEL_MARATHON", "gemini-3-flash-preview"
                            ),
                            "disable_actions": True,
                        },
                        emit_job_events=False,
                    )

                await job_store.add_event(
                    run_id,
                    event_type="demo.baseline_marathon_bootstrap_completed",
                    status="completed",
                    step="marathon_baseline",
                    message="Seeded continuity weeks are now available on the demo track",
                    payload={"track_id": track_id},
                )
            else:
                await job_store.add_event(
                    run_id,
                    event_type="demo.baseline_marathon_bootstrap_skipped",
                    status="completed",
                    step="marathon_baseline",
                    message="Seeded continuity weeks already present on this demo track",
                    payload={"track_id": track_id},
                )

            # 3) Scan (reuse unless forced)
            reusable = None
            if not bool(payload.force_refresh):
                reusable = await _find_reusable_scan(
                    states=states,
                    days_back=days_back,
                    period_key=period_key,
                )
            if reusable:
                scan_id = int(reusable["scan_id"])
                scan_run_id = str(reusable["run_id"])
                await job_store.add_event(
                    run_id,
                    event_type="demo.scan_reused",
                    status="completed",
                    step="scan",
                    message=f"Reused completed scan {scan_id} for period {period_key}",
                    payload={"scan_id": scan_id, "scan_run_id": scan_run_id},
                )
            else:
                scan_run_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"
                scan_db_id = 0
                async with get_async_session() as session:
                    scan = AegisScan(
                        run_id=scan_run_id,
                        started_at=utcnow_naive(),
                        status="running",
                        states_scanned=0,
                        total_events=0,
                        total_fatalities=0,
                    )
                    session.add(scan)
                    await session.flush()
                    scan_db_id = int(scan.id)

                await job_store.create_job(
                    scan_run_id,
                    "aegis_scan",
                    metadata={
                        "states": states,
                        "scan_id": scan_db_id,
                        "days_back": days_back,
                        "period_key": period_key,
                    },
                )
                await job_store.add_event(
                    run_id,
                    event_type="demo.scan_started",
                    status="running",
                    step="scan",
                    message=f"Running fresh scan {scan_run_id}",
                    payload={"scan_id": scan_db_id},
                )
                scan_result = await run_aegis_scan(
                    states=states,
                    days_back=days_back,
                    force=bool(payload.force_refresh),
                    run_id=scan_run_id,
                    scan_id=scan_db_id,
                )
                if (scan_result.get("status") or "").lower() == "failed":
                    raise RuntimeError(str(scan_result.get("error") or "scan_failed"))
                await job_store.update_job(
                    scan_run_id,
                    status="completed",
                    result=scan_result,
                    completed_at=utcnow_naive(),
                )
                scan_id = int(scan_result.get("scan_id") or scan_db_id or 0)
                await job_store.add_event(
                    run_id,
                    event_type="demo.scan_completed",
                    status="completed",
                    step="scan",
                    message=f"Scan completed (scan_id={scan_id})",
                    payload={"scan_id": scan_id, "scan_run_id": scan_run_id},
                )

            if scan_id <= 0:
                raise RuntimeError("Could not resolve scan_id for demo run")

            # 4) Synthesis (skip when already ready)
            readiness = await _scan_readiness_snapshot(scan_id)
            if bool(readiness.get("synthesis_ready")):
                await job_store.add_event(
                    run_id,
                    event_type="demo.synthesis_skipped",
                    status="completed",
                    step="synthesis",
                    message="Synthesis artifacts already present",
                    payload={"scan_id": scan_id},
                )
            else:
                synth_run_id = f"SYNTH-{uuid.uuid4().hex[:8].upper()}"
                await job_store.create_job(
                    synth_run_id,
                    "aegis_synthesis",
                    metadata={"scan_id": scan_id, "states": states},
                )
                await job_store.add_event(
                    run_id,
                    event_type="demo.synthesis_started",
                    status="running",
                    step="synthesis",
                    payload={"scan_id": scan_id},
                )
                synth_result = await run_synthesis_dag(
                    scan_id=int(scan_id),
                    states=states,
                    run_id=synth_run_id,
                    emit_job_events=False,
                )
                await job_store.update_job(
                    synth_run_id,
                    status="completed",
                    result=synth_result,
                    completed_at=utcnow_naive(),
                )
                readiness = await _scan_readiness_snapshot(scan_id)
                if not bool(readiness.get("synthesis_ready")):
                    raise RuntimeError(
                        "Synthesis did not produce required rollup/assessments"
                    )
                await job_store.add_event(
                    run_id,
                    event_type="demo.synthesis_completed",
                    status="completed",
                    step="synthesis",
                    payload={"scan_id": scan_id},
                )

            # 5) Marathon append (agent decides downstream simulation/report actions)
            latest_day_row = None
            async with get_async_session() as session:
                latest_res = await session.execute(
                    select(AegisMarathonDay)
                    .where(AegisMarathonDay.track_id == track_id)
                    .order_by(AegisMarathonDay.day_date.desc())
                    .limit(1)
                )
                latest_day_row = latest_res.scalar_one_or_none()

            if latest_day_row and int(latest_day_row.scan_id) == int(scan_id):
                day_date = str(latest_day_row.day_date)
                simulation_id = str(latest_day_row.simulation_triggered or "")
                report_id = str(latest_day_row.report_triggered or "")
                marathon_result = {
                    "actions_taken": latest_day_row.actions_taken or [],
                    "simulation_triggered": simulation_id or None,
                    "report_triggered": report_id or None,
                }
                await job_store.add_event(
                    run_id,
                    event_type="demo.marathon_skipped_same_scan",
                    status="completed",
                    step="marathon",
                    message=(
                        f"Latest marathon entry already uses scan {scan_id}; "
                        "continuity unchanged for this period."
                    ),
                    payload={
                        "track_id": track_id,
                        "scan_id": scan_id,
                        "day_date": day_date,
                    },
                )
            else:
                day_date = await _next_marathon_day_date_for_demo(
                    track_id=track_id,
                    base_day_date=base_day_date,
                    days_back=days_back,
                )
                marathon_run_id = f"MARA-{uuid.uuid4().hex[:8].upper()}"
                await job_store.create_job(
                    marathon_run_id,
                    "aegis_marathon",
                    metadata={
                        "track_id": track_id,
                        "scan_id": scan_id,
                        "day_date": day_date,
                    },
                )
                await job_store.add_event(
                    run_id,
                    event_type="demo.marathon_started",
                    status="running",
                    step="marathon",
                    payload={
                        "track_id": track_id,
                        "scan_id": scan_id,
                        "day_date": day_date,
                    },
                )
                marathon_result = await run_marathon_day(
                    run_id=marathon_run_id,
                    track_id=track_id,
                    day_date=day_date,
                    scan_id=int(scan_id),
                    prev_scan_id=None,
                    mode="manual",
                    config={
                        "model": os.getenv(
                            "GEMINI_MODEL_MARATHON", "gemini-3-flash-preview"
                        ),
                    },
                    emit_job_events=False,
                )
                simulation_id = str(marathon_result.get("simulation_triggered") or "")
                report_id = str(marathon_result.get("report_triggered") or "")
                await job_store.add_event(
                    run_id,
                    event_type="demo.marathon_completed",
                    status="completed",
                    step="marathon",
                    payload={
                        "track_id": track_id,
                        "scan_id": scan_id,
                        "day_date": day_date,
                        "actions_taken": marathon_result.get("actions_taken") or [],
                        "simulation_triggered": simulation_id or None,
                        "report_triggered": report_id or None,
                    },
                )

            timeline = await get_marathon_timeline(track_id)
            result_payload = {
                "status": "completed",
                "track_id": track_id,
                "period_key": period_key,
                "day_date": day_date,
                "scan_id": scan_id,
                "scan_run_id": scan_run_id,
                "simulation_id": simulation_id or None,
                "report_id": report_id or None,
                "marathon_run_id": marathon_run_id,
                "marathon_actions": marathon_result.get("actions_taken") or [],
                "timeline": timeline.model_dump(),
            }
            await job_store.update_job(
                run_id,
                status="completed",
                result=result_payload,
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="demo_completed",
                status="completed",
                step="demo_complete",
                message="AEGIS one-click demo completed successfully",
                payload={
                    "scan_id": scan_id,
                    "report_id": report_id,
                    "track_id": track_id,
                    "timeline_days": timeline.total_days,
                },
            )
        except Exception as exc:
            await job_store.update_job(
                run_id,
                status="failed",
                result={
                    "error": str(exc),
                    "track_id": track_id,
                    "period_key": period_key,
                    "scan_id": scan_id or None,
                    "report_id": report_id or None,
                    "marathon_run_id": marathon_run_id or None,
                },
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="demo_failed",
                status="failed",
                step="demo_error",
                message=str(exc),
            )

    spawn_bg_task(request.app, _bg())
    return AegisDemoRunResponse(
        run_id=run_id,
        status=JobStatus.RUNNING,
        track_id=track_id,
        period_key=period_key,
        message="Demo orchestrator started. Poll /api/jobs/{run_id} for progress.",
    )


@router.post("/marathon/run", response_model=AegisMarathonRunResponse)
async def run_marathon_day_endpoint(
    payload: AegisMarathonRunRequest, request: Request
) -> AegisMarathonRunResponse:
    """Start one AEGIS marathon continuity run.

    Request:
        JSON body (`AegisMarathonRunRequest`):
        - `track_id` (str): continuity track identifier.
        - `scan_id` (optional int): required in manual mode.
        - `day_date` (optional YYYY-MM-DD): defaults to current UTC date.
        - `prev_scan_id` (optional int): reference scan for delta calculations.
        - `mode` (`manual` or `autonomous`).

    Response:
        `AegisMarathonRunResponse` with run metadata and `running` status.

    Status Codes:
        200: Run accepted and queued.
        422: Manual mode requested without `scan_id`.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each call creates a new marathon run.

    Args:
        payload: Marathon run configuration.
        request: FastAPI request object used to schedule background work.

    Returns:
        AegisMarathonRunResponse: Immediate run contract for tracking.

    Raises:
        HTTPException: 422 when manual-mode preconditions are not satisfied.

    Side Effects:
        Writes job metadata and spawns background marathon execution.

    Latency:
        Fast request path; background marathon execution may include LLM-heavy
        reasoning and downstream workflow calls.
    """
    from app.aegis.marathon.runner import run_marathon_day

    mode = payload.mode or "manual"

    # In manual mode, scan_id is required
    if mode == "manual" and payload.scan_id is None:
        raise HTTPException(status_code=422, detail="scan_id is required for manual mode")
    if payload.scan_id is not None:
        readiness = await _scan_readiness_snapshot(int(payload.scan_id))
        if not bool(readiness.get("marathon_ready")):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Marathon prerequisites not met for scan_id "
                    f"{int(payload.scan_id)}: "
                    + ", ".join(readiness.get("missing_requirements") or [])
                ),
            )
    if payload.prev_scan_id is not None:
        prev_readiness = await _scan_readiness_snapshot(int(payload.prev_scan_id))
        if not bool(prev_readiness.get("marathon_ready")):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Marathon prerequisites not met for prev_scan_id "
                    f"{int(payload.prev_scan_id)}: "
                    + ", ".join(prev_readiness.get("missing_requirements") or [])
                ),
            )

    run_id = f"MARA-{uuid.uuid4().hex[:8].upper()}"
    day_date = payload.day_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    await job_store.create_job(
        run_id,
        "aegis_marathon",
        metadata={
            "track_id": payload.track_id,
            "day_date": day_date,
            "scan_id": int(payload.scan_id) if payload.scan_id else None,
            "prev_scan_id": int(payload.prev_scan_id) if payload.prev_scan_id else None,
            "mode": mode,
        },
    )

    async def _bg() -> None:
        """Execute marathon-day background run with configured LLM settings.

        Args:
            None.

        Returns:
            None.

        Raises:
            Exceptions from `run_marathon_day` may propagate to task logs.

        Side Effects:
            Calls marathon runner which writes DB/job artifacts and may trigger
            downstream simulation/report actions.

        Latency:
            Potentially high for LLM-heavy continuity analysis.
        """
        try:
            await run_marathon_day(
                run_id=run_id,
                track_id=payload.track_id,
                day_date=day_date,
                scan_id=int(payload.scan_id) if payload.scan_id else None,
                prev_scan_id=int(payload.prev_scan_id) if payload.prev_scan_id else None,
                mode=mode,
                config={
                    "model": os.getenv("GEMINI_MODEL_MARATHON", "gemini-3-flash-preview"),
                },
                emit_job_events=True,
            )
        except Exception as exc:
            # Guard against dangling "running" marathon jobs if runner fails before finalize.
            try:
                await job_store.update_job(
                    run_id,
                    status="failed",
                    result={
                        "error": str(exc),
                        "track_id": payload.track_id,
                        "day_date": day_date,
                    },
                    completed_at=utcnow_naive(),
                )
            except Exception:
                pass
            try:
                await job_store.add_event(
                    run_id,
                    event_type="marathon_failed",
                    status="failed",
                    step="marathon_error",
                    message=str(exc),
                )
            except Exception:
                pass

    spawn_bg_task(request.app, _bg())
    return AegisMarathonRunResponse(
        run_id=run_id,
        status=JobStatus.RUNNING,
        track_id=payload.track_id,
        scan_id=int(payload.scan_id) if payload.scan_id else None,
        day_date=day_date,
        mode=mode,
    )


@router.get("/marathon/{track_id}/timeline", response_model=AegisMarathonTimelineResponse)
async def get_marathon_timeline(track_id: str) -> AegisMarathonTimelineResponse:
    """Return chronological continuity data for a marathon track.

    Request:
        Path parameter `track_id` identifies a marathon continuity chain.

    Response:
        `AegisMarathonTimelineResponse` containing per-day records plus a
        derived continuity chain summary.

    Status Codes:
        200: Timeline returned (possibly empty).

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        track_id: Continuity track identifier.

    Returns:
        AegisMarathonTimelineResponse: Ordered timeline and continuity metrics.

    Raises:
        SQLAlchemyError: Can propagate on DB query failure.

    Side Effects:
        Performs database reads only.

    Latency:
        Depends on number of marathon-day rows for the track.
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisMarathonDay
    from sqlalchemy import select

    async with get_async_session() as session:
        res = await session.execute(
            select(AegisMarathonDay)
            .where(AegisMarathonDay.track_id == track_id)
            .order_by(AegisMarathonDay.day_date)
        )
        rows = res.scalars().all()

    def _short(s: Optional[str]) -> Optional[str]:
        """Shorten thought signatures for compact timeline rendering.

        Args:
            s: Full thought-signature string or `None`.

        Returns:
            Optional[str]: Original or abbreviated signature.

        Raises:
            Does not raise intentionally.

        Side Effects:
            None.

        Latency:
            Constant-time string processing.
        """
        if not s:
            return None
        s = str(s).strip()
        if len(s) <= 24:
            return s
        return f"{s[:12]}…{s[-12:]}"

    days: list[AegisMarathonDayResponse] = []
    chain: list[ContinuityChainEntry] = []
    total_corrections = 0
    total_actions = 0
    prev_sig: Optional[str] = None

    for r in rows:
        note = r.continuity_note_json or {}
        corrections = note.get("self_corrections") or []
        actions = r.actions_taken or []
        predictions = note.get("predictions") or []

        total_corrections += len(corrections)
        total_actions += len(actions)

        # Check if thought signature chain is intact
        sig_linked = False
        if prev_sig and r.prev_thought_signature:
            sig_linked = r.prev_thought_signature == prev_sig
        prev_sig = r.thought_signature

        days.append(AegisMarathonDayResponse(
            id=r.id,
            track_id=r.track_id,
            day_date=str(r.day_date),
            scan_id=r.scan_id,
            prev_scan_id=r.prev_scan_id,
            delta_json=r.delta_json,
            continuity_note_json=r.continuity_note_json,
            thought_signature=r.thought_signature,
            prev_thought_signature=r.prev_thought_signature,
            signature_short=_short(r.thought_signature),
            prev_signature_short=_short(r.prev_thought_signature),
            thinking_level=r.thinking_level,
            actions_taken=actions if actions else None,
            simulation_triggered=r.simulation_triggered,
            report_triggered=r.report_triggered,
            created_at=r.created_at.isoformat() if r.created_at else None,
        ))

        chain.append(ContinuityChainEntry(
            day_date=str(r.day_date),
            thinking_level=r.thinking_level,
            summary=note.get("summary", ""),
            decision_explanation=note.get("decision_explanation", ""),
            predictions=predictions,
            self_corrections=corrections,
            actions_taken=actions,
            signature_linked=sig_linked,
        ))

    return AegisMarathonTimelineResponse(
        track_id=track_id,
        days=days,
        continuity_chain=chain,
        total_days=len(days),
        total_self_corrections=total_corrections,
        total_actions=total_actions,
    )


@router.post("/simulations", response_model=AegisSimulationResponse)
async def create_simulation(
    payload: AegisSimulationRequest, request: Request
) -> AegisSimulationResponse:
    """Start a crisis simulation run for a selected scan and scenario.

    Request:
        JSON body (`AegisSimulationRequest`):
        - `scan_id` (int): base scan for simulation.
        - `scenario` (object): scenario knobs consumed by simulator DAG.

    Response:
        `AegisSimulationResponse` with simulation ID and initial status.

    Status Codes:
        200: Simulation accepted and queued.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each request creates a new simulation ID/job.

    Args:
        payload: Simulation input payload.
        request: FastAPI request object for background task scheduling.

    Returns:
        AegisSimulationResponse: Job metadata for polling progress.

    Raises:
        Does not raise intentionally in normal request handling.

    Side Effects:
        Writes job metadata and starts background simulation DAG execution.

    Latency:
        Fast request path; simulation DAG can be slow due to model inference.
    """
    from app.aegis.simulator.runner import run_simulation_dag

    scan_id = int(payload.scan_id)
    readiness = await _scan_readiness_snapshot(scan_id)
    if not bool(readiness.get("simulation_ready")):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Simulation prerequisites not met for scan_id {scan_id}: "
                + ", ".join(readiness.get("missing_requirements") or [])
            ),
        )
    simulation_id = f"SIM-{uuid.uuid4().hex[:8].upper()}"

    await job_store.create_job(
        simulation_id,
        "aegis_simulation",
        metadata={"scan_id": scan_id, "scenario": payload.scenario},
    )

    async def _bg() -> None:
        """Run simulation DAG using environment-driven model configuration.

        Args:
            None.

        Returns:
            None.

        Raises:
            Exceptions from `run_simulation_dag` may propagate to task logs.

        Side Effects:
            Executes simulation workflow and emits job events.

        Latency:
            Potentially high due to projection + narrative generation work.
        """
        cfg = {
            "model": os.getenv("GEMINI_MODEL_SIMULATOR", "gemini-3-flash-preview"),
            "thinking_level": os.getenv("SIMULATOR_THINKING_LEVEL", "LOW"),
        }
        await run_simulation_dag(
            scan_id=scan_id,
            simulation_id=simulation_id,
            scenario=payload.scenario,
            run_id=simulation_id,
            emit_job_events=True,
            config=cfg,
        )

    spawn_bg_task(request.app, _bg())
    return AegisSimulationResponse(
        simulation_id=simulation_id,
        status="running",
        message=f"Simulation started. Poll /api/jobs/{simulation_id}/events for progress.",
    )


@router.get("/simulations/{simulation_id}", response_model=AegisSimulationStatusResponse)
async def get_simulation(simulation_id: str) -> AegisSimulationStatusResponse:
    """Return persisted status/details for a simulation run.

    Request:
        Path parameter `simulation_id` is required.

    Response:
        `AegisSimulationStatusResponse` loaded from simulation persistence.

    Status Codes:
        200: Simulation found.
        404: Simulation ID is unknown.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        simulation_id: Simulation identifier returned by `/simulations`.

    Returns:
        AegisSimulationStatusResponse: Current simulation record.

    Raises:
        HTTPException: 404 when no simulation record is found.

    Side Effects:
        Reads simulation status from persistence layer.

    Latency:
        Depends on storage backend latency.
    """
    from app.aegis.simulator.persist import get_simulation

    sim = await get_simulation(simulation_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return AegisSimulationStatusResponse(**sim)


@router.get("/scan/{scan_id}", response_model=AegisScanStatusResponse)
async def get_scan_status(scan_id: str) -> AegisScanStatusResponse:
    """Return scan progress/details by run ID or numeric scan ID.

    Request:
        Path parameter `scan_id` accepts either:
        - A run/job ID string (for example `SCAN-XXXX`), or
        - A numeric database scan ID.

    Response:
        `AegisScanStatusResponse` containing status, totals, and optional
        state/event/LGA summaries.

    Status Codes:
        200: Matching job or scan found.
        404: No matching job/scan exists.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        scan_id: Run ID or numeric scan identifier.

    Returns:
        AegisScanStatusResponse: Current scan/job status snapshot.

    Raises:
        HTTPException: 404 when the scan cannot be resolved.

    Side Effects:
        Reads job-store records and database scan/intelligence records.

    Latency:
        Can be moderate for large scans because summary enrichment performs
        additional DB queries.
    """
    job = await job_store.get_job(scan_id)
    if job:
        status = job.get("status", "running")
        metadata = job.get("metadata") or {}
        result = job.get("result") or {}
        scan_id_value = metadata.get("scan_id") or result.get("scan_id") or 0
        summaries = None
        conflict_events = None
        lga_risk = None
        if scan_id_value:
            try:
                summaries, conflict_events, lga_risk = await summaries_for_scan(
                    int(scan_id_value)
                )
            except Exception:
                summaries, conflict_events, lga_risk = None, None, None
        return AegisScanStatusResponse(
            scan_id=int(scan_id_value or 0),
            run_id=scan_id,
            status=ScanStatus(
                status if status in ("running", "completed", "failed") else "running"
            ),
            started_at=job.get("started_at", datetime.now(timezone.utc)),
            completed_at=job.get("completed_at"),
            states_scanned=result.get("states_scanned", 0),
            total_events=result.get("total_events", 0),
            total_fatalities=result.get("total_fatalities", 0),
            state_summaries=summaries,
            conflict_events=conflict_events,
            lga_risk=lga_risk,
        )

    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisScan
        from sqlalchemy import select

        async with get_async_session() as session:
            result = await session.execute(select(AegisScan).where(AegisScan.run_id == scan_id))
            scan = result.scalar_one_or_none()
            if not scan and scan_id.isdigit():
                result = await session.execute(select(AegisScan).where(AegisScan.id == int(scan_id)))
                scan = result.scalar_one_or_none()
            if not scan:
                raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
            summaries, conflict_events, lga_risk = await summaries_for_scan(scan.id)
            return AegisScanStatusResponse(
                scan_id=scan.id,
                run_id=scan.run_id,
                status=ScanStatus(scan.status),
                started_at=scan.started_at,
                completed_at=scan.completed_at,
                states_scanned=scan.states_scanned,
                total_events=scan.total_events,
                total_fatalities=scan.total_fatalities,
                state_summaries=summaries,
                conflict_events=conflict_events,
                lga_risk=lga_risk,
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")


@router.post("/report", response_model=AegisReportResponse)
async def generate_aegis_report(
    payload: AegisReportRequest, request: Request
) -> AegisReportResponse:
    """Start asynchronous PDF report generation for a scan.

    Request:
        JSON body (`AegisReportRequest`):
        - `scan_id` (int): source scan ID.
        - `states` (optional list[str]): subset of states to include.
        - `include_infographics` (bool): generate infographic assets.
        - `include_annexes` (bool): include state annexes.
        - `simulation_id` (optional str): attach simulation context.

    Response:
        `AegisReportResponse` with report ID and initial `running` status.

    Status Codes:
        200: Report job accepted and background execution scheduled.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Not idempotent. Each call creates a new report job.

    Args:
        payload: Report-generation options and scan reference.
        request: FastAPI request object for background task scheduling.

    Returns:
        AegisReportResponse: Immediate report job metadata.

    Raises:
        Does not raise intentionally in normal request path.

    Side Effects:
        Writes job/event records and triggers background report DAG execution.

    Latency:
        Fast request path; report generation is I/O and model heavy.
    """
    from app.aegis.report.runner import run_report_dag

    scan_id = int(payload.scan_id)
    readiness = await _scan_readiness_snapshot(scan_id)
    if not bool(readiness.get("report_ready")):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Report prerequisites not met for scan_id {scan_id}: "
                + ", ".join(readiness.get("missing_requirements") or [])
            ),
        )
    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(
        report_id,
        "aegis_report",
        metadata={"scan_id": scan_id, "states": payload.states or []},
    )
    await job_store.add_event(
        report_id,
        "report_started",
        status="running",
        step="report_start",
        message="AEGIS report started",
        payload={"scan_id": scan_id},
    )

    async def _bg() -> None:
        """Run report DAG and persist completion/failure into job contracts.

        Args:
            None.

        Returns:
            None.

        Raises:
            Does not raise intentionally; failures are persisted in `job_store`.

        Side Effects:
            Executes report generation and writes job status/event updates.

        Latency:
            Potentially high for PDF generation, storage, and optional imagery.
        """
        try:
            result = await run_report_dag(
                report_id=report_id,
                scan_id=scan_id,
                states=payload.states or [],
                include_infographics=payload.include_infographics,
                include_annexes=payload.include_annexes,
                output_dir=str(REPORTS_DIR),
                simulation_id=payload.simulation_id,
                emit_job_events=True,
            )
            await job_store.update_job(
                report_id,
                status="completed",
                result=result,
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                report_id,
                "report_completed",
                status="completed",
                step="report_complete",
                message="AEGIS report completed",
                payload={"pdf_path": result.get("pdf_path")},
            )
        except Exception as e:
            await job_store.update_job(
                report_id,
                status="failed",
                result={"error": str(e)},
                completed_at=utcnow_naive(),
            )
            await job_store.add_event(
                report_id,
                "report_failed",
                status="failed",
                step="report_error",
                message=str(e),
            )

    spawn_bg_task(request.app, _bg())
    return AegisReportResponse(
        report_id=report_id,
        status="running",
        message="Report generation started.",
        pdf_path=None,
        download_url=None,
    )


@router.get("/report/{report_id}", response_model=AegisReportStatusResponse)
async def get_report_status(report_id: str) -> AegisReportStatusResponse:
    """Return detailed status for a report-generation job.

    Request:
        Path parameter `report_id` is required.

    Response:
        `AegisReportStatusResponse` including status, output path/url,
        completed steps, timings, and summary metrics.

    Status Codes:
        200: Report job found.
        404: Report job not found.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        report_id: Report job identifier returned by `/report`.

    Returns:
        AegisReportStatusResponse: Current report job status view.

    Raises:
        HTTPException: 404 when the report job does not exist.

    Side Effects:
        Reads report job data from job store.

    Latency:
        Depends on job-store backend latency.
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisReport
    from sqlalchemy import select

    job = await job_store.get_job(report_id)

    db_row = None
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisReport).where(AegisReport.report_id == report_id)
        )
        db_row = res.scalar_one_or_none()

    if not job and not db_row:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    result_payload = (job or {}).get("result") or {}
    status_value = (
        (job or {}).get("status")
        or (db_row.status if db_row else None)
        or "running"
    ).lower()
    if status_value == "error":
        status_value = "failed"
    if status_value not in {"running", "completed", "failed"}:
        status_value = "running"

    raw_states = result_payload.get("states_analyzed")
    if raw_states is None:
        raw_states = result_payload.get("states")
    if isinstance(raw_states, dict):
        raw_states = raw_states.get("states") or []
    if not isinstance(raw_states, list):
        raw_states = []

    raw_steps = (job or {}).get("steps_completed") or []
    if not isinstance(raw_steps, list):
        raw_steps = []

    raw_timings = (job or {}).get("timings") or {}
    if not isinstance(raw_timings, dict):
        raw_timings = {}

    raw_db_states = (db_row.states or {}) if db_row and db_row.states else {}
    if isinstance(raw_db_states, dict):
        raw_db_states = raw_db_states.get("states") or []
    if not raw_states and isinstance(raw_db_states, list):
        raw_states = raw_db_states

    pdf_path = result_payload.get("pdf_path")
    if not pdf_path and db_row:
        pdf_path = db_row.pdf_path
    has_download = bool(pdf_path or (db_row and db_row.gcs_key))

    return AegisReportStatusResponse(
        report_id=report_id,
        status=ReportStatus(status_value),
        started_at=(job or {}).get("started_at") or (db_row.started_at if db_row else None),
        completed_at=(job or {}).get("completed_at") or (db_row.completed_at if db_row else None),
        pdf_path=pdf_path,
        download_url=f"/api/aegis/report/{report_id}/download"
        if has_download
        else None,
        steps_completed=[str(s) for s in raw_steps],
        timings={
            str(k): float(v)
            for k, v in raw_timings.items()
            if isinstance(k, (str, int, float)) and isinstance(v, (int, float))
        },
        error=(
            str(result_payload.get("error"))
            if result_payload.get("error") is not None
            else (db_row.error if db_row else None)
        ),
        states_analyzed=[str(s) for s in raw_states],
        sources_cited=int(result_payload.get("sources_cited") or 0),
        infographics_generated=int(result_payload.get("infographics_generated") or 0),
    )


@router.get("/report/{report_id}/download")
async def download_report(report_id: str) -> Response:
    """Download a completed report PDF from local disk or GCS fallback.

    Request:
        Path parameter `report_id` is required.

    Response:
        Binary PDF response with `application/pdf` content type.

    Status Codes:
        200: PDF returned successfully.
        400: Report exists but is not yet completed.
        404: Report missing or PDF artifact unavailable.

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent retrieval for an immutable completed report artifact.

    Args:
        report_id: Report job identifier to download.

    Returns:
        Response: File response from disk or bytes response from GCS.

    Raises:
        HTTPException: 400 or 404 based on report state/artifact availability.

    Side Effects:
        Reads job-store metadata.
        Performs filesystem checks and optional network call to GCS.

    Latency:
        May be slow for remote GCS download or large PDFs.
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisReport
    from sqlalchemy import select

    job = await job_store.get_job(report_id)
    db_row = None
    async with get_async_session() as session:
        res = await session.execute(
            select(AegisReport).where(AegisReport.report_id == report_id)
        )
        db_row = res.scalar_one_or_none()

    if not job and not db_row:
        raise HTTPException(status_code=404, detail="Report not found")

    status_value = (
        (job or {}).get("status") or (db_row.status if db_row else None) or "running"
    )
    if str(status_value).lower() != "completed":
        raise HTTPException(status_code=400, detail="Report not yet completed")

    result_payload = (job or {}).get("result") or {}
    pdf_path = result_payload.get("pdf_path") or (db_row.pdf_path if db_row else None)
    if not pdf_path or not Path(pdf_path).exists():
        try:
            from app.utils.gcs_store import download_bytes
            from app.config import GCS_BUCKET, GCS_REPORT_PREFIX

            gcs_key = result_payload.get("gcs_key") or (db_row.gcs_key if db_row else None)
            if not gcs_key and pdf_path:
                gcs_key = GCS_REPORT_PREFIX + Path(pdf_path).name
            if gcs_key:
                data = download_bytes(GCS_BUCKET, gcs_key)
                filename = Path(gcs_key).name
                return Response(
                    content=data,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename=\"{filename}\"'
                    },
                )
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="PDF file not found")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=Path(pdf_path).name,
    )


@router.get("/reports")
async def list_reports() -> dict[str, Any]:
    """List available report artifacts from GCS or local fallback storage.

    Request:
        No body, query, or path parameters.

    Response:
        JSON object:
        - `reports`: list of file metadata dictionaries.
        - `total`: integer report count.

    Status Codes:
        200: Listing returned (can be empty).

    Auth:
        Requires valid API token via `require_api_auth`.

    Idempotency:
        Idempotent read endpoint.

    Args:
        None.

    Returns:
        dict[str, Any]: Report listing payload consumed by frontend report views.

    Raises:
        Does not raise intentionally; GCS failures fall back to local listing.

    Side Effects:
        Performs optional network call to GCS and/or local filesystem reads.

    Latency:
        GCS listing can be slower than local fallback.
    """
    reports: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Preferred source: persisted report rows (stable even when job_store is ephemeral).
    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisReport
        from sqlalchemy import desc, select

        async with get_async_session() as session:
            res = await session.execute(
                select(AegisReport)
                .where(AegisReport.status == "completed")
                .order_by(desc(AegisReport.completed_at), desc(AegisReport.created_at))
            )
            rows = res.scalars().all()

        for row in rows:
            filename = None
            if row.pdf_path:
                filename = Path(row.pdf_path).name
            elif row.gcs_key:
                filename = Path(row.gcs_key).name
            if not filename:
                filename = f"{row.report_id}.pdf"

            if filename in seen:
                continue
            seen.add(filename)

            size_bytes = 0
            try:
                if row.pdf_path and Path(row.pdf_path).exists():
                    size_bytes = int(Path(row.pdf_path).stat().st_size)
            except Exception:
                size_bytes = 0

            created_dt = row.completed_at or row.created_at or datetime.now(timezone.utc)
            created_at = (
                created_dt.isoformat()
                if hasattr(created_dt, "isoformat")
                else str(created_dt)
            )

            reports.append(
                {
                    "filename": filename,
                    "created_at": created_at,
                    "size_bytes": size_bytes,
                    "download_url": f"/api/aegis/report/{row.report_id}/download",
                }
            )
    except Exception:
        pass

    # Fallback: object listing from GCS.
    if not reports:
        try:
            from app.utils.gcs_store import list_objects
            from app.config import GCS_BUCKET, GCS_REPORT_PREFIX

            objs = list_objects(GCS_BUCKET, GCS_REPORT_PREFIX)
            for obj in objs:
                name = obj.get("name") or ""
                if not name.endswith(".pdf"):
                    continue
                filename = Path(name).name
                report_id = filename.rsplit("_", 1)[-1].replace(".pdf", "")
                updated = obj.get("updated")
                if hasattr(updated, "isoformat"):
                    created_at = updated.isoformat()
                else:
                    created_at = (
                        datetime.fromtimestamp(float(updated)).isoformat()
                        if updated
                        else datetime.now(timezone.utc).isoformat()
                    )
                reports.append(
                    {
                        "filename": filename,
                        "created_at": created_at,
                        "size_bytes": int(obj.get("size") or 0),
                        "download_url": f"/api/aegis/report/{report_id}/download",
                    }
                )
        except Exception:
            for pdf_file in REPORTS_DIR.glob("*.pdf"):
                reports.append(
                    {
                        "filename": pdf_file.name,
                        "created_at": datetime.fromtimestamp(pdf_file.stat().st_mtime).isoformat(),
                        "size_bytes": pdf_file.stat().st_size,
                        "download_url": f"/static/reports/{pdf_file.name}",
                    }
                )

    reports.sort(key=lambda x: x["created_at"], reverse=True)
    return {"reports": reports, "total": len(reports)}
