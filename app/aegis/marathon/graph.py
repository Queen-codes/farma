"""LangGraph topology/workflow for marathon continuity + autonomous follow-up actions.

Purpose:
- Chain context loading, delta computation, continuity-note generation,
  action decision, and persistence.
- Conditionally branch into simulation/report enqueue nodes.

Used by:
- `app.aegis.marathon.runner`.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.aegis.marathon.nodes import (
    load_context_node,
    resolve_scan_node,
    compute_deltas_node,
    generate_continuity_note_node,
    decide_actions_node,
    enqueue_simulation_node,
    enqueue_report_node,
    persist_marathon_day_node,
)


class MarathonState(TypedDict):
    """State contract shared across marathon graph nodes."""

    # Inputs (provided by runner)
    track_id: str
    day_date: str
    scan_id: Optional[int]
    prev_scan_id: Optional[int]
    config: Dict[str, Any]
    mode: str  # "manual" or "autonomous"

    # Loaded from DB
    scan_rollup_json: Optional[Dict[str, Any]]
    scan_assessments: Annotated[List[Dict[str, Any]], operator.add]
    prev_rollup_json: Optional[Dict[str, Any]]
    prev_assessments: Annotated[List[Dict[str, Any]], operator.add]
    prior_model_content_json: Optional[dict]
    prev_thought_signature: Optional[str]
    prev_day_date: Optional[str]
    prev_continuity_note: Optional[dict]  # previous day's note (for self-correction)

    # Computed
    uri_whitelist: Annotated[List[str], operator.add]
    delta_json: Optional[Dict[str, Any]]
    effective_thinking_level: Optional[str]

    # LLM output
    continuity_note_json: Optional[Dict[str, Any]]
    thought_signature: Optional[str]
    stored_model_content_json: Optional[dict]
    schema_mode: Optional[str]

    # Actions decided by Marathon
    actions_taken: Annotated[List[str], operator.add]
    simulation_triggered: Optional[str]  # simulation_id if triggered
    report_triggered: Optional[str]  # report_id if triggered

    # Error tracking
    errors: Annotated[List[Dict[str, Any]], operator.add]


def _route_after_actions(state: MarathonState) -> list[str]:
    """Conditional edge: decide which sub-agents to enqueue after decide_actions."""
    targets: list[str] = []
    actions = state.get("actions_taken") or []

    if "enqueue_simulation" in actions:
        targets.append("enqueue_simulation")
    if "enqueue_report" in actions:
        targets.append("enqueue_report")

    if not targets:
        targets.append("persist_marathon_day")

    return targets


def build_marathon_graph() -> Any:
    """Build and compile marathon graph with memory checkpointer."""
    g = StateGraph(MarathonState)

    # Nodes
    g.add_node("load_context", load_context_node)
    g.add_node("resolve_scan", resolve_scan_node)
    g.add_node("compute_deltas", compute_deltas_node)
    g.add_node("generate_continuity_note", generate_continuity_note_node)
    g.add_node("decide_actions", decide_actions_node)
    g.add_node("enqueue_simulation", enqueue_simulation_node)
    g.add_node("enqueue_report", enqueue_report_node)
    g.add_node("persist_marathon_day", persist_marathon_day_node)

    # Edges — linear up to decide_actions
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "resolve_scan")
    g.add_edge("resolve_scan", "compute_deltas")
    g.add_edge("compute_deltas", "generate_continuity_note")
    g.add_edge("generate_continuity_note", "decide_actions")

    # Conditional: decide_actions routes to simulation/report or straight to persist
    g.add_conditional_edges(
        "decide_actions",
        _route_after_actions,
        ["enqueue_simulation", "enqueue_report", "persist_marathon_day"],
    )

    # Both sub-agent enqueue nodes converge to persist
    g.add_edge("enqueue_simulation", "persist_marathon_day")
    g.add_edge("enqueue_report", "persist_marathon_day")

    g.add_edge("persist_marathon_day", END)

    return g.compile(checkpointer=MemorySaver())


marathon_graph = build_marathon_graph()
