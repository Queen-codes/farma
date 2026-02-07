"""LangGraph workflow assembly for FARMA request processing.

This module wires parser nodes, intent routing gates, domain-specific engines,
and response delivery into one executable graph object (`farma_graph`).

Call flow:
1. Input route chooses SMS vs voice parser.
2. Intent gate validates readiness and routes by detected intent.
3. Intent-specific chains run (loan, disease, climate, or human escalation).
4. Response aggregation/sending finalizes farmer output and status.

Used by:
- `app.workflows.runner`, which invokes `farma_graph.astream(...)`.
"""

import logging
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.workflows.state import FarmaState
from app.workflows.nodes.parsers.sms_parser import sms_parser_node
from app.workflows.nodes.parsers.voice_parser import voice_parser_node
from app.workflows.nodes.disease.analyze import disease_generate_once
from app.workflows.nodes.disease.guardrails import disease_guardrails
from app.workflows.nodes.climate.geocode import geocode_location_deterministic
from app.workflows.nodes.climate.forecast import fetch_weather_forecast
from app.workflows.nodes.climate.chirps import fetch_recent_rainfall_chirps
from app.workflows.nodes.climate.advisory import gemini_climate_advisory
from app.workflows.nodes.loan.geocode import geocoding_node
from app.workflows.nodes.loan.satellite import satellite_analysis_node
from app.workflows.nodes.loan.aegis_context import aegis_risk_check_node
from app.workflows.nodes.loan.underwriter import loan_underwriter_node
from app.workflows.nodes.human.escalation import human_escalation_handler
from app.workflows.nodes.human.awaiting import awaiting_response_handler

logger = logging.getLogger(__name__)


def route_input(state: FarmaState) -> Literal["sms_parser", "voice_parser"]:
    """Choose parser entry node from inbound request type.

    Args:
        state: Current workflow state containing `input_type`.

    Returns:
        `"voice_parser"` when `input_type == "voice"`, otherwise `"sms_parser"`.

    Raises:
        None: Missing/unknown input types default to SMS parser.

    Side Effects:
        None.

    Latency:
        Constant-time local routing.
    """
    if state.get("input_type") == "voice":
        return "voice_parser"
    return "sms_parser"


def intent_gate(state: FarmaState) -> dict:
    """Transition request status before intent fan-out.

    Args:
        state: Current workflow state.

    Returns:
        State update dict. Sets `status` to `ANALYSIS_ONGOING` when parser
        finished with `READY_FOR_ANALYSIS`; returns empty dict otherwise.

    Raises:
        None: This gate does not intentionally raise.

    Side Effects:
        Emits an informational log line when analysis starts.

    Latency:
        Constant-time local checks.
    """
    status = state.get("status")
    if status == "READY_FOR_ANALYSIS":
        logger.info("Request received. Starting analysis.")
        # Return dict with update - don't mutate state directly
        return {"status": "ANALYSIS_ONGOING"}

    # Return empty dict if no changes needed
    return {}


def route_by_intent(
    state: FarmaState,
) -> Literal[
    "geocoding_node",
    "satellite_analysis_node",
    "disease_generate_once",
    "climate_geocode",
    "human",
    "awaiting",
]:
    """Route parsed request into intent-specific workflow branch.

    Args:
        state: Current workflow state with `intent`, `status`, and coordinates.

    Returns:
        Node name for the next step in graph execution.

    Raises:
        None: Unknown intents are routed to `"human"`.

    Side Effects:
        None.

    Latency:
        Constant-time dictionary routing.
    """

    if state.get("status") == "AWAITING_FARMER_RESPONSE":
        # TOOO: Action update if we havent heard from them in 72 hours to let them know we're still waiting to hear from them.
        return "awaiting"

    intent = state.get("intent", "HUMAN_ESCALATION")

    # Byp geocoding if coordinates already exist(For farmers who might be able to drop a whatsapp pin or returning farmers)
    if intent == "LOAN_REQUEST" and state.get("coordinates"):
        return "satellite_analysis_node"

    route_map = {
        "LOAN_REQUEST": "geocoding_node",
        "DISEASE_REPORT": "disease_generate_once",
        "WEATHER_INQUIRY": "climate_geocode",
        "HUMAN_ESCALATION": "human",
    }
    return route_map.get(intent, "human")


