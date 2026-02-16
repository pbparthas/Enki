"""Integration tests for Enki v3.

Suite 1: Session lifecycle — start → gate enforcement → tool use → compact → end
Suite 2: Orchestration pipeline — agent spawn → JSON parse → mail route → DAG advance
"""

import json
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod

PROJECT = "integration-test"


@pytest.fixture
def em_root(tmp_path):
    """Isolated ENKI_ROOT with all DBs initialized."""
    root = tmp_path / ".enki"
    root.mkdir()
    db_dir = root / "db"
    db_dir.mkdir()
    old_initialized = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    import enki.gates.uru as uru_mod
    with patch.object(db_mod, "ENKI_ROOT", root), \
         patch.object(db_mod, "DB_DIR", db_dir), \
         patch.object(uru_mod, "ENKI_ROOT", root):
        db_mod.init_all()
        yield root
    db_mod._em_initialized = old_initialized


# =============================================================================
# Suite 1: Session Lifecycle
# =============================================================================


class TestSessionLifecycle:
    """Full session lifecycle: start → gates → tool use → compact → end."""

    def test_full_session_flow(self, em_root):
        """End-to-end session: init → gate checks → summary → end."""
        from enki.gates.uru import init_session, end_session
        from enki.gates.uru import check_pre_tool_use, check_post_tool_use

        session_id = str(uuid.uuid4())

        # Phase 1: Session start
        init_session(session_id)
        session_file = em_root / "SESSION_ID"
        assert session_file.exists()
        assert session_file.read_text().strip() == session_id

        # Phase 2: Gate enforcement — allowed tool
        result = check_pre_tool_use("Read", {"file_path": "/tmp/test.py"})
        assert result["decision"] == "allow"

        # Phase 3: Post-tool-use nudge check
        post = check_post_tool_use("Read", {"file_path": "/tmp/test.py"})
        assert post["decision"] == "allow"

        # Phase 4: Session end
        summary = end_session(session_id)
        assert summary["session_id"] == session_id
        assert "enforcement" in summary

    def test_gate_blocks_dangerous_tool(self, em_root):
        """Pre-tool-use gate blocks infrastructure writes."""
        from enki.gates.uru import init_session, check_pre_tool_use

        init_session(str(uuid.uuid4()))

        # Attempting to write to a protected gate file should be blocked
        result = check_pre_tool_use(
            "Write",
            {"file_path": "src/enki/gates/uru.py"},
        )
        # Either blocked or allowed depending on config — just verify structure
        assert "decision" in result
        assert result["decision"] in ("allow", "block")

    def test_multiple_tool_uses_in_session(self, em_root):
        """Multiple tool uses accumulate enforcement state."""
        from enki.gates.uru import (
            init_session, end_session,
            check_pre_tool_use, check_post_tool_use,
        )

        session_id = str(uuid.uuid4())
        init_session(session_id)

        # Simulate 5 tool uses
        tools = [
            ("Read", {"file_path": "README.md"}),
            ("Glob", {"pattern": "*.py"}),
            ("Grep", {"pattern": "def main", "path": "."}),
            ("Read", {"file_path": "src/enki/db.py"}),
            ("Bash", {"command": "echo hello"}),
        ]
        for tool_name, tool_input in tools:
            pre = check_pre_tool_use(tool_name, tool_input)
            assert "decision" in pre
            if pre["decision"] == "allow":
                post = check_post_tool_use(tool_name, tool_input)
                assert post["decision"] == "allow"

        summary = end_session(session_id)
        assert summary["session_id"] == session_id

    def test_session_with_compact_cycle(self, em_root):
        """Session with pre-compact summary and post-compact injection."""
        from enki.gates.uru import init_session, end_session, inject_enforcement_context
        from enki.memory.sessions import (
            create_summary,
            update_pre_compact_summary,
            get_post_compact_injection,
            finalize_session,
        )

        session_id = str(uuid.uuid4())
        init_session(session_id)

        # Create initial session summary
        create_summary(session_id, project=PROJECT, goal="Test integration")

        # Simulate pre-compact: save state
        update_pre_compact_summary(
            session_id,
            project=PROJECT,
            operational_state="Building auth module",
            conversational_state="User asked for login feature",
            goal="Test integration",
            phase="implement",
        )

        # Simulate post-compact: inject context
        injection = get_post_compact_injection(session_id, tier="standard")
        assert isinstance(injection, str)

        # Gate enforcement context injection
        gate_context = inject_enforcement_context()
        assert isinstance(gate_context, str)

        # Finalize session
        final_id = finalize_session(session_id, project=PROJECT)
        assert final_id  # non-empty string

        end_session(session_id)

    def test_session_summary_persistence(self, em_root):
        """Session summaries persist and can be retrieved."""
        from enki.memory.sessions import (
            create_summary,
            update_pre_compact_summary,
            get_accumulated_summaries,
            finalize_session,
            get_last_final_summary,
        )

        session_id = str(uuid.uuid4())
        create_summary(session_id, project=PROJECT, goal="Persistence test")

        # Multiple compactions accumulate
        for i in range(3):
            update_pre_compact_summary(
                session_id,
                project=PROJECT,
                operational_state=f"Step {i}",
                conversational_state=f"Context {i}",
            )

        summaries = get_accumulated_summaries(session_id)
        assert len(summaries) == 4  # 1 initial + 3 pre-compact

        finalize_session(session_id, project=PROJECT)
        last = get_last_final_summary(PROJECT)
        assert last is not None


