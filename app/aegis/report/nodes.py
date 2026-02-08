"""Node implementations for the report graph workflow.

Purpose:
- Load synthesis/simulation artifacts from DB.
- Build normalized report data payload.
- Generate narrative, infographics, and PDF.
- Persist report metadata and optional GCS artifact location.

Used by:
- `app.aegis.report.graph`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer
from sqlalchemy import select

from app.aegis.db.connection import get_async_session
from app.aegis.db.models import AegisScan, StateIntelligence
from app.aegis.report.config import ReportDAGConfig
from app.aegis.report.infographics import generate_all_infographics
from app.aegis.report.narrative import generate_narrative_llm, render_template_narrative
from app.aegis.report.pdf import PDFConfig, build_pdf
from app.aegis.report.persist import create_report_row, mark_report_completed
from app.aegis.report.report_data import ReportData, ReportInputs, utcnow_iso


def _emit(
    event: str,
    *,
    status: str = "running",
    step: str = "report",
    message: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """Emit report custom event into LangGraph stream writer."""
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


async def load_report_inputs(state: Dict[str, Any]) -> Dict[str, Any]:
    """Load scan rollup and per-state synthesis assessments for report run."""
    report_id = state["report_id"]
    scan_id = int(state["scan_id"])
    states = state.get("states") or []
    simulation_id = state.get("simulation_id")

    _emit(
        "report_started",
        step="report_start",
        message="AEGIS report started",
        payload={"scan_id": scan_id, "states": states},
    )

    async with get_async_session() as session:
        scan = await session.get(AegisScan, scan_id)
        if not scan:
            raise RuntimeError(f"Scan {scan_id} not found")
        if not scan.rollup_json:
            raise RuntimeError(
                "Missing synthesis rollup_json. Run POST /api/aegis/synthesis first."
            )

        # if states not provided, infer from DB for this scan.
        if not states:
            res = await session.execute(
                select(StateIntelligence.state_name).where(
                    StateIntelligence.scan_id == scan_id
                )
            )
            states = [r[0] for r in res.all() if r and r[0]]

        assessments: Dict[str, Dict[str, Any]] = {}
        missing: list[str] = []
        for st_name in states:
            res = await session.execute(
                select(StateIntelligence).where(
                    StateIntelligence.scan_id == scan_id,
                    StateIntelligence.state_name == st_name,
                )
            )
            row = res.scalar_one_or_none()
            if not row or not row.assessment_json:
                missing.append(st_name)
                continue
            assessments[st_name] = dict(row.assessment_json)

        if missing:
            raise RuntimeError(
                "Missing synthesis assessments for states: "
                + ", ".join(missing)
                + ". Run POST /api/aegis/synthesis for this scan_id."
            )

        inputs = ReportInputs(
            scan_id=scan_id,
            scan_run_id=scan.run_id,
            scan_started_at=(
                scan.started_at.isoformat()
                if getattr(scan, "started_at", None)
                else None
            ),
            scan_completed_at=(
                scan.completed_at.isoformat()
                if getattr(scan, "completed_at", None)
                else None
            ),
            states=states,
            rollup_json=dict(scan.rollup_json),
            assessments_by_state=assessments,
            simulation=None,
        )

    if simulation_id:
        try:
            from app.aegis.simulator.persist import get_simulation as _get_sim

            sim = await _get_sim(str(simulation_id))
            if sim:
                inputs.simulation = sim
        except Exception:
            # Report remains packaging-only; simulation is optional.
            pass

    _emit(
        "report_data_loaded",
        status="completed",
        step="load_inputs",
        payload={"scan_id": scan_id, "states": states, "simulation_id": simulation_id},
    )
    return {"report_inputs": inputs}


async def build_report_data_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Construct `ReportData` object and URI whitelist for downstream nodes."""
    inputs: ReportInputs = state["report_inputs"]
    report_id = state["report_id"]
    rd = ReportData(
        report_id=report_id,
        scan_id=inputs.scan_id,
        generated_at=utcnow_iso(),
        states=inputs.states,
        rollup=inputs.rollup_json,
        assessments_by_state=inputs.assessments_by_state,
        simulation=inputs.simulation,
    )
    rd.build_uri_whitelist()
    _emit(
        "report_data_built",
        status="completed",
        step="build_report_data",
        payload={"scan_id": inputs.scan_id, "uris": len(rd.uri_whitelist)},
    )
    return {"report_data": rd}