def response_aggregator(state: FarmaState) -> dict:
    """Normalize downstream response text into one SMS-safe output field.

    Args:
        state: Current workflow state that may include `sms_text` and/or
            `farmer_response`.

    Returns:
        Dict with normalized `farmer_response` capped at 160 characters.

    Raises:
        None: This function does not intentionally raise.

    Side Effects:
        None.

    Latency:
        Constant-time string handling only.
    """
    sms_text = (state.get("sms_text") or "").strip()
    farmer_response = (state.get("farmer_response") or "").strip()
    out = sms_text or farmer_response
    out = out.strip()
    if len(out) > 160:
        out = out[:157].rstrip() + "..."
    return {"farmer_response": out}


def sms_sender_node(state: FarmaState) -> dict:
    """Emit response delivery events and finalize workflow status.

    Args:
        state: Current workflow state containing phone and outgoing response.

    Returns:
        Empty dict when workflow should remain in waiting/human-verification
        states, otherwise `{"status": "COMPLETED"}`.

    Raises:
        None: Event emission failures are handled by downstream utilities.

    Side Effects:
        Emits `response_started/response_done` custom events and writes logs.
        Imports `emit_event` lazily to avoid cyclic imports at module load.

    Latency:
        Local event/log emission; external SMS integration is currently mocked.
    """
    from app.workflows.job_events import emit_event

    emit_event("response_started", step="response")
    response = state.get("farmer_response")
    phone = state.get("phone")

    logger.info("SMS send initiated: phone=%s message=%s", phone, response)

    emit_event("response_done", status="completed", step="response")
    existing_status = state.get("status")
    if existing_status in {"AWAITING_FARMER_RESPONSE", "NEEDS_HUMAN_VERIFICATION"}:
        return {}
    return {"status": "COMPLETED"}


# build graph
builder = StateGraph(FarmaState)

# Nodes
builder.add_node("sms_parser", sms_parser_node)
builder.add_node("voice_parser", voice_parser_node)
builder.add_node("intent_gate", intent_gate)

# Engines
builder.add_node("geocoding_node", geocoding_node)
builder.add_node("satellite_analysis_node", satellite_analysis_node)
builder.add_node("aegis_risk_check", aegis_risk_check_node)  # Aegis integration
builder.add_node("loan_underwriter", loan_underwriter_node)
builder.add_node("disease_generate_once", disease_generate_once)
builder.add_node("disease_guardrails", disease_guardrails)

# Climate nodes (WEATHER_INQUIRY)
builder.add_node("climate_geocode", geocode_location_deterministic)
builder.add_node("weather_fetch", fetch_weather_forecast)
builder.add_node("chirps_rainfall", fetch_recent_rainfall_chirps)
builder.add_node("climate_advisory", gemini_climate_advisory)

# Handlers
builder.add_node("human", human_escalation_handler)
builder.add_node("awaiting", awaiting_response_handler)
builder.add_node("response_aggregator", response_aggregator)
builder.add_node("sms_sender", sms_sender_node)

# Edges
builder.add_conditional_edges(START, route_input)
builder.add_edge("sms_parser", "intent_gate")
builder.add_edge("voice_parser", "intent_gate")

builder.add_conditional_edges("intent_gate", route_by_intent)

# Loan Flow: Geocoding -> Satellite -> AEGIS -> Underwriter -> Sender
builder.add_edge("geocoding_node", "satellite_analysis_node")
builder.add_edge("satellite_analysis_node", "aegis_risk_check")
builder.add_edge("aegis_risk_check", "loan_underwriter")
builder.add_edge("loan_underwriter", "sms_sender")

# Disease Flow: one-call -> guardrails -> response -> sender
builder.add_edge("disease_generate_once", "disease_guardrails")
builder.add_edge("disease_guardrails", "response_aggregator")
builder.add_edge("response_aggregator", "sms_sender")

# Climate Flow: geocode -> (forecast + chirps in parallel) -> advisory -> response -> sender
builder.add_edge("climate_geocode", "weather_fetch")
builder.add_edge("climate_geocode", "chirps_rainfall")
builder.add_edge(["weather_fetch", "chirps_rainfall"], "climate_advisory")
builder.add_edge("climate_advisory", "response_aggregator")

# Termination
builder.add_edge("human", "response_aggregator")
builder.add_edge("awaiting", "sms_sender")
builder.add_edge("sms_sender", END)

# Compile with Memory
memory = MemorySaver()
farma_graph = builder.compile(checkpointer=memory)
