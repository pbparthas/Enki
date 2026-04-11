"""gemini.py — Gemini Flash API client for transcript extraction.

Uses stdlib urllib only — no new dependencies.
Independent model layer for transcript extraction.
Graceful degradation: if not configured, falls back to Ollama.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ENKI_ROOT = Path(os.environ.get("ENKI_ROOT", Path.home() / ".enki"))
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


def _get_api_key() -> str | None:
    try:
        config = json.loads((ENKI_ROOT / "config.json").read_text())
        key = config.get("gemini_api_key", "")
        return key if key and key != "YOUR_GEMINI_API_KEY" else None
    except Exception:
        return None


def is_configured() -> bool:
    return _get_api_key() is not None


def call_gemini(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4000,
    timeout: int = 60,
) -> dict:
    """Call Gemini Flash API. Returns {content, error}."""
    api_key = _get_api_key()
    if not api_key:
        return {"content": None, "error": "Gemini API key not configured"}

    payload = json.dumps({
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{system_prompt}\n\n{user_message}"},
                ],
            },
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_tokens,
        },
    }).encode()

    url = f"{GEMINI_API_URL}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"content": content, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return {"content": None, "error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"content": None, "error": f"URL error: {e.reason}"}
    except (KeyError, IndexError) as e:
        return {"content": None, "error": f"Response parse error: {e}"}
    except Exception as e:
        return {"content": None, "error": str(e)}


def extract_from_transcript_chunk(
    chunk: str,
    project: str | None = None,
    chunk_index: int = 0,
) -> list[dict]:
    """Extract note candidates from a transcript chunk using Gemini Flash.

    Returns list of:
    {content, category, rationale, alternatives_rejected,
     keywords, summary, source_line_start, source_line_end}

    Falls back to empty list on error — never raises.
    """
    project_ctx = f" for project '{project}'" if project else ""
    system = (
        "You are an AI that extracts structured knowledge from "
        "software development conversation transcripts. "
        "You identify decisions with their rationale, "
        "learnings from failures, recurring patterns, "
        "and constraints imposed by the human. "
        "You output only valid JSON arrays."
    )
    user = f"""Extract noteworthy items from this transcript chunk{project_ctx}.

Focus on:
- DECISIONS: what was chosen and WHY (include rationale and alternatives rejected)
- LEARNINGS: what failed, what the root cause was, what was done differently
- PATTERNS: recurring approaches worth remembering
- CONSTRAINTS: rules the human imposed on how CC should work

Skip: routine file reads, test runs, status checks, tool calls with
no decision significance.

The "rationale" field is mandatory for decisions — capture the actual
reasoning, not just what was decided.

Transcript chunk {chunk_index}:
{chunk}

Respond with ONLY a JSON array. Each element:
{{
  "content": "clear description of the decision/learning/pattern/constraint",
  "category": "decision|learning|pattern|constraint",
  "rationale": "why this decision was made (mandatory for decisions, optional for others)",
  "alternatives_rejected": ["alternative 1", "alternative 2"] or [],
  "keywords": ["keyword1", "keyword2"],
  "summary": "one sentence"
}}

If nothing noteworthy in this chunk, return [].
JSON:"""

    result = call_gemini(system, user, max_tokens=2000, timeout=45)
    if result["error"] or not result["content"]:
        return []

    text = result["content"].strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(parsed, list):
        return []

    valid_categories = {"decision", "learning", "pattern", "constraint"}
    items = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        cat = item.get("category", "learning")
        if cat not in valid_categories:
            cat = "learning"
        items.append({
            "content": item["content"],
            "category": cat,
            "rationale": item.get("rationale", ""),
            "alternatives_rejected": item.get("alternatives_rejected", []),
            "keywords": item.get("keywords", []),
            "summary": item.get("summary", ""),
        })
    return items
