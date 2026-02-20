"""recall.py — EM recall responsibilities before agent spawning.

Before Architect: extract keywords from Product Spec → enki_recall per keyword → inject.
Before Dev (per task): enki_recall on task description → inject code knowledge + relevant notes.
Check onboarding readiness: if codebase_scan = in_progress, skip code knowledge injection.
Recall results pass through sanitization before injection.
"""

import logging
import re
from typing import Optional

from enki.sanitization import sanitize_content, wrap_context

logger = logging.getLogger(__name__)


def recall_for_architect(
    spec_text: str,
    project: Optional[str] = None,
    limit_per_keyword: int = 3,
) -> list[dict]:
    """Recall relevant knowledge before spawning Architect.

    Extracts keywords from Product Spec, queries Abzu for each.

    Args:
        spec_text: Product Spec text.
        project: Project filter for recall.
        limit_per_keyword: Max results per keyword query.

    Returns list of unique recalled notes.
    """
    keywords = extract_keywords(spec_text)
    if not keywords:
        return []

    seen_ids = set()
    results = []

    for kw in keywords[:10]:  # Cap at 10 keywords
        try:
            notes = _do_recall(kw, project, limit_per_keyword)
            for note in notes:
                nid = note.get("note_id") or note.get("id", "")
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    results.append(note)
        except Exception as e:
            logger.debug("Recall for keyword '%s' failed: %s", kw, e)

    return results


def recall_for_dev(
    task_description: str,
    project: Optional[str] = None,
    limit: int = 5,
) -> dict:
    """Recall relevant knowledge before spawning Dev.

    Queries on task description. Separates code knowledge from other notes.

    Args:
        task_description: Task description text.
        project: Project filter.
        limit: Max total results.

    Returns dict with 'code_knowledge' and 'notes' lists.
    """
    # Check onboarding readiness
    if _is_scan_in_progress(project):
        logger.info("Codebase scan in progress — skipping code knowledge injection")
        return {
            "code_knowledge": [],
            "notes": [],
            "scan_in_progress": True,
        }

    try:
        results = _do_recall(task_description, project, limit)
    except Exception as e:
        logger.warning("Recall for Dev failed: %s", e)
        return {"code_knowledge": [], "notes": [], "scan_in_progress": False}

    code_knowledge = []
    notes = []
    for r in results:
        if r.get("category") == "code_knowledge":
            code_knowledge.append(r)
        else:
            notes.append(r)

    return {
        "code_knowledge": code_knowledge,
        "notes": notes,
        "scan_in_progress": False,
    }


def format_recall_for_injection(
    recall_results: list[dict],
    section_label: str = "RECALLED KNOWLEDGE",
) -> str:
    """Format recall results as sanitized injection text.

    All content passes through sanitization before inclusion.
    """
    if not recall_results:
        return ""

    lines = [f"## {section_label}", ""]
    for note in recall_results:
        content = note.get("content", "")[:200]
        category = note.get("category", "unknown")
        safe = sanitize_content(content, "em_distill")
        lines.append(f"- [{category}] {safe}")

    text = "\n".join(lines)
    return wrap_context(text, "recalled_knowledge")


def extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """Extract meaningful keywords from text for recall queries.

    Uses simple heuristic: noun-like words of length >= 4,
    excluding common stopwords.
    """
    if not text:
        return []

    # Tokenize and filter
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]+\b", text)

    # Remove stopwords and short words
    stopwords = {
        "the", "and", "that", "this", "with", "from", "have", "will",
        "should", "would", "could", "been", "being", "their", "they",
        "them", "then", "than", "when", "where", "what", "which",
        "into", "about", "between", "through", "before", "after",
        "above", "below", "each", "every", "some", "such", "only",
        "also", "must", "shall", "need", "make", "like", "just",
        "over", "more", "most", "other", "very", "well", "much",
        "many", "same", "both", "does", "done", "given", "based",
        "using", "used", "include", "including", "implement",
        "implementation", "following", "section", "ensure",
    }

    candidates = []
    seen = set()
    for word in words:
        lower = word.lower()
        if (
            len(lower) >= 4
            and lower not in stopwords
            and lower not in seen
        ):
            seen.add(lower)
            candidates.append(lower)

    return candidates[:max_keywords]


def _do_recall(
    query: str,
    project: Optional[str],
    limit: int,
) -> list[dict]:
    """Execute recall query via v4 hybrid search with v3 fallback."""
    try:
        from enki.embeddings import hybrid_search
        return hybrid_search(query, project=project, limit=limit)
    except Exception:
        try:
            from enki.memory.abzu import recall
            return recall(query=query, scope="project", project=project, limit=limit)
        except Exception:
            return []


def _is_scan_in_progress(project: Optional[str]) -> bool:
    """Check if codebase scan is still in progress."""
    if not project:
        return False
    try:
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            row = conn.execute(
                "SELECT codebase_scan FROM onboarding_status WHERE project = ?",
                (project,),
            ).fetchone()
            return row and row["codebase_scan"] == "in_progress"
        finally:
            conn.close()
    except Exception:
        return False
