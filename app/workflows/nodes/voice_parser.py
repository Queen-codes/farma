from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import GOOGLE_API_KEY, MODEL_FLASH
from app.prompts.voice_parser import VOICE_PARSER_PROMPT
from app.workflows.state import FarmaState
from pydantic import BaseModel, Field
from typing import Optional
import base64
import mimetypes

model = ChatGoogleGenerativeAI(
    model=MODEL_FLASH,
    google_api_key=GOOGLE_API_KEY,
    temperature=0.0,
    thinking_level="low",
)


class ParserOutput(BaseModel):
    """Schema for the parser output. extraction ONLY."""

    intent: str = Field(
        description="The detected intent (LOAN_REQUEST, DISEASE_REPORT, WEATHER_INQUIRY, etc.)"
    )
    language: str = Field(description="The detected language/dialect.")

    # Extracted data fields
    crop_type: Optional[str] = Field(description="The type of crop mentioned.")
    amount: Optional[float] = Field(description="The loan amount mentioned.")
    landmark: Optional[str] = Field(description="The landmark or location mentioned.")
    symptoms: Optional[str] = Field(description="The plant symptoms described.")

    status: str = Field(
        description="Set to 'READY_FOR_ANALYSIS' if intent is clear, or 'HUMAN_ESCALATION' if confused."
    )


def voice_parser_node(state: FarmaState) -> dict:
    """Parses farmer voice input. Silent extraction only."""

    if not state.get("audio_path"):
        return {"intent": "HUMAN_ESCALATION", "status": "ESCALATE_TO_HUMAN"}

    # Handle Audio Input
    audio_path = state["audio_path"]
    mime_type, _ = mimetypes.guess_type(audio_path)
    if not mime_type:
        mime_type = "audio/mp4"

    with open(audio_path, "rb") as audio_file:
        audio_data = base64.b64encode(audio_file.read()).decode("utf-8")

    user_content = [
        {
            "type": "text",
            "text": f"Extract details from this voice message. Phone: {state['phone']}",
        },
        {"type": "media", "mime_type": mime_type, "data": audio_data},
    ]

    messages = [
        SystemMessage(content=VOICE_PARSER_PROMPT),
        HumanMessage(content=user_content),
    ]

    structured_llm = model.with_structured_output(ParserOutput)

    try:
        parsed: ParserOutput = structured_llm.invoke(messages)

        return {
            "intent": parsed.intent,
            "language": parsed.language,
            "status": parsed.status,
            "parsed_data": {
                "crop_type": parsed.crop_type,
                "amount": parsed.amount,
                "landmark": parsed.landmark,
                "symptoms": parsed.symptoms,
            },
            "farmer_response": None,  # Silence the parser
        }

    except Exception as e:
        print(f"Voice Parser Error: {e}")
        return {
            "intent": "HUMAN_ESCALATION",
            "status": "ESCALATE_TO_HUMAN",
            "farmer_response": "I'm sorry, I'm having trouble understanding your voice message.",
        }
