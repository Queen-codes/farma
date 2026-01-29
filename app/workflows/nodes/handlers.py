from app.workflows.state import FarmaState


def loan_decision_node(state: FarmaState) -> dict:
    """
    Handler to validate loan approval/rejection from llm.
    """
    # TODO: CONSIDER FLOW FOR HUMAN IN THE LOOP AND ALSO MARKET STRATEGY: DO WE APPROVE THE LOANS ON THE FINANCING INST BEHALF AND FORWARD TO THEM??
    gemini_decision = state.get("final_decision", "REVIEW")
    risk_flags = state.get("risk_flags", [])

    print(f"decision:{gemini_decision}")

    # 1. Enforce Critical Overrides
    if "SECURITY_HOLD" in risk_flags:
        gemini_decision = "HELD"

    # 2. Normalize Status
    # Maps internal decision to workflow status
    status_map = {
        "APPROVED": "LOAN_APPROVED",
        "APPROVED_WITH_INSURANCE": "LOAN_APPROVED_INSURED",
        "REJECTED": "LOAN_REJECTED",
        "HELD": "LOAN_HELD",
        "REVIEW": "MANUAL_REVIEW_REQUIRED",
    }

    final_status = status_map.get(gemini_decision, "MANUAL_REVIEW_REQUIRED")

    return {
        "final_decision": gemini_decision,
        "status": final_status,
    }


def climate_advisory_handler(state: FarmaState) -> dict:
    """
    Compiles the final message for the farmer.
    """
    advisory = state.get("analysis_summary", [])

    # Ensure advisory is a list of strings
    if isinstance(advisory, str):
        advisory = [advisory]

    return {"analysis_summary": advisory}


# TODO: REVIEW ARCHITECTURE TO DETERMINE REDUNDANCY OF THIS HANDLERS
def disease_handler(state: FarmaState) -> dict:
    """Handles DISEASE_REPORT intent"""
    return {}


def weather_handler(state: FarmaState) -> dict:
    """Handles WEATHER_INQUIRY intent"""
    return {"analysis_summary": ["Current weather is stable for your region."]}


# TODO: SET UP LOGIC FOR BOTH
def human_escalation_handler(state: FarmaState) -> dict:
    """Handles HUMAN_ESCALATION intent"""
    return {}


def awaiting_response_handler(state: FarmaState) -> dict:
    """Handles AWAITING_FARMER_RESPONSE status"""
    return {}
