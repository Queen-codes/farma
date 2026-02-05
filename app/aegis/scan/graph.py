from __future__ import annotations

import operator
from typing import Annotated, List
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from app.aegis.scan.state_worker import aegis_state_worker


class AegisScanState(TypedDict):
    run_id: str
    days_back: int
    states: List[str]
    api_key: str
    scan_id: int | None
    results: Annotated[List[dict], operator.add]


def dispatch_states(state: AegisScanState) -> list[Send] | str:
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


def build_aegis_scan_graph():
    builder: StateGraph = StateGraph(AegisScanState)
    builder.add_node("state_worker", aegis_state_worker)

    builder.add_conditional_edges(START, dispatch_states, ["state_worker"])
    builder.add_edge("state_worker", END)
    return builder.compile()


aegis_scan_graph = build_aegis_scan_graph()
