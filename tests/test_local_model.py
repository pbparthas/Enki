"""Tests for v4 local model integration (Item 2.5).

All tests mock the Ollama API since it may not be running.
Tests verify prompt construction, JSON parsing, retry logic,
and all 5 operations.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from enki.local_model import (
    MAX_RETRIES,
    OllamaUnavailableError,
    _generate,
    _generate_json,
    _parse_json,
    check_evolution,
    classify_links,
    construct_note,
    extract_code_knowledge,
    extract_from_transcript,
    is_available,
)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_plain_json_object(self):
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_plain_json_array(self):
        result = _parse_json('[{"id": 1}]')
        assert result == [{"id": 1}]

    def test_markdown_fenced_json(self):
        text = '```json\n{"key": "value"}\n```'
        result = _parse_json(text)
        assert result == {"key": "value"}

    def test_markdown_fenced_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        result = _parse_json(text)
        assert result == {"key": "value"}

    def test_whitespace_padding(self):
        result = _parse_json('  \n  {"key": "value"}  \n  ')
        assert result == {"key": "value"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json at all")


# ---------------------------------------------------------------------------
# _generate — Ollama communication
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_successful_generation(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "hello"}
        mock_response.raise_for_status = MagicMock()

        with patch("enki.local_model.httpx.post", return_value=mock_response):
            result = _generate("test prompt")
            assert result == "hello"

    def test_connection_error_raises(self):
        import httpx
        with patch("enki.local_model.httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(OllamaUnavailableError):
                _generate("test")

    def test_http_error_raises(self):
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )
        with patch("enki.local_model.httpx.post", return_value=mock_response):
            with pytest.raises(OllamaUnavailableError):
                _generate("test")


# ---------------------------------------------------------------------------
# _generate_json — retry logic
# ---------------------------------------------------------------------------


class TestGenerateJson:
    def test_valid_json_first_try(self):
        with patch("enki.local_model._generate", return_value='{"key": "value"}'):
            result = _generate_json("prompt")
            assert result == {"key": "value"}

    def test_retries_on_parse_failure(self):
        call_count = 0

        def mock_generate(prompt, model=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "not json"
            return '{"key": "value"}'

        with patch("enki.local_model._generate", side_effect=mock_generate):
            result = _generate_json("prompt")
            assert result == {"key": "value"}
            assert call_count == 2

    def test_raises_after_max_retries(self):
        with patch("enki.local_model._generate", return_value="not json ever"):
            with pytest.raises(ValueError, match="Failed to get valid JSON"):
                _generate_json("prompt")

    def test_retry_prompt_includes_format_instruction(self):
        calls = []

        def mock_generate(prompt, model=None):
            calls.append(prompt)
            if len(calls) < 3:
                return "bad"
            return '{"ok": true}'

        with patch("enki.local_model._generate", side_effect=mock_generate):
            _generate_json("original prompt")
            # Second and third calls should have format instruction appended
            assert "IMPORTANT" in calls[1]
            assert "valid JSON" in calls[1]


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_with_model(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "llama3.2:3b"}]
        }
        with patch("enki.local_model.httpx.get", return_value=mock_response):
            assert is_available("llama3.2:3b") is True

    def test_unavailable_no_server(self):
        import httpx
        with patch("enki.local_model.httpx.get", side_effect=httpx.ConnectError("refused")):
            assert is_available() is False

    def test_unavailable_wrong_model(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "llama3.2:3b"}]
        }
        with patch("enki.local_model.httpx.get", return_value=mock_response):
            assert is_available("nonexistent-model") is False


# ---------------------------------------------------------------------------
# Operation 1: Note Construction
# ---------------------------------------------------------------------------


class TestConstructNote:
    def test_returns_enriched_metadata(self):
        response = json.dumps({
            "keywords": "retry,backoff,exponential",
            "context_description": "Useful when implementing retry logic",
            "tags": "infrastructure,resilience",
            "summary": "Exponential backoff retry pattern",
        })
        with patch("enki.local_model._generate", return_value=response):
            result = construct_note("Retry with exponential backoff", "pattern")
            assert "keywords" in result
            assert "context_description" in result
            assert "tags" in result
            assert "summary" in result


# ---------------------------------------------------------------------------
# Operation 2: Link Classification
# ---------------------------------------------------------------------------


class TestClassifyLinks:
    def test_returns_valid_links(self):
        candidates = [
            {"note_id": "note-1", "source_db": "wisdom", "score": 0.8},
            {"note_id": "note-2", "source_db": "abzu", "score": 0.6},
        ]
        response = json.dumps([
            {"target_id": "note-1", "relationship": "relates_to"},
            {"target_id": "note-2", "relationship": "extends"},
        ])
        with patch("enki.local_model._generate", return_value=response), \
             patch("enki.local_model._get_candidate_content", return_value="some content"):
            result = classify_links("new content", "learning", candidates)
            assert len(result) == 2
            assert result[0]["relationship"] == "relates_to"
            assert result[1]["target_db"] == "abzu"

    def test_filters_invalid_relationships(self):
        candidates = [
            {"note_id": "note-1", "source_db": "wisdom", "score": 0.8},
        ]
        response = json.dumps([
            {"target_id": "note-1", "relationship": "bogus_rel"},
        ])
        with patch("enki.local_model._generate", return_value=response), \
             patch("enki.local_model._get_candidate_content", return_value="content"):
            result = classify_links("content", "learning", candidates)
            assert len(result) == 0

    def test_filters_unknown_target_ids(self):
        candidates = [
            {"note_id": "note-1", "source_db": "wisdom", "score": 0.8},
        ]
        response = json.dumps([
            {"target_id": "unknown-id", "relationship": "relates_to"},
        ])
        with patch("enki.local_model._generate", return_value=response), \
             patch("enki.local_model._get_candidate_content", return_value="content"):
            result = classify_links("content", "learning", candidates)
            assert len(result) == 0

    def test_handles_non_list_response(self):
        response = json.dumps({"error": "not a list"})
        with patch("enki.local_model._generate", return_value=response), \
             patch("enki.local_model._get_candidate_content", return_value="content"):
            result = classify_links("content", "learning", [])
            assert result == []


# ---------------------------------------------------------------------------
# Operation 3: Evolution Check
# ---------------------------------------------------------------------------


class TestCheckEvolution:
    def test_returns_proposed_changes(self):
        response = json.dumps({
            "should_update": True,
            "proposed_keywords": "retry,backoff,timeout",
            "proposed_context": "Updated with timeout context",
            "proposed_tags": "infrastructure",
        })
        with patch("enki.local_model._generate", return_value=response):
            result = check_evolution(
                "new note about timeouts", "pattern", "timeout",
                "retry logic", "pattern", "retry",
            )
            assert result is not None
            assert "proposed_keywords" in result

    def test_returns_none_when_no_update_needed(self):
        response = json.dumps({"should_update": False})
        with patch("enki.local_model._generate", return_value=response):
            result = check_evolution(
                "unrelated", "learning", None,
                "target", "decision", None,
            )
            assert result is None


# ---------------------------------------------------------------------------
# Operation 4: Code Extraction
# ---------------------------------------------------------------------------


class TestExtractCodeKnowledge:
    def test_returns_code_items(self):
        response = json.dumps([
            {
                "content": "Uses singleton pattern for DB connections",
                "keywords": "singleton,database,connection",
                "summary": "DB connection singleton",
            },
        ])
        with patch("enki.local_model._generate", return_value=response):
            result = extract_code_knowledge("class DB: ...", "src/db.py")
            assert len(result) == 1
            assert result[0]["category"] == "code_knowledge"
            assert result[0]["file_ref"] == "src/db.py"

    def test_handles_empty_extraction(self):
        with patch("enki.local_model._generate", return_value="[]"):
            result = extract_code_knowledge("x = 1", "test.py")
            assert result == []

    def test_handles_non_list_response(self):
        response = json.dumps({"error": "not a list"})
        with patch("enki.local_model._generate", return_value=response):
            result = extract_code_knowledge("code", "file.py")
            assert result == []


# ---------------------------------------------------------------------------
# Operation 5: JSONL Extraction
# ---------------------------------------------------------------------------


class TestExtractFromTranscript:
    def test_returns_note_candidates(self):
        response = json.dumps([
            {
                "content": "Decided to use JWT for auth",
                "category": "decision",
                "keywords": "auth,jwt",
                "summary": "JWT auth decision",
            },
            {
                "content": "SQLite WAL mode improves concurrency",
                "category": "learning",
                "keywords": "sqlite,wal",
                "summary": "WAL mode benefit",
            },
        ])
        with patch("enki.local_model._generate", return_value=response):
            result = extract_from_transcript("conversation text", "myproject")
            assert len(result) == 2
            assert result[0]["category"] == "decision"
            assert result[1]["category"] == "learning"

    def test_normalizes_invalid_category(self):
        response = json.dumps([
            {"content": "something", "category": "invalid_cat", "keywords": "x"},
        ])
        with patch("enki.local_model._generate", return_value=response):
            result = extract_from_transcript("text")
            assert result[0]["category"] == "learning"  # Falls back

    def test_handles_empty_extraction(self):
        with patch("enki.local_model._generate", return_value="[]"):
            result = extract_from_transcript("boring conversation")
            assert result == []

    def test_skips_items_without_content(self):
        response = json.dumps([
            {"category": "learning", "keywords": "x"},  # No content
            {"content": "valid item", "category": "fix"},
        ])
        with patch("enki.local_model._generate", return_value=response):
            result = extract_from_transcript("text")
            assert len(result) == 1
            assert result[0]["content"] == "valid item"
