from typing import Literal
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from app.workflows.state import FarmaState
from app.config import GOOGLE_API_KEY, MODEL_FLASH, MODEL_PRO


# TODO: INCLUDE OPTION FOR HUMAN IN THE LOOP IF THE EVALUATOR DECIDES THE CONFIDENDE IS TOO LOW
# This uses evaluator-optimizer workflow.
class DiseaseDiagnosis(BaseModel):
    """Initial diagnosis from the Disease Generator Node"""

    disease_name: str = Field(description="Scientific or common name of the disease")
    confidence: float = Field(description="Confidence score between 0 and 1")
    symptoms_matched: list[str] = Field(
        description="List of symptoms that support this diagnosis"
    )
    treatment: str = Field(description="Recommended treatment or action for the farmer")
    risk_score: float = Field(description="Risk to crop yield (0 low, 1 high)")
    needs_more_info: bool = Field(
        description="True if the description is too vague to diagnose"
    )
    clarifying_question: str | None = Field(
        description="Question to ask the farmer if more info is needed"
    )


class EvaluationResult(BaseModel):
    """Feedback from the Evaluator"""

    is_accurate: bool = Field(
        description="Whether the diagnosis is scientifically sound for the region"
    )
    contradictions: list[str] = Field(
        description="Any biological or geographical contradictions found"
    )
    improvement_suggestion: str | None = Field(
        description="Specific advice to improve the diagnosis"
    )
    final_confidence: float = Field(description="Adjusted confidence score")


# gemini 3 flash for generation
llm_flash = ChatGoogleGenerativeAI(model=MODEL_FLASH, google_api_key=GOOGLE_API_KEY)
# pro for evaluation
llm_pro = ChatGoogleGenerativeAI(model=MODEL_PRO, google_api_key=GOOGLE_API_KEY)


# TODO: REFACTOR
def disease_generator(state: FarmaState) -> dict:
    """Generates initial diagnosis grounded in African Agronomy."""
    print("Generating diagnosis...")

    # Extract symptoms from parsed_data or message
    symptoms = state.get("parsed_data", {}).get("symptoms") or state.get("message")
    crop = state.get("parsed_data", {}).get("crop_type", "unknown crop")

    prompt = f"""
    You are an expert African Plant Pathologist. 
    A farmer reports the following symptoms on their {crop}: "{symptoms}"
    
    ### YOUR TASK:
    1. Identify the most likely disease using PlantVillage knowledge.
    2. Focus strictly on diseases common in Africa.
    3. **FORBIDDEN:** Do NOT ask for a photo. Assume no photo is coming. You must diagnose based on this text.
    4. **ACTIONABLE ADVICE:** Provide a "Low-Cost Fix" that a farmer can do right now (e.g., pruning, burning, traditional organic sprays).
    5. **RISK ASSESSMENT:** If this disease is highly contagious or fatal for the crop, set a high risk_score.
    
    ### OUTPUT:
    - Provide the diagnosis name.
    - List symptoms matched.
    - In 'treatment', provide clear, numbered physical steps the farmer should take.
    - If symptoms are too vague to even guess, ask ONE clarifying question about the physical state (e.g., "is the stem soft?").
    """

    structured_llm = llm_flash.with_structured_output(DiseaseDiagnosis)
    diagnosis = structured_llm.invoke(prompt)

    print(f"Diagnosis: {diagnosis.disease_name} (Confidence: {diagnosis.confidence})")

    return {"disease_analysis": diagnosis.model_dump()}


# Evaluator
def disease_evaluator(state: FarmaState) -> dict:

    analysis = state.get("disease_analysis", {})
    crop = state.get("parsed_data", {}).get("crop_type", "unknown")

    print(f"Evaluating {analysis.get('disease_name')}...")

    prompt = f"""
    You are a Senior Plant Pathologist reviewing a junior's diagnosis.
    
    Diagnosis to Review:
    - Crop: {crop}
    - Disease: {analysis.get('disease_name')}
    - Symptoms Matched: {analysis.get('symptoms_matched')}
    - Treatment: {analysis.get('treatment')}
    
    Your Task:
    1. Check for biological contradictions (e.g., does this disease actually affect this crop?).
    2. Check for geographical accuracy (e.g., is this disease present in Africa?).
    3. If confidence is high and no contradictions, set is_accurate to True.
    4. If there are issues, provide improvement_suggestion.
    """

    structured_llm = llm_pro.with_structured_output(EvaluationResult)
    evaluation = structured_llm.invoke(prompt)

    # merge or update state
    updated_analysis = analysis.copy()
    updated_analysis["confidence"] = evaluation.final_confidence
    updated_analysis["is_verified"] = evaluation.is_accurate
    updated_analysis["evaluator_notes"] = evaluation.improvement_suggestion
    updated_analysis["iterations"] = updated_analysis.get("iterations", 0) + 1

    # Add to summary for aggregator if verified or max iterations reached
    summary = []
    if updated_analysis["is_verified"] or updated_analysis["iterations"] >= 2:
        print(f"valuation Complete. Verified: {updated_analysis['is_verified']}")
        summary = [
            f"Diagnosis: {updated_analysis.get('disease_name')}",
            f"Treatment: {updated_analysis.get('treatment')}",
        ]
    else:
        print(
            f"Evaluation failed or low confidence. Suggestion: {evaluation.improvement_suggestion}"
        )

    return {"disease_analysis": updated_analysis, "analysis_summary": summary}
