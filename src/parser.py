# src/parser.py
from dataclasses import dataclass, field


# A dataclass is like a plain class but Python auto-generates
# __init__, __repr__ etc. for you. Think of it as a typed struct.

@dataclass
class Hunk:
    """
    A hunk is one contiguous block of changes in a file.
    Git diffs show them starting with @@ -line,count +line,count @@
    Example: @@ -10,6 +10,8 @@ def my_function():
    """
    old_start: int        # Line number in old file where this hunk starts
    new_start: int        # Line number in new file where this hunk starts
    added_lines: list[str] = field(default_factory=list)    # Lines starting with +
    removed_lines: list[str] = field(default_factory=list)  # Lines starting with -
    context_lines: list[str] = field(default_factory=list)  # Unchanged lines shown for context


@dataclass
class FileDiff:
    """
    Represents all changes to a single file in the diff.
    One git diff can contain many FileDiffs.
    """
    filename: str                          # e.g. "src/auth/login.py"
    change_type: str                       # "modified", "added", "deleted", "renamed"
    old_filename: str = ""                 # Only set on renames
    extension: str = ""                    # e.g. ".py", ".ts", ".go"
    hunks: list[Hunk] = field(default_factory=list)
    added_count: int = 0                   # Total lines added
    removed_count: int = 0                 # Total lines removed

    @property
    def is_risky(self) -> bool:
        """
        Heuristic: flag files that commonly contain sensitive logic.
        We check if the filename contains any of these keywords.
        This is used later to generate the 'risks' section in the PR description.
        """
        risky_keywords = [
            "auth", "login", "password", "secret", "token",
            "migration", "schema", "database", "db", "payment",
            "security", "permission", "role", "admin"
        ]
        lower = self.filename.lower()
        return any(keyword in lower for keyword in risky_keywords)


def parse_diff(diff_text: str) -> list[FileDiff]:
    """
    Main parsing function.
    Takes raw git diff output as a string and returns a list of FileDiff objects.

    A git diff looks like this:
    ─────────────────────────────────────
    diff --git a/src/auth.py b/src/auth.py
    index abc123..def456 100644
    --- a/src/auth.py
    +++ b/src/auth.py
    @@ -10,6 +10,8 @@
     def login(user, password):
    -    return check(user, password)
    +    result = check(user, password)
    +    log_attempt(user)
    +    return result
    ─────────────────────────────────────
    We parse this line by line, using the "diff --git" lines as file boundaries
    and "@@ ... @@" lines as hunk boundaries.
    """

    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None

    for line in diff_text.splitlines():

        # ── Detect start of a new file ──────────────────────────────────
        # "diff --git a/old_path b/new_path" marks the beginning of each file's diff
        if line.startswith("diff --git "):
            # If we were already tracking a file, save it before starting a new one
            if current_file is not None:
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)
                files.append(current_file)

            # Extract the filename from "diff --git a/src/foo.py b/src/foo.py"
            # We take the 'b/' side (new file path) as the canonical filename
            parts = line.split(" ")
            raw_path = parts[-1]  # "b/src/foo.py"
            filename = raw_path[2:] if raw_path.startswith("b/") else raw_path

            import os
            _, ext = os.path.splitext(filename)

            current_file = FileDiff(
                filename=filename,
                change_type="modified",   # Default; overridden below if needed
                extension=ext,
            )
            current_hunk = None

        # ── Detect file-level metadata lines ────────────────────────────
        elif line.startswith("new file"):
            if current_file:
                current_file.change_type = "added"

        elif line.startswith("deleted file"):
            if current_file:
                current_file.change_type = "deleted"

        elif line.startswith("rename from "):
            if current_file:
                current_file.change_type = "renamed"
                current_file.old_filename = line.replace("rename from ", "").strip()

        # ── Detect start of a new hunk ──────────────────────────────────
        # Hunk headers look like: @@ -10,6 +10,8 @@ optional_function_name
        elif line.startswith("@@ "):
            # Save previous hunk if it exists
            if current_hunk is not None and current_file is not None:
                current_file.hunks.append(current_hunk)

            # Parse "@@ -10,6 +10,8 @@" to extract line numbers
            try:
                # The hunk header format: @@ -old_start,old_count +new_start,new_count @@
                hunk_info = line.split("@@")[1].strip()      # "-10,6 +10,8"
                old_part, new_part = hunk_info.split(" ")    # "-10,6" and "+10,8"
                old_start = int(old_part[1:].split(",")[0])  # 10
                new_start = int(new_part[1:].split(",")[0])  # 10
            except (IndexError, ValueError):
                old_start, new_start = 0, 0

            current_hunk = Hunk(old_start=old_start, new_start=new_start)

        # ── Process diff content lines ───────────────────────────────────
        # Lines inside a hunk start with +, -, or space (context)
        elif current_hunk is not None and current_file is not None:

            if line.startswith("+") and not line.startswith("+++"):
                # Added line — strip the leading "+"
                clean = line[1:]
                current_hunk.added_lines.append(clean)
                current_file.added_count += 1

            elif line.startswith("-") and not line.startswith("---"):
                # Removed line — strip the leading "-"
                clean = line[1:]
                current_hunk.removed_lines.append(clean)
                current_file.removed_count += 1

            elif line.startswith(" "):
                # Context line (unchanged, just shown for reference)
                current_hunk.context_lines.append(line[1:])

    # ── Don't forget the last file/hunk ─────────────────────────────────
    if current_file is not None:
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    return files


def get_diff_summary(files: list[FileDiff]) -> dict:
    """
    Returns a high-level summary of what changed.
    Used for logging and for building the LLM prompt context.
    """
    return {
        "total_files": len(files),
        "total_added": sum(f.added_count for f in files),
        "total_removed": sum(f.removed_count for f in files),
        "risky_files": [f.filename for f in files if f.is_risky],
        "change_types": {
            ct: len([f for f in files if f.change_type == ct])
            for ct in {"modified", "added", "deleted", "renamed"}
        },
        "extensions": list({f.extension for f in files if f.extension}),
    }