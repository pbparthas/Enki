"""sessions.py — Session summary lifecycle + injection budget.

Pre-compact summaries accumulate across compactions.
Session end reconciles all into one final summary + bead candidates.
Post-compact re-injects the full intellectual thread.

Injection budget prevents context overflow:
    Minimal: ~1,500 tokens
    Standard: ~4,000 tokens
    Full: ~8,000 tokens
"""

import uuid
from datetime import datetime

from enki.config import get_config
from enki.db import abzu_db

# Rough token estimation: ~4 chars per token
CHARS_PER_TOKEN = 4


def create_summary(
    session_id: str,
    project: str | None = None,
    goal: str | None = None,
    phase: str | None = None,
    operational_state: str | None = None,
    conversational_state: str | None = None,
    is_final: bool = False,
) -> str:
    """Create a session summary entry. Returns summary ID."""
    summary_id = str(uuid.uuid4())

    with abzu_db() as conn:
        # Get next sequence number for this session
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 as next_seq "
            "FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sequence = row["next_seq"]

        conn.execute(
            "INSERT INTO session_summaries "
            "(id, session_id, project, sequence, goal, phase, "
            "operational_state, conversational_state, is_final) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (summary_id, session_id, project, sequence, goal, phase,
             operational_state, conversational_state, 1 if is_final else 0),
        )

    return summary_id


def update_pre_compact_summary(
    session_id: str,
    project: str | None = None,
    operational_state: str | None = None,
    conversational_state: str | None = None,
    goal: str | None = None,
    phase: str | None = None,
) -> str:
    """Store pre-compact summary. Accumulates across compactions."""
    return create_summary(
        session_id=session_id,
        project=project,
        goal=goal,
        phase=phase,
        operational_state=operational_state,
        conversational_state=conversational_state,
        is_final=False,
    )


def get_accumulated_summaries(session_id: str) -> list[dict]:
    """Load all pre-compact summaries for a session, ordered by sequence."""
    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT * FROM session_summaries "
            "WHERE session_id = ? AND is_final = 0 "
            "ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_post_compact_injection(session_id: str, tier: str) -> str:
    """Build post-compact injection within token budget.

    If accumulated summaries exceed budget:
    1. Keep most recent pre-compact summary in full
    2. Collapse older summaries into condensed narrative
    3. If still over, keep only most recent + key decisions list
    """
    config = get_config()
    budget = config["memory"]["session_summary_max_tokens"].get(tier, 4000)

    summaries = get_accumulated_summaries(session_id)
    if not summaries:
        return ""

    total_tokens = sum(_estimate_tokens(s) for s in summaries)

    if total_tokens <= budget:
        return _format_summaries(summaries)

    # Over budget — collapse
    most_recent = summaries[-1]
    older = summaries[:-1]

    condensed = _condense_summaries(older)
    combined_tokens = (
        _estimate_tokens(most_recent) + _estimate_tokens_str(condensed)
    )

    if combined_tokens <= budget:
        return _format_condensed(condensed, most_recent)

    # Still over — extreme compression
    decisions_only = _extract_decisions_only(summaries)
    return _format_minimal(decisions_only, most_recent)


def finalize_session(session_id: str, project: str | None = None) -> dict:
    """Create final session summary from accumulated pre-compact summaries.

    Returns the final summary ID.
    """
    summaries = get_accumulated_summaries(session_id)

    # Build reconciled summary
    parts = []
    goal = None
    phase = None
    for s in summaries:
        if s.get("goal"):
            goal = s["goal"]
        if s.get("phase"):
            phase = s["phase"]
        if s.get("operational_state"):
            parts.append(s["operational_state"])
        if s.get("conversational_state"):
            parts.append(s["conversational_state"])

    reconciled = "\n\n".join(parts) if parts else "No activity recorded."

    final_id = create_summary(
        session_id=session_id,
        project=project,
        goal=goal,
        phase=phase,
        operational_state=reconciled,
        is_final=True,
    )

    # Clean up pre-compact snapshots for this session
    _cleanup_pre_compact(session_id)

    return {"id": final_id, "content": reconciled}


