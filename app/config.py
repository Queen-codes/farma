import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Earth Engine Settings
service_account = os.getenv("service_account")

# Model Versions
MODEL_FLASH = "gemini-3-flash-preview"
MODEL_PRO = "gemini-3-pro-preview"
MODEL_GROUNDING = "gemini-2.5-pro"

AEGIS_FOCUS_STATES = ["Borno", "Adamawa", "Yobe", "Bauchi", "Gombe", "Taraba"]

# Models
GEMINI_MODEL_PLANNER = os.getenv("GEMINI_MODEL_PLANNER", "gemini-3-flash-preview")
GEMINI_MODEL_GROUNDED = os.getenv("GEMINI_MODEL_GROUNDED", "gemini-3-flash-preview")
GEMINI_MODEL_SYNTH = os.getenv("GEMINI_MODEL_SYNTH", "gemini-3-flash-preview")

# Thinking
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "LOW")

# Concurrency
MAX_STATE_WORKERS = int(
    os.getenv("MAX_STATE_WORKERS", "8")
)  # LangGraph max_concurrency
GLOBAL_TOOL_CONCURRENCY = int(os.getenv("GLOBAL_TOOL_CONCURRENCY", "12"))
PER_STATE_TOOL_CONCURRENCY = int(os.getenv("PER_STATE_TOOL_CONCURRENCY", "4"))

# Networking
GEMINI_TIMEOUT_S = float(os.getenv("GEMINI_TIMEOUT_S", "60"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "2"))

# Google Cloud Storage (reports + temp audio)
GCS_BUCKET = os.getenv("GCS_BUCKET", "gen-lang-client-0340377833-farma-reports")
GCS_REPORT_PREFIX = "reports/"
GCS_AUDIO_PREFIX = "tmp_audio/"
