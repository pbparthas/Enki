"""staging.py — Candidate staging + promotion.

All non-preference beads go to staging (abzu.db) first.
Only two paths write to wisdom.db:
1. Preference beads — direct (factual, no review needed)
2. Gemini-promoted beads — quarterly reviewed and approved
"""

import hashlib
import uuid
from datetime import datetime

from enki.db import abzu_db, wisdom_db


def add_candidate(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    source: str = "session",
    session_id: str | None = None,
) -> str | None:
    """Add a bead candidate to staging in abzu.db.

    Returns candidate ID, or None if duplicate.
    """
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Check for duplicates in staging
    with abzu_db() as conn:
        existing = conn.execute(
            "SELECT id FROM bead_candidates WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return None

    # Also check wisdom.db for already-promoted duplicates
    with wisdom_db() as conn:
        existing = conn.execute(
            "SELECT id FROM beads WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return None

    candidate_id = str(uuid.uuid4())
    with abzu_db() as conn:
        conn.execute(
            "INSERT INTO bead_candidates "
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
    query = "SELECT * FROM bead_candidates WHERE 1=1"
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
            "SELECT * FROM bead_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None


def search_candidates(query: str, limit: int = 10) -> list[dict]:
    """FTS5 search over staged candidates."""
    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT bc.*, rank AS fts_score "
            "FROM candidates_fts "
            "JOIN bead_candidates bc ON candidates_fts.rowid = bc.rowid "
            "WHERE candidates_fts MATCH ? "
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

    from enki.memory.beads import create

    bead = create(
        content=candidate["content"],
        category=candidate["category"],
        project=candidate.get("project"),
        summary=candidate.get("summary"),
    )

    # Update promoted_at
    with wisdom_db() as conn:
        conn.execute(
            "UPDATE beads SET promoted_at = datetime('now') WHERE id = ?",
            (bead["id"],),
        )

    # Remove from staging
    discard(candidate_id)

    return bead["id"]


def discard(candidate_id: str) -> bool:
    """Remove a candidate from staging."""
    with abzu_db() as conn:
        cursor = conn.execute(
            "DELETE FROM bead_candidates WHERE id = ?",
            (candidate_id,),
        )
        return cursor.rowcount > 0


def count_candidates(project: str | None = None) -> int:
    """Count staged candidates."""
    query = "SELECT COUNT(*) FROM bead_candidates"
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
