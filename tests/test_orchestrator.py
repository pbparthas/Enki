"""Tests for Orchestrator module."""

import pytest
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session, set_phase
from enki.pm import (
    generate_perspectives, get_perspectives_path,
    create_spec, approve_spec, decompose_spec, TaskGraph, Task,
    generate_approval_token,
)
from enki.orchestrator import (
    Bug, Orchestration, AGENTS,
    start_orchestration, load_orchestration, save_orchestration,
    start_task, complete_task, fail_task,
    file_bug, assign_bug, start_bug_verification, close_bug, reopen_bug, get_open_bugs,
    escalate_to_hitl, resolve_hitl, check_hitl_required,
    get_full_orchestration_status, get_next_action,
    generate_orchestration_id, generate_bug_id,
    validate_escalation_evidence,
    check_gate_4_5_validation,
    submit_for_validation, record_validation_result,
    needs_validation,
    check_gate_5_completion, complete_orchestration,
    ValidationResult, run_validation_hierarchy,
    get_validator_tier, classify_validation_verdict,
)
from enki.agents_config import VALIDATION_TIERS, VALIDATOR_TIERS


def _make_valid_evidence():
    """Helper: create valid escalation evidence for tests."""
    return {
        "attempts": [
            {
                "description": "Tried approach A with standard configuration and defaults",
                "result": "Failed with timeout error after 30 seconds elapsed",
                "why_failed": "The connection pool was exhausted because max connections was set too low",
            },
            {
                "description": "Tried approach B by increasing pool size to 50 connections",
                "result": "Failed with memory error when pool reached capacity",
                "why_failed": "Each connection consumes too much memory, pool size alone is not the solution",
            },
            {
                "description": "Tried approach C using connection recycling with short TTL",
                "result": "Partial success but introduced latency spikes on recycling",
                "why_failed": "Recycling creates connection storms during peak load, need a different strategy entirely",
            },
        ],
        "hypothesis": "The connection management architecture needs a fundamental redesign to handle load properly",
        "resolution_options": [
            "Implement connection multiplexing at the proxy layer",
            "Switch to a connectionless protocol for non-transactional queries",
        ],
    }


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
    token = generate_approval_token(temp_project)
    approve_spec("test-feature", temp_project, approval_token=token)

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

        escalate_to_hitl("Manual escalation needed", temp_project, evidence=_make_valid_evidence())

        required, reason = check_hitl_required(temp_project)

        assert required is True
        assert "Manual escalation needed" in reason

    def test_resolve_hitl(self, temp_project, approved_spec):
        """Test resolving HITL."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        escalate_to_hitl("Problem found", temp_project, evidence=_make_valid_evidence())
        resolve_hitl("Problem fixed by human", temp_project)

        required, reason = check_hitl_required(temp_project)

        assert required is False
        assert reason is None


class TestEscalationEvidence:
    """Tests for escalation evidence gate (GAP-07, Hardening Spec v2)."""

    def test_valid_evidence_passes(self):
        """Valid evidence with 3+ attempts passes validation."""
        valid, msg = validate_escalation_evidence(_make_valid_evidence())
        assert valid is True

    def test_no_evidence_raises(self, temp_project, approved_spec):
        """Escalation without evidence raises ValueError."""
        graph = decompose_spec(approved_spec, temp_project)
        start_orchestration(approved_spec, graph, temp_project)

        with pytest.raises(ValueError, match="ESCALATION EVIDENCE REQUIRED"):
            escalate_to_hitl("lazy reason", temp_project)

    def test_too_few_attempts(self):
        """Less than 3 attempts rejected."""
        evidence = _make_valid_evidence()
        evidence["attempts"] = evidence["attempts"][:2]
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "Minimum 3" in msg

    def test_short_description_rejected(self):
        """Attempt with short description rejected."""
        evidence = _make_valid_evidence()
        evidence["attempts"][0]["description"] = "short"
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "description" in msg

    def test_short_result_rejected(self):
        """Attempt with short result rejected."""
        evidence = _make_valid_evidence()
        evidence["attempts"][0]["result"] = "fail"
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "result" in msg

    def test_short_why_failed_rejected(self):
        """Attempt with short why_failed rejected."""
        evidence = _make_valid_evidence()
        evidence["attempts"][0]["why_failed"] = "dunno"
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "why_failed" in msg

    def test_duplicate_attempts_rejected(self):
        """Identical attempt descriptions rejected."""
        evidence = _make_valid_evidence()
        evidence["attempts"][1]["description"] = evidence["attempts"][0]["description"]
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "identical" in msg

    def test_short_hypothesis_rejected(self):
        """Short hypothesis rejected."""
        evidence = _make_valid_evidence()
        evidence["hypothesis"] = "idk"
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "Hypothesis" in msg

    def test_too_few_resolution_options(self):
        """Less than 2 resolution options rejected."""
        evidence = _make_valid_evidence()
        evidence["resolution_options"] = ["only one option"]
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "resolution options" in msg

    def test_empty_resolution_option_rejected(self):
        """Empty resolution option rejected."""
        evidence = _make_valid_evidence()
        evidence["resolution_options"] = ["valid option here", ""]
        valid, msg = validate_escalation_evidence(evidence)
        assert valid is False
        assert "non-empty" in msg

    def test_non_dict_evidence_rejected(self):
        """Non-dict evidence rejected."""
        valid, msg = validate_escalation_evidence("not a dict")
        assert valid is False
        assert "must be a dict" in msg


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
        escalate_to_hitl("Problem", temp_project, evidence=_make_valid_evidence())

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


class TestGate45Validation:
    """Tests for Gate 4.5: Validation enforcement (GAP-03, Hardening Spec v2)."""

    @pytest.fixture
    def orch_project(self, temp_project, approved_spec):
        """Create project with orchestration containing Dev and Architect tasks."""
        graph = TaskGraph(spec_name=approved_spec, spec_path=f"specs/{approved_spec}.md")
        graph.add_task(Task(
            id="task-dev", description="Implement auth module",
            agent="Dev", status="pending", wave=1,
            files_in_scope=["src/auth.py"],
        ))
        graph.add_task(Task(
            id="task-arch", description="Design API schema",
            agent="Architect", status="pending", wave=1,
        ))
        graph.add_task(Task(
            id="task-qa", description="Write auth tests",
            agent="QA", status="pending", wave=1,
            files_in_scope=["tests/test_auth.py"],
        ))
        start_orchestration(approved_spec, graph, temp_project)
        return temp_project

    def test_no_orchestration_passes(self, temp_project):
        """Gate 4.5 passes when no active orchestration."""
        allowed, reason = check_gate_4_5_validation("any-task", temp_project)
        assert allowed
        assert "No active orchestration" in reason

    def test_no_validators_passes(self, orch_project):
        """Gate 4.5 passes for agent types with no validators (Architect)."""
        start_task("task-arch", orch_project)
        allowed, reason = check_gate_4_5_validation("task-arch", orch_project)
        assert allowed
        assert "No validators" in reason

    def test_dev_task_blocked_without_validation(self, orch_project):
        """Gate 4.5 blocks Dev task completion without validators passed."""
        start_task("task-dev", orch_project)
        allowed, reason = check_gate_4_5_validation("task-dev", orch_project)
        assert not allowed
        assert "GATE 4.5" in reason
        assert "Validator-Tests" in reason

    def test_dev_task_allowed_after_validation_passed(self, orch_project):
        """Gate 4.5 passes after all validators have passed."""
        start_task("task-dev", orch_project)
        # Dev has two validators: Validator-Tests, Validator-Code
        submit_for_validation("task-dev", "done", orch_project)
        # First validator passes (stage 1)
        record_validation_result("task-dev", "Validator-Tests", True, project_path=orch_project)
        # Second validator passes (stage 2) — this marks task complete
        record_validation_result("task-dev", "Validator-Code", True, project_path=orch_project)
        # Task now has validation_status=passed
        allowed, reason = check_gate_4_5_validation("task-dev", orch_project)
        assert allowed
        assert "passed" in reason.lower()

    def test_skip_validation_blocked_for_dev(self, orch_project):
        """Cannot skip_validation for Dev tasks (has validators)."""
        start_task("task-dev", orch_project)
        with pytest.raises(ValueError, match="GATE 4.5"):
            complete_task("task-dev", output="done", project_path=orch_project, skip_validation=True)

    def test_skip_validation_allowed_for_architect(self, orch_project):
        """Can skip_validation for Architect tasks (no validators)."""
        start_task("task-arch", orch_project)
        task = complete_task("task-arch", output="design done", project_path=orch_project, skip_validation=True)
        assert task.status == "complete"

    def test_complete_task_submits_for_validation(self, orch_project):
        """complete_task routes Dev tasks to validation flow."""
        start_task("task-dev", orch_project)
        task = complete_task("task-dev", output="implemented", project_path=orch_project)
        assert task.status == "validating"
        assert task.validation_status == "pending"

    def test_rejection_loop_reverts_to_rejected(self, orch_project):
        """Validator rejection sets task to rejected state."""
        start_task("task-dev", orch_project)
        submit_for_validation("task-dev", "done", orch_project)
        task = record_validation_result(
            "task-dev", "Validator-Tests", False,
            feedback="Tests don't cover edge cases",
            project_path=orch_project,
        )
        assert task.status == "rejected"
        assert task.rejection_count == 1

    def test_max_rejections_escalates_to_hitl(self, orch_project):
        """Exceeding max_rejections triggers HITL escalation."""
        start_task("task-dev", orch_project)

        # First rejection
        submit_for_validation("task-dev", "done", orch_project)
        record_validation_result("task-dev", "Validator-Tests", False,
                                 feedback="Issue 1", project_path=orch_project)

        # Mark active again for retry
        orch = load_orchestration(orch_project)
        orch.task_graph.tasks["task-dev"].status = "active"
        orch.task_graph.tasks["task-dev"].validation_status = "none"
        save_orchestration(orch, orch_project)

        # Second rejection (max_rejections=2)
        submit_for_validation("task-dev", "done v2", orch_project)
        task = record_validation_result("task-dev", "Validator-Tests", False,
                                        feedback="Issue 2", project_path=orch_project)
        assert task.status == "failed"
        assert task.rejection_count == 2

        # HITL should be required
        orch = load_orchestration(orch_project)
        assert orch.hitl_required

    def test_qa_task_needs_validation(self, orch_project):
        """QA tasks also need validation (Validator-Tests)."""
        start_task("task-qa", orch_project)
        allowed, reason = check_gate_4_5_validation("task-qa", orch_project)
        assert not allowed
        assert "GATE 4.5" in reason

    def test_task_not_found_returns_false(self, orch_project):
        """Gate returns False for unknown task ID."""
        allowed, reason = check_gate_4_5_validation("nonexistent", orch_project)
        assert not allowed
        assert "not found" in reason.lower()


class TestGate5Completion:
    """Tests for Gate 5: Orchestration completion with validation_commands (GAP-05)."""

    @pytest.fixture
    def orch_project(self, temp_project, approved_spec):
        """Project with orchestration — single Architect task (no validators needed)."""
        graph = TaskGraph(spec_name=approved_spec, spec_path=f"specs/{approved_spec}.md")
        graph.add_task(Task(
            id="task-arch", description="Design module",
            agent="Architect", status="pending", wave=1,
        ))
        start_orchestration(approved_spec, graph, temp_project)
        return temp_project

    def test_no_orchestration_passes(self, temp_project):
        """Gate 5 passes when no active orchestration."""
        allowed, reason = check_gate_5_completion(temp_project)
        assert allowed
        assert "No active orchestration" in reason

    def test_no_validation_commands_passes(self, orch_project):
        """Gate 5 passes when no validation_commands configured."""
        allowed, reason = check_gate_5_completion(orch_project)
        assert allowed
        assert "No validation_commands" in reason

    def test_passing_command(self, orch_project):
        """Gate 5 passes when all commands succeed."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["true"]  # Always exits 0
        save_orchestration(orch, orch_project)

        allowed, reason = check_gate_5_completion(orch_project)
        assert allowed
        assert "passed" in reason.lower()

    def test_failing_command_blocks(self, orch_project):
        """Gate 5 blocks when a command fails."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["false"]  # Always exits 1
        save_orchestration(orch, orch_project)

        allowed, reason = check_gate_5_completion(orch_project)
        assert not allowed
        assert "GATE 5" in reason
        assert "failed" in reason.lower()

    def test_multiple_commands_all_must_pass(self, orch_project):
        """All commands must pass — one failure blocks."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["true", "false", "true"]
        save_orchestration(orch, orch_project)

        allowed, reason = check_gate_5_completion(orch_project)
        assert not allowed
        assert "GATE 5" in reason

    def test_invalid_command_blocks(self, orch_project):
        """Empty/invalid command string blocks."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = [""]
        save_orchestration(orch, orch_project)

        allowed, reason = check_gate_5_completion(orch_project)
        assert not allowed
        assert "Invalid command" in reason

    def test_complete_orchestration_with_passing_commands(self, orch_project):
        """complete_orchestration succeeds when Gate 5 passes."""
        # Complete the task first
        start_task("task-arch", orch_project)
        complete_task("task-arch", output="designed", project_path=orch_project, skip_validation=True)

        # Set validation commands
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["true"]
        orch.status = "active"  # Reset — complete_task may have already completed it
        save_orchestration(orch, orch_project)

        result = complete_orchestration(orch_project)
        assert result.status == "completed"

    def test_complete_orchestration_blocked_by_failing_commands(self, orch_project):
        """complete_orchestration raises when Gate 5 fails."""
        start_task("task-arch", orch_project)
        complete_task("task-arch", output="designed", project_path=orch_project, skip_validation=True)

        orch = load_orchestration(orch_project)
        orch.validation_commands = ["false"]
        orch.status = "active"
        save_orchestration(orch, orch_project)

        with pytest.raises(ValueError, match="GATE 5"):
            complete_orchestration(orch_project)

    def test_complete_orchestration_blocked_with_incomplete_tasks(self, orch_project):
        """complete_orchestration raises when tasks aren't complete."""
        with pytest.raises(ValueError, match="not complete"):
            complete_orchestration(orch_project)

    def test_implicit_completion_blocked_by_gate5(self, orch_project):
        """complete_task doesn't mark orchestration complete when Gate 5 fails."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["false"]
        save_orchestration(orch, orch_project)

        start_task("task-arch", orch_project)
        complete_task("task-arch", output="done", project_path=orch_project, skip_validation=True)

        # Orchestration should NOT be completed
        orch = load_orchestration(orch_project)
        assert orch.status != "completed"

    def test_implicit_completion_succeeds_with_gate5(self, orch_project):
        """complete_task marks orchestration complete when Gate 5 passes."""
        orch = load_orchestration(orch_project)
        orch.validation_commands = ["true"]
        save_orchestration(orch, orch_project)

        start_task("task-arch", orch_project)
        complete_task("task-arch", output="done", project_path=orch_project, skip_validation=True)

        orch = load_orchestration(orch_project)
        assert orch.status == "completed"

    def test_validation_commands_serialization(self):
        """validation_commands field round-trips through serialization."""
        orch = Orchestration(
            id="test", spec_name="test", spec_path="/test.md",
            validation_commands=["pytest tests/", "mypy src/"],
        )
        data = orch.to_dict()
        restored = Orchestration.from_dict(data)
        assert restored.validation_commands == ["pytest tests/", "mypy src/"]


class TestValidationHierarchy:
    """Tests for Validation Hierarchy (Step 7, Hardening Spec v2).

    Tier 1 (deterministic): mandatory, gates completion.
    Tier 2 (LLM review): advisory, surfaced but don't gate.
    Tier 3 (human): only override path for Tier 1 failure.

    INVARIANT: No code path allows Tier 2 to override Tier 1 failure.
    """

    # --- Tier configuration tests ---

    def test_validation_tiers_configured(self):
        """All three tiers are defined."""
        assert 1 in VALIDATION_TIERS
        assert 2 in VALIDATION_TIERS
        assert 3 in VALIDATION_TIERS

    def test_tier1_is_mandatory(self):
        """Tier 1 (deterministic) is mandatory."""
        assert VALIDATION_TIERS[1]["mandatory"] is True

    def test_tier2_is_advisory(self):
        """Tier 2 (LLM review) is NOT mandatory."""
        assert VALIDATION_TIERS[2]["mandatory"] is False

    def test_tier1_override_is_hitl(self):
        """Tier 1 can only be overridden by human (HITL)."""
        assert VALIDATION_TIERS[1]["override"] == "hitl"

    def test_deterministic_validators_are_tier1(self):
        """Validator-Tests, Validator-Code, Validator-Security are Tier 1."""
        assert VALIDATOR_TIERS["Validator-Tests"] == 1
        assert VALIDATOR_TIERS["Validator-Code"] == 1
        assert VALIDATOR_TIERS["Validator-Security"] == 1

    def test_sentinel_agents_are_tier2(self):
        """All sentinel agents are Tier 2 (advisory)."""
        sentinels = [k for k in VALIDATOR_TIERS if k.startswith("Sentinel-")]
        assert len(sentinels) >= 5
        for sentinel in sentinels:
            assert VALIDATOR_TIERS[sentinel] == 2

    def test_unknown_validator_defaults_to_tier1(self):
        """Unknown validator defaults to Tier 1 (fail-closed)."""
        assert get_validator_tier("Unknown-Agent") == 1

    # --- run_validation_hierarchy tests ---

    def test_no_commands_passes(self, temp_project):
        """Empty tier 1 commands = pass."""
        result = run_validation_hierarchy(
            deterministic_commands=[],
            project_path=temp_project,
        )
        assert result.tier1_passed
        assert result.can_complete
        assert not result.override_required

    def test_passing_tier1_commands(self, temp_project):
        """Tier 1 commands that exit 0 = pass."""
        result = run_validation_hierarchy(
            deterministic_commands=["true", "true"],
            project_path=temp_project,
        )
        assert result.tier1_passed
        assert result.can_complete
        assert len(result.tier1_failures) == 0

    def test_failing_tier1_blocks_completion(self, temp_project):
        """Tier 1 failure blocks can_complete."""
        result = run_validation_hierarchy(
            deterministic_commands=["false"],
            project_path=temp_project,
        )
        assert not result.tier1_passed
        assert not result.can_complete
        assert result.override_required
        assert len(result.tier1_failures) == 1

    def test_tier2_findings_dont_gate(self, temp_project):
        """Tier 2 findings are surfaced but don't affect can_complete."""
        result = run_validation_hierarchy(
            deterministic_commands=["true"],
            tier2_findings=["Consider simplifying module X", "Duplication in Y"],
            project_path=temp_project,
        )
        assert result.tier1_passed
        assert result.can_complete  # Tier 2 doesn't gate
        assert len(result.tier2_findings) == 2

    def test_invariant_tier2_cannot_override_tier1(self, temp_project):
        """INVARIANT: Tier 2 pass + Tier 1 fail = BLOCKED."""
        result = run_validation_hierarchy(
            deterministic_commands=["false"],
            tier2_findings=[],  # Tier 2 is clean
            project_path=temp_project,
        )
        assert not result.tier1_passed
        assert not result.can_complete  # Tier 2 cannot save this
        assert result.override_required

    def test_tier1_fail_with_tier2_findings(self, temp_project):
        """Both tiers have issues — Tier 1 failure gates, Tier 2 advisory."""
        result = run_validation_hierarchy(
            deterministic_commands=["true", "false"],
            tier2_findings=["Finding A"],
            project_path=temp_project,
        )
        assert not result.can_complete
        assert result.override_required
        assert len(result.tier1_failures) == 1
        assert len(result.tier2_findings) == 1

    def test_summary_format(self, temp_project):
        """ValidationResult.summary is readable."""
        result = run_validation_hierarchy(
            deterministic_commands=["true"],
            tier2_findings=["info"],
            project_path=temp_project,
        )
        assert "PASSED" in result.summary
        assert "1 findings" in result.summary

    # --- classify_validation_verdict tests ---

    def test_classify_tier1_failure_gates(self):
        """Tier 1 validator failure gates completion."""
        verdict = classify_validation_verdict("Validator-Tests", False, "tests fail")
        assert verdict["tier"] == 1
        assert verdict["mandatory"] is True
        assert verdict["gates_completion"] is True

    def test_classify_tier1_pass_doesnt_gate(self):
        """Tier 1 validator pass doesn't gate."""
        verdict = classify_validation_verdict("Validator-Code", True)
        assert verdict["tier"] == 1
        assert verdict["gates_completion"] is False

    def test_classify_tier2_failure_doesnt_gate(self):
        """Tier 2 sentinel failure does NOT gate completion."""
        verdict = classify_validation_verdict("Sentinel-Bugs", False, "found issues")
        assert verdict["tier"] == 2
        assert verdict["mandatory"] is False
        assert verdict["gates_completion"] is False  # Advisory only

    def test_classify_unknown_validator_is_mandatory(self):
        """Unknown validator is treated as Tier 1 (fail-closed)."""
        verdict = classify_validation_verdict("Unknown-Agent", False, "error")
        assert verdict["tier"] == 1
        assert verdict["mandatory"] is True
        assert verdict["gates_completion"] is True
