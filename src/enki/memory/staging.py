"""staging.py — Candidate staging + promotion.

All non-preference beads go to staging (abzu.db) first.
Only two paths write to wisdom.db:
1. Preference beads — direct (factual, no review needed)
2. Gemini-promoted beads — quarterly reviewed and approved
"""

import hashlib
import re
import uuid
from datetime import datetime

from enki.db import abzu_db, wisdom_db

ALLOWED_NOTE_SOURCES = frozenset({
    "manual",
    "session_end",
    "code_scan",
    "onboarding",
    "rescan",
    "em_distill",
})

SOURCE_ALIASES = {
    "session": "session_end",
    "enki_remember": "manual",
    "synthesis": "manual",
    "em.db": "em_distill",
    "em.db bugs": "em_distill",
    "em.db pm_decisions": "em_distill",
    "v1/v2 migration": "manual",
}


# Filler phrases that indicate no actionable content
FILLER_PHRASES = frozenset({
    "ok", "done", "got it", "sure", "yes", "no", "thanks",
    "let me think", "hmm", "alright", "sounds good", "will do",
    "okay", "yep", "nope", "yeah", "nah", "right", "fine",
    "thank you", "cool", "nice", "great", "good", "yup",
})

# Common English stopwords (articles, prepositions, conjunctions, pronouns)
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "this", "that", "i", "we",
    "you", "he", "she", "they", "me", "my", "your", "his", "her", "its",
    "our", "their", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "just", "so", "if", "not",
    "then", "than", "when", "what", "how", "all", "each", "every", "both",
    "few", "more", "most", "some", "any", "no", "about", "up", "out",
    "into", "over", "after", "before", "between", "under", "again",
    "there", "here", "very", "too", "also",
})


