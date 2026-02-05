"""Tests for Enki Transcript Extractor — deterministic digest builder."""

import json
import pytest
from pathlib import Path

from enki.transcript import (
    parse_transcript,
    extract_user_messages,
    extract_assistant_texts,
    extract_tool_uses,
    extract_tool_results,
    extract_modified_files,
    extract_decisions,
    extract_errors,
    extract_open_threads,
    build_work_summary,
    build_current_state,
    build_digest,
    MAX_DECISIONS,
    MAX_FILES,
    MAX_ERRORS,
    MAX_OPEN_THREADS,
)


# =============================================================================
# FIXTURES
# =============================================================================

def _write_transcript(tmp_path, messages: list[dict]) -> str:
    """Write messages as JSONL and return the path."""
    path = tmp_path / "transcript.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return str(path)


def _make_user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


def _make_assistant_msg(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _make_assistant_with_tool(text: str, tool_name: str, tool_input: dict) -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": text},
            {"type": "tool_use", "id": "tu_1", "name": tool_name, "input": tool_input},
        ],
    }


def _make_tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error},
        ],
    }


@pytest.fixture
def enki_dir(tmp_path):
    """Create a .enki/ directory with state files."""
    d = tmp_path / ".enki"
    d.mkdir()
    (d / "PHASE").write_text("implement")
    (d / "GOAL").write_text("Add user authentication")
    (d / "TIER").write_text("medium")
    return str(d)


# =============================================================================
# PARSER TESTS
# =============================================================================

class TestParseTranscript:
    def test_parses_valid_jsonl(self, tmp_path):
        path = _write_transcript(tmp_path, [
            _make_user_msg("hello"),
            _make_assistant_msg("hi there"),
        ])
        messages = parse_transcript(path)
        assert len(messages) == 2

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_user_msg("good")) + "\n")
            f.write("THIS IS NOT JSON\n")
            f.write(json.dumps(_make_assistant_msg("also good")) + "\n")
        messages = parse_transcript(str(path))
        assert len(messages) == 2

    def test_returns_empty_for_missing_file(self):
        messages = parse_transcript("/nonexistent/file.jsonl")
        assert messages == []

    def test_handles_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        messages = parse_transcript(str(path))
        assert messages == []

    def test_skips_blank_lines(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_make_user_msg("msg")) + "\n")
            f.write("\n")
            f.write("\n")
            f.write(json.dumps(_make_assistant_msg("reply")) + "\n")
        messages = parse_transcript(str(path))
        assert len(messages) == 2

    def test_cc_wrapper_format(self, tmp_path):
        """Claude Code wraps messages in {type, message: {role, content}}."""
        path = tmp_path / "transcript.jsonl"
        with open(path, "w") as f:
            # CC wrapper format
            f.write(json.dumps({
                "type": "user", "uuid": "u1",
                "message": {"role": "user", "content": "hello from CC"},
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant", "uuid": "a1",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "response from CC"},
                ]},
            }) + "\n")
            # Non-message lines (system, progress) should be skipped
            f.write(json.dumps({
                "type": "system", "content": "system info",
            }) + "\n")
        messages = parse_transcript(str(path))
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello from CC"
        assert messages[1]["role"] == "assistant"


# =============================================================================
# EXTRACTOR TESTS
# =============================================================================

class TestExtractUserMessages:
    def test_string_content(self):
        msgs = [_make_user_msg("hello"), _make_assistant_msg("hi")]
        assert extract_user_messages(msgs) == ["hello"]

    def test_list_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "from list"}]}]
        assert extract_user_messages(msgs) == ["from list"]

    def test_ignores_non_user(self):
        msgs = [_make_assistant_msg("only assistant")]
        assert extract_user_messages(msgs) == []


class TestExtractAssistantTexts:
    def test_string_content(self):
        msgs = [_make_assistant_msg("response")]
        assert extract_assistant_texts(msgs) == ["response"]

    def test_list_content_with_tool_use(self):
        msgs = [_make_assistant_with_tool("some text", "Edit", {"file_path": "a.py"})]
        texts = extract_assistant_texts(msgs)
        assert texts == ["some text"]


