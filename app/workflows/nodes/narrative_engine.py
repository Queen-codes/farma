"""Credit Decision Engine for FARMA.

This module combines:
1. Deterministic underwriting rules (fast, reliable)
2. LLM reasoning for edge cases (when rules can't decide)
3. LLM-generated farmer advisory (always, for personalized advice)

Aegis integration handles security/conflict checks separately.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import List
from app.workflows.state import FarmaState
from app.config import GOOGLE_API_KEY, MODEL_PRO, MODEL_FLASH
from app.workflows.nodes.underwriting import (
    apply_underwriting_rules,
    DataQuality,
    DecisionType,
    validate_loan_amount,
)


# Gemini 3 Pro for complex reasoning
llm_reasoning = ChatGoogleGenerativeAI(
    model=MODEL_PRO,
    google_api_key=GOOGLE_API_KEY,
)

# Gemini Flash for advisory
llm_advisory = ChatGoogleGenerativeAI(
    model=MODEL_FLASH,
    google_api_key=GOOGLE_API_KEY,
    temperature=0.3,
)


class EdgeCaseAnalysis(BaseModel):
    """LLM output for edge case analysis."""

    decision: str = Field(
        description="One of: APPROVED, REJECTED, APPROVED_WITH_INSURANCE, HELD, REVIEW"
    )
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Explanation of the decision")
    key_factors: List[str] = Field(
        description="Main factors that influenced the decision"
    )


class FarmerAdvisory(BaseModel):
    """LLM output for farmer-friendly advice."""

    advisory_message: str = Field(
        description="Short, helpful advice for the farmer (2-3 sentences max)"
    )
    action_items: List[str] = Field(
        description="1-3 specific actions the farmer should take"
    )


def get_llm_edge_case_analysis(
    ndvi: float,
    z_score: float,
    rainfall: float,
    zone: str,
    target_range: tuple,
    existing_flags: List[str],
) -> dict:
    """
    Use Gemini 3 Pro to analyze edge cases that don't fit deterministic rules.

    This is called ONLY when the deterministic rules return REVIEW.
    """
    rainfall_str = f"{rainfall:.1f}mm" if rainfall is not None else "DATA UNAVAILABLE"

    prompt = f"""You are an experienced Nigerian agricultural credit officer analyzing a borderline loan case.

**SATELLITE DATA:**
- NDVI (vegetation index): {ndvi:.3f}
- Z-Score (vs 10-year history): {z_score:.2f}
- 30-day Rainfall: {rainfall_str}
- Agro-Ecological Zone: {zone}
- Target NDVI for zone: {target_range[0]} - {target_range[1]}

**EXISTING FLAGS:** {existing_flags}

**CONTEXT:**
This case didn't clearly fit the standard approval/rejection rules. The deterministic system flagged it for expert review.

**YOUR TASK:**
Analyze the specific combination of factors and make a credit decision. Consider:
1. Is the low performance due to climate (drought/flood) or farmer management?
2. Is the farm showing signs of recovery or continued decline?
3. What's the repayment risk given current conditions?

Provide a clear decision with your reasoning."""

    try:
        structured_llm = llm_reasoning.with_structured_output(EdgeCaseAnalysis)
        analysis = structured_llm.invoke(prompt)

        return {
            "decision": analysis.decision,
            "confidence": analysis.confidence,
            "reasoning": analysis.reasoning,
            "flags": analysis.key_factors,
        }
    except Exception as e:
        print(f"LLM Edge Case Analysis failed: {e}")
        return {
            "decision": "REVIEW",
            "confidence": 0.3,
            "reasoning": "Automated analysis failed. Manual review required.",
            "flags": ["LLM_ANALYSIS_FAILED"],
        }


def get_farmer_advisory(
    decision: str,
    ndvi: float,
    z_score: float,
    zone: str,
    target_range: tuple,
    flags: List[str],
) -> str:
    """
    Generate personalized advisory message for the farmer using Gemini Flash.

    Called for ALL decisions to provide helpful, localized advice.
    """
    prompt = f"""You are a friendly Nigerian agricultural extension officer.

**FARM STATUS:**
- Decision: {decision}
- Current health (NDVI): {ndvi:.2f} (target: {target_range[0]}-{target_range[1]})
- Performance vs history: {"Above average" if z_score > 0 else "Below average" if z_score < -1 else "Average"}
- Zone: {zone}
- Flags: {flags}

**TASK:**
Write 2-3 sentences of practical advice for this farmer. Be:
- Encouraging but honest
- Specific to their zone and situation
- Actionable (what they should do next)

