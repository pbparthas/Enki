"""extraction.py — Heuristic JSONL extraction + versioning.

Extracts bead candidates from CC's JSONL transcripts.
Falls back gracefully if format is unrecognized.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from enki.db import abzu_db

JSONL_FORMAT_VERSION = "1.0"

# Expected JSONL structure (Claude Code format as of 2025-02)
EXPECTED_KEYS = {"type", "message", "timestamp"}
EXPECTED_MESSAGE_TYPES = {"human", "assistant", "tool_use", "tool_result"}

# Heuristic patterns for bead candidate extraction
DECISION_PATTERNS = [
    re.compile(r"(?:decided|choosing|going with|picked|selected)\s+(.+?)(?:\.|$)", re.I),
    re.compile(r"(?:approach|strategy|architecture):\s*(.+?)(?:\.|$)", re.I),
]

LEARNING_PATTERNS = [
    re.compile(r"(?:learned|discovered|realized|found out)\s+(?:that\s+)?(.+?)(?:\.|$)", re.I),
    re.compile(r"(?:turns out|it appears|apparently)\s+(.+?)(?:\.|$)", re.I),
]

FIX_PATTERNS = [
    re.compile(r"(?:fixed|resolved|solved)\s+(?:by\s+)?(.+?)(?:\.|$)", re.I),
    re.compile(r"(?:root cause|the issue was|the problem was)\s+(.+?)(?:\.|$)", re.I),
]

PATTERN_PATTERNS = [
    re.compile(r"(?:pattern|convention|standard|always use)\s+(.+?)(?:\.|$)", re.I),
]

# Error/exception patterns (Abzu Spec §6)
ERROR_PATTERNS = [
    re.compile(r"(?:error|exception|traceback|failure):\s*(.+?)(?:\n|$)", re.I),
    re.compile(r"(?:TypeError|ValueError|KeyError|AttributeError|ImportError|RuntimeError):\s*(.+?)(?:\n|$)"),
    re.compile(r"(?:failed|broke|crashed)\s+(?:because|due to|when)\s+(.+?)(?:\.|$)", re.I),
]

# File modification patterns (Abzu Spec §6)
FILE_PATTERNS = [
    re.compile(r"(?:created|modified|edited|updated|wrote|changed)\s+(?:file\s+)?[`'\"]?(\S+\.\w+)[`'\"]?", re.I),
    re.compile(r"(?:in|to)\s+[`'\"]?(\S+\.\w{1,5})[`'\"]?\s*(?::|,|\.|$)"),
]

# Task completion patterns (Abzu Spec §6)
TASK_COMPLETION_PATTERNS = [
    re.compile(r"(?:completed|finished|done with|implemented|shipped)\s+(.+?)(?:\.|$)", re.I),
    re.compile(r"(?:I'll|I will|going to|let me)\s+(.+?)(?:\.|$)", re.I),
    re.compile(r"(?:changed|switched|migrated)\s+(?:from\s+\S+\s+)?to\s+(.+?)(?:\.|$)", re.I),
]


def validate_jsonl_format(jsonl_path: str) -> bool:
    """Check if JSONL matches expected format before extraction.

    Returns True if format matches, False if unrecognized.
    On False: log warning, skip heuristic extraction,
    fall back to CC distillation only.
    """
    try:
        with open(jsonl_path) as f:
            first_line = f.readline()
            if not first_line:
                return False
            entry = json.loads(first_line)

            if not EXPECTED_KEYS.issubset(entry.keys()):
                _log_format_mismatch(jsonl_path, list(entry.keys()))
                return False

            return True
    except (json.JSONDecodeError, IOError):
        return False


def extract_from_jsonl(jsonl_path: str, session_id: str) -> list[dict]:
    """Heuristic extraction from JSONL transcript.

    Returns list of bead candidate dicts.
    If format is unrecognized, returns empty list (falls back to CC distillation).
    """
    if not validate_jsonl_format(jsonl_path):
        return []

    candidates = []
    try:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message", "")
                if isinstance(message, dict):
                    message = message.get("content", "")
                if not isinstance(message, str):
                    continue

                # Try each pattern category
                for pattern in DECISION_PATTERNS:
                    match = pattern.search(message)
                    if match:
                        candidates.append({
                            "content": match.group(0).strip(),
                            "category": "decision",
                            "source": "heuristic",
                            "session_id": session_id,
                        })

                for pattern in LEARNING_PATTERNS:
                    match = pattern.search(message)
                    if match:
                        candidates.append({
                            "content": match.group(0).strip(),
                            "category": "learning",
                            "source": "heuristic",
                            "session_id": session_id,
                        })

                for pattern in FIX_PATTERNS:
                    match = pattern.search(message)
                    if match:
                        candidates.append({
                            "content": match.group(0).strip(),
                            "category": "fix",
                            "source": "heuristic",
                            "session_id": session_id,
                        })

                for pattern in PATTERN_PATTERNS:
                    match = pattern.search(message)
                    if match:
                        candidates.append({
                            "content": match.group(0).strip(),
                            "category": "pattern",
                            "source": "heuristic",
                            "session_id": session_id,
                        })

    except IOError:
        return []

    # Log extraction
    _log_extraction(session_id, jsonl_path, len(candidates), "heuristic")

    return candidates


def extract_decisions(text: str) -> list[str]:
    """Extract decision statements from text (Abzu Spec §6).

    Patterns: "I'll...", "Changed...", "Decided...", "Going with..."
    Returns list of extracted decision strings.
    """
    results = []
    for pattern in DECISION_PATTERNS:
        for match in pattern.finditer(text):
            content = match.group(0).strip()
            if len(content) > 10:  # Skip trivial matches
                results.append(content)
    return results


def extract_errors(text: str) -> list[str]:
    """Extract error/exception mentions from text (Abzu Spec §6).

    Patterns: exceptions, tracebacks, "failed because..."
    Returns list of extracted error strings.
    """
    results = []
    for pattern in ERROR_PATTERNS:
        for match in pattern.finditer(text):
            content = match.group(0).strip()
            if len(content) > 5:
                results.append(content)
    return results


def extract_files(text: str) -> list[str]:
    """Extract file paths from text (Abzu Spec §6).

    Patterns: "modified src/foo.py", "in config.yaml"
    Returns deduplicated list of file paths.
    """
    results = set()
    for pattern in FILE_PATTERNS:
        for match in pattern.finditer(text):
            filepath = match.group(1).strip()
            # Filter out common false positives
            if not filepath.startswith(("http", "www", "//")):
                results.add(filepath)
    return list(results)


def extract_task_completions(text: str) -> list[str]:
    """Extract task completion statements from text (Abzu Spec §6).

    Patterns: "completed...", "finished...", "implemented..."
    Returns list of extracted completion strings.
    """
    results = []
    for pattern in TASK_COMPLETION_PATTERNS:
        for match in pattern.finditer(text):
            content = match.group(0).strip()
            if len(content) > 10:
                results.append(content)
    return results


def extract_all_from_text(text: str, session_id: str) -> list[dict]:
    """Run all extractors on text, returning categorized candidates.

    Combines decisions, errors (→ fix category), and task completions (→ learning).
    """
    candidates = []

    for content in extract_decisions(text):
        candidates.append({
            "content": content,
            "category": "decision",
            "source": "heuristic",
            "session_id": session_id,
        })

    for content in extract_errors(text):
        candidates.append({
            "content": content,
            "category": "fix",
            "source": "heuristic",
            "session_id": session_id,
        })

    for content in extract_task_completions(text):
        candidates.append({
            "content": content,
            "category": "learning",
            "source": "heuristic",
            "session_id": session_id,
        })

    return candidates


def extract_from_text(text: str, session_id: str) -> list[dict]:
    """Heuristic extraction from plain text (e.g., CC distillation output)."""
    candidates = []

    all_patterns = [
        (DECISION_PATTERNS, "decision"),
        (LEARNING_PATTERNS, "learning"),
        (FIX_PATTERNS, "fix"),
        (PATTERN_PATTERNS, "pattern"),
    ]

    for patterns, category in all_patterns:
        for pattern in patterns:
            for match in pattern.finditer(text):
                candidates.append({
                    "content": match.group(0).strip(),
                    "category": category,
                    "source": "cc_distillation",
                    "session_id": session_id,
                })

    return candidates


def extract_candidates(text: str, session_id: str) -> list[dict]:
    """Extract bead candidates from text for session end staging."""
    return extract_all_from_text(text, session_id)


def extract_operational_state(transcript_path: str) -> dict:
    """Extract operational state from a JSONL transcript for pre-compact summaries.

    Parses from END of file (most recent first), max 100 lines.
    Extracts files modified, errors, user messages, tool call count,
    assistant summary, and task completions.

    Returns empty dict with empty lists on missing/malformed file.
    """
    empty = {
        "files_modified": [],
        "tasks_completed": [],
        "errors": [],
        "user_messages": [],
        "assistant_summary": "",
        "tool_calls_count": 0,
    }

    path = Path(transcript_path)
    if not path.exists():
        return empty

    # Read last 100 lines from file
    try:
        with open(path) as f:
            all_lines = f.readlines()
    except (IOError, OSError):
        return empty

    tail_lines = all_lines[-100:] if len(all_lines) > 100 else all_lines

    files_modified = set()
    errors = []
    user_messages = []
    assistant_texts = []
    tool_calls_count = 0
    tasks_completed = []

    for raw_line in tail_lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            continue

        entry_type = entry.get("type", "")

        # Human messages
        if entry_type == "human":
            content = entry.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        user_messages.append(block["text"])
            elif isinstance(content, str) and content:
                user_messages.append(content)

        # Tool use entries — extract file paths and count
        elif entry_type == "tool_use":
            tool_calls_count += 1
            name = entry.get("name", "")
            inp = entry.get("input", {})
            if not isinstance(inp, dict):
                continue

            if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                fp = inp.get("file_path") or inp.get("path", "")
                if fp:
                    files_modified.add(fp)
            elif name == "Bash":
                cmd = inp.get("command", "")
                # Extract task completions from common commands
                for pat in TASK_COMPLETION_PATTERNS:
                    m = pat.search(cmd)
                    if m:
                        tasks_completed.append(m.group(0).strip())

        # Tool results — extract errors
        elif entry_type == "tool_result":
            content = entry.get("content", "")
            if isinstance(content, str):
                content_lower = content.lower()
                if any(kw in content_lower for kw in
                       ("error", "traceback", "exception", "failed", "typeerror",
                        "valueerror", "keyerror", "attributeerror", "importerror")):
                    # Take first meaningful line as error summary
                    for line in content.split("\n"):
                        line = line.strip()
                        if line and any(kw in line.lower() for kw in
                                        ("error", "traceback", "exception", "failed",
                                         "typeerror", "valueerror", "keyerror")):
                            errors.append(line[:300])
                            break

        # Assistant messages — collect for summary
        elif entry_type == "assistant":
            content = entry.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        assistant_texts.append(block["text"])
            elif isinstance(content, str) and content:
                assistant_texts.append(content)

    # Build assistant summary from most recent assistant text
    assistant_summary = ""
    if assistant_texts:
        last_text = assistant_texts[-1]
        # Take first 300 chars as summary
        assistant_summary = last_text[:300].strip()

    return {
        "files_modified": sorted(files_modified),
        "tasks_completed": tasks_completed[:10],
        "errors": errors[:10],
        "user_messages": user_messages[-5:],  # Last 5 user messages
        "assistant_summary": assistant_summary,
        "tool_calls_count": tool_calls_count,
    }


# ── Private helpers ──


def _log_format_mismatch(jsonl_path: str, found_keys: list[str]) -> None:
    """Log a format mismatch for debugging."""
    log_id = str(uuid.uuid4())
    try:
        with abzu_db() as conn:
            conn.execute(
                "INSERT INTO extraction_log "
                "(id, session_id, jsonl_path, method, candidates_created) "
                "VALUES (?, 'format_check', ?, 'format_mismatch', 0)",
                (log_id, jsonl_path),
            )
    except Exception:
        pass


def _log_extraction(
    session_id: str, jsonl_path: str, count: int, method: str
) -> None:
    """Log extraction run."""
    log_id = str(uuid.uuid4())
    try:
        with abzu_db() as conn:
            conn.execute(
                "INSERT INTO extraction_log "
                "(id, session_id, jsonl_path, candidates_created, method) "
                "VALUES (?, ?, ?, ?, ?)",
                (log_id, session_id, jsonl_path, count, method),
            )
    except Exception:
        pass