class TestExtractToolUses:
    def test_extracts_tool_calls(self):
        msgs = [_make_assistant_with_tool("text", "Edit", {"file_path": "a.py"})]
        tools = extract_tool_uses(msgs)
        assert len(tools) == 1
        assert tools[0]["name"] == "Edit"
        assert tools[0]["input"]["file_path"] == "a.py"

    def test_ignores_messages_without_tools(self):
        msgs = [_make_user_msg("hello"), _make_assistant_msg("hi")]
        assert extract_tool_uses(msgs) == []


class TestExtractToolResults:
    def test_extracts_error_results(self):
        msgs = [_make_tool_result("tu_1", "command not found", is_error=True)]
        results = extract_tool_results(msgs)
        assert len(results) == 1
        assert results[0]["is_error"] is True

    def test_extracts_success_results(self):
        msgs = [_make_tool_result("tu_1", "file written")]
        results = extract_tool_results(msgs)
        assert len(results) == 1
        assert results[0]["is_error"] is False


class TestExtractModifiedFiles:
    def test_edit_tool(self):
        tools = [{"name": "Edit", "input": {"file_path": "/home/partha/project/auth.py"}}]
        files = extract_modified_files(tools)
        assert len(files) == 1
        assert "auth.py" in files[0]

    def test_write_tool(self):
        tools = [{"name": "Write", "input": {"file_path": "/tmp/new_file.py"}}]
        files = extract_modified_files(tools)
        assert len(files) == 1

    def test_deduplicates(self):
        tools = [
            {"name": "Edit", "input": {"file_path": "/a.py"}},
            {"name": "Edit", "input": {"file_path": "/a.py"}},
            {"name": "Write", "input": {"file_path": "/a.py"}},
        ]
        files = extract_modified_files(tools)
        assert len(files) == 1

    def test_caps_at_max(self):
        tools = [
            {"name": "Edit", "input": {"file_path": f"/file_{i}.py"}}
            for i in range(30)
        ]
        files = extract_modified_files(tools)
        assert len(files) == MAX_FILES

    def test_ignores_non_mod_tools(self):
        tools = [
            {"name": "Read", "input": {"file_path": "/a.py"}},
            {"name": "Glob", "input": {"pattern": "*.py"}},
        ]
        files = extract_modified_files(tools)
        assert len(files) == 0

    def test_shortens_home_paths(self):
        tools = [{"name": "Edit", "input": {"file_path": "/home/partha/project/src/auth.py"}}]
        files = extract_modified_files(tools)
        assert files[0].startswith("~/")


class TestExtractDecisions:
    def test_finds_ill_patterns(self):
        texts = ["I'll add authentication to the login flow using JWT tokens."]
        decisions = extract_decisions(texts)
        assert len(decisions) >= 1
        assert "authentication" in decisions[0].lower()

    def test_finds_fix_is_patterns(self):
        texts = ["The fix is to add a null check before accessing the property."]
        decisions = extract_decisions(texts)
        assert len(decisions) >= 1

    def test_finds_instead_of_patterns(self):
        texts = ["Instead of using raw SQL, we should use the ORM for safety."]
        decisions = extract_decisions(texts)
        assert len(decisions) >= 1

    def test_deduplicates(self):
        texts = [
            "I'll add authentication to the app.",
            "I'll add authentication to the app.",
        ]
        decisions = extract_decisions(texts)
        assert len(decisions) == 1

    def test_caps_at_max(self):
        texts = [f"I'll do thing number {i} which is important for the project." for i in range(20)]
        decisions = extract_decisions(texts)
        assert len(decisions) <= MAX_DECISIONS

    def test_returns_empty_for_no_matches(self):
        texts = ["Hello world", "Just a simple response"]
        decisions = extract_decisions(texts)
        assert decisions == []


