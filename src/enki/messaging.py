"""Agent messaging infrastructure.

Provides send/receive messages and file claim management.
This module is role-agnostic — it doesn't know about PM/EM.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Literal
from dataclasses import dataclass

from .db import get_db
from .beads import create_bead

Importance = Literal["low", "normal", "high", "critical"]


@dataclass
class Message:
    id: str
    from_agent: str
    to_agent: str
    subject: str
    body: str
    importance: Importance
    thread_id: Optional[str]
    session_id: str
    created_at: datetime
    read_at: Optional[datetime]


@dataclass
class FileClaim:
    file_path: str
    agent_id: str
    session_id: str
    claimed_at: datetime
    released_at: Optional[datetime]


def register_agent(agent_id: str, role: str, session_id: str) -> None:
    """Register an agent for the current session."""
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO agents (id, role, session_id, status)
           VALUES (?, ?, ?, 'active')""",
        (agent_id, role, session_id),
    )
    db.commit()


def send_message(
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    session_id: str,
    importance: Importance = "normal",
    thread_id: Optional[str] = None,
) -> Message:
    """Send a message. Messages are append-only (never edited or deleted)."""
    msg_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        """INSERT INTO messages (id, from_agent, to_agent, subject, body,
           importance, thread_id, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (msg_id, from_agent, to_agent, subject, body,
         importance, thread_id or msg_id, session_id),
    )
    db.commit()

    # Auto-record critical/high messages as beads (G-10 support)
    if importance in ("critical", "high"):
        create_bead(
            content=f"Agent message [{importance}]: {from_agent} → {to_agent}: {subject}\n{body[:500]}",
            bead_type="decision" if importance == "critical" else "learning",
            kind="decision" if importance == "critical" else "fact",
            summary=f"{from_agent} → {to_agent}: {subject}",
            tags=["agent_message", from_agent, to_agent],
        )

    return Message(
        id=msg_id, from_agent=from_agent, to_agent=to_agent,
        subject=subject, body=body, importance=importance,
        thread_id=thread_id or msg_id, session_id=session_id,
        created_at=datetime.now(timezone.utc), read_at=None,
    )


def get_messages(
    agent_id: str,
    session_id: str,
    unread_only: bool = False,
    limit: int = 20,
) -> list[Message]:
    """Get messages for an agent. Marks returned messages as read."""
    db = get_db()
    query = """
        SELECT * FROM messages
        WHERE to_agent = ? AND session_id = ?
    """
    params: list = [agent_id, session_id]
    if unread_only:
        query += " AND read_at IS NULL"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    messages = []
    for row in rows:
        if row["read_at"] is None:
            db.execute(
                "UPDATE messages SET read_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
        messages.append(Message(
            id=row["id"], from_agent=row["from_agent"],
            to_agent=row["to_agent"], subject=row["subject"],
            body=row["body"], importance=row["importance"],
            thread_id=row["thread_id"], session_id=row["session_id"],
            created_at=row["created_at"], read_at=row["read_at"],
        ))
    db.commit()
    return messages


def claim_file(agent_id: str, file_path: str, session_id: str) -> bool:
    """Claim a file for exclusive editing. Returns False if already claimed."""
    db = get_db()
    existing = db.execute(
        """SELECT agent_id FROM file_claims
           WHERE file_path = ? AND session_id = ? AND released_at IS NULL""",
        (file_path, session_id),
    ).fetchone()

    if existing and existing["agent_id"] != agent_id:
        return False  # Another agent holds the claim

    if existing and existing["agent_id"] == agent_id:
        return True  # Already claimed by this agent

    db.execute(
        """INSERT INTO file_claims (file_path, agent_id, session_id)
           VALUES (?, ?, ?)""",
        (file_path, agent_id, session_id),
    )
    db.commit()
    return True


def release_file(agent_id: str, file_path: str, session_id: str) -> None:
    """Release a file claim."""
    db = get_db()
    db.execute(
        """UPDATE file_claims SET released_at = CURRENT_TIMESTAMP
           WHERE file_path = ? AND agent_id = ? AND session_id = ? AND released_at IS NULL""",
        (file_path, agent_id, session_id),
    )
    db.commit()


def get_file_owner(file_path: str, session_id: str) -> Optional[str]:
    """Get the agent that currently owns a file. None if unclaimed."""
    db = get_db()
    row = db.execute(
        """SELECT agent_id FROM file_claims
           WHERE file_path = ? AND session_id = ? AND released_at IS NULL""",
        (file_path, session_id),
    ).fetchone()
    return row["agent_id"] if row else None
