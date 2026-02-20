"""Tests for duplicate code nudge (Item 4.6)."""

import pytest
from unittest.mock import patch
from enki.orch.code_nudge import (
    check_for_reusable_code,
    build_dev_nudge,
    build_reviewer_instruction,
)


class TestCheckForReusableCode:
    def test_returns_matches(self):
        mock_result = {
            "code_knowledge": [
                {"content": "util function for dates", "file_ref": "utils/dates.py"},
            ]
        }
        with patch("enki.orch.recall.recall_for_dev", return_value=mock_result):
            matches = check_for_reusable_code("handle date formatting")
            assert len(matches) == 1
            assert matches[0]["file_ref"] == "utils/dates.py"

    def test_returns_empty_on_no_matches(self):
        mock_result = {"code_knowledge": []}
        with patch("enki.orch.recall.recall_for_dev", return_value=mock_result):
            matches = check_for_reusable_code("something unique")
            assert matches == []

    def test_returns_empty_on_error(self):
        with patch(
            "enki.orch.recall.recall_for_dev",
            side_effect=Exception("DB error"),
        ):
            matches = check_for_reusable_code("anything")
            assert matches == []

    def test_passes_project_and_limit(self):
        mock_result = {"code_knowledge": []}
        with patch("enki.orch.recall.recall_for_dev", return_value=mock_result) as mock:
            check_for_reusable_code("task", project="myproj", limit=3)
            mock.assert_called_once_with("task", project="myproj", limit=3)


class TestBuildDevNudge:
    def test_returns_none_on_empty(self):
        assert build_dev_nudge([]) is None

    def test_basic_nudge(self):
        matches = [
            {"content": "Date parser utility", "file_ref": "utils/dates.py"},
        ]
        result = build_dev_nudge(matches)
        assert result is not None
        assert "CODE REUSE ADVISORY" in result
        assert "utils/dates.py" in result
        assert "Date parser utility" in result

    def test_nudge_without_file_ref(self):
        matches = [{"content": "Some pattern for validation"}]
        result = build_dev_nudge(matches)
        assert "Some pattern for validation" in result
        assert "`" not in result.split("\n")[-1]  # No backtick wrapper without file_ref

    def test_truncates_long_content(self):
        matches = [{"content": "x" * 300, "file_ref": "file.py"}]
        result = build_dev_nudge(matches)
        # Content should be truncated to 200 chars
        lines = result.split("\n")
        match_line = [l for l in lines if "file.py" in l][0]
        assert len(match_line) < 300

    def test_caps_at_five(self):
        matches = [
            {"content": f"match {i}", "file_ref": f"file{i}.py"}
            for i in range(10)
        ]
        result = build_dev_nudge(matches)
        # Should only include first 5
        assert "file4.py" in result
        assert "file5.py" not in result

    def test_multiple_matches(self):
        matches = [
            {"content": "First match", "file_ref": "a.py"},
            {"content": "Second match", "file_ref": "b.py"},
        ]
        result = build_dev_nudge(matches)
        assert "a.py" in result
        assert "b.py" in result


class TestBuildReviewerInstruction:
    def test_returns_none_when_no_matches(self):
        assert build_reviewer_instruction(False) is None

    def test_returns_instruction_when_matches(self):
        result = build_reviewer_instruction(True)
        assert result is not None
        assert "Code knowledge" in result
        assert "duplicate" in result.lower()

    def test_instruction_mentions_flagging(self):
        result = build_reviewer_instruction(True)
        assert "Flag" in result or "flag" in result
