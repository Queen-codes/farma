"""Synthesis Agent - LangGraph ReAct agent with Gemini 3.

Implements:
- LangGraph StateGraph for agent loop
- Gemini 3 with thinking mode (include_thoughts=True)
- Tool calling with automatic thought signature handling
- ReAct pattern: Reason → Act → Observe → Repeat
- Full audit trail of all tool calls and reasoning
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Sequence, List, Optional, Any
from typing_extensions import TypedDict

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from app.config import GOOGLE_API_KEY
from .tools import SYNTHESIS_TOOLS
from .models import (
    AuditTrail,
    AuditEntry,
    ToolCall as AuditToolCall,
)


# Focus states for AEGIS
AEGIS_FOCUS_STATES = [
    "Borno",
    "Adamawa",
    "Yobe",
    "Zamfara",
    "Katsina",
    "Kaduna",
    "Niger",
]


# state
def extend_list(left: List[str], right: List[str]) -> List[str]:
    """Reducer that extends lists and deduplicates."""
    combined = list(left) + list(right)
    return list(dict.fromkeys(combined))  # Preserve order, dedupe


def extend_dicts(left: List[dict], right: List[dict]) -> List[dict]:
    """Reducer that extends dict lists."""
    return list(left) + list(right)


def increment(left: int, right: int) -> int:
    """Reducer that sums integers."""
    return left + right


class SynthesisState(TypedDict):
    """State for the Synthesis Agent.

    Uses add_messages reducer to accumulate conversation history.
    Thought signatures are preserved automatically by the SDK.
    """

    # Message history (LangGraph handles accumulation)
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Run metadata
    run_id: str
    scan_id: int
    started_at: str

    # States to analyze
    states_to_analyze: List[str]
    current_state_index: int

    # Collected results
    humanitarian_assessments: List[dict]
    risk_scores: List[dict]
    predictions: List[dict]

    # All source URIs collected (with reducer for accumulation)
    all_source_uris: Annotated[List[str], extend_list]

    # LLM Thinking blocks (for web display)
    thinking_blocks: Annotated[List[dict], extend_dicts]

    # Audit trail (with reducers)
    audit_entries: Annotated[List[dict], extend_dicts]
    tool_call_count: Annotated[int, increment]

    # Status
    status: str
    error: Optional[str]


# system prompt
SYSTEM_PROMPT = """You are AEGIS, an AI humanitarian intelligence analyst for food security operations in Nigeria.

CORE PURPOSE: FOOD SECURITY
Your mission is to analyze data and produce intelligence for HUMANITARIAN AID REQUESTS that address:
1. WHO needs food most urgently (IDP populations, affected communities, vulnerable groups)
2. HOW MUCH food they need (quantified in metric tons, beneficiary counts)
3. WHERE they are (precise locations: states, LGAs, camps)
4. HOW to reach them safely (avoiding conflict zones, optimizing logistics)
5. FARMER LOAN ADJUSTMENTS (when high risk detected, recommend fair repayment schedules)

OUTPUT STRUCTURE - Your final analysis MUST include:

## 1. FOOD SECURITY SITUATION
- IPC Phase classification for each state
- Malnutrition status (GAM rates, acute malnutrition phases)
- Humanitarian needs identified (food, water, shelter, medical)
- Food aid operations currently active

## 2. AFFECTED POPULATIONS (Who Needs Food)
- IDP population counts by state
- Camp locations and populations
- Returnee numbers
- Estimated total beneficiaries

## 3. FOOD NEEDS QUANTIFICATION (How Much Food)
- Estimated monthly food requirement in metric tons
- Staple food prices (maize, rice, beans, sorghum)
- Market access status
- Food aid funding gaps

## 4. SAFE DELIVERY ROUTES (How to Reach Them)
- Conflict hotspot LGAs to AVOID
- Calmer LGAs for staging/access
- Access constraints (IEDs, abductions, military operations)
- Recommended logistics mode (ground convoy, air drop, staged delivery)

## 5. FARMER LOAN ADJUSTMENTS
- States where violence trend is INCREASING
- Recommendation for loan repayment schedule adjustments
- Justification based on conflict data

