"""schemas.py — wisdom.db + abzu.db table definitions.

wisdom.db: Permanent beads (Gemini-approved), projects, user profile.
abzu.db: Session summaries, bead candidates (staging), extraction log.

DDL copied verbatim from Abzu Memory Spec v1.2, Section 16.
"""


def create_tables(conn, db_type: str) -> None:
    """Create tables for the specified database type.

    Args:
        conn: SQLite connection (already configured with WAL/busy_timeout).
        db_type: "wisdom" or "abzu".
    """
    if db_type == "wisdom":
        _create_wisdom_tables(conn)
    elif db_type == "abzu":
        _create_abzu_tables(conn)
    else:
        raise ValueError(f"Unknown db_type: {db_type}")


def _create_wisdom_tables(conn) -> None:
    """wisdom.db: beads, beads_fts, projects, user_profile."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            name TEXT PRIMARY KEY,
            path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS beads (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            category TEXT NOT NULL CHECK (category IN (
                'decision', 'learning', 'pattern', 'fix', 'preference'
            )),
            project TEXT,
            weight REAL DEFAULT 1.0,
            starred INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            tags TEXT,
            context TEXT,
            superseded_by TEXT,
            gemini_flagged INTEGER DEFAULT 0,
            flag_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,
            promoted_at TIMESTAMP,
            FOREIGN KEY (superseded_by) REFERENCES beads(id),
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beads_project ON beads(project)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beads_category ON beads(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beads_weight ON beads(weight)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beads_hash ON beads(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_beads_flagged ON beads(gemini_flagged)"
    )

    # FTS5 virtual table — cannot use IF NOT EXISTS, so check first
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='beads_fts'"
    ).fetchone()
    if not existing:
        conn.execute("""
            CREATE VIRTUAL TABLE beads_fts USING fts5(
                content,
                summary,
                tags,
                content='beads',
                content_rowid='rowid'
            )
        """)

        conn.execute("""
            CREATE TRIGGER beads_ai AFTER INSERT ON beads BEGIN
                INSERT INTO beads_fts(rowid, content, summary, tags)
                VALUES (new.rowid, new.content, new.summary, new.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER beads_ad AFTER DELETE ON beads BEGIN
                INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER beads_au AFTER UPDATE ON beads BEGIN
                INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
                INSERT INTO beads_fts(rowid, content, summary, tags)
                VALUES (new.rowid, new.content, new.summary, new.tags);
            END
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS freshness_checks (
            bead_id TEXT NOT NULL,
            detected_version TEXT NOT NULL,
            current_version TEXT,
            checked_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (bead_id, detected_version),
            FOREIGN KEY (bead_id) REFERENCES beads(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('explicit', 'inferred', 'codebase')),
            confidence REAL DEFAULT 1.0,
            project_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _create_abzu_tables(conn) -> None:
    """abzu.db: session_summaries, bead_candidates, candidates_fts, extraction_log."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_summaries (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            project TEXT,
            sequence INTEGER DEFAULT 0,
            goal TEXT,
            phase TEXT,
            operational_state TEXT,
            conversational_state TEXT,
            is_final INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_summaries_session "
        "ON session_summaries(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_summaries_project "
        "ON session_summaries(project, is_final)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bead_candidates (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            category TEXT NOT NULL CHECK (category IN (
                'decision', 'learning', 'pattern', 'fix'
            )),
            project TEXT,
            content_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidates_project "
        "ON bead_candidates(project)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidates_hash "
        "ON bead_candidates(content_hash)"
    )

    # FTS5 for candidates
    existing = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='candidates_fts'"
    ).fetchone()
    if not existing:
        conn.execute("""
            CREATE VIRTUAL TABLE candidates_fts USING fts5(
                content,
                summary,
                content='bead_candidates',
                content_rowid='rowid'
            )
        """)

        conn.execute("""
            CREATE TRIGGER candidates_ai AFTER INSERT ON bead_candidates BEGIN
                INSERT INTO candidates_fts(rowid, content, summary)
                VALUES (new.rowid, new.content, new.summary);
            END
        """)

        conn.execute("""
            CREATE TRIGGER candidates_ad AFTER DELETE ON bead_candidates BEGIN
                INSERT INTO candidates_fts(candidates_fts, rowid, content, summary)
                VALUES ('delete', old.rowid, old.content, old.summary);
            END
        """)

        conn.execute("""
            CREATE TRIGGER candidates_au AFTER UPDATE ON bead_candidates BEGIN
                INSERT INTO candidates_fts(candidates_fts, rowid, content, summary)
                VALUES ('delete', old.rowid, old.content, old.summary);
                INSERT INTO candidates_fts(rowid, content, summary)
                VALUES (new.rowid, new.content, new.summary);
            END
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS staging_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            reason TEXT NOT NULL,
            rejected_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS extraction_log (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            jsonl_path TEXT,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            candidates_created INTEGER DEFAULT 0,
            method TEXT NOT NULL
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_extraction_session "
        "ON extraction_log(session_id)"
    )
