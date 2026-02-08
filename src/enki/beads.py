"""Bead CRUD operations."""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Optional, Literal
from dataclasses import dataclass, field

from .db import get_db
from .embeddings import embed, vector_to_blob, blob_to_vector

BeadType = Literal["decision", "solution", "learning", "violation", "pattern"]


@dataclass
class Bead:
    """A knowledge unit."""
    id: str
    content: str
    type: BeadType
    summary: Optional[str] = None
    project: Optional[str] = None
    weight: float = 1.0
    starred: bool = False
    superseded_by: Optional[str] = None
    context: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    last_accessed: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Bead":
        """Create Bead from database row."""
        tags = []
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
            except json.JSONDecodeError:
                tags = []

        return cls(
            id=row["id"],
            content=row["content"],
            type=row["type"],
            summary=row["summary"],
            project=row["project"],
            weight=row["weight"],
            starred=bool(row["starred"]),
            superseded_by=row["superseded_by"],
            context=row["context"],
            tags=tags,
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
        )


def create_bead(
    content: str,
    bead_type: BeadType,
    summary: Optional[str] = None,
    project: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[list[str]] = None,
    starred: bool = False,
) -> Bead:
    """Create a new bead with embedding.

    Args:
        content: The knowledge content
        bead_type: Type of bead (decision, solution, learning, violation, pattern)
        summary: Optional short summary
        project: Optional project identifier
        context: Optional context when learned
        tags: Optional list of tags
        starred: Whether to star (never decay)

    Returns:
        Created Bead
    """
    bead_id = str(uuid.uuid4())
    tags_json = json.dumps(tags or [])
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    db = get_db()

    # Exact-content dedup: skip if identical bead already exists
    existing = db.execute(
        "SELECT id FROM beads WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing:
        return get_bead(existing[0])

    # P1-09: Wrap bead + embedding insert in explicit transaction
    try:
        db.execute("BEGIN")
        db.execute(
            """
            INSERT INTO beads (id, content, type, summary, project, context, tags, starred, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bead_id, content, bead_type, summary, project, context, tags_json, int(starred), content_hash),
        )

        # Generate and store embedding
        vector = embed(content)
        db.execute(
            "INSERT INTO embeddings (bead_id, vector) VALUES (?, ?)",
            (bead_id, vector_to_blob(vector)),
        )

        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    # Fetch and return
    return get_bead(bead_id)


def get_bead(bead_id: str) -> Optional[Bead]:
    """Get a bead by ID.

    Args:
        bead_id: The bead ID

    Returns:
        Bead if found, None otherwise
    """
    db = get_db()
    row = db.execute("SELECT * FROM beads WHERE id = ?", (bead_id,)).fetchone()

    if row is None:
        return None

    return Bead.from_row(row)


def update_bead(
    bead_id: str,
    content: Optional[str] = None,
    summary: Optional[str] = None,
    starred: Optional[bool] = None,
    superseded_by: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Optional[Bead]:
    """Update a bead.

    Args:
        bead_id: The bead ID
        content: New content (will regenerate embedding)
        summary: New summary
        starred: New starred status
        superseded_by: ID of bead that supersedes this one
        tags: New tags

    Returns:
        Updated Bead if found, None otherwise
    """
    bead = get_bead(bead_id)
    if bead is None:
        return None

    db = get_db()
    updates = []
    params = []

    if content is not None:
        updates.append("content = ?")
        params.append(content)

        # Regenerate embedding
        vector = embed(content)
        db.execute(
            "UPDATE embeddings SET vector = ? WHERE bead_id = ?",
            (vector_to_blob(vector), bead_id),
        )

    if summary is not None:
        updates.append("summary = ?")
        params.append(summary)

    if starred is not None:
        updates.append("starred = ?")
        params.append(int(starred))

    if superseded_by is not None:
        updates.append("superseded_by = ?")
        params.append(superseded_by)
        updates.append("weight = 0")

    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))

    if updates:
        params.append(bead_id)
        db.execute(
            f"UPDATE beads SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        db.commit()

    return get_bead(bead_id)


def delete_bead(bead_id: str) -> bool:
    """Delete a bead.

    Args:
        bead_id: The bead ID

    Returns:
        True if deleted, False if not found
    """
    db = get_db()
    cursor = db.execute("DELETE FROM beads WHERE id = ?", (bead_id,))
    db.commit()
    return cursor.rowcount > 0


def star_bead(bead_id: str) -> Optional[Bead]:
    """Star a bead (never decay).

    Args:
        bead_id: The bead ID

    Returns:
        Updated Bead if found, None otherwise
    """
    return update_bead(bead_id, starred=True)


def unstar_bead(bead_id: str) -> Optional[Bead]:
    """Unstar a bead.

    Args:
        bead_id: The bead ID

    Returns:
        Updated Bead if found, None otherwise
    """
    return update_bead(bead_id, starred=False)


def supersede_bead(old_id: str, new_id: str) -> Optional[Bead]:
    """Mark a bead as superseded by another.

    Args:
        old_id: The bead being superseded
        new_id: The bead that supersedes it

    Returns:
        Updated old Bead if found, None otherwise
    """
    return update_bead(old_id, superseded_by=new_id)


def get_bead_stats() -> dict:
    """Get bead statistics (P2-11: service function replaces raw SQL in CLI).

    Returns:
        {"total": int, "active": int, "starred": int, "by_type": dict[str, int]}
    """
    db = get_db()
    total = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()["count"]
    active = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE superseded_by IS NULL"
    ).fetchone()["count"]
    starred = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE starred = 1"
    ).fetchone()["count"]
    by_type = {
        row["type"]: row["count"]
        for row in db.execute(
            "SELECT type, COUNT(*) as count FROM beads WHERE superseded_by IS NULL GROUP BY type"
        ).fetchall()
    }
    return {"total": total, "active": active, "starred": starred, "by_type": by_type}


def log_access(bead_id: str, session_id: Optional[str] = None, was_useful: Optional[bool] = None) -> None:
    """Log access to a bead.

    Args:
        bead_id: The bead ID
        session_id: Optional session ID
        was_useful: Optional feedback
    """
    db = get_db()

    # Log access
    db.execute(
        "INSERT INTO access_log (bead_id, session_id, was_useful) VALUES (?, ?, ?)",
        (bead_id, session_id, was_useful if was_useful is not None else None),
    )

    # Update last_accessed
    db.execute(
        "UPDATE beads SET last_accessed = CURRENT_TIMESTAMP WHERE id = ?",
        (bead_id,),
    )

    db.commit()


def get_beads_by_project(project: str, include_global: bool = True) -> list[Bead]:
    """Get all beads for a project.

    Args:
        project: Project identifier
        include_global: Include beads with project=NULL

    Returns:
        List of Beads
    """
    db = get_db()

    if include_global:
        rows = db.execute(
            "SELECT * FROM beads WHERE project = ? OR project IS NULL ORDER BY created_at DESC",
            (project,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM beads WHERE project = ? ORDER BY created_at DESC",
            (project,),
        ).fetchall()

    return [Bead.from_row(row) for row in rows]


def get_recent_beads(limit: int = 10, project: Optional[str] = None) -> list[Bead]:
    """Get most recently created beads.

    Args:
        limit: Maximum number to return
        project: Optional project filter

    Returns:
        List of Beads
    """
    db = get_db()

    if project:
        rows = db.execute(
            """
            SELECT * FROM beads
            WHERE (project = ? OR project IS NULL) AND superseded_by IS NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT * FROM beads
            WHERE superseded_by IS NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [Bead.from_row(row) for row in rows]
