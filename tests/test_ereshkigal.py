"""Tests for Ereshkigal - The Pattern Interceptor."""

import pytest
import json
from pathlib import Path

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session
from enki.ereshkigal import (
    init_patterns,
    load_patterns,
    save_patterns,
    add_pattern,
    remove_pattern,
    get_pattern_categories,
    intercept,
    would_block,
    log_attempt,
    mark_false_positive,
    mark_legitimate,
    get_interception_stats,
    get_recent_interceptions,
    generate_weekly_report,
    DEFAULT_PATTERNS,
    InterceptionResult,
)


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


@pytest.fixture
def temp_patterns(tmp_path):
    """Create a temporary patterns file."""
    patterns_file = tmp_path / "patterns.json"
    yield patterns_file


class TestPatternManagement:
    """Tests for pattern file management."""

    def test_init_patterns_creates_file(self, temp_patterns):
        """Test init_patterns creates patterns.json."""
        init_patterns(temp_patterns)
        assert temp_patterns.exists()

    def test_init_patterns_has_default_patterns(self, temp_patterns):
        """Test init_patterns includes default patterns."""
        init_patterns(temp_patterns)
        patterns = load_patterns(temp_patterns)

        assert "skip_patterns" in patterns
        assert "minimize_patterns" in patterns
        assert "urgency_patterns" in patterns
        assert "certainty_patterns" in patterns

    def test_load_patterns_creates_if_not_exists(self, temp_patterns):
        """Test load_patterns creates file if missing."""
        assert not temp_patterns.exists()
        patterns = load_patterns(temp_patterns)
        assert temp_patterns.exists()
        assert "skip_patterns" in patterns

    def test_save_and_load_patterns(self, temp_patterns):
        """Test saving and loading patterns."""
        init_patterns(temp_patterns)

        patterns = load_patterns(temp_patterns)
        patterns["skip_patterns"].append("test_pattern")
        save_patterns(patterns, temp_patterns)

        loaded = load_patterns(temp_patterns)
        assert "test_pattern" in loaded["skip_patterns"]

    def test_add_pattern(self, temp_patterns):
        """Test adding a pattern."""
        init_patterns(temp_patterns)

        add_pattern(r"new pattern", "skip_patterns", temp_patterns)

        patterns = load_patterns(temp_patterns)
        assert r"new pattern" in patterns["skip_patterns"]

    def test_add_pattern_no_duplicates(self, temp_patterns):
        """Test adding duplicate pattern doesn't create duplicates."""
        init_patterns(temp_patterns)

        add_pattern(r"unique pattern", "skip_patterns", temp_patterns)
        add_pattern(r"unique pattern", "skip_patterns", temp_patterns)

        patterns = load_patterns(temp_patterns)
        count = patterns["skip_patterns"].count(r"unique pattern")
        assert count == 1

    def test_remove_pattern(self, temp_patterns):
        """Test removing a pattern."""
        init_patterns(temp_patterns)

        add_pattern(r"remove me", "skip_patterns", temp_patterns)
        result = remove_pattern(r"remove me", "skip_patterns", temp_patterns)

        assert result is True
        patterns = load_patterns(temp_patterns)
        assert r"remove me" not in patterns["skip_patterns"]

    def test_remove_nonexistent_pattern(self, temp_patterns):
        """Test removing a nonexistent pattern returns False."""
        init_patterns(temp_patterns)

        result = remove_pattern(r"not there", "skip_patterns", temp_patterns)
        assert result is False

    def test_get_pattern_categories(self, temp_patterns):
        """Test getting pattern categories."""
        init_patterns(temp_patterns)

        categories = get_pattern_categories(temp_patterns)

        assert "skip_patterns" in categories
        assert "minimize_patterns" in categories
        assert "version" not in categories
        assert "updated_at" not in categories


