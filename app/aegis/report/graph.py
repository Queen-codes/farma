"""LangGraph workflow for end-to-end report generation workflow.

Purpose:
- aggregates report nodes from input loading through persistence.

Used by:
- `app.aegis.report.runner.run_report_dag`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from app.aegis.report.nodes import (
    load_report_inputs,
    build_report_data_node,
    generate_narrative_node,
    generate_infographics_node,
    build_pdf_node,
    persist_report_node,
)


class ReportRunState(TypedDict, total=False):
    """Shared graph state for report generation execution."""

    report_id: str
    scan_id: int
    states: list[str]
    include_infographics: bool
    include_annexes: bool
    simulation_id: Optional[str]
    output_dir: str

    report_inputs: Any
    report_data: Any
    narrative: Any
    infographics: Dict[str, str]
    pdf_path: Optional[str]
    status: str


def build_report_graph() -> Any:
    """Build and compile report DAG graph runnable."""
    g = StateGraph(ReportRunState)
    g.add_node("load_report_inputs", load_report_inputs)
    g.add_node("build_report_data", build_report_data_node)
    g.add_node("generate_narrative", generate_narrative_node)
    g.add_node("generate_infographics", generate_infographics_node)
    g.add_node("build_pdf", build_pdf_node)
    g.add_node("persist_report", persist_report_node)

    g.add_edge(START, "load_report_inputs")
    g.add_edge("load_report_inputs", "build_report_data")
    g.add_edge("build_report_data", "generate_narrative")
    g.add_edge("generate_narrative", "generate_infographics")
    g.add_edge("generate_infographics", "build_pdf")
    g.add_edge("build_pdf", "persist_report")
    g.add_edge("persist_report", END)
    return g.compile()


report_graph = build_report_graph()
