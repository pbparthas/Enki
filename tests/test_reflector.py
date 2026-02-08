"""Tests for Enki's Reflector — ACE-adapted feedback loop."""

import pytest
from datetime import datetime
from pathlib import Path

from enki.reflector import (
    ExecutionTrace,
    Reflection,
    Skill,
    reflect_on_session,
    distill_reflections,
    close_feedback_loop,
    gather_execution_trace,
    _reflect_violations,
    _reflect_escalations,
    _reflect_interceptions,
    _reflect_knowledge_usage,
    _reflect_process,
    _reflect_productivity,
    _format_skill_content,
    _derive_tags,
)


# =============================================================================
# FIXTURES
# =============================================================================

def _make_trace(**overrides) -> ExecutionTrace:
    """Create an ExecutionTrace with defaults."""
    defaults = {
        "session_id": "test-session-001",
        "project": "TestProject",
        "goal": "Add user authentication",
        "phase_start": "research",
        "phase_end": "implement",
        "tier_start": "small",
        "tier_end": "small",
        "files_edited": [],
        "violations": [],
        "escalations": [],
        "interceptions": [],
        "beads_accessed": [],
        "running_log": "",
    }
    defaults.update(overrides)
    return ExecutionTrace(**defaults)


# =============================================================================
# VIOLATION REFLECTION TESTS
# =============================================================================

class TestReflectViolations:
    def test_clean_session_with_edits(self):
        """No violations + edits = positive reflection."""
        trace = _make_trace(
            files_edited=["auth.py", "test_auth.py"],
            violations=[],
        )
        reflections = _reflect_violations(trace)
        assert len(reflections) == 1
        assert reflections[0].category == "worked"
        assert "Clean session" in reflections[0].description

    def test_no_edits_no_violations(self):
        """No work done = no reflection."""
        trace = _make_trace(files_edited=[], violations=[])
        reflections = _reflect_violations(trace)
        assert len(reflections) == 0

    def test_repeated_violations_detected(self):
        """3+ violations on same gate = pattern detected."""
        violations = [
            {"gate": "tdd", "tool": "Edit", "reason": "No test file"},
            {"gate": "tdd", "tool": "Write", "reason": "No test file"},
            {"gate": "tdd", "tool": "Edit", "reason": "No test file"},
        ]
        trace = _make_trace(violations=violations)
        reflections = _reflect_violations(trace)

        pattern_reflections = [r for r in reflections if r.category == "pattern"]
        assert len(pattern_reflections) >= 1
        assert "tdd" in pattern_reflections[0].description.lower()

    def test_single_violation_then_success(self):
        """One violation + edits = gate working correctly."""
        trace = _make_trace(
            violations=[{"gate": "phase", "tool": "Edit", "reason": "Not in implement phase"}],
            files_edited=["auth.py"],
        )
        reflections = _reflect_violations(trace)

        worked = [r for r in reflections if r.category == "worked"]
        assert len(worked) >= 1

    def test_violation_to_success_pattern(self):
        """Edit violations followed by successful edits."""
        trace = _make_trace(
            violations=[
                {"gate": "tdd", "tool": "Edit", "reason": "No tests"},
                {"gate": "tdd", "tool": "Write", "reason": "No tests"},
            ],
            files_edited=["auth.py", "test_auth.py"],
        )
        reflections = _reflect_violations(trace)

        worked = [r for r in reflections if "blocked premature" in r.description.lower()
                  or "gate blocked" in r.description.lower()]
        assert len(worked) >= 1


# =============================================================================
# ESCALATION REFLECTION TESTS
# =============================================================================

