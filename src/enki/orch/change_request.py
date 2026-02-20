"""change_request.py — Mid-project change request flow (Item 4.5).

PM owns mid-project changes:
- Minor: PM approves, logs in mail thread
- Major: PM writes CR → Architect reviews impact → spec revision → re-review → HITL
- Locked specs get version bump, not rewrite
- Change history tracked in mail thread
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from enki.db import em_db

logger = logging.getLogger(__name__)


def classify_change(description: str, scope: dict | None = None) -> str:
    """Classify a change request as minor or major.

    Minor: affects ≤2 tasks, no new dependencies, no spec structure change.
    Major: affects >2 tasks, adds dependencies, changes spec structure.
    """
    text = description.lower()

    major_indicators = [
        "new feature", "architecture change", "breaking change",
        "new dependency", "new service", "schema change",
        "api contract", "security model", "deployment",
        "remove", "replace", "rewrite", "redesign",
    ]

    if any(ind in text for ind in major_indicators):
        return "major"

    # Check scope impact
    if scope:
        affected_tasks = scope.get("affected_tasks", [])
        if len(affected_tasks) > 2:
            return "major"
        if scope.get("new_dependencies"):
            return "major"

    return "minor"


def create_change_request(
    project: str,
    description: str,
    requested_by: str = "Human",
    classification: str | None = None,
    affected_tasks: list[str] | None = None,
) -> dict:
    """Create a change request record.

    Returns the CR dict with ID and status.
    """
    cr_id = f"CR-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    if not classification:
        classification = classify_change(
            description,
            {"affected_tasks": affected_tasks or []},
        )

    with em_db(project) as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO change_requests "
            "(id, project_id, description, requested_by, classification, "
            "affected_tasks, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cr_id, project, description, requested_by, classification,
                json.dumps(affected_tasks or []),
                "pending",
                now,
            ),
        )

    return {
        "cr_id": cr_id,
        "classification": classification,
        "status": "pending",
        "next_step": (
            "PM approves and logs" if classification == "minor"
            else "Architect reviews impact → spec revision → HITL"
        ),
    }


def approve_change_request(project: str, cr_id: str, approved_by: str = "PM") -> dict:
    """Approve a change request (minor flow)."""
    with em_db(project) as conn:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE change_requests SET status = 'approved', "
            "resolved_by = ?, resolved_at = ? "
            "WHERE id = ? AND project_id = ?",
            (approved_by, now, cr_id, project),
        )
        if cursor.rowcount == 0:
            return {"error": f"CR {cr_id} not found"}

    return {"cr_id": cr_id, "status": "approved", "approved_by": approved_by}


def reject_change_request(
    project: str, cr_id: str, reason: str = "", rejected_by: str = "PM"
) -> dict:
    """Reject a change request."""
    with em_db(project) as conn:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE change_requests SET status = 'rejected', "
            "resolved_by = ?, resolved_at = ?, resolution_notes = ? "
            "WHERE id = ? AND project_id = ?",
            (rejected_by, now, reason, cr_id, project),
        )
        if cursor.rowcount == 0:
            return {"error": f"CR {cr_id} not found"}

    return {"cr_id": cr_id, "status": "rejected", "reason": reason}


def get_change_requests(project: str, status: str | None = None) -> list[dict]:
    """List change requests for a project."""
    with em_db(project) as conn:
        _ensure_table(conn)
        query = "SELECT * FROM change_requests WHERE project_id = ?"
        params = [project]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


def bump_spec_version(project: str) -> dict:
    """Bump spec version for a locked spec (major change).

    Increments version counter, does not rewrite the spec.
    """
    with em_db(project) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT version_number FROM spec_versions "
            "WHERE project_id = ? ORDER BY version_number DESC LIMIT 1",
            (project,),
        ).fetchone()

        if row:
            new_version = row["version_number"] + 1
        else:
            new_version = 2  # v1 was the original

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO spec_versions "
            "(project_id, version_number, created_at) "
            "VALUES (?, ?, ?)",
            (project, new_version, now),
        )

    return {"project": project, "new_version": f"v{new_version}"}


def _ensure_table(conn):
    """Create change_requests and spec_versions tables if not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS change_requests (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            description TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            classification TEXT NOT NULL CHECK (classification IN ('minor', 'major')),
            affected_tasks TEXT,
            status TEXT DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'implemented')),
            resolved_by TEXT,
            resolved_at TIMESTAMP,
            resolution_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spec_versions (
            project_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, version_number)
        )
    """)
