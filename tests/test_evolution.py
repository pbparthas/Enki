"""Tests for Evolution module."""

import pytest
from pathlib import Path
from datetime import datetime, timedelta

from enki.db import init_db, set_db_path, close_db, get_db
from enki.session import start_session
from enki.violations import log_violation
from enki.evolution import (
    SelfCorrection,
    GateAdjustment,
    init_evolution_log,
    load_evolution_state,
    save_evolution_state,
    get_evolution_path,
    analyze_violation_patterns,
    analyze_escalation_patterns,
    check_correction_triggers,
    create_self_correction,
    add_gate_adjustment,
    mark_correction_effective,
    run_weekly_self_review,
    is_review_due,
    get_last_review_date,
    explain_block,
    get_evolution_summary,
    get_self_awareness_response,
    TRIGGER_THRESHOLDS,
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


class TestSelfCorrection:
    """Tests for SelfCorrection dataclass."""

    def test_create_correction(self):
        """Test creating a self-correction."""
        correction = SelfCorrection(
            id="corr_001",
            date="2026-02-02",
            pattern_type="gate_bypass",
            description="TDD gate bypassed repeatedly",
            frequency=5,
            impact="Bugs found in production",
            correction="Added stricter TDD checks",
        )

        assert correction.id == "corr_001"
        assert correction.status == "proposed"  # P0-06: defaults to proposed
        assert correction.effective is None

    def test_correction_serialization(self):
        """Test to_dict and from_dict."""
        correction = SelfCorrection(
            id="corr_001",
            date="2026-02-02",
            pattern_type="gate_bypass",
            description="Test",
            frequency=3,
            impact="Impact",
            correction="Correction",
            effective=True,
            status="effective",
        )

        data = correction.to_dict()
        restored = SelfCorrection.from_dict(data)

        assert restored.id == correction.id
        assert restored.effective is True
        assert restored.status == "effective"


class TestGateAdjustment:
    """Tests for GateAdjustment dataclass."""

    def test_create_adjustment(self):
        """Test creating a gate adjustment."""
        adjustment = GateAdjustment(
            gate="tdd",
            adjustment_type="tighten",
            description="Require 80% coverage",
            reason="Shallow tests passing",
        )

        assert adjustment.gate == "tdd"
        assert adjustment.active is True


class TestEvolutionLog:
    """Tests for evolution log management."""

    def test_init_creates_file(self, temp_project):
        """Test init creates EVOLUTION.md."""
        init_evolution_log(temp_project)

        evolution_path = get_evolution_path(temp_project)
        assert evolution_path.exists()

    def test_load_empty_state(self, temp_project):
        """Test loading empty state."""
        init_evolution_log(temp_project)

        state = load_evolution_state(temp_project)

        assert state["corrections"] == []
        assert state["adjustments"] == []
        assert state["last_review"] is None

    def test_save_and_load_state(self, temp_project):
        """Test saving and loading state."""
        init_evolution_log(temp_project)

        state = {
            "corrections": [
                {"id": "corr_001", "date": "2026-02-02", "pattern_type": "test",
                 "description": "Test", "frequency": 1, "impact": "None",
                 "correction": "Fixed", "status": "active"}
            ],
            "adjustments": [],
            "last_review": "2026-02-02T12:00:00",
        }

        save_evolution_state(state, temp_project)
        loaded = load_evolution_state(temp_project)

        assert len(loaded["corrections"]) == 1
        assert loaded["last_review"] == "2026-02-02T12:00:00"


class TestViolationPatternAnalysis:
    """Tests for violation pattern analysis."""

    def test_returns_empty_when_no_violations(self, temp_project):
        """Test returns empty list when no violations."""
        patterns = analyze_violation_patterns(days=7, project_path=temp_project)
        assert patterns == []

    def test_finds_violation_patterns(self, temp_project):
        """Test finds patterns in violations."""
        # Log some violations
        for i in range(3):
            log_violation(
                gate="tdd",
                tool="Edit",
                reason="No tests found",
                file_path=f"src/file{i}.py",
                project_path=temp_project,
            )

        patterns = analyze_violation_patterns(days=7, project_path=temp_project)

        # Should find the tdd pattern
        tdd_pattern = next((p for p in patterns if p["gate"] == "tdd"), None)
        assert tdd_pattern is not None
        assert tdd_pattern["total"] >= 3


class TestEscalationPatternAnalysis:
    """Tests for escalation pattern analysis."""

    def test_returns_empty_when_no_escalations(self, temp_project):
        """Test returns empty list when no escalations."""
        patterns = analyze_escalation_patterns(days=30, project_path=temp_project)
        assert patterns == []


class TestCorrectionTriggers:
    """Tests for correction triggers."""

    def test_returns_empty_when_no_issues(self, temp_project):
        """Test returns empty when no trigger conditions met."""
        triggers = check_correction_triggers(temp_project)
        # No violations logged, so no triggers should fire
        assert triggers == []

    def test_triggers_on_repeated_violations(self, temp_project):
        """Test triggers on repeated violations."""
        # Log many violations
        for i in range(TRIGGER_THRESHOLDS["same_violation_count"] + 1):
            log_violation(
                gate="phase",
                tool="Edit",
                reason="Wrong phase",
                file_path=f"src/file{i}.py",
                project_path=temp_project,
            )

        triggers = check_correction_triggers(temp_project)

        # Should find a trigger for repeated violations
        repeated = [t for t in triggers if t["trigger"] == "repeated_violations"]
        assert len(repeated) >= 1


class TestCreateSelfCorrection:
    """Tests for creating self-corrections."""

    def test_creates_correction(self, temp_project):
        """Test creates and saves a correction."""
        init_evolution_log(temp_project)

        correction = create_self_correction(
            pattern_type="gate_bypass",
            description="TDD violations",
            frequency=5,
            impact="Bugs in production",
            correction="Stricter TDD checks",
            project_path=temp_project,
        )

        assert correction.id.startswith("corr_")
        assert correction.status == "proposed"  # P0-06: defaults to proposed

        # Should be in state
        state = load_evolution_state(temp_project)
        assert len(state["corrections"]) == 1
        assert state["corrections"][0]["status"] == "proposed"


class TestAddGateAdjustment:
    """Tests for adding gate adjustments."""

    def test_adds_adjustment(self, temp_project):
        """Test adds and saves an adjustment."""
        init_evolution_log(temp_project)

        adjustment = add_gate_adjustment(
            gate="tdd",
            adjustment_type="tighten",
            description="Require 80% coverage",
            reason="Shallow tests passing",
            project_path=temp_project,
        )

        assert adjustment.gate == "tdd"

        # Should be in state
        state = load_evolution_state(temp_project)
        assert len(state["adjustments"]) == 1


class TestImmutableGateFloor:
    """Tests for P0-07: immutable gates cannot be loosened."""

    def test_loosen_phase_blocked(self, temp_project):
        """Cannot loosen the phase gate."""
        init_evolution_log(temp_project)
        with pytest.raises(ValueError, match="Cannot loosen immutable gate 'phase'"):
            add_gate_adjustment(
                gate="phase", adjustment_type="loosen",
                description="Skip phase", reason="Testing",
                project_path=temp_project,
            )

    def test_loosen_spec_blocked(self, temp_project):
        """Cannot loosen the spec gate."""
        init_evolution_log(temp_project)
        with pytest.raises(ValueError, match="Cannot loosen immutable gate 'spec'"):
            add_gate_adjustment(
                gate="spec", adjustment_type="loosen",
                description="Skip spec", reason="Testing",
                project_path=temp_project,
            )

    def test_loosen_scope_blocked(self, temp_project):
        """Cannot loosen the scope gate."""
        init_evolution_log(temp_project)
        with pytest.raises(ValueError, match="Cannot loosen immutable gate 'scope'"):
            add_gate_adjustment(
                gate="scope", adjustment_type="loosen",
                description="Skip scope", reason="Testing",
                project_path=temp_project,
            )

    def test_loosen_enforcement_integrity_blocked(self, temp_project):
        """Cannot loosen enforcement_integrity gate."""
        init_evolution_log(temp_project)
        with pytest.raises(ValueError, match="Cannot loosen immutable gate"):
            add_gate_adjustment(
                gate="enforcement_integrity", adjustment_type="loosen",
                description="Bypass enforcement", reason="Testing",
                project_path=temp_project,
            )

    def test_tighten_immutable_allowed(self, temp_project):
        """Tightening immutable gates is allowed."""
        init_evolution_log(temp_project)
        adj = add_gate_adjustment(
            gate="phase", adjustment_type="tighten",
            description="Stricter phase checks", reason="More rigor",
            project_path=temp_project,
        )
        assert adj.gate == "phase"
        assert adj.adjustment_type == "tighten"

    def test_loosen_non_immutable_allowed(self, temp_project):
        """Loosening non-immutable gates is allowed."""
        init_evolution_log(temp_project)
        adj = add_gate_adjustment(
            gate="tdd", adjustment_type="loosen",
            description="Allow integration tests only", reason="Testing",
            project_path=temp_project,
        )
        assert adj.gate == "tdd"
        assert adj.adjustment_type == "loosen"

    def test_no_state_change_on_blocked(self, temp_project):
        """Blocked loosen does NOT modify evolution state."""
        init_evolution_log(temp_project)
        state_before = load_evolution_state(temp_project)

        with pytest.raises(ValueError):
            add_gate_adjustment(
                gate="phase", adjustment_type="loosen",
                description="Skip phase", reason="Testing",
                project_path=temp_project,
            )

        state_after = load_evolution_state(temp_project)
        assert len(state_after["adjustments"]) == len(state_before["adjustments"])


class TestMarkCorrectionEffective:
    """Tests for marking corrections effective."""

    def test_marks_effective(self, temp_project):
        """Test marks a correction as effective."""
        init_evolution_log(temp_project)

        correction = create_self_correction(
            pattern_type="test",
            description="Test",
            frequency=1,
            impact="Test",
            correction="Test",
            project_path=temp_project,
        )

        mark_correction_effective(correction.id, True, temp_project)

        state = load_evolution_state(temp_project)
        saved_correction = next(
            (c for c in state["corrections"] if c["id"] == correction.id),
            None
        )

        assert saved_correction is not None
        assert saved_correction["effective"] is True
        assert saved_correction["status"] == "effective"


class TestWeeklySelfReview:
    """Tests for weekly self-review."""

    def test_runs_review(self, temp_project):
        """Test runs self-review and returns report."""
        init_evolution_log(temp_project)

        report = run_weekly_self_review(temp_project)

        assert "date" in report
        assert "violation_patterns" in report
        assert "escalation_patterns" in report
        assert "triggers" in report

    def test_updates_last_review(self, temp_project):
        """Test updates last review timestamp."""
        init_evolution_log(temp_project)

        run_weekly_self_review(temp_project)

        state = load_evolution_state(temp_project)
        assert state["last_review"] is not None


class TestReviewDue:
    """Tests for review due checks."""

    def test_due_when_never_reviewed(self, temp_project):
        """Test review is due when never reviewed."""
        init_evolution_log(temp_project)

        assert is_review_due(temp_project) is True

    def test_not_due_after_review(self, temp_project):
        """Test review not due after recent review."""
        init_evolution_log(temp_project)
        run_weekly_self_review(temp_project)

        assert is_review_due(temp_project) is False

    def test_get_last_review_date(self, temp_project):
        """Test getting last review date."""
        init_evolution_log(temp_project)

        # Before review
        assert get_last_review_date(temp_project) is None

        # After review
        run_weekly_self_review(temp_project)
        assert get_last_review_date(temp_project) is not None


class TestExplainBlock:
    """Tests for explain_block."""

    def test_returns_explanation(self, temp_project):
        """Test returns an explanation string."""
        init_evolution_log(temp_project)

        explanation = explain_block("tdd", "No tests found", temp_project)

        assert isinstance(explanation, str)
        assert "tdd" in explanation.lower()

    def test_includes_correction_info(self, temp_project):
        """Test includes self-correction info when available."""
        init_evolution_log(temp_project)

        # Create a correction for TDD
        create_self_correction(
            pattern_type="test",
            description="TDD gate bypass detected",
            frequency=3,
            impact="Bugs",
            correction="Tightened TDD gate",
            project_path=temp_project,
        )

        explanation = explain_block("tdd", "No tests", temp_project)

        # Should mention the correction
        assert "TDD" in explanation or "tdd" in explanation.lower()


class TestEvolutionSummary:
    """Tests for evolution summary."""

    def test_returns_summary(self, temp_project):
        """Test returns a summary string."""
        init_evolution_log(temp_project)

        summary = get_evolution_summary(temp_project)

        assert isinstance(summary, str)
        assert "Evolution Summary" in summary

    def test_shows_corrections(self, temp_project):
        """Test shows active corrections."""
        init_evolution_log(temp_project)

        create_self_correction(
            pattern_type="test",
            description="Test correction",
            frequency=1,
            impact="Test",
            correction="Test",
            project_path=temp_project,
        )

        summary = get_evolution_summary(temp_project)

        assert "Active Corrections" in summary or "1" in summary


class TestSelfAwarenessResponse:
    """Tests for self-awareness responses."""

    def test_responds_to_block_question(self, temp_project):
        """Test responds to 'why did you block' questions."""
        init_evolution_log(temp_project)

        response = get_self_awareness_response("Why did you block that?", temp_project)

        assert isinstance(response, str)
        assert len(response) > 0

    def test_responds_to_strict_question(self, temp_project):
        """Test responds to 'you seem stricter' observations."""
        init_evolution_log(temp_project)

        response = get_self_awareness_response("You seem stricter lately", temp_project)

        assert isinstance(response, str)
        assert "strict" in response.lower() or "correction" in response.lower() or "gates" in response.lower()

    def test_responds_to_loosen_request(self, temp_project):
        """Test responds to 'can you loosen' requests."""
        init_evolution_log(temp_project)

        response = get_self_awareness_response("Can you loosen the TDD gate?", temp_project)

        assert isinstance(response, str)
        assert "data" in response.lower() or "show" in response.lower()
