# src/llm.py
import json
import httpx
from src.config import CONFIG

# This is the system prompt that tells the LLM exactly what to do
# and what format to respond in.
# It's the same prompt logic from before but now used by all three providers.
SYSTEM_PROMPT = """You are a senior software engineer reviewing a pull request.
Analyze the provided git diff and return ONLY a valid JSON object.
No markdown, no explanation, no code fences. Raw JSON only.

Return exactly this schema:
{
  "title": "Short PR title under 72 chars, imperative mood (Add/Fix/Refactor not Added/Fixed)",
  "summary": "2-3 sentence plain English summary of what changed and why",
  "changes": ["specific bullet per logical change"],
  "risks": ["flag auth/DB/payment/security/breaking changes — empty list if none"],
  "notes": "extra reviewer context or empty string"
}

Be specific. 'Add bcrypt password hashing to login endpoint' not 'Updated auth'."""


def _parse_llm_json(raw_text: str) -> dict:
    """
    Parses JSON from LLM response text.
    Handles the common case where the model wraps its JSON in markdown
    code fences like ```json ... ``` despite being told not to.
    
    This is a defensive utility used by all three provider functions.
    """
    text = raw_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (```json or just ```)
        lines = text.split("\n")
        lines = lines[1:]                           # drop first line (the fence)
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return json.loads(text)


# ── GROQ ────────────────────────────────────────────────────────────────

def call_groq(diff_chunk: str) -> dict:
    """
    Calls the Groq API with llama-3.3-70b-versatile.
    
    Groq uses the OpenAI-compatible chat completions format,
    so the request structure looks identical to OpenAI —
    the only difference is the base URL and the model name.
    
    Groq is extremely fast (LPU inference) — expect responses in 1-3 seconds
    even for large diffs. This is one of the main reasons we use it.
    """
    api_key = CONFIG.get("groq_api_key", "")
    if not api_key:
        raise ValueError(
            "Groq API key not found.\n"
            "Set it with: export GROQ_API_KEY=your_key\n"
            "Or run: pr-summarize --configure"
        )

    model = CONFIG.get("groq_model", "llama-3.3-70b-versatile")

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Here is the git diff to analyze:\n\n{diff_chunk}"
                    },
                ],
                # Low temperature = more deterministic, consistent JSON output
                "temperature": 0.2,
                # response_format forces JSON — Groq supports this like OpenAI
                "response_format": {"type": "json_object"},
            },
        )

    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


# ── GEMINI ──────────────────────────────────────────────────────────────

def call_gemini(diff_chunk: str) -> dict:
    """
    Calls Google's Gemini API using the REST endpoint directly via httpx.
    We use httpx instead of the google-generativeai SDK because it gives us
    more control and avoids the SDK's heavy dependency chain.
    
    Gemini's API structure is different from OpenAI's:
    - No "system" role in messages — system instructions go in a separate field
    - Response is nested under candidates[0].content.parts[0].text
    - Model is part of the URL, not the request body
    """
    api_key = CONFIG.get("gemini_api_key", "")
    if not api_key:
        raise ValueError(
            "Gemini API key not found.\n"
            "Set it with: export GEMINI_API_KEY=your_key\n"
            "Get one at: https://aistudio.google.com/apikey"
        )

    model = CONFIG.get("gemini_model", "gemini-2.5-pro")

    # Gemini REST endpoint — model is in the URL path
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    with httpx.Client(timeout=120.0) as client:  # Gemini can be slower
        response = client.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                # systemInstruction is Gemini's equivalent of the system prompt
                "systemInstruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Here is the git diff to analyze:\n\n"
                                    f"{diff_chunk}"
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    # Tell Gemini to return JSON specifically
                    "responseMimeType": "application/json",
                },
            },
        )

    response.raise_for_status()
    data = response.json()

    # Gemini response structure is deeply nested
    content = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_llm_json(content)


# ── OLLAMA ──────────────────────────────────────────────────────────────

def call_ollama(diff_chunk: str) -> dict:
    """
    Calls a locally running Ollama server.
    
    Ollama runs open-source LLMs entirely on your machine.
    No API key. No internet required. No cost.
    
    To use this:
    1. Install Ollama from https://ollama.com
    2. Run: ollama pull codellama
    3. Run: pr-summarize --provider ollama
    
    Ollama also uses the OpenAI-compatible format at /v1/chat/completions,
    same as Groq. The host defaults to localhost:11434.
    """
    host = CONFIG.get("ollama_host", "http://localhost:11434")
    model = CONFIG.get("ollama_model", "codellama")

    with httpx.Client(timeout=120.0) as client:   # Local models can be slow
        try:
            response = client.post(
                f"{host}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Here is the git diff to analyze:\n\n"
                                f"{diff_chunk}"
                            )
                        },
                    ],
                    "temperature": 0.2,
                    "stream": False,   # We want the full response at once
                },
            )
        except httpx.ConnectError:
            raise ValueError(
                f"Cannot connect to Ollama at {host}.\n"
                "Make sure Ollama is running: ollama serve\n"
                "Install from: https://ollama.com"
            )

    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


# ── Unified entry point ─────────────────────────────────────────────────

def analyze_diff_chunk(diff_chunk: str, provider: str = None) -> dict:
    """
    Single function the rest of the app calls.
    Routes to the right provider based on config or argument.
    
    Provider priority:
    1. Explicit argument passed in (from --provider flag)
    2. Config file setting
    3. Default (groq)
    """
    if provider is None:
        provider = CONFIG.get("provider", "groq")

    provider = provider.lower().strip()

    if provider == "groq":
        return call_groq(diff_chunk)
    elif provider == "gemini":
        return call_gemini(diff_chunk)
    elif provider == "ollama":
        return call_ollama(diff_chunk)
    else:
        raise ValueError(
            f"Unknown provider: '{provider}'\n"
            f"Valid options: groq, gemini, ollama"
        )


def merge_results(results: list[dict]) -> dict:
    """
    Merges multiple chunk results into one coherent PR description.
    Called when a large diff was split into multiple chunks,
    each analyzed separately.
    """
    results = [r for r in results if isinstance(r, dict)]

    if not results:
        return {
            "title": "Update codebase",
            "summary": "Changes made to the codebase.",
            "changes": [],
            "risks": [],
            "notes": "",
        }

    if len(results) == 1:
        return results[0]

    merged = {
        "title": results[0].get("title", "Update codebase"),
        "summary": " ".join(
            r.get("summary", "") for r in results if r.get("summary")
        ),
        "changes": [],
        "risks": [],
        "notes": "",
    }

    seen_changes: set[str] = set()
    seen_risks: set[str] = set()

    for r in results:
        for change in r.get("changes", []):
            if change and change not in seen_changes:
                merged["changes"].append(change)
                seen_changes.add(change)
        for risk in r.get("risks", []):
            if risk and risk not in seen_risks:
                merged["risks"].append(risk)
                seen_risks.add(risk)

    notes = [r.get("notes", "") for r in results if r.get("notes")]
    merged["notes"] = " | ".join(notes)

    return merged