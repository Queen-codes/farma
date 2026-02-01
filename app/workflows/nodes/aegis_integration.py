"""Aegis Integration for Loan Risk Assessment.

This module connects the loan workflow to the Aegis humanitarian intelligence
system, checking for conflict zones, food crises, and displacement patterns
that could affect loan viability.
"""

from typing import List, Optional
from app.workflows.state import FarmaState


# IPC Phase descriptions for context
IPC_PHASES = {
    1: "Minimal",  # Safe
    2: "Stressed",  # Caution
    3: "Crisis",  # Elevated risk
    4: "Emergency",  # High risk - consider pausing
    5: "Famine",  # Critical - pause disbursements
}


def get_aegis_risk_sync(state: str, lga: Optional[str] = None) -> dict:
    """
    Synchronous version of Aegis risk query for LangGraph compatibility.

    Uses synchronous SQLAlchemy session to avoid async loop issues.
    """
    try:
        from sqlalchemy import create_engine, select, desc
        from sqlalchemy.orm import Session
        from app.aegis.db.models import StateIntelligence, AegisScan
        import os

        # Get database URL from environment
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("Aegis Check: No DATABASE_URL configured")
            return {
                "aegis_available": False,
                "risk_flags": [],
                "message": "Database not configured",
            }

        # Convert async URL to sync if needed
        if database_url.startswith("postgresql+asyncpg"):
            database_url = database_url.replace("postgresql+asyncpg", "postgresql")

        engine = create_engine(database_url)

        with Session(engine) as session:
            # Get latest scan
            latest_scan = session.execute(
                select(AegisScan).order_by(desc(AegisScan.started_at)).limit(1)
            ).scalar_one_or_none()

            if not latest_scan:
                return {
                    "aegis_available": False,
                    "risk_flags": [],
                    "message": "No Aegis scan data available",
                }

            # Get state intelligence
            intel = session.execute(
                select(StateIntelligence).where(
                    StateIntelligence.scan_id == latest_scan.id,
                    StateIntelligence.state_name == state,
                )
            ).scalar_one_or_none()

            if not intel:
                return {
                    "aegis_available": True,
                    "risk_flags": [],
                    "message": f"No specific intelligence for {state}",
                }

            # Build risk flags
            risk_flags = []

            if intel.ipc_phase:
                if intel.ipc_phase >= 5:
                    risk_flags.extend(["FAMINE_ZONE", "AEGIS_LOAN_PAUSE"])
                elif intel.ipc_phase >= 4:
                    risk_flags.extend(["FOOD_CRISIS_ZONE", "AEGIS_GRACE_PERIOD"])
                elif intel.ipc_phase >= 3:
                    risk_flags.append("FOOD_STRESSED_ZONE")

            if intel.conflict_events_count:
                if intel.conflict_events_count > 20:
                    risk_flags.extend(["ACTIVE_CONFLICT", "AEGIS_LOAN_PAUSE"])
                elif intel.conflict_events_count > 10:
                    risk_flags.append("ELEVATED_CONFLICT")
                elif intel.conflict_events_count > 5:
                    risk_flags.append("CONFLICT_MONITOR")

            if intel.idp_estimate:
                if intel.idp_estimate > 100000:
                    risk_flags.extend(["HIGH_DISPLACEMENT", "AEGIS_GRACE_PERIOD"])
                elif intel.idp_estimate > 50000:
                    risk_flags.append("MODERATE_DISPLACEMENT")

            if intel.markets_operational is not None and intel.markets_operational < 50:
                risk_flags.extend(["MARKET_DISRUPTION", "AEGIS_REPAYMENT_CONCERN"])

            return {
                "aegis_available": True,
                "state": state,
                "scan_date": latest_scan.started_at.isoformat() if latest_scan.started_at else None,
                "risk_flags": risk_flags,
                "details": {
                    "ipc_phase": intel.ipc_phase,
                    "ipc_description": IPC_PHASES.get(intel.ipc_phase, "Unknown"),
                    "conflict_events": intel.conflict_events_count,
                    "idp_estimate": intel.idp_estimate,
                    "idp_trend": intel.idp_trend,
                    "food_insecurity_level": intel.food_insecurity_level,
                    "markets_operational_percent": intel.markets_operational,
                },
            }

    except Exception as e:
        # Don't block loan processing if Aegis is unavailable
        print(f"Aegis sync query failed (non-blocking): {e}")
        return {
            "aegis_available": False,
            "risk_flags": [],
            "error": str(e),
        }


