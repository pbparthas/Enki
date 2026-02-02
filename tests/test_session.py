"""Tests for session module."""

import pytest
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import (
    start_session, get_session, get_phase, set_phase,
    get_tier, set_tier, get_goal, set_goal,
    add_session_edit, get_session_edits, tier_escalated,
    ensure_project_enki_dir,
)


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory with enki DB."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    yield tmp_path
    close_db()
    set_db_path(None)


class TestStartSession:
    """Tests for start_session."""

    def test_creates_session_files(self, temp_project):
        """Test that start_session creates state files."""
        session = start_session(temp_project)

        enki_dir = temp_project / ".enki"
        assert (enki_dir / "SESSION_ID").exists()
        assert (enki_dir / "PHASE").exists()
        assert (enki_dir / "TIER").exists()

    def test_initializes_default_state(self, temp_project):
        """Test default session state."""
        session = start_session(temp_project)

        assert session.session_id is not None
        assert session.phase == "intake"
        assert session.tier == "trivial"
        assert session.edits == []

    def test_sets_goal_if_provided(self, temp_project):
        """Test that goal is set when provided."""
        session = start_session(temp_project, goal="Test goal")

        assert session.goal == "Test goal"
        assert (temp_project / ".enki" / "GOAL").read_text() == "Test goal"


class TestGetSession:
    """Tests for get_session."""

    def test_returns_none_if_no_session(self, temp_project):
        """Test returns None if no session exists."""
        # Don't start a session
        session = get_session(temp_project)
        # Should return None or session with no ID
        # Since we init_db, we might have dir but no SESSION_ID
        assert session is None or session.session_id is None

    def test_returns_session_state(self, temp_project):
        """Test returns current session state."""
        start_session(temp_project, goal="My goal")
        set_phase("implement", temp_project)

        session = get_session(temp_project)

        assert session is not None
        assert session.phase == "implement"
        assert session.goal == "My goal"


class TestPhase:
    """Tests for phase management."""

    def test_get_phase_default(self, temp_project):
        """Test default phase is intake."""
        ensure_project_enki_dir(temp_project)
        phase = get_phase(temp_project)
        assert phase == "intake"

    def test_set_and_get_phase(self, temp_project):
        """Test setting and getting phase."""
        ensure_project_enki_dir(temp_project)

        set_phase("implement", temp_project)
        assert get_phase(temp_project) == "implement"

        set_phase("review", temp_project)
        assert get_phase(temp_project) == "review"

    def test_invalid_phase_raises(self, temp_project):
        """Test that invalid phase raises ValueError."""
        ensure_project_enki_dir(temp_project)

        with pytest.raises(ValueError):
            set_phase("invalid_phase", temp_project)


class TestTier:
    """Tests for tier management."""

    def test_get_tier_default(self, temp_project):
        """Test default tier is trivial."""
        ensure_project_enki_dir(temp_project)
        tier = get_tier(temp_project)
        assert tier == "trivial"

    def test_set_and_get_tier(self, temp_project):
        """Test setting and getting tier."""
        ensure_project_enki_dir(temp_project)

        set_tier("feature", temp_project)
        assert get_tier(temp_project) == "feature"

    def test_invalid_tier_raises(self, temp_project):
        """Test that invalid tier raises ValueError."""
        ensure_project_enki_dir(temp_project)

        with pytest.raises(ValueError):
            set_tier("invalid_tier", temp_project)


class TestGoal:
    """Tests for goal management."""

    def test_get_goal_none_if_not_set(self, temp_project):
        """Test goal is None if not set."""
        ensure_project_enki_dir(temp_project)
        assert get_goal(temp_project) is None

    def test_set_and_get_goal(self, temp_project):
        """Test setting and getting goal."""
        ensure_project_enki_dir(temp_project)

        set_goal("Implement feature X", temp_project)
        assert get_goal(temp_project) == "Implement feature X"


class TestSessionEdits:
    """Tests for session edit tracking."""

    def test_empty_edits_by_default(self, temp_project):
        """Test no edits initially."""
        start_session(temp_project)
        assert get_session_edits(temp_project) == []

    def test_add_session_edit(self, temp_project):
        """Test adding edits."""
        start_session(temp_project)

        add_session_edit("src/main.py", temp_project)
        add_session_edit("src/utils.py", temp_project)

        edits = get_session_edits(temp_project)
        assert "src/main.py" in edits
        assert "src/utils.py" in edits

    def test_no_duplicate_edits(self, temp_project):
        """Test same file isn't added twice."""
        start_session(temp_project)

        add_session_edit("src/main.py", temp_project)
        add_session_edit("src/main.py", temp_project)

        edits = get_session_edits(temp_project)
        assert len(edits) == 1


class TestTierEscalation:
    """Tests for tier_escalated helper."""

    def test_escalation_detected(self):
        """Test escalation is detected."""
        assert tier_escalated("trivial", "quick_fix")
        assert tier_escalated("quick_fix", "feature")
        assert tier_escalated("feature", "major")

    def test_no_escalation(self):
        """Test no escalation for same or lower tier."""
        assert not tier_escalated("feature", "quick_fix")
        assert not tier_escalated("major", "trivial")
        assert not tier_escalated("feature", "feature")
