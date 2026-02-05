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
from app.aegis.report.narrative import render_template_narrative
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
    report_id = state["report_id"]
    scan_id = int(state["scan_id"])
    states = state.get("states") or []

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
        )

    _emit(
        "report_data_loaded",
        status="completed",
        step="load_inputs",
        payload={"scan_id": scan_id, "states": states},
    )
    return {"report_inputs": inputs}


async def build_report_data_node(state: Dict[str, Any]) -> Dict[str, Any]:
    inputs: ReportInputs = state["report_inputs"]
    report_id = state["report_id"]
    rd = ReportData(
        report_id=report_id,
        scan_id=inputs.scan_id,
        generated_at=utcnow_iso(),
        states=inputs.states,
        rollup=inputs.rollup_json,
        assessments_by_state=inputs.assessments_by_state,
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
    rd: ReportData = state["report_data"]
    include_annexes = bool(state.get("include_annexes", True))
    _emit("narrative_started", step="narrative", payload={"scan_id": rd.scan_id})
    narrative = render_template_narrative(rd, include_annexes=include_annexes)
    _emit(
        "narrative_completed",
        status="completed",
        step="narrative",
        payload={"scan_id": rd.scan_id},
    )
    return {"narrative": narrative}


async def generate_infographics_node(state: Dict[str, Any]) -> Dict[str, Any]:
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
    imgs = await generate_all_infographics(report_data=rd, config=cfg)
    _emit(
        "infographics_completed",
        status="completed",
        step="infographics",
        payload={"scan_id": rd.scan_id, "count": len(imgs)},
    )
    return {"infographics": {k: v.file_path for k, v in imgs.items()}}


async def build_pdf_node(state: Dict[str, Any]) -> Dict[str, Any]:
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
    await mark_report_completed(report_id=report_id, pdf_path=pdf_path, gcs_key=None)
    _emit(
        "report_completed",
        status="completed",
        step="report_complete",
        payload={"scan_id": scan_id, "pdf_path": pdf_path},
    )
    return {"status": "completed", "sources_cited": sources_cited}
