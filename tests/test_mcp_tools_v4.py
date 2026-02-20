"""Tests for v4 MCP memory tool implementations.

Tests enki_remember, enki_recall, enki_star, enki_status, enki_restore
with v4 note model (notes + note_candidates tables).
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest


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


# ---------------------------------------------------------------------------
# enki_remember
# ---------------------------------------------------------------------------


class TestEnkiRemember:
    def test_stores_preference_in_wisdom(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Always use pytest",
                category="preference",
                project="test-proj",
            )
            assert result["stored"] == "wisdom"
            assert result["category"] == "preference"
            assert "id" in result

    def test_stores_learning_in_staging(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="SQLite WAL mode improves concurrency",
                category="learning",
            )
            assert result["stored"] == "staging"
            assert result["category"] == "learning"
            assert "id" in result

    def test_stores_decision_in_staging(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Use JWT for authentication",
                category="decision",
            )
            assert result["stored"] == "staging"

    def test_stores_fix_in_staging(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Fixed race condition in DB init",
                category="fix",
            )
            assert result["stored"] == "staging"

    def test_stores_pattern_in_staging(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Singleton pattern for DB connections",
                category="pattern",
            )
            assert result["stored"] == "staging"

    def test_rejects_empty_content(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(content="", category="learning")
            assert result["stored"] == "rejected"

    def test_rejects_whitespace_only(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(content="   \n  ", category="learning")
            assert result["stored"] == "rejected"

    def test_detects_duplicate_preference(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            enki_remember(content="Always use pytest", category="preference")
            result = enki_remember(content="Always use pytest", category="preference")
            assert result["stored"] == "duplicate"

    def test_detects_duplicate_candidate(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            enki_remember(content="Use WAL mode", category="learning")
            result = enki_remember(content="Use WAL mode", category="learning")
            assert result["stored"] == "duplicate"

    def test_stores_with_summary_and_tags(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Important decision about architecture",
                category="decision",
                summary="Architecture choice",
                tags="arch,design",
            )
            assert result["stored"] == "staging"

            # Verify in DB
            from enki.db import get_abzu_db
            conn = get_abzu_db()
            try:
                row = conn.execute(
                    "SELECT summary, tags FROM note_candidates WHERE id = ?",
                    (result["id"],),
                ).fetchone()
                assert row["summary"] == "Architecture choice"
                assert row["tags"] == "arch,design"
            finally:
                conn.close()

    def test_preference_persisted_in_wisdom_db(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_remember
            result = enki_remember(
                content="Prefer dark mode",
                category="preference",
                project="my-proj",
            )

            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                row = conn.execute(
                    "SELECT content, category, project FROM notes WHERE id = ?",
                    (result["id"],),
                ).fetchone()
                assert row["content"] == "Prefer dark mode"
                assert row["category"] == "preference"
                assert row["project"] == "my-proj"
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# enki_recall
# ---------------------------------------------------------------------------


class TestEnkiRecall:
    def test_returns_empty_for_no_matches(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_recall
            results = enki_recall(query="nonexistent topic xyz")
            assert results == []

    def test_returns_empty_for_empty_query(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_recall
            assert enki_recall(query="") == []
            assert enki_recall(query="   ") == []

    def test_falls_back_to_v3_on_error(self, tmp_enki):
        """If v4 hybrid search fails, falls back to v3 recall."""
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_recall
            with patch("enki.embeddings.hybrid_search", side_effect=Exception("broken")):
                # Should not raise — falls back to v3
                results = enki_recall(query="test")
                # v3 may or may not find anything, but shouldn't crash
                assert isinstance(results, list)

    def test_recall_with_project_filter(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_recall
            results = enki_recall(
                query="test", scope="project", project="my-proj"
            )
            assert isinstance(results, list)


# ---------------------------------------------------------------------------
# enki_star
# ---------------------------------------------------------------------------


class TestEnkiStar:
    def test_stars_v4_note(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            from enki.mcp.memory_tools import enki_star

            # Insert a note
            conn = get_wisdom_db()
            note_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO notes (id, content, category, content_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (note_id, "test note", "learning", "hash1"),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_star(note_id)
            assert result["starred"] is True
            assert result["note_id"] == note_id

    def test_stars_v3_bead_fallback(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            from enki.mcp.memory_tools import enki_star

            # Insert a v3 bead
            conn = get_wisdom_db()
            bead_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO beads (id, content, category, content_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (bead_id, "v3 bead", "learning", "bhash1"),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_star(bead_id)
            assert result["starred"] is True
            assert result["bead_id"] == bead_id

    def test_star_nonexistent_returns_false(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_star
            result = enki_star("fake-id-12345")
            assert result["starred"] is False

    def test_star_prefers_v4_over_v3(self, tmp_enki):
        """If same ID in both tables, v4 wins."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            from enki.mcp.memory_tools import enki_star

            shared_id = str(uuid.uuid4())
            conn = get_wisdom_db()
            try:
                conn.execute(
                    "INSERT INTO notes (id, content, category, content_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (shared_id, "v4 note", "learning", "nhash"),
                )
                conn.execute(
                    "INSERT INTO beads (id, content, category, content_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (shared_id, "v3 bead", "learning", "bhash"),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_star(shared_id)
            assert "note_id" in result  # v4 path


# ---------------------------------------------------------------------------
# enki_status
# ---------------------------------------------------------------------------


class TestEnkiStatus:
    def test_returns_status_structure(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_status
            result = enki_status()
            assert "notes" in result
            assert "staging" in result
            assert "v3_beads" in result
            assert "db_sizes" in result

    def test_counts_notes_by_category(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            from enki.mcp.memory_tools import enki_status

            conn = get_wisdom_db()
            try:
                for cat in ("preference", "learning", "decision"):
                    conn.execute(
                        "INSERT INTO notes (id, content, category, content_hash) "
                        "VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4()), f"{cat} note", cat, f"h_{cat}"),
                    )
                conn.commit()
            finally:
                conn.close()

            result = enki_status()
            assert result["notes"]["total"] == 3
            assert result["notes"]["preference"] == 1
            assert result["notes"]["learning"] == 1
            assert result["notes"]["decision"] == 1

    def test_counts_staging_candidates(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_abzu_db
            from enki.mcp.memory_tools import enki_status

            conn = get_abzu_db()
            try:
                conn.execute(
                    "INSERT INTO note_candidates "
                    "(id, content, category, content_hash, source, status) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), "raw note", "learning", "rh1", "manual", "raw"),
                )
                conn.execute(
                    "INSERT INTO note_candidates "
                    "(id, content, category, content_hash, source, status) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), "enriched note", "pattern", "eh1", "manual", "enriched"),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_status()
            assert result["staging"]["candidates"] == 2
            assert result["staging"]["raw"] == 1
            assert result["staging"]["enriched"] == 1

    def test_empty_system_returns_zeros(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_status
            result = enki_status()
            assert result["notes"]["total"] == 0
            assert result["staging"]["candidates"] == 0
            assert result["v3_beads"]["total"] == 0


# ---------------------------------------------------------------------------
# enki_restore
# ---------------------------------------------------------------------------


class TestEnkiRestore:
    def test_returns_restore_structure(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_restore
            result = enki_restore()
            assert result["restored"] is True
            assert "content" in result
            assert "chars" in result
            assert result["chars"] <= 6000

    def test_includes_persona(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_restore
            result = enki_restore()
            assert "Enki" in result["content"]

    def test_includes_enforcement_header(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_restore
            result = enki_restore()
            assert "Enforcement" in result["content"]

    def test_includes_project_when_specified(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_restore
            result = enki_restore(project="my-project")
            assert "my-project" in result["content"]

    def test_truncates_long_content(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp.memory_tools import enki_restore

            # Inject a very long session summary
            from enki.db import get_abzu_db
            conn = get_abzu_db()
            try:
                conn.execute(
                    "INSERT INTO session_summaries "
                    "(id, session_id, goal, operational_state, conversational_state, is_final) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("s1", "sess-1", "big goal", "x" * 10000, "state", 0),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_restore()
            assert result["chars"] <= 6200  # Allow small overhead from truncation message

    def test_includes_recent_knowledge_for_project(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            from enki.mcp.memory_tools import enki_restore

            conn = get_wisdom_db()
            try:
                conn.execute(
                    "INSERT INTO projects (name) VALUES (?)", ("my-proj",)
                )
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO notes (id, content, category, content_hash, "
                    "project, last_accessed) VALUES (?, ?, ?, ?, ?, ?)",
                    ("n1", "Important pattern about retries", "pattern",
                     "ph1", "my-proj", now),
                )
                conn.commit()
            finally:
                conn.close()

            result = enki_restore(project="my-proj")
            assert "Recent Knowledge" in result["content"]
            assert "retries" in result["content"]


# ---------------------------------------------------------------------------
# MCP server dispatch
# ---------------------------------------------------------------------------


class TestMCPDispatch:
    def test_restore_in_tool_list(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import get_tools
            tools = get_tools()
            names = [t["name"] for t in tools]
            assert "enki_restore" in names

    def test_restore_handler_works(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import handle_tool
            result = handle_tool("enki_restore", {})
            assert result["restored"] is True

    def test_remember_handler_works(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import handle_tool
            result = handle_tool("enki_remember", {
                "content": "Test knowledge",
                "category": "learning",
            })
            assert result["stored"] in ("staging", "duplicate")

    def test_status_handler_works(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import handle_tool
            result = handle_tool("enki_status", {})
            assert "notes" in result

    def test_star_handler_works(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import handle_tool
            result = handle_tool("enki_star", {"bead_id": "fake-id"})
            assert result["starred"] is False

    def test_category_enum_includes_code_knowledge(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.mcp_server import get_tools
            tools = get_tools()
            remember = [t for t in tools if t["name"] == "enki_remember"][0]
            cats = remember["inputSchema"]["properties"]["category"]["enum"]
            assert "code_knowledge" in cats
