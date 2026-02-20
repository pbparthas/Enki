"""Tests for v4 memory evolution (Item 2.4).

Tests evolution checking, direct candidate evolution, wisdom proposal
creation, proposal approval/rejection, and heuristic keyword merging.
"""

import struct
import uuid
from hashlib import sha256
from unittest.mock import patch

import numpy as np
import pytest

from enki.embeddings import EMBEDDING_DIM
from enki.evolution import (
    _heuristic_evolution,
    apply_proposal,
    check_evolution,
    reject_proposal,
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
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=tmp_enki,
        DB_DIR=tmp_enki / "db",
    )


def _make_embedding(seed: float) -> bytes:
    rng = np.random.RandomState(int(seed * 1000))
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def _similar_embedding(base_seed: float, noise: float = 0.05) -> bytes:
    rng_base = np.random.RandomState(int(base_seed * 1000))
    vec = rng_base.randn(EMBEDDING_DIM).astype(np.float32)
    rng_noise = np.random.RandomState(42)
    vec = vec + noise * rng_noise.randn(EMBEDDING_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def _insert_wisdom_note(conn, content="test", category="learning",
                        keywords=None, embedding=None):
    nid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO notes (id, content, category, content_hash, keywords) "
        "VALUES (?, ?, ?, ?, ?)",
        (nid, content, category, chash, keywords),
    )
    if embedding:
        conn.execute(
            "INSERT INTO embeddings (note_id, vector) VALUES (?, ?)",
            (nid, embedding),
        )
    conn.commit()
    return nid


def _insert_abzu_candidate(conn, content="cand", category="learning",
                            keywords=None, embedding=None):
    cid = str(uuid.uuid4())
    chash = sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT INTO note_candidates (id, content, category, content_hash, source, keywords) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, content, category, chash, "manual", keywords),
    )
    if embedding:
        conn.execute(
            "INSERT INTO candidate_embeddings (note_id, vector) VALUES (?, ?)",
            (cid, embedding),
        )
    conn.commit()
    return cid


# ---------------------------------------------------------------------------
# Heuristic evolution
# ---------------------------------------------------------------------------


class TestHeuristicEvolution:
    def test_merges_new_keywords(self):
        result = _heuristic_evolution(
            new_content="retry with exponential backoff",
            new_category="pattern",
            new_keywords="retry,backoff,exponential",
            target_content="retry logic implementation for api calls retry",
            target_category="pattern",
            target_keywords="retry,api",
        )
        assert result is not None
        merged = result["proposed_keywords"].split(",")
        assert "backoff" in merged
        assert "retry" in merged
        assert "api" in merged

    def test_no_new_keywords_returns_none(self):
        result = _heuristic_evolution(
            new_content="retry logic",
            new_category="pattern",
            new_keywords="retry",
            target_content="retry implementation",
            target_category="pattern",
            target_keywords="retry",
        )
        assert result is None

    def test_irrelevant_keywords_not_added(self):
        result = _heuristic_evolution(
            new_content="chocolate cake recipe",
            new_category="learning",
            new_keywords="chocolate,cake",
            target_content="database indexing strategy",
            target_category="learning",
            target_keywords="database,indexing",
        )
        assert result is None

    def test_no_new_keywords_field_returns_none(self):
        result = _heuristic_evolution(
            new_content="something",
            new_category="learning",
            new_keywords=None,
            target_content="something else",
            target_category="learning",
            target_keywords="existing",
        )
        assert result is None

    def test_empty_target_keywords(self):
        result = _heuristic_evolution(
            new_content="retry pattern with backoff",
            new_category="pattern",
            new_keywords="retry,backoff",
            target_content="retry mechanism for resilience retry",
            target_category="pattern",
            target_keywords=None,
        )
        assert result is not None
        assert "retry" in result["proposed_keywords"]


# ---------------------------------------------------------------------------
# Direct evolution on abzu candidates
# ---------------------------------------------------------------------------