Keep it under 100 words. Don't mention technical terms like NDVI or Z-Score."""

    try:
        response = llm_advisory.invoke(prompt)
        content = response.content
        if isinstance(content, list):
            content = " ".join(
                [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
            )
        return content.strip()
    except Exception as e:
        print(f"Advisory generation failed: {e}")
        # Fallback advisory based on decision
        if decision == "APPROVED":
            return "Your farm is performing well. Continue your current practices and consider diversifying crops for additional income."
        elif decision == "REJECTED":
            return "Your farm is facing challenges. Consider consulting with local agricultural extension services for guidance on improving your yields."
        else:
            return "Your application is being reviewed. Please ensure your farm information is accurate."


def narrative_orchestration_node(state: FarmaState) -> dict:
    """
    Credit decision node using deterministic rules + LLM for edge cases.

    Flow:
    1. Extract satellite data and build DataQuality object
    2. Get Aegis risk flags from state
    3. Apply deterministic underwriting rules
    4. If REVIEW needed, use LLM for edge case analysis
    5. Generate farmer advisory (always uses LLM)
    6. Return decision with all context
    """
    report = state.get("satellite_report", {})
    aez = state.get("nigeria_aez_context", {})
    parsed_data = state.get("parsed_data", {})

    # unpack satellite signals
    ndvi = report.get("ndvi", 0.0)
    z_score = report.get("z_score", 0.0)
    rainfall = report.get("rainfall_30d")  # Can be None now
    zone = aez.get("zone_name", "Unknown Zone")
    target_range = aez.get("target_ndvi", (0.0, 1.0))

    # build data quality object from satellite report
    data_quality_dict = report.get("data_quality", {})
    data_quality = DataQuality(
        ndvi_available=data_quality_dict.get(
            "ndvi_available", ndvi is not None and ndvi > 0
        ),
        rainfall_available=data_quality_dict.get(
            "rainfall_available", rainfall is not None
        ),
        zscore_available=data_quality_dict.get(
            "zscore_available", z_score is not None and z_score != 0.0
        ),
        ndvi_error=data_quality_dict.get("ndvi_error"),
        rainfall_error=data_quality_dict.get("rainfall_error"),
        zscore_error=data_quality_dict.get("zscore_error"),
    )

    # get Aegis risk flags
    aegis_flags = [
        f
        for f in state.get("risk_flags", [])
        if f.startswith("AEGIS")
        or f
        in ["ACTIVE_CONFLICT", "FOOD_CRISIS_ZONE", "HIGH_DISPLACEMENT", "FAMINE_ZONE"]
    ]

    # get loan amount and crop type for validation
    loan_amount = parsed_data.get("amount")
    crop_type = parsed_data.get("crop_type")

    print(
        f"Credit Assessment: NDVI={ndvi:.2f}, Z={z_score:.2f}, Rain={rainfall}, Zone={zone}"
    )

    #  Apply deterministic underwriting rules
    result = apply_underwriting_rules(
        ndvi=ndvi,
        z_score=z_score,
        rainfall_30d=(
            rainfall if rainfall is not None else 0.0
        ),  # Default for rules, but flag is set
        target_ndvi_range=target_range,
        data_quality=data_quality,
        aegis_risk_flags=aegis_flags,
        loan_amount=loan_amount,
        crop_type=crop_type,
    )

    decision = result.decision.value
    confidence = result.confidence
    flags = result.flags
    explanation = result.explanation

    # If deterministic rules returned REVIEW, use LLM for analysis
    if result.requires_llm_review:
        print("Edge case detected - invoking LLM analysis...")
        llm_result = get_llm_edge_case_analysis(
            ndvi=ndvi,
            z_score=z_score,
            rainfall=rainfall if rainfall is not None else 0.0,
            zone=zone,
            target_range=target_range,
            existing_flags=flags,
        )
        decision = llm_result["decision"]
        confidence = llm_result["confidence"]
        flags = flags + llm_result["flags"]
        explanation = llm_result["reasoning"]
        print(f"LLM Decision: {decision} (Conf: {confidence})")
    else:
        print(f"Deterministic Decision: {decision} (Conf: {confidence})")

    # Generate farmer advisory
    advisory = get_farmer_advisory(
        decision=decision,
        ndvi=ndvi,
        z_score=z_score,
        zone=zone,
        target_range=target_range,
        flags=flags,
    )

    print(f"Final Decision: {decision} (Conf: {confidence:.2f})")

    # merge all flags
    existing_flags = state.get("risk_flags", [])
    all_flags = list(set(existing_flags + flags))

    return {
        "final_decision": decision,
        "climate_score": confidence,
        "risk_flags": all_flags,
        "analysis_summary": [advisory],
        "climate_narrative": explanation,
    }
