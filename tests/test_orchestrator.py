"""Tests for Orchestrator module."""

import pytest
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session, set_phase
from enki.pm import (
    generate_perspectives, get_perspectives_path,
    create_spec, approve_spec, decompose_spec, TaskGraph, Task,
)
from enki.orchestrator import (
    Bug, Orchestration, AGENTS,
    start_orchestration, load_orchestration, save_orchestration,
    start_task, complete_task, fail_task,
    file_bug, assign_bug, start_bug_verification, close_bug, reopen_bug, get_open_bugs,
    escalate_to_hitl, resolve_hitl, check_hitl_required,
    get_full_orchestration_status, get_next_action,
    generate_orchestration_id, generate_bug_id,
)


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory with enki DB."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    start_session(tmp_path)

    # Create RUNNING.md
    running_path = tmp_path / ".enki" / "RUNNING.md"
    running_path.write_text("# Enki Running Log\n")

    yield tmp_path
    close_db()
    set_db_path(None)


@pytest.fixture
def approved_spec(temp_project):
    """Create and approve a spec for testing."""
    # Complete debate
    generate_perspectives(goal="Test Feature", project_path=temp_project)
    path = get_perspectives_path(temp_project)
    content = path.read_text().replace(
        "(Fill in your analysis here)",
        "Analysis complete."
    )
    path.write_text(content)

    # Create and approve spec
    create_spec(name="test-feature", project_path=temp_project)
    approve_spec("test-feature", temp_project)

    return "test-feature"


class TestBug:
    """Tests for Bug dataclass."""

    def test_create_bug(self):
        """Test creating a bug."""
        bug = Bug(
            id="BUG-001",
            title="Test bug",
            description="A test bug",
            found_by="QA",
        )

        assert bug.id == "BUG-001"
        assert bug.status == "open"
        assert bug.cycle == 0
        assert bug.max_cycles == 3

    def test_bug_serialization(self):
        """Test bug to_dict and from_dict."""
        bug = Bug(
            id="BUG-001",
            title="Test bug",
            description="A test bug",
            found_by="QA",
            severity="high",
            cycle=2,
        )

        data = bug.to_dict()
        restored = Bug.from_dict(data)

        assert restored.id == bug.id
        assert restored.severity == "high"
        assert restored.cycle == 2


class TestOrchestration:
    """Tests for Orchestration dataclass."""

    def test_create_orchestration(self):
        """Test creating an orchestration."""
        orch = Orchestration(
            id="orch_123",
            spec_name="test",
            spec_path="/path/to/spec.md",
        )

        assert orch.id == "orch_123"
        assert orch.status == "active"
        assert orch.hitl_required is False

    def test_orchestration_serialization(self):
        """Test orchestration to_dict and from_dict."""
        graph = TaskGraph(spec_name="test", spec_path="/path/to/spec.md")
        graph.add_task(Task(id="task_1", description="Test", agent="Dev"))

        orch = Orchestration(
            id="orch_123",
            spec_name="test",
            spec_path="/path/to/spec.md",
            task_graph=graph,
            current_wave=2,
        )

        bug = Bug(id="BUG-001", title="Bug", description="Desc", found_by="QA")
        orch.bugs["BUG-001"] = bug

        data = orch.to_dict()
        restored = Orchestration.from_dict(data)

        assert restored.id == "orch_123"
        assert restored.current_wave == 2
        assert "task_1" in restored.task_graph.tasks
        assert "BUG-001" in restored.bugs


class TestStartOrchestration:
    """Tests for starting orchestration."""

    def test_start_orchestration(self, temp_project, approved_spec):
        """Test starting orchestration from approved spec."""
        graph = decompose_spec(approved_spec, temp_project)
        orch = start_orchestration(approved_spec, graph, temp_project)

        assert orch.spec_name == approved_spec
        assert orch.status == "active"
        assert orch.task_graph is not None

    def test_start_without_approval_raises(self, temp_project):
        """Test starting orchestration without approved spec raises."""
        graph = TaskGraph(spec_name="unapproved", spec_path="/path")

        with pytest.raises(ValueError) as exc:
            start_orchestration("unapproved", graph, temp_project)

        assert "not approved" in str(exc.value)

    def test_save_and_load_orchestration(self, temp_project, approved_spec):
        """Test saving and loading orchestration."""
        graph = decompose_spec(approved_spec, temp_project)
        orch = start_orchestration(approved_spec, graph, temp_project)

        loaded = load_orchestration(temp_project)

        assert loaded is not None
        assert loaded.id == orch.id
        assert loaded.spec_name == approved_spec


