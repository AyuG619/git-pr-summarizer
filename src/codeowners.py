# src/codeowners.py
import os
import fnmatch


def find_codeowners_file(repo_root: str = ".") -> str | None:
    """
    GitHub looks for CODEOWNERS in three locations, in this priority order:
    1. CODEOWNERS          (root of repo)
    2. docs/CODEOWNERS
    3. .github/CODEOWNERS
    
    We do the same — check all three and return the first one found.
    Returns the file path if found, None if this repo has no CODEOWNERS.
    """
    candidates = [
        os.path.join(repo_root, "CODEOWNERS"),
        os.path.join(repo_root, "docs", "CODEOWNERS"),
        os.path.join(repo_root, ".github", "CODEOWNERS"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def parse_codeowners(filepath: str) -> list[tuple[str, list[str]]]:
    """
    Parses the CODEOWNERS file into a list of (pattern, owners) tuples.
    
    CODEOWNERS format:
        # This is a comment
        *.py @alice @bob
        src/auth/ @charlie
        /README.md @team-lead
    
    We parse this line by line, skip comments and blank lines,
    and return a list like:
        [
            ("*.py", ["@alice", "@bob"]),
            ("src/auth/", ["@charlie"]),
            ("/README.md", ["@team-lead"]),
        ]
    
    Order matters — later rules override earlier ones in GitHub's engine.
    We preserve order so callers can apply the same logic.
    """
    rules: list[tuple[str, list[str]]] = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # Skip blank lines and comment lines
            if not line or line.startswith("#"):
                continue

            # Split on whitespace: "*.py @alice @bob" → ["*.py", "@alice", "@bob"]
            parts = line.split()
            if len(parts) < 2:
                # A pattern with no owner is valid in CODEOWNERS but useless to us
                continue

            pattern = parts[0]           # "*.py" or "src/auth/" or "/README.md"
            owners = parts[1:]           # ["@alice", "@bob"]
            rules.append((pattern, owners))

    return rules


def match_file_to_owners(
    filename: str,
    rules: list[tuple[str, list[str]]]
) -> list[str]:
    """
    Given a filename and all the CODEOWNERS rules, returns the owners
    that apply to this file.
    
    GitHub applies the LAST matching rule (rules at the bottom take priority).
    We replicate that by iterating all rules and keeping track of the last match.
    
    fnmatch is Python's built-in glob pattern matcher.
    fnmatch.fnmatch("src/auth/login.py", "*.py") → True
    fnmatch.fnmatch("src/auth/login.py", "src/auth/") → False (directories need special handling)
    """
    matched_owners: list[str] = []

    for pattern, owners in rules:
        # Handle directory patterns like "src/auth/"
        # If pattern ends with /, it matches any file inside that directory
        if pattern.endswith("/"):
            # Strip the trailing slash and check if filename starts with this path
            dir_pattern = pattern.rstrip("/")
            if filename.startswith(dir_pattern + "/") or filename.startswith(dir_pattern):
                matched_owners = owners   # Override with this match

        # Handle root-anchored patterns like "/README.md"
        elif pattern.startswith("/"):
            # Strip the leading slash — it anchors to repo root
            anchored = pattern.lstrip("/")
            if fnmatch.fnmatch(filename, anchored):
                matched_owners = owners

        # Handle glob patterns like "*.py" or "src/**/*.ts"
        else:
            # fnmatch checks the full path against the pattern
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(
                os.path.basename(filename), pattern
            ):
                matched_owners = owners

    return matched_owners


def suggest_reviewers(
    changed_files: list[str],
    repo_root: str = "."
) -> dict:
    """
    Main public function. Takes a list of changed filenames and returns
    reviewer suggestions.
    
    Returns a dict with:
        "reviewers"       — sorted list of unique owner handles to tag
        "file_ownership"  — dict mapping each file to its owners
        "codeowners_found"— bool, False if there's no CODEOWNERS file
    
    Example return:
        {
            "reviewers": ["@alice", "@bob"],
            "file_ownership": {
                "src/auth/login.py": ["@bob"],
                "src/utils/helpers.py": ["@alice"],
            },
            "codeowners_found": True,
        }
    """
    codeowners_path = find_codeowners_file(repo_root)

    if codeowners_path is None:
        return {
            "reviewers": [],
            "file_ownership": {},
            "codeowners_found": False,
        }

    rules = parse_codeowners(codeowners_path)
    file_ownership: dict[str, list[str]] = {}
    all_reviewers: set[str] = set()

    for filename in changed_files:
        owners = match_file_to_owners(filename, rules)
        if owners:
            file_ownership[filename] = owners
            all_reviewers.update(owners)

    return {
        "reviewers": sorted(all_reviewers),    # sorted for consistent output
        "file_ownership": file_ownership,
        "codeowners_found": True,
    }


def create_sample_codeowners() -> str:
    """
    Returns sample CODEOWNERS content.
    Used by the CLI's --init-codeowners flag (added in cli.py below)
    to help users get started.
    """
    return """\
# CODEOWNERS — assign reviewers by file pattern
# Docs: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners

# Default owner for everything not matched below
* @your-username

# Python files
*.py @backend-team

# Auth and security — always needs careful review
src/auth/ @security-lead @backend-team

# Database migrations
**/migrations/ @dba-team

# Frontend
*.ts @frontend-team
*.tsx @frontend-team

# CI/CD config
.github/ @devops-team
"""