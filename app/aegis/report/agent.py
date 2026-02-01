"""Report Generator Agent - Main orchestrator for report generation.

Orchestrates the full report generation pipeline:
1. Extract data from synthesis agent output
2. Generate narrative sections with Gemini 3 Pro
3. Generate infographics with Gemini 3 Pro Image Preview (Nano Banana Pro)
4. Assemble final PDF

Architecture:
    Analysis Agent
            ↓ [outputs structured state dict]
    Report Generator Agent (Phase 3)
            ↓ [orchestrates 3 components]
        ┌───┴────┬──────────┐
        ↓        ↓          ↓
    Gemini 3  Gemini 3    PDF
    Pro       Pro Image   Assembler
    (text)    (images)    (layout)
        └───┬────┴──────────┘
            ↓
        Final PDF Report
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path

from .data_extractor import extract_report_data, ReportData
from .narrative import generate_narrative, NarrativeSections
from .infographics import (
    generate_all_infographics,
    GeneratedInfographic,
    InfographicType,
    InfographicConfig,
)
from .pdf_builder import build_pdf, PDFConfig


@dataclass
class ReportGeneratorState:
    """State for the report generation process."""

    # Input
    synthesis_state: Dict[str, Any] = field(default_factory=dict)

    # Extracted data
    report_data: Optional[ReportData] = None

    # Generated content
    narrative: Optional[NarrativeSections] = None
    infographics: Dict[InfographicType, GeneratedInfographic] = field(
        default_factory=dict
    )

    # Output
    pdf_path: Optional[str] = None

    # Metadata
    report_id: str = ""
    started_at: str = ""
    completed_at: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None

    # Audit trail
    steps_completed: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)


async def run_report_generation(
    synthesis_state: Dict[str, Any],
    output_dir: str = "reports",
    include_infographics: bool = True,
    include_annexes: bool = True,
) -> ReportGeneratorState:
    """Run the full report generation pipeline.

    Args:
        synthesis_state: Final state dict from run_synthesis()
        output_dir: Directory for output files
        include_infographics: Whether to generate infographics
        include_annexes: Whether to include state annexes

    Returns:
        ReportGeneratorState with final PDF path and metadata
    """
    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    started_at = datetime.now(timezone.utc)

    print(f"\n{'='*60}")
    print(f"[REPORT] Starting Report Generation: {report_id}")
    print(f"{'='*60}")

    state = ReportGeneratorState(
        synthesis_state=synthesis_state,
        report_id=report_id,
        started_at=started_at.isoformat(),
        status="running",
    )

    try:
        # Step 1: Extract data
        print("[REPORT] Step 1: Extracting data from synthesis output...")
        step_start = datetime.now(timezone.utc)

        state.report_data = extract_report_data(synthesis_state)
        state.report_data.report_id = report_id

        state.timings["data_extraction"] = (
            datetime.now(timezone.utc) - step_start
        ).total_seconds()
        state.steps_completed.append("data_extraction")
        print(
            f"[REPORT] - Data extracted: {state.report_data.states_count} states, {len(state.report_data.all_source_uris)} sources"
        )

        # Step 2: Generate narrative
        print("[REPORT] - Generating narrative with Gemini 3 Pro...")
        step_start = datetime.now(timezone.utc)

        state.narrative = await generate_narrative(state.report_data)

        state.timings["narrative_generation"] = (
            datetime.now(timezone.utc) - step_start
        ).total_seconds()
        state.steps_completed.append("narrative_generation")
        print(
            f"[REPORT] - Narrative generated: {len(state.narrative.state_annexes)} state annexes"
        )

        # Step 3: Generate infographics (optional)
        if include_infographics:
            print(
                "[REPORT] - Generating infographics with Gemini 3 Pro Image Preview..."
            )
            step_start = datetime.now(timezone.utc)

            infographic_config = InfographicConfig(
                output_dir=f"{output_dir}/infographics",
                image_size="2K",
            )

            state.infographics = await generate_all_infographics(
                state.report_data, infographic_config
            )

            state.timings["infographic_generation"] = (
                datetime.now(timezone.utc) - step_start
            ).total_seconds()
            state.steps_completed.append("infographic_generation")
            print(
                f"[REPORT] - infographics generated: {len(state.infographics)} images"
            )
        else:
            print("[REPORT] - Skipping infographics")
            state.steps_completed.append("infographic_generation_skipped")

        # Step 4: Assemble PDF
        print("[REPORT] -  Assembling PDF report...")
        step_start = datetime.now(timezone.utc)

        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"aegis_report_{timestamp}_{report_id}.pdf"
        pdf_path = f"{output_dir}/{pdf_filename}"

        pdf_config = PDFConfig(
            output_path=pdf_path,
            include_infographics=include_infographics,
            include_annexes=include_annexes,
        )

        state.pdf_path = build_pdf(
            report_data=state.report_data,
            narrative=state.narrative,
            infographics=state.infographics if include_infographics else None,
            config=pdf_config,
        )

        state.timings["pdf_assembly"] = (
            datetime.now(timezone.utc) - step_start
        ).total_seconds()
        state.steps_completed.append("pdf_assembly")
        print(f"[REPORT] ✓ PDF assembled: {state.pdf_path}")

        # Complete
        state.status = "completed"
        state.completed_at = datetime.now(timezone.utc).isoformat()

        total_time = (datetime.now(timezone.utc) - started_at).total_seconds()
        state.timings["total"] = total_time

        print(f"[REPORT] Report Generation Complete!")
        print(f"[REPORT] Report ID: {report_id}")
        print(f"[REPORT] PDF: {state.pdf_path}")
        print(f"[REPORT] Total time: {total_time:.1f}s")

        return state

    except Exception as e:
        state.status = "error"
        state.error = str(e)
        state.completed_at = datetime.now(timezone.utc).isoformat()

        print(f"[REPORT] ERROR: {e}")
        import traceback

        traceback.print_exc()

        return state


async def run_report_generation_from_scan(
    scan_id: int,
    states: Optional[List[str]] = None,
    output_dir: str = "reports",
    include_infographics: bool = True,
) -> ReportGeneratorState:
    """Convenience function to run synthesis + report generation.

    This runs the full pipeline from scan ID to PDF.

    Args:
        scan_id: AEGIS scan ID to analyze
        states: Optional list of states (defaults to focus states)
        output_dir: Output directory for report
        include_infographics: Whether to generate infographics

    Returns:
        ReportGeneratorState with final PDF
    """
    # Import here to avoid circular imports
    from app.aegis.synthesis.agent import run_synthesis

    print("[REPORT] Running synthesis agent first...")

    # Run synthesis
    synthesis_state = await run_synthesis(scan_id=scan_id, states=states)

    if synthesis_state.get("status") != "completed":
        raise RuntimeError(
            f"Synthesis failed: {synthesis_state.get('error', 'Unknown error')}"
        )

    # Run report generation
    return await run_report_generation(
        synthesis_state=synthesis_state,
        output_dir=output_dir,
        include_infographics=include_infographics,
    )


def get_report_summary(state: ReportGeneratorState) -> dict:
    """Get a summary of the report generation results."""
    return {
        "report_id": state.report_id,
        "status": state.status,
        "pdf_path": state.pdf_path,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "steps_completed": state.steps_completed,
        "timings": state.timings,
        "error": state.error,
        "states_analyzed": (
            state.report_data.regional.states_analyzed if state.report_data else []
        ),
        "sources_cited": (
            len(state.report_data.all_source_uris) if state.report_data else 0
        ),
        "infographics_generated": len(state.infographics),
    }
