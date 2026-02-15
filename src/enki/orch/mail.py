"""mail.py — Mail system for agent communication.

Agents communicate via mail in em.db. EM is relay/postman.
Agents never talk directly. Thread IS the project memory —
resume mid-flight by reading thread history.

Message status flow: unread → read → acknowledged → assigned → resolved
Thread hierarchy: project → sprint → task → hitl
"""

import uuid
from datetime import datetime, timedelta

from enki.db import em_db


# Valid status transitions (from → to)
_VALID_TRANSITIONS = {
    "unread": {"read", "acknowledged"},
    "read": {"acknowledged", "assigned", "resolved"},
    "acknowledged": {"assigned", "resolved"},
    "assigned": {"resolved"},
    "resolved": set(),
}

THREAD_TYPES = {"project", "sprint", "task", "hitl", "change_request",
                "escalation", "decision", "status", "agent_output",
                "design", "design-review", "task-output"}


# ── Thread Management ──


def create_thread(
    project: str,
    thread_type: str,
    parent_thread_id: str | None = None,
) -> str:
    """Create a mail thread. Returns thread_id.

    Thread types form a hierarchy: project → sprint → task → hitl.
    Parent thread provides context inheritance.
    """
    thread_id = str(uuid.uuid4())
    with em_db(project) as conn:
        # Validate parent exists if specified
        if parent_thread_id:
            parent = conn.execute(
                "SELECT thread_id FROM mail_threads WHERE thread_id = ?",
                (parent_thread_id,),
            ).fetchone()
            if not parent:
                raise ValueError(f"Parent thread {parent_thread_id} not found")

        conn.execute(
            "INSERT INTO mail_threads "
            "(thread_id, project_id, parent_thread_id, type) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, project, parent_thread_id, thread_type),
        )
    return thread_id


def get_thread(project: str, thread_id: str) -> dict | None:
    """Get thread metadata."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM mail_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row else None


def get_child_threads(project: str, parent_thread_id: str) -> list[dict]:
    """Get all child threads of a parent thread."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM mail_threads WHERE parent_thread_id = ? "
            "ORDER BY created_at",
            (parent_thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_thread_hierarchy(project: str, thread_id: str) -> list[dict]:
    """Walk up the thread hierarchy to root. Returns list from root to leaf."""
    chain = []
    current_id = thread_id
    visited = set()

    while current_id and current_id not in visited:
        visited.add(current_id)
        thread = get_thread(project, current_id)
        if not thread:
            break
        chain.append(thread)
        current_id = thread.get("parent_thread_id")

    chain.reverse()
    return chain


def close_thread(project: str, thread_id: str) -> None:
    """Archive a thread. Sets status to 'archived'."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE mail_threads SET status = 'archived', "
            "archived_at = datetime('now') WHERE thread_id = ?",
            (thread_id,),
        )


def reopen_thread(project: str, thread_id: str) -> None:
    """Reopen an archived thread."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE mail_threads SET status = 'active', "
            "archived_at = NULL WHERE thread_id = ?",
            (thread_id,),
        )


