"""LangGraph workflow/topology for parallel per-state AEGIS scan execution.

Purpose:
- Define scan run state contract.
- Fan out one worker invocation per target state.
- Aggregate worker results back into a single graph output.

Used by:
- `app.aegis.scan.runner.run_aegis_scan`.

Assumptions:
- `aegis_state_worker` is idempotent for a `(scan_id, state)` pair.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, List
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from app.aegis.scan.state_worker import aegis_state_worker


class AegisScanState(TypedDict):
    """State container for the scan graph run."""

    run_id: str
    days_back: int
    states: List[str]
    api_key: str
    scan_id: int | None
    results: Annotated[List[dict], operator.add]


def dispatch_states(state: AegisScanState) -> list[Send] | str:
    """Route from START to one `state_worker` send per state.

    Args:
        state: Graph state containing target states and shared run params.

    Returns:
        list[Send] | str: `END` when no states are provided, otherwise send
        instructions for each state.

    Raises:
        Does not raise intentionally.

    Side Effects:
        None.

    Latency:
        Linear in number of target states.
    """
    states = state.get("states") or []
    if not states:
        return END
    days_back = int(state.get("days_back") or 7)
    api_key = state.get("api_key") or ""
    scan_id = state.get("scan_id")
    return [
        Send(
            "state_worker",
            {
                "state": s,
                "days_back": days_back,
                "api_key": api_key,
                "scan_id": scan_id,
            },
        )
        for s in states
    ]


def build_aegis_scan_graph() -> Any:
    """Build and compile the scan graph.

    Args:
        None.

    Returns:
        Any: Compiled LangGraph runnable.

    Raises:
        Exception: Can propagate LangGraph compile-time errors.

    Side Effects:
        Instantiates a graph object in memory.

    Latency:
        Fast in-memory graph construction.
    """
    builder: StateGraph = StateGraph(AegisScanState)
    builder.add_node("state_worker", aegis_state_worker)

    builder.add_conditional_edges(START, dispatch_states, ["state_worker"])
    builder.add_edge("state_worker", END)
    return builder.compile()


aegis_scan_graph = build_aegis_scan_graph()
