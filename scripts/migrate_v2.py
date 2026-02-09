#!/usr/bin/env python3
"""Enki v2 Schema Migration

Run ONCE before any v2 spec implementation.

Changes:
  1. Expand beads.type CHECK constraint (add: style, approach, rejection)
  2. Add beads.kind column (fact, preference, pattern, decision)
  3. Add beads.archived_at column
  4. Backfill kind from type for existing beads
  5. Rebuild FTS5 index and triggers
  6. Add new indexes (kind, archived_at)
  7. Create agent messaging tables (agents, messages, file_claims)

Usage:
    python scripts/migrate_v2.py [--db-path /path/to/wisdom.db]
"""

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path


def get_default_db_path() -> Path:
    import os
    env_path = os.environ.get("ENKI_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path.home() / ".enki" / "wisdom.db"


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # Needed for table recreation
    conn.execute("PRAGMA journal_mode = WAL")

    # Check if migration already applied
    columns = [row[1] for row in conn.execute("PRAGMA table_info(beads)").fetchall()]
    if "kind" in columns and "archived_at" in columns:
        print("Migration already applied (kind and archived_at columns exist). Verifying...")
        verify(conn)
        conn.close()
        return

    print(f"Migrating {db_path}...")

    bead_count = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
    print(f"  Beads to migrate: {bead_count}")

    # === Step 1: Recreate beads table with expanded schema ===
    print("  Step 1: Recreating beads table...")

    conn.execute("DROP TRIGGER IF EXISTS beads_ai")
    conn.execute("DROP TRIGGER IF EXISTS beads_ad")
    conn.execute("DROP TRIGGER IF EXISTS beads_au")

    conn.execute("ALTER TABLE beads RENAME TO beads_old")

    conn.executescript("""
        CREATE TABLE beads (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,

            -- WHAT was learned (Memory v2 expansion)
            type TEXT NOT NULL CHECK (type IN (
                'decision', 'solution', 'learning', 'violation', 'pattern',
                'style', 'approach', 'rejection'
            )),

            -- HOW it behaves in lifecycle (Proactive)
            kind TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN (
                'fact', 'preference', 'pattern', 'decision'
            )),

            project TEXT,

            -- Retention
            weight REAL DEFAULT 1.0,
            starred INTEGER DEFAULT 0,
            superseded_by TEXT,

            -- Proactive: archival + dedup
            archived_at TIMESTAMP,
            content_hash TEXT,

            -- Context
            context TEXT,
            tags TEXT,  -- JSON array

            -- Timestamps
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,

            FOREIGN KEY (superseded_by) REFERENCES beads(id)
        );
    """)

    # === Step 2: Copy data with kind backfill ===
    print("  Step 2: Copying data with kind backfill...")

    conn.execute("""
        INSERT INTO beads (
            id, content, summary, type, kind, project,
            weight, starred, superseded_by,
            archived_at, content_hash,
            context, tags, created_at, last_accessed
        )
        SELECT
            id, content, summary, type,
            -- Backfill kind based on type
            CASE
                WHEN type = 'pattern' THEN 'pattern'
                WHEN type = 'decision' THEN 'decision'
                ELSE 'fact'
            END,
            project,
            weight, starred, superseded_by,
            NULL,  -- archived_at (none archived yet)
            content_hash,
            context, tags, created_at, last_accessed
        FROM beads_old
    """)

    migrated = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
    print(f"  Migrated {migrated} beads")

    conn.execute("DROP TABLE beads_old")

    # === Step 3: Rebuild FTS5 ===
    print("  Step 3: Rebuilding FTS5 index...")

    conn.execute("DROP TABLE IF EXISTS beads_fts")
    conn.executescript("""
        CREATE VIRTUAL TABLE beads_fts USING fts5(
            content,
            summary,
            tags,
            content='beads',
            content_rowid='rowid'
        );

        INSERT INTO beads_fts(rowid, content, summary, tags)
            SELECT rowid, content, summary, tags FROM beads;

        -- Recreate FTS sync triggers
        CREATE TRIGGER beads_ai AFTER INSERT ON beads BEGIN
            INSERT INTO beads_fts(rowid, content, summary, tags)
            VALUES (new.rowid, new.content, new.summary, new.tags);
        END;

        CREATE TRIGGER beads_ad AFTER DELETE ON beads BEGIN
            INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
            VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
        END;

        CREATE TRIGGER beads_au AFTER UPDATE ON beads BEGIN
            INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
            VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
            INSERT INTO beads_fts(rowid, content, summary, tags)
            VALUES (new.rowid, new.content, new.summary, new.tags);
        END;
    """)

    # === Step 4: Indexes ===
    print("  Step 4: Creating indexes...")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_kind ON beads(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_archived ON beads(archived_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_content_hash ON beads(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_project ON beads(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_created ON beads(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_beads_weight ON beads(weight)")

    # === Step 5: Backfill content_hash for any nulls ===
    print("  Step 5: Backfilling content_hash...")

    null_hashes = conn.execute(
        "SELECT id, content FROM beads WHERE content_hash IS NULL"
    ).fetchall()
    for row in null_hashes:
        h = hashlib.sha256(row[1].encode()).hexdigest()
        conn.execute("UPDATE beads SET content_hash = ? WHERE id = ?", (h, row[0]))
    print(f"  Backfilled {len(null_hashes)} content hashes")

    # === Step 6: Agent Messaging tables ===
    print("  Step 6: Creating agent messaging tables...")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            importance TEXT DEFAULT 'normal' CHECK (importance IN ('low', 'normal', 'high', 'critical')),
            thread_id TEXT,
            session_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            FOREIGN KEY (from_agent) REFERENCES agents(id),
            FOREIGN KEY (to_agent) REFERENCES agents(id)
        );

        CREATE TABLE IF NOT EXISTS file_claims (
            file_path TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            released_at TIMESTAMP,
            PRIMARY KEY (file_path, agent_id, session_id),
            FOREIGN KEY (agent_id) REFERENCES agents(id)
        );

        CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, read_at);
        CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
        CREATE INDEX IF NOT EXISTS idx_file_claims_active ON file_claims(file_path, released_at);
    """)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    print("  Migration complete.")
    verify(conn)
    conn.close()


def verify(conn: sqlite3.Connection) -> None:
    """Verify migration was applied correctly."""
    print("\n  === Verification ===")

    # Check columns
    columns = {row[1] for row in conn.execute("PRAGMA table_info(beads)").fetchall()}
    required = {"id", "content", "summary", "type", "kind", "project",
                "weight", "starred", "superseded_by", "archived_at",
                "content_hash", "context", "tags", "created_at", "last_accessed"}
    missing = required - columns
    if missing:
        print(f"  FAIL: Missing columns: {missing}")
    else:
        print(f"  OK: All {len(required)} columns present")

    # Check kind values
    kinds = [row[0] for row in conn.execute("SELECT DISTINCT kind FROM beads").fetchall()]
    print(f"  OK: kind values in DB: {kinds}")

    # Check content_hash coverage
    total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
    hashed = conn.execute("SELECT COUNT(*) FROM beads WHERE content_hash IS NOT NULL").fetchone()[0]
    if total == hashed:
        print(f"  OK: content_hash populated for all {total} beads")
    else:
        print(f"  WARN: {total - hashed} beads missing content_hash")

    # Check messaging tables
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    msg_tables = {"agents", "messages", "file_claims"}
    if msg_tables.issubset(tables):
        print(f"  OK: Messaging tables present ({', '.join(msg_tables)})")
    else:
        print(f"  FAIL: Missing tables: {msg_tables - tables}")

    # Check FTS
    try:
        conn.execute("SELECT * FROM beads_fts LIMIT 1")
        print("  OK: FTS5 index operational")
    except sqlite3.OperationalError as e:
        print(f"  FAIL: FTS5 error: {e}")

    print("  === Done ===\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enki v2 Schema Migration")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Path to wisdom.db (default: ~/.enki/wisdom.db)")
    args = parser.parse_args()

    db_path = args.db_path or get_default_db_path()
    migrate(db_path)
