from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.workflows.state import FarmaState
from app.workflows.nodes.sms_parser import sms_parser_node
from app.workflows.nodes.voice_parser import voice_parser_node
from app.workflows.nodes.disease_engine import disease_generator, disease_evaluator
from app.workflows.nodes.geospatial_engine import (
    geocoding_node,
    satellite_analysis_node,
)
from app.workflows.nodes.narrative_engine import narrative_orchestration_node
from app.workflows.nodes.handlers import (
    loan_decision_node,
    climate_advisory_handler,
    weather_handler,
    human_escalation_handler,
    awaiting_response_handler,
)

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from app.config import GOOGLE_API_KEY, MODEL_FLASH

# flash for aggregator/synthesis
synthesis_llm = ChatGoogleGenerativeAI(
    model=MODEL_FLASH, google_api_key=GOOGLE_API_KEY, temperature=0.3
)


def route_input(state: FarmaState) -> Literal["sms_parser", "voice_parser"]:
    """Routes to the correct parser based on input type"""
    if state.get("input_type") == "voice":
        return "voice_parser"
    return "sms_parser"


def intent_gate(state: FarmaState) -> FarmaState:
    """Centralized Gateway for routing and safety logic."""
    status = state.get("status")
    if status == "READY_FOR_ANALYSIS":
        print("Request Received. Starting Analysis...")
        # send a text to let them know their message has been recieved??
        state["status"] = "ANALYSIS_ONGOING"

    return state


def route_by_intent(
    state: FarmaState,
) -> Literal[
    "geocoding_node",
    "satellite_analysis_node",
    "disease_generator",
    "weather",
    "human",
    "awaiting",
]:
    """Routes to the appropriate engine based on intent"""

    if state.get("status") == "AWAITING_FARMER_RESPONSE":
        # TOOO: Action update if we havent heard from them in 72 hours to let them know we're still waiting to hear from them.
        return "awaiting"

    intent = state.get("intent", "HUMAN_ESCALATION")

    # Byp geocoding if coordinates already exist(For farmers who might be able to drop a whatsapp pin)
    if intent == "LOAN_REQUEST" and state.get("coordinates"):
        return "satellite_analysis_node"

    route_map = {
        "LOAN_REQUEST": "geocoding_node",
        "DISEASE_REPORT": "disease_generator",
        "WEATHER_INQUIRY": "weather",
        "HUMAN_ESCALATION": "human",
    }
    return route_map.get(intent, "human")


def route_disease_evaluation(
    state: FarmaState,
) -> Literal["disease_generator", "response_aggregator"]:
    """Optimizer Loop: Decide whether to re-run or finish."""
    analysis = state.get("disease_analysis", {})
    if analysis.get("is_verified") or analysis.get("iterations", 0) >= 2:
        return "response_aggregator"
    return "disease_generator"


def response_aggregator(state: FarmaState) -> dict:
    lang = state.get("language", "English")
    parsed = state.get("parsed_data", {})
    advisories = state.get("analysis_summary", [])
    intent = state.get("intent", "LOAN_REQUEST")
    decision = state.get("final_decision", "REVIEW")
    zone = state.get("nigeria_aez_context", {}).get("zone_name", "your area")

    context = "\n- ".join(advisories)

    if intent == "DISEASE_REPORT":
        prompt = f"""
        You are a digital agricultural officer in Nigeria.
        Language: {lang}
        Intent: Plant Disease Report
        Advice to include: {context}
        
        Task: Write a single, unified SMS in {lang}. 
        Tone: Empathetic, professional, and clear. 
        Constraint: Max 160 characters (Standard SMS length). 
        Acknowledge the disease diagnosis and provide the specific treatment steps from the context.
        """
    else:
        prompt = f"""
        You are a digital agricultural officer in Nigeria.
        Language: {lang}
        Region: {zone}
        Loan Decision: {decision}
        Advice to include: {context}
        
        Task: Write a single, unified SMS in {lang}. 
        Tone: Professional, clear, and encouraging. 
        Constraint: Max 160 characters (Standard SMS length). 
        If approved, tell them next steps. If rejected, provide the specific agronomic advice from the context.
        """

    try:
        response = synthesis_llm.invoke([HumanMessage(content=prompt)])

        # Handle cases where response.content is a list (Gemini parts)
        if isinstance(response.content, list):
            response_text = " ".join(
                [
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in response.content
                ]
            )
        else:
            response_text = str(response.content)

        response_text = response_text.strip()
    except Exception as e:
        print(f"Error in response generation: {e}")
        response_text = f"Farma Update: Your loan for {zone} is under status {decision}. Advice: {context[:50]}"

    return {"farmer_response": response_text}


