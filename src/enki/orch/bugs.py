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
        _ensure_bug_number_schema(conn)
        bug_number = _next_bug_number(conn, project)
        conn.execute(
            "INSERT INTO bugs "
            "(id, bug_number, project_id, task_id, sprint_id, filed_by, priority, "
            "title, description, mail_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bug_id, bug_number, project, task_id, sprint_id, filed_by, priority,
             title, description, mail_message_id),
        )
    return bug_id


def get_bug(project: str, bug_id: str) -> dict | None:
    """Get bug by ID."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
        row = conn.execute(
            "SELECT * FROM bugs WHERE id = ?", (bug_id,)
        ).fetchone()
        return dict(row) if row else None


def assign_bug(project: str, bug_id: str, agent: str) -> None:
    """Assign a bug to an agent."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
        conn.execute(
            "UPDATE bugs SET assigned_to = ? WHERE id = ?",
            (agent, bug_id),
        )


def resolve_bug(project: str, bug_id: str) -> None:
    """Mark a bug as resolved."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
        conn.execute(
            "UPDATE bugs SET status = 'resolved', "
            "resolved_at = datetime('now') WHERE id = ?",
            (bug_id,),
        )


def close_bug(project: str, bug_id: str) -> None:
    """Close a bug (verified fix)."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
        conn.execute(
            "UPDATE bugs SET status = 'closed', "
            "resolved_at = COALESCE(resolved_at, datetime('now')) WHERE id = ?",
            (bug_id,),
        )


def reopen_bug(project: str, bug_id: str) -> None:
    """Reopen a previously resolved/closed bug."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
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
        _ensure_bug_number_schema(conn)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_open_bugs(project: str) -> dict:
    """Count open bugs by priority."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
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
        _ensure_bug_number_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM bugs "
            "WHERE project_id = ? AND status = 'open' "
            "AND priority IN ('P0', 'P1')",
            (project,),
        ).fetchone()
        return row[0] > 0


def derive_project_prefix(project: str) -> str:
    """Derive human-readable prefix from project name."""
    raw = (project or "").strip()
    if not raw:
        return "ENKI"
    parts = [part for part in raw.replace("_", "-").split("-") if part]
    if not parts:
        return "ENKI"
    initials = "".join(part[0].upper() for part in parts if part and part[0].isalnum())
    if not initials:
        return "ENKI"
    if len(parts) == 1 and len(initials) == 1 and len(parts[0]) > 1:
        initials = parts[0][:2].upper()
    return initials[:4]


def to_human_bug_id(project: str, bug_number: int) -> str:
    """Format human-readable bug id."""
    return f"{derive_project_prefix(project)}-{bug_number:03d}"


def resolve_bug_identifier(project: str, bug_ref: str) -> tuple[str, str] | None:
    """Resolve UUID or human bug ID to (uuid, human_id)."""
    with em_db(project) as conn:
        _ensure_bug_number_schema(conn)
        row = conn.execute(
            "SELECT id, bug_number FROM bugs WHERE id = ? LIMIT 1",
            (bug_ref,),
        ).fetchone()
        if row:
            return row["id"], to_human_bug_id(project, int(row["bug_number"]))

        if "-" not in bug_ref:
            return None
        prefix, _, num = bug_ref.partition("-")
        if prefix != derive_project_prefix(project):
            return None
        try:
            bug_number = int(num)
        except ValueError:
            return None
        row = conn.execute(
            "SELECT id, bug_number FROM bugs WHERE project_id = ? AND bug_number = ? LIMIT 1",
            (project, bug_number),
        ).fetchone()
        if not row:
            return None
        return row["id"], to_human_bug_id(project, int(row["bug_number"]))


def _ensure_bug_number_schema(conn) -> None:
    cols = conn.execute("PRAGMA table_info(bugs)").fetchall()
    col_names = {str(col["name"]) for col in cols}
    if "bug_number" not in col_names:
        conn.execute("ALTER TABLE bugs ADD COLUMN bug_number INTEGER")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bugs_project_number "
        "ON bugs(project_id, bug_number)"
    )
    _backfill_bug_numbers(conn)


def _backfill_bug_numbers(conn) -> None:
    projects = conn.execute(
        "SELECT DISTINCT project_id FROM bugs WHERE project_id IS NOT NULL"
    ).fetchall()
    for project_row in projects:
        project = project_row["project_id"]
        existing_max = conn.execute(
            "SELECT COALESCE(MAX(bug_number), 0) AS max_bug_number "
            "FROM bugs WHERE project_id = ?",
            (project,),
        ).fetchone()
        next_num = int(existing_max["max_bug_number"] or 0)
        missing = conn.execute(
            "SELECT id FROM bugs WHERE project_id = ? AND bug_number IS NULL "
            "ORDER BY datetime(created_at) ASC, rowid ASC",
            (project,),
        ).fetchall()
        for row in missing:
            next_num += 1
            conn.execute(
                "UPDATE bugs SET bug_number = ? WHERE id = ?",
                (next_num, row["id"]),
            )


def _next_bug_number(conn, project: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(bug_number), 0) + 1 AS next_num FROM bugs WHERE project_id = ?",
        (project,),
    ).fetchone()
    return int(row["next_num"])
