from typing import Annotated, Optional, List
from typing_extensions import TypedDict
import operator


class FarmaState(TypedDict):
    """
    The main state for Farma workflow.
    """

    # Input Data
    phone: str
    input_type: str  # sms or extracted text from voice input
    message: Optional[str]  # Raw text or transcription
    audio_path: Optional[str]  # Path to audio file if input type is voice
    language: Optional[str]

    # Intent/Status
    intent: Optional[
        str
    ]  # LOAN_REQUEST, DISEASE_REPORT, WEATHER_INQUIRY, HUMAN_ESCALATION
    status: Optional[str]  # READY_FOR_ANALYSIS, AWAITING_FARMER_RESPONSE, COMPLETED
    parsed_data: Optional[dict]  # {crop_type, amount, landmark, symptoms, etc.}

    # TODO: THIS WAS INTENDED TO BE A SHARED INTELLIGENCE: LOAN EVALUATOR USES ALL THE INFORMATION GOTTEN FROM DIEASES ANALYSIS, CLIMATE TO MAKE A LOAN DECSION
    # NOT TESTED A LOAN REQUEST WITH A DISEASE REPOROT, But individaul diease report is no longer working as intened, find the root cause and seperate
    # Disease Engine (Evaluator-Optimizer)
    # Stores: {name, confidence, treatment, risk_flag, iterations}
    disease_analysis: Optional[dict]

    # Geospatial Engine
    location_query: Optional[str]  # The raw landmark description
    coordinates: Optional[dict]  # {'lat': float, 'lng': float, 'confidence': float}

    # Climate-Smart Shared Data
    # Stores: {ndvi, rainfall_total_30d, soil_moisture, historical_comparison_gap}
    satellite_report: Optional[dict]
    climate_score: Optional[float]  # 0.0 to 1.0 (Health Score)
    climate_narrative: Optional[str]  # Gemini 3 Pro generated
    risk_flags: Annotated[
        List[str], operator.add
    ]  # ["DROUGHT_RISK", "FLOOD_WARNING", "PEST_LIKELY"]

    # Nigeria AEZ
    nigeria_aez_context: Optional[
        dict
    ]  # {zone_name, target_ndvi, expected_seasonality}
    visualization_artifacts: Optional[dict]  # {scatter_plot_path, map_path}

    # Loan Underwriting
    satellite_score: Optional[
        float
    ]  # might not be using for v1, swapped in favor of climate_score, keeping for backward compat, or future updates
    mobile_money_score: Optional[float]
    history_score: Optional[float]

    # Final Decisions
    final_decision: Optional[str]  # APPROVED, REJECTED, HELD, ADVICE_ONLY
    farmer_response: Optional[str]  # The final message to be sent to the farmer
    analysis_summary: Annotated[
        List[str], operator.add
    ]  # Detailed internal reasoning for the demo

    # History and Traceability
    # Tracks the flow for debugging and multi-turn potential
    history: Annotated[List[dict], operator.add]