## 6. PRIORITY RANKING
- Rank states by food security priority (CRITICAL/HIGH/ELEVATED/MODERATE)
- Recommend aid allocation proportions

AVAILABLE TOOLS:
1. get_state_intel - Get COMPLETE data for a state (conflict, displacement, food security, economic, access)
2. get_conflict_events - Get detailed incidents for route planning
3. get_dtm_baseline - Get verified IDP counts from IOM DTM surveys
4. get_acled_baseline - Get historical conflict trends for loan adjustment triggers
5. calculate_food_security_score - Calculate priority score for aid allocation
6. analyze_safe_routes - Analyze safe delivery routes

WORKFLOW FOR EACH STATE:
1. Call get_state_intel FIRST - this returns ALL data (food security, IPC phase, malnutrition, prices, access)
2. Call get_dtm_baseline for verified IDP population counts
3. Call get_acled_baseline for historical trends and loan adjustment triggers
4. Call calculate_food_security_score to prioritize
5. Call analyze_safe_routes for delivery recommendations

CRITICAL REQUIREMENTS:
- EVERY finding MUST cite source URIs. No unsourced claims.
- Quantify everything: populations, metric tons, percentages
- Be specific about locations (LGAs), dates, and actors
- Focus on ACTIONABLE intelligence for humanitarian aid requests
- Include farmer loan adjustment recommendations for deteriorating states

