"""Violation and escalation logging."""

import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from .db import get_db
from .session import (
    get_session_id, get_phase, get_tier, get_goal,
    get_session_edits, Tier,
)
from .enforcement import count_lines_changed


@dataclass
class Violation:
    """A gate violation."""
    id: str
    session_id: str
    gate: str
    tool: str
    file_path: Optional[str]
    phase: str
    tier: str
    reason: str
    was_overridden: bool
    timestamp: datetime


@dataclass
class Escalation:
    """A tier escalation."""
    id: int
    session_id: str
    initial_tier: str
    final_tier: str
    files_at_escalation: int
    lines_at_escalation: int
    goal: Optional[str]
    created_at: datetime


def log_violation(
    gate: str,
    tool: str,
    reason: str,
    file_path: Optional[str] = None,
    was_overridden: bool = False,
    project_path: Optional[Path] = None,
) -> Violation:
    """Log a gate violation.

    Args:
        gate: Which gate was violated (phase, spec, tdd, scope)
        tool: Tool that was attempted
        reason: Reason for violation
        file_path: File being accessed (if applicable)
        was_overridden: Whether user overrode the block
        project_path: Project path

    Returns:
        Created Violation record
    """
    db = get_db()
    violation_id = str(uuid.uuid4())
    session_id = get_session_id(project_path) or "unknown"
    phase = get_phase(project_path)
    tier = get_tier(project_path)

    db.execute(
        """
        INSERT INTO violations
        (id, session_id, gate, tool, file_path, phase, tier, reason, was_overridden)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (violation_id, session_id, gate, tool, file_path, phase, tier, reason, int(was_overridden)),
    )
    db.commit()

    return Violation(
        id=violation_id,
        session_id=session_id,
        gate=gate,
        tool=tool,
        file_path=file_path,
        phase=phase,
        tier=tier,
        reason=reason,
        was_overridden=was_overridden,
        timestamp=datetime.now(),
    )


def log_escalation(
    initial_tier: Tier,
    final_tier: Tier,
    project_path: Optional[Path] = None,
) -> Escalation:
    """Log a tier escalation.

    Args:
        initial_tier: Tier before escalation
        final_tier: Tier after escalation
        project_path: Project path

    Returns:
        Created Escalation record
    """
    db = get_db()
    session_id = get_session_id(project_path) or "unknown"
    goal = get_goal(project_path)
    edits = get_session_edits(project_path)

    files_count = len(edits)
    lines_count = sum(count_lines_changed(f, project_path) for f in edits)

    cursor = db.execute(
        """
        INSERT INTO tier_escalations
        (session_id, initial_tier, final_tier, files_at_escalation, lines_at_escalation, goal)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, initial_tier, final_tier, files_count, lines_count, goal),
    )
    db.commit()

    return Escalation(
        id=cursor.lastrowid,
        session_id=session_id,
        initial_tier=initial_tier,
        final_tier=final_tier,
        files_at_escalation=files_count,
        lines_at_escalation=lines_count,
        goal=goal,
        created_at=datetime.now(),
    )


def log_escalation_to_file(
    initial_tier: Tier,
    final_tier: Tier,
    project_path: Optional[Path] = None,
) -> None:
    """Append escalation to ESCALATIONS.md file."""
    from .session import ensure_project_enki_dir

    enki_dir = ensure_project_enki_dir(project_path)
    escalations_file = enki_dir / "ESCALATIONS.md"

    edits = get_session_edits(project_path)
    lines_count = sum(count_lines_changed(f, project_path) for f in edits)
    goal = get_goal(project_path) or "(no goal set)"

    entry = (
        f"\n## {datetime.now().isoformat()}\n"
        f"- **Escalation**: {initial_tier} → {final_tier}\n"
        f"- **Files edited**: {len(edits)}\n"
        f"- **Lines changed**: {lines_count}\n"
        f"- **Goal**: {goal}\n"
        f"- **Files**: {', '.join(edits[-5:])}"
        + (" ..." if len(edits) > 5 else "")
        + "\n"
    )

    if escalations_file.exists():
        content = escalations_file.read_text()
    else:
        content = "# Tier Escalations\n"

    escalations_file.write_text(content + entry)


def get_violations(
    session_id: Optional[str] = None,
    gate: Optional[str] = None,
    limit: int = 100,
) -> list[Violation]:
    """Get violations from database.

    Args:
        session_id: Filter by session
        gate: Filter by gate type
        limit: Maximum records to return

    Returns:
        List of Violation records
    """
    db = get_db()

    query = "SELECT * FROM violations WHERE 1=1"
    params = []

    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)

    if gate:
        query += " AND gate = ?"
        params.append(gate)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()

    return [
        Violation(
            id=row["id"],
            session_id=row["session_id"],
            gate=row["gate"],
            tool=row["tool"],
            file_path=row["file_path"],
            phase=row["phase"],
            tier=row["tier"],
            reason=row["reason"],
            was_overridden=bool(row["was_overridden"]),
            timestamp=row["timestamp"],
        )
        for row in rows
    ]


def get_escalations(
    session_id: Optional[str] = None,
    limit: int = 100,
) -> list[Escalation]:
    """Get escalations from database.

    Args:
        session_id: Filter by session
        limit: Maximum records to return

    Returns:
        List of Escalation records
    """
    db = get_db()

    query = "SELECT * FROM tier_escalations WHERE 1=1"
    params = []

    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()

    return [
        Escalation(
            id=row["id"],
            session_id=row["session_id"],
            initial_tier=row["initial_tier"],
            final_tier=row["final_tier"],
            files_at_escalation=row["files_at_escalation"],
            lines_at_escalation=row["lines_at_escalation"],
            goal=row["goal"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def get_violation_stats(days: int = 7) -> dict:
    """Get violation statistics for reporting.

    Args:
        days: Number of days to look back

    Returns:
        Statistics dict
    """
    db = get_db()

    # Total violations
    total = db.execute(
        """
        SELECT COUNT(*) as count FROM violations
        WHERE timestamp > datetime('now', ?)
        """,
        (f"-{days} days",),
    ).fetchone()["count"]

    # By gate
    by_gate = db.execute(
        """
        SELECT gate, COUNT(*) as count FROM violations
        WHERE timestamp > datetime('now', ?)
        GROUP BY gate
        """,
        (f"-{days} days",),
    ).fetchall()

    # Override rate
    overridden = db.execute(
        """
        SELECT COUNT(*) as count FROM violations
        WHERE timestamp > datetime('now', ?)
        AND was_overridden = 1
        """,
        (f"-{days} days",),
    ).fetchone()["count"]

    # Escalations
    escalations = db.execute(
        """
        SELECT COUNT(*) as count FROM tier_escalations
        WHERE created_at > datetime('now', ?)
        """,
        (f"-{days} days",),
    ).fetchone()["count"]

    return {
        "total_violations": total,
        "by_gate": {row["gate"]: row["count"] for row in by_gate},
        "overridden": overridden,
        "override_rate": overridden / total if total > 0 else 0,
        "escalations": escalations,
    }