class TestDirectEvolution:
    def test_evolves_abzu_candidate(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_abzu_db
            conn = get_abzu_db()
            try:
                base_emb = _make_embedding(20.0)
                sim_emb = _similar_embedding(20.0, noise=0.05)

                target = _insert_abzu_candidate(
                    conn, "retry logic implementation retry",
                    "pattern", keywords="retry", embedding=base_emb,
                )
                new_note = _insert_abzu_candidate(
                    conn, "retry with exponential backoff retry",
                    "pattern", keywords="retry,backoff,exponential",
                    embedding=sim_emb,
                )
            finally:
                conn.close()

            related = [{"note_id": target, "source_db": "abzu", "score": 0.9}]
            actions = check_evolution(new_note, "abzu", related_notes=related)

            if actions:
                assert actions[0]["action"] == "direct_update"
                # Verify the candidate was updated
                conn = get_abzu_db()
                try:
                    row = conn.execute(
                        "SELECT keywords FROM note_candidates WHERE id = ?",
                        (target,),
                    ).fetchone()
                    if row["keywords"]:
                        assert "backoff" in row["keywords"]
                finally:
                    conn.close()


# ---------------------------------------------------------------------------
# Wisdom evolution proposals
# ---------------------------------------------------------------------------


class TestWisdomEvolutionProposal:
    def test_creates_proposal_for_wisdom_note(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_abzu_db, get_wisdom_db
            w_conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(30.0)
                target = _insert_wisdom_note(
                    w_conn, "caching layer design caching",
                    "decision", keywords="caching", embedding=base_emb,
                )
            finally:
                w_conn.close()

            a_conn = get_abzu_db()
            try:
                sim_emb = _similar_embedding(30.0, noise=0.05)
                new_note = _insert_abzu_candidate(
                    a_conn, "redis caching with ttl caching",
                    "decision", keywords="caching,redis,ttl",
                    embedding=sim_emb,
                )
            finally:
                a_conn.close()

            related = [{"note_id": target, "source_db": "wisdom", "score": 0.85}]
            actions = check_evolution(new_note, "abzu", related_notes=related)

            if actions:
                assert actions[0]["action"] == "proposal_created"
                assert actions[0]["target_db"] == "wisdom"

                # Verify proposal exists in abzu.db
                a_conn = get_abzu_db()
                try:
                    proposal = a_conn.execute(
                        "SELECT * FROM evolution_proposals WHERE id = ?",
                        (actions[0]["proposal_id"],),
                    ).fetchone()
                    assert proposal is not None
                    assert proposal["status"] == "pending"
                    assert proposal["target_note_id"] == target
                finally:
                    a_conn.close()

    def test_content_never_changed(self, tmp_enki):
        """Evolution must NEVER modify the content field."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            w_conn = get_wisdom_db()
            try:
                base_emb = _make_embedding(31.0)
                original_content = "original immutable content"
                target = _insert_wisdom_note(
                    w_conn, original_content,
                    "learning", keywords="test", embedding=base_emb,
                )
            finally:
                w_conn.close()

            related = [{"note_id": target, "source_db": "wisdom", "score": 0.9}]
            check_evolution("fake-new-id", "abzu", related_notes=related)

            w_conn = get_wisdom_db()
            try:
                row = w_conn.execute(
                    "SELECT content FROM notes WHERE id = ?", (target,)
                ).fetchone()
                assert row["content"] == original_content
            finally:
                w_conn.close()


# ---------------------------------------------------------------------------
# Proposal approval/rejection
# ---------------------------------------------------------------------------


class TestProposalApproval:
    def _create_test_proposal(self, tmp_enki):
        from enki.db import get_abzu_db, get_wisdom_db

        w_conn = get_wisdom_db()
        try:
            nid = str(uuid.uuid4())
            w_conn.execute(
                "INSERT INTO notes (id, content, category, content_hash, keywords) "
                "VALUES (?, ?, ?, ?, ?)",
                (nid, "test note", "learning", "ph1", "old_keyword"),
            )
            w_conn.commit()
        finally:
            w_conn.close()

        pid = str(uuid.uuid4())
        a_conn = get_abzu_db()
        try:
            a_conn.execute(
                "INSERT INTO evolution_proposals "
                "(id, target_note_id, triggered_by, proposed_keywords, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, nid, "trigger-123", "old_keyword,new_keyword", "test reason"),
            )
            a_conn.commit()
        finally:
            a_conn.close()

        return nid, pid

    def test_apply_proposal_updates_note(self, tmp_enki):
        with _patch_db(tmp_enki):
            nid, pid = self._create_test_proposal(tmp_enki)
            result = apply_proposal(pid)
            assert result is True

            from enki.db import get_wisdom_db
            w_conn = get_wisdom_db()
            try:
                row = w_conn.execute(
                    "SELECT keywords, evolved_at FROM notes WHERE id = ?", (nid,)
                ).fetchone()
                assert "new_keyword" in row["keywords"]
                assert row["evolved_at"] is not None
            finally:
                w_conn.close()

    def test_apply_proposal_marks_approved(self, tmp_enki):
        with _patch_db(tmp_enki):
            nid, pid = self._create_test_proposal(tmp_enki)
            apply_proposal(pid)

            from enki.db import get_abzu_db
            a_conn = get_abzu_db()
            try:
                row = a_conn.execute(
                    "SELECT status, reviewed_at FROM evolution_proposals WHERE id = ?",
                    (pid,),
                ).fetchone()
                assert row["status"] == "approved"
                assert row["reviewed_at"] is not None
            finally:
                a_conn.close()

    def test_apply_nonexistent_proposal_returns_false(self, tmp_enki):
        with _patch_db(tmp_enki):
            result = apply_proposal("nonexistent-id")
            assert result is False

    def test_reject_proposal(self, tmp_enki):
        with _patch_db(tmp_enki):
            nid, pid = self._create_test_proposal(tmp_enki)
            result = reject_proposal(pid)
            assert result is True

            from enki.db import get_abzu_db
            a_conn = get_abzu_db()
            try:
                row = a_conn.execute(
                    "SELECT status FROM evolution_proposals WHERE id = ?", (pid,)
                ).fetchone()
                assert row["status"] == "rejected"
            finally:
                a_conn.close()

    def test_reject_already_approved_fails(self, tmp_enki):
        with _patch_db(tmp_enki):
            nid, pid = self._create_test_proposal(tmp_enki)
            apply_proposal(pid)
            result = reject_proposal(pid)
            assert result is False

    def test_double_apply_fails(self, tmp_enki):
        with _patch_db(tmp_enki):
            nid, pid = self._create_test_proposal(tmp_enki)
            apply_proposal(pid)
            result = apply_proposal(pid)
            assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_nonexistent_note_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            actions = check_evolution("nonexistent", "wisdom")
            assert actions == []

    def test_no_related_notes_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                nid = _insert_wisdom_note(conn, "solo note", "learning")
            finally:
                conn.close()

            actions = check_evolution(nid, "wisdom", related_notes=[])
            assert actions == []
