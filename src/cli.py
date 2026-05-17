# src/cli.py
import subprocess
import sys
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from src.parser import parse_diff, get_diff_summary
from src.chunker import chunk_files
from src.llm import analyze_diff_chunk, merge_results
from src.config import DEFAULT_PROVIDER

# ── NEW ── Import the three new modules
from src.validator import safe_validate
from src.formatter import to_markdown, to_github_body
from src.codeowners import suggest_reviewers, create_sample_codeowners

app = typer.Typer(help="AI-powered git PR description generator.")
console = Console()


def get_git_diff(base: str = "HEAD~1") -> str:
    """Runs git diff and returns output as a string."""
    result = subprocess.run(
        ["git", "diff", base],
        capture_output=True,
        text=True,
        encoding="utf-8",        # ← add this line
        errors="replace",        # ← add this line — replaces unreadable chars instead of crashing
        cwd=".",
    )
    if result.returncode != 0:
        console.print(f"[red]Error running git diff:[/red] {result.stderr}")
        raise typer.Exit(1)
    return result.stdout


def get_repo_root() -> str:
    """Returns the root directory of the current git repo."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",        # ← add this
        errors="replace",        # ← add this
        cwd=".",
    )
    if result.returncode != 0:
        return "."
    return result.stdout.strip()

@app.command()
def main(
    base: str = typer.Option(
        "HEAD~1", "--base", "-b",
        help="Base commit to diff against."
    ),
    provider: str = typer.Option(
        DEFAULT_PROVIDER, "--provider", "-p",
        help="LLM provider: 'openai' or 'claude'."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse and chunk only. Skip LLM call."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show chunk previews and extra detail."
    ),
    # ── NEW ── Copy flag
    copy: bool = typer.Option(
        False, "--copy", "-c",
        help="Copy the Markdown output to clipboard."
    ),
    # ── NEW ── GitHub PR creation flag
    create_pr: bool = typer.Option(
        False, "--create-pr",
        help="Create a GitHub PR using the gh CLI."
    ),
    # ── NEW ── Init codeowners flag
    init_codeowners: bool = typer.Option(
        False, "--init-codeowners",
        help="Create a sample CODEOWNERS file in .github/ and exit."
    ),
):
    """
    Generate a PR description from your current git diff.

    Examples:\n
        pr-summarize\n
        pr-summarize --base main\n
        pr-summarize --provider claude --copy\n
        pr-summarize --create-pr\n
        pr-summarize --dry-run --verbose
    """

    # ── NEW ── Handle --init-codeowners before anything else
    if init_codeowners:
        import os
        github_dir = os.path.join(get_repo_root(), ".github")
        os.makedirs(github_dir, exist_ok=True)
        path = os.path.join(github_dir, "CODEOWNERS")
        if os.path.exists(path):
            console.print(f"[yellow]CODEOWNERS already exists at {path}[/yellow]")
        else:
            with open(path, "w") as f:
                f.write(create_sample_codeowners())
            console.print(f"[green]Created CODEOWNERS at {path}[/green]")
            console.print("Edit it to add your team's GitHub usernames.")
        raise typer.Exit(0)

    # ── Step 1: Get diff ────────────────────────────────────────────────
    console.print(f"\n[bold]Fetching diff[/bold] against [cyan]{base}[/cyan]...")
    diff_text = get_git_diff(base)

    if not diff_text.strip():
        console.print("[yellow]No changes detected.[/yellow] Commit something first.")
        raise typer.Exit(0)

    # ── Step 2: Parse ───────────────────────────────────────────────────
    files = parse_diff(diff_text)
    summary = get_diff_summary(files)

    console.print(
        f"Found [bold]{summary['total_files']}[/bold] changed file(s) — "
        f"[green]+{summary['total_added']}[/green] / "
        f"[red]-{summary['total_removed']}[/red]"
    )

    # ── NEW ── Show risky files in verbose mode
    if summary["risky_files"]:
        console.print(
            f"[yellow]⚠ Sensitive files:[/yellow] "
            f"{', '.join(summary['risky_files'])}"
        )

    # ── NEW ── Reviewer suggestions from CODEOWNERS
    repo_root = get_repo_root()
    changed_filenames = [f.filename for f in files]
    reviewer_data = suggest_reviewers(changed_filenames, repo_root=repo_root)

    if reviewer_data["codeowners_found"] and reviewer_data["reviewers"]:
        console.print(
            f"[blue]Suggested reviewers:[/blue] "
            f"{', '.join(reviewer_data['reviewers'])}"
        )
    elif not reviewer_data["codeowners_found"] and verbose:
        console.print(
            "[dim]No CODEOWNERS file found. "
            "Run --init-codeowners to create one.[/dim]"
        )

    # ── Step 3: Chunk ───────────────────────────────────────────────────
    chunks = chunk_files(files)

    if verbose:
        console.print(
            f"Split into [bold]{len(chunks)}[/bold] chunk(s) "
            f"for LLM context window."
        )
        for i, chunk in enumerate(chunks):
            console.print(f"\n[dim]--- Chunk {i+1} preview ---[/dim]")
            console.print(f"[dim]{chunk[:300]}...[/dim]")

    if dry_run:
        console.print("\n[yellow]--dry-run:[/yellow] Skipping LLM call.")
        raise typer.Exit(0)

    # ── Step 4: Call LLM ────────────────────────────────────────────────
    raw_results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(chunks))
        for i, chunk in enumerate(chunks):
            progress.update(
                task,
                description=f"Analyzing chunk {i+1}/{len(chunks)}..."
            )
            try:
                result = analyze_diff_chunk(chunk, provider=provider)
                raw_results.append(result)
            except Exception as e:
                console.print(f"[red]LLM error on chunk {i+1}:[/red] {e}")
                raise typer.Exit(1)
            progress.advance(task)

    # ── NEW ── Merge + Validate with Pydantic
    # In Week 1 we passed the raw dict straight to rendering.
    # Now we merge first, then run through Pydantic validation.
    # safe_validate() never crashes — it fills in defaults if something is wrong.
    merged_raw = merge_results(raw_results)
    validated = safe_validate(merged_raw)

    # ── NEW ── Add reviewer suggestions into the validated result's notes
    # We append reviewer info to the notes field so it shows up in output.
    if reviewer_data["reviewers"]:
        reviewer_note = (
            f"Suggested reviewers: {', '.join(reviewer_data['reviewers'])}"
        )
        if validated.notes:
            validated.notes = validated.notes + " | " + reviewer_note
        else:
            validated.notes = reviewer_note

    # ── Step 5: Render output ───────────────────────────────────────────
    render_output(validated, summary, reviewer_data)

    # ── NEW ── Build the markdown string for copy/gh operations
    markdown_output = to_markdown(validated, summary)

    # ── NEW ── --copy flag: put markdown on clipboard
    if copy:
        try:
            import pyperclip
            pyperclip.copy(markdown_output)
            console.print(
                "\n[green]✓ Copied to clipboard.[/green] "
                "Paste directly into GitHub."
            )
        except Exception as e:
            console.print(f"[red]Clipboard error:[/red] {e}")

    # ── NEW ── --create-pr flag: pipe into gh CLI
    if create_pr:
        create_github_pr(validated, summary)


def render_output(result, summary: dict, reviewer_data: dict):
    """
    Renders the validated PRDescription to the terminal.
    Uses Rich panels for a clean, readable layout.
    """
    console.print()

    # Title panel
    console.print(Panel(
        f"[bold]{result.title}[/bold]",
        title="PR Title",
        border_style="blue",
    ))

    # Summary panel
    console.print(Panel(
        result.summary,
        title="Summary",
        border_style="cyan",
    ))

    # Changes panel
    if result.changes:
        change_text = "\n".join(f"• {c}" for c in result.changes)
        console.print(Panel(
            change_text,
            title="Changes",
            border_style="green"
        ))

    # Risks panel
    if result.risks:
        risk_text = "\n".join(f"⚠ {r}" for r in result.risks)
        console.print(Panel(
            risk_text,
            title="Risks",
            border_style="red"
        ))

    # Notes panel (includes reviewer suggestions now)
    if result.notes and result.notes.strip():
        console.print(Panel(
            result.notes,
            title="Notes",
            border_style="yellow"
        ))

    # ── NEW ── Reviewer table (only shown if CODEOWNERS exists)
    if reviewer_data["codeowners_found"] and reviewer_data["file_ownership"]:
        console.print()
        table = Table(title="File Ownership", border_style="dim")
        table.add_column("File", style="cyan")
        table.add_column("Owners", style="yellow")
        for fname, owners in reviewer_data["file_ownership"].items():
            table.add_row(fname, ", ".join(owners))
        console.print(table)

    # ── Markdown output (ready to copy) ────────────────────────────────
    from src.formatter import to_markdown
    md = to_markdown(result, summary)
    console.print("\n[bold]Markdown Output:[/bold]")
    console.print(Syntax(md, "markdown", theme="monokai"))


def create_github_pr(result, summary: dict):
    """
    Creates a GitHub Pull Request using the GitHub CLI (`gh`).
    
    `gh` is GitHub's official CLI tool. `gh pr create` opens a PR
    with a given title and body. We pipe our generated markdown as the body.
    
    Prerequisite: user must have `gh` installed and authenticated.
    We check for this and give a clear error if not.
    """

    # Check if gh is installed
    check = subprocess.run(
        ["gh", "--version"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        console.print(
            "[red]GitHub CLI not found.[/red]\n"
            "Install it from: https://cli.github.com\n"
            "Then run: gh auth login"
        )
        return

    # Build the PR body using the GitHub-specific formatter
    from src.formatter import to_github_body
    body = to_github_body(result, summary)

    console.print("\n[bold]Creating GitHub PR...[/bold]")

    # Run gh pr create
    # --title sets the PR title
    # --body sets the PR description
    # --web opens the PR in the browser after creation
    proc = subprocess.run(
        [
            "gh", "pr", "create",
            "--title", result.title,
            "--body", body,
            "--web",    # Opens browser so user can review before submitting
        ],
        text=True,
    )

    if proc.returncode == 0:
        console.print("[green]✓ PR created successfully.[/green]")
    else:
        console.print(
            "[red]gh pr create failed.[/red]\n"
            "Make sure you're on a branch (not main) and "
            "have run: gh auth login"
        )


if __name__ == "__main__":
    app()