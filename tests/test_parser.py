# tests/test_parser.py
import pytest
from src.parser import parse_diff, get_diff_summary, FileDiff


# ── Fixtures ────────────────────────────────────────────────────────────
# Fixtures are reusable test data. Instead of copy-pasting the same diff
# string into every test, we define it once here and pytest injects it
# wherever a test function has a matching parameter name.

@pytest.fixture
def simple_diff():
    """A minimal valid diff — one file, one hunk, small change."""
    return """\
diff --git a/src/auth/login.py b/src/auth/login.py
index abc123..def456 100644
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -10,4 +10,6 @@ def login(user, password):
     if not user:
         return None
-    return check(user, password)
+    result = check(user, password)
+    log_attempt(user)
+    return result
"""

@pytest.fixture
def multi_file_diff():
    """A diff touching two files — tests that we correctly track boundaries."""
    return """\
diff --git a/src/auth/login.py b/src/auth/login.py
index abc..def 100644
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,3 +1,4 @@
 def login():
-    pass
+    return True
+
diff --git a/src/utils/helpers.py b/src/utils/helpers.py
index 111..222 100644
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -1,2 +1,5 @@
+def format_date(dt):
+    return dt.isoformat()
+
 def slugify(text):
     return text.lower()
"""

@pytest.fixture
def pycache_diff():
    """A diff that includes __pycache__ — should be filtered out."""
    return """\
diff --git a/src/__pycache__/login.cpython-313.pyc b/src/__pycache__/login.cpython-313.pyc
index abc..def 100644
Binary files differ
diff --git a/src/login.py b/src/login.py
index abc..def 100644
--- a/src/login.py
+++ b/src/login.py
@@ -1,2 +1,3 @@
 def login():
+    return True
"""


# ── Parser tests ────────────────────────────────────────────────────────

class TestParseDiff:
    """Tests for the core parse_diff() function."""

    def test_returns_list(self, simple_diff):
        """parse_diff always returns a list, even on empty input."""
        result = parse_diff(simple_diff)
        assert isinstance(result, list)

    def test_correct_file_count(self, simple_diff):
        """One file diff should produce exactly one FileDiff object."""
        result = parse_diff(simple_diff)
        assert len(result) == 1

    def test_correct_filename(self, simple_diff):
        """The filename should be parsed from the b/ path correctly."""
        result = parse_diff(simple_diff)
        assert result[0].filename == "src/auth/login.py"

    def test_added_lines_counted(self, simple_diff):
        """Lines starting with + should be counted as added."""
        result = parse_diff(simple_diff)
        assert result[0].added_count == 3

    def test_removed_lines_counted(self, simple_diff):
        """Lines starting with - should be counted as removed."""
        result = parse_diff(simple_diff)
        assert result[0].removed_count == 1

    def test_multi_file_boundary(self, multi_file_diff):
        """Parser must correctly split two files into two FileDiff objects."""
        result = parse_diff(multi_file_diff)
        assert len(result) == 2
        filenames = [f.filename for f in result]
        assert "src/auth/login.py" in filenames
        assert "src/utils/helpers.py" in filenames

    def test_pycache_filtered(self, pycache_diff):
        """__pycache__ and .pyc files must never appear in parsed output."""
        result = parse_diff(pycache_diff)
        for f in result:
            assert "__pycache__" not in f.filename
            assert not f.filename.endswith(".pyc")

    def test_empty_diff(self):
        """Empty string input should return empty list, not crash."""
        result = parse_diff("")
        assert result == []

    def test_hunk_parsed(self, simple_diff):
        """Each file should have at least one hunk parsed."""
        result = parse_diff(simple_diff)
        assert len(result[0].hunks) >= 1

    def test_extension_extracted(self, simple_diff):
        """File extension should be extracted correctly."""
        result = parse_diff(simple_diff)
        assert result[0].extension == ".py"


# ── Risk classifier tests ───────────────────────────────────────────────

class TestRiskClassifier:
    """Tests for the is_risky property on FileDiff."""

    @pytest.mark.parametrize("filename,expected", [
        ("src/auth/login.py",        True),   # contains 'auth'
        ("src/payment/stripe.py",    True),   # contains 'payment'
        ("db/migrations/001.sql",    True),   # contains 'migration'
        ("src/utils/helpers.py",     False),  # no risky keyword
        ("README.md",                False),  # no risky keyword
        ("src/admin/dashboard.py",   True),   # contains 'admin'
        ("config/database.yml",      True),   # contains 'database'
    ])
    def test_risk_detection(self, filename, expected):
        """
        Parametrize runs this one test function once per tuple in the list.
        Much cleaner than writing 7 separate test functions.
        """
        f = FileDiff(filename=filename, change_type="modified")
        assert f.is_risky == expected


# ── Summary tests ───────────────────────────────────────────────────────

class TestGetDiffSummary:
    """Tests for the get_diff_summary() aggregation function."""

    def test_total_files(self, multi_file_diff):
        files = parse_diff(multi_file_diff)
        summary = get_diff_summary(files)
        assert summary["total_files"] == 2

    def test_total_added(self, simple_diff):
        files = parse_diff(simple_diff)
        summary = get_diff_summary(files)
        assert summary["total_added"] == 3

    def test_risky_files_list(self, simple_diff):
        """src/auth/login.py should appear in risky_files."""
        files = parse_diff(simple_diff)
        summary = get_diff_summary(files)
        assert "src/auth/login.py" in summary["risky_files"]

    def test_extensions_collected(self, simple_diff):
        files = parse_diff(simple_diff)
        summary = get_diff_summary(files)
        assert ".py" in summary["extensions"]