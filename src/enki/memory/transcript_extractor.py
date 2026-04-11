"""transcript_extractor.py — Extract knowledge from CC session JSONL.

Two-model pipeline:
1. Gemini Flash: extract decisions/learnings/patterns with rationale
2. Ollama (local): enrich with keywords, tags, context description

Falls back gracefully if either model unavailable.
Processes in chunks to avoid timeout — never processes full transcript.

JSONL format: each line is a CC conversation turn with 'role' and 'content'.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK_SIZE_TURNS = 40       # process 40 turns at a time
MAX_CHARS_PER_CHUNK = 4000  # hard cap per chunk to Gemini
TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"


def _find_session_jsonl(session_id: str, project: str | None = None) -> Path | None:
    """Find the JSONL file for a session ID."""
    if not session_id or not TRANSCRIPT_ROOT.exists():
        return None

    # Search project-specific directory first
    if project:
        for project_dir in TRANSCRIPT_ROOT.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.rglob("*.jsonl"):
                if session_id in jsonl_file.stem:
                    return jsonl_file

    # Fallback: search all project dirs
    for project_dir in TRANSCRIPT_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.rglob("*.jsonl"):
            if session_id in jsonl_file.stem:
                return jsonl_file

    return None


def _read_transcript_turns(jsonl_path: Path) -> list[dict]:
    """Read JSONL transcript into list of turns."""
    turns = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    turn = json.loads(line)
                    if isinstance(turn, dict):
                        turns.append(turn)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Failed to read transcript %s: %s", jsonl_path, e)
    return turns


def _turns_to_text(turns: list[dict]) -> str:
    """Convert turns to readable text for extraction."""
    lines = []
    for turn in turns:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        pass
                else:
                    texts.append(str(block))
            content = " ".join(texts)
        if content and str(content).strip():
            prefix = "Human" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {str(content)[:500]}")
    return "\n".join(lines)


def _chunk_turns(turns: list[dict]) -> list[tuple[int, list[dict]]]:
    """Split turns into chunks. Returns (chunk_index, turns) pairs."""
    chunks = []
    for i in range(0, len(turns), CHUNK_SIZE_TURNS):
        chunk = turns[i:i + CHUNK_SIZE_TURNS]
        if chunk:
            chunks.append((i // CHUNK_SIZE_TURNS, chunk))
    return chunks


def extract_from_session(
    session_id: str,
    project: str | None = None,
) -> list[dict]:
    """Extract knowledge candidates from a session's JSONL transcript.

    Returns list of candidate dicts ready for staging.add_candidate.
    Silently returns [] on any failure — never blocks session end.
    """
    from enki.integrations.gemini import is_configured, extract_from_transcript_chunk
    from enki.local_model import construct_note, is_available as ollama_available

    if not is_configured():
        logger.info(
            "Gemini not configured — skipping transcript extraction. "
            "Add gemini_api_key to ~/.enki/config.json to enable."
        )
        return []

    jsonl_path = _find_session_jsonl(session_id, project)
    if not jsonl_path:
        logger.debug("No JSONL found for session %s", session_id[:8])
        return []

    turns = _read_transcript_turns(jsonl_path)
    if not turns:
        return []

    chunks = _chunk_turns(turns)
    all_candidates = []
    consecutive_gemini_errors = 0

    for chunk_index, chunk_turns in chunks:
        chunk_text = _turns_to_text(chunk_turns)
        if len(chunk_text) < 100:
            continue

        chunk_text = chunk_text[:MAX_CHARS_PER_CHUNK]

        try:
            items = extract_from_transcript_chunk(
                chunk=chunk_text,
                project=project,
                chunk_index=chunk_index,
            )
            consecutive_gemini_errors = 0
            for item in items:
                category = item.get("category", "learning")
                if category == "constraint":
                    category = "learning"
                item["category"] = category
                item["source_session"] = session_id
                item["source_chunk_index"] = chunk_index
                all_candidates.append(item)
        except Exception as e:
            consecutive_gemini_errors += 1
            logger.warning(
                "Gemini extraction failed on chunk %d: %s", chunk_index, e
            )
            if consecutive_gemini_errors >= 3:
                logger.error(
                    "3 consecutive Gemini errors — stopping extraction"
                )
                break

    # Ollama enrichment pass: add keywords/tags to each candidate
    if ollama_available() and all_candidates:
        for candidate in all_candidates:
            try:
                enriched = construct_note(
                    candidate["content"],
                    candidate["category"],
                )
                if isinstance(enriched, dict):
                    if enriched.get("keywords"):
                        candidate.setdefault("keywords", enriched["keywords"])
                    if enriched.get("summary") and not candidate.get("summary"):
                        candidate["summary"] = enriched["summary"]
            except Exception:
                pass

    logger.info(
        "Transcript extraction complete: %d candidates from %d chunks "
        "(session %s)",
        len(all_candidates), len(chunks), session_id[:8],
    )
    return all_candidates