class TestExtractErrors:
    def test_from_tool_results(self):
        results = [{"is_error": True, "content": "Error: command not found bash"}]
        errors = extract_errors([], results)
        assert len(errors) >= 1

    def test_from_assistant_text(self):
        texts = ["I encountered an Error: ModuleNotFoundError for the package"]
        errors = extract_errors(texts, [])
        assert len(errors) >= 1

    def test_deduplicates(self):
        texts = [
            "Error: file not found at path",
            "Error: file not found at path",
        ]
        errors = extract_errors(texts, [])
        assert len(errors) == 1

    def test_caps_at_max(self):
        results = [
            {"is_error": True, "content": f"Error number {i}: something went wrong here"}
            for i in range(10)
        ]
        errors = extract_errors([], results)
        assert len(errors) <= MAX_ERRORS

    def test_returns_empty_for_no_errors(self):
        errors = extract_errors(["Everything is fine"], [])
        assert errors == []


class TestExtractOpenThreads:
    def test_finds_todo(self):
        # Need enough messages so the last 30% window includes the target
        texts = ["first msg", "second msg", "third msg", "TODO: implement the logout endpoint"]
        threads = extract_open_threads(texts, [])
        assert len(threads) >= 1
        assert "logout" in threads[0].lower()

    def test_finds_still_need_to(self):
        texts = ["first", "second", "third", "We still need to add error handling for the edge case."]
        threads = extract_open_threads(texts, [])
        assert len(threads) >= 1

    def test_finds_next_step(self):
        texts = ["first", "second", "third", "The next step is to write integration tests for the API."]
        threads = extract_open_threads(texts, [])
        assert len(threads) >= 1

    def test_flags_unanswered_question(self):
        threads = extract_open_threads([], ["Can you fix the bug in auth.py?"])
        assert any("[Unanswered]" in t for t in threads)

    def test_caps_at_max(self):
        texts = [f"TODO: task number {i} needs doing for the project" for i in range(20)]
        threads = extract_open_threads(texts, [])
        assert len(threads) <= MAX_OPEN_THREADS

    def test_returns_empty_for_no_threads(self):
        threads = extract_open_threads(["Everything is done."], ["thanks"])
        assert threads == []


# =============================================================================
# SUMMARY BUILDERS
# =============================================================================

class TestBuildWorkSummary:
    def test_includes_first_user_message(self):
        summary = build_work_summary(["Add auth to the app"], [], [])
        assert "Add auth" in summary

    def test_includes_file_count(self):
        summary = build_work_summary(["task"], ["a.py", "b.py"], [])
        assert "2 file" in summary

    def test_includes_tool_distribution(self):
        tools = [
            {"name": "Edit"},
            {"name": "Edit"},
            {"name": "Read"},
        ]
        summary = build_work_summary(["task"], [], tools)
        assert "Edit" in summary

    def test_handles_no_user_messages(self):
        summary = build_work_summary([], [], [])
        assert "No user messages" in summary


class TestBuildCurrentState:
    def test_uses_last_meaningful_message(self):
        texts = [
            "This is a long first message that explains what we did at the start of the session.",
            "ok",
            "Here is the final state of what we accomplished in the session, tests pass and everything works.",
        ]
        state = build_current_state(texts, [])
        assert "final state" in state

    def test_skips_short_messages(self):
        texts = ["Long meaningful message about the current state of the implementation.", "ok", "done"]
        state = build_current_state(texts, [])
        assert "meaningful" in state

    def test_handles_empty(self):
        state = build_current_state([], [])
        assert "without substantive" in state

    def test_truncates_long_state(self):
        texts = ["x" * 500]
        state = build_current_state(texts, [])
        assert len(state) <= 260  # MAX_STATE_CHARS + "..."


# =============================================================================
# FULL DIGEST TESTS
# =============================================================================

