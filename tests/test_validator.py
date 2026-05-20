# tests/test_validator.py
import pytest
from src.validator import validate_llm_output, safe_validate, PRDescription


class TestPRDescriptionSchema:
    """Tests for the Pydantic schema itself."""

    def test_valid_input_accepted(self):
        """A perfectly formed LLM response should pass validation cleanly."""
        raw = {
            "title": "Add login endpoint",
            "summary": "This PR adds a login endpoint with JWT auth.",
            "changes": ["Added POST /login route", "Added JWT token generation"],
            "risks": ["Touches auth logic"],
            "notes": "",
        }
        result = validate_llm_output(raw)
        assert result.title == "Add login endpoint"
        assert len(result.changes) == 2

    def test_past_tense_corrected(self):
        """
        Validator must auto-correct past tense to imperative.
        'Added X' → 'Add X'
        This is the most common LLM mistake.
        """
        raw = {
            "title": "Added JWT authentication",
            "summary": "Added auth to the app.",
            "changes": [],
            "risks": [],
        }
        result = validate_llm_output(raw)
        assert result.title == "Add JWT authentication"

    def test_quoted_title_stripped(self):
        """LLMs sometimes wrap the title in quotes — strip them."""
        raw = {
            "title": '"Fix the login bug"',
            "summary": "Fixed a bug in login.",
            "changes": [],
            "risks": [],
        }
        result = validate_llm_output(raw)
        assert result.title == "Fix the login bug"

    def test_string_changes_coerced_to_list(self):
        """
        If LLM returns changes as a string instead of a list,
        the validator should wrap it in a list automatically.
        """
        raw = {
            "title": "Fix bug",
            "summary": "Fixed a bug.",
            "changes": "Updated the login function",   # string, not list
            "risks": [],
        }
        result = validate_llm_output(raw)
        assert isinstance(result.changes, list)
        assert len(result.changes) == 1

    def test_missing_optional_fields_use_defaults(self):
        """risks and notes are optional — missing them should not raise."""
        raw = {
            "title": "Add feature",
            "summary": "Added a new feature to the app.",
            # no risks, no notes
        }
        result = validate_llm_output(raw)
        assert result.risks == []
        assert result.notes == ""

    def test_missing_required_field_raises(self):
        """title is required — missing it must raise ValueError."""
        raw = {
            "summary": "Some summary.",
            "changes": [],
        }
        with pytest.raises(ValueError):
            validate_llm_output(raw)


class TestSafeValidate:
    """Tests for the fallback safe_validate() function."""

    def test_never_raises(self):
        """safe_validate must never raise, even on completely broken input."""
        bad_inputs = [
            {},
            {"title": ""},
            {"title": None, "summary": None},
            {"title": 123, "summary": 456},
        ]
        for bad in bad_inputs:
            result = safe_validate(bad)   # Should never raise
            assert isinstance(result, PRDescription)

    def test_returns_prdescription(self):
        """safe_validate always returns a PRDescription object."""
        result = safe_validate({"title": "Fix bug", "summary": "Fixed it."})
        assert isinstance(result, PRDescription)