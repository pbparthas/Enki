"""Tests for enrichment.py — batch enrichment with mocked Ollama."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from enki.memory.enrichment import (
    enrich_raw_candidates,
    generate_links_batch,
    run_daily_batch,
    _find_link_candidates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dbs(tmp_path, monkeypatch):
    """Set up temporary abzu.db and wisdom.db with proper schemas."""
    monkeypatch.setattr("enki.db.ENKI_ROOT", tmp_path)
    monkeypatch.setattr("enki.db.DB_DIR", tmp_path / "db")
    (tmp_path / "db").mkdir(exist_ok=True)

    from enki.db import get_abzu_db, get_wisdom_db

    # Create abzu.db tables
    conn = get_abzu_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS note_candidates (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                summary TEXT,
                context_description TEXT,
                keywords TEXT,
                tags TEXT,
                category TEXT NOT NULL,
                project TEXT,
                status TEXT DEFAULT 'raw',
                file_ref TEXT,
                file_hash TEXT,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_embeddings (
                note_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_links (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_db TEXT DEFAULT 'wisdom',
                relationship TEXT NOT NULL,
                UNIQUE(source_id, target_id, relationship)
            )
        """)
        conn.commit()
    finally:
        conn.close()

    # Create wisdom.db tables
    wconn = get_wisdom_db()
    try:
        wconn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT,
                keywords TEXT,
                tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        wconn.commit()
    finally:
        wconn.close()

    return tmp_path


def _insert_raw_candidate(conn, content="Test learning", category="learning",
                          project=None, cid=None):
    """Insert a raw note_candidate."""
    import hashlib
    cid = cid or str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO note_candidates "
        "(id, content, category, project, status, content_hash, source, created_at) "
        "VALUES (?, ?, ?, ?, 'raw', ?, 'manual', ?)",
        (cid, content, category, project, content_hash,
         datetime.now(timezone.utc).isoformat()),
    )
    return cid


def _insert_enriched_candidate(conn, content="Enriched note", category="learning",
                                cid=None):
    """Insert an enriched note_candidate (no links yet)."""
    import hashlib
    cid = cid or str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO note_candidates "
        "(id, content, category, project, status, content_hash, source, "
        "keywords, tags, summary, created_at) "
        "VALUES (?, ?, ?, NULL, 'enriched', ?, 'manual', "
        "'[\"test\"]', '[\"test\"]', 'summary', ?)",
        (cid, content, category, content_hash,
         datetime.now(timezone.utc).isoformat()),
    )
    return cid


# ---------------------------------------------------------------------------
# enrich_raw_candidates
# ---------------------------------------------------------------------------