async def get_aegis_risk_for_location(
    state: str, lga: Optional[str] = None
) -> dict:
    """
    Query Aegis database for humanitarian risk indicators in a given location.

    Args:
        state: Nigerian state name
        lga: Local Government Area (optional, for more granular data)

    Returns:
        dict with risk_flags, ipc_phase, conflict_level, idp_estimate
    """
    try:
        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import StateIntelligence, AegisScan
        from sqlalchemy import select, desc

        async with get_async_session() as session:
            # Get latest scan
            latest_scan_result = await session.execute(
                select(AegisScan).order_by(desc(AegisScan.started_at)).limit(1)
            )
            latest_scan = latest_scan_result.scalar_one_or_none()

            if not latest_scan:
                return {
                    "aegis_available": False,
                    "risk_flags": [],
                    "message": "No Aegis scan data available",
                }

            # Get state intelligence from latest scan
            intel_result = await session.execute(
                select(StateIntelligence).where(
                    StateIntelligence.scan_id == latest_scan.id,
                    StateIntelligence.state_name == state,
                )
            )
            intel = intel_result.scalar_one_or_none()

            if not intel:
                return {
                    "aegis_available": True,
                    "risk_flags": [],
                    "message": f"No specific intelligence for {state}",
                }

            # Build risk flags based on Aegis data
            risk_flags = []

            # IPC Phase check
            if intel.ipc_phase:
                if intel.ipc_phase >= 5:
                    risk_flags.append("FAMINE_ZONE")
                    risk_flags.append("AEGIS_LOAN_PAUSE")
                elif intel.ipc_phase >= 4:
                    risk_flags.append("FOOD_CRISIS_ZONE")
                    risk_flags.append("AEGIS_GRACE_PERIOD")
                elif intel.ipc_phase >= 3:
                    risk_flags.append("FOOD_STRESSED_ZONE")

            # Conflict check
            if intel.conflict_events_count:
                if intel.conflict_events_count > 20:
                    risk_flags.append("ACTIVE_CONFLICT")
                    risk_flags.append("AEGIS_LOAN_PAUSE")
                elif intel.conflict_events_count > 10:
                    risk_flags.append("ELEVATED_CONFLICT")
                elif intel.conflict_events_count > 5:
                    risk_flags.append("CONFLICT_MONITOR")

            # IDP check
            if intel.idp_estimate:
                if intel.idp_estimate > 100000:
                    risk_flags.append("HIGH_DISPLACEMENT")
                    risk_flags.append("AEGIS_GRACE_PERIOD")
                elif intel.idp_estimate > 50000:
                    risk_flags.append("MODERATE_DISPLACEMENT")

            # Market disruption
            if intel.markets_operational is not None:
                if intel.markets_operational < 50:
                    risk_flags.append("MARKET_DISRUPTION")
                    risk_flags.append("AEGIS_REPAYMENT_CONCERN")

            return {
                "aegis_available": True,
                "state": state,
                "scan_date": latest_scan.started_at.isoformat() if latest_scan.started_at else None,
                "risk_flags": risk_flags,
                "details": {
                    "ipc_phase": intel.ipc_phase,
                    "ipc_description": IPC_PHASES.get(intel.ipc_phase, "Unknown"),
                    "conflict_events": intel.conflict_events_count,
                    "idp_estimate": intel.idp_estimate,
                    "idp_trend": intel.idp_trend,
                    "food_insecurity_level": intel.food_insecurity_level,
                    "markets_operational_percent": intel.markets_operational,
                },
            }

    except Exception as e:
        # Don't block loan processing if Aegis is unavailable
        print(f"Aegis query failed (non-blocking): {e}")
        return {
            "aegis_available": False,
            "risk_flags": [],
            "error": str(e),
        }


def aegis_risk_check_node(state: FarmaState) -> dict:
    """
    LangGraph node that checks Aegis for location-based risk.

    This runs synchronously - we use synchronous DB queries to avoid async issues.
    """
    coords = state.get("coordinates", {})
    location_state = coords.get("state")

    # If we don't have state info, try to infer from AEZ or skip
    if not location_state:
        aez_context = state.get("nigeria_aez_context", {})
        zone = aez_context.get("zone_name", "")

        # Rough mapping of AEZ to likely states (for North East focus)
        if "Sahel" in zone:
            location_state = "Borno"  # Default to Borno for Sahel
        elif "Sudan" in zone:
            location_state = "Yobe"  # Default to Yobe for Sudan

    if not location_state:
        print("Aegis Check: No state identified, skipping")
        return {"aegis_risk_flags": []}

    lga = coords.get("lga")

    print(f"Aegis Check: Querying risk for {location_state}" + (f", {lga}" if lga else ""))

    # Use synchronous query to avoid async issues in LangGraph sync context
    result = get_aegis_risk_sync(location_state, lga)

    aegis_flags = result.get("risk_flags", [])

    if aegis_flags:
        print(f"Aegis Risk Flags: {aegis_flags}")
    else:
        print("Aegis Check: No risk flags")

    # Store full Aegis context for audit trail
    aegis_context = {
        "aegis_available": result.get("aegis_available", False),
        "scan_date": result.get("scan_date"),
        "details": result.get("details", {}),
    }

    # Merge with existing risk_flags
    existing_flags = state.get("risk_flags", [])
    combined_flags = list(set(existing_flags + aegis_flags))

    return {
        "risk_flags": combined_flags,
        "aegis_context": aegis_context,
    }


def should_pause_loan(aegis_flags: List[str]) -> tuple[bool, str]:
    """
    Determine if loan should be paused based on Aegis flags.

    Returns:
        (should_pause, reason)
    """
    if "AEGIS_LOAN_PAUSE" in aegis_flags:
        if "FAMINE_ZONE" in aegis_flags:
            return True, "Location in famine zone (IPC 5). Loan disbursement paused for farmer safety."
        if "ACTIVE_CONFLICT" in aegis_flags:
            return True, "Active conflict in region. Loan paused pending security improvement."
        return True, "Humanitarian crisis in region. Loan temporarily paused."

    return False, ""


def get_loan_adjustments(aegis_flags: List[str]) -> dict:
    """
    Get loan term adjustments based on Aegis risk assessment.

    Returns:
        dict with adjustments like grace_period, reduced_installments, etc.
    """
    adjustments = {}

    if "AEGIS_GRACE_PERIOD" in aegis_flags:
        adjustments["grace_period_days"] = 90  # 3 month grace period
        adjustments["reason"] = "Extended grace period due to regional humanitarian situation"

    if "AEGIS_REPAYMENT_CONCERN" in aegis_flags:
        adjustments["flexible_repayment"] = True
        adjustments["market_disruption_acknowledged"] = True

    if "FOOD_CRISIS_ZONE" in aegis_flags:
        adjustments["interest_rate_reduction"] = 0.25  # 25% reduction
        adjustments["crisis_support"] = True

    return adjustments
