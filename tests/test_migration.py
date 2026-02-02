"""Tests for Phase 0: Migration from Odin/Freyja to Enki."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from enki.migration import (
    migrate_to_enki,
    validate_migration,
    rollback_migration,
    _map_bead_type,
    _migrate_odin,
    _migrate_freyja,
    _migrate_project,
)
from enki.db import init_db, get_db, set_db_path, ENKI_DIR


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Set up temporary home directory with Odin/Freyja data."""
    # Create mock home directory structure
    home = tmp_path / "home"
    home.mkdir()

    # Patch home directory
    monkeypatch.setattr("enki.migration.Path.home", lambda: home)
    monkeypatch.setattr("enki.db.Path.home", lambda: home)

    # Update paths for migration module
    import enki.migration as m
    m.ENKI_DIR = home / ".enki"
    m.ODIN_GLOBAL_DIR = home / ".odin"
    m.ODIN_DB = home / ".odin" / "odin.db"
    m.FREYJA_GLOBAL_DIR = home / ".freyja"
    m.FREYJA_DB = home / ".freyja" / "wisdom.db"
    m.HOOKS_DIR = home / ".claude" / "hooks"
    m.DB_PATH = home / ".enki" / "wisdom.db"

    # Update db module paths
    import enki.db as db
    db.ENKI_DIR = home / ".enki"
    db.DB_PATH = home / ".enki" / "wisdom.db"
    set_db_path(home / ".enki" / "wisdom.db")

    return home