def get_last_final_summary(project: str) -> dict | None:
    """Get the most recent final summary for a project."""
    with abzu_db() as conn:
        row = conn.execute(
            "SELECT * FROM session_summaries "
            "WHERE project = ? AND is_final = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        return dict(row) if row else None


def get_final_summaries(project: str, limit: int = 5) -> list[dict]:
    """Get recent final summaries for a project."""
    with abzu_db() as conn:
        rows = conn.execute(
            "SELECT * FROM session_summaries "
            "WHERE project = ? AND is_final = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def cleanup_old_summaries(project: str) -> int:
    """Delete old final summaries beyond the retention limit.

    Keeps the most recent N summaries per project (default 5).
    Returns number deleted.
    """
    config = get_config()
    keep = config["memory"].get("max_final_summaries_per_project", 5)

    with abzu_db() as conn:
        # Get IDs to keep
        keep_ids = conn.execute(
            "SELECT id FROM session_summaries "
            "WHERE project = ? AND is_final = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (project, keep),
        ).fetchall()
        keep_set = {r["id"] for r in keep_ids}

        if not keep_set:
            return 0

        # Delete older ones
        placeholders = ",".join("?" * len(keep_set))
        cursor = conn.execute(
            f"DELETE FROM session_summaries "
            f"WHERE project = ? AND is_final = 1 AND id NOT IN ({placeholders})",
            [project] + list(keep_set),
        )
        return cursor.rowcount


# ── Private helpers ──


def _cleanup_pre_compact(session_id: str) -> None:
    """Delete pre-compact snapshots after final summary is created."""
    with abzu_db() as conn:
        conn.execute(
            "DELETE FROM session_summaries "
            "WHERE session_id = ? AND is_final = 0",
            (session_id,),
        )


def _estimate_tokens(summary: dict) -> int:
    """Rough token estimate for a summary dict."""
    text = (summary.get("operational_state") or "") + (summary.get("conversational_state") or "")
    return len(text) // CHARS_PER_TOKEN


def _estimate_tokens_str(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _format_summaries(summaries: list[dict]) -> str:
    """Format all summaries for injection."""
    parts = ["## Session Context (accumulated)"]
    for i, s in enumerate(summaries):
        parts.append(f"\n### Checkpoint {i + 1}")
        if s.get("goal"):
            parts.append(f"Goal: {s['goal']}")
        if s.get("phase"):
            parts.append(f"Phase: {s['phase']}")
        if s.get("operational_state"):
            parts.append(s["operational_state"])
        if s.get("conversational_state"):
            parts.append(s["conversational_state"])
    return "\n".join(parts)


def _condense_summaries(summaries: list[dict]) -> str:
    """Collapse multiple summaries into a condensed narrative."""
    points = []
    for s in summaries:
        if s.get("operational_state"):
            # Extract key lines (decisions, completions)
            for line in s["operational_state"].split("\n"):
                line = line.strip()
                if line and len(line) > 10:
                    points.append(f"- {line}")
    return "Earlier session activity:\n" + "\n".join(points[:10])


def _extract_decisions_only(summaries: list[dict]) -> str:
    """Extract only decision-like content from summaries."""
    decisions = []
    keywords = ["decided", "chose", "picked", "using", "approach", "strategy"]
    for s in summaries:
        text = (s.get("operational_state") or "") + " " + (s.get("conversational_state") or "")
        for line in text.split("\n"):
            if any(kw in line.lower() for kw in keywords):
                decisions.append(f"- {line.strip()}")
    return "Key decisions:\n" + "\n".join(decisions[:5]) if decisions else ""


def _format_condensed(condensed: str, most_recent: dict) -> str:
    """Format condensed older summaries + full recent summary."""
    parts = ["## Session Context"]
    parts.append(condensed)
    parts.append("\n### Current State")
    if most_recent.get("operational_state"):
        parts.append(most_recent["operational_state"])
    if most_recent.get("conversational_state"):
        parts.append(most_recent["conversational_state"])
    return "\n".join(parts)


def _format_minimal(decisions: str, most_recent: dict) -> str:
    """Minimal format: decisions + most recent only."""
    parts = ["## Session Context (compressed)"]
    if decisions:
        parts.append(decisions)
    parts.append("\n### Latest State")
    if most_recent.get("operational_state"):
        parts.append(most_recent["operational_state"])
    return "\n".join(parts)
