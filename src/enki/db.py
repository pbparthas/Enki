"""Database connection and schema management."""

import sqlite3
from pathlib import Path
from typing import Optional
import threading

# Thread-local storage for connections
_local = threading.local()

ENKI_DIR = Path.home() / ".enki"
DB_PATH = ENKI_DIR / "wisdom.db"

# Current active database path (can be overridden for testing)
_current_db_path: Optional[Path] = None


def set_db_path(path: Optional[Path]) -> None:
    """Set the current database path. Use None to reset to default."""
    global _current_db_path
    _current_db_path = path


def get_current_db_path() -> Path:
    """Get the current database path."""
    return _current_db_path or DB_PATH

SCHEMA = """
-- Beads: knowledge units
CREATE TABLE IF NOT EXISTS beads (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,
    type TEXT NOT NULL CHECK (type IN ('decision', 'solution', 'learning', 'violation', 'pattern')),
    project TEXT,

    -- Retention
    weight REAL DEFAULT 1.0,
    starred INTEGER DEFAULT 0,
    superseded_by TEXT,

    -- Context
    context TEXT,
    tags TEXT,  -- JSON array

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,

    FOREIGN KEY (superseded_by) REFERENCES beads(id)
);

-- Embeddings: vector representations
CREATE TABLE IF NOT EXISTS embeddings (
    bead_id TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    model TEXT DEFAULT 'all-MiniLM-L6-v2',
    FOREIGN KEY (bead_id) REFERENCES beads(id) ON DELETE CASCADE
);

-- Access log: usage tracking
CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bead_id TEXT NOT NULL,
    session_id TEXT,
    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    was_useful INTEGER,
    FOREIGN KEY (bead_id) REFERENCES beads(id)
);

-- Projects: project registry
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_session TIMESTAMP
);

-- Sessions: session history
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    goal TEXT,
    summary TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

-- Interceptions: Ereshkigal logs (Phase 7, but schema here)
CREATE TABLE IF NOT EXISTS interceptions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    tool TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    category TEXT,
    pattern TEXT,
    result TEXT NOT NULL CHECK (result IN ('allowed', 'blocked')),
    task_id TEXT,
    phase TEXT,
    was_legitimate INTEGER,
    outcome_note TEXT
);

-- Full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS beads_fts USING fts5(
    content,
    summary,
    tags,
    content='beads',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS beads_ai AFTER INSERT ON beads BEGIN
    INSERT INTO beads_fts(rowid, content, summary, tags)
    VALUES (new.rowid, new.content, new.summary, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS beads_ad AFTER DELETE ON beads BEGIN
    INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
    VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS beads_au AFTER UPDATE ON beads BEGIN
    INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
    VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
    INSERT INTO beads_fts(rowid, content, summary, tags)
    VALUES (new.rowid, new.content, new.summary, new.tags);
END;

-- Violations: gate enforcement logs
CREATE TABLE IF NOT EXISTS violations (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    gate TEXT NOT NULL,
    tool TEXT NOT NULL,
    file_path TEXT,
    phase TEXT,
    tier TEXT,
    reason TEXT,
    was_overridden INTEGER DEFAULT 0
);

-- Tier escalations: when Claude's work grows beyond initial tier
CREATE TABLE IF NOT EXISTS tier_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    initial_tier TEXT NOT NULL,
    final_tier TEXT NOT NULL,
    files_at_escalation INT,
    lines_at_escalation INT,
    goal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Self-analysis: Enki's self-correction tracking
CREATE TABLE IF NOT EXISTS enki_self_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pattern_type TEXT,
    description TEXT,
    frequency INT,
    impact TEXT,
    correction TEXT,
    effective INTEGER
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_beads_project ON beads(project);
CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(type);
CREATE INDEX IF NOT EXISTS idx_beads_created ON beads(created_at);
CREATE INDEX IF NOT EXISTS idx_beads_weight ON beads(weight);
CREATE INDEX IF NOT EXISTS idx_access_log_bead ON access_log(bead_id);
CREATE INDEX IF NOT EXISTS idx_interceptions_session ON interceptions(session_id);
CREATE INDEX IF NOT EXISTS idx_interceptions_result ON interceptions(result);
CREATE INDEX IF NOT EXISTS idx_violations_session ON violations(session_id);
CREATE INDEX IF NOT EXISTS idx_violations_gate ON violations(gate);
CREATE INDEX IF NOT EXISTS idx_escalations_session ON tier_escalations(session_id);
"""


def get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get thread-local database connection."""
    path = db_path or get_current_db_path()

    if not hasattr(_local, 'connections'):
        _local.connections = {}

    path_str = str(path)
    if path_str not in _local.connections:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.connections[path_str] = conn

    return _local.connections[path_str]


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize database with schema."""
    path = db_path or get_current_db_path()

    # Set as current path if explicitly provided
    if db_path is not None:
        set_db_path(db_path)

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_db(path)
    conn.executescript(SCHEMA)
    conn.commit()


def close_db() -> None:
    """Close all thread-local connections."""
    if hasattr(_local, 'connections'):
        for conn in _local.connections.values():
            conn.close()
        _local.connections = {}
