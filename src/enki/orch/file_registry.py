"""file_registry.py — Lightweight file tracking per task.

Tracks files created/modified during execution. Before spawning Dev,
EM checks registry for description matches and injects reuse hints.

Dictionary maintained in EM session state (em.db).
"""

import json
import logging
from datetime import datetime, timezone

from enki.db import em_db

logger = logging.getLogger(__name__)


def register_files(
    project: str,
    task_id: str,
    files_created: list[str] | None = None,
    files_modified: list[str] | None = None,
    description: str | None = None,
) -> int:
    """Register files from agent output.

    Args:
        project: Project ID.
        task_id: Task that created/modified the files.
        files_created: New files created.
        files_modified: Existing files modified.
        description: Brief description of what was done.

    Returns count of entries registered.
    """
    entries = []
    for f in (files_created or []):
        entries.append((f, "created"))
    for f in (files_modified or []):
        entries.append((f, "modified"))

    if not entries:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    with em_db(project) as conn:
        _ensure_table(conn)
        for file_path, action in entries:
            conn.execute(
                "INSERT OR REPLACE INTO file_registry "
                "(project_id, file_path, task_id, action, description, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project, file_path, task_id, action, description, now),
            )

    return len(entries)


def lookup_files(project: str, query: str) -> list[dict]:
    """Find registered files matching a description query.

    Simple keyword match against file paths and descriptions.

    Returns list of matching registry entries.
    """
    if not query or not query.strip():
        return []

    keywords = query.lower().split()

    with em_db(project) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT file_path, task_id, action, description "
            "FROM file_registry WHERE project_id = ? "
            "ORDER BY registered_at DESC",
            (project,),
        ).fetchall()

    matches = []
    for row in rows:
        text = f"{row['file_path']} {row['description'] or ''}".lower()
        if any(kw in text for kw in keywords):
            matches.append(dict(row))

    return matches


def build_reuse_hint(matches: list[dict]) -> str | None:
    """Build a reuse hint string from matching registry entries.

    Returns None if no matches.
    """
    if not matches:
        return None

    lines = ["Note: Existing files may be relevant to this task. Evaluate for reuse:"]
    for m in matches[:5]:  # Cap at 5 hints
        desc = f" — {m['description']}" if m.get("description") else ""
        lines.append(f"  - {m['file_path']} ({m['action']} by task {m['task_id']}{desc})")

    return "\n".join(lines)


def get_all_files(project: str) -> list[dict]:
    """Get all registered files for a project."""
    with em_db(project) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT file_path, task_id, action, description, registered_at "
            "FROM file_registry WHERE project_id = ? "
            "ORDER BY registered_at DESC",
            (project,),
        ).fetchall()
    return [dict(r) for r in rows]


def _ensure_table(conn):
    """Create file_registry table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_registry (
            project_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            task_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('created', 'modified')),
            description TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, file_path)
        )
    """)