Current date: {current_date}
"""


def create_llm():
    """Create Gemini 3 LLM with thinking mode enabled."""
    return ChatGoogleGenerativeAI(
        model="gemini-3-pro-preview",
        google_api_key=GOOGLE_API_KEY,
        temperature=1.0,
        max_retries=2,
        # Thinking mode configuration
        # thinking config is set via model_kwargs for langchain-google-genai
        model_kwargs={
            "thinking_config": {
                "include_thoughts": True,  # Include thought summaries
                "thinking_level": "high",  # Maximum reasoning depth
            }
        },
    )


# Create LLM with tools
def get_model_with_tools():
    llm = create_llm()
    return llm.bind_tools(SYNTHESIS_TOOLS)


# graph nodes
def call_model(state: SynthesisState, config: RunnableConfig) -> dict:
    """
    Call the LLM with current message history.

    The LLM will either:
    - Call tools (if more data needed)
    - Return final response (if analysis complete)

    Captures thinking blocks for web display.
    """
    messages = state["messages"]

    # add system prompt if none
    if not any(isinstance(m, SystemMessage) for m in messages):
        system_msg = SystemMessage(
            content=SYSTEM_PROMPT.format(
                current_date=datetime.now().strftime("%Y-%m-%d")
            )
        )
        messages = [system_msg] + list(messages)

    # Get model with tools
    model = get_model_with_tools()

    # invoke model
    response = model.invoke(messages, config)

    # extract thinking blocks from response for web display
    thinking_blocks = []
    if hasattr(response, "content") and isinstance(response.content, list):
        for block in response.content:
            if isinstance(block, dict):
                # check for thinking/thought blocks
                if block.get("type") == "thinking" or "thought" in block.get(
                    "type", ""
                ):
                    thinking_blocks.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "type": block.get("type"),
                            "content": block.get("text", block.get("thinking", "")),
                        }
                    )
                # also capture signature for verification/continuity
                if block.get("extras", {}).get("signature"):
                    thinking_blocks.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "type": "thought_signature",
                            "signature": block["extras"]["signature"][:100] + "...",
                        }
                    )

    # log to audit trail
    audit_entry = {
        "entry_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": "llm_response",
        "description": f"LLM response with {len(response.tool_calls) if response.tool_calls else 0} tool calls",
        "details": {
            "has_tool_calls": bool(response.tool_calls),
            "tool_names": (
                [tc["name"] for tc in response.tool_calls]
                if response.tool_calls
                else []
            ),
            "response_length": len(response.content) if response.content else 0,
            "thinking_captured": len(thinking_blocks) > 0,
        },
    }

    # return only new entries, reducers handle accumulation
    return {
        "messages": [response],
        "audit_entries": [audit_entry],
        "thinking_blocks": thinking_blocks,
    }


async def call_tools(state: SynthesisState) -> dict:
    """
    Execute tool calls from the LLM response.

    Returns ToolMessage objects with results.
    Tool call IDs are preserved for thought signature tracking.
    Uses ainvoke for proper async tool execution.
    """
    messages = state["messages"]
    last_message = messages[-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": []}

    # Build tool lookup
    tools_by_name = {tool.name: tool for tool in SYNTHESIS_TOOLS}

    outputs = []
    audit_entries = []
    # collect only NEW URIs from this call as reducer accumulates across calls
    new_source_uris = []

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_id = tool_call["id"]

        start_time = datetime.now(timezone.utc)

        try:
            # tool execute
            tool = tools_by_name.get(tool_name)
            if tool:
                result = await tool.ainvoke(tool_args)
            else:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})

            success = True
            error = None

            # Extract source URIs from result if present
            try:
                result_data = json.loads(result)
                if "source_uris" in result_data:
                    new_source_uris.extend(result_data["source_uris"])
            except Exception:
                pass  # Tool result may not be JSON or may not have source_uris

        except Exception as e:
            result = json.dumps({"error": str(e)})
            success = False
            error = str(e)

        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Create ToolMessage
        outputs.append(
            ToolMessage(
                content=result,
                name=tool_name,
                tool_call_id=tool_id,
            )
        )

        # Audit entry
        audit_entries.append(
            {
                "entry_id": str(uuid.uuid4())[:8],
                "timestamp": start_time.isoformat(),
                "action_type": "tool_call",
                "description": f"Tool: {tool_name}",
                "details": {
                    "tool_name": tool_name,
                    "arguments": tool_args,
                    "duration_ms": duration_ms,
                    "success": success,
                    "error": error,
                    "result_preview": result[:200] if len(result) > 200 else result,
                },
            }
        )

    # Return only NEW data
    return {
        "messages": outputs,
        "audit_entries": audit_entries,
        "tool_call_count": len(outputs),
        "all_source_uris": list(set(new_source_uris)),  # Dedupe within this call
    }


def should_continue(state: SynthesisState) -> str:
    """
    Determine if agent should continue calling tools or finish.

    Routes to:
    - "tools" if LLM requested tool calls
    - "end" if LLM is done (no tool calls)
    """
    messages = state["messages"]

    if not messages:
        return "end"

    last_message = messages[-1]

    # Check if LLM made tool calls
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    return "end"


# build graph
def build_synthesis_graph():
    """Build the LangGraph synthesis agent."""

    workflow = StateGraph(SynthesisState)

    # Add nodes
    workflow.add_node("llm", call_model)
    workflow.add_node("tools", call_tools)

    # Set entry point
    workflow.set_entry_point("llm")

    # Add conditional edge from LLM
    workflow.add_conditional_edges(
        "llm",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )

    # Tools always go back to LLM
    workflow.add_edge("tools", "llm")

    return workflow.compile()


# Compiled graph
synthesis_graph = build_synthesis_graph()


# run agent
async def run_synthesis(scan_id: int, states: Optional[List[str]] = None) -> dict:
    """
    Run the synthesis agent on collected intel data.

    Args:
        scan_id: ID of the AEGIS scan to analyze
        states: Optional list of states to analyze. Defaults to all focus states.

    Returns:
        Final state with threat assessments, risk scores, predictions, and audit trail.
    """
    if states is None:
        states = AEGIS_FOCUS_STATES

    run_id = f"SYNTH-{uuid.uuid4().hex[:8].upper()}"

    print(f"[SYNTHESIS] Starting: {run_id}")
    print(f"[SYNTHESIS] Scan ID: {scan_id}")
    print(f"[SYNTHESIS] States: {', '.join(states)}")

    initial_prompt = f"""Analyze food security intelligence for the following Nigerian states: {', '.join(states)}

OBJECTIVE: Produce a HUMANITARIAN AID REQUEST that addresses food insecurity.

