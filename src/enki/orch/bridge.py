"""bridge.py — Memory bridge: extract bead candidates from completed em.db.

At project completion, Abzu reads em.db and distills bead candidates.
Bridge is the extraction interface.

What becomes candidates:
- Product + Implementation Specs → decision
- Key decisions from planning threads → decision
- Bug patterns and fixes → fix
- Architectural approaches → pattern
- Final review feedback → learning

What does NOT become candidates:
- Routine "task complete" messages
- Intermediate validator output
- Raw test results
- Status update messages
"""

import re

from enki.db import em_db


# Message types that produce candidates
_CANDIDATE_SOURCES = {
    "spec_product": "decision",
    "spec_implementation": "decision",
    "planning": "decision",
    "bug_fix": "fix",
    "architecture": "pattern",
    "review": "learning",
}

# Agents whose output is worth extracting
_EXTRACT_FROM = {
    "PM", "Architect", "DBA", "Reviewer", "InfoSec",
    "Dev", "QA",
}

# Skip these thread types — noise, not signal
_SKIP_TYPES = {
    "status", "ack", "notification",
}


def extract_beads_from_project(project: str) -> list[dict]:
    """Extract bead candidates from completed project's em.db.

    Returns list of candidate dicts ready for staging in abzu.db.
    """
    candidates = []

    with em_db(project) as conn:
        # Get all mail threads
        threads = conn.execute(
            "SELECT thread_id, type FROM mail_threads "
            "WHERE project_id = ? ORDER BY created_at",
            (project,),
        ).fetchall()

        for thread in threads:
            thread_id = thread["thread_id"]
            thread_type = (thread["type"] or "").lower()

            # Skip noise threads
            if thread_type in _SKIP_TYPES:
                continue

            messages = conn.execute(
                "SELECT from_agent, body, importance, subject FROM mail_messages "
                "WHERE thread_id = ? ORDER BY created_at",
                (thread_id,),
            ).fetchall()

            for msg in messages:
                candidate = _extract_from_message(
                    from_agent=msg["from_agent"],
                    content=msg["body"],
                    importance=msg["importance"],
                    thread_type=thread_type,
                    subject=msg["subject"] or "",
                )
                if candidate:
                    candidate["source"] = f"em.db thread:{thread_id}"
                    candidate["project"] = project
                    candidates.append(candidate)

        # Extract from bugs
        bugs = conn.execute(
            "SELECT title, description, status FROM bugs "
            "WHERE project_id = ? AND status IN ('resolved', 'closed')",
            (project,),
        ).fetchall()

        for bug in bugs:
            candidates.append({
                "category": "fix",
                "content": f"Bug: {bug['title']}\n{bug['description']}",
                "source": "em.db bugs",
                "project": project,
            })

        # Extract from PM decisions
        decisions = conn.execute(
            "SELECT decision_type, proposed_action, context "
            "FROM pm_decisions WHERE project_id = ? "
            "AND decision_type NOT LIKE 'spec_approval%'",
            (project,),
        ).fetchall()

        for dec in decisions:
            content = f"{dec['decision_type']}: {dec['proposed_action']}"
            if dec["context"]:
                content += f"\nContext: {dec['context']}"
            candidates.append({
                "category": "decision",
                "content": content,
                "source": "em.db pm_decisions",
                "project": project,
            })

    return candidates


def _extract_from_message(
    from_agent: str,
    content: str,
    importance: str,
    thread_type: str,
    subject: str,
) -> dict | None:
    """Extract a single candidate from a mail message, or None."""
    if from_agent not in _EXTRACT_FROM:
        return None

    if not content or len(content.strip()) < 20:
        return None

    # Determine category from context
    category = _classify_message(from_agent, content, thread_type, subject)
    if not category:
        return None

    return {
        "category": category,
        "content": _distill_content(content),
    }


def _classify_message(
    from_agent: str,
    content: str,
    thread_type: str,
    subject: str,
) -> str | None:
    """Classify message into bead category."""
    subject_lower = subject.lower()
    content_lower = content.lower()
    type_lower = thread_type.lower()

    # Spec threads → decision
    if "spec" in type_lower or "spec" in subject_lower:
        return "decision"

    # Architecture threads → pattern
    if "architecture" in type_lower or "design" in type_lower:
        return "pattern"

    # Review threads → learning
    if from_agent == "Reviewer" or "review" in type_lower:
        return "learning"

    # Bug-related → fix
    if from_agent in ("QA", "InfoSec") and "bug" in content_lower:
        return "fix"

    # Decision language in content
    decision_markers = [
        "decided to", "decision:", "we chose", "approach:",
        "trade-off", "rationale",
    ]
    if any(m in content_lower for m in decision_markers):
        return "decision"

    # Pattern language
    pattern_markers = ["pattern:", "convention:", "always", "never"]
    if any(m in content_lower for m in pattern_markers):
        return "pattern"

    return None


def _distill_content(content: str) -> str:
    """Trim content to essential information."""
    lines = content.strip().split("\n")
    meaningful = [
        line for line in lines
        if line.strip() and not line.strip().startswith("---")
    ]
    return "\n".join(meaningful[:20])


def cleanup_em_db(project: str, days_old: int = 30) -> dict:
    """Clean up em.db after bead extraction.

    Called after extract_beads_from_project. Archives old data.
    Returns summary of what was cleaned.
    """
    with em_db(project) as conn:
        # Archive old messages
        archived = conn.execute(
            "INSERT INTO mail_archive (id, original_id, thread_id, project_id, "
            "from_agent, to_agent, subject, body, created_at, archived_at) "
            "SELECT id, id, thread_id, project_id, from_agent, to_agent, "
            "subject, body, created_at, datetime('now') FROM mail_messages "
            f"WHERE created_at < datetime('now', '-{days_old} days')"
        ).rowcount

        # Delete archived messages
        deleted = conn.execute(
            f"DELETE FROM mail_messages "
            f"WHERE created_at < datetime('now', '-{days_old} days')"
        ).rowcount

    return {"archived": archived, "deleted": deleted}