class TestEnrichRawCandidates:
    def test_ollama_unavailable(self, tmp_dbs):
        with patch("enki.local_model.is_available", return_value=False):
            result = enrich_raw_candidates()
        assert result["processed"] == 0
        assert "Ollama not available" in result["errors"]

    def test_no_raw_candidates(self, tmp_dbs):
        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note") as mock_cn:
            result = enrich_raw_candidates()
        assert result["processed"] == 0
        assert result["failed"] == 0
        mock_cn.assert_not_called()

    def test_enriches_raw_candidates(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            cid = _insert_raw_candidate(conn, "Learn about pytest fixtures")
            conn.commit()
        finally:
            conn.close()

        mock_enriched = {
            "keywords": ["pytest", "fixtures"],
            "context_description": "Testing knowledge",
            "tags": ["testing", "python"],
            "summary": "About pytest fixtures",
        }

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", return_value=mock_enriched), \
             patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128):
            result = enrich_raw_candidates()

        assert result["processed"] == 1
        assert result["failed"] == 0

        # Verify DB was updated
        conn = get_abzu_db()
        try:
            row = conn.execute(
                "SELECT * FROM note_candidates WHERE id = ?", (cid,)
            ).fetchone()
            assert row["status"] == "enriched"
            assert json.loads(row["keywords"]) == ["pytest", "fixtures"]
            assert json.loads(row["tags"]) == ["testing", "python"]
            assert row["summary"] == "About pytest fixtures"

            # Check embedding was stored
            emb = conn.execute(
                "SELECT * FROM candidate_embeddings WHERE note_id = ?", (cid,)
            ).fetchone()
            assert emb is not None
        finally:
            conn.close()

    def test_multiple_candidates(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            for i in range(5):
                _insert_raw_candidate(conn, f"Learning {i}")
            conn.commit()
        finally:
            conn.close()

        mock_enriched = {
            "keywords": ["test"],
            "context_description": "desc",
            "tags": ["tag"],
            "summary": "sum",
        }

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", return_value=mock_enriched), \
             patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128):
            result = enrich_raw_candidates(limit=5)

        assert result["processed"] == 5

    def test_limit_respected(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            for i in range(10):
                _insert_raw_candidate(conn, f"Learning {i}")
            conn.commit()
        finally:
            conn.close()

        mock_enriched = {
            "keywords": [], "context_description": "",
            "tags": [], "summary": "",
        }

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", return_value=mock_enriched), \
             patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128):
            result = enrich_raw_candidates(limit=3)

        assert result["processed"] == 3

    def test_construct_note_failure_counted(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_raw_candidate(conn, "Will fail")
            conn.commit()
        finally:
            conn.close()

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", side_effect=Exception("model error")):
            result = enrich_raw_candidates()

        assert result["processed"] == 0
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    def test_embedding_failure_counted(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_raw_candidate(conn, "Will fail at embedding")
            conn.commit()
        finally:
            conn.close()

        mock_enriched = {
            "keywords": [], "context_description": "",
            "tags": [], "summary": "",
        }

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", return_value=mock_enriched), \
             patch("enki.embeddings.compute_embedding", side_effect=Exception("embed fail")):
            result = enrich_raw_candidates()

        assert result["failed"] == 1

    def test_keywords_as_string_handled(self, tmp_dbs):
        """If construct_note returns keywords as a string, should still work."""
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            cid = _insert_raw_candidate(conn, "String keywords test")
            conn.commit()
        finally:
            conn.close()

        mock_enriched = {
            "keywords": "already a string",
            "context_description": "desc",
            "tags": "also a string",
            "summary": "sum",
        }

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.construct_note", return_value=mock_enriched), \
             patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128):
            result = enrich_raw_candidates()

        assert result["processed"] == 1

        conn = get_abzu_db()
        try:
            row = conn.execute(
                "SELECT keywords FROM note_candidates WHERE id = ?", (cid,)
            ).fetchone()
            assert row["keywords"] == "already a string"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# generate_links_batch
# ---------------------------------------------------------------------------

class TestGenerateLinksBatch:
    def test_ollama_unavailable(self, tmp_dbs):
        with patch("enki.local_model.is_available", return_value=False):
            result = generate_links_batch()
        assert result["processed"] == 0
        assert "Ollama not available" in result["errors"]

    def test_no_enriched_candidates(self, tmp_dbs):
        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links") as mock_cl:
            result = generate_links_batch()
        assert result["processed"] == 0
        mock_cl.assert_not_called()

    def test_enriched_with_no_similar_notes(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_enriched_candidate(conn, "Lonely note")
            conn.commit()
        finally:
            conn.close()

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links") as mock_cl, \
             patch("enki.memory.enrichment._find_link_candidates", return_value=[]):
            result = generate_links_batch()

        assert result["processed"] == 1
        assert result["links_created"] == 0
        mock_cl.assert_not_called()

    def test_links_created(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            cid = _insert_enriched_candidate(conn, "Has links")
            conn.commit()
        finally:
            conn.close()

        target_id = str(uuid.uuid4())
        mock_links = [
            {"target_id": target_id, "target_db": "wisdom", "relationship": "related_to"},
        ]

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links", return_value=mock_links), \
             patch("enki.memory.enrichment._find_link_candidates",
                   return_value=[{"note_id": target_id, "content": "target", "category": "learning"}]):
            result = generate_links_batch()

        assert result["processed"] == 1
        assert result["links_created"] == 1

        conn = get_abzu_db()
        try:
            link = conn.execute(
                "SELECT * FROM candidate_links WHERE source_id = ?", (cid,)
            ).fetchone()
            assert link is not None
            assert link["target_id"] == target_id
            assert link["relationship"] == "related_to"
        finally:
            conn.close()

    def test_already_linked_skipped(self, tmp_dbs):
        """Candidates that already have links should be excluded."""
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            cid = _insert_enriched_candidate(conn, "Already linked")
            target_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO candidate_links (source_id, target_id, target_db, relationship) "
                "VALUES (?, ?, 'wisdom', 'related_to')",
                (cid, target_id),
            )
            conn.commit()
        finally:
            conn.close()

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links") as mock_cl:
            result = generate_links_batch()

        assert result["processed"] == 0
        mock_cl.assert_not_called()

    def test_classify_links_failure(self, tmp_dbs):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_enriched_candidate(conn, "Will fail linking")
            conn.commit()
        finally:
            conn.close()

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links", side_effect=Exception("classify fail")), \
             patch("enki.memory.enrichment._find_link_candidates",
                   return_value=[{"note_id": "x", "content": "y", "category": "z"}]):
            result = generate_links_batch()

        assert len(result["errors"]) == 1
        assert result["links_created"] == 0

    def test_duplicate_links_ignored(self, tmp_dbs):
        """INSERT OR IGNORE should prevent duplicate links."""
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            cid = _insert_enriched_candidate(conn, "Dup test")
            conn.commit()
        finally:
            conn.close()

        target_id = str(uuid.uuid4())
        mock_links = [
            {"target_id": target_id, "target_db": "wisdom", "relationship": "related_to"},
            {"target_id": target_id, "target_db": "wisdom", "relationship": "related_to"},
        ]

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links", return_value=mock_links), \
             patch("enki.memory.enrichment._find_link_candidates",
                   return_value=[{"note_id": target_id, "content": "t", "category": "l"}]):
            result = generate_links_batch()

        # Should count 2 in links_created (the INSERT OR IGNORE silently skips dups)
        # but only 1 actually in DB
        conn = get_abzu_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM candidate_links WHERE source_id = ?", (cid,)
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_empty_links_response(self, tmp_dbs):
        """classify_links returns empty list → no links created but still processed."""
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_enriched_candidate(conn, "No links returned")
            conn.commit()
        finally:
            conn.close()

        with patch("enki.local_model.is_available", return_value=True), \
             patch("enki.local_model.classify_links", return_value=[]), \
             patch("enki.memory.enrichment._find_link_candidates",
                   return_value=[{"note_id": "x", "content": "y", "category": "z"}]):
            result = generate_links_batch()

        assert result["processed"] == 1
        assert result["links_created"] == 0


# ---------------------------------------------------------------------------
# run_daily_batch
# ---------------------------------------------------------------------------

class TestRunDailyBatch:
    def test_calls_both_phases(self, tmp_dbs):
        with patch("enki.memory.enrichment.enrich_raw_candidates",
                   return_value={"processed": 3, "failed": 0, "errors": []}) as mock_e, \
             patch("enki.memory.enrichment.generate_links_batch",
                   return_value={"processed": 2, "links_created": 5, "errors": []}) as mock_l:
            result = run_daily_batch()

        mock_e.assert_called_once()
        mock_l.assert_called_once()
        assert result["enrich"]["processed"] == 3
        assert result["links"]["links_created"] == 5

    def test_enrich_failure_doesnt_block_links(self, tmp_dbs):
        """If enrich raises, links should still run."""
        # Note: run_daily_batch doesn't catch exceptions from enrich,
        # so this tests that enrich returns error dict (not raises)
        with patch("enki.memory.enrichment.enrich_raw_candidates",
                   return_value={"processed": 0, "failed": 1, "errors": ["oops"]}), \
             patch("enki.memory.enrichment.generate_links_batch",
                   return_value={"processed": 0, "links_created": 0, "errors": []}):
            result = run_daily_batch()

        assert result["enrich"]["failed"] == 1
        assert result["links"] is not None


# ---------------------------------------------------------------------------
# _find_link_candidates
# ---------------------------------------------------------------------------

class TestFindLinkCandidates:
    def test_returns_empty_on_embedding_failure(self, tmp_dbs):
        with patch("enki.embeddings.compute_embedding", side_effect=Exception("fail")):
            result = _find_link_candidates("src-id", "some content")
        assert result == []

    def test_finds_wisdom_matches(self, tmp_dbs):
        from enki.db import get_wisdom_db
        wconn = get_wisdom_db()
        try:
            wconn.execute(
                "INSERT INTO notes (id, content, category) VALUES (?, ?, ?)",
                ("note-1", "Related wisdom note", "learning"),
            )
            wconn.commit()
        finally:
            wconn.close()

        with patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128), \
             patch("enki.embeddings.search_similar",
                   return_value=[("note-1", 0.85)]):
            result = _find_link_candidates("src-id", "query content")

        assert len(result) >= 1
        assert result[0]["note_id"] == "note-1"
        assert result[0]["source_db"] == "wisdom"

    def test_low_score_filtered_out(self, tmp_dbs):
        from enki.db import get_wisdom_db
        wconn = get_wisdom_db()
        try:
            wconn.execute(
                "INSERT INTO notes (id, content, category) VALUES (?, ?, ?)",
                ("note-low", "Low relevance", "learning"),
            )
            wconn.commit()
        finally:
            wconn.close()

        with patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128), \
             patch("enki.embeddings.search_similar",
                   return_value=[("note-low", 0.1)]):
            result = _find_link_candidates("src-id", "query")

        assert len(result) == 0

    def test_self_excluded_from_abzu(self, tmp_dbs):
        """Source ID should not link to itself."""
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            _insert_enriched_candidate(conn, "Self note", cid="self-id")
            conn.commit()
        finally:
            conn.close()

        with patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128), \
             patch("enki.embeddings.search_similar",
                   return_value=[("self-id", 0.9)]):
            result = _find_link_candidates("self-id", "query")

        assert not any(c["note_id"] == "self-id" for c in result)

    def test_results_sorted_by_score(self, tmp_dbs):
        from enki.db import get_wisdom_db
        wconn = get_wisdom_db()
        try:
            wconn.execute(
                "INSERT INTO notes (id, content, category) VALUES (?, ?, ?)",
                ("high", "High score", "learning"),
            )
            wconn.execute(
                "INSERT INTO notes (id, content, category) VALUES (?, ?, ?)",
                ("mid", "Mid score", "learning"),
            )
            wconn.commit()
        finally:
            wconn.close()

        with patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128), \
             patch("enki.embeddings.search_similar",
                   return_value=[("mid", 0.5), ("high", 0.9)]):
            result = _find_link_candidates("src-id", "query")

        assert result[0]["note_id"] == "high"
        assert result[1]["note_id"] == "mid"

    def test_capped_at_limit(self, tmp_dbs):
        from enki.db import get_wisdom_db
        wconn = get_wisdom_db()
        try:
            for i in range(10):
                wconn.execute(
                    "INSERT INTO notes (id, content, category) VALUES (?, ?, ?)",
                    (f"note-{i}", f"Note {i}", "learning"),
                )
            wconn.commit()
        finally:
            wconn.close()

        matches = [(f"note-{i}", 0.9 - i * 0.05) for i in range(10)]

        with patch("enki.embeddings.compute_embedding", return_value=b"\x00" * 128), \
             patch("enki.embeddings.search_similar", return_value=matches):
            result = _find_link_candidates("src-id", "query", limit=3)

        assert len(result) <= 3