def sms_sender_node(state: FarmaState) -> dict:
    """
    handles the sending of of messages to farmers
    Simulates sending via Twilio/Africa's Talking.
    """
    # TODO: make this the only source of thruth for sending sms, no matter the intent, if you want to send a message either for more deets, or actual response fufuilled
    response = state.get("farmer_response")
    phone = state.get("phone")

    print(f"sms sending starting now")
    print(f"TO: {phone}")
    print(f"MESSAGE: {response}")
    print(f"sent..")

    return {"status": "COMPLETED"}


def route_satellite_results(
    state: FarmaState,
) -> Literal["geocoding_node", "narrative_orchestration"]:
    """Implements the rediriection if water/barren detected."""
    risk_flags = state.get("risk_flags", [])
    if "LOCATION_REVIEW_REQUIRED" in risk_flags:
        print(
            "REDIRECT: Location review required. Retrying geocoding with offset logic."
        )
        return "geocoding_node"
    return "narrative_orchestration"


# build graph
builder = StateGraph(FarmaState)

# Nodes
builder.add_node("sms_parser", sms_parser_node)
builder.add_node("voice_parser", voice_parser_node)
builder.add_node("intent_gate", intent_gate)

# Engines
builder.add_node("geocoding_node", geocoding_node)
builder.add_node("satellite_analysis_node", satellite_analysis_node)
builder.add_node("narrative_orchestration", narrative_orchestration_node)
builder.add_node("disease_generator", disease_generator)
builder.add_node("disease_evaluator", disease_evaluator)

# Handlers
builder.add_node("loan_decision", loan_decision_node)
builder.add_node("climate_advisory", climate_advisory_handler)
builder.add_node("weather", weather_handler)
builder.add_node("human", human_escalation_handler)
builder.add_node("awaiting", awaiting_response_handler)
builder.add_node("response_aggregator", response_aggregator)
builder.add_node("sms_sender", sms_sender_node)

# Edges
builder.add_conditional_edges(
    START, route_input, {"sms_parser": "sms_parser", "voice_parser": "voice_parser"}
)
builder.add_edge("sms_parser", "intent_gate")
builder.add_edge("voice_parser", "intent_gate")

builder.add_conditional_edges(
    "intent_gate",
    route_by_intent,
    {
        "geocoding_node": "geocoding_node",
        "satellite_analysis_node": "satellite_analysis_node",
        "disease_generator": "disease_generator",
        "weather": "weather",
        "human": "human",
        "awaiting": "awaiting",
    },
)

# Loan Flow: Geocoding -> Satellite -> (Conditional Route) -> Narrative -> Parallel(Decision & Advisory) -> Response -> Sender
builder.add_edge("geocoding_node", "satellite_analysis_node")
builder.add_conditional_edges(
    "satellite_analysis_node",
    route_satellite_results,
    {
        "geocoding_node": "geocoding_node",
        "narrative_orchestration": "narrative_orchestration",
    },
)
builder.add_edge("narrative_orchestration", "loan_decision")
builder.add_edge("narrative_orchestration", "climate_advisory")
builder.add_edge("loan_decision", "response_aggregator")
builder.add_edge("climate_advisory", "response_aggregator")
builder.add_edge("response_aggregator", "sms_sender")

# Disease Loop
builder.add_edge("disease_generator", "disease_evaluator")
builder.add_conditional_edges(
    "disease_evaluator",
    route_disease_evaluation,
    {
        "disease_generator": "disease_generator",
        "response_aggregator": "response_aggregator",
    },
)

# Termination
builder.add_edge("weather", "response_aggregator")
builder.add_edge("human", END)
builder.add_edge("awaiting", END)
builder.add_edge("sms_sender", END)

# Compile with Memory
memory = MemorySaver()
farma_graph = builder.compile(checkpointer=memory)

# TODO: ENSURE PERSISTENCE 1. IF A FARMER TAKES longer than 72 hours to reply, send a message reminding them of their reply and resend the message you sent initially, incase they missed it
# also whenever a famer responds, remeber the farmer an d pick up from the exact flow
