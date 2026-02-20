"""Tests for v4 embedding infrastructure (Item 2.2).

Tests compute_embedding, search_similar, hybrid_search,
and BLOB ↔ array round-trips.
"""

import struct
import uuid
from hashlib import sha256
from unittest.mock import patch

import numpy as np
import pytest

from enki.embeddings import (
    BLOB_SIZE,
    EMBEDDING_DIM,
    blob_to_array,
    compute_embedding,
    hybrid_search,
    search_similar,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_enki(tmp_path):
    """Isolated ENKI_ROOT with v4 tables."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    with patch("enki.db.ENKI_ROOT", tmp_path), \
         patch("enki.db.DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield tmp_path


@pytest.fixture
def wisdom_conn(tmp_enki):
    with patch("enki.db.ENKI_ROOT", tmp_enki), \
         patch("enki.db.DB_DIR", tmp_enki / "db"):
        from enki.db import get_wisdom_db
        conn = get_wisdom_db()
        yield conn
        conn.close()


@pytest.fixture
def abzu_conn(tmp_enki):
    with patch("enki.db.ENKI_ROOT", tmp_enki), \
         patch("enki.db.DB_DIR", tmp_enki / "db"):
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        yield conn
        conn.close()


def _fake_embedding(seed: float = 1.0) -> bytes:
    """Create a deterministic fake embedding BLOB."""
    vec = np.random.RandomState(int(seed * 1000)).randn(EMBEDDING_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)  # normalize
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def _insert_note_with_embedding(conn, content="test", category="learning",
                                project=None, embedding_seed=1.0):
    """Insert a note + embedding into wisdom.db."""
    nid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO notes (id, content, category, content_hash, project) "
        "VALUES (?, ?, ?, ?, ?)",
        (nid, content, category, chash, project),
    )
    emb = _fake_embedding(embedding_seed)
    conn.execute(
        "INSERT INTO embeddings (note_id, vector) VALUES (?, ?)",
        (nid, emb),
    )
    conn.commit()
    return nid, emb


def _insert_candidate_with_embedding(conn, content="cand", category="learning",
                                     project=None, embedding_seed=2.0):
    """Insert a note_candidate + embedding into abzu.db."""
    cid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO note_candidates (id, content, category, content_hash, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, content, category, chash, "manual", project),
    )
    emb = _fake_embedding(embedding_seed)
    conn.execute(
        "INSERT INTO candidate_embeddings (note_id, vector) VALUES (?, ?)",
        (cid, emb),
    )
    conn.commit()
    return cid, emb


# ---------------------------------------------------------------------------
# compute_embedding
# ---------------------------------------------------------------------------


class TestComputeEmbedding:
    def test_returns_correct_size(self):
        vec = compute_embedding("retry logic with exponential backoff")
        assert len(vec) == BLOB_SIZE  # 384 * 4 = 1536

    def test_round_trip(self):
        vec_blob = compute_embedding("hello world")
        arr = blob_to_array(vec_blob)
        assert arr.shape == (EMBEDDING_DIM,)
        assert arr.dtype == np.float32

    def test_normalized(self):
        vec_blob = compute_embedding("some test text")
        arr = blob_to_array(vec_blob)
        norm = np.linalg.norm(arr)
        assert abs(norm - 1.0) < 0.01  # Should be unit-normalized

    def test_empty_string_returns_zeros(self):
        vec = compute_embedding("")
        assert vec == b"\x00" * BLOB_SIZE

    def test_whitespace_only_returns_zeros(self):
        vec = compute_embedding("   ")
        assert vec == b"\x00" * BLOB_SIZE

    def test_similar_texts_similar_embeddings(self):
        v1 = blob_to_array(compute_embedding("retry with exponential backoff"))
        v2 = blob_to_array(compute_embedding("exponential backoff retry logic"))
        v3 = blob_to_array(compute_embedding("chocolate cake recipe"))
        sim_related = float(np.dot(v1, v2))
        sim_unrelated = float(np.dot(v1, v3))
        assert sim_related > sim_unrelated


# ---------------------------------------------------------------------------
# blob_to_array
# ---------------------------------------------------------------------------


class TestBlobToArray:
    def test_round_trip(self):
        original = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        blob = struct.pack(f"{EMBEDDING_DIM}f", *original.tolist())
        restored = blob_to_array(blob)
        np.testing.assert_array_almost_equal(original, restored)


# ---------------------------------------------------------------------------
# search_similar
# ---------------------------------------------------------------------------


class TestSearchSimilar:
    def test_finds_similar_in_wisdom(self, tmp_enki, wisdom_conn):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            n1, e1 = _insert_note_with_embedding(wisdom_conn, "retry logic", embedding_seed=1.0)
            n2, e2 = _insert_note_with_embedding(wisdom_conn, "caching layer", embedding_seed=5.0)

            results = search_similar(e1, "wisdom", limit=10)
            assert len(results) == 2
            # First result should be itself (highest similarity)
            assert results[0][0] == n1
            assert results[0][1] > results[1][1]

    def test_finds_similar_in_abzu(self, tmp_enki, abzu_conn):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            c1, e1 = _insert_candidate_with_embedding(abzu_conn, "db indexing", embedding_seed=3.0)
            c2, e2 = _insert_candidate_with_embedding(abzu_conn, "api design", embedding_seed=7.0)

            results = search_similar(e1, "abzu", limit=10)
            assert len(results) == 2
            assert results[0][0] == c1

    def test_respects_limit(self, tmp_enki, wisdom_conn):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            for i in range(5):
                _insert_note_with_embedding(wisdom_conn, f"note {i}", embedding_seed=float(i + 1))

            results = search_similar(_fake_embedding(1.0), "wisdom", limit=3)
            assert len(results) == 3

    def test_empty_db_returns_empty(self, tmp_enki):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            results = search_similar(_fake_embedding(1.0), "wisdom", limit=10)
            assert results == []

    def test_invalid_db_raises(self, tmp_enki):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            with pytest.raises(ValueError, match="Unknown db"):
                search_similar(_fake_embedding(1.0), "invalid")


# ---------------------------------------------------------------------------
# hybrid_search
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_fts_only_search(self, tmp_enki, wisdom_conn):
        """Finds results via FTS even without embeddings."""
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            nid = str(uuid.uuid4())
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash, keywords) "
                "VALUES (?, ?, ?, ?, ?)",
                (nid, "exponential backoff retry pattern", "pattern", "fh1", "retry,backoff"),
            )
            wisdom_conn.commit()

            results = hybrid_search("exponential backoff")
            assert len(results) >= 1
            assert any(r["note_id"] == nid for r in results)

    def test_abzu_multiplier(self, tmp_enki, wisdom_conn, abzu_conn):
        """Abzu results get 0.7 multiplier."""
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            # Same content in both DBs
            w_id = str(uuid.uuid4())
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
                (w_id, "unique hybrid search test content", "learning", "hm1"),
            )
            wisdom_conn.commit()

            a_id = str(uuid.uuid4())
            abzu_conn.execute(
                "INSERT INTO note_candidates (id, content, category, content_hash, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (a_id, "unique hybrid search test content", "learning", "hm2", "manual"),
            )
            abzu_conn.commit()

            results = hybrid_search("unique hybrid search test content")
            w_result = next((r for r in results if r["note_id"] == w_id), None)
            a_result = next((r for r in results if r["note_id"] == a_id), None)
            if w_result and a_result:
                assert w_result["score"] >= a_result["score"]

    def test_project_filter(self, tmp_enki, wisdom_conn):
        """Project filter restricts results."""
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            wisdom_conn.execute(
                "INSERT INTO projects (name) VALUES (?)", ("proj-a",)
            )
            wisdom_conn.execute(
                "INSERT INTO projects (name) VALUES (?)", ("proj-b",)
            )

            n1 = str(uuid.uuid4())
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash, project) "
                "VALUES (?, ?, ?, ?, ?)",
                (n1, "project filter test alpha", "learning", "pf1", "proj-a"),
            )
            n2 = str(uuid.uuid4())
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash, project) "
                "VALUES (?, ?, ?, ?, ?)",
                (n2, "project filter test beta", "learning", "pf2", "proj-b"),
            )
            wisdom_conn.commit()

            results = hybrid_search("project filter test", project="proj-a")
            ids = [r["note_id"] for r in results]
            assert n1 in ids
            assert n2 not in ids

    def test_empty_query_returns_empty(self, tmp_enki):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            results = hybrid_search("")
            assert results == []

    def test_link_expansion(self, tmp_enki, wisdom_conn):
        """1-hop links are included in results."""
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            n1 = str(uuid.uuid4())
            n2 = str(uuid.uuid4())
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
                (n1, "link expansion source content unique", "learning", "le1"),
            )
            wisdom_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
                (n2, "linked target not matching query", "decision", "le2"),
            )
            wisdom_conn.execute(
                "INSERT INTO note_links (source_id, target_id, relationship, created_by) "
                "VALUES (?, ?, ?, ?)",
                (n1, n2, "relates_to", "test"),
            )
            wisdom_conn.commit()

            results = hybrid_search("link expansion source content unique")
            ids = [r["note_id"] for r in results]
            assert n1 in ids
            assert n2 in ids  # Pulled in via link
            linked_result = next(r for r in results if r["note_id"] == n2)
            assert linked_result["via_link"] is True

    def test_respects_limit(self, tmp_enki, wisdom_conn):
        with patch("enki.db.ENKI_ROOT", tmp_enki), \
             patch("enki.db.DB_DIR", tmp_enki / "db"):
            for i in range(10):
                nid = str(uuid.uuid4())
                wisdom_conn.execute(
                    "INSERT INTO notes (id, content, category, content_hash) VALUES (?, ?, ?, ?)",
                    (nid, f"limit test item number {i}", "learning", f"lt{i}"),
                )
            wisdom_conn.commit()

            results = hybrid_search("limit test item", limit=3)
            assert len(results) <= 3
