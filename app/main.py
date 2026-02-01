"""Farma API - Main application entry point.

Exposes endpoints for:
1. Farmer interactions (SMS, Voice, loan applications monitoring)
2. AEGIS intelligence system (scans, reports, dashboard)
3. System health and monitoring
"""

import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.workflows.graph import farma_graph
from app.api.schemas import (
    FarmerResponse,
    AegisScanRequest,
    AegisScanResponse,
    AegisScanStatusResponse,
    AegisReportRequest,
    AegisReportResponse,
    AegisReportStatusResponse,
    AegisDashboardResponse,
    StateIntelligenceSummary,
    HealthResponse,
    ScanStatus,
    ReportStatus,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TMP_DIR = BASE_DIR / "tmp_audio"
REPORTS_DIR = BASE_DIR / "reports"
TMP_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# in memory storing of operations todo- use DB in production
_active_jobs: dict = {}
_farmer_interactions: list = []  # store recent farmer interactions for admin view
_interaction_limit = 100  # Keep last 100 interactions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup
    print("[FARMA] Starting up...")
    print(f"[FARMA] Reports directory: {REPORTS_DIR}")
    yield
    # Shutdown
    print("[FARMA] Shutting down...")


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


# farmer
def _log_interaction(input_type: str, phone: str, message: str, result: dict):
    """Log farmer interaction for admin dashboard."""
    interaction = {
        "id": len(_farmer_interactions) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_type": input_type,
        "phone": phone,
        "message": message if message else None,
        "intent": result.get("intent"),
        "language": result.get("language"),
        "status": result.get("status"),
        "final_decision": result.get("final_decision"),
        "climate_score": result.get("climate_score"),
        "risk_flags": result.get("risk_flags", []),
        "farmer_response": result.get("farmer_response"),
    }
    _farmer_interactions.insert(0, interaction)  # newest to oldest
    # keep only recent interactions
    while len(_farmer_interactions) > _interaction_limit:
        _farmer_interactions.pop()


@app.post("/api/sms", response_model=FarmerResponse, tags=["Farmer"])
def receive_sms(From: str = Form(...), Body: str = Form(...)):
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
    result = farma_graph.invoke(sms_input, config=config)

    # log for admin dashboard
    _log_interaction("sms", From, Body, result)

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
    result = farma_graph.invoke(voice_input, config=config)

    # log for admin dashboard
    _log_interaction(
        "voice", From, result.get("message", "[voice transcription]"), result
    )

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


@app.post("/api/simulate/sms", response_model=FarmerResponse, tags=["Admin"])
def simulate_sms(phone: str, message: str):
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
    result = farma_graph.invoke(sms_input, config=config)

    # log for admin dashboard
    _log_interaction("simulate", phone, message, result)

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


# admin endpoints
@app.get("/api/admin/interactions", tags=["Admin"])
def get_farmer_interactions(
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
    interactions = _farmer_interactions[:limit]

    if intent:
        interactions = [i for i in interactions if i.get("intent") == intent]

    if decision:
        interactions = [i for i in interactions if i.get("final_decision") == decision]

    return {
        "interactions": interactions,
        "total": len(interactions),
        "filters_applied": {"intent": intent, "decision": decision},
    }


@app.get("/api/admin/interactions/{interaction_id}", tags=["Admin"])
def get_interaction_detail(interaction_id: int):
    """Get detailed view of a single farmer interaction."""
    for interaction in _farmer_interactions:
        if interaction.get("id") == interaction_id:
            return interaction

    raise HTTPException(
        status_code=404, detail=f"Interaction {interaction_id} not found"
    )


@app.get("/api/admin/stats", tags=["Admin"])
def get_admin_stats():
    """
    Get statistics for admin dashboard.

    Returns counts by intent, decision, and recent activity.
    """
    # Count by intent
    intent_counts = {}
    decision_counts = {}
    language_counts = {}

    for interaction in _farmer_interactions:
        intent = interaction.get("intent") or "UNKNOWN"
        decision = interaction.get("final_decision") or "PENDING"
        language = interaction.get("language") or "Unknown"

        intent_counts[intent] = intent_counts.get(intent, 0) + 1
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        language_counts[language] = language_counts.get(language, 0) + 1

    # calculate approval rate for loans
    loan_interactions = [
        i for i in _farmer_interactions if i.get("intent") == "LOAN_REQUEST"
    ]
    approved = len(
        [i for i in loan_interactions if i.get("final_decision") == "APPROVED"]
    )
    approval_rate = (
        (approved / len(loan_interactions) * 100) if loan_interactions else 0
    )

    return {
        "total_interactions": len(_farmer_interactions),
        "by_intent": intent_counts,
        "by_decision": decision_counts,
        "by_language": language_counts,
        "loan_stats": {
            "total_applications": len(loan_interactions),
            "approved": approved,
            "approval_rate_percent": round(approval_rate, 1),
        },
        "aegis_stats": {
            "total_reports": len(list(REPORTS_DIR.glob("*.pdf"))),
            "focus_states": AEGIS_FOCUS_STATES,
        },
    }


@app.get("/api/admin/activity", tags=["Admin"])
def get_recent_activity(limit: int = 20):
    """
    Get recent system activity feed for dashboard.

    Combines farmer interactions and AEGIS jobs.
    """
    activities = []

    # add farmer interactions
    for interaction in _farmer_interactions[:limit]:
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

    # add AEGIS jobs
    for job_id, job in list(_active_jobs.items())[:10]:
        job_type = "report" if job_id.startswith("RPT") else "scan"
        activities.append(
            {
                "type": f"aegis_{job_type}",
                "timestamp": job.get("started_at") or job.get("completed_at"),
                "summary": f"AEGIS {job_type}: {job_id}",
                "status": job.get("status"),
                "details": {"job_id": job_id, **job},
            }
        )

    # Sort by timestamp
    activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {"activities": activities[:limit], "total": len(activities)}


# ============================================================
# AEGIS ENDPOINTS
# ============================================================

# North East focus states (have complete DTM + ACLED data)
AEGIS_FOCUS_STATES = ["Borno", "Adamawa", "Yobe", "Bauchi", "Gombe", "Taraba"]


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
                    state_summaries.append(
                        StateIntelligenceSummary(
                            state_name=intel.state_name,
                            conflict_events=intel.conflict_events_count,
                            idp_estimate=intel.idp_estimate,
                            idp_trend=intel.idp_trend,
                            food_insecurity_level=intel.food_insecurity_level,
                            ipc_phase=intel.ipc_phase,
                            markets_operational=intel.markets_operational,
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
    background_tasks: BackgroundTasks,
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
    run_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"

    # Create scan record
    scan_id = hash(run_id) % 100000  # temporary id and will be reassigned by db

    # run scan in background
    async def run_scan_background():
        try:
            _active_jobs[run_id] = {
                "status": "running",
                "started_at": datetime.now(timezone.utc),
            }
            result = await run_aegis_scan(states=states)
            _active_jobs[run_id] = {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "result": result,
            }
        except Exception as e:
            _active_jobs[run_id] = {
                "status": "failed",
                "error": str(e),
                "completed_at": datetime.now(timezone.utc),
            }

    background_tasks.add_task(run_scan_background)

    return AegisScanResponse(
        scan_id=scan_id,
        run_id=run_id,
        status=ScanStatus.RUNNING,
        states_to_scan=states,
        message=f"Scan initiated. Poll /api/aegis/scan/{run_id} for status.",
    )


@app.get(
    "/api/aegis/scan/{scan_id}", response_model=AegisScanStatusResponse, tags=["AEGIS"]
)
async def get_scan_status(scan_id: str):
    """Get status of an AEGIS scan."""
    # Check in-memory jobs first
    if scan_id in _active_jobs:
        job = _active_jobs[scan_id]
        return AegisScanStatusResponse(
            scan_id=0,
            run_id=scan_id,
            status=ScanStatus(job.get("status", "running")),
            started_at=job.get("started_at", datetime.now(timezone.utc)),
            completed_at=job.get("completed_at"),
            states_scanned=0,
            total_events=0,
            total_fatalities=0,
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

            return AegisScanStatusResponse(
                scan_id=scan.id,
                run_id=scan.run_id,
                status=ScanStatus(scan.status),
                started_at=scan.started_at,
                completed_at=scan.completed_at,
                states_scanned=scan.states_scanned,
                total_events=scan.total_events,
                total_fatalities=scan.total_fatalities,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/aegis/report", response_model=AegisReportResponse, tags=["AEGIS"])
async def generate_aegis_report(
    request: AegisReportRequest,
    background_tasks: BackgroundTasks,
):
    """
    Generate AEGIS PDF report from scan data.

    Uses:
    - Gemini 3 Pro for narrative generation
    - Gemini 3 Pro Image Preview for infographics
    - ReportLab for PDF assembly

    Runs in background, poll /api/aegis/report/{report_id} for status.
    """
    from app.aegis.synthesis.agent import run_synthesis
    from app.aegis.report import run_report_generation
    import uuid

    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    states = request.states or AEGIS_FOCUS_STATES

    async def generate_report_background():
        try:
            _active_jobs[report_id] = {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "steps_completed": [],
            }

            # Step 1: Run synthesis
            _active_jobs[report_id]["steps_completed"].append("synthesis_started")
            synthesis_state = await run_synthesis(
                scan_id=request.scan_id, states=states
            )

            if synthesis_state.get("status") != "completed":
                raise RuntimeError(f"Synthesis failed: {synthesis_state.get('error')}")

            _active_jobs[report_id]["steps_completed"].append("synthesis_completed")

            # Step 2: Generate report
            _active_jobs[report_id]["steps_completed"].append(
                "report_generation_started"
            )
            report_state = await run_report_generation(
                synthesis_state=synthesis_state,
                output_dir=str(REPORTS_DIR),
                include_infographics=request.include_infographics,
                include_annexes=request.include_annexes,
            )

            _active_jobs[report_id].update(
                {
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "pdf_path": report_state.pdf_path,
                    "steps_completed": report_state.steps_completed,
                    "timings": report_state.timings,
                    "states_analyzed": (
                        report_state.report_data.regional.states_analyzed
                        if report_state.report_data
                        else []
                    ),
                    "sources_cited": (
                        len(report_state.report_data.all_source_uris)
                        if report_state.report_data
                        else 0
                    ),
                    "infographics_generated": len(report_state.infographics),
                }
            )

        except Exception as e:
            _active_jobs[report_id].update(
                {
                    "status": "error",
                    "error": str(e),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    background_tasks.add_task(generate_report_background)

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
    if report_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    job = _active_jobs[report_id]

    download_url = None
    if job.get("pdf_path"):
        pdf_filename = Path(job["pdf_path"]).name
        download_url = f"/api/aegis/report/{report_id}/download"

    return AegisReportStatusResponse(
        report_id=report_id,
        status=ReportStatus(job.get("status", "running")),
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
    if report_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    job = _active_jobs[report_id]

    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report not yet completed")

    pdf_path = job.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
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
