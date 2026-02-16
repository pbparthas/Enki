"""checkpoints.py — Session stacking: checkpoint, list, resume.

Pause a session and resume later with full context.
Checkpoints capture goal, phase, tier, orchestration state,
mail thread position, and recent bead IDs.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from enki.db import ENKI_ROOT, em_db, wisdom_db


def checkpoint_session(project: str, label: str | None = None) -> str:
    """Snapshot current session state to checkpoints table.

    Args:
        project: Project identifier.
        label: Optional human-readable label for this checkpoint.

    Returns:
        checkpoint_id
    """
    from enki.orch.tiers import get_project_state

    checkpoint_id = str(uuid.uuid4())
    session_id_file = ENKI_ROOT / "SESSION_ID"
    session_id = session_id_file.read_text().strip() if session_id_file.exists() else "unknown"

    state = get_project_state(project)

    # Gather orchestration state (active sprints + tasks)
    orch_state = _capture_orchestration_state(project)

    # Gather mail thread positions
    mail_pos = _capture_mail_position(project)

    # Get recent bead IDs
    recent_beads = _get_recent_bead_ids(limit=10)

    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO checkpoints "
            "(id, session_id, label, phase, tier, goal, "
            "orchestration_state, mail_position, recent_bead_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint_id,
                session_id,
                label,
                state.get("phase"),
                state.get("tier"),
                state.get("goal"),
                json.dumps(orch_state),
                json.dumps(mail_pos),
                json.dumps(recent_beads),
            ),
        )

    return checkpoint_id


def list_checkpoints(project: str) -> list[dict]:
    """List all checkpoints for a project, newest first.

    Returns list of checkpoint dicts with id, label, created_at, goal, phase, tier.
    """
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT id, session_id, label, created_at, phase, tier, goal "
            "FROM checkpoints ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def resume_session(project: str, checkpoint_id: str) -> dict:
    """Restore session state from a checkpoint.

    Restores phase, tier, and goal by writing to em.db task_state.
    Writes state files (.enki/PHASE, .enki/GOAL, .enki/TIER).

    Returns dict with restored state summary.
    """
    from enki.orch.tiers import set_goal, set_phase

    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE id = ?",
            (checkpoint_id,),
        ).fetchone()

    if not row:
        return {"error": f"Checkpoint not found: {checkpoint_id}"}

    checkpoint = dict(row)

    # Restore goal + tier (this creates a new task_state entry)
    if checkpoint.get("goal"):
        tier = checkpoint.get("tier") or "standard"
        set_goal(project, checkpoint["goal"], tier=tier)

    # Restore phase
    if checkpoint.get("phase"):
        set_phase(project, checkpoint["phase"])

    # Write state files for hook scripts
    _write_state_file("PHASE", checkpoint.get("phase", ""))
    _write_state_file("GOAL", checkpoint.get("goal", ""))
    _write_state_file("TIER", checkpoint.get("tier", ""))

    # Build summary for display
    recent_beads = json.loads(checkpoint.get("recent_bead_ids") or "[]")
    orch_state = json.loads(checkpoint.get("orchestration_state") or "{}")

    summary = {
        "checkpoint_id": checkpoint_id,
        "label": checkpoint.get("label"),
        "created_at": checkpoint.get("created_at"),
        "goal": checkpoint.get("goal"),
        "phase": checkpoint.get("phase"),
        "tier": checkpoint.get("tier"),
        "recent_bead_count": len(recent_beads),
        "active_sprints": len(orch_state.get("sprints", [])),
        "active_tasks": len(orch_state.get("tasks", [])),
    }

    return summary


# ── Private helpers ──


def _capture_orchestration_state(project: str) -> dict:
    """Capture active sprints and tasks from em.db."""
    with em_db(project) as conn:
        sprints = conn.execute(
            "SELECT sprint_id, sprint_number, status "
            "FROM sprint_state WHERE status != 'completed' "
            "ORDER BY sprint_number"
        ).fetchall()

        tasks = conn.execute(
            "SELECT task_id, task_name, status, assigned_files "
            "FROM task_state WHERE status NOT IN ('completed', 'cancelled') "
            "ORDER BY started_at"
        ).fetchall()

    return {
        "sprints": [dict(s) for s in sprints],
        "tasks": [dict(t) for t in tasks],
    }


def _capture_mail_position(project: str) -> dict:
    """Capture latest mail thread positions."""
    with em_db(project) as conn:
        threads = conn.execute(
            "SELECT thread_id, type, status FROM mail_threads "
            "WHERE status = 'active' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

        last_msg = conn.execute(
            "SELECT id, thread_id, from_agent, to_agent, created_at "
            "FROM mail_messages ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    return {
        "active_threads": [dict(t) for t in threads],
        "last_message_id": dict(last_msg)["id"] if last_msg else None,
    }


def _get_recent_bead_ids(limit: int = 10) -> list[str]:
    """Get IDs of most recently created beads."""
    try:
        with wisdom_db() as conn:
            rows = conn.execute(
                "SELECT id FROM beads ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [r["id"] for r in rows]
    except Exception:
        return []


def _write_state_file(name: str, value: str | None) -> None:
    """Write a state file to ~/.enki/ for hook scripts."""
    state_file = ENKI_ROOT / name
    state_file.write_text(value or "")