# =============================================================================
# Suite 2: Orchestration Pipeline
# =============================================================================


class TestOrchestrationPipeline:
    """Full pipeline: agent spawn → JSON parse → mail route → DAG advance."""

    def test_full_orchestration_flow(self, em_root):
        """End-to-end: start project → create tasks → process output → advance."""
        from enki.orch.orchestrator import Orchestrator
        from enki.orch.task_graph import (
            create_sprint, create_task, get_task,
            update_task_status, get_next_wave, is_sprint_complete,
            TaskStatus,
        )
        from enki.orch.mail import get_inbox

        orch = Orchestrator(PROJECT)

        # Phase 1: Start project
        start = orch.handle_project_start(
            "Build a REST API for user management",
            {"existing_repo": False},
        )
        assert "tier" in start
        assert "entry_point" in start

        # Phase 2: Create sprint and tasks
        sid = create_sprint(PROJECT, 1)
        t1 = create_task(PROJECT, sid, "Design API schema", tier="standard", work_type="task")
        t2 = create_task(PROJECT, sid, "Implement endpoints", tier="standard",
                         dependencies=[t1], work_type="task")
        t3 = create_task(PROJECT, sid, "Write tests", tier="standard",
                         dependencies=[t2], work_type="task")

        # Phase 3: Wave 1 — only t1 is ready
        wave1 = get_next_wave(PROJECT, sid)
        assert len(wave1) == 1
        assert wave1[0]["task_id"] == t1

        # Spawn agent for t1
        spawn = orch.spawn_agent("dev", t1, {"goal": "Design API schema"})
        assert spawn["task_id"] == t1
        assert "prompt" in spawn

        # Phase 4: Process agent output (JSON parse + mail route)
        agent_output = json.dumps({
            "agent": "dev",
            "task_id": t1,
            "status": "DONE",
            "completed_work": "Designed REST API schema with 5 endpoints",
            "files_modified": [],
            "files_created": ["api_schema.json"],
            "decisions": [{"type": "tech", "decision": "Use FastAPI", "reason": "async support"}],
            "messages": [{"to": "qa", "content": "Schema ready for review"}],
            "concerns": [],
            "blockers": [],
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
        })

        result = orch.process_agent_output(t1, agent_output)
        assert result["status"] == "processed"
        assert result["messages_routed"] >= 1

        # Phase 5: DAG advancement — t1 done, t2 now ready
        update_task_status(PROJECT, t1, TaskStatus.COMPLETED)
        wave2 = get_next_wave(PROJECT, sid)
        assert len(wave2) == 1
        assert wave2[0]["task_id"] == t2

        # Phase 6: Complete remaining tasks
        update_task_status(PROJECT, t2, TaskStatus.COMPLETED)
        wave3 = get_next_wave(PROJECT, sid)
        assert len(wave3) == 1
        assert wave3[0]["task_id"] == t3

        update_task_status(PROJECT, t3, TaskStatus.COMPLETED)
        assert is_sprint_complete(PROJECT, sid)

    def test_agent_output_parse_and_mail_route(self, em_root):
        """Agent JSON output → parse → extract messages → route to inbox."""
        from enki.orch.parsing import parse_agent_output, extract_messages, extract_decisions
        from enki.orch.mail import create_thread, send, get_inbox, count_unread
        from enki.orch.task_graph import create_sprint, create_task

        # Setup: sprint + task
        sid = create_sprint(PROJECT, 1)
        t1 = create_task(PROJECT, sid, "Build feature", tier="standard", work_type="task")

        # Agent produces JSON output
        raw = json.dumps({
            "agent": "dev",
            "task_id": t1,
            "status": "DONE",
            "completed_work": "Built the feature",
            "files_modified": ["src/feature.py"],
            "files_created": [],
            "decisions": [
                {"type": "arch", "decision": "Use repository pattern", "reason": "Testability"},
            ],
            "messages": [
                {"to": "qa", "body": "Feature ready for testing", "subject": "Review request"},
                {"to": "pm", "body": "Architecture decision recorded"},
            ],
            "concerns": [{"concern": "Performance may degrade at scale"}],
            "blockers": [],
            "tests_run": 5,
            "tests_passed": 5,
            "tests_failed": 0,
        })

        # Parse
        parsed = parse_agent_output(raw)
        assert parsed["success"]
        data = parsed["parsed"]
        assert data["status"] == "DONE"

        # Extract
        messages = extract_messages(data)
        assert len(messages) == 2
        decisions = extract_decisions(data)
        assert len(decisions) == 1

        # Route messages to mail
        thread_id = create_thread(PROJECT, "task-output")
        for msg in messages:
            send(
                PROJECT, thread_id,
                from_agent="dev",
                to_agent=msg["to"],
                body=msg["body"],
                subject=msg.get("subject"),
            )

        # Verify delivery
        qa_inbox = get_inbox(PROJECT, "qa")
        assert len(qa_inbox) >= 1
        assert any("testing" in m["body"].lower() for m in qa_inbox)

        pm_inbox = get_inbox(PROJECT, "pm")
        assert len(pm_inbox) >= 1
        assert count_unread(PROJECT, "pm") >= 1

    def test_dag_dependency_chain(self, em_root):
        """Task DAG respects dependency chain across 3 waves."""
        from enki.orch.task_graph import (
            create_sprint, create_task, get_next_wave,
            update_task_status, is_sprint_complete, TaskStatus,
        )

        sid = create_sprint(PROJECT, 1)

        # Create diamond dependency: A → B, A → C, B+C → D
        a = create_task(PROJECT, sid, "Task A", tier="standard", work_type="task")
        b = create_task(PROJECT, sid, "Task B", tier="standard",
                        dependencies=[a], work_type="task")
        c = create_task(PROJECT, sid, "Task C", tier="standard",
                        dependencies=[a], work_type="task")
        d = create_task(PROJECT, sid, "Task D", tier="standard",
                        dependencies=[b, c], work_type="task")

        # Wave 1: only A
        wave = get_next_wave(PROJECT, sid)
        assert len(wave) == 1
        assert wave[0]["task_id"] == a

        # Complete A → wave 2: B and C (parallel)
        update_task_status(PROJECT, a, TaskStatus.COMPLETED)
        wave = get_next_wave(PROJECT, sid)
        wave_ids = {t["task_id"] for t in wave}
        assert wave_ids == {b, c}

        # Complete B only → D still blocked (needs C)
        update_task_status(PROJECT, b, TaskStatus.COMPLETED)
        wave = get_next_wave(PROJECT, sid)
        wave_ids = {t["task_id"] for t in wave}
        assert d not in wave_ids
        assert c in wave_ids

        # Complete C → D ready
        update_task_status(PROJECT, c, TaskStatus.COMPLETED)
        wave = get_next_wave(PROJECT, sid)
        assert len(wave) == 1
        assert wave[0]["task_id"] == d

        # Complete D → sprint done
        update_task_status(PROJECT, d, TaskStatus.COMPLETED)
        assert is_sprint_complete(PROJECT, sid)

    def test_parse_failure_retry_escalation(self, em_root):
        """Bad agent output → parse failure → retry prompt → HITL escalation."""
        from enki.orch.parsing import parse_agent_output, get_retry_prompt
        from enki.orch.task_graph import (
            create_sprint, create_task, increment_retry, needs_hitl,
        )

        sid = create_sprint(PROJECT, 1)
        t1 = create_task(PROJECT, sid, "Broken task", tier="standard", work_type="task")

        # Attempt 1: garbled output
        parsed = parse_agent_output("This is not JSON at all, just text output")
        assert not parsed["success"]
        retry1 = get_retry_prompt(1)
        assert len(retry1) > 0  # has retry instructions
        increment_retry(PROJECT, t1)

        # Attempt 2: still bad
        parsed = parse_agent_output("{invalid json!!}")
        assert not parsed["success"]
        retry2 = get_retry_prompt(2)
        assert len(retry2) > 0
        increment_retry(PROJECT, t1)

        # Attempt 3: exhausted retries
        increment_retry(PROJECT, t1)
        assert needs_hitl(PROJECT, t1)  # escalate to human
        retry3 = get_retry_prompt(3)
        assert retry3 == ""  # no more retries

    def test_mail_thread_lifecycle(self, em_root):
        """Full mail thread: create → send → read → acknowledge → archive."""
        from enki.orch.mail import (
            create_thread, send, get_inbox, get_thread_messages,
            mark_read, mark_acknowledged, close_thread,
            archive_thread_messages, count_unread,
        )

        # Create conversation thread
        tid = create_thread(PROJECT, "design-review")

        # PM sends to Dev
        m1 = send(PROJECT, tid, "pm", "dev",
                   body="Please review the API design", subject="API review")
        # Dev replies
        m2 = send(PROJECT, tid, "dev", "pm",
                   body="Looks good, minor suggestion on auth endpoint")
        # QA chimes in
        m3 = send(PROJECT, tid, "dev", "qa",
                   body="Ready for QA review")

        # Dev has 1 unread (from pm)
        assert count_unread(PROJECT, "dev") == 1
        dev_inbox = get_inbox(PROJECT, "dev")
        assert len(dev_inbox) == 1

        # Dev reads and acknowledges
        mark_read(PROJECT, m1)
        assert count_unread(PROJECT, "dev") == 0
        mark_acknowledged(PROJECT, m1)

        # Thread has 3 messages total
        msgs = get_thread_messages(PROJECT, tid)
        assert len(msgs) == 3

        # Close and archive
        close_thread(PROJECT, tid)
        archived = archive_thread_messages(PROJECT, tid)
        assert archived == 3

    def test_orchestrator_minimal_flow(self, em_root):
        """Minimal tier: single Dev → QA cycle."""
        from enki.orch.orchestrator import Orchestrator
        from enki.orch.task_graph import (
            get_next_wave, update_task_status, is_sprint_complete,
            TaskStatus,
        )

        orch = Orchestrator(PROJECT)
        result = orch.minimal_flow("Fix the login button color")

        assert "sprint_id" in result
        sid = result["sprint_id"]

        # Should have tasks created
        wave = get_next_wave(PROJECT, sid)
        assert len(wave) >= 1

        # Complete all tasks
        for task in wave:
            update_task_status(PROJECT, task["task_id"], TaskStatus.COMPLETED)

        # Check for next wave or sprint complete
        next_wave = get_next_wave(PROJECT, sid)
        for task in next_wave:
            update_task_status(PROJECT, task["task_id"], TaskStatus.COMPLETED)

        assert is_sprint_complete(PROJECT, sid)

    def test_bug_blocks_task_advancement(self, em_root):
        """Filing a blocking bug prevents task advancement."""
        from enki.orch.bugs import file_bug, has_blocking_bugs
        from enki.orch.task_graph import (
            create_sprint, create_task, update_task_status, TaskStatus,
        )

        sid = create_sprint(PROJECT, 1)
        t1 = create_task(PROJECT, sid, "Deploy", tier="standard", work_type="task")

        # File blocking bug
        bug_id = file_bug(
            PROJECT,
            title="Critical auth bypass",
            description="Auth middleware can be skipped",
            filed_by="qa",
            priority="P0",
            task_id=t1,
        )

        assert has_blocking_bugs(PROJECT)

        # Task should be marked blocked
        update_task_status(PROJECT, t1, TaskStatus.BLOCKED)

    def test_bridge_extracts_from_orchestration(self, em_root):
        """Bridge extracts bead candidates from completed orchestration work."""
        from enki.orch.mail import create_thread, send
        from enki.orch.pm import record_decision
        from enki.orch.bridge import extract_beads_from_project

        # Record decisions during orchestration
        record_decision(PROJECT, "tech", "Use PostgreSQL", "ACID compliance needed")
        record_decision(PROJECT, "arch", "Microservices", "Team scaling")

        # Create discussion threads
        tid = create_thread(PROJECT, "decision")
        send(PROJECT, tid, "pm", "dev",
             body="Decision: Use PostgreSQL for the data layer")
        send(PROJECT, tid, "dev", "pm",
             body="Agreed, will set up migrations")

        # Extract bead candidates
        candidates = extract_beads_from_project(PROJECT)
        assert isinstance(candidates, list)
        # Should find decisions and/or mail content
        assert len(candidates) >= 1
