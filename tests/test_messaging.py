"""Tests for Spec 3: Agent Messaging — messages, file claims, handlers, AM-1 through AM-4."""

from pathlib import Path

import pytest

from enki.db import init_db, get_db, set_db_path, reset_connection
from enki.messaging import (
    register_agent,
    send_message,
    get_messages,
    claim_file,
    release_file,
    get_file_owner,
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    reset_connection()
    set_db_path(db_path)
    init_db(db_path)
    yield tmp_path
    reset_connection()
    set_db_path(None)


SESSION = "test-session-001"


def _setup_agents(*names):
    """Register agents as prerequisite for FK constraints."""
    for name in names:
        register_agent(name, name, SESSION)


def _setup_agents_session(*names, session_id=SESSION):
    """Register agents with a specific session."""
    for name in names:
        register_agent(name, name, session_id)


# ========== register_agent ==========


class TestRegisterAgent:
    def test_register_new_agent(self):
        register_agent("worker-1", "worker", SESSION)
        db = get_db()
        row = db.execute("SELECT * FROM agents WHERE id = ?", ("worker-1",)).fetchone()
        assert row is not None
        assert row["role"] == "worker"
        assert row["status"] == "active"

    def test_register_updates_existing(self):
        register_agent("worker-1", "worker", SESSION)
        register_agent("worker-1", "lead", SESSION)
        db = get_db()
        row = db.execute("SELECT * FROM agents WHERE id = ?", ("worker-1",)).fetchone()
        assert row["role"] == "lead"

    def test_register_multiple_agents(self):
        register_agent("pm", "pm", SESSION)
        register_agent("em", "em", SESSION)
        register_agent("worker-1", "worker", SESSION)
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        assert count == 3


# ========== send_message + get_messages ==========


class TestSendMessage:
    def test_send_basic(self):
        _setup_agents("pm", "em")
        msg = send_message("pm", "em", "Task ready", "Please start task-1", SESSION)
        assert msg.from_agent == "pm"
        assert msg.to_agent == "em"
        assert msg.subject == "Task ready"
        assert msg.importance == "normal"
        assert msg.id is not None

    def test_send_with_importance(self):
        _setup_agents("pm", "em")
        msg = send_message("pm", "em", "Urgent", "Critical bug", SESSION, importance="critical")
        assert msg.importance == "critical"

    def test_send_with_thread(self):
        _setup_agents("pm", "em")
        msg1 = send_message("pm", "em", "Topic", "First", SESSION)
        msg2 = send_message("em", "pm", "Re: Topic", "Reply", SESSION, thread_id=msg1.thread_id)
        assert msg2.thread_id == msg1.thread_id

    def test_thread_defaults_to_msg_id(self):
        _setup_agents("pm", "em")
        msg = send_message("pm", "em", "New thread", "Body", SESSION)
        assert msg.thread_id == msg.id


class TestGetMessages:
    def test_get_messages_empty(self):
        msgs = get_messages("em", SESSION)
        assert msgs == []

    def test_get_messages_for_recipient(self):
        _setup_agents("pm", "em")
        send_message("pm", "em", "S1", "B1", SESSION)
        send_message("pm", "em", "S2", "B2", SESSION)
        send_message("em", "pm", "S3", "B3", SESSION)  # To pm, not em
        msgs = get_messages("em", SESSION)
        assert len(msgs) == 2

    def test_marks_as_read(self):
        _setup_agents("pm", "em")
        send_message("pm", "em", "Test", "Body", SESSION)
        msgs1 = get_messages("em", SESSION, unread_only=True)
        assert len(msgs1) == 1
        # Second call for unread_only should return 0
        msgs2 = get_messages("em", SESSION, unread_only=True)
        assert len(msgs2) == 0

    def test_limit(self):
        _setup_agents("pm", "em")
        for i in range(5):
            send_message("pm", "em", f"S{i}", f"B{i}", SESSION)
        msgs = get_messages("em", SESSION, limit=3)
        assert len(msgs) == 3

    def test_cross_session_isolation(self):
        _setup_agents_session("pm", "em", session_id="session-A")
        _setup_agents_session("pm", "em", session_id="session-B")
        send_message("pm", "em", "S1", "B1", "session-A")
        send_message("pm", "em", "S2", "B2", "session-B")
        msgs = get_messages("em", "session-A")
        assert len(msgs) == 1


# ========== File Claims ==========


class TestFileClaims:
    def test_claim_file_success(self):
        _setup_agents("worker-1")
        result = claim_file("worker-1", "src/main.py", SESSION)
        assert result is True

    def test_claim_same_file_same_agent(self):
        _setup_agents("worker-1")
        claim_file("worker-1", "src/main.py", SESSION)
        result = claim_file("worker-1", "src/main.py", SESSION)
        assert result is True  # Idempotent

    def test_claim_conflict(self):
        _setup_agents("worker-1", "worker-2")
        claim_file("worker-1", "src/main.py", SESSION)
        result = claim_file("worker-2", "src/main.py", SESSION)
        assert result is False  # AM-4: Returns False, not raises

    def test_get_file_owner(self):
        _setup_agents("worker-1")
        claim_file("worker-1", "src/main.py", SESSION)
        owner = get_file_owner("src/main.py", SESSION)
        assert owner == "worker-1"

    def test_get_file_owner_unclaimed(self):
        owner = get_file_owner("src/main.py", SESSION)
        assert owner is None

    def test_release_file(self):
        _setup_agents("worker-1")
        claim_file("worker-1", "src/main.py", SESSION)
        release_file("worker-1", "src/main.py", SESSION)
        owner = get_file_owner("src/main.py", SESSION)
        assert owner is None

    def test_release_then_reclaim_by_other(self):
        _setup_agents("worker-1", "worker-2")
        claim_file("worker-1", "src/main.py", SESSION)
        release_file("worker-1", "src/main.py", SESSION)
        result = claim_file("worker-2", "src/main.py", SESSION)
        assert result is True

    def test_cross_session_claims_independent(self):
        _setup_agents_session("worker-1", session_id="session-A")
        _setup_agents_session("worker-2", session_id="session-B")
        claim_file("worker-1", "src/main.py", "session-A")
        result = claim_file("worker-2", "src/main.py", "session-B")
        assert result is True  # Different sessions


# ========== AM-3: Critical/high auto-create beads ==========


class TestAutoBeadCreation:
    def test_critical_message_creates_bead(self):
        _setup_agents("pm", "em")
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        send_message("pm", "em", "CRITICAL", "Scope change required", SESSION, importance="critical")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after == before + 1

    def test_high_message_creates_bead(self):
        _setup_agents("pm", "em")
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        send_message("pm", "em", "Warning", "Approaching limit", SESSION, importance="high")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after == before + 1

    def test_normal_message_no_bead(self):
        _setup_agents("pm", "em")
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        send_message("pm", "em", "FYI", "Regular update", SESSION, importance="normal")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after == before

    def test_low_message_no_bead(self):
        _setup_agents("pm", "em")
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        send_message("pm", "em", "Log", "Routine info", SESSION, importance="low")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after == before


# ========== MCP Handler Tests ==========


class TestMessageHandlers:
    def test_send_message_handler(self, setup_db):
        from enki.mcp_server import _handle_send_message
        enki_dir = setup_db / ".enki"
        enki_dir.mkdir(exist_ok=True)
        (enki_dir / "SESSION_ID").write_text("handler-session")
        result = _handle_send_message(
            {
                "from_agent": "pm",
                "to_agent": "em",
                "subject": "Test",
                "body": "Handler test",
                "project": str(setup_db),
            },
            remote=False,
        )
        assert "Message sent" in result[0].text
        assert "pm" in result[0].text
        assert "em" in result[0].text

    def test_get_messages_handler_empty(self, setup_db):
        from enki.mcp_server import _handle_get_messages
        enki_dir = setup_db / ".enki"
        enki_dir.mkdir(exist_ok=True)
        (enki_dir / "SESSION_ID").write_text("handler-session")
        result = _handle_get_messages(
            {"agent_id": "em", "project": str(setup_db)},
            remote=False,
        )
        assert "No messages" in result[0].text

    def test_claim_file_handler(self, setup_db):
        from enki.mcp_server import _handle_claim_file
        enki_dir = setup_db / ".enki"
        enki_dir.mkdir(exist_ok=True)
        (enki_dir / "SESSION_ID").write_text("handler-session")
        # Register agent first (FK constraint)
        register_agent("worker-1", "worker", "handler-session")
        result = _handle_claim_file(
            {"agent_id": "worker-1", "file_path": "src/app.py", "project": str(setup_db)},
            remote=False,
        )
        assert "File claimed" in result[0].text

    def test_claim_file_conflict_handler(self, setup_db):
        from enki.mcp_server import _handle_claim_file
        enki_dir = setup_db / ".enki"
        enki_dir.mkdir(exist_ok=True)
        (enki_dir / "SESSION_ID").write_text("handler-session")
        register_agent("worker-1", "worker", "handler-session")
        register_agent("worker-2", "worker", "handler-session")
        _handle_claim_file(
            {"agent_id": "worker-1", "file_path": "src/app.py", "project": str(setup_db)},
            remote=False,
        )
        result = _handle_claim_file(
            {"agent_id": "worker-2", "file_path": "src/app.py", "project": str(setup_db)},
            remote=False,
        )
        assert "CLAIM DENIED" in result[0].text

    def test_release_file_handler(self, setup_db):
        from enki.mcp_server import _handle_claim_file, _handle_release_file
        enki_dir = setup_db / ".enki"
        enki_dir.mkdir(exist_ok=True)
        (enki_dir / "SESSION_ID").write_text("handler-session")
        register_agent("worker-1", "worker", "handler-session")
        _handle_claim_file(
            {"agent_id": "worker-1", "file_path": "src/app.py", "project": str(setup_db)},
            remote=False,
        )
        result = _handle_release_file(
            {"agent_id": "worker-1", "file_path": "src/app.py", "project": str(setup_db)},
            remote=False,
        )
        assert "File released" in result[0].text


# ========== AM-1 through AM-4 Verification ==========


class TestGovernanceConstraints:
    def test_am1_messages_append_only(self):
        """AM-1: No UPDATE or DELETE on messages table in messaging.py."""
        source = Path(__file__).parent.parent / "src" / "enki" / "messaging.py"
        code = source.read_text()
        assert "DELETE FROM messages" not in code
        for line in code.split("\n"):
            if "UPDATE messages" in line and "read_at" not in line:
                pytest.fail(f"AM-1 violated: UPDATE on messages table (not read_at): {line}")

    def test_am2_no_bypass_flag_in_claim_logic(self):
        """AM-2: File claim check in pre-tool-use has no bypass/skip mechanism."""
        hook = Path(__file__).parent.parent / "scripts" / "hooks" / "enki-pre-tool-use.sh"
        code = hook.read_text()
        assert "File Claim" in code or "file claim" in code.lower()
        # Extract the claim logic (the Python block and the decision block)
        # Check that there's no --skip-claims, SKIP_CLAIMS, or similar flags
        assert "--skip-claims" not in code
        assert "SKIP_CLAIMS" not in code
        assert "--no-claims" not in code
        # The claim check uses no conditional env-var override
        claim_section = code.split("File Claim")[1].split("Layer 2")[0]
        assert "ENKI_SKIP" not in claim_section
        assert "DISABLE_CLAIM" not in claim_section

    def test_am3_critical_high_create_beads(self):
        """AM-3: create_bead called in send_message for critical/high."""
        source = Path(__file__).parent.parent / "src" / "enki" / "messaging.py"
        code = source.read_text()
        send_section = code.split("def send_message")[1].split("\ndef ")[0]
        assert "create_bead" in send_section
        assert '"critical"' in send_section or "'critical'" in send_section
        assert '"high"' in send_section or "'high'" in send_section

    def test_am4_claim_returns_false_not_raises(self):
        """AM-4: claim_file returns False on conflict, does not raise."""
        source = Path(__file__).parent.parent / "src" / "enki" / "messaging.py"
        code = source.read_text()
        claim_section = code.split("def claim_file")[1].split("\ndef ")[0]
        assert "return False" in claim_section
        assert "raise" not in claim_section


# ========== Dispatch Map Update ==========


class TestDispatchMapUpdate:
    def test_35_handlers_registered(self):
        """Dispatch map now has 35 handlers (28 original + 4 messaging + 3 PM-EM)."""
        from enki.mcp_server import TOOL_HANDLERS
        assert len(TOOL_HANDLERS) == 35

    def test_messaging_tools_in_dispatch(self):
        from enki.mcp_server import TOOL_HANDLERS
        messaging_tools = {
            "enki_send_message", "enki_get_messages",
            "enki_claim_file", "enki_release_file",
        }
        assert messaging_tools.issubset(set(TOOL_HANDLERS.keys()))
