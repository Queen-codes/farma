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

    # Run all 4 tools - they now always return results (with error field if failed)
    conflict = search_conflict_events(state_name, days_back)
    displacement = search_displacement(state_name, days_back)
    food_security = search_food_security(state_name, days_back)
    economic = search_economic_indicators(state_name, days_back)

    # Track any collection errors
    errors = []
    if conflict.error:
        errors.append(f"conflict: {conflict.error}")
    if displacement.error:
        errors.append(f"displacement: {displacement.error}")
    if food_security.error:
        errors.append(f"food_security: {food_security.error}")
    if economic.error:
        errors.append(f"economic: {economic.error}")
    
    if errors:
        print(f"[WORKER] {state_name} → Completed with {len(errors)} collection error(s)")
    else:
        print(f"[WORKER] {state_name} → Data collected successfully")

    return {
        "state_name": state_name,
        "conflict_data": conflict.model_dump(),
        "displacement_data": displacement.model_dump(),
        "food_security_data": food_security.model_dump(),
        "economic_data": economic.model_dump(),
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
    Tracks collection errors for monitoring.
    """
    print(f"\nPersisting to database...")

    results = state.get("state_results", [])

    # Compute summary counts (factual aggregation, not analysis)
    # Only count data from successful collections (no error field)
    total_events = 0
    total_fatalities = 0
    collection_errors = 0

    for r in results:
        conflict_data = r.get("conflict_data", {})
        # Only count if no error
        if conflict_data and not conflict_data.get("error"):
            total_events += conflict_data.get("total_events", 0)
            for event in conflict_data.get("events", []):
                total_fatalities += event.get("fatalities", 0) or 0
        
        # Track errors across all tools
        for tool_name in ["conflict_data", "displacement_data", "food_security_data", "economic_data"]:
            tool_data = r.get(tool_name, {})
            if tool_data and tool_data.get("error"):
                collection_errors += 1

    async with async_session() as session:
        # Create scan record
        scan = AegisScan(
            run_id=state["run_id"],
            started_at=datetime.fromisoformat(state["started_at"]),
            completed_at=datetime.now(timezone.utc),
            status="completed" if collection_errors == 0 else "completed_with_errors",
            states_scanned=len(results),
            total_events=total_events,
            total_fatalities=total_fatalities,
        )
        session.add(scan)
        await session.flush()  # Get scan.id

        # Create state intel records
        for r in results:
            conflict_data = r.get("conflict_data", {})
            displacement_data = r.get("displacement_data", {})
            food_security_data = r.get("food_security_data", {})
            economic_data = r.get("economic_data", {})
            
            intel = StateIntelligence(
                scan_id=scan.id,
                state_name=r["state_name"],
                # Store raw JSON data from each tool (includes error info for debugging)
                conflict_raw=conflict_data if conflict_data else None,
                displacement_raw=displacement_data if displacement_data else None,
                food_security_raw=food_security_data if food_security_data else None,
                economic_raw=economic_data if economic_data else None,
                # Extract key facts for querying - only if no error
                conflict_events_count=(
                    conflict_data.get("total_events", 0) 
                    if conflict_data and not conflict_data.get("error") 
                    else 0
                ),
                idp_estimate=(
                    displacement_data.get("idp_estimate")
                    if displacement_data and not displacement_data.get("error")
                    else None
                ),
                food_insecurity_level=(
                    food_security_data.get("acute_food_insecurity", "unknown")
                    if food_security_data and not food_security_data.get("error")
                    else "unknown"
                ),
                ipc_phase=(
                    food_security_data.get("ipc_phase")
                    if food_security_data and not food_security_data.get("error")
                    else None
                ),
                markets_operational=(
                    economic_data.get("markets_operational", "unknown")
                    if economic_data and not economic_data.get("error")
                    else "unknown"
                ),
            )
            session.add(intel)

            # Create individual conflict event records for detailed queries
            # Only if conflict data collection succeeded
            if conflict_data and not conflict_data.get("error"):
                await session.flush()  # Get intel.id
                for event in conflict_data.get("events", []):
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
    if collection_errors > 0:
        print(f"⚠️  Collection errors: {collection_errors} (data stored for debugging)")

    return {
        "total_events": total_events,
        "total_fatalities": total_fatalities,
        "states_scanned": len(results),
        "status": "completed" if collection_errors == 0 else "completed_with_errors",
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
