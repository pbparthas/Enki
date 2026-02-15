"""feedback.py — Feedback proposal CRUD for Uru.

Proposals are auto-created from overrides and ignored nudges.
Gemini reviews quarterly. Human approves changes.
CC never modifies its own rules.
"""

import uuid
from datetime import datetime

from enki.db import uru_db


def create_proposal(
    trigger_type: str,
    description: str,
    related_log_ids: list[str] | None = None,
) -> str:
    """Create a feedback proposal.

    Args:
        trigger_type: What triggered this (e.g., "override", "nudge_ignored", "false_positive").
        description: What the proposal suggests changing.
        related_log_ids: Enforcement log IDs that triggered this.

    Returns:
        The proposal ID.
    """
    proposal_id = str(uuid.uuid4())
    with uru_db() as conn:
        conn.execute(
            "INSERT INTO feedback_proposals "
            "(id, trigger_type, description, related_log_ids) "
            "VALUES (?, ?, ?, ?)",
            (
                proposal_id,
                trigger_type,
                description,
                ",".join(related_log_ids) if related_log_ids else None,
            ),
        )
    return proposal_id


def list_proposals(status: str = "pending") -> list[dict]:
    """List proposals filtered by status."""
    with uru_db() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback_proposals WHERE status = ? "
            "ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_proposal(proposal_id: str) -> dict | None:
    """Get a single proposal by ID."""
    with uru_db() as conn:
        row = conn.execute(
            "SELECT * FROM feedback_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        return dict(row) if row else None


def apply_proposal(proposal_id: str) -> bool:
    """Mark a proposal as applied (human approved)."""
    with uru_db() as conn:
        conn.execute(
            "UPDATE feedback_proposals SET status = 'applied', "
            "applied = 1, reviewed_at = datetime('now') "
            "WHERE id = ?",
            (proposal_id,),
        )
        return True


def reject_proposal(proposal_id: str, reason: str = "") -> bool:
    """Mark a proposal as rejected."""
    with uru_db() as conn:
        conn.execute(
            "UPDATE feedback_proposals SET status = 'rejected', "
            "gemini_response = ?, reviewed_at = datetime('now') "
            "WHERE id = ?",
            (reason, proposal_id),
        )
        return True


def generate_session_proposals(session_id: str) -> list[str]:
    """Analyze session enforcement log and generate proposals.

    Called at session end. Looks for patterns that suggest
    gates are too strict or too lenient.
    """
    proposals = []

    with uru_db() as conn:
        # Pattern: Multiple overrides of the same gate
        overrides = conn.execute(
            "SELECT reason, COUNT(*) as cnt FROM enforcement_log "
            "WHERE session_id = ? AND user_override = 1 "
            "GROUP BY reason HAVING cnt >= 2",
            (session_id,),
        ).fetchall()

        for row in overrides:
            pid = create_proposal(
                trigger_type="repeated_override",
                description=(
                    f"Gate '{row['reason']}' was overridden {row['cnt']} times "
                    "in one session. Consider adjusting threshold or adding exemption."
                ),
            )
            proposals.append(pid)

        # Pattern: Nudges fired but never acted on
        ignored_nudges = conn.execute(
            "SELECT nudge_type, fire_count FROM nudge_state "
            "WHERE session_id = ? AND fire_count >= 3 AND acted_on = 0",
            (session_id,),
        ).fetchall()

        for row in ignored_nudges:
            pid = create_proposal(
                trigger_type="nudge_ignored",
                description=(
                    f"Nudge '{row['nudge_type']}' fired {row['fire_count']} times "
                    "but was never acted on. Consider adjusting nudge sensitivity."
                ),
            )
            proposals.append(pid)

    return proposals
