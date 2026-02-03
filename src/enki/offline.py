"""Offline mode handling for Enki.

Manages local bead cache, embedding cache, and sync queue
for offline operation. Handles connection state tracking
and sync operations.
"""

import json
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import numpy as np

from .db import get_db, init_db

# Connection state
class ConnectionState(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    SYNCING = "syncing"


# Global state
_connection_state = ConnectionState.OFFLINE
_state_lock = threading.Lock()

# Sync configuration
MAX_RETRIES = 5
BACKOFF_BASE = 1  # seconds
CACHE_TTL_DAYS = 7
CURRENT_MODEL = "all-MiniLM-L6-v2"


@dataclass
class SyncOperation:
    """Queued operation for offline sync."""
    id: int
    operation: str
    payload: dict
    created_at: datetime
    last_retry: Optional[datetime]
    retry_count: int
    status: str


@dataclass
class CachedBead:
    """Locally cached bead."""
    id: str
    content: str
    summary: Optional[str]
    type: str
    project: Optional[str]
    weight: float
    starred: bool
    tags: Optional[list[str]]
    cached_at: datetime
    server_version: int


# --- Connection State ---

def get_connection_state() -> ConnectionState:
    """Get current connection state."""
    with _state_lock:
        return _connection_state


def set_connection_state(state: ConnectionState) -> None:
    """Set connection state."""
    global _connection_state
    with _state_lock:
        _connection_state = state


def is_online() -> bool:
    """Check if currently online."""
    return get_connection_state() == ConnectionState.ONLINE


def is_offline() -> bool:
    """Check if currently offline."""
    return get_connection_state() == ConnectionState.OFFLINE


# --- Bead Cache ---

def cache_bead(
    bead_id: str,
    content: str,
    bead_type: str,
    summary: Optional[str] = None,
    project: Optional[str] = None,
    weight: float = 1.0,
    starred: bool = False,
    tags: Optional[list[str]] = None,
    server_version: int = 1,
    embedding: Optional[list[float]] = None,
) -> CachedBead:
    """Cache a bead locally.

    Args:
        bead_id: Unique bead identifier
        content: Bead content
        bead_type: Type of bead
        summary: Optional summary
        project: Optional project identifier
        weight: Retention weight
        starred: Whether bead is starred
        tags: Optional tags list
        server_version: Version from server
        embedding: Optional embedding vector

    Returns:
        CachedBead object
    """
    init_db()
    conn = get_db()

    now = datetime.now(timezone.utc)

    conn.execute("""
        INSERT OR REPLACE INTO bead_cache
        (id, content, summary, type, project, weight, starred, tags, cached_at, server_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        bead_id,
        content,
        summary,
        bead_type,
        project,
        weight,
        1 if starred else 0,
        json.dumps(tags) if tags else None,
        now.isoformat(),
        server_version,
    ))

    # Cache embedding if provided
    if embedding:
        vector_bytes = struct.pack(f'{len(embedding)}f', *embedding)
        conn.execute("""
            INSERT OR REPLACE INTO embedding_cache (bead_id, vector, model)
            VALUES (?, ?, ?)
        """, (bead_id, vector_bytes, CURRENT_MODEL))

    conn.commit()

    return CachedBead(
        id=bead_id,
        content=content,
        summary=summary,
        type=bead_type,
        project=project,
        weight=weight,
        starred=starred,
        tags=tags,
        cached_at=now,
        server_version=server_version,
    )


def get_cached_bead(bead_id: str) -> Optional[CachedBead]:
    """Get a cached bead by ID."""
    init_db()
    conn = get_db()

    row = conn.execute("""
        SELECT * FROM bead_cache WHERE id = ?
    """, (bead_id,)).fetchone()

    if not row:
        return None

    return _row_to_cached_bead(row)


def get_all_cached_beads(project: Optional[str] = None) -> list[CachedBead]:
    """Get all cached beads, optionally filtered by project."""
    init_db()
    conn = get_db()

    if project:
        rows = conn.execute("""
            SELECT * FROM bead_cache WHERE project = ?
        """, (project,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM bead_cache").fetchall()

    return [_row_to_cached_bead(row) for row in rows]


def _row_to_cached_bead(row) -> CachedBead:
    """Convert database row to CachedBead."""
    tags = None
    if row["tags"]:
        try:
            tags = json.loads(row["tags"])
        except json.JSONDecodeError:
            pass

    cached_at = row["cached_at"]
    if isinstance(cached_at, str):
        cached_at = datetime.fromisoformat(cached_at)

    return CachedBead(
        id=row["id"],
        content=row["content"],
        summary=row["summary"],
        type=row["type"],
        project=row["project"],
        weight=row["weight"],
        starred=bool(row["starred"]),
        tags=tags,
        cached_at=cached_at,
        server_version=row["server_version"],
    )


def search_cached_beads(
    query_embedding: list[float],
    project: Optional[str] = None,
    bead_type: Optional[str] = None,
    limit: int = 10,
) -> list[tuple[CachedBead, float]]:
    """Search cached beads using vector similarity.

    Args:
        query_embedding: Query embedding vector
        project: Optional project filter
        bead_type: Optional type filter
        limit: Maximum results

    Returns:
        List of (CachedBead, score) tuples sorted by score descending
    """
    init_db()
    conn = get_db()

    query_vec = np.array(query_embedding)

    # Get all beads with embeddings
    sql = """
        SELECT bc.*, ec.vector
        FROM bead_cache bc
        JOIN embedding_cache ec ON bc.id = ec.bead_id
        WHERE 1=1
    """
    params = []

    if project:
        sql += " AND bc.project = ?"
        params.append(project)
    if bead_type:
        sql += " AND bc.type = ?"
        params.append(bead_type)

    rows = conn.execute(sql, params).fetchall()

    # Compute similarities
    results = []
    for row in rows:
        vec = np.array(struct.unpack(f'{len(row["vector"])//4}f', row["vector"]))
        score = _cosine_similarity(query_vec, vec)
        bead = _row_to_cached_bead(row)
        results.append((bead, score))

    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def get_cache_count() -> int:
    """Get count of cached beads."""
    init_db()
    conn = get_db()
    return conn.execute("SELECT COUNT(*) FROM bead_cache").fetchone()[0]


def invalidate_stale_embeddings() -> int:
    """Remove embeddings from wrong model version.

    Returns:
        Number of embeddings invalidated
    """
    init_db()
    conn = get_db()

    result = conn.execute("""
        DELETE FROM embedding_cache WHERE model != ?
    """, (CURRENT_MODEL,))
    conn.commit()

    return result.rowcount


def refresh_stale_cache(ttl_days: int = CACHE_TTL_DAYS) -> list[str]:
    """Get IDs of beads that should be refreshed from server.

    Args:
        ttl_days: Cache entries older than this are stale

    Returns:
        List of stale bead IDs
    """
    init_db()
    conn = get_db()

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

    rows = conn.execute("""
        SELECT id FROM bead_cache WHERE cached_at < ?
    """, (cutoff.isoformat(),)).fetchall()

    return [row["id"] for row in rows]


# --- Sync Queue ---

def queue_operation(
    operation: str,
    payload: dict,
) -> SyncOperation:
    """Queue an operation for later sync.

    Args:
        operation: Operation type ('remember', 'star', 'supersede', 'goal', 'phase')
        payload: Operation data as dict

    Returns:
        SyncOperation object
    """
    init_db()
    conn = get_db()

    now = datetime.now(timezone.utc)

    cursor = conn.execute("""
        INSERT INTO sync_queue (operation, payload, created_at, status)
        VALUES (?, ?, ?, 'pending')
    """, (operation, json.dumps(payload), now.isoformat()))
    conn.commit()

    return SyncOperation(
        id=cursor.lastrowid,
        operation=operation,
        payload=payload,
        created_at=now,
        last_retry=None,
        retry_count=0,
        status="pending",
    )


def get_pending_operations() -> list[SyncOperation]:
    """Get all pending operations in queue order (oldest first)."""
    init_db()
    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM sync_queue
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """).fetchall()

    return [_row_to_sync_op(row) for row in rows]


def get_failed_operations() -> list[SyncOperation]:
    """Get all failed operations."""
    init_db()
    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM sync_queue
        WHERE status = 'failed'
        ORDER BY created_at ASC
    """).fetchall()

    return [_row_to_sync_op(row) for row in rows]


def _row_to_sync_op(row) -> SyncOperation:
    """Convert database row to SyncOperation."""
    created_at = row["created_at"]
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)

    last_retry = row["last_retry"]
    if isinstance(last_retry, str):
        last_retry = datetime.fromisoformat(last_retry)

    return SyncOperation(
        id=row["id"],
        operation=row["operation"],
        payload=json.loads(row["payload"]),
        created_at=created_at,
        last_retry=last_retry,
        retry_count=row["retry_count"],
        status=row["status"],
    )


def mark_operation_syncing(op_id: int) -> None:
    """Mark an operation as currently syncing."""
    init_db()
    conn = get_db()
    conn.execute("""
        UPDATE sync_queue SET status = 'syncing' WHERE id = ?
    """, (op_id,))
    conn.commit()


def mark_operation_complete(op_id: int) -> None:
    """Mark an operation as complete and remove from queue."""
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM sync_queue WHERE id = ?", (op_id,))
    conn.commit()


def mark_operation_failed(op_id: int, increment_retry: bool = True) -> None:
    """Mark an operation as failed.

    Args:
        op_id: Operation ID
        increment_retry: Whether to increment retry count
    """
    init_db()
    conn = get_db()

    now = datetime.now(timezone.utc)

    if increment_retry:
        conn.execute("""
            UPDATE sync_queue
            SET status = CASE
                    WHEN retry_count + 1 >= ? THEN 'failed'
                    ELSE 'pending'
                END,
                retry_count = retry_count + 1,
                last_retry = ?
            WHERE id = ?
        """, (MAX_RETRIES, now.isoformat(), op_id))
    else:
        conn.execute("""
            UPDATE sync_queue SET status = 'pending', last_retry = ?
            WHERE id = ?
        """, (now.isoformat(), op_id))

    conn.commit()


def get_queue_size() -> int:
    """Get count of pending operations."""
    init_db()
    conn = get_db()
    return conn.execute("""
        SELECT COUNT(*) FROM sync_queue WHERE status IN ('pending', 'syncing')
    """).fetchone()[0]


def should_retry(op: SyncOperation) -> bool:
    """Check if an operation should be retried now.

    Uses exponential backoff.
    """
    if op.retry_count >= MAX_RETRIES:
        return False

    if op.last_retry:
        now = datetime.now(timezone.utc)
        # Handle naive datetime
        last_retry = op.last_retry
        if last_retry.tzinfo is None:
            last_retry = last_retry.replace(tzinfo=timezone.utc)

        backoff = get_backoff(op.retry_count)
        elapsed = (now - last_retry).total_seconds()
        if elapsed < backoff:
            return False

    return True


def get_backoff(retry_count: int) -> float:
    """Calculate exponential backoff delay in seconds.

    1s, 2s, 4s, 8s, 16s (max 60s)
    """
    return min(BACKOFF_BASE * (2 ** retry_count), 60)


# --- Sync Status ---

@dataclass
class SyncStatus:
    """Current sync status."""
    state: ConnectionState
    pending_count: int
    failed_count: int
    cached_beads: int
    last_sync: Optional[datetime]


_last_sync: Optional[datetime] = None


def get_sync_status() -> SyncStatus:
    """Get current sync status."""
    return SyncStatus(
        state=get_connection_state(),
        pending_count=get_queue_size(),
        failed_count=len(get_failed_operations()),
        cached_beads=get_cache_count(),
        last_sync=_last_sync,
    )


def update_last_sync() -> None:
    """Update last sync timestamp."""
    global _last_sync
    _last_sync = datetime.now(timezone.utc)
