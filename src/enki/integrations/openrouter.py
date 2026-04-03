"""openrouter.py — OpenRouter API client for multi-model review.

Uses stdlib urllib only — no new dependencies.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ENKI_ROOT = Path(os.environ.get("ENKI_ROOT", Path.home() / ".enki"))


def _get_api_key() -> str | None:
    try:
        config = json.loads((ENKI_ROOT / "config.json").read_text())
        key = config.get("openrouter_api_key", "")
        return key if key and key != "YOUR_OPENROUTER_API_KEY" else None
    except Exception:
        return None


def _get_model() -> str:
    try:
        config = json.loads((ENKI_ROOT / "config.json").read_text())
        return config.get("codex_review_model", "openai/gpt-4o")
    except Exception:
        return "openai/gpt-4o"


def call_openrouter(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    max_tokens: int = 4000,
    timeout: int = 120,
) -> dict:
    """Call OpenRouter API and return parsed response."""
    api_key = _get_api_key()
    if not api_key:
        return {"content": None, "error": "OpenRouter API key not configured"}

    model_name = model or _get_model()
    payload = json.dumps(
        {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
    ).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/enki",
            "X-Title": "Enki Orchestration",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "model": model_name, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return {"content": None, "error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"content": None, "error": f"URL error: {e.reason}"}
    except Exception as e:
        return {"content": None, "error": str(e)}


def normalize_review_output(raw: str) -> dict:
    """Normalize OpenRouter response to reviewer output schema."""
    content = raw.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(content)
        return {
            "mode": parsed.get("mode", "sprint-review"),
            "status": parsed.get("status", "completed"),
            "summary": parsed.get("summary", ""),
            "spec_alignment_issues": parsed.get("spec_alignment_issues", []),
            "architectural_issues": parsed.get("architectural_issues", []),
            "quality_violations": parsed.get("quality_violations", []),
            "approved": parsed.get("approved", True),
            "notes": parsed.get("notes", ""),
            "_model": parsed.get("_model", "unknown"),
        }
    except Exception:
        return {
            "mode": "sprint-review",
            "status": "failed",
            "summary": f"Failed to parse Codex review output: {raw[:200]}",
            "spec_alignment_issues": [],
            "architectural_issues": [],
            "quality_violations": [],
            "approved": True,
            "notes": "Codex review output could not be parsed",
        }

