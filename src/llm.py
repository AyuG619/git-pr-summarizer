# src/llm.py

import httpx
import json

from src.config import (
    GROQ_API_KEY,
    GEMINI_API_KEY,
    MODELS
)


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
You are a senior software engineer reviewing a pull request diff.

Analyze the provided git diff and return ONLY a valid JSON object.

NO markdown.
NO explanation.
NO code fences.

Return JSON matching this schema:

{
  "title": "Short PR title under 72 characters",
  "summary": "2-3 sentence explanation of what changed",
  "changes": [
    "Specific change"
  ],
  "risks": [
    "Potential risk"
  ],
  "notes": "Extra reviewer notes"
}

Rules:
- Use imperative mood for titles
- Be specific
- Return ONLY raw JSON
"""


# =========================================================
# GROQ API
# =========================================================

def call_groq(diff_chunk: str) -> dict:
    """
    Calls the Groq API.
    Returns parsed JSON output.
    """

    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY not set."
        )

    with httpx.Client(timeout=60.0) as client:

        response = client.post(

            "https://api.groq.com/openai/v1/chat/completions",

            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },

            json={
                "model": MODELS["groq"],

                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": f"Here is the git diff:\n\n{diff_chunk}",
                    },
                ],

                "temperature": 0.2,
            },
        )

    response.raise_for_status()

    data = response.json()

    content = data["choices"][0]["message"]["content"]

    return json.loads(content)


# =========================================================
# GEMINI API
# =========================================================

def call_gemini(diff_chunk: str) -> dict:
    """
    Calls the Gemini API.
    """

    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY not set."
        )

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODELS['gemini']}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )

    payload = {

        "contents": [
            {
                "parts": [
                    {
                        "text":
                        SYSTEM_PROMPT
                        + "\n\nHere is the git diff:\n\n"
                        + diff_chunk
                    }
                ]
            }
        ],

        "generationConfig": {
            "temperature": 0.2,
        }
    }

    with httpx.Client(timeout=60.0) as client:

        response = client.post(
            endpoint,
            json=payload,
        )

    response.raise_for_status()

    data = response.json()

    content = (
        data["candidates"][0]
        ["content"]["parts"][0]["text"]
    )

    # Gemini sometimes wraps JSON in markdown fences
    content = content.strip()

    if content.startswith("```"):

        content = content.split("```")[1]

        if content.startswith("json"):
            content = content[4:]

    content = content.strip()

    return json.loads(content)


# =========================================================
# PROVIDER ROUTER
# =========================================================

def analyze_diff_chunk(
    diff_chunk: str,
    provider: str = "groq"
) -> dict:
    """
    Unified provider abstraction.
    """

    if provider == "groq":
        return call_groq(diff_chunk)

    elif provider == "gemini":
        return call_gemini(diff_chunk)

    else:
        raise ValueError(
            f"Unknown provider: {provider}"
        )


# =========================================================
# RESULT MERGING
# =========================================================

def merge_results(results: list[dict]) -> dict:
    """
    Merges multiple chunk summaries into one result.
    """

    if len(results) == 1:
        return results[0]

    merged = {
        "title": results[0].get("title", ""),
        "summary": " ".join(
            r.get("summary", "")
            for r in results
        ),
        "changes": [],
        "risks": [],
        "notes": "",
    }

    seen_changes = set()
    seen_risks = set()

    for r in results:

        for change in r.get("changes", []):

            if change not in seen_changes:

                merged["changes"].append(change)
                seen_changes.add(change)

        for risk in r.get("risks", []):

            if risk not in seen_risks:

                merged["risks"].append(risk)
                seen_risks.add(risk)

    notes = [
        r.get("notes", "")
        for r in results
        if r.get("notes", "")
    ]

    merged["notes"] = " | ".join(notes)

    return merged