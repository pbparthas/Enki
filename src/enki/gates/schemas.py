"""schemas.py — uru.db table definitions.

uru.db: Enforcement logs, feedback proposals, nudge state.

DDL copied verbatim from Uru Gates Spec v1.1, Section 11.
"""

DRIFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS drift_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    tool_name TEXT,
    tool_input_summary TEXT,
    drift_contribution REAL DEFAULT 0,
    cumulative_drift REAL DEFAULT 0,
    event_type TEXT DEFAULT 'scored',
    pattern_matched TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS session_drift (
    session_id TEXT PRIMARY KEY,
    cumulative_score REAL DEFAULT 0,
    last_updated TEXT,
    nudge_count INTEGER DEFAULT 0,
    escalated INTEGER DEFAULT 0,
    project TEXT
);

CREATE INDEX IF NOT EXISTS idx_drift_session ON drift_events(session_id);
CREATE INDEX IF NOT EXISTS idx_drift_time ON drift_events(timestamp);
"""


def _migrate_drift_tables(conn) -> None:
    """Add drift scoring tables if not present."""
    conn.executescript(DRIFT_SCHEMA)
    conn.commit()


def create_tables(conn) -> None:
    """Create uru.db tables."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS enforcement_log (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hook TEXT NOT NULL,
            layer TEXT NOT NULL,
            tool_name TEXT,
            target TEXT,
            action TEXT NOT NULL,
            reason TEXT,
            user_override INTEGER DEFAULT 0,
            project TEXT
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_enforcement_session "
        "ON enforcement_log(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_enforcement_action "
        "ON enforcement_log(action)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_enforcement_layer "
        "ON enforcement_log(layer)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback_proposals (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT NOT NULL,
            description TEXT NOT NULL,
            related_log_ids TEXT,
            status TEXT DEFAULT 'pending',
            gemini_response TEXT,
            reviewed_at TIMESTAMP,
            applied INTEGER DEFAULT 0
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_status "
        "ON feedback_proposals(status)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS nudge_state (
            nudge_type TEXT NOT NULL,
            session_id TEXT NOT NULL,
            last_fired TIMESTAMP,
            fire_count INTEGER DEFAULT 0,
            acted_on INTEGER DEFAULT 0,
            PRIMARY KEY (nudge_type, session_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_status (
            goal_id TEXT NOT NULL,
            agent_role TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (goal_id, agent_role)
        )
    """)
    conn.execute("DROP INDEX IF EXISTS idx_agent_status_goal")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_status_goal "
        "ON agent_status(goal_id, agent_role, status)"
    )
    _migrate_drift_tables(conn)
