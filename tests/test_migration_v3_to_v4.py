"""Tests for v3 → v4 migration script (Item 5.1)."""

import hashlib
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# Import from scripts — add to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from migrate_v3_to_v4 import (
    CATEGORY_MAP,
    backup_databases,
    map_category,
    migrate_bead_to_note,
    migrate_bead_to_candidate,
    migrate_v3_candidate_to_v4,
    read_v3_beads,
    read_v3_candidates,
    run_migration,
    _table_exists,
    _connect,
)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _make_bead(category="learning", project=None, content="Test bead content"):
    return {
        "id": str(uuid.uuid4()),
        "content": content,
        "summary": "Test summary",
        "category": category,
        "project": project,
        "weight": 1.0,
        "starred": 0,
        "content_hash": _hash(content),
        "tags": "test",
        "context": "Some context",
        "created_at": "2025-01-01T00:00:00",
        "last_accessed": None,
        "promoted_at": None,
    }


def _make_candidate(category="learning", project=None, content="Test candidate"):
    return {
        "id": str(uuid.uuid4()),
        "content": content,
        "summary": "Test",
        "category": category,
        "project": project,
        "content_hash": _hash(content),
        "source": "manual",
        "session_id": None,
        "created_at": "2025-01-01T00:00:00",
    }