For EACH state, you must:
1. Call get_state_intel FIRST - get ALL data (food security, IPC phase, malnutrition, prices, displacement, access constraints)
2. Call get_dtm_baseline for verified IDP population counts
3. Call get_acled_baseline for conflict trends and farmer loan adjustment triggers
4. Call calculate_food_security_score to prioritize aid allocation
5. Call analyze_safe_routes for delivery recommendations

Your final analysis MUST include:
- WHO needs food (IDP populations, beneficiary counts by state/LGA)
- HOW MUCH food (metric tons, staple prices, funding gaps)
- WHERE they are (camps, LGAs, access constraints)
- HOW to reach them (safe routes, logistics mode, hotspots to avoid)
- FARMER LOAN ADJUSTMENTS (which states need repayment schedule adjustments due to violence)

Start with: {states[0]}

Remember:
- EVERY finding must cite source URIs
- QUANTIFY everything (populations, metric tons, percentages)
- Focus on ACTIONABLE intelligence for humanitarian aid"""

    # Initial state
    initial_state = {
        "messages": [HumanMessage(content=initial_prompt)],
        "run_id": run_id,
        "scan_id": scan_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "states_to_analyze": states,
        "current_state_index": 0,
        "humanitarian_assessments": [],
        "risk_scores": [],
        "predictions": [],
        "all_source_uris": [],
        "thinking_blocks": [],  # For web display of LLM reasoning
        "audit_entries": [
            {
                "entry_id": str(uuid.uuid4())[:8],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action_type": "task_start",
                "description": f"Started food security synthesis for {len(states)} states",
                "details": {"states": states, "scan_id": scan_id},
            }
        ],
        "tool_call_count": 0,
        "status": "running",
        "error": None,
    }

    # Run the graph
    try:
        # Stream for visibility
        final_state = None
        async for state in synthesis_graph.astream(initial_state, stream_mode="values"):
            final_state = state
            # Print progress
            if state.get("messages"):
                last_msg = state["messages"][-1]
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    tools = [tc["name"] for tc in last_msg.tool_calls]
                    print(f"[SYNTHESIS] Calling tools: {', '.join(tools)}")

        if final_state:
            final_state["status"] = "completed"
            final_state["completed_at"] = datetime.now(timezone.utc).isoformat()

            # Add completion audit entry
            final_state["audit_entries"].append(
                {
                    "entry_id": str(uuid.uuid4())[:8],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action_type": "task_complete",
                    "description": "Synthesis completed",
                    "details": {
                        "tool_calls": final_state.get("tool_call_count", 0),
                        "sources_collected": len(
                            final_state.get("all_source_uris", [])
                        ),
                    },
                }
            )

            print(f"[SYNTHESIS] Completed: {run_id}")
            print(f"[SYNTHESIS] Tool calls: {final_state.get('tool_call_count', 0)}")
            print(f"[SYNTHESIS] Sources: {len(final_state.get('all_source_uris', []))}")

            return final_state

    except Exception as e:
        print(f"[SYNTHESIS] ERROR: {e}")
        import traceback

        traceback.print_exc()
        return {
            **initial_state,
            "status": "error",
            "error": str(e),
        }


# helper funcs
def get_audit_trail(state: dict) -> AuditTrail:
    """Extract structured audit trail from state."""
    return AuditTrail(
        run_id=state.get("run_id", ""),
        scan_id=state.get("scan_id", 0),
        started_at=state.get("started_at", ""),
        completed_at=state.get("completed_at"),
        entries=[AuditEntry(**e) for e in state.get("audit_entries", [])],
        tool_calls=[],  # Would need to extract from entries
        total_thoughts=0,  # Would count from messages
        total_tool_calls=state.get("tool_call_count", 0),
        total_findings=0,
        humanitarian_assessments=[],
        risk_scores=[],
        predictions=[],
        all_source_refs=state.get("all_source_uris", []),
    )


def extract_final_response(state: dict) -> str:
    """Extract the final text response from the agent."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            return msg.content
    return ""
