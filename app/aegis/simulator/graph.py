"""LangGraph topology/workflow for simulation workflow execution.

Purpose:
- Define simulator graph state.
- Compose node pipeline from baseline loading to persistence/finalization.

Used by:
- `app.aegis.simulator.runner`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from app.aegis.simulator.nodes import (
    load_baseline_inputs,
    compute_projections_node,
    generate_policy_brief_node,
    persist_simulation_node,
    finalize_simulation_node,
)


class SimulatorRunState(TypedDict, total=False):
    scan_id: int
    simulation_id: str
    scenario: dict
    config: Dict[str, Any]

    baseline_rollup_json: dict
    baseline_assessments_by_state: dict
    uri_whitelist: list[str]
    projections: dict
    policy_brief: dict
    schema_mode: str


def build_simulator_graph() -> Any:
    """Build and compile simulator graph runnable."""
    g = StateGraph(SimulatorRunState)
    g.add_node("load_baseline_inputs", load_baseline_inputs)
    g.add_node("compute_projections", compute_projections_node)
    g.add_node("generate_policy_brief", generate_policy_brief_node)
    g.add_node("persist_simulation", persist_simulation_node)
    g.add_node("finalize_simulation", finalize_simulation_node)

    g.add_edge(START, "load_baseline_inputs")
    g.add_edge("load_baseline_inputs", "compute_projections")
    g.add_edge("compute_projections", "generate_policy_brief")
    g.add_edge("generate_policy_brief", "persist_simulation")
    g.add_edge("persist_simulation", "finalize_simulation")
    g.add_edge("finalize_simulation", END)
    return g.compile()


simulator_graph = build_simulator_graph()
