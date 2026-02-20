"""local_model.py — Ollama-based local model integration for Enki v4.

Provides five prompt operations for note enrichment:
1. Note construction (raw content → keywords + context + tags)
2. Link classification (new note + candidates → link list)
3. Evolution check (new note + existing → should_update + proposed changes)
4. Code extraction (file content → list of extractable items)
5. JSONL extraction (transcript chunk → list of note candidates)

Uses Ollama REST API via httpx. No ollama Python package required.
All prompts produce structured JSON. Retries on parse failure (max 2).
Runs async at session-end — NEVER blocks session-time operations.
"""

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 2


class OllamaUnavailableError(Exception):
    """Raised when Ollama server is not reachable."""


def _generate(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Send a generation request to Ollama and return the response text.

    Raises OllamaUnavailableError if Ollama is not running.
    """
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 2048,
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except httpx.ConnectError:
        raise OllamaUnavailableError("Ollama not running at {OLLAMA_BASE_URL}")
    except httpx.HTTPStatusError as e:
        raise OllamaUnavailableError(f"Ollama error: {e.response.status_code}")


def _parse_json(text: str) -> Any:
    """Extract JSON from model response, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    return json.loads(text)


def _generate_json(prompt: str, model: str = DEFAULT_MODEL) -> Any:
    """Generate and parse JSON response, with retries on parse failure."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            if attempt > 0:
                prompt = (
                    f"{prompt}\n\n"
                    "IMPORTANT: Your response must be valid JSON only. "
                    "No markdown, no explanation, no text before or after the JSON. "
                    "Start with {{ or [."
                )
            raw = _generate(prompt, model)
            return _parse_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            logger.warning(
                "JSON parse failure (attempt %d/%d): %s",
                attempt + 1, MAX_RETRIES + 1, e,
            )
            continue

    raise ValueError(f"Failed to get valid JSON after {MAX_RETRIES + 1} attempts: {last_error}")


def is_available(model: str = DEFAULT_MODEL) -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        response = httpx.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=5.0,
        )
        if response.status_code != 200:
            return False
        tags = response.json().get("models", [])
        return any(model in t.get("name", "") for t in tags)
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


# ---------------------------------------------------------------------------
# Operation 1: Note Construction
# ---------------------------------------------------------------------------

def construct_note(content: str, category: str) -> dict:
    """Enrich raw note content with keywords, context description, and tags.

    Args:
        content: Raw note content.
        category: Note category (decision, learning, pattern, fix, code_knowledge).

    Returns:
        {
            "keywords": "comma,separated,keywords",
            "context_description": "Brief description of when this note is useful",
            "tags": "comma,separated,tags",
            "summary": "One-line summary"
        }
    """
    prompt = f"""Analyze this {category} note and extract structured metadata.

Note content:
---
{content}
---

Return a JSON object with exactly these fields:
- "keywords": comma-separated technical keywords (3-8 keywords)
- "context_description": one sentence describing when this knowledge is useful
- "tags": comma-separated categorization tags (2-5 tags)
- "summary": one-line summary (max 80 chars)

JSON only, no other text:"""

    return _generate_json(prompt)


# ---------------------------------------------------------------------------
# Operation 2: Link Classification
# ---------------------------------------------------------------------------

def classify_links(
    source_content: str,
    source_category: str,
    candidates: list[dict],
) -> list[dict]:
    """Classify relationships between a new note and candidate notes.

    Args:
        source_content: Content of the new note.
        source_category: Category of the new note.
        candidates: List of dicts with note_id, content, category, score.

    Returns:
        List of {"target_id": "...", "target_db": "...", "relationship": "...", "score": float}
    """
    candidate_summaries = []
    for i, c in enumerate(candidates):
        info = _get_candidate_content(c)
        candidate_summaries.append(
            f"[{i}] ID={c['note_id']} DB={c.get('source_db', 'unknown')} "
            f"Content: {info}"
        )

    prompt = f"""Given a new {source_category} note and a list of existing notes,
determine which existing notes should be linked and what relationship type.

New note:
---
{source_content}
---

Existing notes:
{chr(10).join(candidate_summaries)}

Valid relationship types:
- relates_to: general topical relationship
- supersedes: new note replaces/updates the target
- contradicts: new note conflicts with target
- extends: new note adds detail to target
- imports: new note uses concepts from target
- uses: code/implementation relationship
- implements: new note implements a decision/pattern from target

Return a JSON array of links to create. Only include notes with a meaningful relationship.
Each element: {{"target_id": "...", "relationship": "relates_to|supersedes|contradicts|extends|imports|uses|implements"}}

If no links should be created, return an empty array: []

JSON only:"""

    result = _generate_json(prompt)
    if not isinstance(result, list):
        return []

    # Validate and enrich results
    valid = []
    valid_rels = {"relates_to", "supersedes", "contradicts", "extends",
                  "imports", "uses", "implements"}
    candidate_map = {c["note_id"]: c for c in candidates}

    for link in result:
        tid = link.get("target_id", "")
        rel = link.get("relationship", "")
        if tid in candidate_map and rel in valid_rels:
            cand = candidate_map[tid]
            valid.append({
                "target_id": tid,
                "target_db": cand.get("source_db", "wisdom"),
                "relationship": rel,
                "score": cand.get("score", 0.0),
            })

    return valid


def _get_candidate_content(candidate: dict) -> str:
    """Get content string for a candidate, fetching from DB if needed."""
    if "content" in candidate:
        return candidate["content"][:200]

    from enki.db import get_abzu_db, get_wisdom_db

    source_db = candidate.get("source_db", "wisdom")
    if source_db == "wisdom":
        conn = get_wisdom_db()
        table = "notes"
    else:
        conn = get_abzu_db()
        table = "note_candidates"

    try:
        row = conn.execute(
            f"SELECT content FROM {table} WHERE id = ?",
            (candidate["note_id"],),
        ).fetchone()
        return row["content"][:200] if row else "[content not found]"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Operation 3: Evolution Check
# ---------------------------------------------------------------------------

def check_evolution(
    new_content: str,
    new_category: str,
    new_keywords: Optional[str],
    target_content: str,
    target_category: str,
    target_keywords: Optional[str],
) -> Optional[dict]:
    """Check if a new note should trigger evolution of an existing note.

    Returns:
        Dict with proposed changes, or None if no evolution needed.
        {"proposed_context": "...", "proposed_keywords": "...", "proposed_tags": "..."}
    """
    prompt = f"""Compare these two notes and determine if the existing note's metadata
should be updated based on the new note's information.

New note ({new_category}):
Content: {new_content}
Keywords: {new_keywords or 'none'}

Existing note ({target_category}):
Content: {target_content}
Keywords: {target_keywords or 'none'}

Rules:
- The content field NEVER changes
- Only context_description, keywords, and tags can be updated
- Only propose changes if the new note adds meaningful context

Return a JSON object:
- If evolution needed: {{"should_update": true, "proposed_keywords": "merged,keywords", "proposed_context": "updated context description", "proposed_tags": "updated,tags"}}
- If no evolution needed: {{"should_update": false}}

JSON only:"""

    result = _generate_json(prompt)
    if not isinstance(result, dict) or not result.get("should_update"):
        return None

    proposed = {}
    if result.get("proposed_context"):
        proposed["proposed_context"] = result["proposed_context"]
    if result.get("proposed_keywords"):
        proposed["proposed_keywords"] = result["proposed_keywords"]
    if result.get("proposed_tags"):
        proposed["proposed_tags"] = result["proposed_tags"]

    return proposed if proposed else None


# ---------------------------------------------------------------------------
# Operation 4: Code Extraction
# ---------------------------------------------------------------------------

def extract_code_knowledge(file_content: str, file_path: str) -> list[dict]:
    """Extract code knowledge items from a file.

    Args:
        file_content: Content of the source file.
        file_path: Path to the file (for context).

    Returns:
        List of extractable items:
        [{"content": "...", "category": "code_knowledge", "keywords": "...", "summary": "..."}]
    """
    prompt = f"""Analyze this source file and extract important code knowledge.

File: {file_path}
---
{file_content[:4000]}
---

Extract items like:
- Key architectural decisions visible in the code
- Important patterns or conventions used
- Non-obvious design choices that future developers should know
- Critical configuration or setup requirements

Return a JSON array of extracted items.
Each element: {{"content": "description of the knowledge", "keywords": "relevant,keywords", "summary": "one-line summary"}}

If nothing notable, return an empty array: []

JSON only:"""

    result = _generate_json(prompt)
    if not isinstance(result, list):
        return []

    items = []
    for item in result:
        if isinstance(item, dict) and item.get("content"):
            items.append({
                "content": item["content"],
                "category": "code_knowledge",
                "keywords": item.get("keywords", ""),
                "summary": item.get("summary", ""),
                "file_ref": file_path,
            })

    return items


# ---------------------------------------------------------------------------
# Operation 5: JSONL Extraction
# ---------------------------------------------------------------------------

def extract_from_transcript(transcript_chunk: str, project: str = None) -> list[dict]:
    """Extract note candidates from a conversation transcript chunk.

    Args:
        transcript_chunk: Section of JSONL conversation transcript.
        project: Optional project context.

    Returns:
        List of note candidates:
        [{"content": "...", "category": "decision|learning|pattern|fix", "keywords": "...", "summary": "..."}]
    """
    project_ctx = f" for project '{project}'" if project else ""
    prompt = f"""Analyze this conversation transcript{project_ctx} and extract
noteworthy items that should be remembered for future sessions.

Transcript:
---
{transcript_chunk[:4000]}
---

Extract items like:
- Decisions made (category: "decision")
- Things learned (category: "learning")
- Patterns identified (category: "pattern")
- Bug fixes and their root causes (category: "fix")

Do NOT extract:
- Routine operations (file reads, simple edits)
- Temporary context (current task status)
- Already well-known information

Return a JSON array of extracted items.
Each element: {{"content": "what was decided/learned/found", "category": "decision|learning|pattern|fix", "keywords": "relevant,keywords", "summary": "one-line summary"}}

If nothing noteworthy, return an empty array: []

JSON only:"""

    result = _generate_json(prompt)
    if not isinstance(result, list):
        return []

    valid_categories = {"decision", "learning", "pattern", "fix"}
    items = []
    for item in result:
        if isinstance(item, dict) and item.get("content"):
            cat = item.get("category", "learning")
            if cat not in valid_categories:
                cat = "learning"
            items.append({
                "content": item["content"],
                "category": cat,
                "keywords": item.get("keywords", ""),
                "summary": item.get("summary", ""),
            })

    return items
