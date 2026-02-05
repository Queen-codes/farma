from __future__ import annotations

import os


SYNTHESIS_VERSION = os.getenv("AEGIS_SYNTHESIS_VERSION", "dag-v1")


GEMINI_MODEL_SYNTH_STATE = os.getenv(
    "AEGIS_SYNTH_MODEL_STATE", "gemini-3-flash-preview"
)
GEMINI_MODEL_SYNTH_ROLLUP = os.getenv(
    "AEGIS_SYNTH_MODEL_ROLLUP", "gemini-3-flash-preview"
)

# Thinking low by default for responsiveness
THINKING_LEVEL = os.getenv("AEGIS_SYNTH_THINKING_LEVEL", "LOW")

# Concurrency
MAX_STATE_WORKERS = int(os.getenv("AEGIS_SYNTH_MAX_STATE_WORKERS", "8"))
LLM_CONCURRENCY = int(os.getenv("AEGIS_SYNTH_LLM_CONCURRENCY", "8"))

# Temperature for determinism
TEMPERATURE = float(os.getenv("AEGIS_SYNTH_TEMPERATURE", "0.2"))

# Timeouts/retries
TIMEOUT_S = float(os.getenv("AEGIS_SYNTH_TIMEOUT_S", "45"))
MAX_RETRIES = int(os.getenv("AEGIS_SYNTH_MAX_RETRIES", "1"))
