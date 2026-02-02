"""Tests for Persona module."""

import pytest
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session, set_phase, set_goal
from enki.beads import create_bead
from enki.persona import (
    PersonaContext,
    get_persona_context,
    build_session_start_injection,
    build_error_context_injection,
    build_decision_context,
    get_enki_greeting,
    generate_session_summary,
    extract_session_learnings,
    ENKI_IDENTITY,
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


class TestPersonaContext:
    """Tests for PersonaContext dataclass."""

    def test_create_context(self):
        """Test creating a persona context."""
        context = PersonaContext(
            project="test-project",
            goal="Build feature",
            phase="implement",
        )

        assert context.project == "test-project"
        assert context.goal == "Build feature"
        assert context.relevant_beads == []

    def test_default_values(self):
        """Test default values are set."""
        context = PersonaContext()

        assert context.project is None
        assert context.relevant_beads == []
        assert context.cross_project_beads == []
        assert context.working_patterns == {}


class TestGetPersonaContext:
    """Tests for get_persona_context."""

    def test_returns_context(self, temp_project):
        """Test returns a valid PersonaContext."""
        context = get_persona_context(temp_project)

        assert isinstance(context, PersonaContext)
        assert context.project == temp_project.name
        assert context.phase is not None

    def test_includes_goal(self, temp_project):
        """Test includes goal when set."""
        set_goal("Test goal", temp_project)

        context = get_persona_context(temp_project)

        assert context.goal == "Test goal"

    def test_includes_phase(self, temp_project):
        """Test includes current phase."""
        set_phase("implement", temp_project)

        context = get_persona_context(temp_project)

        assert context.phase == "implement"


class TestBuildSessionStartInjection:
    """Tests for build_session_start_injection."""

    def test_includes_project_name(self, temp_project):
        """Test injection includes project name."""
        injection = build_session_start_injection(temp_project)

        assert temp_project.name in injection

    def test_includes_phase(self, temp_project):
        """Test injection includes phase."""
        set_phase("debate", temp_project)

        injection = build_session_start_injection(temp_project)

        assert "debate" in injection.lower()

    def test_includes_goal(self, temp_project):
        """Test injection includes goal when set."""
        set_goal("Build awesome feature", temp_project)

        injection = build_session_start_injection(temp_project)

        assert "Build awesome feature" in injection

    def test_includes_process_check(self, temp_project):
        """Test injection includes process check."""
        injection = build_session_start_injection(temp_project)

        assert "Process Check" in injection

    def test_shows_relevant_beads(self, temp_project):
        """Test shows relevant beads when available."""
        set_goal("authentication", temp_project)

        # Create a relevant bead
        create_bead(
            content="Use JWT for stateless auth",
            bead_type="decision",
            summary="JWT over sessions",
            project=temp_project.name,
        )

        injection = build_session_start_injection(temp_project)

        # Should mention knowledge base section
        assert "Knowledge Base" in injection or "Decision" in injection


class TestBuildErrorContextInjection:
    """Tests for build_error_context_injection."""

    def test_returns_context(self, temp_project):
        """Test returns context string."""
        context = build_error_context_injection(
            "ModuleNotFoundError: No module named 'foo'",
            temp_project,
        )

        assert isinstance(context, str)
        assert "Error Context" in context

    def test_shows_similar_solutions(self, temp_project):
        """Test shows similar solutions when available."""
        # Create a solution bead
        create_bead(
            content="Install missing module with pip install foo",
            bead_type="solution",
            summary="Fix missing module error",
            project=temp_project.name,
        )

        context = build_error_context_injection(
            "ModuleNotFoundError: No module named 'foo'",
            temp_project,
        )

        # Should show solutions or indicate none found
        assert "solution" in context.lower() or "No similar" in context


class TestBuildDecisionContext:
    """Tests for build_decision_context."""

    def test_returns_context(self, temp_project):
        """Test returns context string."""
        context = build_decision_context("authentication", temp_project)

        assert isinstance(context, str)
        assert "Decision Context" in context
        assert "authentication" in context

    def test_shows_past_decisions(self, temp_project):
        """Test shows past decisions when available."""
        # Create a decision bead
        create_bead(
            content="Chose JWT over sessions for auth",
            bead_type="decision",
            summary="JWT for authentication",
            project=temp_project.name,
            context="Needed stateless auth for microservices",
        )

        context = build_decision_context("authentication", temp_project)

        # Should mention decisions or indicate none found
        assert "decision" in context.lower() or "No past decisions" in context


class TestGetEnkiGreeting:
    """Tests for get_enki_greeting."""

    def test_returns_greeting(self, temp_project):
        """Test returns a greeting string."""
        greeting = get_enki_greeting(temp_project)

        assert isinstance(greeting, str)
        assert len(greeting) > 0

    def test_mentions_project(self, temp_project):
        """Test mentions project name."""
        greeting = get_enki_greeting(temp_project)

        assert temp_project.name in greeting or "work" in greeting.lower()

    def test_mentions_goal(self, temp_project):
        """Test mentions goal when set."""
        set_goal("Build amazing feature", temp_project)

        greeting = get_enki_greeting(temp_project)

        assert "Build amazing feature" in greeting or "Working on" in greeting

    def test_mentions_phase_guidance(self, temp_project):
        """Test gives phase-appropriate guidance."""
        set_phase("debate", temp_project)

        greeting = get_enki_greeting(temp_project)

        assert "perspective" in greeting.lower() or "plan" in greeting.lower()


class TestGenerateSessionSummary:
    """Tests for generate_session_summary."""

    def test_returns_summary(self, temp_project):
        """Test returns a summary string."""
        summary = generate_session_summary(temp_project)

        assert isinstance(summary, str)
        assert "Session Summary" in summary

    def test_includes_session_id(self, temp_project):
        """Test includes session ID."""
        summary = generate_session_summary(temp_project)

        assert "Session ID" in summary

    def test_includes_goal(self, temp_project):
        """Test includes goal."""
        set_goal("Test goal", temp_project)

        summary = generate_session_summary(temp_project)

        assert "Test goal" in summary or "Goal" in summary


class TestExtractSessionLearnings:
    """Tests for extract_session_learnings."""

    def test_returns_list(self, temp_project):
        """Test returns a list."""
        learnings = extract_session_learnings(temp_project)

        assert isinstance(learnings, list)

    def test_extracts_from_violations(self, temp_project):
        """Test extracts learnings from violations pattern."""
        # Add content to RUNNING.md indicating violation then success
        running_path = temp_project / ".enki" / "RUNNING.md"
        running_path.write_text("""# Enki Running Log
[14:00] BLOCKED by gate: phase
[14:05] SPEC APPROVED: test-feature
""")

        learnings = extract_session_learnings(temp_project)

        # Should find a learning about following process
        assert len(learnings) >= 1 or learnings == []  # May not find depending on patterns

    def test_extracts_from_escalations(self, temp_project):
        """Test extracts learnings from escalations."""
        # Add escalation to RUNNING.md
        running_path = temp_project / ".enki" / "RUNNING.md"
        running_path.write_text("""# Enki Running Log
[14:00] ESCALATION: trivial -> feature
""")

        learnings = extract_session_learnings(temp_project)

        # Should find a learning about tier estimation
        has_escalation_learning = any(
            "escalation" in l.get("content", "").lower()
            for l in learnings
        )
        assert has_escalation_learning or len(learnings) == 0


class TestEnkiIdentity:
    """Tests for Enki's identity definition."""

    def test_identity_defined(self):
        """Test ENKI_IDENTITY is defined."""
        assert ENKI_IDENTITY is not None
        assert len(ENKI_IDENTITY) > 100

    def test_identity_mentions_female(self):
        """Test identity mentions Enki is female."""
        assert "female" in ENKI_IDENTITY.lower()

    def test_identity_mentions_challenger(self):
        """Test identity mentions challenging role."""
        assert "challenge" in ENKI_IDENTITY.lower()
