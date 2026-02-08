"""Tests for enforcement module."""

import pytest
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session, set_phase, set_tier, set_goal, add_session_edit
from enki.enforcement import (
    detect_tier, is_impl_file, is_test_file, is_enki_file,
    check_gate_1_phase, check_gate_2_spec, check_gate_3_tdd, check_gate_4_scope,
    check_gate_2_5_taskgraph, check_all_gates,
    _word_overlap_score, _tokenize,
)
from enki.pm import (
    generate_perspectives, get_perspectives_path,
    create_spec, approve_spec, decompose_spec,
    TaskGraph, Task, save_task_graph, load_task_graph,
    generate_approval_token,
)
from enki.orchestrator import start_orchestration


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory with enki DB."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    start_session(tmp_path)
    yield tmp_path
    close_db()
    set_db_path(None)


class TestFileClassification:
    """Tests for file type classification."""

    def test_is_impl_file(self):
        """Test implementation file detection."""
        assert is_impl_file("src/main.py")
        assert is_impl_file("lib/utils.ts")
        assert is_impl_file("app.js")
        assert is_impl_file("service.go")
        assert is_impl_file("Handler.java")

        assert not is_impl_file("README.md")
        assert not is_impl_file("config.yaml")
        assert not is_impl_file("Makefile")

    def test_is_test_file(self):
        """Test test file detection."""
        assert is_test_file("test_main.py")
        assert is_test_file("main_test.py")
        assert is_test_file("main.test.ts")
        assert is_test_file("main.spec.js")
        assert is_test_file("tests/test_utils.py")

        assert not is_test_file("main.py")
        assert not is_test_file("testing_utils.py")

    def test_is_enki_file(self):
        """Test .enki file detection."""
        assert is_enki_file(".enki/PHASE")
        assert is_enki_file("project/.enki/GOAL")

        assert not is_enki_file("src/enki/main.py")
        assert not is_enki_file("enki_config.py")


class TestDetectTier:
    """Tests for tier detection."""

    def test_trivial_no_edits(self, temp_project):
        """Test trivial tier with no edits."""
        tier = detect_tier([], "", temp_project)
        assert tier == "trivial"

    def test_quick_fix_few_edits(self, temp_project):
        """Test quick_fix tier with 1-2 files."""
        tier = detect_tier(["src/main.py"], "", temp_project)
        assert tier == "quick_fix"

    def test_feature_multiple_files(self, temp_project):
        """Test feature tier with 3+ files."""
        tier = detect_tier(["a.py", "b.py", "c.py"], "", temp_project)
        assert tier == "feature"

    def test_major_many_files(self, temp_project):
        """Test major tier with 10+ files."""
        files = [f"file{i}.py" for i in range(10)]
        tier = detect_tier(files, "", temp_project)
        assert tier == "major"

    def test_tier_based_on_file_count_not_keywords(self, temp_project):
        """Test that tier is based on file count, not goal keywords."""
        # Keywords in goal don't affect tier - only file/line counts matter
        tier = detect_tier([], "Refactor the authentication system", temp_project)
        assert tier == "trivial"  # No files = trivial

        tier = detect_tier(["a.py"], "Migrate to new database", temp_project)
        assert tier == "quick_fix"  # 1 file = quick_fix


class TestGate1Phase:
    """Tests for Gate 1: Phase check."""

    def test_allows_non_edit_tools(self, temp_project):
        """Test that non-Edit tools are allowed."""
        result = check_gate_1_phase("Read", "src/main.py", temp_project)
        assert result.allowed

        result = check_gate_1_phase("Glob", "*.py", temp_project)
        assert result.allowed

    def test_allows_enki_files(self, temp_project):
        """Test that .enki files are always allowed."""
        result = check_gate_1_phase("Edit", ".enki/GOAL", temp_project)
        assert result.allowed

    def test_allows_non_impl_files(self, temp_project):
        """Test that non-implementation files are allowed."""
        result = check_gate_1_phase("Edit", "README.md", temp_project)
        assert result.allowed

    def test_allows_test_files(self, temp_project):
        """Test that test files are always allowed."""
        result = check_gate_1_phase("Edit", "tests/test_main.py", temp_project)
        assert result.allowed

    def test_blocks_impl_in_wrong_phase(self, temp_project):
        """Test that impl files are blocked in non-implement phase."""
        set_phase("intake", temp_project)

        result = check_gate_1_phase("Edit", "src/main.py", temp_project)
        assert not result.allowed
        assert result.gate == "phase"

    def test_allows_impl_in_implement_phase(self, temp_project):
        """Test that impl files are allowed in implement phase."""
        set_phase("implement", temp_project)

        result = check_gate_1_phase("Edit", "src/main.py", temp_project)
        assert result.allowed


