"""Farma API - Main application entry point.

Exposes endpoints for:
1. Farmer interactions (SMS, Voice, loan applications monitoring)
2. AEGIS intelligence system (scans, reports, dashboard)
3. System health and monitoring
"""

import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
import asyncio

from fastapi import (
    FastAPI,
    Form,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.workflows.graph import farma_graph
from app.config import AEGIS_FOCUS_STATES
from app.api.schemas import (
    FarmerResponse,
    AegisScanRequest,
    AegisScanResponse,
    AegisScanStatusResponse,
    AegisReportRequest,
    AegisReportResponse,
    AegisReportStatusResponse,
    AegisSynthesisRequest,
    AegisSynthesisResponse,
    AegisDashboardResponse,
    StateIntelligenceSummary,
    HealthResponse,
    ScanStatus,
    ReportStatus,
    JobResponse,
    JobEventsResponse,
    JobStatus,
)
from app.utils.job_store import job_store
from app.utils.thinking_bus import thinking_bus

BASE_DIR = Path(__file__).resolve().parent.parent
TMP_DIR = BASE_DIR / "tmp_audio"
REPORTS_DIR = BASE_DIR / "reports"
TMP_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


def _spawn_bg_task(coro) -> None:
    """Run a background coroutine and retain a reference to avoid GC."""
    task = asyncio.create_task(coro)
    try:
        app.state.bg_tasks.add(task)  # type: ignore[attr-defined]
    except Exception:
        pass

    def _done(_t):
        try:
            app.state.bg_tasks.discard(_t)  # type: ignore[attr-defined]
        except Exception:
            pass

    task.add_done_callback(_done)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _utcnow_naive() -> datetime:
    """UTC timestamp as naive datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seconds_until_next_utc_time(hour: int, minute: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


def _parse_hour_minute() -> tuple[int, int] | None:
    raw_hour = os.getenv("AEGIS_REFRESH_HOUR", "6").strip()
    raw_minute = os.getenv("AEGIS_REFRESH_MINUTE", "0").strip()

    if ":" in raw_hour:
        parts = raw_hour.split(":", 1)
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except Exception:
            return None
    else:
        try:
            hour = int(raw_hour)
            minute = int(raw_minute)
        except Exception:
            return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


async def _scheduled_aegis_loop() -> None:
    """Run daily scheduled scan (+ optional report) for unattended demos."""
    from app.aegis.graph import run_aegis_scan
    from app.aegis.synthesis.runner import run_synthesis_dag
    from app.aegis.report.runner import run_report_dag
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan

    enabled = _env_bool("AEGIS_AUTO_REFRESH", default=False)
    if not enabled:
        return

    parsed = _parse_hour_minute()
    if not parsed:
        print("[AEGIS/SCHED] Disabled: invalid schedule env vars")
        return
    hour, minute = parsed

    days_back = int(os.getenv("AEGIS_SCAN_DAYS_BACK", "7"))
    include_report = _env_bool("AEGIS_AUTO_REPORT", default=True)
    include_infographics = _env_bool("AEGIS_AUTO_REPORT_INFOGRAPHICS", default=False)
    include_annexes = _env_bool("AEGIS_AUTO_REPORT_ANNEXES", default=True)

    while True:
        wait_s = _seconds_until_next_utc_time(hour, minute)
        print(
            f"[AEGIS/SCHED] Next run in {wait_s/3600:.1f}h (UTC {hour:02d}:{minute:02d})"
        )
        await asyncio.sleep(wait_s)

        run_id = f"SCHED-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        print(f"[AEGIS/SCHED] Starting scheduled scan: {run_id}")

        scan_db_id = 0
        try:
            async with get_async_session() as session:
                scan = AegisScan(
                    run_id=run_id,
                    started_at=_utcnow_naive(),
                    status="running",
                    states_scanned=0,
                    total_events=0,
                    total_fatalities=0,
                )
                session.add(scan)
                await session.flush()
                scan_db_id = scan.id
        except Exception as e:
            print(f"[AEGIS/SCHED] Could not create scan record: {e}")

        await job_store.create_job(
            run_id,
            "aegis_scan",
            metadata={"scan_id": scan_db_id, "days_back": days_back},
        )
        await job_store.add_event(
            run_id,
            "scan_started",
            status="running",
            step="scan_start",
            message="Scheduled scan started",
        )

        try:
            scan_result = await run_aegis_scan(
                days_back=days_back,
                force=False,
                states=AEGIS_FOCUS_STATES,
                run_id=run_id,
                scan_id=scan_db_id or None,
            )
            await job_store.update_job(
                run_id,
                status="completed",
                result=scan_result,
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                "scan_completed",
                status="completed",
                step="scan_complete",
                message="Scheduled scan completed",
            )
        except Exception as e:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                "scan_failed",
                status="failed",
                step="scan_error",
                message=str(e),
            )
            continue

        if include_report and scan_db_id:
            # Stage 1: Synthesis (DB-canonical JSON)
            synth_id = f"SYNTH-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            print(f"[AEGIS/SCHED] Starting scheduled synthesis: {synth_id}")
            await job_store.create_job(
                synth_id,
                "aegis_synthesis",
                metadata={"scan_id": scan_db_id, "states": AEGIS_FOCUS_STATES},
            )
            await job_store.add_event(
                synth_id,
                "synthesis_started",
                status="running",
                step="synthesis_start",
                message="Scheduled synthesis started",
            )
            try:
                synth_result = await run_synthesis_dag(
                    scan_id=int(scan_db_id),
                    states=AEGIS_FOCUS_STATES,
                    run_id=synth_id,
                    emit_job_events=True,
                )
                await job_store.update_job(
                    synth_id,
                    status="completed",
                    result=synth_result,
                    completed_at=_utcnow_naive(),
                )
                await job_store.add_event(
                    synth_id,
                    "synthesis_completed",
                    status="completed",
                    step="synthesis_complete",
                    message="Scheduled synthesis completed",
                    payload={"scan_id": scan_db_id},
                )
            except Exception as e:
                await job_store.update_job(
                    synth_id,
                    status="failed",
                    result={"error": str(e), "scan_id": scan_db_id},
                    completed_at=_utcnow_naive(),
                )
                await job_store.add_event(
                    synth_id,
                    "synthesis_failed",
                    status="failed",
                    step="synthesis_error",
                    message=str(e),
                    payload={"scan_id": scan_db_id},
                )
                continue

            # Stage 2: Report (packaging-only PDF)
            report_id = f"SCHED-RPT-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            print(f"[AEGIS/SCHED] Starting scheduled report: {report_id}")
            await job_store.create_job(
                report_id, "aegis_report", metadata={"scan_id": scan_db_id}
            )
            await job_store.add_event(
                report_id,
                "report_started",
                status="running",
                step="report_start",
                message="Scheduled report started",
            )
            try:
                report_result = await run_report_dag(
                    report_id=report_id,
                    scan_id=int(scan_db_id),
                    states=[],
                    include_infographics=include_infographics,
                    include_annexes=include_annexes,
                    output_dir=str(REPORTS_DIR),
                    emit_job_events=True,
                )
                await job_store.update_job(
                    report_id,
                    status="completed",
                    result={"pdf_path": report_result.get("pdf_path")},
                    completed_at=_utcnow_naive(),
                )
                await job_store.add_event(
                    report_id,
                    "report_completed",
                    status="completed",
                    step="report_complete",
                    message="Scheduled report ready",
                    payload={"pdf_path": report_result.get("pdf_path")},
                )
            except Exception as e:
                await job_store.update_job(
                    report_id,
                    status="failed",
                    result={"error": str(e)},
                    completed_at=_utcnow_naive(),
                )
                await job_store.add_event(
                    report_id,
                    "report_failed",
                    status="failed",
                    step="report_error",
                    message=str(e),
                )


def _priority_from_intel(intel) -> tuple[str, int]:
    score = 0
    ipc = intel.ipc_phase or 0
    score += min(ipc * 15, 75)

    idp = intel.idp_estimate or 0
    if idp > 1000000:
        score += 35
    elif idp > 500000:
        score += 25
    elif idp > 200000:
        score += 15

    conflicts = intel.conflict_events_count or 0
    if conflicts > 100:
        score += 25
    elif conflicts > 50:
        score += 15
    elif conflicts > 20:
        score += 8

    score = min(score, 100)
    if score >= 80:
        return "CRITICAL", score
    if score >= 60:
        return "HIGH", score
    if score >= 40:
        return "ELEVATED", score
    return "LOW", score


async def _summaries_for_scan(scan_id: int) -> tuple[list, list, list]:
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import StateIntelligence, ConflictEvent, LGARiskScore
    from sqlalchemy import select, desc

    summaries = []
    events = []
    lga_risk: list[dict] = []
    async with get_async_session() as session:
        result = await session.execute(
            select(StateIntelligence).where(StateIntelligence.scan_id == scan_id)
        )
        state_rows = result.scalars().all()
        for intel in state_rows:
            level, score = _priority_from_intel(intel)
            summaries.append(
                {
                    "state_name": intel.state_name,
                    "conflict_events": intel.conflict_events_count,
                    "idp_estimate": intel.idp_estimate,
                    "idp_trend": intel.idp_trend,
                    "food_insecurity_level": intel.food_insecurity_level,
                    "ipc_phase": intel.ipc_phase,
                    "markets_operational": intel.markets_operational,
                    "priority_level": level,
                    "priority_score": score,
                }
            )

            conflict_result = await session.execute(
                select(ConflictEvent)
                .where(ConflictEvent.state_intel_id == intel.id)
                .limit(200)
            )
            for event in conflict_result.scalars().all():
                events.append(
                    {
                        "state": event.state,
                        "lga": event.lga,
                        "event_type": event.event_type,
                        "fatalities": event.fatalities,
                        "date": event.event_date,
                        "summary": event.summary,
                        "location": event.location,
                        "lat": event.latitude,
                        "lon": event.longitude,
                    }
                )

        # stored LGA risk scores
        try:
            lga_result = await session.execute(
                select(LGARiskScore)
                .where(LGARiskScore.scan_id == scan_id)
                .order_by(desc(LGARiskScore.risk_score))
            )
            lga_risk = [
                {
                    "lga": r.lga,
                    "state": r.state,
                    "event_count": r.event_count,
                    "fatalities": r.fatalities,
                    "risk_score": r.risk_score,
                    "risk_level": r.risk_level,
                }
                for r in lga_result.scalars().all()
            ]
            # while a scan is still running, finalize may not have computed/stored LGA
            # scores yet.this is to fall back to on-the-fly aggregation so the map can update incrementally as states complete.
            if not lga_risk and events:
                agg: dict[tuple[str, str], dict] = {}
                for e in events:
                    lga = (e.get("lga") or "").strip()
                    state = (e.get("state") or "").strip()
                    if not lga or not state:
                        continue
                    key = (state, lga)
                    entry = agg.setdefault(
                        key,
                        {"lga": lga, "state": state, "event_count": 0, "fatalities": 0},
                    )
                    entry["event_count"] += 1
                    entry["fatalities"] += int(e.get("fatalities") or 0)

                lga_risk = []
                for entry in agg.values():
                    score = entry["event_count"] * 2 + entry["fatalities"] * 3
                    if score >= 25:
                        level = "CRITICAL"
                    elif score >= 15:
                        level = "HIGH"
                    elif score >= 6:
                        level = "ELEVATED"
                    else:
                        level = "LOW"
                    lga_risk.append({**entry, "risk_score": score, "risk_level": level})
                lga_risk.sort(key=lambda x: x.get("risk_score", 0), reverse=True)
        except Exception:
            lga_risk = []

    return summaries, events, lga_risk


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup
    print("[FARMA] Starting up...")
    print(f"[FARMA] Reports directory: {REPORTS_DIR}")
    # Ensure DB tables exist for job contract if enabled
    try:
        from app.aegis.db.connection import init_db

        auto_init = os.getenv("AUTO_INIT_DB", "true").lower() in ("1", "true", "yes")
        if auto_init:
            await init_db()
            try:
                from app.aegis.db.connection import get_async_session
                from sqlalchemy import text

                async with get_async_session() as session:
                    # Demo-safe schema migrations for older local DBs (no Alembic required).
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS food_security_raw JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS economic_raw JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS conflict_events_count INTEGER DEFAULT 0"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS idp_estimate INTEGER"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS idp_trend VARCHAR(20) DEFAULT 'unknown'"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS food_insecurity_level VARCHAR(20) DEFAULT 'unknown'"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS ipc_phase INTEGER"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS markets_operational VARCHAR(20) DEFAULT 'unknown'"
                        )
                    )
                    # Deterministic synthesis output persistence (assessment per state)
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS assessment_json JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS synthesized_at TIMESTAMP WITHOUT TIME ZONE"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_state_intelligence "
                            "ADD COLUMN IF NOT EXISTS synthesis_version VARCHAR(32)"
                        )
                    )

                    # Deterministic synthesis rollup persistence (per scan)
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_scans "
                            "ADD COLUMN IF NOT EXISTS rollup_json JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_scans "
                            "ADD COLUMN IF NOT EXISTS rollup_at TIMESTAMP WITHOUT TIME ZONE"
                        )
                    )

                    # Report metadata table (packaging-only report DAG persists here)
                    await session.execute(
                        text(
                            "CREATE TABLE IF NOT EXISTS aegis_reports ("
                            "id SERIAL PRIMARY KEY"
                            ")"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS report_id VARCHAR(50)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS scan_id INTEGER"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITHOUT TIME ZONE"
                        )
                    )
                    # Demo-safe: if started_at exists but has no default / has NULLs, fix it.
                    await session.execute(
                        text(
                            "DO $$\n"
                            "BEGIN\n"
                            "  IF EXISTS (\n"
                            "    SELECT 1 FROM information_schema.columns\n"
                            "    WHERE table_name='aegis_reports' AND column_name='started_at'\n"
                            "  ) THEN\n"
                            "    EXECUTE 'ALTER TABLE aegis_reports ALTER COLUMN started_at SET DEFAULT NOW()';\n"
                            "    EXECUTE 'UPDATE aegis_reports SET started_at=COALESCE(started_at, created_at, NOW()) WHERE started_at IS NULL';\n"
                            "  END IF;\n"
                            "END $$;"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS states JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS include_infographics BOOLEAN DEFAULT TRUE"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS include_annexes BOOLEAN DEFAULT TRUE"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS pdf_path VARCHAR(300)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS gcs_key VARCHAR(300)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'running'"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_reports "
                            "ADD COLUMN IF NOT EXISTS error TEXT"
                        )
                    )
                    await session.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS ix_aegis_reports_report_id "
                            "ON aegis_reports (report_id)"
                        )
                    )
                    await session.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_aegis_reports_scan_id "
                            "ON aegis_reports (scan_id)"
                        )
                    )

                    # Older schema used `trend_direction` (NOT NULL) for IDP trend.
                    # Ensure it has a safe default so inserts don't fail.
                    await session.execute(
                        text(
                            "DO $$\n"
                            "BEGIN\n"
                            "  IF EXISTS (\n"
                            "    SELECT 1 FROM information_schema.columns\n"
                            "    WHERE table_name='aegis_state_intelligence' AND column_name='trend_direction'\n"
                            "  ) THEN\n"
                            "    EXECUTE 'ALTER TABLE aegis_state_intelligence ALTER COLUMN trend_direction SET DEFAULT ''unknown''';\n"
                            "    EXECUTE 'UPDATE aegis_state_intelligence SET trend_direction=''unknown'' WHERE trend_direction IS NULL';\n"
                            "  END IF;\n"
                            "END $$;"
                        )
                    )

                    # Demo-safe: ensure conflict event coordinates columns exist when DB is older.
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_conflict_events "
                            "ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS aegis_conflict_events "
                            "ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS job_runs "
                            "ADD COLUMN IF NOT EXISTS job_metadata JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS job_runs "
                            "DROP COLUMN IF EXISTS metadata"
                        )
                    )

                    # Farmer interaction persistence (Phase 1: remove in-memory logs)
                    await session.execute(
                        text(
                            "CREATE TABLE IF NOT EXISTS farmer_interactions ("
                            "id SERIAL PRIMARY KEY"
                            ")"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS input_type VARCHAR(20) DEFAULT 'sms'"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS phone VARCHAR(64)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS message TEXT"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS intent VARCHAR(64)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS language VARCHAR(32)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS status VARCHAR(64)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS final_decision VARCHAR(32)"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS climate_score DOUBLE PRECISION"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS risk_flags JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS farmer_response TEXT"
                        )
                    )
                    await session.execute(
                        text(
                            "ALTER TABLE IF EXISTS farmer_interactions "
                            "ADD COLUMN IF NOT EXISTS details JSONB"
                        )
                    )
                    await session.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS idx_farmer_interactions_created_at "
                            "ON farmer_interactions (created_at)"
                        )
                    )
                    await session.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS idx_farmer_interactions_phone "
                            "ON farmer_interactions (phone)"
                        )
                    )
                    await session.commit()
                print("[FARMA] DB schema updated for conflict geocoding.")
            except Exception as e:
                print(f"[FARMA] DB alter skipped: {e}")
            print("[FARMA] DB tables ensured.")
    except Exception as e:
        print(f"[FARMA] DB init skipped: {e}")
    # Track background tasks
    if not hasattr(app.state, "bg_tasks"):
        app.state.bg_tasks = set()
    # Start optional scheduled scan loop (unattended demos)
    app.state.aegis_sched_task = asyncio.create_task(_scheduled_aegis_loop())

    yield
    # Shutdown
    print("[FARMA] Shutting down...")
    task = getattr(app.state, "aegis_sched_task", None)
    if task:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    try:
        from app.aegis.db.connection import close_db

        await close_db()
    except Exception:
        pass


app = FastAPI(
    title="Farma API",
    description="AI-powered agricultural assistance for Nigerian farmers. Includes AEGIS humanitarian intelligence system.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - to allow ai studio and others to access this
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://*.vercel.app",
        "https://*.aistudio.google.com",
        "*",  # For development - restrict in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve static files like reports, infographics
if REPORTS_DIR.exists():
    app.mount(
        "/static/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports"
    )


@app.get("/", tags=["System"])
def root():
    """Root endpoint - API info."""
    return {
        "name": "Farma API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "description": "AI-powered agricultural assistance for Nigerian farmers",
    }


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check for deployment monitoring."""
    # check database connection
    db_status = "unknown"
    try:
        from app.aegis.db.connection import get_async_session
        from sqlalchemy import text

        async with get_async_session() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return HealthResponse(
        status="healthy",
        version="1.0.0",
        database=db_status,
        services={
            "farma_workflow": "ready",
            "aegis_synthesis": "ready",
            "aegis_reports": "ready",
            "gemini_api": "configured" if os.getenv("GOOGLE_API_KEY") else "missing",
        },
    )


@app.websocket("/ws/thinking")
async def thinking_stream(websocket: WebSocket):
    """WebSocket stream for agent reasoning and job events."""
    await thinking_bus.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await thinking_bus.disconnect(websocket)


# farmer
async def _log_interaction(input_type: str, phone: str, message: str, result: dict):
    """Persist farmer interaction for admin dashboard."""
    try:
        from app.aegis.db.connection import get_async_session
        from app.utils.job_store import FarmerInteraction

        async with get_async_session() as session:
            row = FarmerInteraction(
                input_type=input_type,
                phone=phone,
                message=message if message else None,
                intent=result.get("intent"),
                language=result.get("language"),
                status=result.get("status"),
                final_decision=result.get("final_decision"),
                climate_score=result.get("climate_score"),
                risk_flags={"risk_flags": result.get("risk_flags", []) or []},
                farmer_response=result.get("farmer_response"),
                details=result if isinstance(result, dict) else {"raw": str(result)},
            )
            session.add(row)
            await session.flush()
    except Exception as e:
        print(f"[ADMIN] could not persist farmer interaction: {e}")


@app.post("/api/sms", response_model=FarmerResponse, tags=["Farmer"])
async def receive_sms(From: str = Form(...), Body: str = Form(...)):
    """
    Receive SMS from farmer.

    Processes the message through the Farma workflow:
    - Parses intent (loan, disease, weather)
    - Routes to appropriate engine
    - Returns farmer-friendly response
    """
    sms_input = {
        "input_type": "sms",
        "phone": From,
        "message": Body,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    print(f"[SMS] Incoming from {From}: {Body[:50]}...")

    config = {"configurable": {"thread_id": From}}
    result = await asyncio.to_thread(farma_graph.invoke, sms_input, config)

    # log for admin dashboard
    await _log_interaction("sms", From, Body, result)

    return FarmerResponse(
        status=result.get("status", "COMPLETED"),
        intent=result.get("intent"),
        language=result.get("language"),
        parsed_data=result.get("parsed_data"),
        farmer_response=result.get("farmer_response"),
        coordinates=result.get("coordinates"),
        climate_score=result.get("climate_score"),
        final_decision=result.get("final_decision"),
        risk_flags=result.get("risk_flags"),
    )


@app.post("/api/sms/job", response_model=JobResponse, tags=["Farmer"])
async def receive_sms_job(From: str = Form(...), Body: str = Form(...)):
    """SMS pipeline as a job with step events (demo-stable)."""
    job_id = f"SMS-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(job_id, "sms_pipeline", metadata={"phone": From})
    await job_store.add_event(
        job_id,
        event_type="simulation_started",
        status="running",
        step="start",
        message="SMS pipeline started",
    )

    sms_input = {
        "input_type": "sms",
        "phone": From,
        "message": Body,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    async def run_sms_job():
        try:
            from app.workflows.nodes.sms_parser import sms_parser_node
            from app.workflows.nodes.geospatial_engine import (
                geocoding_node,
                satellite_analysis_node,
            )
            from app.workflows.nodes.aegis_integration import aegis_risk_check_node
            from app.workflows.nodes.narrative_engine import (
                narrative_orchestration_node,
            )
            from app.workflows.nodes.handlers import (
                loan_decision_node,
                climate_advisory_handler,
            )
            from app.workflows.graph import response_aggregator, sms_sender_node

            working_state = dict(sms_input)

            async def _step(step: str, fn):
                await job_store.add_event(
                    job_id,
                    event_type="step_started",
                    status="running",
                    step=step,
                    message=f"Running: {step}",
                )
                state_in = dict(working_state)
                out = await asyncio.to_thread(fn, state_in)
                if isinstance(out, dict):
                    working_state.update(out)
                await job_store.add_event(
                    job_id,
                    event_type="step_completed",
                    status="completed",
                    step=step,
                    message=f"Completed: {step}",
                    payload=out if isinstance(out, dict) else {},
                )

            await _step("parse_intent", sms_parser_node)

            intent = working_state.get("intent", "HUMAN_ESCALATION")
            if intent != "LOAN_REQUEST":
                result = await asyncio.to_thread(
                    farma_graph.invoke,
                    working_state,
                    {"configurable": {"thread_id": From}},
                )
                working_state.update(result if isinstance(result, dict) else {})
            else:
                await _step("geocode_location", geocoding_node)
                await _step("satellite_check", satellite_analysis_node)
                await _step("aegis_risk_check", aegis_risk_check_node)
                await _step("loan_decision", narrative_orchestration_node)
                await _step("loan_decision", loan_decision_node)
                await _step("loan_decision", climate_advisory_handler)
                await _step("loan_decision", response_aggregator)
                await _step("loan_decision", sms_sender_node)

            result = working_state
            await _log_interaction("sms", From, Body, result)
            await job_store.update_job(
                job_id,
                status="completed",
                result=result,
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_completed",
                status="completed",
                step="complete",
                message="SMS pipeline completed",
            )
        except Exception as e:
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_failed",
                status="failed",
                step="error",
                message=str(e),
            )

    _spawn_bg_task(run_sms_job())

    return JobResponse(
        job_id=job_id,
        job_type="sms_pipeline",
        status=JobStatus.RUNNING,
        started_at=_utcnow_naive(),
        completed_at=None,
        result=None,
    )


@app.post("/api/voice", response_model=FarmerResponse, tags=["Farmer"])
async def receive_voice(From: str = Form(...), audio_file: UploadFile = File(...)):
    """
    Receive voice message from farmer.

    Transcribes audio and processes through Farma workflow.
    Supports: wav, mp3, m4a, ogg formats.
    """
    # save file temporarily
    file_path = TMP_DIR / audio_file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)

    voice_input = {
        "input_type": "voice",
        "phone": From,
        "message": None,
        "audio_path": str(file_path),
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    print(f"[VOICE] Incoming from {From}")

    config = {"configurable": {"thread_id": From}}
    result = await asyncio.to_thread(farma_graph.invoke, voice_input, config)

    # log for admin dashboard
    await _log_interaction(
        "voice", From, result.get("message", "[voice transcription]"), result
    )

    # Upload audio to GCS for debugging/recovery, then cleanup local file.
    try:
        from app.utils.gcs_store import upload_bytes
        from app.config import GCS_BUCKET, GCS_AUDIO_PREFIX

        audio_key = f"{GCS_AUDIO_PREFIX}{uuid.uuid4().hex}_{audio_file.filename}"
        content_type = audio_file.content_type or "application/octet-stream"
        upload_bytes(GCS_BUCKET, audio_key, file_path.read_bytes(), content_type)
    except Exception as e:
        print(f"[VOICE] GCS upload skipped: {e}")

    # cleanup temp file
    try:
        file_path.unlink()
    except Exception:
        pass

    return FarmerResponse(
        status=result.get("status", "COMPLETED"),
        intent=result.get("intent"),
        language=result.get("language"),
        parsed_data=result.get("parsed_data"),
        farmer_response=result.get("farmer_response"),
        coordinates=result.get("coordinates"),
        climate_score=result.get("climate_score"),
        final_decision=result.get("final_decision"),
        risk_flags=result.get("risk_flags"),
    )


@app.post("/api/voice/job", response_model=JobResponse, tags=["Farmer"])
async def receive_voice_job(From: str = Form(...), audio_file: UploadFile = File(...)):
    """Voice pipeline as a job with step events (demo-stable)."""
    job_id = f"VOICE-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(job_id, "voice_pipeline", metadata={"phone": From})
    await job_store.add_event(
        job_id,
        event_type="simulation_started",
        status="running",
        step="start",
        message="Voice pipeline started",
    )

    file_path = TMP_DIR / f"{uuid.uuid4().hex}_{audio_file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)

    voice_input = {
        "input_type": "voice",
        "phone": From,
        "message": None,
        "audio_path": str(file_path),
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    async def run_voice_job():
        try:
            from app.workflows.nodes.voice_parser import voice_parser_node
            from app.workflows.nodes.geospatial_engine import (
                geocoding_node,
                satellite_analysis_node,
            )
            from app.workflows.nodes.aegis_integration import aegis_risk_check_node
            from app.workflows.nodes.narrative_engine import (
                narrative_orchestration_node,
            )
            from app.workflows.nodes.handlers import (
                loan_decision_node,
                climate_advisory_handler,
            )
            from app.workflows.graph import response_aggregator, sms_sender_node

            working_state = dict(voice_input)

            async def _step(step: str, fn):
                await job_store.add_event(
                    job_id,
                    event_type="step_started",
                    status="running",
                    step=step,
                    message=f"Running: {step}",
                )
                state_in = dict(working_state)
                out = await asyncio.to_thread(fn, state_in)
                if isinstance(out, dict):
                    working_state.update(out)
                await job_store.add_event(
                    job_id,
                    event_type="step_completed",
                    status="completed",
                    step=step,
                    message=f"Completed: {step}",
                    payload=out if isinstance(out, dict) else {},
                )

            # Step 1: Parse intent from voice
            await _step("parse_intent", voice_parser_node)

            intent = working_state.get("intent", "HUMAN_ESCALATION")
            if intent != "LOAN_REQUEST":
                result = await asyncio.to_thread(
                    farma_graph.invoke,
                    working_state,
                    {"configurable": {"thread_id": From}},
                )
                working_state.update(result if isinstance(result, dict) else {})
            else:
                await _step("geocode_location", geocoding_node)
                await _step("satellite_check", satellite_analysis_node)
                await _step("aegis_risk_check", aegis_risk_check_node)
                await _step("loan_decision", narrative_orchestration_node)
                await _step("loan_decision", loan_decision_node)
                await _step("loan_decision", climate_advisory_handler)
                await _step("loan_decision", response_aggregator)
                await _step("loan_decision", sms_sender_node)

            result = working_state
            await _log_interaction("voice", From, "[voice message]", result)

            await job_store.update_job(
                job_id,
                status="completed",
                result=result,
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_completed",
                status="completed",
                step="complete",
                message="Voice pipeline completed",
            )
        except Exception as e:
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_failed",
                status="failed",
                step="error",
                message=str(e),
            )
        finally:
            try:
                file_path.unlink()
            except Exception:
                pass

    _spawn_bg_task(run_voice_job())

    return JobResponse(
        job_id=job_id,
        job_type="voice_pipeline",
        status=JobStatus.RUNNING,
        started_at=_utcnow_naive(),
        completed_at=None,
        result=None,
    )


@app.post("/api/simulate/sms", response_model=FarmerResponse, tags=["Admin"])
async def simulate_sms(phone: str, message: str):
    """
    Simulate SMS for testing/demo purposes.

    Allows admin dashboard to test the SMS workflow without
    actual Twilio integration. For demo
    """
    sms_input = {
        "input_type": "sms",
        "phone": phone,
        "message": message,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    print(f"[SIMULATE SMS] Testing from {phone}: {message[:50]}...")

    config = {"configurable": {"thread_id": phone}}
    result = await asyncio.to_thread(farma_graph.invoke, sms_input, config)

    # log for admin dashboard
    await _log_interaction("simulate", phone, message, result)

    return FarmerResponse(
        status=result.get("status", "COMPLETED"),
        intent=result.get("intent"),
        language=result.get("language"),
        parsed_data=result.get("parsed_data"),
        farmer_response=result.get("farmer_response"),
        coordinates=result.get("coordinates"),
        climate_score=result.get("climate_score"),
        final_decision=result.get("final_decision"),
        risk_flags=result.get("risk_flags"),
    )


@app.post("/api/simulate/sms/job", response_model=JobResponse, tags=["Admin"])
async def simulate_sms_job(phone: str, message: str):
    """Admin SMS simulation as a job with step events (same as farmer demo)."""
    job_id = f"SIMSMS-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(job_id, "sms_simulation", metadata={"phone": phone})
    await job_store.add_event(
        job_id,
        event_type="simulation_started",
        status="running",
        step="start",
        message="SMS simulation started",
    )

    sms_input = {
        "input_type": "sms",
        "phone": phone,
        "message": message,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    async def run_sms_job():
        try:
            from app.workflows.nodes.sms_parser import sms_parser_node
            from app.workflows.nodes.geospatial_engine import (
                geocoding_node,
                satellite_analysis_node,
            )
            from app.workflows.nodes.aegis_integration import aegis_risk_check_node
            from app.workflows.nodes.narrative_engine import (
                narrative_orchestration_node,
            )
            from app.workflows.nodes.handlers import (
                loan_decision_node,
                climate_advisory_handler,
            )
            from app.workflows.graph import response_aggregator, sms_sender_node

            working_state = dict(sms_input)

            async def _step(step: str, fn):
                await job_store.add_event(
                    job_id,
                    event_type="step_started",
                    status="running",
                    step=step,
                    message=f"Running: {step}",
                )
                state_in = dict(working_state)
                out = await asyncio.to_thread(fn, state_in)
                if isinstance(out, dict):
                    working_state.update(out)
                await job_store.add_event(
                    job_id,
                    event_type="step_completed",
                    status="completed",
                    step=step,
                    message=f"Completed: {step}",
                    payload=out if isinstance(out, dict) else {},
                )

            await _step("parse_intent", sms_parser_node)

            intent = working_state.get("intent", "HUMAN_ESCALATION")
            if intent != "LOAN_REQUEST":
                result = await asyncio.to_thread(
                    farma_graph.invoke,
                    working_state,
                    {"configurable": {"thread_id": phone}},
                )
                working_state.update(result if isinstance(result, dict) else {})
            else:
                await _step("geocode_location", geocoding_node)
                await _step("satellite_check", satellite_analysis_node)
                await _step("aegis_risk_check", aegis_risk_check_node)
                await _step("loan_decision", narrative_orchestration_node)
                await _step("loan_decision", loan_decision_node)
                await _step("loan_decision", climate_advisory_handler)
                await _step("loan_decision", response_aggregator)
                await _step("loan_decision", sms_sender_node)

            result = working_state
            await _log_interaction("simulate", phone, message, result)
            await job_store.update_job(
                job_id,
                status="completed",
                result=result,
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_completed",
                status="completed",
                step="complete",
                message="SMS simulation completed",
            )
        except Exception as e:
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_failed",
                status="failed",
                step="error",
                message=str(e),
            )

    _spawn_bg_task(run_sms_job())

    return JobResponse(
        job_id=job_id,
        job_type="sms_simulation",
        status=JobStatus.RUNNING,
        started_at=_utcnow_naive(),
        completed_at=None,
        result=None,
    )


@app.post("/api/farmer/simulate", response_model=JobResponse, tags=["Farmer"])
async def simulate_farmer_pipeline(phone: str, message: str):
    """Run farmer pipeline with job events for UI step tracking."""
    job_id = f"FARMA-{uuid.uuid4().hex[:8].upper()}"
    await job_store.create_job(job_id, "farmer_simulation", metadata={"phone": phone})
    await job_store.add_event(
        job_id,
        event_type="simulation_started",
        status="running",
        step="start",
        message="FARMA pipeline started",
    )

    sms_input = {
        "input_type": "sms",
        "phone": phone,
        "message": message,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }

    async def run_pipeline():
        try:
            from app.workflows.nodes.sms_parser import sms_parser_node
            from app.workflows.nodes.geospatial_engine import (
                geocoding_node,
                satellite_analysis_node,
            )
            from app.workflows.nodes.aegis_integration import aegis_risk_check_node
            from app.workflows.nodes.narrative_engine import (
                narrative_orchestration_node,
            )
            from app.workflows.nodes.handlers import (
                loan_decision_node,
                climate_advisory_handler,
            )
            from app.workflows.graph import response_aggregator, sms_sender_node

            working_state = dict(sms_input)

            async def _step(step: str, fn):
                await job_store.add_event(
                    job_id,
                    event_type="step_started",
                    status="running",
                    step=step,
                    message=f"Running: {step}",
                )
                # Run sync node in a worker thread so polling endpoints stay responsive.
                state_in = dict(working_state)
                out = await asyncio.to_thread(fn, state_in)
                if isinstance(out, dict):
                    working_state.update(out)
                await job_store.add_event(
                    job_id,
                    event_type="step_completed",
                    status="completed",
                    step=step,
                    message=f"Completed: {step}",
                    payload=out if isinstance(out, dict) else {},
                )

            # Step 1: Parse Intent
            await _step("parse_intent", sms_parser_node)

            intent = working_state.get("intent", "HUMAN_ESCALATION")
            if intent != "LOAN_REQUEST":
                # Fallback: run full graph synchronously for non-loan intents.
                result = await asyncio.to_thread(
                    farma_graph.invoke,
                    working_state,
                    {"configurable": {"thread_id": phone}},
                )
                working_state.update(result if isinstance(result, dict) else {})
            else:
                # Step 2: Geocode Location
                await _step("geocode_location", geocoding_node)
                # Step 3: Satellite Check
                await _step("satellite_check", satellite_analysis_node)
                # Step 4: AEGIS Risk Check
                await _step("aegis_risk_check", aegis_risk_check_node)
                # Step 5: Loan Decision (includes narrative + advisory + response aggregation)
                await _step("loan_decision", narrative_orchestration_node)
                await _step("loan_decision", loan_decision_node)
                await _step("loan_decision", climate_advisory_handler)
                await _step("loan_decision", response_aggregator)
                await _step("loan_decision", sms_sender_node)

            result = working_state

            await _log_interaction("simulate", phone, message, result)
            await job_store.update_job(
                job_id, status="completed", result=result, completed_at=_utcnow_naive()
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_completed",
                status="completed",
                step="complete",
                message="FARMA pipeline completed",
            )
        except Exception as e:
            await job_store.update_job(
                job_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                job_id,
                event_type="simulation_failed",
                status="failed",
                step="error",
                message=str(e),
            )

    _spawn_bg_task(run_pipeline())

    return JobResponse(
        job_id=job_id,
        job_type="farmer_simulation",
        status=JobStatus.RUNNING,
        started_at=_utcnow_naive(),
        completed_at=None,
        result=None,
    )


# admin endpoints
@app.get("/api/admin/interactions", tags=["Admin"])
async def get_farmer_interactions(
    limit: int = 50,
    intent: Optional[str] = None,
    decision: Optional[str] = None,
):
    """
    Get recent farmer interactions for admin dashboard.

    Filter by:
    - intent: LOAN_REQUEST, DISEASE_REPORT, WEATHER_INQUIRY
    - decision: APPROVED, REJECTED, HELD, REVIEW
    """
    from app.aegis.db.connection import get_async_session
    from app.utils.job_store import FarmerInteraction
    from sqlalchemy import select, desc

    async with get_async_session() as session:
        stmt = select(FarmerInteraction).order_by(desc(FarmerInteraction.created_at))
        if intent:
            stmt = stmt.where(FarmerInteraction.intent == intent)
        if decision:
            stmt = stmt.where(FarmerInteraction.final_decision == decision)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    interactions = []
    for row in rows:
        details = row.details or {}
        interactions.append(
            {
                "id": row.id,
                "timestamp": row.created_at.isoformat(),
                "input_type": row.input_type,
                "phone": row.phone,
                "message": row.message,
                "intent": row.intent,
                "language": row.language,
                "status": row.status,
                "final_decision": row.final_decision,
                "climate_score": row.climate_score,
                "risk_flags": (
                    details.get("risk_flags") if isinstance(details, dict) else None
                )
                or [],
                "farmer_response": row.farmer_response,
            }
        )

    return {
        "interactions": interactions,
        "total": len(interactions),
        "filters_applied": {"intent": intent, "decision": decision},
    }


@app.get("/api/admin/interactions/{interaction_id}", tags=["Admin"])
async def get_interaction_detail(interaction_id: int):
    """Get detailed view of a single farmer interaction."""
    from app.aegis.db.connection import get_async_session
    from app.utils.job_store import FarmerInteraction

    async with get_async_session() as session:
        row = await session.get(FarmerInteraction, interaction_id)

    if row:
        details = row.details or {}
        return {
            "id": row.id,
            "timestamp": row.created_at.isoformat(),
            "input_type": row.input_type,
            "phone": row.phone,
            "message": row.message,
            "intent": row.intent,
            "language": row.language,
            "status": row.status,
            "final_decision": row.final_decision,
            "climate_score": row.climate_score,
            "risk_flags": (
                details.get("risk_flags") if isinstance(details, dict) else None
            )
            or [],
            "farmer_response": row.farmer_response,
            "details": details,
        }

    raise HTTPException(
        status_code=404, detail=f"Interaction {interaction_id} not found"
    )


@app.get("/api/admin/stats", tags=["Admin"])
async def get_admin_stats():
    """
    Get statistics for admin dashboard.

    Returns counts by intent, decision, and recent activity.
    """
    from app.aegis.db.connection import get_async_session
    from app.utils.job_store import FarmerInteraction
    from sqlalchemy import select, desc

    # Count by intent
    intent_counts = {}
    decision_counts = {}
    language_counts = {}

    async with get_async_session() as session:
        result = await session.execute(
            select(FarmerInteraction)
            .order_by(desc(FarmerInteraction.created_at))
            .limit(100)
        )
        rows = result.scalars().all()

    for row in rows:
        intent_v = row.intent or "UNKNOWN"
        decision_v = row.final_decision or "PENDING"
        language_v = row.language or "Unknown"

        intent_counts[intent_v] = intent_counts.get(intent_v, 0) + 1
        decision_counts[decision_v] = decision_counts.get(decision_v, 0) + 1
        language_counts[language_v] = language_counts.get(language_v, 0) + 1

    # calculate approval rate for loans
    loan_rows = [r for r in rows if r.intent == "LOAN_REQUEST"]
    approved = len([r for r in loan_rows if r.final_decision == "APPROVED"])
    approval_rate = (approved / len(loan_rows) * 100) if loan_rows else 0

    return {
        "total_interactions": len(rows),
        "by_intent": intent_counts,
        "by_decision": decision_counts,
        "by_language": language_counts,
        "loan_stats": {
            "total_applications": len(loan_rows),
            "approved": approved,
            "approval_rate_percent": round(approval_rate, 1),
        },
        "aegis_stats": {
            "total_reports": len(list(REPORTS_DIR.glob("*.pdf"))),
            "focus_states": AEGIS_FOCUS_STATES,
        },
    }


@app.get("/api/admin/activity", tags=["Admin"])
async def get_recent_activity(limit: int = 20):
    """
    Get recent system activity feed for dashboard.

    Combines farmer interactions and AEGIS jobs.
    """
    from app.aegis.db.connection import get_async_session
    from app.utils.job_store import FarmerInteraction, JobRun
    from sqlalchemy import select, desc

    activities = []

    # add farmer interactions
    async with get_async_session() as session:
        i_result = await session.execute(
            select(FarmerInteraction)
            .order_by(desc(FarmerInteraction.created_at))
            .limit(limit)
        )
        interactions = i_result.scalars().all()

        j_result = await session.execute(
            select(JobRun)
            .where(JobRun.job_type.in_(["aegis_scan", "aegis_report"]))
            .order_by(desc(JobRun.created_at))
            .limit(10)
        )
        recent_jobs = j_result.scalars().all()

    for row in interactions:
        details = row.details or {}
        interaction = {
            "id": row.id,
            "timestamp": row.created_at.isoformat(),
            "input_type": row.input_type,
            "phone": row.phone,
            "message": row.message,
            "intent": row.intent,
            "language": row.language,
            "status": row.status,
            "final_decision": row.final_decision,
            "climate_score": row.climate_score,
            "risk_flags": (
                details.get("risk_flags") if isinstance(details, dict) else None
            )
            or [],
            "farmer_response": row.farmer_response,
        }
        activities.append(
            {
                "type": "farmer_interaction",
                "timestamp": interaction.get("timestamp"),
                "summary": f"{interaction.get('intent', 'Unknown')} from {interaction.get('phone', 'Unknown')[:6]}***",
                "status": interaction.get("final_decision")
                or interaction.get("status"),
                "details": interaction,
            }
        )

    for job in recent_jobs:
        job_type = "report" if job.job_id.startswith("RPT") else "scan"
        activities.append(
            {
                "type": f"aegis_{job_type}",
                "timestamp": (
                    (job.started_at or job.created_at).isoformat()
                    if (job.started_at or job.created_at)
                    else None
                ),
                "summary": f"AEGIS {job_type}: {job.job_id}",
                "status": job.status,
                "details": {
                    "job_id": job.job_id,
                    "status": job.status,
                    "started_at": (
                        job.started_at.isoformat() if job.started_at else None
                    ),
                    "completed_at": (
                        job.completed_at.isoformat() if job.completed_at else None
                    ),
                    "result": job.result,
                    "metadata": job.job_metadata,
                },
            }
        )

    # Sort by timestamp
    activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {"activities": activities[:limit], "total": len(activities)}


# aegis endpoints


@app.get("/api/aegis/dashboard", response_model=AegisDashboardResponse, tags=["AEGIS"])
async def get_aegis_dashboard():
    """
    Get AEGIS dashboard overview.

    Returns:
    - Latest scan status
    - State intelligence summaries
    - Recent alerts
    """
    from app.aegis.db.connection import get_async_session
    from app.aegis.db.models import AegisScan, StateIntelligence
    from sqlalchemy import select, desc

    try:
        async with get_async_session() as session:
            # get latest scan/data collation
            latest_scan_result = await session.execute(
                select(AegisScan).order_by(desc(AegisScan.started_at)).limit(1)
            )
            latest_scan = latest_scan_result.scalar_one_or_none()

            # Count total scans
            scan_count_result = await session.execute(select(AegisScan))
            total_scans = len(scan_count_result.scalars().all())

            # Get state summaries from latest scan
            state_summaries = []
            if latest_scan:
                state_intel_result = await session.execute(
                    select(StateIntelligence).where(
                        StateIntelligence.scan_id == latest_scan.id
                    )
                )
                for intel in state_intel_result.scalars().all():
                    priority_level, priority_score = _priority_from_intel(intel)
                    state_summaries.append(
                        StateIntelligenceSummary(
                            state_name=intel.state_name,
                            conflict_events=intel.conflict_events_count,
                            idp_estimate=intel.idp_estimate,
                            idp_trend=intel.idp_trend,
                            food_insecurity_level=intel.food_insecurity_level,
                            ipc_phase=intel.ipc_phase,
                            markets_operational=intel.markets_operational,
                            priority_level=priority_level,
                            priority_score=priority_score,
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
                recent_alerts=[],  # TODO: Implement alerts
            )

    except Exception as e:
        # return minimal dashboard if db not available
        return AegisDashboardResponse(
            latest_scan=None,
            total_scans=0,
            total_reports=len(list(REPORTS_DIR.glob("*.pdf"))),
            focus_states=AEGIS_FOCUS_STATES,
            state_summaries=[],
            recent_alerts=[],
        )


@app.post("/api/aegis/scan", response_model=AegisScanResponse, tags=["AEGIS"])
async def trigger_aegis_scan(
    request: AegisScanRequest,
):
    """
    Trigger an AEGIS data collection scan.

    Collects:
    - ACLED conflict data
    - IOM DTM displacement data
    - Economic indicators
    - Trend analysis

    Runs in background, poll /api/aegis/scan/{scan_id} for status.
    """
    from app.aegis.graph import run_aegis_scan
    import uuid

    states = request.states or AEGIS_FOCUS_STATES
    days_back = getattr(request, "days_back", 7) or 7
    run_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"
    scan_db_id = 0
    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisScan

        async with get_async_session() as session:
            scan = AegisScan(
                run_id=run_id,
                started_at=_utcnow_naive(),
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
        metadata={"states": states, "scan_id": scan_db_id, "days_back": days_back},
    )
    await job_store.add_event(
        run_id,
        event_type="scan_started",
        status="running",
        step="scan_start",
        message="AEGIS scan started",
        payload={"states": states},
    )

    # run scan in background
    async def run_scan_background():
        try:
            result = await run_aegis_scan(
                states=states,
                days_back=days_back,
                force=request.force_refresh,
                run_id=run_id,
                scan_id=scan_db_id,
            )
            await job_store.update_job(
                run_id,
                status="completed",
                result=result,
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="scan_completed",
                status="completed",
                step="scan_complete",
                message="AEGIS scan completed",
            )
        except Exception as e:
            await job_store.update_job(
                run_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="scan_failed",
                status="failed",
                step="scan_error",
                message=str(e),
            )

    _spawn_bg_task(run_scan_background())

    return AegisScanResponse(
        scan_id=scan_db_id or 0,
        run_id=run_id,
        status=ScanStatus.RUNNING,
        states_to_scan=states,
        message=f"Scan initiated. Poll /api/aegis/scan/{run_id} for status.",
    )


@app.post("/api/aegis/synthesis", response_model=AegisSynthesisResponse, tags=["AEGIS"])
async def trigger_aegis_synthesis(request: AegisSynthesisRequest):
    """Trigger deterministic synthesis over a completed scan (fanout DAG).

    Synthesis is its own stage (not hidden inside report generation).
    Poll job events via /api/jobs/{run_id}/events.
    """
    from app.aegis.synthesis.runner import run_synthesis_dag

    scan_id = int(request.scan_id)
    run_id = f"SYNTH-{uuid.uuid4().hex[:8].upper()}"

    # Determine which states to synthesize: explicit list or whatever exists for the scan.
    states = request.states
    if not states:
        try:
            from app.aegis.db.connection import get_async_session
            from app.aegis.db.models import StateIntelligence
            from sqlalchemy import select

            async with get_async_session() as session:
                res = await session.execute(
                    select(StateIntelligence.state_name).where(
                        StateIntelligence.scan_id == scan_id
                    )
                )
                states = sorted({row[0] for row in res.all() if row and row[0]})
        except Exception:
            states = []

    await job_store.create_job(
        run_id,
        "aegis_synthesis",
        metadata={"scan_id": scan_id, "states": states or []},
    )
    await job_store.add_event(
        run_id,
        event_type="synthesis_started",
        status="running",
        step="synthesis_start",
        message="AEGIS synthesis started",
        payload={"scan_id": scan_id, "states": states or []},
    )

    async def run_synthesis_background():
        try:
            result = await run_synthesis_dag(
                scan_id=scan_id,
                states=states or [],
                run_id=run_id,
                emit_job_events=True,
            )
            await job_store.update_job(
                run_id,
                status="completed",
                result=result,
                completed_at=_utcnow_naive(),
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
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                run_id,
                event_type="synthesis_failed",
                status="failed",
                step="synthesis_error",
                message=str(e),
                payload={"scan_id": scan_id},
            )

    _spawn_bg_task(run_synthesis_background())

    return AegisSynthesisResponse(
        run_id=run_id,
        status="running",
        message=f"Synthesis initiated. Poll /api/jobs/{run_id}/events for status.",
    )


@app.get(
    "/api/aegis/scan/{scan_id}", response_model=AegisScanStatusResponse, tags=["AEGIS"]
)
async def get_scan_status(scan_id: str):
    """Get status of an AEGIS scan."""
    job = await job_store.get_job(scan_id)
    if job:
        status = job.get("status", "running")
        metadata = job.get("metadata") or {}
        result = job.get("result") or {}
        scan_id_value = metadata.get("scan_id") or result.get("scan_id") or 0
        result = job.get("result") or {}
        summaries = None
        conflict_events = None
        lga_risk = None
        # For UI progress (map/cards), expose whatever has already been persisted,
        # even while the job is still running.
        if scan_id_value:
            try:
                summaries, conflict_events, lga_risk = await _summaries_for_scan(
                    scan_id_value
                )
            except Exception:
                summaries, conflict_events, lga_risk = None, None, None
        return AegisScanStatusResponse(
            scan_id=scan_id_value or 0,
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

    # Check database
    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisScan
        from sqlalchemy import select

        async with get_async_session() as session:
            # Try by run_id first, then by id
            result = await session.execute(
                select(AegisScan).where(AegisScan.run_id == scan_id)
            )
            scan = result.scalar_one_or_none()

            if not scan and scan_id.isdigit():
                result = await session.execute(
                    select(AegisScan).where(AegisScan.id == int(scan_id))
                )
                scan = result.scalar_one_or_none()

            if not scan:
                raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")

            summaries, conflict_events, lga_risk = await _summaries_for_scan(scan.id)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/aegis/report", response_model=AegisReportResponse, tags=["AEGIS"])
async def generate_aegis_report(
    request: AegisReportRequest,
):
    """
    Generate AEGIS PDF report from scan data.

    Uses:
    - Gemini 3 Pro for narrative generation
    - Gemini 3 Pro Image Preview for infographics
    - ReportLab for PDF assembly

    Runs in background, poll /api/aegis/report/{report_id} for status.
    """
    from app.aegis.report import run_report_dag
    import uuid

    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    # If states are omitted, the report DAG will infer scanned states for this scan_id.
    states = request.states or []
    await job_store.create_job(
        report_id,
        "aegis_report",
        metadata={"scan_id": request.scan_id, "states": states},
    )
    await job_store.add_event(
        report_id,
        event_type="report_started",
        status="running",
        step="report_start",
        message="AEGIS report generation started",
    )

    async def generate_report_background():
        try:
            report_result = await run_report_dag(
                report_id=report_id,
                scan_id=int(request.scan_id),
                states=states,
                include_infographics=request.include_infographics,
                include_annexes=request.include_annexes,
                output_dir=str(REPORTS_DIR),
            )

            await job_store.update_job(
                report_id,
                status="completed",
                result={
                    "pdf_path": report_result.get("pdf_path"),
                    "gcs_key": report_result.get("gcs_key"),
                    "states_analyzed": report_result.get("states_analyzed", []),
                    "sources_cited": int(report_result.get("sources_cited") or 0),
                },
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                report_id,
                event_type="report_completed",
                status="completed",
                step="report_complete",
                message="Report PDF ready",
                payload={"pdf_path": report_result.get("pdf_path")},
            )

        except Exception as e:
            await job_store.update_job(
                report_id,
                status="failed",
                result={"error": str(e)},
                completed_at=_utcnow_naive(),
            )
            await job_store.add_event(
                report_id,
                event_type="report_failed",
                status="failed",
                step="report_error",
                message=str(e),
            )

    _spawn_bg_task(generate_report_background())

    return AegisReportResponse(
        report_id=report_id,
        status=ReportStatus.RUNNING,
        message=f"Report generation started. Poll /api/aegis/report/{report_id} for status.",
    )


@app.get(
    "/api/aegis/report/{report_id}",
    response_model=AegisReportStatusResponse,
    tags=["AEGIS"],
)
async def get_report_status(report_id: str):
    """Get status of report generation."""
    stored = await job_store.get_job(report_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    def _iso(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return value.isoformat()
        except Exception:
            return str(value)

    # Derive step progress (and, if needed, completion) from persisted job events.
    steps_completed: list[str] = []
    derived_status: str = str(stored.get("status") or "running")
    derived_error: Optional[str] = None
    derived_pdf_path: Optional[str] = (stored.get("result") or {}).get("pdf_path")
    try:
        events = await job_store.list_events(report_id)
        steps_completed = [
            str(e.get("event_type")) for e in events if e.get("event_type")
        ]
        completed_event = next(
            (e for e in reversed(events) if e.get("event_type") == "report_completed"),
            None,
        )
        pdf_built_event = next(
            (
                e
                for e in reversed(events)
                if e.get("event_type") == "pdf_build_completed"
            ),
            None,
        )
        failed_event = next(
            (
                e
                for e in reversed(events)
                if e.get("event_type") in ("report_failed", "report_error")
            ),
            None,
        )

        if derived_status in ("running", "pending") and completed_event:
            derived_status = "completed"
            payload = completed_event.get("payload") or {}
            if not derived_pdf_path and isinstance(payload, dict):
                derived_pdf_path = payload.get("pdf_path")
        # If a PDF was built, expose it even if final persistence failed.
        if not derived_pdf_path and pdf_built_event:
            payload = pdf_built_event.get("payload") or {}
            if isinstance(payload, dict):
                derived_pdf_path = payload.get("pdf_path")
        if derived_status in ("running", "pending") and failed_event:
            derived_status = "failed"
            derived_error = failed_event.get("message")
    except Exception:
        steps_completed = []

    job = {
        "status": derived_status,
        "started_at": _iso(stored.get("started_at")),
        "completed_at": _iso(stored.get("completed_at")),
        "pdf_path": derived_pdf_path,
        "steps_completed": steps_completed,
        "timings": {},
        "states_analyzed": (stored.get("result") or {}).get("states_analyzed", []),
        "sources_cited": (stored.get("result") or {}).get("sources_cited", 0),
        "infographics_generated": 0,
        "error": derived_error,
    }

    download_url = None
    if job.get("pdf_path"):
        pdf_filename = Path(job["pdf_path"]).name
        download_url = f"/api/aegis/report/{report_id}/download"

    status_value = job.get("status", "running")
    # Normalize job-store status to ReportStatus enum values.
    if status_value == "failed":
        status_value = "error"
    if status_value not in ("running", "completed", "error", "pending"):
        status_value = "running"

    return AegisReportStatusResponse(
        report_id=report_id,
        status=ReportStatus(status_value),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        pdf_path=job.get("pdf_path"),
        download_url=download_url,
        steps_completed=job.get("steps_completed", []),
        timings=job.get("timings", {}),
        error=job.get("error"),
        states_analyzed=job.get("states_analyzed", []),
        sources_cited=job.get("sources_cited", 0),
        infographics_generated=job.get("infographics_generated", 0),
    )


@app.get("/api/aegis/report/{report_id}/download", tags=["AEGIS"])
async def download_report(report_id: str):
    """Download generated PDF report."""
    stored = await job_store.get_job(report_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    job = {
        "status": stored.get("status"),
        "pdf_path": (stored.get("result") or {}).get("pdf_path"),
        "gcs_key": (stored.get("result") or {}).get("gcs_key"),
    }

    if job.get("status") != "completed":
        # If the status row is stale (transient DB errors), fall back to events.
        try:
            events = await job_store.list_events(report_id)
            completed = any(e.get("event_type") == "report_completed" for e in events)
            # Allow download if the PDF was built, even if final persistence failed.
            if not completed:
                built = next(
                    (
                        e
                        for e in reversed(events)
                        if e.get("event_type") == "pdf_build_completed"
                    ),
                    None,
                )
                if built:
                    payload = built.get("payload") or {}
                    if isinstance(payload, dict) and payload.get("pdf_path"):
                        job["pdf_path"] = payload.get("pdf_path")
                        completed = True
            if not completed:
                raise HTTPException(status_code=400, detail="Report not yet completed")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Report not yet completed")

    pdf_path = job.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        # Cloud Run instances are ephemeral: fall back to GCS if configured.
        try:
            from app.utils.gcs_store import download_bytes
            from app.config import GCS_BUCKET, GCS_REPORT_PREFIX

            gcs_key = job.get("gcs_key")
            if not gcs_key and pdf_path:
                gcs_key = GCS_REPORT_PREFIX + Path(pdf_path).name
            if gcs_key:
                data = download_bytes(GCS_BUCKET, gcs_key)
                filename = Path(gcs_key).name
                return Response(
                    content=data,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
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


@app.get("/api/aegis/reports", tags=["AEGIS"])
async def list_reports():
    """List all generated reports."""
    reports = []
    # Prefer GCS in production; fall back to local for dev.
    try:
        from app.utils.gcs_store import list_objects
        from app.config import GCS_BUCKET, GCS_REPORT_PREFIX

        objs = list_objects(GCS_BUCKET, GCS_REPORT_PREFIX)
        for obj in objs:
            name = obj.get("name") or ""
            if not name.endswith(".pdf"):
                continue
            filename = Path(name).name
            # Filename format includes report_id at the end: ..._{report_id}.pdf
            report_id = filename.rsplit("_", 1)[-1].replace(".pdf", "")
            updated = obj.get("updated")
            if hasattr(updated, "isoformat"):
                created_at = updated.isoformat()
            else:
                # local fallback uses epoch seconds
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
                    "created_at": datetime.fromtimestamp(
                        pdf_file.stat().st_mtime
                    ).isoformat(),
                    "size_bytes": pdf_file.stat().st_size,
                    "download_url": f"/static/reports/{pdf_file.name}",
                }
            )

    # Sort by creation time, newest first
    reports.sort(key=lambda x: x["created_at"], reverse=True)

    return {"reports": reports, "total": len(reports)}


@app.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
async def get_job(job_id: str):
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    status = job.get("status", "running")
    try:
        status_enum = JobStatus(status)
    except Exception:
        status_enum = JobStatus.RUNNING
    return JobResponse(
        job_id=job.get("job_id"),
        job_type=job.get("job_type", "unknown"),
        status=status_enum,
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        result=job.get("result"),
    )


@app.get("/api/jobs/{job_id}/events", response_model=JobEventsResponse, tags=["Jobs"])
async def get_job_events(job_id: str):
    events = await job_store.list_events(job_id)
    return JobEventsResponse(job_id=job_id, events=events)


# util endpoints
@app.get("/api/states", tags=["Utility"])
def get_supported_states():
    """Get list of supported Nigerian states."""
    return {
        "focus_states": AEGIS_FOCUS_STATES,
        "description": "North East Nigeria - States with complete DTM and ACLED coverage",
        "all_nigerian_states": [
            "Abia",
            "Adamawa",
            "Akwa Ibom",
            "Anambra",
            "Bauchi",
            "Bayelsa",
            "Benue",
            "Borno",
            "Cross River",
            "Delta",
            "Ebonyi",
            "Edo",
            "Ekiti",
            "Enugu",
            "Gombe",
            "Imo",
            "Jigawa",
            "Kaduna",
            "Kano",
            "Katsina",
            "Kebbi",
            "Kogi",
            "Kwara",
            "Lagos",
            "Nasarawa",
            "Niger",
            "Ogun",
            "Ondo",
            "Osun",
            "Oyo",
            "Plateau",
            "Rivers",
            "Sokoto",
            "Taraba",
            "Yobe",
            "Zamfara",
            "FCT",
        ],
    }


@app.get("/api/crops", tags=["Utility"])
def get_supported_crops():
    """Get list of supported crop types."""
    return {
        "crops": [
            {"name": "Maize", "local_names": ["Agbado", "Masara"]},
            {"name": "Rice", "local_names": ["Shinkafa", "Iresi"]},
            {"name": "Beans", "local_names": ["Wake", "Ere"]},
            {"name": "Sorghum", "local_names": ["Dawa", "Oka-baba"]},
            {"name": "Millet", "local_names": ["Gero", "Jero"]},
            {"name": "Cassava", "local_names": ["Rogo", "Ege"]},
            {"name": "Yam", "local_names": ["Doya", "Isu"]},
            {"name": "Groundnut", "local_names": ["Gyada", "Epa"]},
        ]
    }