class TestBuildDigest:
    def test_empty_transcript(self, tmp_path):
        path = _write_transcript(tmp_path, [])
        # Empty file means no messages
        empty_path = tmp_path / "empty.jsonl"
        empty_path.write_text("")
        digest = build_digest(str(empty_path))
        assert "No transcript data" in digest

    def test_missing_file(self):
        digest = build_digest("/nonexistent/file.jsonl")
        assert "No transcript data" in digest

    def test_all_seven_sections_present(self, tmp_path, enki_dir):
        path = _write_transcript(tmp_path, [
            _make_user_msg("Add authentication"),
            _make_assistant_with_tool(
                "I'll add JWT authentication to the login flow.",
                "Edit", {"file_path": "/home/partha/project/auth.py"},
            ),
            _make_tool_result("tu_1", "file edited"),
        ])
        digest = build_digest(path, enki_dir)

        assert "## Work Summary" in digest
        assert "## Decisions Made" in digest
        assert "## Files Modified" in digest
        assert "## Problems Encountered" in digest
        assert "## Open Threads" in digest
        assert "## Current State" in digest
        assert "Digest:" in digest  # stats line

    def test_includes_session_state(self, tmp_path, enki_dir):
        path = _write_transcript(tmp_path, [_make_user_msg("hello")])
        digest = build_digest(path, enki_dir)
        assert "implement" in digest  # phase
        assert "medium" in digest  # tier
        assert "authentication" in digest  # goal

    def test_none_markers_for_empty_sections(self, tmp_path):
        """Empty sections show (none), not omitted."""
        path = _write_transcript(tmp_path, [
            _make_user_msg("just chatting"),
            _make_assistant_msg("Sure, let's talk."),
        ])
        digest = build_digest(path)
        assert "(none)" in digest

    def test_deterministic(self, tmp_path, enki_dir):
        """Same input → same output. Always."""
        messages = [
            _make_user_msg("Fix the bug in auth.py"),
            _make_assistant_with_tool(
                "I'll fix the null pointer error in the authentication module.",
                "Edit", {"file_path": "/home/partha/project/auth.py"},
            ),
            _make_tool_result("tu_1", "file edited"),
            _make_assistant_msg("The fix is to check for null before accessing user.email property."),
        ]
        path = _write_transcript(tmp_path, messages)

        digest1 = build_digest(path, enki_dir)
        digest2 = build_digest(path, enki_dir)
        assert digest1 == digest2

    def test_stats_line(self, tmp_path):
        path = _write_transcript(tmp_path, [
            _make_user_msg("task"),
            _make_assistant_with_tool("doing", "Edit", {"file_path": "/a.py"}),
            _make_tool_result("tu_1", "done"),
            _make_assistant_msg("Finished."),
        ])
        digest = build_digest(path)
        # Should show message count, tool count, file count
        assert "4 messages" in digest
        assert "1 tool calls" in digest
        assert "1 files modified" in digest

    def test_without_enki_dir(self, tmp_path):
        """Works without .enki/ directory."""
        path = _write_transcript(tmp_path, [
            _make_user_msg("hello"),
            _make_assistant_msg("This is a long enough response to be considered meaningful by the state builder."),
        ])
        digest = build_digest(path)
        # Should have default state values
        assert "intake" in digest  # default phase
        assert "## Work Summary" in digest

    def test_realistic_session(self, tmp_path, enki_dir):
        """A realistic multi-turn session produces a complete digest."""
        messages = [
            _make_user_msg("Add JWT authentication to the login endpoint"),
            _make_assistant_with_tool(
                "I'll add JWT authentication. Let me first read the existing auth module.",
                "Read", {"file_path": "/project/auth.py"},
            ),
            _make_tool_result("tu_1", "class AuthHandler: ..."),
            _make_assistant_with_tool(
                "The fix is to add a JWT token generation function after login validation.",
                "Edit", {"file_path": "/project/auth.py"},
            ),
            _make_tool_result("tu_2", "file edited"),
            _make_assistant_with_tool(
                "I'll also add the JWT dependency and write tests.",
                "Write", {"file_path": "/project/test_auth.py"},
            ),
            _make_tool_result("tu_3", "file created"),
            _make_assistant_msg(
                "JWT authentication is now implemented. "
                "TODO: add token refresh endpoint. "
                "The next step is to integrate with the middleware."
            ),
        ]
        path = _write_transcript(tmp_path, messages)
        digest = build_digest(path, enki_dir)

        # Should capture key elements
        assert "auth" in digest.lower()
        assert "## Decisions Made" in digest
        assert "## Files Modified" in digest
        # Should find open threads
        assert "## Open Threads" in digest
        # Files modified should include auth.py and test_auth.py
        assert "auth.py" in digest
        assert "test_auth.py" in digest
