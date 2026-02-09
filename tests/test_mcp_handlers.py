"""Tests for MCP server dispatch map and key handlers (P2-21).

Tests handlers directly (no MCP wire protocol needed) since P2-02
extracted them as plain functions: handler(arguments, remote) -> list[TextContent].
"""

import pytest
from pathlib import Path

from enki.db import init_db, close_db, set_db_path


@pytest.fixture
def temp_project(tmp_path):
    """Set up a temporary project with initialized DB."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    # Create minimal session state files
    enki_dir = tmp_path / ".enki"
    (enki_dir / "PHASE").write_text("intake")
    (enki_dir / "TIER").write_text("trivial")

    yield tmp_path
    close_db()
    set_db_path(None)


# --- Dispatch Map Structure ---


class TestDispatchMap:
    def test_all_35_handlers_registered(self):
        from enki.mcp_server import TOOL_HANDLERS
        assert len(TOOL_HANDLERS) == 35

    def test_all_handlers_are_callable(self):
        from enki.mcp_server import TOOL_HANDLERS
        for name, handler in TOOL_HANDLERS.items():
            assert callable(handler), f"{name} handler is not callable"

    def test_known_tool_names(self):
        from enki.mcp_server import TOOL_HANDLERS
        expected = {
            "enki_remember", "enki_recall", "enki_forget", "enki_star",
            "enki_status", "enki_goal", "enki_phase", "enki_debate",
            "enki_plan", "enki_approve", "enki_decompose", "enki_orchestrate",
            "enki_task", "enki_bug", "enki_log", "enki_maintain",
            "enki_submit_for_validation", "enki_spawn_validators",
            "enki_record_validation", "enki_retry_rejected_task",
            "enki_validation_status", "enki_worktree_create",
            "enki_worktree_list", "enki_worktree_merge",
            "enki_worktree_remove", "enki_reflect", "enki_feedback_loop",
            "enki_simplify",
            "enki_send_message", "enki_get_messages",
            "enki_claim_file", "enki_release_file",
            "enki_triage", "enki_handover", "enki_escalate",
        }
        assert set(TOOL_HANDLERS.keys()) == expected


# --- Handler Tests (local mode, remote=False) ---


class TestHandleRemember:
    def test_creates_bead(self, temp_project):
        from enki.mcp_server import _handle_remember
        result = _handle_remember(
            {"content": "Test knowledge", "type": "learning", "project": str(temp_project)},
            remote=False,
        )
        assert len(result) == 1
        assert "Remembered [learning]" in result[0].text

    def test_remember_shows_echo_section(self, temp_project):
        """C7: Remember response always has Similar Knowledge header."""
        from enki.mcp_server import _handle_remember
        result = _handle_remember(
            {"content": "some unique knowledge content here", "type": "decision", "project": str(temp_project)},
            remote=False,
        )
        assert "--- Similar Knowledge ---" in result[0].text

    def test_with_optional_fields(self, temp_project):
        from enki.mcp_server import _handle_remember
        result = _handle_remember(
            {
                "content": "Tagged knowledge",
                "type": "pattern",
                "summary": "A pattern",
                "tags": ["test", "demo"],
                "context": "testing",
                "starred": True,
                "project": str(temp_project),
            },
            remote=False,
        )
        assert "Remembered [pattern]" in result[0].text


class TestHandleRecall:
    def test_no_results(self, temp_project):
        from enki.mcp_server import _handle_recall
        result = _handle_recall(
            {"query": "nonexistent topic", "project": str(temp_project)},
            remote=False,
        )
        assert "No relevant knowledge found" in result[0].text

    def test_finds_stored_bead(self, temp_project):
        from enki.mcp_server import _handle_remember, _handle_recall
        _handle_remember(
            {"content": "Python asyncio patterns for concurrency", "type": "learning", "project": str(temp_project)},
            remote=False,
        )
        result = _handle_recall(
            {"query": "asyncio", "project": str(temp_project)},
            remote=False,
        )
        assert "Found" in result[0].text or "asyncio" in result[0].text.lower()


class TestHandleForget:
    def test_supersede_nonexistent(self, temp_project):
        from enki.mcp_server import _handle_forget
        result = _handle_forget(
            {"old_id": "nonexistent", "new_id": "also-nonexistent"},
            remote=False,
        )
        assert "not found" in result[0].text.lower()


class TestHandleStar:
    def test_star_nonexistent(self, temp_project):
        from enki.mcp_server import _handle_star
        result = _handle_star(
            {"bead_id": "nonexistent", "starred": True},
            remote=False,
        )
        assert "not found" in result[0].text.lower()


class TestHandleGoal:
    def test_set_goal(self, temp_project):
        from enki.mcp_server import _handle_goal
        result = _handle_goal(
            {"goal": "Build the feature", "project": str(temp_project)},
            remote=False,
        )
        assert "Goal set: Build the feature" in result[0].text
        assert "Gate 1" in result[0].text

    def test_goal_persists(self, temp_project):
        from enki.mcp_server import _handle_goal
        from enki.session import get_goal
        _handle_goal(
            {"goal": "Persist this", "project": str(temp_project)},
            remote=False,
        )
        assert get_goal(temp_project) == "Persist this"


class TestHandlePhase:
    def test_get_phase(self, temp_project):
        from enki.mcp_server import _handle_phase
        result = _handle_phase(
            {"project": str(temp_project)},
            remote=False,
        )
        assert "intake" in result[0].text

    def test_set_phase(self, temp_project):
        from enki.mcp_server import _handle_phase
        result = _handle_phase(
            {"phase": "implement", "project": str(temp_project)},
            remote=False,
        )
        assert "implement" in result[0].text


class TestHandleLog:
    def test_log_entry(self, temp_project):
        from enki.mcp_server import _handle_log
        result = _handle_log(
            {"message": "Test log entry", "entry_type": "NOTE", "project": str(temp_project)},
            remote=False,
        )
        assert len(result) == 1
        text = result[0].text.lower()
        assert "logged" in text or "log" in text or "test log entry" in text.lower()


class TestHandleMaintain:
    def test_runs_maintenance(self, temp_project):
        from enki.mcp_server import _handle_maintain
        result = _handle_maintain(
            {"project": str(temp_project)},
            remote=False,
        )
        assert len(result) == 1
        # Should mention maintenance or weights
        assert "maint" in result[0].text.lower() or "weight" in result[0].text.lower() or "decay" in result[0].text.lower()


class TestHandleBug:
    def test_file_bug_requires_orchestration(self, temp_project):
        """Filing a bug requires an active orchestration."""
        from enki.mcp_server import _handle_bug
        result = _handle_bug(
            {
                "action": "file",
                "title": "Test bug",
                "description": "Something broke",
                "severity": "medium",
                "project": str(temp_project),
            },
            remote=False,
        )
        assert "error" in result[0].text.lower()

    def test_list_bugs_empty(self, temp_project):
        from enki.mcp_server import _handle_bug
        result = _handle_bug(
            {"action": "list", "project": str(temp_project)},
            remote=False,
        )
        assert len(result) == 1


class TestHandleStatus:
    def test_returns_status(self, temp_project):
        from enki.mcp_server import _handle_status
        result = _handle_status(
            {"project": str(temp_project)},
            remote=False,
        )
        assert len(result) == 1
        # Status output should mention beads or scope or session info
        text = result[0].text.lower()
        assert any(kw in text for kw in ["bead", "scope", "session", "phase", "status"])