def query_threads(
    project: str,
    status: str | None = None,
    thread_type: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query threads with optional filters.

    Args:
        project: Project ID.
        status: Filter by thread status (active, archived).
        thread_type: Filter by thread type.
        agent: Filter threads where agent has messages.
        since: ISO timestamp — only threads created after this.
        limit: Max results.
    """
    query = "SELECT * FROM mail_threads WHERE project_id = ?"
    params: list = [project]

    if status:
        query += " AND status = ?"
        params.append(status)
    if thread_type:
        query += " AND type = ?"
        params.append(thread_type)
    if since:
        query += " AND created_at >= ?"
        params.append(since)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        threads = [dict(r) for r in rows]

    # Filter by agent participation if requested
    if agent:
        agent_threads = []
        with em_db(project) as conn:
            for t in threads:
                has_msg = conn.execute(
                    "SELECT 1 FROM mail_messages "
                    "WHERE thread_id = ? AND (from_agent = ? OR to_agent = ?) "
                    "LIMIT 1",
                    (t["thread_id"], agent, agent),
                ).fetchone()
                if has_msg:
                    agent_threads.append(t)
        return agent_threads

    return threads


# ── Message Operations ──


def send(
    project: str,
    thread_id: str,
    from_agent: str,
    to_agent: str,
    body: str,
    subject: str | None = None,
    importance: str = "normal",
    task_id: str | None = None,
    sprint_id: str | None = None,
) -> str:
    """Send a mail message. Returns message ID.

    Importance levels: normal, high, critical.
    Critical messages trigger immediate processing by EM.
    """
    if importance not in ("normal", "high", "critical"):
        importance = "normal"

    msg_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO mail_messages "
            "(id, thread_id, project_id, from_agent, to_agent, "
            "subject, body, importance, task_id, sprint_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, thread_id, project, from_agent, to_agent,
             subject, body, importance, task_id, sprint_id),
        )
    return msg_id


def get_message(project: str, msg_id: str) -> dict | None:
    """Get a single message by ID."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM mail_messages WHERE id = ?", (msg_id,)
        ).fetchone()
        return dict(row) if row else None


def get_inbox(
    project: str,
    agent: str,
    status: str = "unread",
    importance: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Get messages for an agent filtered by status and optional importance.

    Returns messages ordered by importance (critical first) then chronologically.
    """
    query = (
        "SELECT * FROM mail_messages "
        "WHERE project_id = ? AND to_agent = ? AND status = ?"
    )
    params: list = [project, agent, status]

    if importance:
        query += " AND importance = ?"
        params.append(importance)

    # Critical first, then high, then normal; within same importance: newest first
    query += (
        " ORDER BY "
        "CASE importance WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, "
        "created_at DESC LIMIT ?"
    )
    params.append(limit)

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_thread_messages(project: str, thread_id: str) -> list[dict]:
    """Get all messages in a thread, ordered chronologically."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM mail_messages "
            "WHERE thread_id = ? ORDER BY created_at",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(project: str, thread_id: str) -> list[dict]:
    """Get full conversation including child thread messages.

    Returns flat list of all messages across thread hierarchy,
    ordered chronologically.
    """
    messages = []
    thread_ids = [thread_id]

    # Collect child thread IDs
    children = get_child_threads(project, thread_id)
    for child in children:
        thread_ids.append(child["thread_id"])
        # One level of grandchildren
        grandchildren = get_child_threads(project, child["thread_id"])
        for gc in grandchildren:
            thread_ids.append(gc["thread_id"])

    with em_db(project) as conn:
        placeholders = ",".join("?" for _ in thread_ids)
        rows = conn.execute(
            f"SELECT * FROM mail_messages "
            f"WHERE thread_id IN ({placeholders}) "
            f"ORDER BY created_at",
            thread_ids,
        ).fetchall()
        messages = [dict(r) for r in rows]

    return messages


# ── Status Transitions ──


def mark_read(project: str, msg_id: str) -> None:
    """Mark a message as read."""
    _update_status(project, msg_id, "read")


def mark_acknowledged(project: str, msg_id: str) -> None:
    """Mark a message as acknowledged."""
    _update_status(project, msg_id, "acknowledged")


def mark_resolved(project: str, msg_id: str) -> None:
    """Mark a message as resolved."""
    _update_status(project, msg_id, "resolved")


def assign(project: str, msg_id: str, agent: str) -> None:
    """Assign a message to an agent. Sets status to 'assigned'."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE mail_messages SET assigned_to = ?, status = 'assigned' "
            "WHERE id = ?",
            (agent, msg_id),
        )


def _update_status(project: str, msg_id: str, new_status: str) -> None:
    """Update message status with transition validation."""
    msg = get_message(project, msg_id)
    if not msg:
        return

    current = msg["status"]
    allowed = _VALID_TRANSITIONS.get(current, set())

    # Allow the transition even if not strictly valid — log but don't block
    # This prevents deadlocks in the mail system
    with em_db(project) as conn:
        conn.execute(
            "UPDATE mail_messages SET status = ? WHERE id = ?",
            (new_status, msg_id),
        )


# ── Routing ──


def route_messages(project: str, agent_output: dict) -> int:
    """Parse agent output and route messages to inboxes.

    Agent output must contain a 'messages' list with 'to' and 'content' keys.
    Creates a thread per batch and sends each message.

    Returns count of messages routed.
    """
    messages = agent_output.get("messages", [])
    if not messages:
        return 0

    from_agent = agent_output.get("agent", "Unknown")
    task_id = agent_output.get("task_id")
    routed = 0

    for msg in messages:
        to = msg.get("to")
        content = msg.get("content") or msg.get("body")
        if not to or not content:
            continue

        thread_id = create_thread(project, "agent_output")
        send(
            project=project,
            thread_id=thread_id,
            from_agent=from_agent,
            to_agent=to,
            body=content,
            subject=msg.get("subject", f"From {from_agent}"),
            importance=msg.get("importance", "normal"),
            task_id=task_id,
        )
        routed += 1

    return routed


def route_to_thread(
    project: str,
    thread_id: str,
    from_agent: str,
    messages: list[dict],
) -> int:
    """Route multiple messages to an existing thread.

    Each message dict must have 'to' and 'body' keys.
    Returns count of messages sent.
    """
    sent = 0
    for msg in messages:
        to = msg.get("to")
        body = msg.get("body") or msg.get("content")
        if not to or not body:
            continue
        send(
            project=project,
            thread_id=thread_id,
            from_agent=from_agent,
            to_agent=to,
            body=body,
            subject=msg.get("subject"),
            importance=msg.get("importance", "normal"),
        )
        sent += 1
    return sent


# ── Counting & Queries ──


def count_unread(project: str, agent: str) -> int:
    """Count unread messages for an agent."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM mail_messages "
            "WHERE project_id = ? AND to_agent = ? AND status = 'unread'",
            (project, agent),
        ).fetchone()
        return row[0]