class TestTaskExecution:
    """Tests for task execution."""

    def test_start_task(self, temp_project, approved_spec):
        """Test starting a task."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        # Get a ready task
        orch = load_orchestration(temp_project)
        ready = orch.task_graph.get_ready_tasks()
        task_id = ready[0].id

        task = start_task(task_id, temp_project)

        assert task.status == "active"

    def test_complete_task(self, temp_project, approved_spec):
        """Test completing a task."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        orch = load_orchestration(temp_project)
        ready = orch.task_graph.get_ready_tasks()
        task_id = ready[0].id

        start_task(task_id, temp_project)
        task = complete_task(task_id, "Task output here", temp_project)

        assert task.status == "complete"
        assert task.output == "Task output here"

    def test_fail_task_with_retry(self, temp_project, approved_spec):
        """Test failing a task with retry."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        orch = load_orchestration(temp_project)
        ready = orch.task_graph.get_ready_tasks()
        task_id = ready[0].id

        start_task(task_id, temp_project)
        task = fail_task(task_id, "First failure", temp_project)

        assert task.status == "pending"  # Retry
        assert task.attempts == 1

    def test_fail_task_triggers_hitl(self, temp_project, approved_spec):
        """Test failing a task beyond max attempts triggers HITL."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        orch = load_orchestration(temp_project)
        ready = orch.task_graph.get_ready_tasks()
        task_id = ready[0].id

        # Fail 3 times
        for i in range(3):
            start_task(task_id, temp_project)
            task = fail_task(task_id, f"Failure {i+1}", temp_project)

        assert task.status == "failed"

        orch = load_orchestration(temp_project)
        assert orch.hitl_required is True


class TestBugManagement:
    """Tests for bug management."""

    def test_file_bug(self, temp_project, approved_spec):
        """Test filing a bug."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        bug = file_bug(
            title="Test failure",
            description="Tests are failing",
            found_by="QA",
            severity="high",
            project_path=temp_project,
        )

        assert bug.id.startswith("BUG-")
        assert bug.status == "open"
        assert bug.severity == "high"

    def test_assign_bug(self, temp_project, approved_spec):
        """Test assigning a bug."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        bug = file_bug(
            title="Test failure",
            description="Tests are failing",
            found_by="QA",
            project_path=temp_project,
        )

        updated = assign_bug(bug.id, "Dev", temp_project)

        assert updated.assigned_to == "Dev"
        assert updated.status == "fixing"

    def test_close_bug(self, temp_project, approved_spec):
        """Test closing a bug."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        bug = file_bug(
            title="Fixed bug",
            description="Was fixed",
            found_by="QA",
            project_path=temp_project,
        )

        closed = close_bug(bug.id, "fixed", temp_project)

        assert closed.status == "closed"
        assert closed.resolution == "fixed"

    def test_reopen_bug(self, temp_project, approved_spec):
        """Test reopening a bug."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        bug = file_bug(
            title="Flaky fix",
            description="Fix didn't work",
            found_by="QA",
            project_path=temp_project,
        )

        # Verify and reopen
        start_bug_verification(bug.id, temp_project)
        reopened = reopen_bug(bug.id, temp_project)

        assert reopened.status == "fixing"
        assert reopened.cycle == 1

    def test_reopen_bug_triggers_hitl(self, temp_project, approved_spec):
        """Test reopening bug beyond max cycles triggers HITL."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        bug = file_bug(
            title="Persistent bug",
            description="Can't fix",
            found_by="QA",
            project_path=temp_project,
        )

        # Cycle 3 times
        for i in range(3):
            start_bug_verification(bug.id, temp_project)
            bug = reopen_bug(bug.id, temp_project)

        assert bug.status == "hitl"

        orch = load_orchestration(temp_project)
        assert orch.hitl_required is True

    def test_get_open_bugs(self, temp_project, approved_spec):
        """Test getting open bugs."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        file_bug(title="Bug 1", description="Desc", found_by="QA", project_path=temp_project)
        file_bug(title="Bug 2", description="Desc", found_by="QA", project_path=temp_project)

        open_bugs = get_open_bugs(temp_project)

        assert len(open_bugs) == 2


class TestHITL:
    """Tests for HITL escalation."""

    def test_escalate_to_hitl(self, temp_project, approved_spec):
        """Test escalating to HITL."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        escalate_to_hitl("Manual escalation needed", temp_project)

        required, reason = check_hitl_required(temp_project)

        assert required is True
        assert "Manual escalation needed" in reason

    def test_resolve_hitl(self, temp_project, approved_spec):
        """Test resolving HITL."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        escalate_to_hitl("Problem found", temp_project)
        resolve_hitl("Problem fixed by human", temp_project)

        required, reason = check_hitl_required(temp_project)

        assert required is False
        assert reason is None


class TestStatus:
    """Tests for status reporting."""

    def test_get_full_status(self, temp_project, approved_spec):
        """Test getting full orchestration status."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        status = get_full_orchestration_status(temp_project)

        assert status["active"] is True
        assert status["spec"] == approved_spec
        assert "tasks" in status
        assert "bugs" in status
        assert "hitl" in status

    def test_get_next_action_no_orchestration(self, temp_project):
        """Test next action when no orchestration."""
        action = get_next_action(temp_project)

        assert action["action"] == "no_orchestration"

    def test_get_next_action_run_task(self, temp_project, approved_spec):
        """Test next action suggests running task."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        action = get_next_action(temp_project)

        assert action["action"] == "run_task"
        assert "task_id" in action

    def test_get_next_action_hitl(self, temp_project, approved_spec):
        """Test next action shows HITL when required."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)
        escalate_to_hitl("Problem", temp_project)

        action = get_next_action(temp_project)

        assert action["action"] == "hitl_required"


class TestAgents:
    """Tests for agent definitions."""

    def test_agents_defined(self):
        """Test all expected agents are defined."""
        expected = ["Architect", "QA", "Validator-Tests", "Dev", "Validator-Code",
                    "Reviewer", "DBA", "Security", "Docs"]

        for agent in expected:
            assert agent in AGENTS
            assert "role" in AGENTS[agent]
            assert "tools" in AGENTS[agent]
            assert "tier" in AGENTS[agent]
