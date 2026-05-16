# src/chunker.py
import tiktoken
from src.parser import FileDiff
from src.config import MAX_CHUNK_TOKENS


def count_tokens(text: str) -> int:
    """
    Counts how many tokens a string uses.
    We use cl100k_base encoding — this is what GPT-4 and Claude both use approximately.
    Tokens are NOT the same as words. "authentication" is 1 token, but
    a code line like "    return jsonify({'error': 'unauthorized'})" is ~12 tokens.
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


def file_to_text(file: FileDiff) -> str:
    """
    Converts a FileDiff object into a plain text representation
    that we'll send to the LLM. We format it so it's easy for the
    model to understand what changed and where.
    """
    lines = []
    lines.append(f"File: {file.filename} ({file.change_type})")
    lines.append(f"Changes: +{file.added_count} lines, -{file.removed_count} lines")

    if file.is_risky:
        lines.append("⚠️  This file may contain sensitive logic (auth/DB/payments).")

    for i, hunk in enumerate(file.hunks):
        lines.append(f"\n--- Hunk {i + 1} (starting at line {hunk.new_start}) ---")

        for removed in hunk.removed_lines:
            lines.append(f"- {removed}")
        for added in hunk.added_lines:
            lines.append(f"+ {added}")

    return "\n".join(lines)


def chunk_files(files: list[FileDiff]) -> list[str]:
    """
    Groups files into chunks, where each chunk fits within MAX_CHUNK_TOKENS.

    Strategy: we go file by file. If adding the next file would exceed the
    token limit, we start a new chunk. This keeps related changes (within
    one file) always together in the same chunk.

    Returns a list of strings — each string is one chunk ready to send to the LLM.
    """
    chunks: list[str] = []
    current_chunk_parts: list[str] = []
    current_token_count = 0

    for file in files:
        file_text = file_to_text(file)
        file_tokens = count_tokens(file_text)

        # If this single file is already bigger than our limit,
        # we still send it — just truncate it with a warning.
        if file_tokens > MAX_CHUNK_TOKENS:
            # Truncate to roughly the right number of characters
            # (rough ratio: 1 token ≈ 4 characters for code)
            max_chars = MAX_CHUNK_TOKENS * 4
            file_text = file_text[:max_chars] + "\n\n[...diff truncated — file too large]"
            file_tokens = MAX_CHUNK_TOKENS

        # If adding this file would overflow the current chunk, save the chunk and start fresh
        if current_token_count + file_tokens > MAX_CHUNK_TOKENS and current_chunk_parts:
            chunks.append("\n\n".join(current_chunk_parts))
            current_chunk_parts = []
            current_token_count = 0

        current_chunk_parts.append(file_text)
        current_token_count += file_tokens

    # Don't forget the last chunk
    if current_chunk_parts:
        chunks.append("\n\n".join(current_chunk_parts))

    return chunks