def _setup_v3_db(tmp_path):
    """Create v3 database structure with test data."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()

    # wisdom.db with v3 beads + v4 tables
    wisdom_path = db_dir / "wisdom.db"
    conn = _connect(wisdom_path)
    conn.execute("""
        CREATE TABLE projects (
            name TEXT PRIMARY KEY,
            path TEXT,
            primary_branch TEXT DEFAULT 'main',
            tech_stack TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE beads (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            category TEXT NOT NULL,
            project TEXT,
            weight REAL DEFAULT 1.0,
            starred INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            tags TEXT,
            context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,
            promoted_at TIMESTAMP,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)
    # v4 notes table
    conn.execute("""
        CREATE TABLE notes (
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
    conn.commit()
    conn.close()

    # abzu.db with v3 bead_candidates + v4 note_candidates
    abzu_path = db_dir / "abzu.db"
    conn = _connect(abzu_path)
    conn.execute("""
        CREATE TABLE bead_candidates (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            summary TEXT,
            category TEXT NOT NULL,
            project TEXT,
            content_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE note_candidates (
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
            status TEXT DEFAULT 'raw',
            file_ref TEXT,
            file_hash TEXT,
            content_hash TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN (
                'manual', 'session_end', 'code_scan', 'onboarding', 'rescan', 'em_distill'
            )),
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    return tmp_path


def _insert_v3_bead(tmp_path, bead):
    """Insert a bead into the v3 beads table."""
    wisdom_path = tmp_path / "db" / "wisdom.db"
    conn = _connect(wisdom_path)
    if bead.get("project"):
        conn.execute(
            "INSERT OR IGNORE INTO projects (name) VALUES (?)",
            (bead["project"],),
        )
    conn.execute(
        "INSERT INTO beads (id, content, summary, category, project, weight, "
        "starred, content_hash, tags, context, created_at, last_accessed, promoted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bead["id"], bead["content"], bead["summary"], bead["category"],
            bead["project"], bead["weight"], bead["starred"], bead["content_hash"],
            bead["tags"], bead["context"], bead["created_at"],
            bead["last_accessed"], bead["promoted_at"],
        ),
    )
    conn.commit()
    conn.close()


def _insert_v3_candidate(tmp_path, candidate):
    """Insert a bead_candidate into v3 abzu.db."""
    abzu_path = tmp_path / "db" / "abzu.db"
    conn = _connect(abzu_path)
    conn.execute(
        "INSERT INTO bead_candidates (id, content, summary, category, project, "
        "content_hash, source, session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidate["id"], candidate["content"], candidate["summary"],
            candidate["category"], candidate["project"], candidate["content_hash"],
            candidate["source"], candidate["session_id"], candidate["created_at"],
        ),
    )
    conn.commit()
    conn.close()


# ── Category Mapping ──


class TestCategoryMapping:
    def test_direct_mappings(self):
        assert map_category("decision") == "decision"
        assert map_category("learning") == "learning"
        assert map_category("pattern") == "pattern"
        assert map_category("fix") == "fix"
        assert map_category("preference") == "preference"

    def test_alias_mappings(self):
        assert map_category("solution") == "fix"
        assert map_category("violation") == "learning"

    def test_unknown_defaults_to_learning(self):
        assert map_category("unknown_cat") == "learning"


# ── Bead → Note conversion ──


class TestMigrateBeadToNote:
    def test_basic_conversion(self):
        bead = _make_bead(category="preference")
        note = migrate_bead_to_note(bead)
        assert note["id"] == bead["id"]
        assert note["content"] == bead["content"]
        assert note["category"] == "preference"
        assert note["context_description"] == bead["context"]
        assert note["weight"] == bead["weight"]
        assert note["promoted_at"] is not None

    def test_preserves_tags(self):
        bead = _make_bead()
        bead["tags"] = "python,testing"
        note = migrate_bead_to_note(bead)
        assert note["tags"] == "python,testing"

    def test_new_fields_are_none(self):
        bead = _make_bead()
        note = migrate_bead_to_note(bead)
        assert note["keywords"] is None
        assert note["file_ref"] is None
        assert note["file_hash"] is None
        assert note["last_verified"] is None
        assert note["evolved_at"] is None


# ── Bead → Candidate conversion ──


class TestMigrateBeadToCandidate:
    def test_basic_conversion(self):
        bead = _make_bead(category="decision")
        cand = migrate_bead_to_candidate(bead)
        assert cand["id"] == bead["id"]
        assert cand["content"] == bead["content"]
        assert cand["category"] == "decision"
        assert cand["status"] == "raw"
        assert cand["source"] == "manual"
        assert cand["context_description"] == bead["context"]

    def test_solution_maps_to_fix(self):
        bead = _make_bead(category="solution")
        cand = migrate_bead_to_candidate(bead)
        assert cand["category"] == "fix"


# ── v3 Candidate → v4 Candidate ──


class TestMigrateV3Candidate:
    def test_basic_conversion(self):
        cand = _make_candidate(category="learning")
        v4 = migrate_v3_candidate_to_v4(cand)
        assert v4["id"] == cand["id"]
        assert v4["category"] == "learning"
        assert v4["status"] == "raw"
        assert v4["source"] == "manual"

    def test_invalid_source_defaults_to_manual(self):
        cand = _make_candidate()
        cand["source"] = "unknown_source"
        v4 = migrate_v3_candidate_to_v4(cand)
        assert v4["source"] == "manual"

    def test_valid_source_preserved(self):
        cand = _make_candidate()
        cand["source"] = "session_end"
        v4 = migrate_v3_candidate_to_v4(cand)
        assert v4["source"] == "session_end"


# ── Backup ──


class TestBackup:
    def test_creates_backups(self, tmp_path):
        _setup_v3_db(tmp_path)
        result = backup_databases(tmp_path)
        assert "wisdom.db" in result
        assert "abzu.db" in result
        assert Path(result["wisdom.db"]).exists()
        assert Path(result["abzu.db"]).exists()

    def test_skips_existing_backup(self, tmp_path):
        _setup_v3_db(tmp_path)
        # Create existing backup
        bak = tmp_path / "db" / "wisdom.db.v3.bak"
        bak.write_text("existing")
        result = backup_databases(tmp_path)
        # Should skip wisdom but still backup abzu
        assert bak.read_text() == "existing"


# ── Read v3 data ──


class TestReadV3Data:
    def test_read_beads(self, tmp_path):
        _setup_v3_db(tmp_path)
        bead = _make_bead()
        _insert_v3_bead(tmp_path, bead)

        conn = _connect(tmp_path / "db" / "wisdom.db")
        beads = read_v3_beads(conn)
        conn.close()
        assert len(beads) == 1
        assert beads[0]["id"] == bead["id"]

    def test_read_no_beads_table(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = _connect(db_path)
        conn.execute("CREATE TABLE dummy (id TEXT)")
        conn.commit()
        beads = read_v3_beads(conn)
        conn.close()
        assert beads == []

    def test_read_candidates(self, tmp_path):
        _setup_v3_db(tmp_path)
        cand = _make_candidate()
        _insert_v3_candidate(tmp_path, cand)

        conn = _connect(tmp_path / "db" / "abzu.db")
        candidates = read_v3_candidates(conn)
        conn.close()
        assert len(candidates) == 1
        assert candidates[0]["id"] == cand["id"]


# ── Full Migration ──


class TestRunMigration:
    def test_dry_run(self, tmp_path):
        _setup_v3_db(tmp_path)
        _insert_v3_bead(tmp_path, _make_bead(category="preference", content="pref1"))
        _insert_v3_bead(tmp_path, _make_bead(category="decision", content="dec1"))
        _insert_v3_bead(tmp_path, _make_bead(category="learning", content="learn1"))

        result = run_migration(tmp_path, dry_run=True)
        assert result["dry_run"] is True
        assert result["beads_found"] == 3
        assert result["preferences_to_notes"] == 1
        assert result["beads_to_candidates"] == 2

        # Verify no data actually migrated
        conn = _connect(tmp_path / "db" / "wisdom.db")
        count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        conn.close()
        assert count == 0

    def test_full_migration_preferences(self, tmp_path):
        _setup_v3_db(tmp_path)
        pref = _make_bead(category="preference", content="Always use ruff")
        _insert_v3_bead(tmp_path, pref)

        result = run_migration(tmp_path)
        assert result["preferences_to_notes"] == 1
        assert result["errors"] == 0

        # Verify note in wisdom.db
        conn = _connect(tmp_path / "db" / "wisdom.db")
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (pref["id"],)).fetchone()
        conn.close()
        assert row is not None
        assert dict(row)["category"] == "preference"
        assert dict(row)["content"] == "Always use ruff"

    def test_full_migration_non_preferences(self, tmp_path):
        _setup_v3_db(tmp_path)
        dec = _make_bead(category="decision", content="Use FastAPI")
        _insert_v3_bead(tmp_path, dec)

        result = run_migration(tmp_path)
        assert result["beads_to_candidates"] == 1
        assert result["errors"] == 0

        # Verify candidate in abzu.db
        conn = _connect(tmp_path / "db" / "abzu.db")
        row = conn.execute(
            "SELECT * FROM note_candidates WHERE id = ?", (dec["id"],)
        ).fetchone()
        conn.close()
        assert row is not None
        assert dict(row)["status"] == "raw"
        assert dict(row)["source"] == "manual"

    def test_migrates_v3_candidates(self, tmp_path):
        _setup_v3_db(tmp_path)
        cand = _make_candidate(category="fix", content="Fix for issue #42")
        _insert_v3_candidate(tmp_path, cand)

        result = run_migration(tmp_path)
        assert result["v3_candidates_migrated"] == 1

        conn = _connect(tmp_path / "db" / "abzu.db")
        row = conn.execute(
            "SELECT * FROM note_candidates WHERE id = ?", (cand["id"],)
        ).fetchone()
        conn.close()
        assert row is not None
        assert dict(row)["category"] == "fix"

    def test_mixed_migration(self, tmp_path):
        _setup_v3_db(tmp_path)
        _insert_v3_bead(tmp_path, _make_bead(category="preference", content="pref1"))
        _insert_v3_bead(tmp_path, _make_bead(category="decision", content="dec1"))
        _insert_v3_bead(tmp_path, _make_bead(category="learning", content="learn1"))
        _insert_v3_bead(tmp_path, _make_bead(category="pattern", content="pat1"))
        _insert_v3_candidate(tmp_path, _make_candidate(category="fix", content="fix1"))

        result = run_migration(tmp_path)
        assert result["beads_found"] == 4
        assert result["preferences_to_notes"] == 1
        assert result["beads_to_candidates"] == 3
        assert result["v3_candidates_migrated"] == 1
        assert result["errors"] == 0

    def test_with_project_fk(self, tmp_path):
        _setup_v3_db(tmp_path)
        bead = _make_bead(category="preference", project="myproj", content="proj pref")
        _insert_v3_bead(tmp_path, bead)

        result = run_migration(tmp_path)
        assert result["errors"] == 0

        # Project should be auto-created for FK
        conn = _connect(tmp_path / "db" / "wisdom.db")
        proj = conn.execute(
            "SELECT name FROM projects WHERE name = ?", ("myproj",)
        ).fetchone()
        conn.close()
        assert proj is not None

    def test_creates_backups(self, tmp_path):
        _setup_v3_db(tmp_path)
        _insert_v3_bead(tmp_path, _make_bead(content="backup test"))

        run_migration(tmp_path)
        assert (tmp_path / "db" / "wisdom.db.v3.bak").exists()
        assert (tmp_path / "db" / "abzu.db.v3.bak").exists()

    def test_idempotent(self, tmp_path):
        """Running migration twice should not duplicate data (INSERT OR IGNORE)."""
        _setup_v3_db(tmp_path)
        bead = _make_bead(category="preference", content="idempotent test")
        _insert_v3_bead(tmp_path, bead)

        run_migration(tmp_path)
        # Remove backups so second run can proceed
        for bak in (tmp_path / "db").glob("*.v3.bak"):
            bak.unlink()
        run_migration(tmp_path)

        conn = _connect(tmp_path / "db" / "wisdom.db")
        count = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id = ?", (bead["id"],)
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_missing_wisdom_db(self, tmp_path):
        result = run_migration(tmp_path)
        assert "error" in result

    def test_context_maps_to_context_description(self, tmp_path):
        _setup_v3_db(tmp_path)
        bead = _make_bead(category="preference", content="ctx test")
        bead["context"] = "Important context here"
        _insert_v3_bead(tmp_path, bead)

        run_migration(tmp_path)

        conn = _connect(tmp_path / "db" / "wisdom.db")
        row = conn.execute(
            "SELECT context_description FROM notes WHERE id = ?", (bead["id"],)
        ).fetchone()
        conn.close()
        assert row[0] == "Important context here"

    def test_category_alias_migration(self, tmp_path):
        """solution → fix, violation → learning."""
        _setup_v3_db(tmp_path)

        # Need to insert with raw SQL since v3 beads table doesn't CHECK categories
        bead_sol = _make_bead(category="solution", content="sol bead")
        _insert_v3_bead(tmp_path, bead_sol)

        result = run_migration(tmp_path)
        assert result["beads_to_candidates"] == 1

        conn = _connect(tmp_path / "db" / "abzu.db")
        row = conn.execute(
            "SELECT category FROM note_candidates WHERE id = ?", (bead_sol["id"],)
        ).fetchone()
        conn.close()
        assert row[0] == "fix"
