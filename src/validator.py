# src/validator.py
from pydantic import BaseModel, Field, field_validator
from typing import Optional


# BaseModel is Pydantic's base class.
# When you define a class that inherits from it, Pydantic automatically:
#   - Checks that all required fields are present
#   - Checks that types match (str is str, list is list etc.)
#   - Runs any custom validators you define
#   - Gives you a clean error message if anything is wrong

class PRDescription(BaseModel):
    """
    This is the schema for what the LLM must return.
    Every field maps directly to a key in the JSON the LLM produces.
    
    If the LLM returns:
        {"title": "Fix bug", "summary": "Fixed it", "changes": ["did x"]}
    
    Pydantic maps that JSON dict into this Python object automatically.
    You then access result.title, result.summary etc. instead of result["title"].
    This is much safer — if "title" is missing, you get a clear Pydantic error,
    not a silent KeyError somewhere deep in your code.
    """

    title: str = Field(
        ...,                          # ... means this field is required
        min_length=3,
        max_length=100,
        description="Short PR title in imperative mood"
    )

    summary: str = Field(
        ...,
        min_length=10,
        description="2-3 sentence plain English summary of the change"
    )

    changes: list[str] = Field(
        default_factory=list,         # If missing from LLM output, default to empty list
        description="Bullet points of what changed"
    )

    risks: list[str] = Field(
        default_factory=list,
        description="Security/breaking change warnings"
    )

    notes: Optional[str] = Field(
        default="",                   # Optional — empty string if not provided
        description="Extra reviewer context"
    )


    # field_validator runs after Pydantic does its type checks.
    # This one cleans up the title automatically.
    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        """
        Two common LLM mistakes we auto-fix here:
        1. Wrapping the title in quotes: '"Fix the bug"' → 'Fix the bug'
        2. Past tense instead of imperative: 'Fixed the bug' → 'Fix the bug'
        """
        # Strip surrounding quotes the LLM sometimes adds
        v = v.strip().strip('"').strip("'")

        # Fix past tense → imperative (most common LLM mistake)
        past_to_imperative = {
            "Added ": "Add ",
            "Fixed ": "Fix ",
            "Updated ": "Update ",
            "Removed ": "Remove ",
            "Refactored ": "Refactor ",
            "Improved ": "Improve ",
            "Changed ": "Change ",
            "Implemented ": "Implement ",
        }
        for past, imperative in past_to_imperative.items():
            if v.startswith(past):
                v = imperative + v[len(past):]
                break

        return v


    @field_validator("changes", "risks", mode="before")
    @classmethod
    def ensure_list_of_strings(cls, v) -> list[str]:
        """
        The LLM sometimes returns a single string instead of a list.
        Example: "changes": "Updated the login function"
        instead of: "changes": ["Updated the login function"]
        
        This validator catches that and wraps it in a list automatically.
        mode="before" means this runs BEFORE Pydantic does its own type check.
        """
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            # Also clean up each item — strip whitespace and empty strings
            return [str(item).strip() for item in v if str(item).strip()]
        return []


    @field_validator("summary")
    @classmethod
    def clean_summary(cls, v: str) -> str:
        """
        Remove any markdown formatting the LLM accidentally puts in the summary.
        Summaries should be plain text, not markdown.
        """
        # Remove leading/trailing whitespace and asterisks (bold markdown)
        v = v.strip().strip("*")
        return v


def validate_llm_output(raw: dict) -> PRDescription:
    """
    Takes the raw dict from json.loads() and validates it against our schema.
    
    Returns a clean PRDescription object if valid.
    Raises a clear ValueError with details if something is wrong.
    
    This is the only function the rest of the codebase calls —
    they never touch PRDescription directly.
    """
    try:
        return PRDescription(**raw)
    except Exception as e:
        # Pydantic errors are detailed but verbose. We format them cleanly.
        raise ValueError(f"LLM returned invalid output:\n{e}")


def safe_validate(raw: dict) -> PRDescription:
    """
    A softer version of validate_llm_output.
    Instead of raising an error on bad output, it fills in sensible defaults.
    
    Use this in production — you want the tool to always produce SOMETHING
    rather than crash on a bad LLM response.
    """
    # Fill in defaults for any missing or broken fields
    safe_raw = {
        "title": raw.get("title", "Update codebase"),
        "summary": raw.get("summary", "Changes made to the codebase."),
        "changes": raw.get("changes", []),
        "risks": raw.get("risks", []),
        "notes": raw.get("notes", ""),
    }

    # Run the validators but catch any remaining issues
    try:
        return PRDescription(**safe_raw)
    except Exception:
        # Absolute fallback — return a minimal valid object
        return PRDescription(
            title="Update codebase",
            summary="Changes made to the codebase.",
            changes=[],
            risks=[],
            notes="Could not parse LLM output.",
        )