"""Retention and decay logic for beads."""

from datetime import datetime, timezone
from typing import Union

from .beads import Bead
from .db import get_db
from .path_utils import normalize_timestamp


def calculate_weight(bead: Union[Bead, dict], access_counts: dict[str, int] = None) -> float:
    """Calculate bead weight based on age and access patterns.

    Args:
        bead: Bead object or dict-like row

    Returns:
        Weight between 0.0 and 1.0
    """
    # Handle both Bead objects and dict-like rows
    if isinstance(bead, Bead):
        starred = bead.starred
        superseded_by = bead.superseded_by
        created_at = bead.created_at
        last_accessed = bead.last_accessed
        bead_id = bead.id
    else:
        starred = bead.get("starred") in (1, True, "1")
        superseded_by = bead.get("superseded_by")
        created_at = bead.get("created_at")
        last_accessed = bead.get("last_accessed")
        bead_id = bead.get("id")

    # Starred beads never decay
    if starred:
        return 1.0

    # Superseded beads are effectively dead
    if superseded_by:
        return 0.0

    # Calculate age in days (P3-08: shared timestamp normalization)
    now = datetime.now(timezone.utc)
    created_at = normalize_timestamp(created_at, default=now)
    age_days = (now - created_at).days

    # Base weight by age tier
    if age_days < 30:
        base = 1.0      # HOT
    elif age_days < 90:
        base = 0.7      # WARM
    elif age_days < 365:
        base = 0.3      # COLD
    else:
        base = 0.1      # ARCHIVE

    # Boost for recent access
    if last_accessed:
        last_accessed = normalize_timestamp(last_accessed)
        days_since_access = (now - last_accessed).days

        if days_since_access < 7:
            base = min(base * 1.5, 1.0)
        elif days_since_access < 30:
            base = min(base * 1.2, 1.0)

    # Boost for frequent access (last 90 days)
    if bead_id:
        if access_counts is not None:
            access_count = access_counts.get(bead_id, 0)
        else:
            access_count = count_accesses(bead_id, days=90)
        if access_count > 10:
            base = min(base * 1.3, 1.0)
        elif access_count > 5:
            base = min(base * 1.1, 1.0)

    return base


def count_accesses(bead_id: str, days: int = 90) -> int:
    """Count access log entries for a bead in the last N days.

    Args:
        bead_id: The bead ID
        days: Number of days to look back

    Returns:
        Number of accesses
    """
    db = get_db()
    row = db.execute(
        """
        SELECT COUNT(*) as count FROM access_log
        WHERE bead_id = ?
        AND accessed_at > datetime('now', ?)
        """,
        (bead_id, f"-{days} days"),
    ).fetchone()

    return row["count"] if row else 0


def _batch_access_counts(bead_ids: list[str], days: int = 90) -> dict[str, int]:
    """Batch fetch access counts for multiple beads (P3-26: avoid N+1 queries).

    Args:
        bead_ids: List of bead IDs to count
        days: Number of days to look back

    Returns:
        Dict mapping bead_id -> access_count
    """
    if not bead_ids:
        return {}

    db = get_db()
    # SQLite doesn't support parameterized IN with arbitrary length,
    # so we batch in chunks of 500
    counts: dict[str, int] = {}
    chunk_size = 500

    for i in range(0, len(bead_ids), chunk_size):
        chunk = bead_ids[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        rows = db.execute(
            f"""
            SELECT bead_id, COUNT(*) as count FROM access_log
            WHERE bead_id IN ({placeholders})
            AND accessed_at > datetime('now', ?)
            GROUP BY bead_id
            """,
            (*chunk, f"-{days} days"),
        ).fetchall()
        for row in rows:
            counts[row["bead_id"]] = row["count"]

    return counts


def update_all_weights() -> int:
    """Recalculate weights for all active beads.

    Returns:
        Number of beads updated
    """
    db = get_db()

    rows = db.execute(
        "SELECT * FROM beads WHERE superseded_by IS NULL"
    ).fetchall()

    # P3-26: Batch fetch access counts instead of N+1 queries
    bead_ids = [row["id"] for row in rows if row["id"]]
    access_counts = _batch_access_counts(bead_ids)

    updated = 0
    for row in rows:
        new_weight = calculate_weight(dict(row), access_counts=access_counts)
        if abs(new_weight - row["weight"]) > 0.01:  # Only update if changed
            db.execute(
                "UPDATE beads SET weight = ? WHERE id = ?",
                (new_weight, row["id"]),
            )
            updated += 1

    db.commit()
    return updated


def archive_old_beads(days: int = 365) -> int:
    """Archive beads older than N days that were never accessed.

    Args:
        days: Age threshold in days

    Returns:
        Number of beads archived
    """
    db = get_db()

    cursor = db.execute(
        """
        UPDATE beads
        SET weight = 0.05
        WHERE created_at < datetime('now', ?)
        AND last_accessed IS NULL
        AND starred = 0
        AND superseded_by IS NULL
        """,
        (f"-{days} days",),
    )

    db.commit()
    return cursor.rowcount


def purge_old_superseded(days: int = 730) -> int:
    """Delete superseded beads older than N days.

    Args:
        days: Age threshold in days

    Returns:
        Number of beads deleted
    """
    db = get_db()

    cursor = db.execute(
        """
        DELETE FROM beads
        WHERE superseded_by IS NOT NULL
        AND created_at < datetime('now', ?)
        """,
        (f"-{days} days",),
    )

    db.commit()
    return cursor.rowcount


def maintain_wisdom() -> dict:
    """Run maintenance tasks.

    Returns:
        Dict with counts of actions taken
    """
    return {
        "weights_updated": update_all_weights(),
        "archived": archive_old_beads(),
        "purged": purge_old_superseded(),
    }
