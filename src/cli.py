# src/cli.py
import subprocess
import os
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
from src.validator import safe_validate
from src.formatter import to_markdown, to_github_body
from src.codeowners import suggest_reviewers, create_sample_codeowners

app = typer.Typer(
    help="AI-powered git PR description generator.",
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()


def get_git_diff(base: str = "HEAD~1") -> str:
    result = subprocess.run(
        ["git", "diff", base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=".",
    )
    if result.returncode != 0:
        console.print(f"[red]Error running git diff:[/red] {result.stderr}")
        raise typer.Exit(1)
    return result.stdout


def get_repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=".",
    )
    if result.returncode != 0:
        return "."
    return result.stdout.strip()


# ── THIS IS THE KEY CHANGE ──────────────────────────────────────────────
# @app.callback instead of @app.command()
# This makes `main` the default action when no subcommand is given,
# while still allowing `pr-summarize configure` to work as a subcommand.
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,           # ← ctx tells us if a subcommand was invoked
    base: str = typer.Option(
        "HEAD~1", "--base", "-b",
        help="Base commit to diff against."
    ),
    provider: str = typer.Option(
        DEFAULT_PROVIDER, "--provider", "-p",
        help="LLM provider: groq, gemini, or ollama."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse and chunk only. Skip LLM call."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show chunk previews and extra detail."
    ),
    copy: bool = typer.Option(
        False, "--copy", "-c",
        help="Copy Markdown output to clipboard."
    ),
    create_pr: bool = typer.Option(
        False, "--create-pr",
        help="Create a GitHub PR using the gh CLI."
    ),
    init_codeowners: bool = typer.Option(
        False, "--init-codeowners",
        help="Create a sample CODEOWNERS file and exit."
    ),
):
    """
    Generate a PR description from your current git diff.\n
    Examples:\n
        pr-summarize\n
        pr-summarize --base main\n
        pr-summarize --provider gemini --copy\n
        pr-summarize --create-pr\n
        pr-summarize --dry-run --verbose\n
        pr-summarize configure
    """

    # If user typed `pr-summarize configure`, ctx.invoked_subcommand
    # will be "configure" and we return immediately — letting typer
    # route to the configure() function below instead.
    if ctx.invoked_subcommand is not None:
        return

    # ── --init-codeowners ───────────────────────────────────────────────
    if init_codeowners:
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

    # ── Config validation ───────────────────────────────────────────────
    from src.config import validate_config, CONFIG
    problems = validate_config(CONFIG)
    if problems:
        for p in problems:
            console.print(f"[red]Config error:[/red] {p}")
        raise typer.Exit(1)

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

    if summary["risky_files"]:
        console.print(
            f"[yellow]⚠ Sensitive files:[/yellow] "
            f"{', '.join(summary['risky_files'])}"
        )

    # ── CODEOWNERS reviewer suggestions ────────────────────────────────
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

    # ── Step 5: Merge + Validate ────────────────────────────────────────
    merged_raw = merge_results(raw_results)
    validated = safe_validate(merged_raw)

    # Append reviewer suggestions into notes
    if reviewer_data["reviewers"]:
        reviewer_note = f"Suggested reviewers: {', '.join(reviewer_data['reviewers'])}"
        validated.notes = (
            validated.notes + " | " + reviewer_note
            if validated.notes
            else reviewer_note
        )

    # ── Step 6: Render ──────────────────────────────────────────────────
    render_output(validated, summary, reviewer_data)

    markdown_output = to_markdown(validated, summary)

    # ── --copy ──────────────────────────────────────────────────────────
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

    # ── --create-pr ─────────────────────────────────────────────────────
    if create_pr:
        create_github_pr(validated, summary)


def render_output(result, summary: dict, reviewer_data: dict):
    console.print()

    console.print(Panel(
        f"[bold]{result.title}[/bold]",
        title="PR Title",
        border_style="blue",
    ))

    console.print(Panel(
        result.summary,
        title="Summary",
        border_style="cyan",
    ))

    if result.changes:
        console.print(Panel(
            "\n".join(f"• {c}" for c in result.changes),
            title="Changes",
            border_style="green",
        ))

    if result.risks:
        console.print(Panel(
            "\n".join(f"⚠ {r}" for r in result.risks),
            title="Risks",
            border_style="red",
        ))

    if result.notes and result.notes.strip():
        console.print(Panel(
            result.notes,
            title="Notes",
            border_style="yellow",
        ))

    if reviewer_data["codeowners_found"] and reviewer_data["file_ownership"]:
        console.print()
        table = Table(title="File Ownership", border_style="dim")
        table.add_column("File", style="cyan")
        table.add_column("Owners", style="yellow")
        for fname, owners in reviewer_data["file_ownership"].items():
            table.add_row(fname, ", ".join(owners))
        console.print(table)

    md = to_markdown(result, summary)
    console.print("\n[bold]Markdown Output:[/bold]")
    console.print(Syntax(md, "markdown", theme="monokai"))


def create_github_pr(result, summary: dict):
    check = subprocess.run(
        ["gh", "--version"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        console.print(
            "[red]GitHub CLI not found.[/red]\n"
            "Install from: https://cli.github.com\n"
            "Then run: gh auth login"
        )
        return

    body = to_github_body(result, summary)
    console.print("\n[bold]Creating GitHub PR...[/bold]")

    proc = subprocess.run(
        ["gh", "pr", "create",
         "--title", result.title,
         "--body", body,
         "--web"],
        text=True,
    )

    if proc.returncode == 0:
        console.print("[green]✓ PR created successfully.[/green]")
    else:
        console.print(
            "[red]gh pr create failed.[/red]\n"
            "Make sure you're on a branch and have run: gh auth login"
        )


@app.command("configure")
def configure():
    """Save API keys and preferences to ~/.pr-summarizer.toml"""
    from src.config import save_config, CONFIG_FILE

    console.print("\n[bold]PR Summarizer — Setup[/bold]")
    console.print(f"Config will be saved to: [cyan]{CONFIG_FILE}[/cyan]\n")

    provider = typer.prompt("Default provider (groq/gemini/ollama)", default="groq")
    while provider not in ("groq", "gemini", "ollama"):
        console.print("[red]Must be one of: groq, gemini, ollama[/red]")
        provider = typer.prompt("Default provider", default="groq")

    updates = {"provider": provider}

    if provider == "groq":
        key = typer.prompt("Groq API key", hide_input=True)
        updates["groq_api_key"] = key
        updates["groq_model"] = "llama-3.3-70b-versatile"

    elif provider == "gemini":
        key = typer.prompt("Gemini API key", hide_input=True)
        updates["gemini_api_key"] = key
        updates["gemini_model"] = "gemini-2.5-pro"

    elif provider == "ollama":
        host = typer.prompt("Ollama host", default="http://localhost:11434")
        model = typer.prompt("Ollama model", default="codellama")
        updates["ollama_host"] = host
        updates["ollama_model"] = model

    save_config(updates)
    console.print(f"\n[green]✓ Config saved to {CONFIG_FILE}[/green]")
    console.print("Run [bold]pr-summarize[/bold] to test it.")


if __name__ == "__main__":
    app()