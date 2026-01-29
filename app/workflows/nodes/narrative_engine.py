import ee
from langchain_google_genai import ChatGoogleGenerativeAI
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional
from app.workflows.state import FarmaState
from app.config import (
    GOOGLE_API_KEY,
    MODEL_PRO,
)


# TODO: Remove tool binding from gemini 3 pro
llm_brain = ChatGoogleGenerativeAI(
    model=MODEL_PRO,
    google_api_key=GOOGLE_API_KEY,
)


# reasoning schema
class UnderwritingDecision(BaseModel):
    """Structured output for the Nigerian Credit Scorer."""

    decision_intent: str = Field(description="One of: APPROVED, REJECTED, HELD, REVIEW")
    confidence_score: float = Field(description="0.0 to 1.0 based on data confidence")
    reasoning_flags: List[str] = Field(
        description="Specific flags: CLIMATE_RISK_DETECTED, FARMER_NEGLIGENCE, GOOD_HISTORY, etc."
    )
    credit_explanation: str = Field(
        description="Internal technical justification for the credit committee."
    )
    farmer_advisory: str = Field(
        description="Simple, helpful advice for the farmer based on their specific AEZ conditions."
    )


def narrative_orchestration_node(state: FarmaState) -> dict:
    """
    combines Z-Score, Rainfall Anomaly, and AEZ Context to make a credit decision.
    """
    report = state.get("satellite_report", {})
    aez = state.get("nigeria_aez_context", {})
    landmark = state.get("location_query", "Farm")

    print("Credit assessment by gemini 3")

    # Unpack Signals
    ndvi = report.get("ndvi", 0.0)
    z_score = report.get("z_score", 0.0)
    rainfall = report.get("rainfall_30d", 0.0)
    sar = report.get("sar_biomass", 0.0)
    zone = aez.get("zone_name", "Unknown Zone")
    target_range = aez.get("target_ndvi", [0.0, 0.0])

    prompt = (
        f"You are a Senior Credit Officer for Nigerian Agriculture. Evaluate this loan request.\n\n"
        f"**FARM PROFILE:**\n"
        f"- Location: {landmark} ({zone} Zone)\n"
        f"- Expected Seasonality: {aez.get('seasonality')}\n"
        f"- Target Healthy NDVI: {target_range[0]} - {target_range[1]}\n\n"
        f"**REAL-TIME SATELLITE DATA:**\n"
        f"- Current NDVI: {ndvi}\n"
        f"- NDVI Z-Score: {z_score} (Std Devs from 10y Mean)\n"
        f"- 30-Day Rainfall: {rainfall} mm\n"
        f"- SAR Biomass (Cloudy/Tree check): {sar}\n\n"
        f"**UNDERWRITING RULES:**\n"
        f"1. **SANITY CHECK (CRITICAL):** If NDVI < 0.05, the sensor is seeing water, rock, or pavement. This is a 'Ghost Farm' or incorrect location. ACTION: REDIRECT BACK TO GEOCODING NODE TO FIGURE OUT WHERE THE FARM IS: automatically check the 4 surrounding pixels (North, South, East, West) to find the actual farm plot..\n"
        f"2. **The Z-Score Rule:** A Z-Score < -2.0 usually means crop failure. REJECT.\n"
        f"3. **Zero Data Trap:** If NDVI is near 0.0 and Z-Score is 0.0, this is NOT 'normal' - it is a lack of vegetative data. DO NOT APPROVE.\n"
        f"4. **The Climate Exception:** IF Z-Score is low BUT Rainfall is also very low (< 50mm), attribute to 'Climate Risk' (Drought). ACTION: 'APPROVED_WITH_INSURANCE'.\n"
        f"5. **Negligence:** IF Z-Score is low (< -1.5) BUT Rainfall is normal/high, ACTION: 'REJECT' (Reason: Farmer Negligence).\n"
        f"6. **Success:** IF NDVI is within/above Target Range OR Z-Score > -0.5 AND NDVI > 0.1. ACTION: 'APPROVED'.\n"
        f"7. **Validation:** Search Google for recent conflict/banditry in '{landmark}' to set a SECURITY_HOLD if needed.\n\n"
        f"**OUTPUT:**\n"
        f"Provide the credit decision, technical reasoning, and a helpful message for the farmer."
    )

    structured_llm = llm_brain.with_structured_output(UnderwritingDecision)

    try:
        decision = structured_llm.invoke(prompt)
        print(
            f"Final Decison: {decision.decision_intent} (Conf: {decision.confidence_score})"
        )

        return {
            "final_decision": decision.decision_intent,
            "climate_score": decision.confidence_score,
            "risk_flags": decision.reasoning_flags,
            "analysis_summary": [decision.farmer_advisory],  # for the aggregator
            "climate_narrative": decision.credit_explanation,
        }

    except Exception as e:
        print(f"Error: {e}")
        return {
            "final_decision": "REVIEW",
            "climate_narrative": "Automated underwriting failed. Manual review required.",  # TODO: ROUTE TO HUMAN ESCLATION
        }
