"""Tests for Phase 0: Bootstrap — schemas, db, config."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_enki_root(tmp_path):
    """Provide a temporary ~/.enki directory."""
    enki_root = tmp_path / ".enki"
    enki_root.mkdir()
    with patch("enki.db.ENKI_ROOT", enki_root):
        yield enki_root


class TestDBConnections:
    """Test db.py connection management."""

    def test_connect_sets_wal_mode(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_connect_sets_busy_timeout(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout == 5000

    def test_connect_sets_foreign_keys(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1

    def test_connect_sets_row_factory(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            assert conn.row_factory == sqlite3.Row

    def test_connect_autocommits(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            conn.execute("CREATE TABLE t (x TEXT)")
            conn.execute("INSERT INTO t VALUES ('hello')")

        # Verify committed
        with connect(db_path) as conn:
            row = conn.execute("SELECT x FROM t").fetchone()
            assert row["x"] == "hello"

    def test_connect_rollbacks_on_error(self, tmp_path):
        from enki.db import connect

        db_path = tmp_path / "test.db"
        with connect(db_path) as conn:
            conn.execute("CREATE TABLE t (x TEXT)")

        with pytest.raises(Exception):
            with connect(db_path) as conn:
                conn.execute("INSERT INTO t VALUES ('will rollback')")
                raise ValueError("force rollback")

        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert count == 0

    def test_em_db_creates_project_dir(self, temp_enki_root):
        from enki.db import em_db

        with em_db("myproject") as conn:
            conn.execute("SELECT 1")

        assert (temp_enki_root / "projects" / "myproject" / "em.db").exists()


class TestWisdomSchema:
    """Test wisdom.db tables."""

    def test_beads_table_exists(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "beads" in tables
            assert "projects" in tables
            assert "user_profile" in tables

    def test_beads_fts_exists(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "beads_fts" in tables

    def test_beads_category_constraint(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO beads (id, content, category, content_hash) "
                    "VALUES ('b1', 'test', 'invalid_cat', 'hash1')"
                )

    def test_beads_valid_categories(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            for cat in ("decision", "learning", "pattern", "fix", "preference"):
                conn.execute(
                    "INSERT INTO beads (id, content, category, content_hash) "
                    "VALUES (?, 'test', ?, ?)",
                    (f"b-{cat}", cat, f"hash-{cat}"),
                )
            count = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
            assert count == 5

    def test_fts5_triggers_on_insert(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            conn.execute(
                "INSERT INTO beads (id, content, category, content_hash) "
                "VALUES ('b1', 'architectural decision about JWT', 'decision', 'h1')"
            )
            results = conn.execute(
                "SELECT * FROM beads_fts WHERE beads_fts MATCH 'JWT'"
            ).fetchall()
            assert len(results) == 1

    def test_user_profile_source_constraint(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "wisdom.db"
        with connect(db) as conn:
            create_tables(conn, "wisdom")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO user_profile (key, value, source) "
                    "VALUES ('pref', 'val', 'bad_source')"
                )


class TestAbzuSchema:
    """Test abzu.db tables."""

    def test_session_summaries_table(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "abzu.db"
        with connect(db) as conn:
            create_tables(conn, "abzu")
            conn.execute(
                "INSERT INTO session_summaries (id, session_id, goal) "
                "VALUES ('s1', 'sess-1', 'Build v3')"
            )
            row = conn.execute(
                "SELECT * FROM session_summaries WHERE id='s1'"
            ).fetchone()
            assert row["goal"] == "Build v3"

    def test_bead_candidates_no_preference(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "abzu.db"
        with connect(db) as conn:
            create_tables(conn, "abzu")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO bead_candidates "
                    "(id, content, category, content_hash, source) "
                    "VALUES ('c1', 'test', 'preference', 'h1', 'session')"
                )

    def test_candidates_fts(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "abzu.db"
        with connect(db) as conn:
            create_tables(conn, "abzu")
            conn.execute(
                "INSERT INTO bead_candidates "
                "(id, content, category, content_hash, source) "
                "VALUES ('c1', 'SQLite WAL mode for concurrency', "
                "'learning', 'h1', 'session')"
            )
            results = conn.execute(
                "SELECT * FROM candidates_fts WHERE candidates_fts MATCH 'WAL'"
            ).fetchall()
            assert len(results) == 1

    def test_extraction_log(self, tmp_path):
        from enki.db import connect
        from enki.memory.schemas import create_tables

        db = tmp_path / "abzu.db"
        with connect(db) as conn:
            create_tables(conn, "abzu")
            conn.execute(
                "INSERT INTO extraction_log (id, session_id, method) "
                "VALUES ('e1', 'sess-1', 'heuristic')"
            )
            row = conn.execute(
                "SELECT * FROM extraction_log WHERE id='e1'"
            ).fetchone()
            assert row["method"] == "heuristic"


class TestUruSchema:
    """Test uru.db tables."""

    def test_enforcement_log(self, tmp_path):
        from enki.db import connect
        from enki.gates.schemas import create_tables

        db = tmp_path / "uru.db"
        with connect(db) as conn:
            create_tables(conn)
            conn.execute(
                "INSERT INTO enforcement_log "
                "(id, session_id, hook, layer, action) "
                "VALUES ('log1', 'sess-1', 'pre-tool-use', 'layer0', 'block')"
            )
            row = conn.execute(
                "SELECT * FROM enforcement_log WHERE id='log1'"
            ).fetchone()
            assert row["action"] == "block"

    def test_feedback_proposals(self, tmp_path):
        from enki.db import connect
        from enki.gates.schemas import create_tables

        db = tmp_path / "uru.db"
        with connect(db) as conn:
            create_tables(conn)
            conn.execute(
                "INSERT INTO feedback_proposals "
                "(id, trigger_type, description) "
                "VALUES ('p1', 'override', 'Gate too strict')"
            )
            row = conn.execute(
                "SELECT * FROM feedback_proposals WHERE id='p1'"
            ).fetchone()
            assert row["status"] == "pending"

    def test_nudge_state_composite_key(self, tmp_path):
        from enki.db import connect
        from enki.gates.schemas import create_tables

        db = tmp_path / "uru.db"
        with connect(db) as conn:
            create_tables(conn)
            conn.execute(
                "INSERT INTO nudge_state (nudge_type, session_id, fire_count) "
                "VALUES ('unrecorded_decision', 'sess-1', 1)"
            )
            conn.execute(
                "INSERT INTO nudge_state (nudge_type, session_id, fire_count) "
                "VALUES ('long_session', 'sess-1', 2)"
            )
            count = conn.execute("SELECT COUNT(*) FROM nudge_state").fetchone()[0]
            assert count == 2


class TestEmSchema:
    """Test em.db tables."""

    def test_all_tables_created(self, tmp_path):
        from enki.db import connect
        from enki.orch.schemas import create_tables

        db = tmp_path / "em.db"
        with connect(db) as conn:
            create_tables(conn)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            expected = {
                "mail_threads",
                "mail_messages",
                "task_state",
                "sprint_state",
                "bugs",
                "pm_decisions",
                "mail_archive",
            }
            assert expected.issubset(tables)

    def test_mail_thread_fk(self, tmp_path):
        from enki.db import connect
        from enki.orch.schemas import create_tables

        db = tmp_path / "em.db"
        with connect(db) as conn:
            create_tables(conn)
            # Create thread first
            conn.execute(
                "INSERT INTO mail_threads (thread_id, project_id, type) "
                "VALUES ('t1', 'proj1', 'project')"
            )
            # Message referencing thread should work
            conn.execute(
                "INSERT INTO mail_messages "
                "(id, thread_id, project_id, from_agent, to_agent, body) "
                "VALUES ('m1', 't1', 'proj1', 'PM', 'EM', 'kickoff')"
            )
            row = conn.execute(
                "SELECT * FROM mail_messages WHERE id='m1'"
            ).fetchone()
            assert row["from_agent"] == "PM"


class TestIdempotency:
    """Verify init_all can be called multiple times safely."""

    def test_double_init(self, temp_enki_root):
        from enki.db import init_all

        init_all()
        init_all()  # Should not raise

        # Verify tables still exist
        from enki.db import wisdom_db

        with wisdom_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            assert count > 0


class TestConfig:
    """Test config.py."""

    def test_defaults_without_file(self, tmp_path):
        from enki.config import _DEFAULTS, get_config

        with patch("enki.config.CONFIG_PATH", tmp_path / "nonexistent.toml"):
            cfg = get_config()
            assert cfg["general"]["version"] == "3.0"
            assert cfg["memory"]["fts5_min_score"] == 0.3

    def test_ensure_config_creates_file(self, tmp_path):
        config_path = tmp_path / "enki.toml"
        with patch("enki.config.CONFIG_PATH", config_path):
            from enki.config import ensure_config

            ensure_config()
            assert config_path.exists()

    def test_ensure_config_idempotent(self, tmp_path):
        config_path = tmp_path / "enki.toml"
        with patch("enki.config.CONFIG_PATH", config_path):
            from enki.config import ensure_config

            ensure_config()
            content1 = config_path.read_text()
            ensure_config()
            content2 = config_path.read_text()
            assert content1 == content2
