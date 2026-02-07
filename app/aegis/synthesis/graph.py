"""LangGraph workflow/topology for synthesis state workers and rollup finalization.

Purpose:
- Fan out state-level synthesis workers.
- Run rollup aggregation after state assessments.
- Emit final completion node.

Used by:
- `app.aegis.synthesis.runner`.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .state_worker import finalize_synthesis, rollup_worker, synth_state_worker


class SynthesisRunState(TypedDict):
    """Shared state payload for synthesis graph execution."""

    scan_id: int
    states: List[str]
    config: Dict[str, Any]
    assessments: Annotated[List[Dict[str, Any]], operator.add]
    errors: Annotated[List[Dict[str, Any]], operator.add]
    rollup: Optional[Dict[str, Any]]


def route_states(state: SynthesisRunState) -> List[Send] | str:
    """Route START into one `synth_state_worker` send per target state."""
    scan_id = int(state["scan_id"])
    states = state.get("states") or []
    if not states:
        return END
    return [
        Send(
            "synth_state_worker",
            {"scan_id": scan_id, "state_name": s, "config": state.get("config", {})},
        )
        for s in states
    ]


def build_synthesis_graph() -> Any:
    """Build and compile the synthesis graph."""
    g: StateGraph = StateGraph(SynthesisRunState)
    g.add_node("synth_state_worker", synth_state_worker)
    g.add_node("rollup_worker", rollup_worker)
    g.add_node("finalize_synthesis", finalize_synthesis)

    g.add_conditional_edges(START, route_states, ["synth_state_worker"])
    g.add_edge("synth_state_worker", "rollup_worker")
    g.add_edge("rollup_worker", "finalize_synthesis")
    g.add_edge("finalize_synthesis", END)
    return g.compile()


synthesis_graph = build_synthesis_graph()
