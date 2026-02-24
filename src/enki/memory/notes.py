"""notes.py — v4 Notes CRUD + FTS5 search + dedup + ranking."""

import hashlib
import uuid
from datetime import datetime

from enki.config import get_config
from enki.db import wisdom_db

VALID_CATEGORIES = {
    "decision",
    "learning",
    "pattern",
    "fix",
    "preference",
    "code_knowledge",
}


def create(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
    context: str | None = None,
) -> dict:
    """Create a new note in wisdom.db notes table."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}")

    content_hash = _hash_content(content)
    existing = get_by_hash(content_hash)
    if existing:
        return existing

    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    with wisdom_db() as conn:
        if project:
            conn.execute(
                "INSERT OR IGNORE INTO projects (name, last_active) "
                "VALUES (?, datetime('now'))",
                (project,),
            )

        conn.execute(
            "INSERT INTO notes "
            "(id, content, summary, context_description, tags, category, project, "
            "content_hash, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                content,
                summary,
                context,
                tags,
                category,
                project,
                content_hash,
                now,
                now,
            ),
        )

    return get(note_id)


def get(note_id: str) -> dict | None:
    """Get a note by ID."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return dict(row) if row else None


def get_by_hash(content_hash: str) -> dict | None:
    """Get a note by content hash (dedup check)."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT * FROM notes WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None


def update(note_id: str, **kwargs) -> dict | None:
    """Update note fields. Returns updated note or None if not found."""
    allowed = {
        "content",
        "summary",
        "context_description",
        "keywords",
        "tags",
        "file_ref",
        "file_hash",
        "last_verified",
        "weight",
        "starred",
        "evolved_at",
        "promoted_at",
        "last_accessed",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return get(note_id)

    if "content" in updates:
        updates["content_hash"] = _hash_content(updates["content"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [note_id]

    with wisdom_db() as conn:
        conn.execute(f"UPDATE notes SET {set_clause} WHERE id = ?", values)

    return get(note_id)


def delete(note_id: str) -> bool:
    """Delete a note. Returns True if deleted."""
    with wisdom_db() as conn:
        cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cursor.rowcount > 0


def star(note_id: str, starred: bool = True) -> dict | None:
    """Mark note as starred (never decays) or unstar."""
    return update(note_id, starred=1 if starred else 0, weight=1.0)


def search(
    query: str,
    project: str | None = None,
    scope: str = "project",
    limit: int = 10,
    min_score: float | None = None,
) -> list[dict]:
    """FTS5 search with ranking and minimum score filtering for notes."""
    config = get_config()
    min_score = min_score or config["memory"]["fts5_min_score"]

    with wisdom_db() as conn:
        raw_results = conn.execute(
            "SELECT n.*, bm25(notes_fts) AS fts_score "
            "FROM notes_fts "
            "JOIN notes n ON notes_fts.rowid = n.rowid "
            "WHERE notes_fts MATCH ? "
            "ORDER BY bm25(notes_fts) "
            "LIMIT ?",
            (query, limit * 3),
        ).fetchall()

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

    scored = []
    for r in filtered:
        boost = 1.0
        if scope == "project" and project:
            if r["project"] == project:
                boost *= 1.5
            elif r["project"] is None:
                boost *= 1.2
        boost *= r["weight"]

        note = dict(r)
        note["final_score"] = abs(r["fts_score"]) * boost
        scored.append(note)

    scored.sort(key=lambda x: x["final_score"], reverse=True)

    returned = scored[:limit]
    if returned:
        touch([n["id"] for n in returned])

    return returned


def count(project: str | None = None, category: str | None = None) -> int:
    """Count notes with optional filters."""
    query = "SELECT COUNT(*) FROM notes WHERE 1=1"
    params = []
    if project:
        query += " AND project = ?"
        params.append(project)
    if category:
        query += " AND category = ?"
        params.append(category)

    with wisdom_db() as conn:
        return conn.execute(query, params).fetchone()[0]


def list_notes(
    project: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List notes with optional filters."""
    query = "SELECT * FROM notes WHERE 1=1"
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


def touch(note_ids: list[str]) -> None:
    """Update last_accessed timestamp for recalled notes."""
    now = datetime.now().isoformat()
    with wisdom_db() as conn:
        for note_id in note_ids:
            conn.execute(
                "UPDATE notes SET last_accessed = ? WHERE id = ?",
                (now, note_id),
            )


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()
