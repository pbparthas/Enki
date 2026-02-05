#!/usr/bin/env python3
"""
Enki Transcript Extractor

Reads Claude Code's .jsonl transcript and produces a deterministic digest
for pre-compact context preservation.

DESIGN RULES (do not modify):
  1. All extraction is mechanical: regex, counts, paths. No scoring.
  2. No field accepts free-text AI-written content. Every field is derived
     from parsed transcript data only.
  3. Output format is fixed. Adding/removing sections requires spec change.
  4. No "importance" ranking. Items appear in chronological order, capped.
  5. No filtering by "relevance" — all matched items are included up to cap.

These rules exist because the digest must be deterministic and reproducible.
Same transcript in → same digest out. Always.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional


# =============================================================================
# CONSTANTS — Do not make configurable
# =============================================================================

MAX_DECISIONS = 7
MAX_FILES = 20
MAX_ERRORS = 5
MAX_OPEN_THREADS = 5
MAX_SUMMARY_CHARS = 300
MAX_STATE_CHARS = 250

# Decision patterns — match what CC actually writes in tool descriptions
# These are structural markers in assistant messages, not subjective filters
DECISION_PATTERNS = [
    re.compile(r"(?:I'll|I will|Let me|Going to|We should|We need to)\s+(.{10,120})", re.IGNORECASE),
    re.compile(r"(?:The fix is|The solution is|The approach is|The issue is|The root cause is)\s+(.{10,120})", re.IGNORECASE),
    re.compile(r"(?:Instead of|Rather than)\s+(.{10,120})", re.IGNORECASE),
    re.compile(r"(?:Changed|Switched|Moved|Replaced|Refactored)\s+(.{10,120})", re.IGNORECASE),
]

# Error patterns
ERROR_PATTERNS = [
    re.compile(r"(?:Error|ERROR|Exception|FAILED|Failed|Traceback)[\s:]+(.{10,150})", re.IGNORECASE),
    re.compile(r"(?:command not found|No such file|Permission denied|ENOENT|EACCES)(.{0,100})", re.IGNORECASE),
]

# Open thread patterns — things that signal unfinished work
OPEN_THREAD_PATTERNS = [
    re.compile(r"(?:TODO|FIXME|HACK|XXX)[\s:]+(.{5,120})", re.IGNORECASE),
    re.compile(r"(?:still need to|haven't yet|not yet|remaining|left to do)\s+(.{5,120})", re.IGNORECASE),
    re.compile(r"(?:next step|next steps|after that|then we)\s+(.{5,120})", re.IGNORECASE),
]

# Tool names that indicate file modifications
FILE_MOD_TOOLS = {"Edit", "Write", "MultiEdit", "create_file", "str_replace"}


# =============================================================================
# PARSER
# =============================================================================

def parse_transcript(path: str) -> list[dict]:
    """
    Parse JSONL transcript into list of message dicts.

    Each line is one JSON object. We extract role, content, tool info.
    Malformed lines are silently skipped (transcript may be mid-write).

    Claude Code's JSONL uses two formats:
      1. API format: {"role": "user", "content": "..."}
      2. CC wrapper: {"type": "user", "message": {"role": "user", "content": "..."}}

    We normalize both to {"role": ..., "content": ...} for downstream extractors.
    """
    messages = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # CC wrapper format: unwrap .message if present
                if "message" in raw and isinstance(raw["message"], dict):
                    msg = raw["message"]
                    # Only include user/assistant messages
                    if msg.get("role") in ("user", "assistant"):
                        messages.append(msg)
                elif raw.get("role") in ("user", "assistant"):
                    # Already in API format
                    messages.append(raw)
    except (FileNotFoundError, PermissionError):
        return []
    return messages


# =============================================================================
# EXTRACTORS — Each returns raw data, no judgment
# =============================================================================

def extract_user_messages(messages: list[dict]) -> list[str]:
    """Extract all user message texts, in order."""
    texts = []
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
    return texts


def extract_assistant_texts(messages: list[dict]) -> list[str]:
    """Extract all assistant text blocks, in order."""
    texts = []
    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
    return texts


def extract_tool_uses(messages: list[dict]) -> list[dict]:
    """Extract all tool_use blocks with name and input."""
    tools = []
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append({
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })
    return tools


def extract_tool_results(messages: list[dict]) -> list[dict]:
    """Extract all tool_result blocks."""
    results = []
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results.append({
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                        "is_error": block.get("is_error", False),
                    })
    return results


def extract_modified_files(tool_uses: list[dict]) -> list[str]:
    """
    Extract file paths from file-modifying tool calls.
    Returns unique paths in order of first appearance, shortened.
    """
    seen = set()
    files = []
    for tool in tool_uses:
        if tool["name"] in FILE_MOD_TOOLS:
            inp = tool["input"]
            path = inp.get("file_path") or inp.get("path") or ""
            if path and path not in seen:
                seen.add(path)
                # Shorten home paths
                short = path.replace("/home/partha/", "~/")
                files.append(short)
    return files[:MAX_FILES]


def extract_decisions(assistant_texts: list[str]) -> list[str]:
    """
    Extract decision statements from assistant messages.

    Uses regex patterns only. No scoring, no filtering by "importance".
    Chronological order, capped at MAX_DECISIONS.
    """
    decisions = []
    seen_normalized = set()
    for text in assistant_texts:
        for pattern in DECISION_PATTERNS:
            for match in pattern.finditer(text):
                decision = match.group(0).strip()
                # Remove trailing incomplete sentences
                decision = re.sub(r"\s+\S*$", "", decision) if len(decision) > 100 else decision
                # Deduplicate by normalized form
                normalized = decision.lower()[:50]
                if normalized not in seen_normalized:
                    seen_normalized.add(normalized)
                    decisions.append(decision)
                    if len(decisions) >= MAX_DECISIONS:
                        return decisions
    return decisions


def extract_errors(assistant_texts: list[str], tool_results: list[dict]) -> list[str]:
    """
    Extract error messages from assistant text and tool results.
    """
    errors = []
    seen = set()

    # From tool results flagged as errors
    for result in tool_results:
        if result.get("is_error"):
            content = result.get("content", "")
            if isinstance(content, str):
                short = content[:150].strip()
            elif isinstance(content, list):
                short = str(content[0].get("text", ""))[:150].strip() if content else ""
            else:
                short = str(content)[:150].strip()
            norm = short.lower()[:60]
            if norm and norm not in seen:
                seen.add(norm)
                errors.append(short)

    # From assistant texts
    for text in assistant_texts:
        for pattern in ERROR_PATTERNS:
            for match in pattern.finditer(text):
                error = match.group(0).strip()[:150]
                norm = error.lower()[:60]
                if norm not in seen:
                    seen.add(norm)
                    errors.append(error)

    return errors[:MAX_ERRORS]


def extract_open_threads(assistant_texts: list[str], user_texts: list[str]) -> list[str]:
    """
    Extract unfinished work / open threads.

    Looks at the LAST 30% of assistant messages only (open threads
    from the beginning were likely resolved).
    """
    threads = []
    seen = set()

    # Only look at recent assistant messages
    cutoff = max(1, len(assistant_texts) - max(3, len(assistant_texts) // 3))
    recent = assistant_texts[cutoff:]

    for text in recent:
        for pattern in OPEN_THREAD_PATTERNS:
            for match in pattern.finditer(text):
                thread = match.group(0).strip()[:120]
                norm = thread.lower()[:50]
                if norm not in seen:
                    seen.add(norm)
                    threads.append(thread)

    # Check last user message for unanswered requests
    if user_texts:
        last_user = user_texts[-1]
        if last_user.endswith("?") or any(kw in last_user.lower() for kw in ["can you", "please", "could you"]):
            # If conversation ends with user question, flag it
            short = last_user[:120].strip()
            if short not in seen:
                threads.append(f"[Unanswered] {short}")

    return threads[:MAX_OPEN_THREADS]


def build_work_summary(
    user_texts: list[str],
    files: list[str],
    tool_uses: list[dict],
) -> str:
    """
    Build a factual 1-3 sentence summary.

    NOT a generated summary. This is mechanical:
    - First user message (truncated) = what was asked
    - File count = scale
    - Tool usage distribution = what kind of work
    """
    # What was asked
    first_ask = user_texts[0][:MAX_SUMMARY_CHARS].strip() if user_texts else "No user messages found"
    # Remove newlines for clean single-line
    first_ask = re.sub(r"\s+", " ", first_ask)

    # Scale
    file_count = len(files)
    tool_count = len(tool_uses)

    # Tool distribution
    tool_names = {}
    for t in tool_uses:
        name = t["name"]
        tool_names[name] = tool_names.get(name, 0) + 1
    top_tools = sorted(tool_names.items(), key=lambda x: -x[1])[:4]
    tool_summary = ", ".join(f"{name}×{count}" for name, count in top_tools)

    parts = [f"Request: {first_ask}"]
    if file_count:
        parts.append(f"Modified {file_count} file(s).")
    if tool_summary:
        parts.append(f"Tool usage: {tool_summary}.")

    return " ".join(parts)


def build_current_state(assistant_texts: list[str], tool_uses: list[dict]) -> str:
    """
    Build current state from last meaningful assistant message.

    Takes the last assistant text that's >50 chars (skip short acks).
    Truncates to MAX_STATE_CHARS.
    """
    # Find last substantive assistant message
    last_meaningful = ""
    for text in reversed(assistant_texts):
        stripped = text.strip()
        if len(stripped) > 50:
            last_meaningful = stripped
            break

    if not last_meaningful:
        return "Session ended without substantive final message."

    # Take the last paragraph or sentence
    paragraphs = last_meaningful.split("\n\n")
    last_para = paragraphs[-1].strip() if paragraphs else last_meaningful

    if len(last_para) > MAX_STATE_CHARS:
        last_para = last_para[:MAX_STATE_CHARS].rsplit(" ", 1)[0] + "..."

    return last_para


# =============================================================================
# DIGEST BUILDER — Fixed format, not modifiable at runtime
# =============================================================================

def build_digest(transcript_path: str, enki_dir: Optional[str] = None) -> str:
    """
    Build the complete pre-compact digest.

    Output format is FIXED. Do not add conditional sections or
    skip sections based on content. Empty sections get "(none)" marker.

    Args:
        transcript_path: Path to .jsonl transcript file
        enki_dir: Path to .enki/ directory (for phase/goal/tier)

    Returns:
        Markdown-formatted digest string
    """
    messages = parse_transcript(transcript_path)
    if not messages:
        return "# Pre-Compact Digest\n\nNo transcript data available.\n"

    # Extract raw data
    user_texts = extract_user_messages(messages)
    assistant_texts = extract_assistant_texts(messages)
    tool_uses = extract_tool_uses(messages)
    tool_results = extract_tool_results(messages)

    # Derive digest sections
    files = extract_modified_files(tool_uses)
    decisions = extract_decisions(assistant_texts)
    errors = extract_errors(assistant_texts, tool_results)
    open_threads = extract_open_threads(assistant_texts, user_texts)
    work_summary = build_work_summary(user_texts, files, tool_uses)
    current_state = build_current_state(assistant_texts, tool_uses)

    # Read .enki state if available
    phase = _read_file(enki_dir, "PHASE", "intake")
    goal = _read_file(enki_dir, "GOAL", "")
    tier = _read_file(enki_dir, "TIER", "unknown")

    # === BUILD OUTPUT (fixed format) ===
    lines = []
    lines.append("# Pre-Compact Digest")
    lines.append("")

    # Session state
    lines.append(f"**Phase**: {phase} | **Tier**: {tier}")
    if goal:
        lines.append(f"**Goal**: {goal}")
    lines.append("")

    # Work summary
    lines.append("## Work Summary")
    lines.append(work_summary)
    lines.append("")

    # Decisions
    lines.append("## Decisions Made")
    if decisions:
        for d in decisions:
            lines.append(f"- {d}")
    else:
        lines.append("(none)")
    lines.append("")

    # Files modified
    lines.append("## Files Modified")
    if files:
        for f in files:
            lines.append(f"- `{f}`")
    else:
        lines.append("(none)")
    lines.append("")

    # Errors encountered
    lines.append("## Problems Encountered")
    if errors:
        for e in errors:
            lines.append(f"- {e}")
    else:
        lines.append("(none)")
    lines.append("")

    # Open threads
    lines.append("## Open Threads")
    if open_threads:
        for t in open_threads:
            lines.append(f"- {t}")
    else:
        lines.append("(none)")
    lines.append("")

    # Current state
    lines.append("## Current State")
    lines.append(current_state)
    lines.append("")

    # Stats (for auditing)
    lines.append("---")
    lines.append(f"*Digest: {len(messages)} messages, {len(tool_uses)} tool calls, {len(files)} files modified*")

    return "\n".join(lines)


def _read_file(directory: Optional[str], filename: str, default: str) -> str:
    """Read a single-line file from a directory, with default."""
    if not directory:
        return default
    try:
        return Path(directory, filename).read_text().strip() or default
    except (FileNotFoundError, PermissionError):
        return default


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: transcript.py <transcript_path> [enki_dir]", file=sys.stderr)
        sys.exit(1)

    transcript_path = sys.argv[1]
    enki_dir = sys.argv[2] if len(sys.argv) > 2 else None

    digest = build_digest(transcript_path, enki_dir)
    print(digest)
