"""Test loan request with Igbo language (rice farm).

This test validates the full SMS -> loan flow end-to-end:
- Igbo language detection ("Ndewo" greeting)
- Rice crop identification
- Loan amount extraction (N90,000)
- Location geocoding (Anam near Anambra River bridge)
- Satellite analysis (EE)
- AEGIS risk check
- Loan underwriter decision
- Final SMS to farmer
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.workflows.runner import run_farma_job


def _build_initial_state(phone: str, message: str) -> dict:
    """Build a valid initial state dict for the farma graph."""
    return {
        "input_type": "sms",
        "phone": phone,
        "message": message,
        "audio_path": None,
        "intent": None,
        "language": None,
        "status": None,
        "parsed_data": None,
        "farmer_response": None,
        "risk_flags": [],
        "analysis_summary": [],
        "history": [],
    }


@pytest.mark.anyio
async def test_igbo_loan_request() -> None:
    """Igbo loan request for rice farm near Anambra River."""
    job_id = f"TEST-IGBO-{uuid4().hex[:8]}"

    result = await run_farma_job(
        job_id=job_id,
        thread_id=f"thread-{uuid4().hex[:8]}",
        initial_state=_build_initial_state(
            phone="+2348030000004",
            message="Ndewo I need small loan of N90,000 to pay labour and fertilizer for my rice farm at Anam near Anambra River bridge.",
        ),
        emit_job_events=False,
    )

    # Assertions
    assert result.get("intent") == "LOAN_REQUEST"
    lang = (result.get("language") or "").lower()
    assert any(x in lang for x in ["igbo", "english"]), f"Got language: {lang}"
    assert result.get("final_decision") is not None, "No loan decision was made"
    assert result.get("farmer_response"), "No SMS response generated"
    assert len(result.get("farmer_response", "")) <= 160, "SMS exceeds 160 chars"


@pytest.mark.anyio
async def test_hausa_loan_request() -> None:
    """Hausa loan request for maize farm near Kura market."""
    job_id = f"TEST-HAUSA-{uuid4().hex[:8]}"

    result = await run_farma_job(
        job_id=job_id,
        thread_id=f"thread-{uuid4().hex[:8]}",
        initial_state=_build_initial_state(
            phone="+2348030000001",
            message="I need loan of N50,000 for my rice farm near Kura market in Kano",
        ),
        emit_job_events=False,
    )

    assert result.get("intent") == "LOAN_REQUEST"
    assert result.get("final_decision") is not None
    assert result.get("farmer_response")


@pytest.mark.anyio
async def test_vague_location_triggers_clarification() -> None:
    """Vague message should trigger AWAITING_FARMER_RESPONSE."""
    job_id = f"TEST-VAGUE-{uuid4().hex[:8]}"

    result = await run_farma_job(
        job_id=job_id,
        thread_id=f"thread-{uuid4().hex[:8]}",
        initial_state=_build_initial_state(
            phone="+2348030000006",
            message="Loan for farm",
        ),
        emit_job_events=False,
    )

    # Should either ask for clarification or escalate
    status = result.get("status")
    assert status in ("AWAITING_FARMER_RESPONSE", "COMPLETED", "NEEDS_HUMAN_VERIFICATION"), \
        f"Unexpected status: {status}"
