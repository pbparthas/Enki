"""Tests for v4 EM recall responsibilities (Item 3.6).

Tests keyword extraction, recall for Architect/Dev,
scan-in-progress detection, and result formatting.
"""

from unittest.mock import patch

import pytest

from enki.orch.recall import (
    extract_keywords,
    format_recall_for_injection,
    recall_for_architect,
    recall_for_dev,
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


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_extracts_meaningful_words(self):
        text = "Implement JWT authentication with refresh tokens"
        keywords = extract_keywords(text)
        assert "authentication" in keywords
        assert "refresh" in keywords
        assert "tokens" in keywords

    def test_removes_stopwords(self):
        text = "The implementation should include proper testing"
        keywords = extract_keywords(text)
        assert "the" not in keywords
        assert "should" not in keywords

    def test_removes_short_words(self):
        text = "Add a new API for the app to use"
        keywords = extract_keywords(text)
        assert "add" not in keywords
        assert "new" not in keywords
        assert "api" not in keywords  # only 3 chars
        assert "the" not in keywords

    def test_deduplicates(self):
        text = "auth authentication auth token token"
        keywords = extract_keywords(text)
        assert keywords.count("auth") == 1
        assert keywords.count("token") == 1

    def test_caps_at_max(self):
        text = " ".join(f"keyword{i}" for i in range(20))
        keywords = extract_keywords(text, max_keywords=5)
        assert len(keywords) <= 5

    def test_empty_text(self):
        assert extract_keywords("") == []
        assert extract_keywords(None) == []

    def test_case_insensitive(self):
        text = "JWT Authentication TOKEN"
        keywords = extract_keywords(text)
        # All lowercase
        assert all(kw == kw.lower() for kw in keywords)


# ---------------------------------------------------------------------------
# recall_for_architect
# ---------------------------------------------------------------------------


class TestRecallForArchitect:
    def test_returns_results(self, tmp_enki):
        with _patch_db(tmp_enki):
            mock_notes = [
                {"note_id": "n1", "content": "Use JWT", "category": "decision"},
            ]
            with patch("enki.orch.recall._do_recall", return_value=mock_notes):
                results = recall_for_architect(
                    "Implement authentication with JWT tokens"
                )
                assert len(results) >= 1

    def test_deduplicates_across_keywords(self, tmp_enki):
        with _patch_db(tmp_enki):
            same_note = {"note_id": "n1", "content": "Use JWT", "category": "decision"}
            with patch("enki.orch.recall._do_recall", return_value=[same_note]):
                results = recall_for_architect(
                    "authentication tokens security"
                )
                # Should only appear once despite multiple keyword matches
                ids = [r.get("note_id") for r in results]
                assert ids.count("n1") == 1

    def test_handles_recall_failure(self, tmp_enki):
        with _patch_db(tmp_enki):
            with patch("enki.orch.recall._do_recall", side_effect=Exception("fail")):
                results = recall_for_architect("test spec")
                assert results == []

    def test_empty_spec(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert recall_for_architect("") == []


# ---------------------------------------------------------------------------
# recall_for_dev
# ---------------------------------------------------------------------------


class TestRecallForDev:
    def test_separates_code_knowledge(self, tmp_enki):
        with _patch_db(tmp_enki):
            notes = [
                {"note_id": "n1", "content": "DB pattern", "category": "code_knowledge"},
                {"note_id": "n2", "content": "Use JWT", "category": "decision"},
            ]
            with patch("enki.orch.recall._do_recall", return_value=notes):
                result = recall_for_dev("Implement auth module")
                assert len(result["code_knowledge"]) == 1
                assert len(result["notes"]) == 1

    def test_skips_when_scan_in_progress(self, tmp_enki):
        with _patch_db(tmp_enki):
            with patch("enki.orch.recall._is_scan_in_progress", return_value=True):
                result = recall_for_dev("task desc", project="proj")
                assert result["scan_in_progress"] is True
                assert result["code_knowledge"] == []
                assert result["notes"] == []

    def test_handles_recall_failure(self, tmp_enki):
        with _patch_db(tmp_enki):
            with patch("enki.orch.recall._do_recall", side_effect=Exception("fail")):
                result = recall_for_dev("task desc")
                assert result["code_knowledge"] == []
                assert result["notes"] == []


# ---------------------------------------------------------------------------
# format_recall_for_injection
# ---------------------------------------------------------------------------


class TestFormatRecall:
    def test_formats_notes(self):
        notes = [
            {"content": "Use JWT for auth", "category": "decision"},
            {"content": "WAL mode for SQLite", "category": "learning"},
        ]
        result = format_recall_for_injection(notes)
        assert "RECALLED KNOWLEDGE" in result
        assert "decision" in result
        assert "learning" in result

    def test_empty_returns_empty(self):
        assert format_recall_for_injection([]) == ""

    def test_custom_label(self):
        notes = [{"content": "test", "category": "fix"}]
        result = format_recall_for_injection(notes, "CODE CONTEXT")
        assert "CODE CONTEXT" in result

    def test_truncates_long_content(self):
        notes = [{"content": "x" * 500, "category": "learning"}]
        result = format_recall_for_injection(notes)
        # Content should be truncated to 200 chars
        assert len(result) < 500