async def generate_narrative_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate report narrative sections using LLM or template fallback."""
    rd: ReportData = state["report_data"]
    include_annexes = bool(state.get("include_annexes", True))
    cfg = ReportDAGConfig()

    _emit(
        "narrative_started",
        step="narrative",
        payload={"scan_id": rd.scan_id, "mode": cfg.narrative_mode},
    )

    if cfg.narrative_mode == "llm":
        try:
            narrative = await generate_narrative_llm(
                rd,
                include_annexes=include_annexes,
                thinking_level=cfg.thinking_level,
                model=cfg.narrative_model,
            )
        except Exception as exc:
            _emit(
                "narrative_llm_fallback",
                step="narrative",
                message=f"LLM narrative failed ({exc}), falling back to template",
                payload={"scan_id": rd.scan_id},
            )
            narrative = render_template_narrative(rd, include_annexes=include_annexes)
    else:
        narrative = render_template_narrative(rd, include_annexes=include_annexes)

    _emit(
        "narrative_completed",
        status="completed",
        step="narrative",
        payload={"scan_id": rd.scan_id},
    )
    return {"narrative": narrative}


async def generate_infographics_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate infographic assets unless disabled in request state."""
    rd: ReportData = state["report_data"]
    include = bool(state.get("include_infographics", True))
    if not include:
        _emit(
            "infographics_skipped",
            status="completed",
            step="infographics",
            payload={"scan_id": rd.scan_id},
        )
        return {"infographics": {}}

    cfg = ReportDAGConfig()
    _emit("infographics_started", step="infographics", payload={"scan_id": rd.scan_id})
    imgs, img_errors = await generate_all_infographics(report_data=rd, config=cfg)
    if img_errors:
        _emit(
            "infographics_degraded",
            status="completed",
            step="infographics",
            message="One or more infographics failed; using text-only fallback for missing visuals",
            payload={
                "scan_id": rd.scan_id,
                "failed_types": sorted(img_errors.keys()),
                "failed_count": len(img_errors),
            },
        )
    _emit(
        "infographics_completed",
        status="completed",
        step="infographics",
        payload={
            "scan_id": rd.scan_id,
            "count": len(imgs),
            "failed_count": len(img_errors),
        },
    )
    return {
        "infographics": {k: v.file_path for k, v in imgs.items()},
        "infographics_errors": img_errors,
        "infographics_generated_count": len(imgs),
    }


async def build_pdf_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Render final PDF document from narrative, data, and optional infographics."""
    rd: ReportData = state["report_data"]
    report_id = state["report_id"]
    include_infographics = bool(state.get("include_infographics", True))
    include_annexes = bool(state.get("include_annexes", True))
    output_dir = Path(state.get("output_dir") or "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"aegis_report_{timestamp}_{report_id}.pdf"
    pdf_path = str(output_dir / pdf_filename)

    _emit("pdf_build_started", step="pdf_build", payload={"scan_id": rd.scan_id})
    narrative = state["narrative"]
    infographics = state.get("infographics") or {}

    built = build_pdf(
        report_data=rd,
        narrative=narrative,
        infographic_paths=infographics if include_infographics else None,
        config=PDFConfig(
            output_path=pdf_path,
            include_infographics=include_infographics,
            include_annexes=include_annexes,
        ),
    )
    _emit(
        "pdf_build_completed",
        status="completed",
        step="pdf_build",
        payload={"scan_id": rd.scan_id, "pdf_path": built},
    )
    return {"pdf_path": built}


async def persist_report_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Persist report row completion and optionally upload artifact to GCS."""
    report_id = state["report_id"]
    scan_id = int(state["scan_id"])
    states = state.get("states") or []
    include_infographics = bool(state.get("include_infographics", True))
    include_annexes = bool(state.get("include_annexes", True))
    pdf_path = state.get("pdf_path")
    if not pdf_path:
        raise RuntimeError("Missing pdf_path in report state")

    rd: ReportData = state["report_data"]
    sources_cited = len(rd.uri_whitelist or [])

    await create_report_row(
        report_id=report_id,
        scan_id=scan_id,
        states=states,
        include_infographics=include_infographics,
        include_annexes=include_annexes,
    )

    gcs_key: str | None = None
    try:
        from app.config import GCS_BUCKET, GCS_REPORT_PREFIX
        from app.utils.gcs_store import upload_bytes

        gcs_key = f"{GCS_REPORT_PREFIX}{Path(pdf_path).name}"
        upload_bytes(
            bucket=GCS_BUCKET,
            key=gcs_key,
            data=Path(pdf_path).read_bytes(),
            content_type="application/pdf",
        )
    except Exception:
        gcs_key = None

    await mark_report_completed(report_id=report_id, pdf_path=pdf_path, gcs_key=gcs_key)
    _emit(
        "report_completed",
        status="completed",
        step="report_complete",
        payload={"scan_id": scan_id, "pdf_path": pdf_path, "gcs_key": gcs_key},
    )
    return {
        "status": "completed",
        "sources_cited": sources_cited,
        "gcs_key": gcs_key,
        "infographics_generated": int(state.get("infographics_generated_count") or 0),
        "infographics_errors": state.get("infographics_errors") or {},
    }
