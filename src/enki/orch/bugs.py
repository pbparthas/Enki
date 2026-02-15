"""bugs.py — Bug lifecycle management.

Bugs are filed by QA, Validator, Reviewer, or InfoSec.
Assigned by EM. Tracked in em.db per-project.
"""

import uuid
from datetime import datetime

from enki.db import em_db


def file_bug(
    project: str,
    title: str,
    description: str,
    filed_by: str,
    priority: str = "P2",
    task_id: str | None = None,
    sprint_id: str | None = None,
    mail_message_id: str | None = None,
) -> str:
    """File a new bug. Returns bug ID."""
    bug_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO bugs "
            "(id, project_id, task_id, sprint_id, filed_by, priority, "
            "title, description, mail_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bug_id, project, task_id, sprint_id, filed_by, priority,
             title, description, mail_message_id),
        )
    return bug_id


def get_bug(project: str, bug_id: str) -> dict | None:
    """Get bug by ID."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM bugs WHERE id = ?", (bug_id,)
        ).fetchone()
        return dict(row) if row else None


def assign_bug(project: str, bug_id: str, agent: str) -> None:
    """Assign a bug to an agent."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE bugs SET assigned_to = ? WHERE id = ?",
            (agent, bug_id),
        )


def resolve_bug(project: str, bug_id: str) -> None:
    """Mark a bug as resolved."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE bugs SET status = 'resolved', "
            "resolved_at = datetime('now') WHERE id = ?",
            (bug_id,),
        )


def close_bug(project: str, bug_id: str) -> None:
    """Close a bug (verified fix)."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE bugs SET status = 'closed', "
            "resolved_at = COALESCE(resolved_at, datetime('now')) WHERE id = ?",
            (bug_id,),
        )


def reopen_bug(project: str, bug_id: str) -> None:
    """Reopen a previously resolved/closed bug."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE bugs SET status = 'open', resolved_at = NULL WHERE id = ?",
            (bug_id,),
        )


def list_bugs(
    project: str,
    status: str | None = None,
    priority: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    """List bugs with optional filters."""
    query = "SELECT * FROM bugs WHERE project_id = ?"
    params: list = [project]

    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if task_id:
        query += " AND task_id = ?"
        params.append(task_id)

    query += " ORDER BY created_at DESC"

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_open_bugs(project: str) -> dict:
    """Count open bugs by priority."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM bugs "
            "WHERE project_id = ? AND status = 'open' "
            "GROUP BY priority",
            (project,),
        ).fetchall()
        return {row["priority"]: row["cnt"] for row in rows}


def has_blocking_bugs(project: str) -> bool:
    """Check if there are any P0 or P1 open bugs."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM bugs "
            "WHERE project_id = ? AND status = 'open' "
            "AND priority IN ('P0', 'P1')",
            (project,),
        ).fetchone()
        return row[0] > 0