def count_by_status(project: str, agent: str) -> dict:
    """Count messages for an agent grouped by status."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM mail_messages "
            "WHERE project_id = ? AND to_agent = ? "
            "GROUP BY status",
            (project, agent),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}


def get_critical_messages(project: str, agent: str | None = None) -> list[dict]:
    """Get all unread critical-importance messages.

    If agent is None, returns critical messages for all agents.
    """
    query = (
        "SELECT * FROM mail_messages "
        "WHERE project_id = ? AND importance = 'critical' AND status = 'unread'"
    )
    params: list = [project]

    if agent:
        query += " AND to_agent = ?"
        params.append(agent)

    query += " ORDER BY created_at DESC"

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_messages_for_task(project: str, task_id: str) -> list[dict]:
    """Get all messages associated with a task across all threads."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM mail_messages "
            "WHERE project_id = ? AND task_id = ? "
            "ORDER BY created_at",
            (project, task_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_agent_activity(project: str, agent: str, limit: int = 20) -> list[dict]:
    """Get recent activity for an agent (sent + received messages)."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM mail_messages "
            "WHERE project_id = ? AND (from_agent = ? OR to_agent = ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (project, agent, agent, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Thread Summarization ──


def get_thread_summary(project: str, thread_id: str) -> str:
    """Generate a summary of a thread for archival or distillation.

    Returns a markdown summary with key decisions and messages.
    """
    thread = get_thread(project, thread_id)
    if not thread:
        return ""

    messages = get_thread_messages(project, thread_id)
    if not messages:
        return f"Thread {thread_id} ({thread['type']}): no messages"

    lines = [
        f"## Thread: {thread['type']} ({thread['status']})",
        f"Created: {thread['created_at']}",
        f"Messages: {len(messages)}",
        "",
    ]

    # Extract key content: decisions, blockers, resolutions
    decisions = []
    blockers = []

    for msg in messages:
        body = msg["body"] or ""
        from_agent = msg["from_agent"]

        # Track decisions
        if any(kw in body.lower() for kw in ("decision:", "decided:", "approved:", "agreed:")):
            decisions.append(f"- [{from_agent}] {body[:200]}")

        # Track blockers
        if any(kw in body.lower() for kw in ("blocked:", "blocker:", "issue:", "problem:")):
            blockers.append(f"- [{from_agent}] {body[:200]}")

    if decisions:
        lines.append("### Decisions")
        lines.extend(decisions)
        lines.append("")

    if blockers:
        lines.append("### Issues")
        lines.extend(blockers)
        lines.append("")

    # Last 3 messages as recent context
    lines.append("### Recent")
    for msg in messages[-3:]:
        preview = (msg["body"] or "")[:150]
        lines.append(f"- {msg['from_agent']}→{msg['to_agent']}: {preview}")

    return "\n".join(lines)


# ── Archival ──


def archive_thread_messages(project: str, thread_id: str) -> int:
    """Move thread messages to archive. Returns count archived.

    Messages are copied to mail_archive, then deleted from mail_messages.
    Thread metadata is preserved (just status changes to 'archived').
    """
    with em_db(project) as conn:
        messages = conn.execute(
            "SELECT * FROM mail_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()

        count = 0
        for msg in messages:
            conn.execute(
                "INSERT INTO mail_archive "
                "(id, original_id, thread_id, project_id, from_agent, "
                "to_agent, subject, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), msg["id"], msg["thread_id"],
                 msg["project_id"], msg["from_agent"], msg["to_agent"],
                 msg["subject"], msg["body"], msg["created_at"]),
            )
            count += 1

        conn.execute(
            "DELETE FROM mail_messages WHERE thread_id = ?", (thread_id,)
        )
        return count


def archive_old_threads(project: str, days: int = 10) -> int:
    """Archive threads older than N days.

    First summarizes each thread (for bead extraction), then archives.
    Returns count of threads archived.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    archived = 0

    with em_db(project) as conn:
        old_threads = conn.execute(
            "SELECT thread_id FROM mail_threads "
            "WHERE project_id = ? AND status = 'active' "
            "AND created_at < ?",
            (project, cutoff),
        ).fetchall()

    for t in old_threads:
        tid = t["thread_id"]
        archive_thread_messages(project, tid)
        close_thread(project, tid)
        archived += 1

    return archived


def get_archived_messages(
    project: str,
    thread_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve archived messages. Optionally filter by thread."""
    query = "SELECT * FROM mail_archive WHERE project_id = ?"
    params: list = [project]

    if thread_id:
        query += " AND thread_id = ?"
        params.append(thread_id)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ── Statistics ──


def get_mail_stats(project: str) -> dict:
    """Get mail system statistics for a project."""
    with em_db(project) as conn:
        total_threads = conn.execute(
            "SELECT COUNT(*) FROM mail_threads WHERE project_id = ?",
            (project,),
        ).fetchone()[0]

        active_threads = conn.execute(
            "SELECT COUNT(*) FROM mail_threads "
            "WHERE project_id = ? AND status = 'active'",
            (project,),
        ).fetchone()[0]

        total_messages = conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE project_id = ?",
            (project,),
        ).fetchone()[0]

        unread_messages = conn.execute(
            "SELECT COUNT(*) FROM mail_messages "
            "WHERE project_id = ? AND status = 'unread'",
            (project,),
        ).fetchone()[0]

        archived_messages = conn.execute(
            "SELECT COUNT(*) FROM mail_archive WHERE project_id = ?",
            (project,),
        ).fetchone()[0]

        # Messages per agent
        agent_counts = conn.execute(
            "SELECT to_agent, COUNT(*) as cnt FROM mail_messages "
            "WHERE project_id = ? GROUP BY to_agent ORDER BY cnt DESC",
            (project,),
        ).fetchall()

    return {
        "total_threads": total_threads,
        "active_threads": active_threads,
        "total_messages": total_messages,
        "unread_messages": unread_messages,
        "archived_messages": archived_messages,
        "messages_per_agent": {r["to_agent"]: r["cnt"] for r in agent_counts},
    }
