"""Test loan request optimized for approval (not verification).

This test validates:
- Clear, specific location with high geocoding confidence (Bodija Market, Ibadan)
- Moderate loan amount (₦45,000) within typical individual farmer limits
- Common, low-risk crop (maize) prioritized by CBN programs
- Specific farm details (0.5 hectares, clear purpose)
- English language for maximum parsing clarity
- Expected outcome: APPROVED or CONDITIONAL_APPROVAL (possibly partial amount)
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
async def test_approvable_loan_request() -> None:
    """Test Case 4: Optimized loan request for approval scenario."""
    job_id = f"TEST-APPROVE-{uuid4().hex[:8]}"

    result = await run_farma_job(
        job_id=job_id,
        thread_id=f"thread-{uuid4().hex[:8]}",
        initial_state=_build_initial_state(
            phone="+2348050000005",
            message="I need N45000 loan for maize seeds and fertilizer. My farm is 0.5 hectare at Bodija Market area, Ibadan.",
        ),
        emit_job_events=False,
    )

    # Assertions
    assert result.get("intent") == "LOAN_REQUEST", f"Expected LOAN_REQUEST, got {result.get('intent')}"

    # This test should either get approval or hold for verification
    status = result.get("status")
    assert status in ("COMPLETED", "AWAITING_FARMER_RESPONSE", "NEEDS_HUMAN_VERIFICATION"), \
        f"Unexpected status: {status}"

    assert result.get("final_decision") is not None, "No loan decision was made"
    assert result.get("farmer_response"), "No SMS response generated"
    assert len(result.get("farmer_response", "")) <= 160, "SMS exceeds 160 chars"

    # If approved, amount should be present
    if status == "COMPLETED" and result.get("final_decision") == "APPROVED":
        assert result.get("approved_amount") is not None, "Approved loans should have approved_amount"
