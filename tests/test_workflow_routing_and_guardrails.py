"""Workflow routing and disease guardrail regression tests.

This module verifies deterministic graph routing decisions and safety-policy
state transitions in disease guardrails without invoking full end-to-end runs.
"""

from __future__ import annotations

import pytest

from app.workflows.graph import response_aggregator, route_by_intent, route_input, sms_sender_node
from app.workflows.nodes.disease import guardrails as disease_guardrails_mod


def test_route_input_sms_and_voice() -> None:
    """Ensure input router selects SMS or voice parser based on `input_type`."""
    assert route_input({"input_type": "sms"}) == "sms_parser"
    assert route_input({"input_type": "voice"}) == "voice_parser"


def test_route_by_intent_with_existing_coordinates_skips_geocoding() -> None:
    """Ensure loan requests with existing coordinates skip geocode node."""
    state = {
        "intent": "LOAN_REQUEST",
        "coordinates": {"lat": 1.0, "lng": 2.0},
        "status": "READY_FOR_ANALYSIS",
    }
    assert route_by_intent(state) == "satellite_analysis_node"


def test_route_by_intent_respects_awaiting_status() -> None:
    """Ensure awaiting farmer status routes directly to awaiting handler."""
    assert route_by_intent({"status": "AWAITING_FARMER_RESPONSE"}) == "awaiting"


def test_response_aggregator_prefers_sms_text_and_truncates() -> None:
    """Ensure response aggregator prioritizes sms_text and enforces 160 chars."""
    long = "x" * 200
    out = response_aggregator({"sms_text": long, "farmer_response": "fallback"})
    assert out["farmer_response"].endswith("...")
    assert len(out["farmer_response"]) == 160


def test_sms_sender_node_preserves_waiting_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure sender node does not override waiting/verification statuses."""
    monkeypatch.setattr("app.workflows.job_events.emit_event", lambda *a, **k: None)
    out = sms_sender_node({"status": "AWAITING_FARMER_RESPONSE", "phone": "+1"})
    assert out == {}


@pytest.mark.anyio
async def test_disease_guardrails_replaces_unsafe_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure unsafe guidance is replaced and corresponding flag is set."""

    async def _fake_translate(
        message: str,
        language: str,
        context: str = "",
    ) -> str:
        """Return deterministic translated marker for safety-message assertions."""
        return f"SAFE:{language}:{context}:{message[:20]}"

    monkeypatch.setattr(disease_guardrails_mod, "translate_to_farmer_language", _fake_translate)
    monkeypatch.setattr(disease_guardrails_mod, "emit_event", lambda *a, **k: None)

    state = {
        "language": "Hausa",
        "status": "READY_FOR_ANALYSIS",
        "risk_flags": [],
        "sms_text": "Use kerosene on leaves",
        "disease_analysis": {
            "confidence": 0.9,
            "risk_score": 0.2,
            "needs_more_info": False,
            "treatment_steps": ["Apply diesel"],
        },
    }
    out = await disease_guardrails_mod.disease_guardrails(state)
    assert out["status"] == "READY_FOR_ANALYSIS"
    assert "UNSAFE_ADVICE_REMOVED" in out["risk_flags"]
    assert out["sms_text"].startswith("SAFE:Hausa:disease_safety:")


@pytest.mark.anyio
async def test_disease_guardrails_low_confidence_requests_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure low-confidence disease outputs ask follow-up clarification."""

    async def _fake_translate(
        message: str,
        language: str,
        context: str = "",
    ) -> str:
        """Return deterministic translated marker for clarification assertions."""
        return f"Q:{language}:{context}"

    monkeypatch.setattr(disease_guardrails_mod, "translate_to_farmer_language", _fake_translate)
    monkeypatch.setattr(disease_guardrails_mod, "emit_event", lambda *a, **k: None)

    state = {
        "language": "Igbo",
        "risk_flags": [],
        "disease_analysis": {
            "confidence": 0.3,
            "risk_score": 0.2,
            "needs_more_info": True,
            "clarifying_question": "",
            "treatment_steps": [],
        },
    }
    out = await disease_guardrails_mod.disease_guardrails(state)
    assert out["status"] == "AWAITING_FARMER_RESPONSE"
    assert out["pending_question_type"] == "disease"
    assert out["pending_question"].startswith("Q:Igbo:disease_clarification")


@pytest.mark.anyio
async def test_disease_guardrails_high_risk_and_low_confidence_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure high risk with low confidence escalates to human verification."""

    async def _fake_translate(
        message: str,
        language: str,
        context: str = "",
    ) -> str:
        """Return deterministic translated marker for escalation assertions."""
        return f"T:{context}"

    monkeypatch.setattr(disease_guardrails_mod, "translate_to_farmer_language", _fake_translate)
    monkeypatch.setattr(disease_guardrails_mod, "emit_event", lambda *a, **k: None)

    state = {
        "language": "Yoruba",
        "risk_flags": [],
        "disease_analysis": {
            "confidence": 0.5,
            "risk_score": 0.95,
            "needs_more_info": False,
            "clarifying_question": "",
            "treatment_steps": [],
        },
    }
    out = await disease_guardrails_mod.disease_guardrails(state)
    assert out["status"] == "NEEDS_HUMAN_VERIFICATION"
    assert "HIGH_DISEASE_RISK" in out["risk_flags"]
