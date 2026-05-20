# git-pr-summarizer

A developer CLI tool that reads your git diff and generates a structured
Pull Request description — title, summary, changes, risks, and reviewer
suggestions — using Groq (llama-3.3-70b) or Gemini 2.5 Pro.

No copy-pasting code into ChatGPT. One command, directly in your terminal.

---

## Features

- Parses raw `git diff` output into structured per-file representations
- Token-aware chunking — works on diffs of any size
- Supports **Groq** (llama-3.3-70b-versatile), **Gemini 2.5 Pro**, and **Ollama** (offline)
- Pydantic-validated output — never crashes on bad LLM responses
- CODEOWNERS parsing — suggests reviewers based on file ownership
- Copies Markdown directly to clipboard with `--copy`
- Creates GitHub PRs via `gh pr create` with `--create-pr`
- TOML config file — set your API key once, never again

---

## Installation

```bash
git clone https://github.com/AyuG619/git-pr-summarizer
cd git-pr-summarizer
python -m venv pr_sum
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e .
```

---

## Setup

```bash
pr-summarize configure
```

This saves your API key to `~/.pr-summarizer.toml`. Run once, works forever.

Get a free Groq key at [console.groq.com](https://console.groq.com)  
Get a Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

---

## Usage

```bash
# Basic run — diffs against last commit
pr-summarize

# Diff against a specific branch
pr-summarize --base main

# Use Gemini instead of Groq
pr-summarize --provider gemini

# Copy output to clipboard
pr-summarize --copy

# Create GitHub PR directly
pr-summarize --create-pr

# Dry run — parse only, no API call
pr-summarize --dry-run --verbose

# Generate a sample CODEOWNERS file
pr-summarize --init-codeowners
```

---

## Example Output


```text
Found 3 changed file(s) — +47 / -12
⚠ Sensitive files: src/auth/login.py
Suggested reviewers: @backend-team

┌─ PR Title ──────────────────────────────────┐
│ Add bcrypt password hashing to login flow   │
└─────────────────────────────────────────────┘

┌─ Summary ───────────────────────────────────┐
│ Replaces plaintext password comparison with │
│ bcrypt hashing. Adds login attempt logging. │
└─────────────────────────────────────────────┘

┌─ Changes ───────────────────────────────────┐
│ • Add bcrypt dependency                     │
│ • Replace check_password with bcrypt.verify │
│ • Add log_attempt() call on login           │
└─────────────────────────────────────────────┘

┌─ Risks ─────────────────────────────────────┐
│ ⚠ Touches authentication logic              │
└─────────────────────────────────────────────┘
```
---

## How it works
```text
git diff
↓
Diff Parser        — splits by file, hunk, +/- lines
↓
Token Chunker      — fits diff into LLM context window (tiktoken)
↓
LLM (Groq/Gemini)  — structured JSON prompt, schema-enforced response
↓
Pydantic Validator — type checking, tense correction, fallback handling
↓
Formatter          — GitHub Markdown, clipboard, gh pr create
```