"""Tests for v4 note schema (Item 2.1).

Verifies all v4 tables, indexes, FTS5, triggers, and CHECK constraints
are created correctly in wisdom.db and abzu.db.
"""

import sqlite3
import uuid
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_enki(tmp_path):
    """Set up isolated ENKI_ROOT with fresh databases."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    with patch("enki.db.ENKI_ROOT", tmp_path), \
         patch("enki.db.DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield tmp_path


@pytest.fixture
def wisdom_conn(tmp_enki):
    """Return a connection to the test wisdom.db."""
    with patch("enki.db.ENKI_ROOT", tmp_enki), \
         patch("enki.db.DB_DIR", tmp_enki / "db"):
        from enki.db import get_wisdom_db
        conn = get_wisdom_db()
        yield conn
        conn.close()


@pytest.fixture
def abzu_conn(tmp_enki):
    """Return a connection to the test abzu.db."""
    with patch("enki.db.ENKI_ROOT", tmp_enki), \
         patch("enki.db.DB_DIR", tmp_enki / "db"):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        yield conn
        conn.close()


# ---------------------------------------------------------------------------
# wisdom.db table existence
# ---------------------------------------------------------------------------


class TestWisdomV4Tables:
    def test_notes_table_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM notes LIMIT 0")

    def test_embeddings_table_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM embeddings LIMIT 0")

    def test_note_links_table_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM note_links LIMIT 0")

    def test_projects_table_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM projects LIMIT 0")

    def test_notes_fts_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM notes_fts LIMIT 0")

    def test_projects_has_v4_columns(self, wisdom_conn):
        """projects table gains primary_branch and tech_stack columns."""
        wisdom_conn.execute("SELECT primary_branch, tech_stack FROM projects LIMIT 0")

    def test_projects_primary_branch_default(self, wisdom_conn):
        """primary_branch defaults to 'main'."""
        wisdom_conn.execute(
            "INSERT INTO projects (name, path) VALUES ('test-proj', '/tmp/test')"
        )
        row = wisdom_conn.execute(
            "SELECT primary_branch FROM projects WHERE name = 'test-proj'"
        ).fetchone()
        assert row["primary_branch"] == "main"


# ---------------------------------------------------------------------------
# wisdom.db notes column schema
# ---------------------------------------------------------------------------


class TestNotesSchema:
    def _insert_note(self, conn, **overrides):
        defaults = {
            "id": str(uuid.uuid4()),
            "content": "test content",
            "category": "learning",
            "content_hash": sha256(b"test").hexdigest(),
        }
        defaults.update(overrides)
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join("?" for _ in defaults)
        conn.execute(
            f"INSERT INTO notes ({cols}) VALUES ({placeholders})",
            list(defaults.values()),
        )
        return defaults["id"]

    def test_insert_all_categories(self, wisdom_conn):
        for cat in ("decision", "learning", "pattern", "fix", "preference", "code_knowledge"):
            self._insert_note(wisdom_conn, id=str(uuid.uuid4()), category=cat)

    def test_rejects_invalid_category(self, wisdom_conn):
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_note(wisdom_conn, category="bogus")

    def test_code_knowledge_fields(self, wisdom_conn):
        """code_knowledge notes can use file_ref, file_hash, last_verified."""
        nid = self._insert_note(
            wisdom_conn,
            category="code_knowledge",
            file_ref="src/main.py",
            file_hash="abc123",
            last_verified="2025-01-01T00:00:00",
        )
        row = wisdom_conn.execute(
            "SELECT file_ref, file_hash, last_verified FROM notes WHERE id = ?",
            (nid,),
        ).fetchone()
        assert row["file_ref"] == "src/main.py"
        assert row["file_hash"] == "abc123"

    def test_weight_default(self, wisdom_conn):
        nid = self._insert_note(wisdom_conn)
        row = wisdom_conn.execute(
            "SELECT weight FROM notes WHERE id = ?", (nid,)
        ).fetchone()
        assert row["weight"] == 1.0

    def test_starred_default(self, wisdom_conn):
        nid = self._insert_note(wisdom_conn)
        row = wisdom_conn.execute(
            "SELECT starred FROM notes WHERE id = ?", (nid,)
        ).fetchone()
        assert row["starred"] == 0


# ---------------------------------------------------------------------------
# wisdom.db embeddings
# ---------------------------------------------------------------------------


class TestEmbeddingsSchema:
    def test_insert_embedding(self, wisdom_conn):
        nid = str(uuid.uuid4())
        wisdom_conn.execute(
            "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
            (nid, "test", "learning", "h1"),
        )
        fake_vec = b"\x00" * 1536  # 384 floats * 4 bytes
        wisdom_conn.execute(
            "INSERT INTO embeddings (note_id, vector) VALUES (?, ?)",
            (nid, fake_vec),
        )
        row = wisdom_conn.execute(
            "SELECT model, vector FROM embeddings WHERE note_id = ?", (nid,)
        ).fetchone()
        assert row["model"] == "all-MiniLM-L6-v2"
        assert len(row["vector"]) == 1536

    def test_cascade_delete(self, wisdom_conn):
        nid = str(uuid.uuid4())
        wisdom_conn.execute(
            "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
            (nid, "test", "learning", "h2"),
        )
        wisdom_conn.execute(
            "INSERT INTO embeddings (note_id, vector) VALUES (?, ?)",
            (nid, b"\x00" * 100),
        )
        wisdom_conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
        row = wisdom_conn.execute(
            "SELECT 1 FROM embeddings WHERE note_id = ?", (nid,)
        ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# wisdom.db note_links
# ---------------------------------------------------------------------------


class TestNoteLinksSchema:
    def _make_two_notes(self, conn):
        ids = []
        for i in range(2):
            nid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
                (nid, f"note {i}", "learning", f"hash{i}"),
            )
            ids.append(nid)
        return ids

    def test_valid_relationships(self, wisdom_conn):
        valid = ("relates_to", "supersedes", "contradicts", "extends",
                 "imports", "uses", "implements")
        for rel in valid:
            ids = self._make_two_notes(wisdom_conn)
            wisdom_conn.execute(
                "INSERT INTO note_links (source_id, target_id, relationship, created_by) "
                "VALUES (?, ?, ?, ?)",
                (ids[0], ids[1], rel, "test"),
            )

    def test_rejects_invalid_relationship(self, wisdom_conn):
        ids = self._make_two_notes(wisdom_conn)
        with pytest.raises(sqlite3.IntegrityError):
            wisdom_conn.execute(
                "INSERT INTO note_links (source_id, target_id, relationship, created_by) "
                "VALUES (?, ?, ?, ?)",
                (ids[0], ids[1], "bogus_rel", "test"),
            )

    def test_cascade_delete_on_source(self, wisdom_conn):
        ids = self._make_two_notes(wisdom_conn)
        wisdom_conn.execute(
            "INSERT INTO note_links (source_id, target_id, relationship, created_by) "
            "VALUES (?, ?, ?, ?)",
            (ids[0], ids[1], "relates_to", "test"),
        )
        wisdom_conn.execute("DELETE FROM notes WHERE id = ?", (ids[0],))
        row = wisdom_conn.execute(
            "SELECT 1 FROM note_links WHERE source_id = ?", (ids[0],)
        ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# wisdom.db FTS5 triggers
# ---------------------------------------------------------------------------


class TestNotesFTS:
    def test_insert_populates_fts(self, wisdom_conn):
        nid = str(uuid.uuid4())
        wisdom_conn.execute(
            "INSERT INTO notes (id, content, summary, context_description, keywords, tags, category, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (nid, "exponential backoff retry", "retry pattern", "error handling context",
             "retry,backoff", "infrastructure", "pattern", "fts_h1"),
        )
        rows = wisdom_conn.execute(
            "SELECT * FROM notes_fts WHERE notes_fts MATCH 'exponential'"
        ).fetchall()
        assert len(rows) == 1

    def test_delete_removes_from_fts(self, wisdom_conn):
        nid = str(uuid.uuid4())
        wisdom_conn.execute(
            "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
            (nid, "unique_fts_delete_test_content", "learning", "fts_h2"),
        )
        wisdom_conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
        rows = wisdom_conn.execute(
            "SELECT * FROM notes_fts WHERE notes_fts MATCH 'unique_fts_delete_test_content'"
        ).fetchall()
        assert len(rows) == 0

    def test_update_refreshes_fts(self, wisdom_conn):
        nid = str(uuid.uuid4())
        wisdom_conn.execute(
            "INSERT INTO notes (id, content, category, content_hash, keywords) VALUES (?, ?, ?, ?, ?)",
            (nid, "original content", "learning", "fts_h3", "old_keyword"),
        )
        wisdom_conn.execute(
            "UPDATE notes SET keywords = ? WHERE id = ?",
            ("new_keyword_unique", nid),
        )
        rows = wisdom_conn.execute(
            "SELECT * FROM notes_fts WHERE notes_fts MATCH 'new_keyword_unique'"
        ).fetchall()
        assert len(rows) == 1
        # Old keyword no longer matches
        rows_old = wisdom_conn.execute(
            "SELECT * FROM notes_fts WHERE notes_fts MATCH 'old_keyword'"
        ).fetchall()
        assert len(rows_old) == 0


# ---------------------------------------------------------------------------
# abzu.db table existence
# ---------------------------------------------------------------------------


class TestAbzuV4Tables:
    def test_note_candidates_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM note_candidates LIMIT 0")

    def test_candidate_embeddings_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM candidate_embeddings LIMIT 0")

    def test_candidate_links_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM candidate_links LIMIT 0")

    def test_evolution_proposals_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM evolution_proposals LIMIT 0")

    def test_session_summaries_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM session_summaries LIMIT 0")

    def test_onboarding_status_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM onboarding_status LIMIT 0")

    def test_extraction_log_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM extraction_log LIMIT 0")

    def test_candidates_fts_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM candidates_v4_fts LIMIT 0")


# ---------------------------------------------------------------------------
# abzu.db note_candidates schema
# ---------------------------------------------------------------------------


class TestNoteCandidatesSchema:
    def _insert_candidate(self, conn, **overrides):
        defaults = {
            "id": str(uuid.uuid4()),
            "content": "candidate content",
            "category": "learning",
            "content_hash": sha256(b"cand").hexdigest(),
            "source": "manual",
        }
        defaults.update(overrides)
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join("?" for _ in defaults)
        conn.execute(
            f"INSERT INTO note_candidates ({cols}) VALUES ({placeholders})",
            list(defaults.values()),
        )
        return defaults["id"]

    def test_excludes_preference_category(self, abzu_conn):
        """note_candidates must NOT accept 'preference' — those bypass staging."""
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_candidate(abzu_conn, category="preference")

    def test_accepts_code_knowledge(self, abzu_conn):
        self._insert_candidate(abzu_conn, category="code_knowledge")

    def test_valid_categories(self, abzu_conn):
        for cat in ("decision", "learning", "pattern", "fix", "code_knowledge"):
            self._insert_candidate(abzu_conn, id=str(uuid.uuid4()), category=cat)

    def test_status_check_constraint(self, abzu_conn):
        self._insert_candidate(abzu_conn, status="raw")
        self._insert_candidate(abzu_conn, id=str(uuid.uuid4()), status="enriched")
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_candidate(abzu_conn, id=str(uuid.uuid4()), status="invalid")

    def test_source_check_constraint(self, abzu_conn):
        valid_sources = ("manual", "session_end", "code_scan", "onboarding", "rescan", "em_distill")
        for src in valid_sources:
            self._insert_candidate(abzu_conn, id=str(uuid.uuid4()), source=src)
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_candidate(abzu_conn, id=str(uuid.uuid4()), source="invalid_source")

    def test_status_default_raw(self, abzu_conn):
        cid = self._insert_candidate(abzu_conn)
        row = abzu_conn.execute(
            "SELECT status FROM note_candidates WHERE id = ?", (cid,)
        ).fetchone()
        assert row["status"] == "raw"


# ---------------------------------------------------------------------------
# abzu.db candidate_links
# ---------------------------------------------------------------------------


class TestCandidateLinksSchema:
    def test_target_db_column(self, abzu_conn):
        """candidate_links has target_db to track cross-db references."""
        cid = str(uuid.uuid4())
        abzu_conn.execute(
            "INSERT INTO note_candidates (id, content, category, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "test", "learning", "lh1", "manual"),
        )
        abzu_conn.execute(
            "INSERT INTO candidate_links (source_id, target_id, target_db, relationship) "
            "VALUES (?, ?, ?, ?)",
            (cid, "wisdom-note-123", "wisdom", "relates_to"),
        )
        row = abzu_conn.execute(
            "SELECT target_db FROM candidate_links WHERE source_id = ?", (cid,)
        ).fetchone()
        assert row["target_db"] == "wisdom"

    def test_target_db_check_constraint(self, abzu_conn):
        cid = str(uuid.uuid4())
        abzu_conn.execute(
            "INSERT INTO note_candidates (id, content, category, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "test", "learning", "lh2", "manual"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            abzu_conn.execute(
                "INSERT INTO candidate_links (source_id, target_id, target_db, relationship) "
                "VALUES (?, ?, ?, ?)",
                (cid, "target-1", "invalid_db", "relates_to"),
            )


# ---------------------------------------------------------------------------
# abzu.db evolution_proposals
# ---------------------------------------------------------------------------


class TestEvolutionProposalsSchema:
    def test_insert_proposal(self, abzu_conn):
        pid = str(uuid.uuid4())
        abzu_conn.execute(
            "INSERT INTO evolution_proposals "
            "(id, target_note_id, triggered_by, proposed_keywords, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, "note-123", "note-456", "new,keywords", "related content found"),
        )
        row = abzu_conn.execute(
            "SELECT status FROM evolution_proposals WHERE id = ?", (pid,)
        ).fetchone()
        assert row["status"] == "pending"

    def test_status_check_constraint(self, abzu_conn):
        with pytest.raises(sqlite3.IntegrityError):
            abzu_conn.execute(
                "INSERT INTO evolution_proposals "
                "(id, target_note_id, triggered_by, reason, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "n1", "n2", "reason", "invalid_status"),
            )


# ---------------------------------------------------------------------------
# abzu.db onboarding_status
# ---------------------------------------------------------------------------


class TestOnboardingStatusSchema:
    def test_insert_onboarding(self, abzu_conn):
        abzu_conn.execute(
            "INSERT INTO onboarding_status (project) VALUES (?)",
            ("test-project",),
        )
        row = abzu_conn.execute(
            "SELECT codebase_scan FROM onboarding_status WHERE project = ?",
            ("test-project",),
        ).fetchone()
        assert row["codebase_scan"] == "pending"

    def test_codebase_scan_check_constraint(self, abzu_conn):
        with pytest.raises(sqlite3.IntegrityError):
            abzu_conn.execute(
                "INSERT INTO onboarding_status (project, codebase_scan) VALUES (?, ?)",
                ("bad-proj", "invalid_state"),
            )


# ---------------------------------------------------------------------------
# abzu.db candidates FTS triggers
# ---------------------------------------------------------------------------


class TestCandidatesFTS:
    def test_insert_populates_fts(self, abzu_conn):
        cid = str(uuid.uuid4())
        abzu_conn.execute(
            "INSERT INTO note_candidates "
            "(id, content, summary, keywords, category, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, "exponential backoff", "retry strategy", "retry,backoff",
             "pattern", "cfts1", "manual"),
        )
        rows = abzu_conn.execute(
            "SELECT * FROM candidates_v4_fts WHERE candidates_v4_fts MATCH 'exponential'"
        ).fetchall()
        assert len(rows) == 1

    def test_delete_removes_from_fts(self, abzu_conn):
        cid = str(uuid.uuid4())
        abzu_conn.execute(
            "INSERT INTO note_candidates "
            "(id, content, category, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "unique_candidate_fts_delete_test", "learning", "cfts2", "manual"),
        )
        abzu_conn.execute("DELETE FROM note_candidates WHERE id = ?", (cid,))
        rows = abzu_conn.execute(
            "SELECT * FROM candidates_v4_fts WHERE candidates_v4_fts MATCH 'unique_candidate_fts_delete_test'"
        ).fetchall()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# abzu.db extraction_log v4 CHECK constraint
# ---------------------------------------------------------------------------


class TestExtractionLogV4:
    def test_extraction_log_method_constraint(self, abzu_conn):
        """v3 extraction_log has no method CHECK; v4 adds one on note_candidates source."""
        # extraction_log still exists from v3 — just verify it works
        abzu_conn.execute(
            "INSERT INTO extraction_log (id, session_id, method) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), "sess-1", "heuristic"),
        )


# ---------------------------------------------------------------------------
# v3 tables still present (backward compat)
# ---------------------------------------------------------------------------


class TestV3TablesStillPresent:
    def test_beads_table_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM beads LIMIT 0")

    def test_beads_fts_exists(self, wisdom_conn):
        wisdom_conn.execute("SELECT 1 FROM beads_fts LIMIT 0")

    def test_bead_candidates_exists(self, abzu_conn):
        abzu_conn.execute("SELECT 1 FROM bead_candidates LIMIT 0")

    def test_candidates_fts_exists(self, abzu_conn):
        """v3 candidates_fts backed by bead_candidates still exists."""
        abzu_conn.execute("SELECT 1 FROM candidates_fts LIMIT 0")


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_init_all_twice(self, tmp_path):
        """Calling init_all() twice does not error."""
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        with patch("enki.db.ENKI_ROOT", tmp_path), \
             patch("enki.db.DB_DIR", db_dir):
            from enki.db import init_all
            init_all()
            init_all()  # Second call should be fine
