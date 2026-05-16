# src/config.py

import os

from dotenv import load_dotenv

load_dotenv()
# =========================================================
# API KEYS
# =========================================================

# Reads API keys from environment variables (.env file).
# Never hardcode secrets directly into source code.

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


# =========================================================
# DEFAULT PROVIDER
# =========================================================

# Which provider to use if user does not specify one.
# Supported:
# - "groq"
# - "gemini"

DEFAULT_PROVIDER = os.environ.get(
    "PR_SUMMARIZER_PROVIDER",
    "groq"
)


# =========================================================
# TOKEN LIMITS
# =========================================================

# Max tokens per chunk sent to the LLM.
# Smaller chunks improve focus and reduce costs.

MAX_CHUNK_TOKENS = 3500


# =========================================================
# MODEL CONFIGURATION
# =========================================================

# Maps provider name -> model name.

MODELS = {

    # Groq models
    "groq": "llama-3.3-70b-versatile",

    # Gemini models
    "gemini": "gemini-2.5-pro"
}