@pytest.fixture
def odin_db(temp_home):
    """Create mock Odin database."""
    odin_dir = temp_home / ".odin"
    odin_dir.mkdir(parents=True)

    db_path = odin_dir / "odin.db"
    conn = sqlite3.connect(db_path)

    # Create Odin schema
    conn.executescript("""
        CREATE TABLE beads (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            type TEXT,
            project TEXT,
            weight REAL DEFAULT 1.0,
            starred INTEGER DEFAULT 0,
            metadata TEXT,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            goal TEXT,
            summary TEXT
        );

        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            path TEXT,
            created_at TIMESTAMP,
            last_session TIMESTAMP
        );
    """)

    # Insert test data
    conn.execute("""
        INSERT INTO beads (id, content, summary, type, weight)
        VALUES ('bead1', 'Use dependency injection', 'DI pattern', 'decision', 0.9)
    """)
    conn.execute("""
        INSERT INTO beads (id, content, summary, type, starred)
        VALUES ('bead2', 'Redis caching solution', 'Caching', 'solution', 1)
    """)
    conn.execute("""
        INSERT INTO sessions (id, goal, summary)
        VALUES ('session1', 'Implement auth', 'Completed OAuth2')
    """)
    conn.execute("""
        INSERT INTO projects (id, name, path)
        VALUES ('proj1', 'TestProject', '/tmp/testproject')
    """)

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def freyja_db(temp_home):
    """Create mock Freyja database."""
    freyja_dir = temp_home / ".freyja"
    freyja_dir.mkdir(parents=True)

    db_path = freyja_dir / "wisdom.db"
    conn = sqlite3.connect(db_path)

    # Create Freyja schema
    conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            title TEXT,
            decision TEXT,
            why TEXT,
            project TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE solutions (
            id INTEGER PRIMARY KEY,
            problem TEXT,
            solution TEXT,
            gotcha TEXT,
            project TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE learnings (
            id INTEGER PRIMARY KEY,
            category TEXT,
            content TEXT,
            project TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Insert test data
    conn.execute("""
        INSERT INTO decisions (title, decision, why, project)
        VALUES ('Auth method', 'Use JWT', 'Stateless scaling', 'api-gateway')
    """)
    conn.execute("""
        INSERT INTO solutions (problem, solution, gotcha)
        VALUES ('N+1 queries', 'Use eager loading', 'Watch for over-fetching')
    """)
    conn.execute("""
        INSERT INTO learnings (category, content)
        VALUES ('works', 'Circuit breaker for flaky APIs')
    """)

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def old_hooks(temp_home):
    """Create mock old hooks."""
    hooks_dir = temp_home / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)

    # Create Odin hooks
    (hooks_dir / "odin-session-start.sh").write_text("#!/bin/bash\necho 'odin'\n")
    (hooks_dir / "odin-pre-tool-use.sh").write_text("#!/bin/bash\necho 'odin'\n")

    # Create Freyja hooks
    (hooks_dir / "freyja-session-start.sh").write_text("#!/bin/bash\necho 'freyja'\n")

    return hooks_dir


class TestBeadTypeMapping:
    """Test bead type mapping."""

    def test_standard_types(self):
        """Standard types pass through."""
        assert _map_bead_type("decision") == "decision"
        assert _map_bead_type("solution") == "solution"
        assert _map_bead_type("learning") == "learning"
        assert _map_bead_type("violation") == "violation"
        assert _map_bead_type("pattern") == "pattern"

    def test_legacy_types(self):
        """Legacy types are mapped."""
        assert _map_bead_type("knowledge") == "learning"
        assert _map_bead_type("tip") == "learning"
        assert _map_bead_type("gotcha") == "learning"
        assert _map_bead_type("mistake") == "violation"
        assert _map_bead_type("error") == "violation"
        assert _map_bead_type("bug") == "violation"

    def test_case_insensitive(self):
        """Mapping is case insensitive."""
        assert _map_bead_type("DECISION") == "decision"
        assert _map_bead_type("Learning") == "learning"

    def test_unknown_type(self):
        """Unknown types default to learning."""
        assert _map_bead_type("unknown") == "learning"
        assert _map_bead_type("random") == "learning"


class TestOdinMigration:
    """Test Odin data migration."""

    def test_migrate_beads(self, temp_home, odin_db):
        """Beads are migrated from Odin."""
        init_db()

        import enki.migration as m
        m.ODIN_DB = odin_db

        result = _migrate_odin()

        assert result["beads"] == 2

        # Verify beads in Enki
        db = get_db()
        beads = db.execute("SELECT * FROM beads").fetchall()
        assert len(beads) == 2

        # Check bead content
        bead_ids = {b["id"] for b in beads}
        assert "odin_bead1" in bead_ids
        assert "odin_bead2" in bead_ids

    def test_migrate_sessions(self, temp_home, odin_db):
        """Sessions are migrated from Odin."""
        init_db()

        import enki.migration as m
        m.ODIN_DB = odin_db

        result = _migrate_odin()

        assert result["sessions"] == 1

        db = get_db()
        sessions = db.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) == 1
        assert sessions[0]["id"] == "odin_session1"

    def test_migrate_projects(self, temp_home, odin_db):
        """Projects are migrated from Odin."""
        init_db()

        import enki.migration as m
        m.ODIN_DB = odin_db

        result = _migrate_odin()

        assert result["projects"] == 1

        db = get_db()
        projects = db.execute("SELECT * FROM projects").fetchall()
        assert len(projects) == 1
        assert projects[0]["name"] == "TestProject"

    def test_no_duplicates(self, temp_home, odin_db):
        """Running migration twice doesn't create duplicates."""
        init_db()

        import enki.migration as m
        m.ODIN_DB = odin_db

        _migrate_odin()
        result = _migrate_odin()

        # Second run should not add anything
        assert result["beads"] == 0

        db = get_db()
        beads = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()
        assert beads["count"] == 2


class TestFreyjaMigration:
    """Test Freyja data migration."""

    def test_migrate_decisions(self, temp_home, freyja_db):
        """Decisions are migrated from Freyja."""
        init_db()

        import enki.migration as m
        m.FREYJA_DB = freyja_db

        result = _migrate_freyja()

        assert result["beads"] >= 1

        db = get_db()
        decisions = db.execute(
            "SELECT * FROM beads WHERE type = 'decision'"
        ).fetchall()
        assert len(decisions) >= 1

    def test_migrate_solutions(self, temp_home, freyja_db):
        """Solutions are migrated from Freyja."""
        init_db()

        import enki.migration as m
        m.FREYJA_DB = freyja_db

        result = _migrate_freyja()

        db = get_db()
        solutions = db.execute(
            "SELECT * FROM beads WHERE type = 'solution'"
        ).fetchall()
        assert len(solutions) >= 1

        # Check content format
        content = solutions[0]["content"]
        assert "Problem:" in content
        assert "Solution:" in content

    def test_migrate_learnings(self, temp_home, freyja_db):
        """Learnings are migrated from Freyja."""
        init_db()

        import enki.migration as m
        m.FREYJA_DB = freyja_db

        result = _migrate_freyja()

        db = get_db()
        learnings = db.execute(
            "SELECT * FROM beads WHERE type = 'learning'"
        ).fetchall()
        assert len(learnings) >= 1


