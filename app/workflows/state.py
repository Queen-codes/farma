"""Typed workflow state contract shared across FARMA graph nodes.

Purpose:
- Define the mutable state object each node reads/writes during execution.
- Document expected keys for intent parsing, geospatial analysis, risk, and
  final response generation.

Used by:
- `app.workflows.graph` and all node modules under `app/workflows/nodes/`.
"""

from typing import Annotated, Optional, List
from typing_extensions import TypedDict
import operator


class FarmaState(TypedDict):
    """Main LangGraph state for one farmer request pipeline run."""

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
    pending_question: Optional[str]
    pending_question_type: Optional[str]
    human_task: Optional[dict]
    sms_text: Optional[str]

    # Stores: {name, confidence, treatment, risk_flag, iterations}
    disease_analysis: Optional[dict]

    # Geospatial Engine
    location_query: Optional[str]  # The raw landmark description
    coordinates: Optional[dict]  # {'lat': float, 'lng': float, 'confidence': float}
    geocode_provenance: Optional[dict]

    # Climate-Smart Shared Data
    # Stores: {ndvi, rainfall_total_30d, soil_moisture, historical_comparison_gap}
    climate_query: Optional[dict]  # {question_type, time_horizon_days, crop, location_text}
    weather_forecast: Optional[dict]
    chirps_rainfall_30d: Optional[float]
    satellite_report: Optional[dict]
    climate_score: Optional[float]  # 0.0 to 1.0 (Health Score)
    risk_flags: Annotated[
        List[str], operator.add
    ]  # ["DROUGHT_RISK", "FLOOD_WARNING", "PEST_LIKELY"]

    # Nigeria AEZ
    nigeria_aez_context: Optional[
        dict
    ]  # {zone_name, target_ndvi, expected_seasonality}
    visualization_artifacts: Optional[dict]  # {scatter_plot_path, map_path}
    aegis_context: Optional[dict]

    # Final Decisions
    final_decision: Optional[str]  # APPROVED, REJECTED, HELD, ADVICE_ONLY
    approved_amount: Optional[float]
    loan_terms: Optional[dict]
    farmer_response: Optional[str]  # The final message to be sent to the farmer
    analysis_summary: Annotated[
        List[str], operator.add
    ]  # Detailed internal reasoning for the demo

    # History and Traceability
    # Tracks the flow for debugging and multi-turn potential
    history: Annotated[List[dict], operator.add]
