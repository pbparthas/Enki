"""Override mechanism for emergency gate bypass.

Allows temporary bypass of gates for emergencies with:
- Time limit (default 15 minutes)
- File limit (default 3 files)
- Mandatory logging
- Post-hoc review
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import get_db, init_db


@dataclass
class Override:
    """An active override session."""
    id: str
    session_id: str
    reason: str
    tier: str
    max_files: int
    duration_seconds: int
    files_edited: int
    started_at: datetime
    expires_at: datetime
    was_legitimate: Optional[bool] = None


def start_override(
    reason: str,
    tier: str = "quick_fix",
    max_files: int = 3,
    duration_minutes: int = 15,
    project_path: Optional[Path] = None,
) -> Override:
    """Start an emergency override.

    Args:
        reason: Why the override is needed
        tier: Maximum tier for the override
        max_files: Maximum files that can be edited
        duration_minutes: How long the override lasts

    Returns:
        Override object with ID and expiration
    """
    init_db()
    db = get_db()

    override_id = f"override_{uuid.uuid4().hex[:8]}"
    started_at = datetime.now()
    expires_at = started_at + timedelta(minutes=duration_minutes)
    duration_seconds = duration_minutes * 60

    # Get current session ID
    session_id = _get_current_session_id(project_path)

    db.execute("""
        INSERT INTO overrides (id, session_id, reason, tier, max_files,
                              duration_seconds, files_edited, started_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
    """, (
        override_id,
        session_id,
        reason,
        tier,
        max_files,
        duration_seconds,
        started_at.isoformat(),
        expires_at.isoformat(),
    ))
    db.commit()

    # Also save to project .enki/ for hook access
    if project_path:
        enki_dir = project_path / ".enki"
    else:
        enki_dir = Path.cwd() / ".enki"

    enki_dir.mkdir(exist_ok=True)
    override_file = enki_dir / "OVERRIDE"
    override_file.write_text(f"{override_id}\n{expires_at.isoformat()}\n{max_files}\n{tier}")

    return Override(
        id=override_id,
        session_id=session_id,
        reason=reason,
        tier=tier,
        max_files=max_files,
        duration_seconds=duration_seconds,
        files_edited=0,
        started_at=started_at,
        expires_at=expires_at,
    )


def get_active_override(project_path: Optional[Path] = None) -> Optional[Override]:
    """Get the current active override if any."""
    if project_path:
        enki_dir = project_path / ".enki"
    else:
        enki_dir = Path.cwd() / ".enki"

    override_file = enki_dir / "OVERRIDE"
    if not override_file.exists():
        return None

    try:
        lines = override_file.read_text().strip().split("\n")
        override_id = lines[0]
        expires_at = datetime.fromisoformat(lines[1])

        # Check if expired
        if datetime.now() > expires_at:
            # Expired - remove file
            override_file.unlink()
            return None

        # Get full details from database
        init_db()
        db = get_db()
        row = db.execute(
            "SELECT * FROM overrides WHERE id = ?", (override_id,)
        ).fetchone()

        if not row:
            override_file.unlink()
            return None

        return Override(
            id=row["id"],
            session_id=row["session_id"],
            reason=row["reason"],
            tier=row["tier"],
            max_files=row["max_files"],
            duration_seconds=row["duration_seconds"],
            files_edited=row["files_edited"],
            started_at=datetime.fromisoformat(row["started_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            was_legitimate=row["was_legitimate"],
        )

    except Exception:
        return None


def check_override_allows(
    file_path: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> tuple[bool, Optional[str]]:
    """Check if active override allows the operation.

    Returns:
        (allowed, reason) - If allowed, reason is None
    """
    override = get_active_override(project_path)

    if not override:
        return False, "No active override"

    # Check expiration
    if datetime.now() > override.expires_at:
        end_override(override.id, project_path)
        return False, "Override expired"

    # Check file limit
    if file_path and override.files_edited >= override.max_files:
        return False, f"Override file limit ({override.max_files}) reached"

    return True, None


def track_override_edit(
    file_path: str,
    project_path: Optional[Path] = None,
) -> None:
    """Track a file edit under the active override."""
    override = get_active_override(project_path)
    if not override:
        return

    init_db()
    db = get_db()

    db.execute("""
        UPDATE overrides
        SET files_edited = files_edited + 1
        WHERE id = ?
    """, (override.id,))
    db.commit()


def end_override(
    override_id: str,
    project_path: Optional[Path] = None,
) -> None:
    """End an override session."""
    if project_path:
        enki_dir = project_path / ".enki"
    else:
        enki_dir = Path.cwd() / ".enki"

    override_file = enki_dir / "OVERRIDE"
    if override_file.exists():
        override_file.unlink()


def mark_override_legitimate(
    override_id: str,
    was_legitimate: bool,
) -> bool:
    """Mark whether an override was legitimately needed.

    Returns True if found and updated.
    """
    init_db()
    db = get_db()

    result = db.execute("""
        UPDATE overrides
        SET was_legitimate = ?
        WHERE id = ?
    """, (1 if was_legitimate else 0, override_id))
    db.commit()

    return result.rowcount > 0


def get_override_stats(days: int = 30) -> dict:
    """Get statistics about override usage."""
    init_db()
    db = get_db()

    total = db.execute("""
        SELECT COUNT(*) as count FROM overrides
        WHERE started_at > datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    legitimate = db.execute("""
        SELECT COUNT(*) as count FROM overrides
        WHERE was_legitimate = 1
        AND started_at > datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    illegitimate = db.execute("""
        SELECT COUNT(*) as count FROM overrides
        WHERE was_legitimate = 0
        AND started_at > datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    unreviewed = db.execute("""
        SELECT COUNT(*) as count FROM overrides
        WHERE was_legitimate IS NULL
        AND started_at > datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    avg_files = db.execute("""
        SELECT AVG(files_edited) as avg FROM overrides
        WHERE started_at > datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["avg"] or 0

    return {
        "total": total,
        "legitimate": legitimate,
        "illegitimate": illegitimate,
        "unreviewed": unreviewed,
        "average_files_edited": round(avg_files, 1),
    }


def get_recent_overrides(limit: int = 10) -> list[dict]:
    """Get recent overrides for review."""
    init_db()
    db = get_db()

    rows = db.execute("""
        SELECT * FROM overrides
        ORDER BY started_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    return [
        {
            "id": row["id"],
            "reason": row["reason"],
            "tier": row["tier"],
            "max_files": row["max_files"],
            "files_edited": row["files_edited"],
            "started_at": row["started_at"],
            "was_legitimate": row["was_legitimate"],
        }
        for row in rows
    ]


def _get_current_session_id(project_path: Optional[Path] = None) -> str:
    """Get the current session ID."""
    if project_path:
        session_file = project_path / ".enki" / "SESSION"
    else:
        session_file = Path.cwd() / ".enki" / "SESSION"

    if session_file.exists():
        return session_file.read_text().strip()

    return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
