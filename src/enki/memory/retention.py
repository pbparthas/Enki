"""retention.py — Decay scoring + maintenance.

Recall-based decay: recalled beads stay hot, unused beads fade.
Decay reduces search ranking but NEVER deletes.
Only Gemini can flag for deletion.

Thresholds (from config):
    Not recalled in 90 days: weight 0.5
    Not recalled in 180 days: weight 0.2
    Not recalled in 365 days: weight 0.1
    Starred or preference: always 1.0
"""

from datetime import datetime, timedelta

from enki.config import get_config
from enki.db import wisdom_db


def run_decay() -> dict:
    """Run decay pass on all beads in wisdom.db.

    Returns stats dict with counts of beads affected at each threshold.
    """
    config = get_config()
    thresholds = config["memory"]["decay_thresholds"]
    now = datetime.now()

    stats = {"unchanged": 0, "d30": 0, "d90": 0, "d180": 0, "d365": 0}

    with wisdom_db() as conn:
        beads = conn.execute(
            "SELECT id, last_accessed, starred, category, weight FROM beads"
        ).fetchall()

        for bead in beads:
            # Never decay starred beads or preferences
            if bead["starred"] or bead["category"] == "preference":
                stats["unchanged"] += 1
                continue

            last = bead["last_accessed"]
            if not last:
                # Never accessed — apply maximum decay
                _set_weight(conn, bead["id"], thresholds["d365"])
                stats["d365"] += 1
                continue

            try:
                last_dt = datetime.fromisoformat(last)
            except (ValueError, TypeError):
                stats["unchanged"] += 1
                continue

            days_since = (now - last_dt).days

            if days_since >= 365:
                new_weight = thresholds["d365"]
                stats["d365"] += 1
            elif days_since >= 180:
                new_weight = thresholds["d180"]
                stats["d180"] += 1
            elif days_since >= 90:
                new_weight = thresholds["d90"]
                stats["d90"] += 1
            elif days_since >= 30:
                # 30-day threshold: still hot but starting to cool
                new_weight = 1.0
                stats["d30"] += 1
            else:
                new_weight = 1.0
                stats["unchanged"] += 1

            if abs(bead["weight"] - new_weight) > 0.01:
                _set_weight(conn, bead["id"], new_weight)

    return stats


def refresh_weight(bead_id: str) -> None:
    """Reset weight to 1.0 when a bead is recalled."""
    with wisdom_db() as conn:
        conn.execute(
            "UPDATE beads SET weight = 1.0, last_accessed = datetime('now') "
            "WHERE id = ?",
            (bead_id,),
        )


def get_decay_stats() -> dict:
    """Get current decay distribution stats."""
    with wisdom_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        hot = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE weight >= 0.9"
        ).fetchone()[0]
        warm = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE weight >= 0.4 AND weight < 0.9"
        ).fetchone()[0]
        cold = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE weight >= 0.1 AND weight < 0.4"
        ).fetchone()[0]
        frozen = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE weight < 0.1"
        ).fetchone()[0]
        starred = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE starred = 1"
        ).fetchone()[0]

    return {
        "total": total,
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "frozen": frozen,
        "starred": starred,
    }


def process_flagged_deletions() -> dict:
    """Delete beads where gemini_flagged=1 (Abzu Spec §9).

    Only Gemini can flag for deletion. This function executes the deletions.
    Returns stats dict with count of deleted beads and their categories.
    """
    stats = {"deleted": 0, "by_category": {}}

    with wisdom_db() as conn:
        flagged = conn.execute(
            "SELECT id, category, flag_reason FROM beads WHERE gemini_flagged = 1"
        ).fetchall()

        for bead in flagged:
            category = bead["category"]
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
            conn.execute("DELETE FROM beads WHERE id = ?", (bead["id"],))
            stats["deleted"] += 1

    return stats


def calculate_weight(
    last_accessed: str | None,
    starred: bool,
    category: str,
) -> float:
    """Calculate decay weight for a bead (Abzu Spec §9).

    Thresholds:
    - Recalled in last 30 days: 1.0
    - Not recalled in 90 days: 0.5
    - Not recalled in 180 days: 0.2
    - Not recalled in 365 days: 0.1
    - Starred or preference: always 1.0
    """
    if starred or category == "preference":
        return 1.0

    if not last_accessed:
        config = get_config()
        return config["memory"]["decay_thresholds"]["d365"]

    try:
        last_dt = datetime.fromisoformat(last_accessed)
    except (ValueError, TypeError):
        return 1.0

    config = get_config()
    thresholds = config["memory"]["decay_thresholds"]
    days_since = (datetime.now() - last_dt).days

    if days_since >= 365:
        return thresholds["d365"]
    elif days_since >= 180:
        return thresholds["d180"]
    elif days_since >= 90:
        return thresholds["d90"]
    else:
        return 1.0


def _set_weight(conn, bead_id: str, weight: float) -> None:
    """Set weight for a bead."""
    conn.execute(
        "UPDATE beads SET weight = ? WHERE id = ?", (weight, bead_id)
    )
