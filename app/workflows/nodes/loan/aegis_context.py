"""
Docstring for app.workflows.nodes.loan.aegis_context
This module checks if the farmer's region is in a conflict/crisis zone using AEGIS intelligence data.
"""

from __future__ import annotations

from typing import List, Optional

from app.workflows.job_events import emit_event
from app.workflows.state import FarmaState


def _dedupe(flags: List[str]) -> List[str]:
    return list(dict.fromkeys([f for f in flags if f]))


def _flags_from_assessment(assessment: dict, lga_risk: Optional[dict]) -> List[str]:
    flags: List[str] = []

    risk_level = (assessment.get("risk_level") or "").upper()
    metrics = assessment.get("metrics") or {}

    ipc = metrics.get("ipc_phase")
    try:
        ipc_i = int(ipc) if ipc is not None else None
    except Exception:
        ipc_i = None

    idp = metrics.get("idp_estimate")
    try:
        idp_i = int(idp) if idp is not None else None
    except Exception:
        idp_i = None

    if ipc_i is not None:
        if ipc_i >= 5:
            flags += ["FAMINE_ZONE", "AEGIS_LOAN_PAUSE"]
        elif ipc_i >= 4:
            flags += ["FOOD_CRISIS_ZONE", "AEGIS_GRACE_PERIOD"]
        elif ipc_i >= 3:
            flags += ["FOOD_STRESSED_ZONE"]

    if idp_i is not None and idp_i >= 300000:
        flags += ["HIGH_DISPLACEMENT"]

    if risk_level in {"HIGH", "CRITICAL"}:
        flags += ["CONFLICT_MONITOR"]

    if lga_risk:
        lvl = (lga_risk.get("risk_level") or "").upper()
        if lvl in {"HIGH", "CRITICAL"}:
            flags += ["HIGH_LGA_RISK", "AEGIS_GRACE_PERIOD"]

    return _dedupe(flags)


async def aegis_risk_check_node(state: FarmaState) -> dict:
    """Inject AEGIS risk context using latest completed synthesis outputs."""
    emit_event("aegis_started", step="aegis_risk_check")

    coords = state.get("coordinates") or {}
    location_state = (coords.get("state") or "").strip() or None
    lga = (coords.get("lga") or "").strip() or None

    if not location_state:
        emit_event(
            "aegis_done",
            status="completed",
            step="aegis_risk_check",
            payload={"aegis_available": False, "reason": "missing_state"},
        )
        return {"aegis_context": {"aegis_available": False}}

    try:
        from sqlalchemy import desc, select

        from app.aegis.db.connection import get_async_session
        from app.aegis.db.models import AegisScan, LGARiskScore, StateIntelligence

        async with get_async_session() as session:
            scan_res = await session.execute(
                select(AegisScan)
                .where(AegisScan.rollup_json.isnot(None))
                .order_by(desc(AegisScan.completed_at), desc(AegisScan.started_at))
                .limit(1)
            )
            scan = scan_res.scalar_one_or_none()
            if not scan:
                emit_event(
                    "aegis_done",
                    status="completed",
                    step="aegis_risk_check",
                    payload={
                        "aegis_available": False,
                        "reason": "no_synthesis_available",
                    },
                )
                return {
                    "aegis_context": {
                        "aegis_available": False,
                        "message": "No AEGIS synthesis available",
                    },
                }

            intel_res = await session.execute(
                select(StateIntelligence).where(
                    StateIntelligence.scan_id == scan.id,
                    StateIntelligence.state_name == location_state,
                )
            )
            intel = intel_res.scalar_one_or_none()
            assessment = (intel.assessment_json if intel else None) or {}

            lga_risk_row: Optional[LGARiskScore] = None
            if lga:
                lga_res = await session.execute(
                    select(LGARiskScore).where(
                        LGARiskScore.scan_id == scan.id,
                        LGARiskScore.state == location_state,
                        LGARiskScore.lga == lga,
                    )
                )
                lga_risk_row = lga_res.scalar_one_or_none()

            lga_risk = None
            if lga_risk_row:
                lga_risk = {
                    "lga": lga_risk_row.lga,
                    "state": lga_risk_row.state,
                    "event_count": lga_risk_row.event_count,
                    "fatalities": lga_risk_row.fatalities,
                    "risk_score": lga_risk_row.risk_score,
                    "risk_level": lga_risk_row.risk_level,
                }

        # Only return NEW flags; operator.add in state merges with existing
        derived_flags = _flags_from_assessment(assessment, lga_risk)

        aegis_context = {
            "aegis_available": True,
            "scan_id": scan.id,
            "scan_run_id": scan.run_id,
            "scan_completed_at": (
                scan.completed_at.isoformat() if scan.completed_at else None
            ),
            "state": location_state,
            "lga": lga,
            "risk_level": assessment.get("risk_level") or "UNKNOWN",
            "metrics": assessment.get("metrics") or {},
            "key_findings": assessment.get("key_findings") or [],
            "lga_risk": lga_risk,
        }

        emit_event(
            "aegis_done",
            status="completed",
            step="aegis_risk_check",
            payload={
                "aegis_available": True,
                "scan_id": scan.id,
                "risk_level": aegis_context.get("risk_level"),
                "derived_flags": derived_flags,
            },
        )

        return {"aegis_context": aegis_context, "risk_flags": derived_flags}

    except Exception as e:
        emit_event(
            "aegis_done",
            status="failed",
            step="aegis_risk_check",
            payload={"error": str(e)},
        )
        return {"aegis_context": {"aegis_available": False, "error": str(e)}}


__all__ = ["aegis_risk_check_node"]