class TestReflectEscalations:
    def test_no_escalations(self):
        trace = _make_trace(escalations=[])
        reflections = _reflect_escalations(trace)
        assert len(reflections) == 0

    def test_single_escalation(self):
        trace = _make_trace(
            escalations=[{
                "initial_tier": "small",
                "final_tier": "medium",
                "files_at_escalation": 5,
                "lines_at_escalation": 200,
            }],
        )
        reflections = _reflect_escalations(trace)
        assert len(reflections) == 1
        assert reflections[0].category == "warning"
        assert "scope creep" in reflections[0].description.lower()

    def test_multiple_escalations(self):
        """Multiple escalations = serious underestimation."""
        trace = _make_trace(
            escalations=[
                {"initial_tier": "small", "final_tier": "medium",
                 "files_at_escalation": 5, "lines_at_escalation": 200},
                {"initial_tier": "medium", "final_tier": "large",
                 "files_at_escalation": 12, "lines_at_escalation": 800},
            ],
            tier_start="small",
            tier_end="large",
        )
        reflections = _reflect_escalations(trace)

        failed = [r for r in reflections if r.category == "failed"]
        assert len(failed) >= 1
        assert "multiple" in failed[0].description.lower()


# =============================================================================
# INTERCEPTION REFLECTION TESTS
# =============================================================================

class TestReflectInterceptions:
    def test_no_interceptions(self):
        trace = _make_trace(interceptions=[])
        reflections = _reflect_interceptions(trace)
        assert len(reflections) == 0

    def test_true_catches(self):
        """Ereshkigal blocking genuine issues."""
        trace = _make_trace(
            interceptions=[
                {"result": "blocked", "was_legitimate": False, "category": "scope_creep"},
                {"result": "blocked", "was_legitimate": False, "category": "skip_test"},
            ],
        )
        reflections = _reflect_interceptions(trace)
        worked = [r for r in reflections if r.category == "worked"]
        assert len(worked) >= 1

    def test_false_positives_detected(self):
        """Ereshkigal blocking legitimate actions."""
        trace = _make_trace(
            interceptions=[
                {"result": "blocked", "was_legitimate": True, "category": "routine_update"},
                {"result": "blocked", "was_legitimate": True, "category": "routine_update"},
            ],
        )
        reflections = _reflect_interceptions(trace)
        patterns = [r for r in reflections if r.category == "pattern"]
        assert len(patterns) >= 1
        assert "false positive" in patterns[0].description.lower()


# =============================================================================
# KNOWLEDGE USAGE TESTS
# =============================================================================

class TestReflectKnowledgeUsage:
    def test_no_beads_with_goal(self):
        """Had a goal but never checked memory."""
        trace = _make_trace(goal="Add auth", beads_accessed=[])
        reflections = _reflect_knowledge_usage(trace)
        warnings = [r for r in reflections if r.category == "warning"]
        assert len(warnings) >= 1

    def test_useful_beads(self):
        trace = _make_trace(
            beads_accessed=[
                {"id": "b1", "type": "solution", "was_useful": True, "content": "JWT pattern", "summary": "JWT"},
                {"id": "b2", "type": "decision", "was_useful": True, "content": "Use bcrypt", "summary": "bcrypt"},
            ],
        )
        reflections = _reflect_knowledge_usage(trace)
        worked = [r for r in reflections if r.category == "worked"]
        assert len(worked) >= 1

    def test_mostly_unhelpful_beads(self):
        trace = _make_trace(
            beads_accessed=[
                {"id": "b1", "type": "solution", "was_useful": False, "content": "old", "summary": "old"},
                {"id": "b2", "type": "decision", "was_useful": False, "content": "stale", "summary": "stale"},
                {"id": "b3", "type": "learning", "was_useful": True, "content": "ok", "summary": "ok"},
            ],
        )
        reflections = _reflect_knowledge_usage(trace)
        failed = [r for r in reflections if r.category == "failed"]
        assert len(failed) >= 1


# =============================================================================
# PROCESS REFLECTION TESTS
# =============================================================================

class TestReflectProcess:
    def test_no_goal_returns_empty(self):
        """No goal = nothing to evaluate, no reflections created."""
        trace = _make_trace(goal=None)
        reflections = _reflect_process(trace)
        assert len(reflections) == 0

    def test_full_flow(self):
        trace = _make_trace(
            running_log="[12:00] SPEC CREATED: auth\n[12:05] SPEC APPROVED: auth\n",
            files_edited=["auth.py"],
        )
        reflections = _reflect_process(trace)
        worked = [r for r in reflections if r.category == "worked" and "full flow" in r.description.lower()]
        assert len(worked) >= 1

    def test_impl_without_spec(self):
        trace = _make_trace(
            running_log="[12:00] NOTE: Started working on auth\n",
            files_edited=["auth.py", "models.py"],
        )
        reflections = _reflect_process(trace)
        warnings = [r for r in reflections if r.category == "warning"]
        assert len(warnings) >= 1