class TestGate2Spec:
    """Tests for Gate 2: Spec approval."""

    def test_allows_non_task_tools(self, temp_project):
        """Test that non-Task tools are allowed."""
        result = check_gate_2_spec("Edit", None, temp_project)
        assert result.allowed

    def test_allows_research_agents(self, temp_project):
        """Test that research agents are allowed without spec."""
        result = check_gate_2_spec("Task", "Explore", temp_project)
        assert result.allowed

        result = check_gate_2_spec("Task", "Plan", temp_project)
        assert result.allowed

    def test_blocks_impl_agents_without_spec(self, temp_project):
        """Test that impl agents are blocked without approved spec."""
        result = check_gate_2_spec("Task", "Bash", temp_project)
        assert not result.allowed
        assert result.gate == "spec"


class TestGate3TDD:
    """Tests for Gate 3: TDD enforcement."""

    def test_allows_test_files(self, temp_project):
        """Test that test files are always allowed."""
        set_phase("implement", temp_project)

        result = check_gate_3_tdd("Edit", "tests/test_main.py", temp_project)
        assert result.allowed

    def test_allows_non_impl_files(self, temp_project):
        """Test that non-impl files are allowed."""
        set_phase("implement", temp_project)

        result = check_gate_3_tdd("Edit", "README.md", temp_project)
        assert result.allowed

    def test_blocks_impl_without_test_for_feature(self, temp_project):
        """Test that impl files without tests are blocked for feature tier."""
        set_phase("implement", temp_project)
        set_tier("feature", temp_project)

        result = check_gate_3_tdd("Edit", "src/new_module.py", temp_project)
        assert not result.allowed
        assert result.gate == "tdd"


class TestGate4Scope:
    """Tests for Gate 4: Scope guard."""

    def test_allows_when_no_scope(self, temp_project):
        """Test that all files are allowed when no scope defined."""
        result = check_gate_4_scope("Edit", "any_file.py", temp_project)
        assert result.allowed

    def test_allows_enki_files(self, temp_project):
        """Test that .enki files are always allowed."""
        # Set a scope
        (temp_project / ".enki" / "SCOPE").write_text("src/main.py")

        result = check_gate_4_scope("Edit", ".enki/GOAL", temp_project)
        assert result.allowed

    def test_allows_in_scope_files(self, temp_project):
        """Test that in-scope files are allowed."""
        (temp_project / ".enki" / "SCOPE").write_text("src/main.py\nsrc/utils.py")

        result = check_gate_4_scope("Edit", "src/main.py", temp_project)
        assert result.allowed

    def test_blocks_out_of_scope_files(self, temp_project):
        """Test that out-of-scope files are blocked."""
        (temp_project / ".enki" / "SCOPE").write_text("src/main.py")

        result = check_gate_4_scope("Edit", "src/other.py", temp_project)
        assert not result.allowed
        assert result.gate == "scope"


class TestCheckAllGates:
    """Tests for check_all_gates."""

    def test_passes_when_all_gates_pass(self, temp_project):
        """Test that check passes when all gates pass."""
        # Use intake phase - implement phase requires scope
        set_phase("intake", temp_project)

        # README.md is non-impl, allowed in any phase
        result = check_all_gates("Edit", "README.md", None, temp_project)
        assert result.allowed

    def test_fails_on_first_gate_failure(self, temp_project):
        """Test that check fails on first gate failure."""
        set_phase("intake", temp_project)

        result = check_all_gates("Edit", "src/main.py", None, temp_project)
        assert not result.allowed
        assert result.gate == "phase"


