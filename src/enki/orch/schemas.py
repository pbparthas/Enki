"""schemas.py — em.db table definitions.

em.db: Mail messages, threads, task state, sprint state, bugs,
PM decisions, mail archive. Per-project, ephemeral (30 days post-close).

DDL copied verbatim from EM Orchestrator Spec v1.4, Section 20.
"""


def migrate_add_agent_briefs(conn) -> None:
    """Add agent_briefs column to task_state if not present."""
    try:
        conn.execute("ALTER TABLE task_state ADD COLUMN agent_briefs TEXT")
        conn.commit()
    except Exception:
        pass


def migrate_add_impl_council_state(conn) -> None:
    """Add impl_council_state column to sprint_state if not present."""
    try:
        conn.execute("ALTER TABLE sprint_state ADD COLUMN impl_council_state TEXT")
        conn.commit()
    except Exception:
        pass


def migrate_add_model_used(conn) -> None:
    """Add model_used column to task_state if not present."""
    try:
        conn.execute("ALTER TABLE task_state ADD COLUMN model_used TEXT")
        conn.commit()
    except Exception:
        pass


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
            max_retries INTEGER DEFAULT 3,
            session_id TEXT,
            worktree_path TEXT,
            task_phase TEXT DEFAULT 'test_design',
            description TEXT,
            agent_briefs TEXT,
            model_used TEXT
        )
    """)
    for col, coltype, default in [
        ("session_id", "TEXT", None),
        ("worktree_path", "TEXT", None),
        ("task_phase", "TEXT", "'test_design'"),
        ("description", "TEXT", None),
        ("agent_briefs", "TEXT", None),
        ("model_used", "TEXT", None),
    ]:
        try:
            if default:
                conn.execute(
                    f"ALTER TABLE task_state ADD COLUMN {col} {coltype} DEFAULT {default}"
                )
            else:
                conn.execute(f"ALTER TABLE task_state ADD COLUMN {col} {coltype}")
        except Exception:
            pass
    migrate_add_agent_briefs(conn)
    migrate_add_model_used(conn)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_sprint "
        "ON task_state(sprint_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_project "
        "ON task_state(project_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_state (
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_approvals (
            task_id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            tests_written INTEGER DEFAULT 0,
            validator_checked INTEGER DEFAULT 0,
            validator_issues TEXT,
            hitl_approved INTEGER DEFAULT 0,
            hitl_approved_at TIMESTAMP,
            hitl_notes TEXT,
            FOREIGN KEY (task_id) REFERENCES task_state(task_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sprint_state (
            sprint_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            sprint_number INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            dependencies TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            impl_council_state TEXT
        )
    """)
    migrate_add_impl_council_state(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bugs (
            id TEXT PRIMARY KEY,
            bug_number INTEGER,
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
    try:
        conn.execute("ALTER TABLE bugs ADD COLUMN bug_number INTEGER")
    except Exception:
        pass

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bugs_project "
        "ON bugs(project_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bugs_task ON bugs(task_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bugs_project_number "
        "ON bugs(project_id, bug_number)"
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoints (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            phase TEXT,
            tier TEXT,
            goal TEXT,
            orchestration_state TEXT,
            mail_position TEXT,
            recent_bead_ids TEXT
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_session "
        "ON checkpoints(session_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_registry (
            project_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            task_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('created', 'modified')),
            description TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, file_path)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hitl_approvals (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            stage TEXT NOT NULL,
            note TEXT,
            approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hitl_approvals_project_stage "
        "ON hitl_approvals(project, stage)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS merge_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            sprint_branch TEXT NOT NULL,
            queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            merged_at TIMESTAMP,
            status TEXT DEFAULT 'queued',
            conflict_files TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_merge_queue_project_status "
        "ON merge_queue(project_id, status)"
    )
