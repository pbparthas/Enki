"""graph.db schema — codebase knowledge graph per project."""

GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    size_bytes INTEGER DEFAULT 0,
    last_modified TEXT,
    symbol_count INTEGER DEFAULT 0,
    complexity_score REAL DEFAULT 0,
    last_scanned TEXT,
    git_change_frequency INTEGER DEFAULT 0  -- commits touching this file
);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,        -- {file_path}::{symbol_name}::{line_start}
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,         -- function|class|method|export|type|interface|const
    line_start INTEGER,
    line_end INTEGER,
    signature TEXT,             -- function signature or type definition
    complexity INTEGER DEFAULT 0,
    is_exported INTEGER DEFAULT 0,
    FOREIGN KEY (file_path) REFERENCES files(path)
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,        -- {from}::{edge_type}::{to}
    from_id TEXT NOT NULL,      -- file path or symbol id
    to_id TEXT NOT NULL,        -- file path or symbol id
    edge_type TEXT NOT NULL,    -- imports|calls|extends|implements|duplicates|reexports
    weight REAL DEFAULT 1.0,
    line_number INTEGER         -- where in from_id the edge occurs
);

CREATE TABLE IF NOT EXISTS blast_radius (
    symbol_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    direct_importers INTEGER DEFAULT 0,
    transitive_importers INTEGER DEFAULT 0,
    direct_callers INTEGER DEFAULT 0,
    blast_score REAL DEFAULT 0,     -- normalized 0.0-1.0
    risk_level TEXT DEFAULT 'low',  -- low|medium|high|critical
    last_computed TEXT
);

CREATE TABLE IF NOT EXISTS scan_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_blast_file ON blast_radius(file_path);
"""


def create_graph_tables(conn) -> None:
    conn.executescript(GRAPH_SCHEMA)
    conn.commit()

