"""schemas.py — em.db table definitions.

em.db: Mail messages, threads, task state, sprint state, bugs,
PM decisions, mail archive. Per-project, ephemeral (30 days post-close).

DDL copied verbatim from EM Orchestrator Spec v1.4, Section 20.
"""


def create_tables(conn) -> None:
    """Create em.db tables."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mail_threads (
            thread_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_thread_id TEXT,
            type TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            archived_at TIMESTAMP,
            FOREIGN KEY (parent_thread_id) REFERENCES mail_threads(thread_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mail_messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            parent_thread_id TEXT,
            project_id TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            importance TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'unread',
            assigned_to TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            task_id TEXT,
            sprint_id TEXT,
            FOREIGN KEY (thread_id) REFERENCES mail_threads(thread_id)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mail_to_agent "
        "ON mail_messages(to_agent, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mail_thread "
        "ON mail_messages(thread_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mail_project "
        "ON mail_messages(project_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_state (
            task_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sprint_id TEXT NOT NULL,
            task_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            assigned_files TEXT,
            dependencies TEXT,
            tier TEXT NOT NULL,
            work_type TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            agent_outputs TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_sprint "
        "ON task_state(sprint_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_project "
        "ON task_state(project_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sprint_state (
            sprint_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sprint_number INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            dependencies TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bugs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT,
            sprint_id TEXT,
            filed_by TEXT NOT NULL,
            assigned_to TEXT,
            priority TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            mail_message_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY (mail_message_id) REFERENCES mail_messages(id)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bugs_project "
        "ON bugs(project_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bugs_task ON bugs(task_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_decisions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            decision_type TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            context TEXT,
            human_response TEXT,
            human_modification TEXT,
            pm_was_autonomous INTEGER DEFAULT 0,
            human_override TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pm_decisions_type "
        "ON pm_decisions(decision_type, human_response)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mail_archive (
            id TEXT PRIMARY KEY,
            original_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            created_at TIMESTAMP,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_project "
        "ON mail_archive(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_thread "
        "ON mail_archive(thread_id)"
    )
