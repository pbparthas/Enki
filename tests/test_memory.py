"""Tests for Phase 2: Abzu Memory — beads, sessions, staging, extraction, retention."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from enki.db import connect


@pytest.fixture
def mem_env(tmp_path):
    """Set up isolated memory environment."""
    enki_root = tmp_path / ".enki"
    enki_root.mkdir()
    (enki_root / "persona").mkdir()
    (enki_root / "persona" / "PERSONA.md").write_text("# Enki\nTest persona.")

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.memory.abzu.ENKI_ROOT", enki_root):
        from enki.db import init_all
        init_all()
        yield enki_root


# ── Beads CRUD ──


class TestBeads:

    def test_create_and_get(self, mem_env):
        from enki.memory.beads import create, get

        bead = create("Use JWT for auth", "decision", project="cortex")
        assert bead["category"] == "decision"
        assert bead["project"] == "cortex"

        fetched = get(bead["id"])
        assert fetched["content"] == "Use JWT for auth"

    def test_create_all_categories(self, mem_env):
        from enki.memory.beads import create

        for cat in ("decision", "learning", "pattern", "fix", "preference"):
            bead = create(f"Test {cat}", cat)
            assert bead["category"] == cat

    def test_invalid_category_raises(self, mem_env):
        from enki.memory.beads import create

        with pytest.raises(ValueError, match="Invalid category"):
            create("test", "invalid_category")

    def test_dedup_by_content_hash(self, mem_env):
        from enki.memory.beads import create

        bead1 = create("Same content", "decision")
        bead2 = create("Same content", "decision")
        assert bead1["id"] == bead2["id"]

    def test_update_bead(self, mem_env):
        from enki.memory.beads import create, update

        bead = create("Original", "learning")
        updated = update(bead["id"], summary="Updated summary")
        assert updated["summary"] == "Updated summary"

    def test_delete_bead(self, mem_env):
        from enki.memory.beads import create, delete, get

        bead = create("To delete", "fix")
        assert delete(bead["id"])
        assert get(bead["id"]) is None

    def test_star_bead(self, mem_env):
        from enki.memory.beads import create, star

        bead = create("Important", "pattern")
        starred = star(bead["id"])
        assert starred["starred"] == 1
        assert starred["weight"] == 1.0

    def test_fts5_search(self, mem_env):
        from enki.memory.beads import create, search

        create("JWT authentication for stateless APIs", "decision", project="cortex")
        create("SQLite WAL mode for concurrency", "learning")
        create("Python type hints everywhere", "preference")

        results = search("JWT authentication")
        assert len(results) >= 1
        assert any("JWT" in r["content"] for r in results)

    def test_search_project_boost(self, mem_env):
        from enki.memory.beads import create, search

        create("Use Redis for caching", "decision", project="alpha")
        create("Use Redis for sessions", "decision", project="beta")

        results = search("Redis", project="alpha", scope="project")
        # Alpha project result should rank higher
        if len(results) >= 2:
            alpha = [r for r in results if r["project"] == "alpha"]
            beta = [r for r in results if r["project"] == "beta"]
            if alpha and beta:
                assert alpha[0]["final_score"] >= beta[0]["final_score"]

    def test_search_updates_last_accessed(self, mem_env):
        from enki.memory.beads import create, get, search

        bead = create("Unique keyword xyzzy for testing", "learning")
        old_accessed = bead["last_accessed"]

        search("xyzzy")
        updated = get(bead["id"])
        assert updated["last_accessed"] >= old_accessed

    def test_count(self, mem_env):
        from enki.memory.beads import count, create

        create("Bead 1", "decision")
        create("Bead 2", "learning")
        create("Bead 3", "decision")

        assert count() == 3
        assert count(category="decision") == 2
        assert count(category="learning") == 1

    def test_list_beads(self, mem_env):
        from enki.memory.beads import create, list_beads

        for i in range(5):
            create(f"Bead {i}", "pattern")

        beads = list_beads(limit=3)
        assert len(beads) == 3


# ── Sessions ──


class TestSessions:

    def test_create_and_get_summary(self, mem_env):
        from enki.memory.sessions import create_summary, get_accumulated_summaries

        create_summary("sess-1", project="proj", goal="Build v3",
                       operational_state="Working on Phase 0")

        summaries = get_accumulated_summaries("sess-1")
        assert len(summaries) == 1
        assert summaries[0]["goal"] == "Build v3"

    def test_summaries_accumulate(self, mem_env):
        from enki.memory.sessions import get_accumulated_summaries, update_pre_compact_summary

        update_pre_compact_summary("sess-1", "proj", "State 1", "Conv 1")
        update_pre_compact_summary("sess-1", "proj", "State 2", "Conv 2")
        update_pre_compact_summary("sess-1", "proj", "State 3", "Conv 3")

        summaries = get_accumulated_summaries("sess-1")
        assert len(summaries) == 3
        assert summaries[0]["sequence"] == 0
        assert summaries[2]["sequence"] == 2

    def test_finalize_session(self, mem_env):
        from enki.memory.sessions import (
            finalize_session,
            get_accumulated_summaries,
            get_last_final_summary,
            update_pre_compact_summary,
        )

        update_pre_compact_summary("sess-1", "proj", "State 1", "Conv 1")
        update_pre_compact_summary("sess-1", "proj", "State 2", "Conv 2")

        finalize_session("sess-1", "proj")

        # Pre-compact summaries should be cleaned up
        remaining = get_accumulated_summaries("sess-1")
        assert len(remaining) == 0

        # Final summary should exist
        final = get_last_final_summary("proj")
        assert final is not None
        assert final["is_final"] == 1

    def test_injection_budget_under(self, mem_env):
        from enki.memory.sessions import get_post_compact_injection, update_pre_compact_summary

        update_pre_compact_summary("sess-1", "proj", "Short state", "Short conv")

        injection = get_post_compact_injection("sess-1", "standard")
        assert "Short state" in injection

    def test_injection_budget_over(self, mem_env):
        from enki.memory.sessions import get_post_compact_injection, update_pre_compact_summary

        # Create summaries that exceed minimal budget (1500 tokens ~ 6000 chars)
        for i in range(10):
            update_pre_compact_summary(
                "sess-1", "proj",
                f"Operational state {i}: " + "x" * 1000,
                f"Conversational state {i}: " + "y" * 500,
            )

        injection = get_post_compact_injection("sess-1", "minimal")
        # Should be compressed under budget
        assert len(injection) < 10000  # Way less than the raw 15000 chars

    def test_cleanup_old_summaries(self, mem_env):
        from enki.memory.sessions import cleanup_old_summaries, create_summary

        # Create 8 final summaries
        for i in range(8):
            create_summary(f"sess-{i}", project="proj", is_final=True)

        deleted = cleanup_old_summaries("proj")
        assert deleted == 3  # Keep 5, delete 3


# ── Staging ──


class TestStaging:

    def test_add_and_get_candidate(self, mem_env):
        from enki.memory.staging import add_candidate, get_candidate

        cid = add_candidate("WAL mode is important", "learning", project="enki")
        assert cid is not None

        candidate = get_candidate(cid)
        assert candidate["category"] == "learning"

    def test_dedup_staging(self, mem_env):
        from enki.memory.staging import add_candidate

        cid1 = add_candidate("Same content", "decision")
        cid2 = add_candidate("Same content", "decision")
        assert cid1 is not None
        assert cid2 is None

    def test_dedup_cross_db(self, mem_env):
        """Adding a candidate that already exists in wisdom.db returns None."""
        from enki.memory.beads import create
        from enki.memory.staging import add_candidate

        create("Already in wisdom", "preference")
        cid = add_candidate("Already in wisdom", "decision")
        assert cid is None

    def test_promote_candidate(self, mem_env):
        from enki.memory.beads import get
        from enki.memory.staging import add_candidate, get_candidate, promote

        cid = add_candidate("Promote this", "decision")
        bead_id = promote(cid)

        assert bead_id is not None
        bead = get(bead_id)
        assert bead["content"] == "Promote this"

        # Candidate should be removed from staging
        assert get_candidate(cid) is None

    def test_discard_candidate(self, mem_env):
        from enki.memory.staging import add_candidate, discard, get_candidate

        cid = add_candidate("Discard this", "fix")
        assert discard(cid)
        assert get_candidate(cid) is None

    def test_search_candidates(self, mem_env):
        from enki.memory.staging import add_candidate, search_candidates

        add_candidate("SQLite FTS5 search optimization", "learning")
        add_candidate("Python asyncio patterns", "pattern")

        results = search_candidates("FTS5")
        assert len(results) >= 1

    def test_promote_batch(self, mem_env):
        from enki.memory.staging import add_candidate, promote_batch

        ids = []
        for i in range(3):
            cid = add_candidate(f"Batch bead {i}", "learning")
            ids.append(cid)

        stats = promote_batch(ids)
        assert stats["promoted"] == 3

    def test_count_candidates(self, mem_env):
        from enki.memory.staging import add_candidate, count_candidates

        for i in range(4):
            add_candidate(f"Candidate {i}", "fix")

        assert count_candidates() == 4


# ── Retention ──


class TestRetention:

    def test_decay_never_deletes(self, mem_env):
        from enki.memory.beads import count, create
        from enki.memory.retention import run_decay

        create("Old bead", "learning")
        before = count()
        run_decay()
        after = count()
        assert before == after

    def test_starred_never_decays(self, mem_env):
        from enki.memory.beads import create, get, star
        from enki.memory.retention import run_decay

        bead = create("Important bead", "decision")
        star(bead["id"])

        # Set last_accessed to 400 days ago
        from enki.db import wisdom_db
        old_date = (datetime.now() - timedelta(days=400)).isoformat()
        with wisdom_db() as conn:
            conn.execute(
                "UPDATE beads SET last_accessed = ? WHERE id = ?",
                (old_date, bead["id"]),
            )

        run_decay()
        updated = get(bead["id"])
        assert updated["weight"] == 1.0

    def test_preference_never_decays(self, mem_env):
        from enki.memory.beads import create, get
        from enki.memory.retention import run_decay

        bead = create("Always use TypeScript", "preference")

        from enki.db import wisdom_db
        old_date = (datetime.now() - timedelta(days=400)).isoformat()
        with wisdom_db() as conn:
            conn.execute(
                "UPDATE beads SET last_accessed = ? WHERE id = ?",
                (old_date, bead["id"]),
            )

        run_decay()
        updated = get(bead["id"])
        assert updated["weight"] == 1.0

    def test_decay_reduces_weight(self, mem_env):
        from enki.memory.beads import create, get
        from enki.memory.retention import run_decay

        bead = create("Will decay soon", "learning")

        from enki.db import wisdom_db
        old_date = (datetime.now() - timedelta(days=100)).isoformat()
        with wisdom_db() as conn:
            conn.execute(
                "UPDATE beads SET last_accessed = ? WHERE id = ?",
                (old_date, bead["id"]),
            )

        run_decay()
        updated = get(bead["id"])
        assert updated["weight"] < 1.0

    def test_decay_stats(self, mem_env):
        from enki.memory.beads import create
        from enki.memory.retention import get_decay_stats

        create("Bead 1", "decision")
        create("Bead 2", "preference")

        stats = get_decay_stats()
        assert stats["total"] == 2
        assert stats["hot"] >= 1


# ── Extraction ──


class TestExtraction:

    def test_validate_valid_jsonl(self, tmp_path, mem_env):
        from enki.memory.extraction import validate_jsonl_format

        jsonl_file = tmp_path / "transcript.jsonl"
        entry = {"type": "assistant", "message": "Hello", "timestamp": "2025-01-01"}
        jsonl_file.write_text(json.dumps(entry) + "\n")

        assert validate_jsonl_format(str(jsonl_file))

    def test_validate_invalid_jsonl(self, tmp_path, mem_env):
        from enki.memory.extraction import validate_jsonl_format

        jsonl_file = tmp_path / "bad.jsonl"
        jsonl_file.write_text('{"wrong": "format"}\n')

        assert not validate_jsonl_format(str(jsonl_file))

    def test_extract_decisions(self, tmp_path, mem_env):
        from enki.memory.extraction import extract_from_jsonl

        jsonl_file = tmp_path / "transcript.jsonl"
        entries = [
            {"type": "assistant", "message": "I decided to use JWT for auth", "timestamp": "t1"},
            {"type": "human", "message": "ok", "timestamp": "t2"},
        ]
        jsonl_file.write_text("\n".join(json.dumps(e) for e in entries))

        candidates = extract_from_jsonl(str(jsonl_file), "sess-1")
        decisions = [c for c in candidates if c["category"] == "decision"]
        assert len(decisions) >= 1

    def test_extract_from_text(self, mem_env):
        from enki.memory.extraction import extract_from_text

        text = "I learned that FTS5 is faster than manual LIKE queries."
        candidates = extract_from_text(text, "sess-1")
        assert any(c["category"] == "learning" for c in candidates)


# ── Abzu Facade ──


class TestAbzuFacade:

    def test_remember_preference_goes_to_wisdom(self, mem_env):
        from enki.memory.abzu import remember
        from enki.memory.beads import count

        result = remember("Always use strict TS", "preference")
        assert result["stored"] == "wisdom"
        assert count(category="preference") == 1

    def test_remember_decision_goes_to_staging(self, mem_env):
        from enki.memory.abzu import remember
        from enki.memory.staging import count_candidates

        result = remember("Use PostgreSQL", "decision")
        assert result["stored"] == "staging"
        assert count_candidates() == 1

    def test_recall_searches_both_dbs(self, mem_env):
        from enki.memory.abzu import recall, remember

        remember("JWT for stateless auth", "preference")  # wisdom
        remember("Redis for caching layer", "decision")  # staging

        results = recall("JWT")
        assert len(results) >= 1

    def test_star(self, mem_env):
        from enki.memory.abzu import remember, star
        from enki.memory.beads import get

        result = remember("Critical pattern", "preference")
        star(result["id"])
        bead = get(result["id"])
        assert bead["starred"] == 1

    def test_status(self, mem_env):
        from enki.memory.abzu import remember, status

        remember("Bead 1", "preference")
        remember("Bead 2", "decision")

        st = status()
        assert st["beads"]["total"] == 1  # Only preference in wisdom
        assert st["staging"]["candidates"] == 1  # Decision in staging

    def test_inject_session_start(self, mem_env):
        from enki.memory.abzu import inject_session_start, remember

        remember("JWT auth pattern", "preference", project="cortex")

        context = inject_session_start("cortex", "Add auth", "standard")
        assert "Enki" in context  # From persona

    def test_finalize_session(self, mem_env):
        from enki.memory.abzu import finalize_session, update_pre_compact_summary
        from enki.memory.sessions import get_last_final_summary

        update_pre_compact_summary("sess-1", "proj", "Did stuff", "Conv state")
        finalize_session("sess-1", "proj")

        final = get_last_final_summary("proj")
        assert final is not None


# ── Gemini Review ──


class TestGeminiReview:

    def test_generate_review_package(self, mem_env):
        from enki.memory.abzu import remember
        from enki.memory.gemini import generate_review_package

        remember("Test candidate", "decision")

        output_dir = str(mem_env / "reviews")
        path = generate_review_package(output_dir)
        assert os.path.exists(path)

        content = Path(path).read_text()
        assert "Staged Bead Candidates" in content

    def test_process_review_response(self, mem_env):
        from enki.memory.gemini import process_review_response
        from enki.memory.staging import add_candidate

        cid = add_candidate("Good bead", "learning")

        response = json.dumps({
            "bead_decisions": [
                {"candidate_id": cid, "action": "promote", "reason": "Valuable"}
            ],
            "proposal_decisions": []
        })

        stats = process_review_response(response)
        assert stats["promoted"] == 1
