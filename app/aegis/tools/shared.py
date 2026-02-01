"""Shared config for AEGIS grounding tools."""

from langchain_google_genai import ChatGoogleGenerativeAI

from google.genai import types
from app.config import GOOGLE_API_KEY, MODEL_FLASH


AEGIS_FOCUS_STATES = [
    "Borno",
    "Adamawa",
    "Yobe",
    "Zamfara",
    "Katsina",
    "Kaduna",
    "Niger",
]

_llm_base = ChatGoogleGenerativeAI(
    model=MODEL_FLASH,
    google_api_key=GOOGLE_API_KEY,
    convert_system_message_to_human=True,
)
_tools = [types.Tool(google_search=types.GoogleSearch())]
llm_grounding = _llm_base.bind(tools=_tools)
