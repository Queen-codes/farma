"""AEGIS Synthesis Agent - Humanitarian and food security intelligence analysis.

Implements LangGraph ReAct agent with Gemini 3 for:
- Food Security Assessment
- Humanitarian Need Scoring
- Aid Delivery Prediction

All with full audit trail and source traceability.
"""

from .tools import SYNTHESIS_TOOLS
from .models import (
    # Thought structures
    ThoughtType,
    Thought,
    ReasoningChain,
    # Task decomposition
    SubTask,
    TaskPlan,
    # Sourced findings
    SourcedFinding,
    # Outputs
    VulnerablePopulation,
    HumanitarianAssessment,
    RiskFactor,
    RiskScore,
    PredictionFactor,
    Prediction,
    # Audit
    ToolCall,
    AuditEntry,
    AuditTrail,
    # Final report
    SynthesisReport,
)
from .agent import (
    synthesis_graph,
    run_synthesis,
    get_audit_trail,
    extract_final_response,
    AEGIS_FOCUS_STATES,
)

__all__ = [
    # Tools
    "SYNTHESIS_TOOLS",
    # Models
    "ThoughtType",
    "Thought",
    "ReasoningChain",
    "SubTask",
    "TaskPlan",
    "SourcedFinding",
    "VulnerablePopulation",
    "HumanitarianAssessment",
    "RiskFactor",
    "RiskScore",
    "PredictionFactor",
    "Prediction",
    "ToolCall",
    "AuditEntry",
    "AuditTrail",
    "SynthesisReport",
    # Agent
    "synthesis_graph",
    "run_synthesis",
    "get_audit_trail",
    "extract_final_response",
    "AEGIS_FOCUS_STATES",
]
