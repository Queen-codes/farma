"""Inspect recent AEGIS scan/intelligence records from the database.

This script is a read-only CLI utility used during debugging/demo sessions to:
- Print the most recent scan summary and sample conflict events.
- Print deduplicated source URIs for a chosen state.
"""

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, desc
from app.aegis.db import async_session, AegisScan, StateIntelligence, ConflictEvent


async def view_latest_scan() -> None:
    """Print latest scan, state intelligence, and sample conflict events.

    Returns:
        None.

    Raises:
        Exception: Propagates DB/session query failures.
    """
    async with async_session() as session:
        # Get latest scan
        result = await session.execute(
            select(AegisScan).order_by(desc(AegisScan.completed_at)).limit(1)
        )
        scan = result.scalar_one_or_none()

        if not scan:
            print("No scans found.")
            return

        print(f"LATEST SCAN: {scan.run_id}")

        print(f"Status: {scan.status}")
        print(f"Started: {scan.started_at}")
        print(f"Completed: {scan.completed_at}")
        print(f"States Scanned: {scan.states_scanned}")
        print(f"Total Events: {scan.total_events}")
        print(f"Total Fatalities: {scan.total_fatalities}")

        # Get state intelligence for this scan
        intel_result = await session.execute(
            select(StateIntelligence).where(StateIntelligence.scan_id == scan.id)
        )
        intel_records = intel_result.scalars().all()

        print(f"STATE INTELLIGENCE ({len(intel_records)} states)")

        for intel in intel_records:
            print(f"\n--- {intel.state_name} ---")
            print(f"  Conflict Events: {intel.conflict_events_count}")
            print(f"  IDP Estimate: {intel.idp_estimate}")
            print(f"  Trend: {intel.trend_direction}")
            print(f"  Markets: {intel.markets_operational}")

            # Show sources from raw data
            if intel.conflict_raw and intel.conflict_raw.get("sources_consulted"):
                sources = intel.conflict_raw["sources_consulted"][:3]
                print(
                    f"  Sources ({len(intel.conflict_raw.get('sources_consulted', []))} total):"
                )
                for uri in sources:
                    print(f"    - {uri[:70]}...")

        # Get conflict events
        events_result = await session.execute(
            select(ConflictEvent)
            .join(StateIntelligence)
            .where(StateIntelligence.scan_id == scan.id)
            .limit(10)
        )
        events = events_result.scalars().all()

        print(f"CONFLICT EVENTS (showing 10 of {scan.total_events})")

        for event in events:
            print(f"\n[{event.event_type}] {event.state} - {event.location}")
            print(f"  Date: {event.event_date}")
            print(
                f"  Fatalities: {event.fatalities} | Injuries: {event.injuries} | Abducted: {event.abducted}"
            )
            print(f"  {event.summary[:100]}...")
            if event.source:
                print(f"  Source: {event.source}")


async def view_sources_for_state(state_name: str) -> None:
    """Print all unique source URIs for the most recent state intelligence row.

    Args:
        state_name: State name used to filter the latest intelligence snapshot.

    Returns:
        None.

    Raises:
        Exception: Propagates DB/session query failures.
    """
    async with async_session() as session:
        result = await session.execute(
            select(StateIntelligence)
            .join(AegisScan)
            .where(StateIntelligence.state_name == state_name)
            .order_by(desc(AegisScan.completed_at))
            .limit(1)
        )
        intel = result.scalar_one_or_none()

        if not intel:
            print(f"No data for {state_name}")
            return

        print(f"SOURCES FOR {state_name}")

        all_sources = set()

        for raw_field in [
            "conflict_raw",
            "displacement_raw",
            "trend_raw",
            "economic_raw",
        ]:
            raw_data = getattr(intel, raw_field)
            if raw_data and raw_data.get("sources_consulted"):
                for uri in raw_data["sources_consulted"]:
                    all_sources.add(uri)

        print(f"\nTotal unique sources: {len(all_sources)}\n")
        for i, uri in enumerate(all_sources, 1):
            print(f"{i}. {uri}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(view_sources_for_state(sys.argv[1]))
    else:
        asyncio.run(view_latest_scan())
