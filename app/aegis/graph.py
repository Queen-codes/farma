"""Data Intel Agent - LangGraph orchestrator with parallel state workers.
This agent does data collation only.
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated, List
from typing_extensions import TypedDict
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .tools import (
    AEGIS_FOCUS_STATES,
    search_conflict_events,
    search_displacement,
    search_food_security,
    search_economic_indicators,
)
from .db import (
    async_session,
    AegisScan,
    StateIntelligence,
    ConflictEvent as DBConflictEvent,
)


# state definitions
class StateWorkerResult(TypedDict):
    """Result from a single state worker"""

    state_name: str
    conflict_data: dict | None
    displacement_data: dict | None
    food_security_data: dict | None
    economic_data: dict | None


class AegisGraphState(TypedDict):
    """Main state for the Data Intel Agent."""

    # Run metadata
    run_id: str
    started_at: str
    days_back: int

    # Checkpoint
    should_run: bool
    skip_reason: str | None

    # aggregated results from workers
    state_results: Annotated[List[StateWorkerResult], operator.add]

    # Summary counts (computed during persist, not analysis)
    total_events: int
    total_fatalities: int
    states_scanned: int

    # Status
    status: str


# worker node that run for each state in parrallel
def state_worker(state_name: str, days_back: int) -> StateWorkerResult:
    """
    Worker that processes a single state, running all 4 tools
    """

    print(f"[WORKER] Collecting data: {state_name}")

    # Run all 4 tools
    conflict = search_conflict_events(state_name, days_back)
    displacement = search_displacement(state_name, days_back)
    food_security = search_food_security(state_name, days_back)
    economic = search_economic_indicators(state_name, days_back)

    print(f"[WORKER] {state_name} → Data collected")

    return {
        "state_name": state_name,
        "conflict_data": conflict.model_dump() if conflict else None,
        "displacement_data": displacement.model_dump() if displacement else None,
        "food_security_data": food_security.model_dump() if food_security else None,
        "economic_data": economic.model_dump() if economic else None,
    }


# graph node
async def checkpoint_node(state: AegisGraphState) -> dict:
    """
    Checkpoint: Check if we should run or skip.
    Prevents duplicate runs within a timeframe.
    """

    print(f"Checkpoint: {state['run_id']}")

    # Check last successful scan
    async with async_session() as session:
        from sqlalchemy import select, desc

        result = await session.execute(
            select(AegisScan)
            .where(AegisScan.status == "completed")
            .order_by(desc(AegisScan.completed_at))
            .limit(1)
        )
        last_scan = result.scalar_one_or_none()

        if last_scan and last_scan.completed_at:
            hours_since = (
                datetime.now(timezone.utc) - last_scan.completed_at
            ).total_seconds() / 3600
            min_hours = 6  # Minimum hours between scans

            if hours_since < min_hours:
                print(f"skip: Last scan was {hours_since:.1f}h ago (min: {min_hours}h)")
                return {
                    "should_run": False,
                    "skip_reason": f"Last scan {hours_since:.1f}h ago",
                    "status": "skipped",
                }

    print(f"Proceeding...")
    print(f"Scanning {len(AEGIS_FOCUS_STATES)} states | {state['days_back']} days back")

    return {
        "should_run": True,
        "skip_reason": None,
        "status": "collecting",
    }


def dispatch_workers(state: AegisGraphState) -> list[Send] | str:
    """
    Dispatch parallel workers for each focus state OR end if skipped.
    Uses Send() API for map-reduce pattern.
    Returns END if checkpoint decided to skip.
    """
    if not state.get("should_run", True):
        return END

    days_back = state.get("days_back", 7)
    return [
        Send("worker", {"state_name": s, "days_back": days_back})
        for s in AEGIS_FOCUS_STATES
    ]


def worker_node(inputs: dict) -> dict:
    """
    Worker node wrapper - processes single state.
    Returns result to be aggregated via operator.add.
    """
    result = state_worker(inputs["state_name"], inputs["days_back"])
    return {"state_results": [result]}


async def persist_node(state: AegisGraphState) -> dict:
    """
    Persist: Save RAW data to PostgreSQL.
    Computes summary counts but NO analysis.
    """
    print(f"\nPersisting to database...")

    results = state.get("state_results", [])

    # Compute summary counts (factual aggregation, not analysis)
    total_events = 0
    total_fatalities = 0

    for r in results:
        if r.get("conflict_data"):
            total_events += r["conflict_data"].get("total_events", 0)
            for event in r["conflict_data"].get("events", []):
                total_fatalities += event.get("fatalities", 0) or 0

    async with async_session() as session:
        # Create scan record
        scan = AegisScan(
            run_id=state["run_id"],
            started_at=datetime.fromisoformat(state["started_at"]),
            completed_at=datetime.now(timezone.utc),
            status="completed",
            states_scanned=len(results),
            total_events=total_events,
            total_fatalities=total_fatalities,
        )
        session.add(scan)
        await session.flush()  # Get scan.id

        # Create state intel records
        for r in results:
            intel = StateIntelligence(
                scan_id=scan.id,
                state_name=r["state_name"],
                # Store raw JSON data from each tool
                conflict_raw=r.get("conflict_data"),
                displacement_raw=r.get("displacement_data"),
                food_security_raw=r.get("food_security_data"),
                economic_raw=r.get("economic_data"),
                # Extract key facts for querying
                conflict_events_count=(
                    r["conflict_data"]["total_events"] if r.get("conflict_data") else 0
                ),
                idp_estimate=(
                    r["displacement_data"]["idp_estimate"]
                    if r.get("displacement_data")
                    else None
                ),
                food_insecurity_level=(
                    r["food_security_data"]["acute_food_insecurity"]
                    if r.get("food_security_data")
                    else "unknown"
                ),
                ipc_phase=(
                    r["food_security_data"]["ipc_phase"]
                    if r.get("food_security_data")
                    else None
                ),
                markets_operational=(
                    r["economic_data"]["markets_operational"]
                    if r.get("economic_data")
                    else "unknown"
                ),
            )
            session.add(intel)

            # Create individual conflict event records for detailed queries
            if r.get("conflict_data"):
                await session.flush()  # Get intel.id
                for event in r["conflict_data"].get("events", []):
                    db_event = DBConflictEvent(
                        state_intel_id=intel.id,
                        event_date=event.get("date", ""),
                        location=event.get("location", ""),
                        state=r["state_name"],
                        lga=event.get("lga"),
                        event_type=event.get("event_type", "other"),
                        actors=event.get("actors"),
                        fatalities=event.get("fatalities", 0) or 0,
                        injuries=event.get("injuries", 0) or 0,
                        abducted=event.get("abducted", 0) or 0,
                        summary=event.get("summary", ""),
                        source=event.get("source"),
                    )
                    session.add(db_event)

        await session.commit()

    print(f"Persisted scan {state['run_id']}")
    print(
        f"States: {len(results)} | Events: {total_events} | Fatalities: {total_fatalities}"
    )

    return {
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "states_scanned": len(results),
        "status": "completed",
    }


# build graph


def build_aegis_graph():
    """Build and compile the Data Intel Agent graph."""

    builder = StateGraph(AegisGraphState)

    # add nodes
    builder.add_node("checkpoint", checkpoint_node)
    builder.add_node("worker", worker_node)
    builder.add_node("persist", persist_node)

    # add edges
    # START → checkpoint
    builder.add_edge(START, "checkpoint")

    # checkpoint → (dispatch workers via Send() OR end)
    # dispatch_workers returns list of Send() objects OR END
    builder.add_conditional_edges("checkpoint", dispatch_workers, ["worker"])

    # all workers → persist
    builder.add_edge("worker", "persist")

    # persist → END
    builder.add_edge("persist", END)

    return builder.compile()


# compiled graph
aegis_graph = build_aegis_graph()


# run scan
async def run_aegis_scan(days_back: int = 7, force: bool = False) -> dict:
    """
    Run a full AEGIS data collection scan.

    Args:
        days_back: How many days of data to search
        force: If True, skip checkpoint and run anyway

    Returns:
        Final state with collected data
    """
    initial_state = {
        "run_id": f"AEGIS-{uuid.uuid4().hex[:8].upper()}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "days_back": days_back,
        "should_run": force,  # If force=True, skip checkpoint
        "skip_reason": None,
        "state_results": [],
        "total_events": 0,
        "total_fatalities": 0,
        "states_scanned": 0,
        "status": "starting",
    }

    result = await aegis_graph.ainvoke(initial_state)
    return result
