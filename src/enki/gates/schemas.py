"""schemas.py — uru.db table definitions.

uru.db: Enforcement logs, feedback proposals, nudge state.

DDL copied verbatim from Uru Gates Spec v1.1, Section 11.
"""


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