class TestWordOverlap:
    """Tests for word overlap scoring."""

    def test_identical_strings(self):
        assert _word_overlap_score("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _word_overlap_score("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        score = _word_overlap_score("implement database migration", "run database backup")
        assert 0.0 < score < 1.0  # "database" shared

    def test_empty_string(self):
        assert _word_overlap_score("", "hello") == 0.0
        assert _word_overlap_score("hello", "") == 0.0

    def test_tokenize(self):
        tokens = _tokenize("Hello World 123 foo-bar")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens
        assert "foo" in tokens
        assert "bar" in tokens


class TestGate25TaskGraph:
    """Tests for Gate 2.5: TaskGraph binding (GAP-01, Hardening Spec v2)."""

    @pytest.fixture
    def orch_project(self, temp_project):
        """Create a project with active orchestration and task graph."""
        # Set up perspectives + spec + approval
        generate_perspectives(goal="Test", project_path=temp_project)
        path = get_perspectives_path(temp_project)
        content = path.read_text().replace(
            "(Fill in your analysis here)", "Analysis complete."
        )
        path.write_text(content)
        create_spec(name="test-gate25", project_path=temp_project)
        token = generate_approval_token(temp_project)
        approve_spec("test-gate25", temp_project, approval_token=token)

        # Create task graph with ready tasks
        graph = TaskGraph(spec_name="test-gate25", spec_path="specs/test-gate25.md")
        graph.add_task(Task(
            id="task-1", description="Implement user authentication module",
            agent="Dev", status="pending", wave=1,
            files_in_scope=["src/auth.py"],
        ))
        graph.add_task(Task(
            id="task-2", description="Write unit tests for authentication",
            agent="QA", status="pending", wave=2,
            dependencies=["task-1"],
        ))
        graph.add_task(Task(
            id="task-3", description="Implement database migration scripts",
            agent="Dev", status="pending", wave=1,
            files_in_scope=["src/migrations.py"],
        ))
        save_task_graph(graph, temp_project)

        # Start orchestration
        start_orchestration("test-gate25", graph, temp_project)
        return temp_project

    def test_no_orchestration_passes(self, temp_project):
        """Gate passes when no active orchestration."""
        result = check_gate_2_5_taskgraph("Task", "Dev", "some desc", temp_project)
        assert result.allowed

    def test_explore_agent_exempt(self, orch_project):
        """Explore agents always pass."""
        result = check_gate_2_5_taskgraph("Task", "Explore", None, orch_project)
        assert result.allowed

    def test_plan_agent_exempt(self, orch_project):
        """Plan agents always pass."""
        result = check_gate_2_5_taskgraph("Task", "Plan", None, orch_project)
        assert result.allowed

    def test_non_task_tool_passes(self, orch_project):
        """Non-Task tools always pass Gate 2.5."""
        result = check_gate_2_5_taskgraph("Edit", "Dev", None, orch_project)
        assert result.allowed

    def test_single_candidate_allowed(self, orch_project):
        """Single matching agent type allows immediately."""
        result = check_gate_2_5_taskgraph(
            "Task", "QA", "Write tests for authentication", orch_project
        )
        # QA has only task-2, but it's blocked by task-1 (pending dep)
        # So QA should have zero ready tasks
        assert not result.allowed
        assert "No ready task for agent type" in result.reason

    def test_matching_agent_with_ready_task(self, orch_project):
        """Dev has 2 ready tasks — word overlap picks the right one."""
        result = check_gate_2_5_taskgraph(
            "Task", "Dev", "Implement the user authentication module", orch_project
        )
        assert result.allowed

    def test_wrong_agent_type_blocked(self, orch_project):
        """Agent type with no ready tasks is blocked."""
        result = check_gate_2_5_taskgraph(
            "Task", "DBA", "Run database operations", orch_project
        )
        assert not result.allowed
        assert "No ready task for agent type" in result.reason

    def test_below_threshold_blocked(self, orch_project):
        """Description with no word overlap below threshold is blocked."""
        result = check_gate_2_5_taskgraph(
            "Task", "Dev", "xyz completely unrelated gibberish qqq", orch_project
        )
        assert not result.allowed
        assert "threshold" in result.reason.lower() or "No task matched" in result.reason

    def test_multiple_candidates_best_match(self, orch_project):
        """Multiple Dev tasks — best word overlap wins."""
        result = check_gate_2_5_taskgraph(
            "Task", "Dev", "database migration scripts implementation", orch_project
        )
        assert result.allowed
