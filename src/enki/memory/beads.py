"""beads.py — Bead CRUD + FTS5 search + dedup + ranking.

Beads are the atomic unit of knowledge in Abzu. Five categories:
- decision: Architectural choice with reasoning
- learning: Discovered through experience
- pattern: Reusable approach
- fix: Error → solution pair
- preference: Work style, tool choice (never decays)

wisdom.db holds Gemini-approved beads.
abzu.db staging holds candidates awaiting review.
"""

import hashlib
import uuid
from datetime import datetime

from enki.config import get_config
from enki.db import wisdom_db

VALID_CATEGORIES = {"decision", "learning", "pattern", "fix", "preference"}


def create(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
    context: str | None = None,
) -> dict:
    """Create a new bead in wisdom.db.

    Only preferences go directly to wisdom.db via this function.
    Non-preference beads should use staging.add_candidate() instead.

    Returns the created bead as a dict.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}")

    content_hash = _hash_content(content)

    # Check for duplicates
    existing = get_by_hash(content_hash)
    if existing:
        return existing

    bead_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    with wisdom_db() as conn:
        # Ensure project exists if specified
        if project:
            conn.execute(
                "INSERT OR IGNORE INTO projects (name, last_active) "
                "VALUES (?, datetime('now'))",
                (project,),
            )

        conn.execute(
            "INSERT INTO beads "
            "(id, content, summary, category, project, content_hash, "
            "tags, context, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bead_id, content, summary, category, project, content_hash,
             tags, context, now, now),
        )

    return get(bead_id)


def get(bead_id: str) -> dict | None:
    """Get a bead by ID."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT * FROM beads WHERE id = ?", (bead_id,)
        ).fetchone()
        return dict(row) if row else None


def get_by_hash(content_hash: str) -> dict | None:
    """Get a bead by content hash (dedup check)."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT * FROM beads WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None


def update(bead_id: str, **kwargs) -> dict | None:
    """Update bead fields. Returns updated bead or None if not found."""
    allowed = {"content", "summary", "tags", "context", "weight",
               "starred", "superseded_by", "gemini_flagged", "flag_reason"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return get(bead_id)

    # Recalculate hash if content changed
    if "content" in updates:
        updates["content_hash"] = _hash_content(updates["content"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [bead_id]

    with wisdom_db() as conn:
        conn.execute(
            f"UPDATE beads SET {set_clause} WHERE id = ?", values
        )

    return get(bead_id)


def delete(bead_id: str) -> bool:
    """Delete a bead. Returns True if deleted."""
    with wisdom_db() as conn:
        cursor = conn.execute("DELETE FROM beads WHERE id = ?", (bead_id,))
        return cursor.rowcount > 0


def star(bead_id: str, starred: bool = True) -> dict | None:
    """Mark bead as starred (never decays) or unstar."""
    return update(bead_id, starred=1 if starred else 0, weight=1.0)


def search(
    query: str,
    project: str | None = None,
    scope: str = "project",
    limit: int = 10,
    min_score: float | None = None,
) -> list[dict]:
    """FTS5 search with ranking and minimum score filtering.

    Score = fts5_relevance * project_boost * weight * source_boost

    min_score is applied BEFORE project boosts, preventing weak matches
    from surfacing just because they have a project boost.
    """
    config = get_config()
    min_score = min_score or config["memory"]["fts5_min_score"]

    with wisdom_db() as conn:
        # Raw FTS5 search using bm25() for meaningful scores
        # bm25() returns negative values (more negative = better match)
        raw_results = conn.execute(
            "SELECT b.*, bm25(beads_fts) AS fts_score "
            "FROM beads_fts "
            "JOIN beads b ON beads_fts.rowid = b.rowid "
            "WHERE beads_fts MATCH ? "
            "ORDER BY bm25(beads_fts) "
            "LIMIT ?",
            (query, limit * 3),
        ).fetchall()

    # Apply min_score BEFORE boosts (Abzu Spec §8 v1.1)
    # Prevents weak matches from surfacing just because of project boost.
    # BM25 scores are relative to corpus — normalize against best match.
    if raw_results:
        best_score = max(abs(r["fts_score"]) for r in raw_results)
        threshold = min_score * best_score if best_score > 0 else 0
    else:
        threshold = 0

    filtered = []
    for r in raw_results:
        raw_score = abs(r["fts_score"])
        if raw_score >= threshold:
            filtered.append(r)

    # Apply boosts
    scored = []
    for r in filtered:
        boost = 1.0
        if scope == "project" and project:
            if r["project"] == project:
                boost *= 1.5
            elif r["project"] is None:
                boost *= 1.2
        boost *= r["weight"]

        bead = dict(r)
        bead["final_score"] = abs(r["fts_score"]) * boost
        scored.append(bead)

    scored.sort(key=lambda x: x["final_score"], reverse=True)

    # Update last_accessed for returned beads
    returned = scored[:limit]
    if returned:
        _touch_beads([b["id"] for b in returned])

    return returned


def count(project: str | None = None, category: str | None = None) -> int:
    """Count beads with optional filters."""
    query = "SELECT COUNT(*) FROM beads WHERE 1=1"
    params = []
    if project:
        query += " AND project = ?"
        params.append(project)
    if category:
        query += " AND category = ?"
        params.append(category)

    with wisdom_db() as conn:
        return conn.execute(query, params).fetchone()[0]


def list_beads(
    project: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List beads with optional filters."""
    query = "SELECT * FROM beads WHERE 1=1"
    params: list = []
    if project:
        query += " AND project = ?"
        params.append(project)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with wisdom_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ── Private helpers ──


def _hash_content(content: str) -> str:
    """SHA-256 hash of content for dedup."""
    return hashlib.sha256(content.encode()).hexdigest()


def store_with_dedup(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
    context: str | None = None,
) -> dict:
    """Store a bead with 3-outcome dedup check (per Abzu Spec §10).

    Outcomes:
    1. "new" — no match, bead created
    2. "updated" — exact hash match exists, last_accessed refreshed
    3. "duplicate" — exact hash match exists, no change needed

    Returns dict with 'outcome' and 'bead' keys.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}")

    content_hash = _hash_content(content)
    existing = get_by_hash(content_hash)

    if existing:
        # Check if context or tags differ — if so, update (outcome: updated)
        changed = False
        updates = {}
        if context and context != existing.get("context"):
            updates["context"] = context
            changed = True
        if tags and tags != existing.get("tags"):
            updates["tags"] = tags
            changed = True
        if summary and summary != existing.get("summary"):
            updates["summary"] = summary
            changed = True

        if changed:
            updated = update(existing["id"], **updates)
            _touch_beads([existing["id"]])
            return {"outcome": "updated", "bead": updated}
        else:
            return {"outcome": "duplicate", "bead": existing}

    # New bead
    bead = create(
        content=content,
        category=category,
        project=project,
        summary=summary,
        tags=tags,
        context=context,
    )
    return {"outcome": "new", "bead": bead}


def _touch_beads(bead_ids: list[str]) -> None:
    """Update last_accessed timestamp for recalled beads."""
    now = datetime.now().isoformat()
    with wisdom_db() as conn:
        for bead_id in bead_ids:
            conn.execute(
                "UPDATE beads SET last_accessed = ? WHERE id = ?",
                (now, bead_id),
            )