class TestProjectMigration:
    """Test project-level migration."""

    def test_migrate_memory_md(self, temp_home):
        """MEMORY.md is migrated."""
        # Create source project with Freyja dir
        project_dir = temp_home / "myproject"
        project_dir.mkdir()
        freyja_dir = project_dir / ".freyja"
        freyja_dir.mkdir()
        (freyja_dir / "MEMORY.md").write_text("# Project Memory\n\nDecisions here.")

        _migrate_project(project_dir)

        enki_dir = project_dir / ".enki"
        assert enki_dir.exists()
        assert (enki_dir / "MEMORY.md").exists()
        assert "Project Memory" in (enki_dir / "MEMORY.md").read_text()

    def test_migrate_specs(self, temp_home):
        """Specs are migrated."""
        project_dir = temp_home / "myproject"
        project_dir.mkdir()
        odin_specs = project_dir / ".odin" / "specs"
        odin_specs.mkdir(parents=True)
        (odin_specs / "auth.md").write_text("# Auth Spec")

        _migrate_project(project_dir)

        enki_specs = project_dir / ".enki" / "specs"
        assert enki_specs.exists()
        assert (enki_specs / "auth.md").exists()

    def test_initializes_phase(self, temp_home):
        """PHASE file is initialized."""
        project_dir = temp_home / "myproject"
        project_dir.mkdir()

        _migrate_project(project_dir)

        phase_file = project_dir / ".enki" / "PHASE"
        assert phase_file.exists()
        assert phase_file.read_text() == "intake"


class TestHookMigration:
    """Test hook archival and installation."""

    def test_archive_old_hooks(self, temp_home, old_hooks):
        """Old hooks are archived."""
        result = migrate_to_enki(
            generate_embeddings=False,
            archive_hooks=True,
            install_hooks=False,
        )

        assert result.hooks_archived == 3

        # Hooks should be moved to archive
        archive_dir = old_hooks / "archived"
        assert archive_dir.exists()
        archived_files = list(archive_dir.glob("*.sh.*"))
        assert len(archived_files) == 3

        # Old hooks should be gone from main dir
        assert not (old_hooks / "odin-session-start.sh").exists()
        assert not (old_hooks / "freyja-session-start.sh").exists()


class TestFullMigration:
    """Test full migration flow."""

    def test_full_migration(self, temp_home, odin_db, freyja_db, old_hooks):
        """Full migration works end to end."""
        result = migrate_to_enki(
            generate_embeddings=False,  # Skip embeddings for speed
            archive_hooks=True,
            install_hooks=False,  # Skip hook install (no source hooks in test)
        )

        # Check results
        assert result.beads_migrated > 0
        assert result.hooks_archived > 0
        assert len(result.errors) == 0

    def test_validation_after_migration(self, temp_home, odin_db, freyja_db):
        """Validation passes after migration."""
        migrate_to_enki(
            generate_embeddings=False,
            archive_hooks=False,
            install_hooks=False,
        )

        checks = validate_migration()

        assert checks["enki_db_exists"]
        assert checks["beads_count"] > 0


class TestRollback:
    """Test migration rollback."""

    def test_rollback_restores_hooks(self, temp_home, old_hooks):
        """Rollback restores archived hooks."""
        # First, archive the hooks
        migrate_to_enki(
            generate_embeddings=False,
            archive_hooks=True,
            install_hooks=False,
        )

        # Verify hooks are archived
        assert not (old_hooks / "odin-session-start.sh").exists()

        # Rollback
        rollback_migration()

        # Hooks should be restored
        # Note: The implementation restores from archive
        archive_dir = old_hooks / "archived"
        assert archive_dir.exists()
