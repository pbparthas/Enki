"""Tests for v4 link generation (Item 2.3).

Tests candidate retrieval, relationship classification, link storage,
cross-db links, and heuristic fallback logic.
"""

import struct
import uuid
from hashlib import sha256
from unittest.mock import patch

import numpy as np
import pytest

from enki.embeddings import EMBEDDING_DIM
from enki.links import (
    LINK_THRESHOLD,
    STRONG_THRESHOLD,
    _determine_relationship,
    _heuristic_classify,
    generate_links,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_enki(tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    with patch("enki.db.ENKI_ROOT", tmp_path), \
         patch("enki.db.DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield tmp_path


def _patch_db(tmp_enki):
    """Return a context manager that patches DB paths."""
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=tmp_enki,
        DB_DIR=tmp_enki / "db",
    )


def _make_embedding(seed: float) -> bytes:
    """Deterministic normalized embedding."""
    rng = np.random.RandomState(int(seed * 1000))
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def _similar_embedding(base_seed: float, noise: float = 0.05) -> bytes:
    """Create an embedding very similar to the base seed."""
    rng_base = np.random.RandomState(int(base_seed * 1000))
    vec = rng_base.randn(EMBEDDING_DIM).astype(np.float32)
    rng_noise = np.random.RandomState(42)
    vec = vec + noise * rng_noise.randn(EMBEDDING_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def _insert_wisdom_note(conn, content="test", category="learning",
                        project=None, embedding=None):
    nid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO notes (id, content, category, content_hash, project) "
        "VALUES (?, ?, ?, ?, ?)",
        (nid, content, category, chash, project),
    )
    if embedding:
        conn.execute(
            "INSERT INTO embeddings (note_id, vector) VALUES (?, ?)",
            (nid, embedding),
        )
    conn.commit()
    return nid


def _insert_abzu_candidate(conn, content="cand", category="learning",
                            project=None, embedding=None):
    cid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO note_candidates (id, content, category, content_hash, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, content, category, chash, "manual", project),
    )
    if embedding:
        conn.execute(
            "INSERT INTO candidate_embeddings (note_id, vector) VALUES (?, ?)",
            (cid, embedding),
        )
    conn.commit()
    return cid


# ---------------------------------------------------------------------------
# generate_links — wisdom.db
# ---------------------------------------------------------------------------


class TestGenerateLinksWisdom:
    def test_creates_links_for_similar_notes(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(1.0)
                similar_emb = _similar_embedding(1.0, noise=0.05)
                distant_emb = _make_embedding(99.0)

                n1 = _insert_wisdom_note(conn, "retry with backoff", "pattern", embedding=base_emb)
                n2 = _insert_wisdom_note(conn, "retry strategy exponential", "pattern", embedding=similar_emb)
                n3 = _insert_wisdom_note(conn, "chocolate cake recipe", "learning", embedding=distant_emb)
            finally:
                conn.close()

            links = generate_links(n1, "wisdom", k=10)
            target_ids = [l["target_id"] for l in links]
            # Should link to similar note
            assert n2 in target_ids
            # All links should have valid relationships
            for l in links:
                assert l["relationship"] in (
                    "relates_to", "supersedes", "contradicts", "extends",
                    "imports", "uses", "implements",
                )

    def test_no_self_link(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                emb = _make_embedding(1.0)
                n1 = _insert_wisdom_note(conn, "test note", "learning", embedding=emb)
            finally:
                conn.close()

            links = generate_links(n1, "wisdom", k=10)
            target_ids = [l["target_id"] for l in links]
            assert n1 not in target_ids

    def test_stores_in_note_links(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(2.0)
                similar_emb = _similar_embedding(2.0, noise=0.05)
                n1 = _insert_wisdom_note(conn, "database indexing strategy", "decision", embedding=base_emb)
                n2 = _insert_wisdom_note(conn, "index optimization approach", "decision", embedding=similar_emb)
            finally:
                conn.close()

            links = generate_links(n1, "wisdom", k=10)

            conn = get_wisdom_db()
            try:
                rows = conn.execute(
                    "SELECT * FROM note_links WHERE source_id = ?", (n1,)
                ).fetchall()
                assert len(rows) >= 1
                assert rows[0]["created_by"] == "auto_link"
            finally:
                conn.close()

    def test_no_embedding_returns_empty(self, tmp_enki):
        """Note without embedding produces no links."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                n1 = _insert_wisdom_note(conn, "no embedding note", "learning")
            finally:
                conn.close()

            links = generate_links(n1, "wisdom", k=10)
            assert links == []

    def test_no_similar_notes_returns_empty(self, tmp_enki):
        """Single note with no neighbors produces no links."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                emb = _make_embedding(3.0)
                n1 = _insert_wisdom_note(conn, "sole note", "learning", embedding=emb)
            finally:
                conn.close()

            links = generate_links(n1, "wisdom", k=10)
            assert links == []


# ---------------------------------------------------------------------------
# generate_links — abzu.db
# ---------------------------------------------------------------------------


class TestGenerateLinksAbzu:
    def test_creates_candidate_links(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_abzu_db
            conn = get_abzu_db()
            try:
                base_emb = _make_embedding(4.0)
                similar_emb = _similar_embedding(4.0, noise=0.05)
                c1 = _insert_abzu_candidate(conn, "api rate limiting", "pattern", embedding=base_emb)
                c2 = _insert_abzu_candidate(conn, "rate limit strategy", "pattern", embedding=similar_emb)
            finally:
                conn.close()

            links = generate_links(c1, "abzu", k=10)
            target_ids = [l["target_id"] for l in links]
            assert c2 in target_ids

            # Verify stored in candidate_links
            conn = get_abzu_db()
            try:
                rows = conn.execute(
                    "SELECT * FROM candidate_links WHERE source_id = ?", (c1,)
                ).fetchall()
                assert len(rows) >= 1
            finally:
                conn.close()

    def test_cross_db_links(self, tmp_enki):
        """Abzu candidate can link to wisdom note."""
        with _patch_db(tmp_enki):
            from enki.db import get_abzu_db, get_wisdom_db

            base_emb = _make_embedding(5.0)
            similar_emb = _similar_embedding(5.0, noise=0.05)

            w_conn = get_wisdom_db()
            try:
                w_id = _insert_wisdom_note(w_conn, "caching layer design", "decision", embedding=base_emb)
            finally:
                w_conn.close()

            a_conn = get_abzu_db()
            try:
                a_id = _insert_abzu_candidate(a_conn, "cache implementation plan", "decision", embedding=similar_emb)
            finally:
                a_conn.close()

            links = generate_links(a_id, "abzu", k=10)
            wisdom_links = [l for l in links if l["target_db"] == "wisdom"]
            assert len(wisdom_links) >= 1
            assert any(l["target_id"] == w_id for l in wisdom_links)


# ---------------------------------------------------------------------------
# Heuristic classification
# ---------------------------------------------------------------------------


class TestHeuristicClassification:
    def test_very_high_similarity_same_category_supersedes(self):
        rel = _determine_relationship("learning", "learning", 0.90, "a", "b")
        assert rel == "supersedes"

    def test_code_knowledge_uses(self):
        rel = _determine_relationship("code_knowledge", "learning", 0.5, "a", "b")
        assert rel == "uses"

    def test_fix_implements_decision(self):
        rel = _determine_relationship("fix", "decision", 0.5, "a", "b")
        assert rel == "implements"

    def test_pattern_implements_learning(self):
        rel = _determine_relationship("pattern", "learning", 0.5, "a", "b")
        assert rel == "implements"

    def test_default_relates_to(self):
        rel = _determine_relationship("learning", "decision", 0.5, "a", "b")
        assert rel == "relates_to"

    def test_below_threshold_skipped(self):
        candidates = [{"note_id": "x", "score": 0.1, "source_db": "wisdom"}]
        links = _heuristic_classify("content", "learning", candidates)
        assert len(links) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_db_raises(self, tmp_enki):
        with _patch_db(tmp_enki):
            with pytest.raises(ValueError, match="Unknown db"):
                generate_links("fake-id", "invalid_db")

    def test_nonexistent_note_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            links = generate_links("nonexistent-id", "wisdom")
            assert links == []

    def test_respects_k_limit(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(10.0)
                source = _insert_wisdom_note(conn, "source note", "learning", embedding=base_emb)
                for i in range(15):
                    emb = _similar_embedding(10.0, noise=0.05 + i * 0.01)
                    _insert_wisdom_note(conn, f"similar note {i}", "learning", embedding=emb)
            finally:
                conn.close()

            links = generate_links(source, "wisdom", k=3)
            assert len(links) <= 3

    def test_duplicate_link_ignored(self, tmp_enki):
        """Running generate_links twice doesn't create duplicate links."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(11.0)
                similar_emb = _similar_embedding(11.0, noise=0.05)
                n1 = _insert_wisdom_note(conn, "dedup source", "learning", embedding=base_emb)
                n2 = _insert_wisdom_note(conn, "dedup target", "learning", embedding=similar_emb)
            finally:
                conn.close()

            links1 = generate_links(n1, "wisdom", k=10)
            links2 = generate_links(n1, "wisdom", k=10)

            conn = get_wisdom_db()
            try:
                count = conn.execute(
                    "SELECT COUNT(*) as c FROM note_links WHERE source_id = ?", (n1,)
                ).fetchone()["c"]
                # Should not have duplicates
                assert count == len(links1)
            finally:
                conn.close()