class TestPatternMatching:
    """Tests for pattern matching."""

    def test_test_pattern_matches_skip(self, temp_patterns):
        """Test that skip patterns are matched."""
        init_patterns(temp_patterns)

        result = would_block("This is a trivial change", temp_patterns)

        assert result is not None
        category, pattern = result
        assert category == "skip_patterns"
        assert "trivial" in pattern

    def test_test_pattern_matches_minimize(self, temp_patterns):
        """Test that minimize patterns are matched."""
        init_patterns(temp_patterns)

        result = would_block("This is simple enough to do", temp_patterns)

        assert result is not None
        category, pattern = result
        assert category == "minimize_patterns"

    def test_test_pattern_matches_urgency(self, temp_patterns):
        """Test that urgency patterns are matched."""
        init_patterns(temp_patterns)

        result = would_block("Just this once, I'll skip tests", temp_patterns)

        assert result is not None
        category, pattern = result
        assert category == "urgency_patterns"

    def test_test_pattern_matches_certainty(self, temp_patterns):
        """Test that certainty patterns are matched."""
        init_patterns(temp_patterns)

        result = would_block("This definitely works", temp_patterns)

        assert result is not None
        category, pattern = result
        assert category == "certainty_patterns"

    def test_test_pattern_no_match(self, temp_patterns):
        """Test that non-matching text returns None."""
        init_patterns(temp_patterns)

        result = would_block("Implementing user authentication with proper tests", temp_patterns)

        assert result is None

    def test_test_pattern_case_insensitive(self, temp_patterns):
        """Test that pattern matching is case insensitive."""
        init_patterns(temp_patterns)

        result = would_block("This is TRIVIAL", temp_patterns)
        assert result is not None

        result = would_block("QUICK FIX here", temp_patterns)
        assert result is not None