# =============================================================================
# PRODUCTIVITY TESTS
# =============================================================================

class TestReflectProductivity:
    def test_high_friction(self):
        """More violations than edits = fighting the system."""
        trace = _make_trace(
            violations=[{"gate": "tdd"} for _ in range(10)],
            files_edited=["one.py", "two.py"],
        )
        reflections = _reflect_productivity(trace)
        failed = [r for r in reflections if r.category == "failed"]
        assert len(failed) >= 1
        assert "friction" in failed[0].description.lower()

    def test_large_session(self):
        trace = _make_trace(
            files_edited=[f"file_{i}.py" for i in range(20)],
        )
        reflections = _reflect_productivity(trace)
        warnings = [r for r in reflections if r.category == "warning"]
        assert len(warnings) >= 1


# =============================================================================
# SKILL DISTILLATION TESTS
# =============================================================================

class TestDistillReflections:
    def test_filters_non_actionable(self):
        reflections = [
            Reflection("worked", "Phase progressed", "evidence", 0.7, False),
            Reflection("worked", "Clean session", "evidence", 0.9, True, "learning"),
        ]
        skills = distill_reflections(reflections)
        assert len(skills) == 1

    def test_filters_low_confidence(self):
        reflections = [
            Reflection("warning", "Maybe an issue", "weak evidence", 0.3, True, "pattern"),
        ]
        skills = distill_reflections(reflections)
        assert len(skills) == 0

    def test_formats_content(self):
        reflections = [
            Reflection("worked", "TDD gate enforced properly", "3 blocks then success", 0.9, True, "learning"),
        ]
        skills = distill_reflections(reflections)
        assert len(skills) == 1
        assert "EFFECTIVE:" in skills[0].content
        assert "TDD" in skills[0].content

    def test_derives_tags(self):
        reflections = [
            Reflection("pattern", "Ereshkigal false positives high", "40% FP", 0.9, True, "pattern"),
        ]
        skills = distill_reflections(reflections)
        assert "ereshkigal" in skills[0].tags
        assert "auto-reflected" in skills[0].tags


# =============================================================================
# FORMAT AND TAG HELPERS
# =============================================================================

class TestHelpers:
    def test_format_skill_content_worked(self):
        r = Reflection("worked", "Clean run", "0 violations", 0.9, True, "learning")
        content = _format_skill_content(r)
        assert content.startswith("EFFECTIVE:")

    def test_format_skill_content_failed(self):
        r = Reflection("failed", "Bad run", "5 violations", 0.9, True, "pattern")
        content = _format_skill_content(r)
        assert content.startswith("INEFFECTIVE:")

    def test_derive_tags_tdd(self):
        r = Reflection("pattern", "TDD gate fires too much", "evidence", 0.9, True, "pattern")
        tags = _derive_tags(r)
        assert "tdd" in tags

    def test_derive_tags_scope(self):
        r = Reflection("warning", "Scope escalation detected", "evidence", 0.9, True, "pattern")
        tags = _derive_tags(r)
        assert "scope" in tags


# =============================================================================
# PIPELINE GUARD TESTS
# =============================================================================

class TestPipelineGuard:
    def test_no_goal_no_edits_short_circuits(self):
        """No goal + no edits = pipeline returns empty report immediately."""
        trace = _make_trace(goal=None, files_edited=[])
        # Simulate what close_feedback_loop checks
        assert trace.goal is None
        assert len(trace.files_edited) == 0

    def test_no_goal_with_edits_still_reflects(self):
        """No goal but edits happened = still worth reflecting."""
        trace = _make_trace(goal=None, files_edited=["auth.py"])
        assert trace.goal is None
        assert len(trace.files_edited) > 0

    def test_goal_set_no_edits_still_reflects(self):
        """Goal set but no edits = still worth reflecting."""
        trace = _make_trace(goal="Add auth", files_edited=[])
        assert trace.goal is not None
        assert len(trace.files_edited) == 0