def bouncer_check(content: str) -> tuple[bool, str]:
    """Check if content is worth staging.

    Returns:
        (passed, reason) — True if content should be staged,
        False with reason if rejected.
    """
    stripped = content.strip()

    # Rule 1: Too short
    if len(stripped) < 10:
        return (False, "Too short")

    # Rule 2: Filler phrases (normalize and check)
    normalized = stripped.lower().rstrip(".!?,:;")
    if normalized in FILLER_PHRASES:
        return (False, "No actionable content")

    # Rule 3: All stopwords (no substance)
    words = re.findall(r"[a-zA-Z]+", stripped.lower())
    if words and all(w in STOPWORDS for w in words):
        return (False, "No substance")

    # Rule 4: Exact duplicate already in staging (content hash check)
    content_hash = hashlib.sha256(stripped.encode()).hexdigest()
    with abzu_db() as conn:
        existing = conn.execute(
            "SELECT id FROM note_candidates WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return (False, "Duplicate")

    return (True, "")


def _normalize_source(source: str) -> str:
    """Normalize legacy source labels to note_candidates v4 allowed values."""
    normalized = SOURCE_ALIASES.get(source, source)
    if normalized in ALLOWED_NOTE_SOURCES:
        return normalized
    return "manual"


def _log_rejection(content: str, reason: str, source: str = "session") -> None:
    """Log a bouncer rejection to staging_rejections table."""
    with abzu_db() as conn:
        conn.execute(
            "INSERT INTO staging_rejections (content, reason, source) "
            "VALUES (?, ?, ?)",
            (content, reason, source),
        )


def list_rejections(limit: int = 20) -> list[dict]:
    """List recent bouncer rejections."""
    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT id, content, reason, rejected_at, source "
            "FROM staging_rejections ORDER BY rejected_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def override_rejection(rejection_id: int) -> str | None:
    """Push a rejected item back into staging as a candidate.

    Returns the new candidate ID, or None if rejection not found.
    """
    with abzu_db() as conn:
        row = conn.execute(
            "SELECT content, source FROM staging_rejections WHERE id = ?",
            (rejection_id,),
        ).fetchone()
        if not row:
            return None

    # Add directly to staging, bypassing bouncer
    content = row["content"]
    source = _normalize_source(row["source"] or "session")
    content_hash = hashlib.sha256(content.strip().encode()).hexdigest()

    candidate_id = str(uuid.uuid4())
    with abzu_db() as conn:
        conn.execute(
            "INSERT INTO note_candidates "
            "(id, content, summary, category, project, content_hash, source, session_id) "
            "VALUES (?, ?, NULL, 'learning', NULL, ?, ?, NULL)",
            (candidate_id, content, content_hash, source),
        )
        # Remove from rejections
        conn.execute(
            "DELETE FROM staging_rejections WHERE id = ?",
            (rejection_id,),
        )

    return candidate_id


def add_candidate(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    source: str = "session",
    session_id: str | None = None,
) -> str | None:
    """Add a bead candidate to staging in abzu.db.

    Returns candidate ID, or None if rejected by bouncer or duplicate.
    """
    # Bouncer gate — reject junk before staging
    passed, reason = bouncer_check(content)
    if not passed:
        _log_rejection(content, reason, source)
        return None

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    source = _normalize_source(source)

    # Check for duplicates in staging
    with abzu_db() as conn:
        existing = conn.execute(
            "SELECT id FROM note_candidates WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return None

    # Also check wisdom.db for already-promoted duplicates
    with wisdom_db() as conn:
        existing = conn.execute(
            "SELECT id FROM notes WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return None

    candidate_id = str(uuid.uuid4())
    with abzu_db() as conn:
        conn.execute(
            "INSERT INTO note_candidates "
            "(id, content, summary, category, project, content_hash, source, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, content, summary, category, project,
             content_hash, source, session_id),
        )

    return candidate_id


def list_candidates(
    project: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List staged candidates."""
    query = "SELECT * FROM note_candidates WHERE 1=1"
    params: list = []
    if project:
        query += " AND project = ?"
        params.append(project)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with abzu_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_candidate(candidate_id: str) -> dict | None:
    """Get a single candidate."""
    with abzu_db() as conn:
        row = conn.execute(
            "SELECT * FROM note_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None


def resolve_candidate_id(short_id: str) -> str | None:
    """Resolve short candidate ID prefix to full UUID in note_candidates.

    Rules:
    - Full UUID input returns as-is.
    - Prefix input returns full UUID only if exactly one match.
    - No match or ambiguous prefix returns None.
    """
    if not short_id:
        return None

    value = short_id.strip()
    if not value:
        return None

    try:
        uuid.UUID(value)
        return value
    except (ValueError, AttributeError, TypeError):
        pass

    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT id FROM note_candidates WHERE id LIKE ?",
            (f"{value}%",),
        ).fetchall()

    if len(rows) == 1:
        return rows[0]["id"]
    return None


def search_candidates(query: str, limit: int = 10) -> list[dict]:
    """FTS5 search over staged candidates."""
    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT bc.*, rank AS fts_score "
            "FROM candidates_v4_fts "
            "JOIN note_candidates bc ON candidates_v4_fts.rowid = bc.rowid "
            "WHERE candidates_v4_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def promote(candidate_id: str) -> str | None:
    """Promote a candidate from staging to wisdom.db.

    Returns the new bead ID in wisdom.db, or None if candidate not found.
    """
    candidate = get_candidate(candidate_id)
    if not candidate:
        return None

    from enki.memory.notes import create

    bead = create(
        content=candidate["content"],
        category=candidate["category"],
        project=candidate.get("project"),
        summary=candidate.get("summary"),
    )

    # Update promoted_at
    with wisdom_db() as conn:
        conn.execute(
            "UPDATE notes SET promoted_at = datetime('now') WHERE id = ?",
            (bead["id"],),
        )

    # Remove from staging
    discard(candidate_id)

    return bead["id"]


def discard(candidate_id: str) -> bool:
    """Remove a candidate from staging."""
    with abzu_db() as conn:
        cursor = conn.execute(
            "DELETE FROM note_candidates WHERE id = ?",
            (candidate_id,),
        )
        return cursor.rowcount > 0


def count_candidates(project: str | None = None) -> int:
    """Count staged candidates."""
    query = "SELECT COUNT(*) FROM note_candidates"
    params = []
    if project:
        query += " WHERE project = ?"
        params.append(project)

    with abzu_db() as conn:
        return conn.execute(query, params).fetchone()[0]


def promote_batch(candidate_ids: list[str]) -> dict:
    """Promote multiple candidates. Returns stats."""
    promoted = 0
    failed = 0
    for cid in candidate_ids:
        result = promote(cid)
        if result:
            promoted += 1
        else:
            failed += 1
    return {"promoted": promoted, "failed": failed}
