"""schemas.py — wisdom.db + abzu.db table definitions.

wisdom.db: Permanent notes + v3 beads (until migration), projects, user profile.
abzu.db: Note candidates + v3 bead_candidates (until migration), session summaries,
         evolution proposals, onboarding status, extraction log.

v3 tables (beads, bead_candidates) retained for backward compatibility.
v4 tables (notes, note_candidates) created alongside.
Migration script (Phase 5) moves data from v3 → v4 and drops v3 tables.
"""


def create_tables(conn, db_type: str) -> None:
    """Create tables for the specified database type.

    Args:
        conn: SQLite connection (already configured with WAL/busy_timeout).
        db_type: "wisdom" or "abzu".
    """
    if db_type == "wisdom":
        _create_wisdom_tables(conn)
        _create_wisdom_v4_tables(conn)
    elif db_type == "abzu":
        _create_abzu_tables(conn)
        _create_abzu_v4_tables(conn)
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


# ---------------------------------------------------------------------------
# v4 Note Schema — enki-v4-spec.md Section 13
# ---------------------------------------------------------------------------


def _create_wisdom_v4_tables(conn) -> None:
    """wisdom.db v4: notes, embeddings, note_links, projects (upgraded)."""

    # Upgrade projects table with v4 columns (idempotent)
    for col, default in [
        ("primary_branch", "'main'"),
        ("tech_stack", "NULL"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE projects ADD COLUMN {col} TEXT DEFAULT {default}"
            )
        except Exception:
            pass  # Column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            context_description TEXT,
            keywords TEXT,
            tags TEXT,
            category TEXT NOT NULL CHECK (category IN (
                'decision', 'learning', 'pattern', 'fix', 'preference', 'code_knowledge'
            )),
            project TEXT,
            file_ref TEXT,
            file_hash TEXT,
            last_verified TIMESTAMP,
            weight REAL DEFAULT 1.0,
            starred INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,
            evolved_at TIMESTAMP,
            promoted_at TIMESTAMP,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_weight ON notes(weight)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_hash ON notes(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_file_ref ON notes(file_ref)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            note_id TEXT PRIMARY KEY,
            vector BLOB NOT NULL,
            model TEXT DEFAULT 'all-MiniLM-L6-v2',
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_links (
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship TEXT NOT NULL CHECK (relationship IN (
                'relates_to', 'supersedes', 'contradicts', 'extends',
                'imports', 'uses', 'implements'
            )),
            created_by TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, target_id),
            FOREIGN KEY (source_id) REFERENCES notes(id) ON DELETE CASCADE
        )
    """)

    # FTS5 virtual table for notes — cannot use IF NOT EXISTS
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='notes_fts'"
    ).fetchone()
    if not existing:
        conn.execute("""
            CREATE VIRTUAL TABLE notes_fts USING fts5(
                content, summary, context_description, keywords, tags,
                content='notes', content_rowid='rowid'
            )
        """)

        conn.execute("""
            CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, content, summary, context_description, keywords, tags)
                VALUES (new.rowid, new.content, new.summary, new.context_description, new.keywords, new.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, content, summary, context_description, keywords, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.context_description, old.keywords, old.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, content, summary, context_description, keywords, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.context_description, old.keywords, old.tags);
                INSERT INTO notes_fts(rowid, content, summary, context_description, keywords, tags)
                VALUES (new.rowid, new.content, new.summary, new.context_description, new.keywords, new.tags);
            END
        """)


def _create_abzu_v4_tables(conn) -> None:
    """abzu.db v4: note_candidates, candidate_embeddings, candidate_links,
    evolution_proposals, onboarding_status. Updated extraction_log."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_candidates (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            context_description TEXT,
            keywords TEXT,
            tags TEXT,
            category TEXT NOT NULL CHECK (category IN (
                'decision', 'learning', 'pattern', 'fix', 'code_knowledge'
            )),
            project TEXT,
            status TEXT DEFAULT 'raw' CHECK (status IN ('raw', 'enriched', 'discarded')),
            file_ref TEXT,
            file_hash TEXT,
            content_hash TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN (
                'manual', 'session_end', 'code_scan', 'onboarding', 'rescan', 'em_distill', 'transcript-extraction'
            )),
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidates_v4_status "
        "ON note_candidates(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidates_v4_project "
        "ON note_candidates(project)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidates_v4_hash "
        "ON note_candidates(content_hash)"
    )

    # FTS5 for note_candidates — check for v4 version
    # v3 candidates_fts is backed by bead_candidates; v4 needs its own
    existing = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='candidates_v4_fts'"
    ).fetchone()
    if not existing:
        conn.execute("""
            CREATE VIRTUAL TABLE candidates_v4_fts USING fts5(
                content, summary, context_description, keywords, tags,
                content='note_candidates', content_rowid='rowid'
            )
        """)

        conn.execute("""
            CREATE TRIGGER candidates_v4_ai AFTER INSERT ON note_candidates BEGIN
                INSERT INTO candidates_v4_fts(rowid, content, summary, context_description, keywords, tags)
                VALUES (new.rowid, new.content, new.summary, new.context_description, new.keywords, new.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER candidates_v4_ad AFTER DELETE ON note_candidates BEGIN
                INSERT INTO candidates_v4_fts(candidates_v4_fts, rowid, content, summary, context_description, keywords, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.context_description, old.keywords, old.tags);
            END
        """)

        conn.execute("""
            CREATE TRIGGER candidates_v4_au AFTER UPDATE ON note_candidates BEGIN
                INSERT INTO candidates_v4_fts(candidates_v4_fts, rowid, content, summary, context_description, keywords, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.context_description, old.keywords, old.tags);
                INSERT INTO candidates_v4_fts(rowid, content, summary, context_description, keywords, tags)
                VALUES (new.rowid, new.content, new.summary, new.context_description, new.keywords, new.tags);
            END
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_embeddings (
            note_id TEXT PRIMARY KEY,
            vector BLOB NOT NULL,
            model TEXT DEFAULT 'all-MiniLM-L6-v2',
            FOREIGN KEY (note_id) REFERENCES note_candidates(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_links (
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_db TEXT DEFAULT 'abzu' CHECK (target_db IN ('abzu', 'wisdom')),
            relationship TEXT NOT NULL CHECK (relationship IN (
                'relates_to', 'supersedes', 'contradicts', 'extends',
                'imports', 'uses', 'implements'
            )),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, target_id),
            FOREIGN KEY (source_id) REFERENCES note_candidates(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS evolution_proposals (
            id TEXT PRIMARY KEY,
            target_note_id TEXT NOT NULL,
            triggered_by TEXT NOT NULL,
            proposed_context TEXT,
            proposed_keywords TEXT,
            proposed_tags TEXT,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_status "
        "ON evolution_proposals(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_target "
        "ON evolution_proposals(target_note_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_status (
            project TEXT PRIMARY KEY,
            codebase_scan TEXT DEFAULT 'pending' CHECK (codebase_scan IN (
                'pending', 'in_progress', 'complete', 'failed'
            )),
            notes_extracted INTEGER DEFAULT 0,
            notes_total_estimate INTEGER,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            last_rescan TIMESTAMP
        )
    """)
