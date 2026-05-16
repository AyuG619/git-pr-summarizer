# src/cli.py

import subprocess
import typer

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn
)
from rich.syntax import Syntax

from src.parser import (
    parse_diff,
    get_diff_summary
)

from src.chunker import chunk_files

from src.llm import (
    analyze_diff_chunk,
    merge_results
)

from src.config import DEFAULT_PROVIDER


# =========================================================
# CLI APP SETUP
# =========================================================

app = typer.Typer(
    help="AI-powered git PR description generator."
)

console = Console()


# =========================================================
# GIT DIFF FETCHER
# =========================================================

def get_git_diff(base: str = "HEAD~1") -> str:
    """
    Runs:
        git diff <base>

    and returns output as a string.
    """

    result = subprocess.run(

        ["git", "diff", base],

        capture_output=True,

        text=True,

        cwd=".",
    )

    if result.returncode != 0:

        console.print(
            f"[red]Git diff failed:[/red] {result.stderr}"
        )

        raise typer.Exit(1)

    return result.stdout


# =========================================================
# MAIN CLI COMMAND
# =========================================================

@app.command()

def main(

    base: str = typer.Option(
        "HEAD~1",
        "--base",
        "-b",
        help="Base commit to diff against."
    ),

    provider: str = typer.Option(
        DEFAULT_PROVIDER,
        "--provider",
        "-p",
        help="LLM provider: 'groq' or 'gemini'"
    ),

    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip LLM calls."
    ),

    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed debug output."
    ),
):
    """
    Main PR summarization command.
    """

    # =====================================================
    # STEP 1 — GET DIFF
    # =====================================================

    console.print(
        f"\n[bold]Fetching diff[/bold] "
        f"against [cyan]{base}[/cyan]..."
    )

    diff_text = get_git_diff(base)

    if not diff_text.strip():

        console.print(
            "[yellow]No changes detected.[/yellow]"
        )

        raise typer.Exit(0)

    # =====================================================
    # STEP 2 — PARSE
    # =====================================================

    files = parse_diff(diff_text)

    summary = get_diff_summary(files)

    console.print(
        f"Found "
        f"[bold]{summary['total_files']}[/bold] "
        f"changed file(s) — "
        f"[green]+{summary['total_added']}[/green] "
        f"/ "
        f"[red]-{summary['total_removed']}[/red]"
    )

    if summary["risky_files"] and verbose:

        console.print(
            f"[yellow]Risky files:[/yellow] "
            f"{', '.join(summary['risky_files'])}"
        )

    # =====================================================
    # STEP 3 — CHUNK
    # =====================================================

    chunks = chunk_files(files)

    if verbose:

        console.print(
            f"Split into "
            f"[bold]{len(chunks)}[/bold] "
            f"chunk(s)."
        )

        for i, chunk in enumerate(chunks):

            console.print(
                f"\n[dim]--- Chunk {i+1} ---[/dim]"
            )

            console.print(
                f"[dim]{chunk[:300]}...[/dim]"
            )

    if dry_run:

        console.print(
            "\n[yellow]Dry run mode[/yellow]"
        )

        console.print(
            f"Would send "
            f"{len(chunks)} chunk(s) "
            f"to [bold]{provider}[/bold]"
        )

        raise typer.Exit(0)

    # =====================================================
    # STEP 4 — CALL LLM
    # =====================================================

    results = []

    with Progress(

        SpinnerColumn(),

        TextColumn(
            "[progress.description]{task.description}"
        ),

        console=console,

    ) as progress:

        task = progress.add_task(

            f"Analyzing with {provider}...",

            total=len(chunks)
        )

        for i, chunk in enumerate(chunks):

            progress.update(

                task,

                description=(
                    f"Analyzing chunk "
                    f"{i+1}/{len(chunks)}..."
                )
            )

            try:

                result = analyze_diff_chunk(
                    chunk,
                    provider=provider
                )

                results.append(result)

            except Exception as e:

                console.print(
                    f"[red]LLM Error:[/red] {e}"
                )

                raise typer.Exit(1)

            progress.advance(task)

    # =====================================================
    # STEP 5 — MERGE RESULTS
    # =====================================================

    final = merge_results(results)

    render_output(final, summary)


# =========================================================
# TERMINAL OUTPUT RENDERER
# =========================================================

def render_output(
    result: dict,
    summary: dict
):

    console.print()

    # PR TITLE
    console.print(

        Panel(

            f"[bold]{result.get('title', '')}[/bold]",

            title="PR Title",

            border_style="blue",
        )
    )

    # SUMMARY
    console.print(

        Panel(

            result.get("summary", ""),

            title="Summary",

            border_style="cyan",
        )
    )

    # CHANGES
    changes = result.get("changes", [])

    if changes:

        text = "\n".join(
            f"• {c}" for c in changes
        )

        console.print(

            Panel(
                text,
                title="Changes",
                border_style="green",
            )
        )

    # RISKS
    risks = result.get("risks", [])

    if risks:

        text = "\n".join(
            f"⚠ {r}" for r in risks
        )

        console.print(

            Panel(
                text,
                title="Risks",
                border_style="red",
            )
        )

    # NOTES
    notes = result.get("notes", "")

    if notes:

        console.print(

            Panel(
                notes,
                title="Notes",
                border_style="yellow",
            )
        )

    # MARKDOWN OUTPUT
    markdown = build_markdown(
        result,
        summary
    )

    console.print(
        "\n[bold]Markdown Output:[/bold]"
    )

    console.print(

        Syntax(
            markdown,
            "markdown",
            theme="monokai",
            line_numbers=False,
        )
    )


# =========================================================
# MARKDOWN BUILDER
# =========================================================

def build_markdown(
    result: dict,
    summary: dict
) -> str:

    lines = []

    lines.append(
        f"## {result.get('title', '')}\n"
    )

    lines.append(
        f"### Summary\n"
        f"{result.get('summary', '')}\n"
    )

    changes = result.get("changes", [])

    if changes:

        lines.append("### Changes")

        for c in changes:

            lines.append(f"- {c}")

        lines.append("")

    risks = result.get("risks", [])

    if risks:

        lines.append(
            "### ⚠️ Risks / Review Notes"
        )

        for r in risks:

            lines.append(f"- {r}")

        lines.append("")

    lines.append(
        "---\n"
        f"_Generated by git-pr-summarizer | "
        f"{summary['total_files']} files changed, "
        f"+{summary['total_added']}/"
        f"-{summary['total_removed']} lines_"
    )

    return "\n".join(lines)


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    app()