class TestInterception:
    """Tests for the intercept function."""

    def test_intercept_blocks_matching_reasoning(self, temp_project, temp_patterns):
        """Test intercept blocks when pattern matches."""
        init_patterns(temp_patterns)

        result = intercept(
            tool="Edit",
            reasoning="This is a trivial change, no need for tests",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        assert isinstance(result, InterceptionResult)
        assert result.allowed is False
        assert result.category == "skip_patterns"
        assert result.pattern is not None
        assert "BLOCKED" in result.message

    def test_intercept_allows_clean_reasoning(self, temp_project, temp_patterns):
        """Test intercept allows when no pattern matches."""
        init_patterns(temp_patterns)

        result = intercept(
            tool="Edit",
            reasoning="Implementing user authentication with comprehensive tests",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        assert result.allowed is True
        assert result.category is None
        assert result.pattern is None
        assert result.message is None

    def test_intercept_logs_blocked_attempts(self, temp_project, temp_patterns):
        """Test that blocked attempts are logged."""
        init_patterns(temp_patterns)

        result = intercept(
            tool="Edit",
            reasoning="Quick fix here",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        assert result.allowed is False
        assert result.interception_id is not None

        # Check it was logged
        interceptions = get_recent_interceptions(result="blocked", limit=1)
        assert len(interceptions) >= 1

    def test_intercept_logs_allowed_attempts(self, temp_project, temp_patterns):
        """Test that allowed attempts are logged."""
        init_patterns(temp_patterns)

        result = intercept(
            tool="Edit",
            reasoning="Proper implementation with tests",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        assert result.allowed is True
        assert result.interception_id is not None


class TestInterceptionLogging:
    """Tests for logging and marking interceptions."""

    def test_log_attempt(self, temp_project):
        """Test logging an attempt."""
        interception_id = log_attempt(
            tool="Write",
            reasoning="Test reasoning",
            result="allowed",
            session_id="test_session",
        )

        assert interception_id is not None
        assert len(interception_id) > 0

    def test_mark_false_positive(self, temp_project, temp_patterns):
        """Test marking an interception as false positive."""
        init_patterns(temp_patterns)

        # Create a blocked interception
        result = intercept(
            tool="Edit",
            reasoning="This is trivial",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        # Mark as false positive
        success = mark_false_positive(result.interception_id, "Actually legitimate")
        assert success is True

    def test_mark_legitimate(self, temp_project, temp_patterns):
        """Test marking an interception as legitimate block."""
        init_patterns(temp_patterns)

        # Create a blocked interception
        result = intercept(
            tool="Edit",
            reasoning="Just a quick fix",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        # Mark as legitimate
        success = mark_legitimate(result.interception_id, "Correct block")
        assert success is True


class TestInterceptionStats:
    """Tests for interception statistics."""

    def test_get_stats_empty(self, temp_project):
        """Test getting stats when no interceptions."""
        stats = get_interception_stats(days=7)

        assert stats["total"] == 0
        assert stats["blocked"] == 0
        assert stats["allowed"] == 0

    def test_get_stats_with_interceptions(self, temp_project, temp_patterns):
        """Test getting stats with interceptions."""
        init_patterns(temp_patterns)

        # Create some interceptions
        intercept(
            tool="Edit",
            reasoning="This is trivial",
            session_id="test_session",
            patterns_file=temp_patterns,
        )
        intercept(
            tool="Write",
            reasoning="Proper implementation",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        stats = get_interception_stats(days=7)

        assert stats["total"] >= 2
        assert stats["blocked"] >= 1
        assert stats["allowed"] >= 1

    def test_get_recent_interceptions(self, temp_project, temp_patterns):
        """Test getting recent interceptions."""
        init_patterns(temp_patterns)

        # Create an interception
        intercept(
            tool="Edit",
            reasoning="Quick fix needed",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        interceptions = get_recent_interceptions(limit=5)
        assert len(interceptions) >= 1

    def test_get_recent_interceptions_filtered(self, temp_project, temp_patterns):
        """Test filtering recent interceptions by result."""
        init_patterns(temp_patterns)

        # Create blocked and allowed interceptions
        intercept(
            tool="Edit",
            reasoning="Trivial change",
            session_id="test_session",
            patterns_file=temp_patterns,
        )
        intercept(
            tool="Write",
            reasoning="Proper work",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        blocked = get_recent_interceptions(result="blocked", limit=10)
        allowed = get_recent_interceptions(result="allowed", limit=10)

        assert all(i['result'] == 'blocked' for i in blocked)
        assert all(i['result'] == 'allowed' for i in allowed)


class TestWeeklyReport:
    """Tests for weekly report generation."""

    def test_generate_empty_report(self, temp_project):
        """Test generating report with no interceptions."""
        report = generate_weekly_report(days=7)

        assert isinstance(report, str)
        assert "Ereshkigal Weekly Report" in report
        assert "Total attempts: 0" in report

    def test_generate_report_with_data(self, temp_project, temp_patterns):
        """Test generating report with interceptions."""
        init_patterns(temp_patterns)

        # Create some interceptions
        for i in range(3):
            intercept(
                tool="Edit",
                reasoning=f"Trivial change {i}",
                session_id="test_session",
                patterns_file=temp_patterns,
            )
        intercept(
            tool="Write",
            reasoning="Proper work",
            session_id="test_session",
            patterns_file=temp_patterns,
        )

        report = generate_weekly_report(days=7)

        assert "Total attempts:" in report
        assert "Blocked:" in report
        assert "Allowed:" in report


class TestDefaultPatterns:
    """Tests for default pattern effectiveness."""

    def test_default_patterns_cover_skip_language(self, temp_patterns):
        """Test default patterns catch skip language."""
        init_patterns(temp_patterns)

        skip_phrases = [
            "This is trivial",
            "Just a quick fix",
            "Skip the tests",
            "No need for tests",
            "Small change here",
            "Minor update",
            "Straightforward fix",
        ]

        for phrase in skip_phrases:
            result = would_block(phrase, temp_patterns)
            assert result is not None, f"Should catch: {phrase}"

    def test_default_patterns_cover_minimizing_language(self, temp_patterns):
        """Test default patterns catch minimizing language."""
        init_patterns(temp_patterns)

        minimize_phrases = [
            "Simple enough to understand",
            "Obviously correct",
            "Easy fix here",
            "Won't take long",
            "Only a few lines",
            "Routine maintenance",
        ]

        for phrase in minimize_phrases:
            result = would_block(phrase, temp_patterns)
            assert result is not None, f"Should catch: {phrase}"

    def test_default_patterns_cover_urgency_language(self, temp_patterns):
        """Test default patterns catch urgency language."""
        init_patterns(temp_patterns)

        urgency_phrases = [
            "Just this once",
            "Emergency fix",
            "Need to ship this",
            "Deadline pressure",
            "Do it quickly",
            "ASAP please",
        ]

        for phrase in urgency_phrases:
            result = would_block(phrase, temp_patterns)
            assert result is not None, f"Should catch: {phrase}"

    def test_default_patterns_cover_certainty_language(self, temp_patterns):
        """Test default patterns catch overconfident language."""
        init_patterns(temp_patterns)

        certainty_phrases = [
            "This definitely works",
            "100% sure it's correct",
            "Guaranteed to work",
            "Can't fail",
        ]

        for phrase in certainty_phrases:
            result = would_block(phrase, temp_patterns)
            assert result is not None, f"Should catch: {phrase}"

    def test_legitimate_reasoning_not_blocked(self, temp_patterns):
        """Test that proper reasoning is not blocked."""
        init_patterns(temp_patterns)

        good_phrases = [
            "Implementing authentication module with comprehensive tests",
            "Adding rate limiting as specified in the PRD",
            "Refactoring the payment service following the approved spec",
            "Writing integration tests for the API endpoints",
            "Creating database migrations for the new schema",
        ]

        for phrase in good_phrases:
            result = would_block(phrase, temp_patterns)
            assert result is None, f"Should not catch: {phrase}"
