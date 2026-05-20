# src/config.py
import os
import toml
from pathlib import Path


CONFIG_FILE = Path.home() / ".pr-summarizer.toml"

DEFAULTS = {
    "provider": "groq",
    "groq_model": "llama-3.3-70b-versatile",
    "gemini_model": "gemini-2.5-pro",
    "ollama_model": "codellama",
    "ollama_host": "http://localhost:11434",
    "max_chunk_tokens": 3500,
    "output_format": "markdown",
}


def load_config() -> dict:
    config = DEFAULTS.copy()

    if CONFIG_FILE.exists():
        try:
            file_config = toml.load(CONFIG_FILE)
            config.update(file_config)
        except Exception as e:
            print(f"Warning: Could not read config file {CONFIG_FILE}: {e}")

    env_overrides = {
        "groq_api_key":   os.environ.get("GROQ_API_KEY", ""),
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "provider":       os.environ.get("PR_SUMMARIZER_PROVIDER", ""),
    }
    for key, val in env_overrides.items():
        if val:
            config[key] = val

    return config


def save_config(updates: dict):
    existing = {}
    if CONFIG_FILE.exists():
        try:
            existing = toml.load(CONFIG_FILE)
        except Exception:
            pass
    existing.update(updates)
    with open(CONFIG_FILE, "w") as f:
        toml.dump(existing, f)


def validate_config(config: dict) -> list[str]:
    problems = []
    provider = config.get("provider", "groq")

    if provider == "groq" and not config.get("groq_api_key"):
        problems.append(
            "Groq API key not set.\n"
            "  Option 1: set GROQ_API_KEY environment variable\n"
            "  Option 2: run `pr-summarize configure`\n"
            "  Get a key at: https://console.groq.com"
        )
    elif provider == "gemini" and not config.get("gemini_api_key"):
        problems.append(
            "Gemini API key not set.\n"
            "  Option 1: set GEMINI_API_KEY environment variable\n"
            "  Option 2: run `pr-summarize configure`\n"
            "  Get a key at: https://aistudio.google.com/apikey"
        )
    elif provider == "ollama":
        host = config.get("ollama_host", "")
        if not host.startswith("http"):
            problems.append(
                f"Ollama host looks wrong: '{host}'\n"
                "  Should be like: http://localhost:11434"
            )
    return problems


CONFIG = load_config()
DEFAULT_PROVIDER = CONFIG.get("provider", "groq")
MAX_CHUNK_TOKENS = int(CONFIG.get("max_chunk_tokens", 3500))

# These aliases keep your existing llm.py working without changes
GROQ_API_KEY = CONFIG.get("groq_api_key", "")
GEMINI_API_KEY = CONFIG.get("gemini_api_key", "")
MODELS = {
    "groq": CONFIG.get("groq_model", "llama-3.3-70b-versatile"),
    "gemini": CONFIG.get("gemini_model", "gemini-2.5-pro"),
    "ollama": CONFIG.get("ollama_model", "codellama"),
}