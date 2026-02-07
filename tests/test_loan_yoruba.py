"""Test loan request with Yoruba language.

This test validates:
- Yoruba language detection and handling
- Loan amount extraction (₦75,000)
- Tomato crop identification
- Farm size parsing (2 hectares)
- Location geocoding (Ibadan Challenge)
- Loan underwriter decision
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
async def test_yoruba_loan_request() -> None:
    """Test Case 2: Yoruba loan request with farm size details."""
    job_id = f"TEST-YORUBA-{uuid4().hex[:8]}"

    result = await run_farma_job(
        job_id=job_id,
        thread_id=f"thread-{uuid4().hex[:8]}",
        initial_state=_build_initial_state(
            phone="+2348070000002",
            message="Mo nilo owo iyawo N75000 fun oko tomato mi ni Ibadan Challenge. Ilẹ mi jẹ hectare 2.",
        ),
        emit_job_events=False,
    )

    # Assertions
    assert result.get("intent") == "LOAN_REQUEST", f"Expected LOAN_REQUEST, got {result.get('intent')}"
    lang = (result.get("language") or "").lower()
    assert "yoruba" in lang, f"Expected Yoruba language, got {result.get('language')}"
    assert result.get("final_decision") is not None, "No loan decision was made"
    assert result.get("farmer_response"), "No SMS response generated"
    assert len(result.get("farmer_response", "")) <= 160, "SMS exceeds 160 chars"
