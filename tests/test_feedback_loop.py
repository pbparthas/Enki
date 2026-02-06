"""Tests for Enki's Feedback Loop — enforcement feedback system."""

import json
import pytest
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from enki.feedback_loop import (
    FEEDBACK_THRESHOLDS,
    NEVER_LOOSEN,
    MAX_PROPOSALS_PER_CYCLE,
    analyze_pattern_fp_rates,
    analyze_evasion_patterns,
    generate_proposals,
    store_proposal,
    apply_proposal,
    reject_proposal,
    revert_proposal,
    acknowledge_regression,
    check_for_regressions,
    run_feedback_cycle,
    get_feedback_summary,
    get_session_start_alerts,
    cleanup_old_proposals,
    _is_stop_ngram,
)
from enki.db import init_db, get_db, set_db_path


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def test_db(tmp_path):
    """Create a fresh test database."""
    db_path = tmp_path / "test_wisdom.db"
    set_db_path(db_path)
    init_db(db_path)
    yield get_db(db_path)
    set_db_path(None)


def _insert_interception(db, **kwargs):
    """Insert a test interception record.

    Pass timestamp= to set a specific timestamp (e.g. for P2-14 24h cooldown tests).
    Default: 2 days ago (so was_legitimate counts are not filtered by the 24h cooldown).
    """
    defaults = {
        "id": f"int_{datetime.now().timestamp()}_{id(kwargs)}",
        "session_id": "test-session",
        "timestamp": (datetime.now() - timedelta(days=2)).isoformat(),
        "tool": "Edit",
        "reasoning": "test reasoning",
        "category": "test_category",
        "pattern": "test_pattern",
        "result": "blocked",
        "was_legitimate": 0,
    }
    defaults.update(kwargs)
    db.execute("""
        INSERT INTO interceptions (id, session_id, timestamp, tool, reasoning, category, pattern, result, was_legitimate)
        VALUES (:id, :session_id, :timestamp, :tool, :reasoning, :category, :pattern, :result, :was_legitimate)
    """, defaults)
    db.commit()


def _insert_violation(db, **kwargs):
    """Insert a test violation record."""
    defaults = {
        "id": f"viol_{datetime.now().timestamp()}_{id(kwargs)}",
        "session_id": "test-session",
        "gate": "tdd",
        "tool": "Edit",
        "file_path": "test.py",
        "reason": "test violation",
    }
    defaults.update(kwargs)
    db.execute("""
        INSERT INTO violations (id, session_id, gate, tool, file_path, reason)
        VALUES (:id, :session_id, :gate, :tool, :file_path, :reason)
    """, defaults)
    db.commit()


def _insert_proposal(db, **kwargs):
    """Insert a test feedback proposal."""
    defaults = {
        "id": f"fp_{datetime.now().timestamp()}_{id(kwargs)}",
        "session_id": "test-session",
        "proposal_type": "pattern_remove",
        "target": "minimize_patterns",
        "description": "Test proposal",
        "reason": "Test reason",
        "status": "pending",
    }
    defaults.update(kwargs)
    db.execute("""
        INSERT INTO feedback_proposals
        (id, session_id, proposal_type, target, description, reason, old_value, new_value,
         evidence_json, status, applied_at, pre_apply_snapshot, post_apply_snapshot, sessions_since_apply)
        VALUES (:id, :session_id, :proposal_type, :target, :description, :reason,
                :old_value, :new_value, :evidence_json, :status, :applied_at,
                :pre_apply_snapshot, :post_apply_snapshot, :sessions_since_apply)
    """, {
        "old_value": kwargs.get("old_value"),
        "new_value": kwargs.get("new_value"),
        "evidence_json": kwargs.get("evidence_json"),
        "applied_at": kwargs.get("applied_at"),
        "pre_apply_snapshot": kwargs.get("pre_apply_snapshot"),
        "post_apply_snapshot": kwargs.get("post_apply_snapshot"),
        "sessions_since_apply": kwargs.get("sessions_since_apply", 0),
        **defaults,
    })
    db.commit()


# =============================================================================
# FP RATE ANALYSIS TESTS
# =============================================================================

class TestAnalyzePatternFPRates:
    def test_no_interceptions(self, test_db):
        """No data = empty results."""
        results = analyze_pattern_fp_rates()
        assert results == []

    def test_below_minimum_evaluations(self, test_db):
        """Too few blocks = not enough signal."""
        # Insert fewer than min_evaluations_to_loosen
        for i in range(3):
            _insert_interception(test_db, id=f"int_{i}", pattern="weak_pattern")
        results = analyze_pattern_fp_rates()
        assert results == []

    def test_high_fp_rate_detected(self, test_db):
        """Pattern with 40%+ FP rate is flagged."""
        # 5 blocks, 3 legitimate = 60% FP
        for i in range(5):
            _insert_interception(
                test_db,
                id=f"int_{i}",
                pattern="aggressive_pattern",
                category="skip_patterns",
                was_legitimate=1 if i < 3 else 0,
            )
        results = analyze_pattern_fp_rates()
        assert len(results) >= 1
        assert results[0]["pattern"] == "aggressive_pattern"
        assert results[0]["fp_rate"] >= 0.40

    def test_low_fp_rate_not_flagged(self, test_db):
        """Pattern with <40% FP rate is not in high-FP results."""
        # 10 blocks, 1 FP = 10%
        for i in range(10):
            _insert_interception(
                test_db,
                id=f"int_{i}",
                pattern="good_pattern",
                category="skip_patterns",
                was_legitimate=1 if i == 0 else 0,
            )
        results = analyze_pattern_fp_rates()
        # Should still return data, but fp_rate < 0.40
        if results:
            assert results[0]["fp_rate"] < 0.40


# =============================================================================
# EVASION PATTERN ANALYSIS TESTS
# =============================================================================

class TestAnalyzeEvasionPatterns:
    def test_too_few_evasions(self, test_db):
        """Fewer than 2 evasions = not enough signal."""
        with patch("enki.feedback_loop.find_evasions_with_bugs", return_value=[
            {"reasoning": "just a quick change"}
        ]):
            results = analyze_evasion_patterns()
            assert results == []

    def test_common_phrases_extracted(self, test_db):
        """Common n-grams across 2+ evasions are extracted."""
        evasions = [
            {"reasoning": "this is just a trivial change that doesn't need review"},
            {"reasoning": "it's just a trivial change nothing major"},
            {"reasoning": "making another trivial change here"},
        ]
        with patch("enki.feedback_loop.find_evasions_with_bugs", return_value=evasions):
            results = analyze_evasion_patterns()
            # "trivial change" should appear in results
            phrases = [r["phrase"] for r in results]
            assert any("trivial change" in p for p in phrases)

    def test_stop_ngrams_filtered(self):
        """Common/boring n-grams are filtered out."""
        assert _is_stop_ngram("the is") is True
        assert _is_stop_ngram("it is") is True
        assert _is_stop_ngram("trivial change") is False

    def test_returns_example_reasonings(self, test_db):
        """Results include example reasoning text."""
        evasions = [
            {"reasoning": "just a quick fix nothing important"},
            {"reasoning": "another quick fix for the bug"},
        ]
        with patch("enki.feedback_loop.find_evasions_with_bugs", return_value=evasions):
            results = analyze_evasion_patterns()
            for result in results:
                assert "example_reasonings" in result
                assert isinstance(result["example_reasonings"], list)


# =============================================================================
# PROPOSAL GENERATION TESTS
# =============================================================================

class TestGenerateProposals:
    def test_no_data_no_proposals(self, test_db):
        """Clean system = no proposals."""
        proposals = generate_proposals()
        assert proposals == []

    def test_max_one_proposal_per_cycle(self, test_db):
        """Never generates more than MAX_PROPOSALS_PER_CYCLE."""
        # Insert enough data for multiple proposals
        for i in range(10):
            _insert_interception(
                test_db,
                id=f"int_a_{i}",
                pattern="pattern_a",
                category="skip_patterns",
                was_legitimate=1,
            )
        for i in range(10):
            _insert_interception(
                test_db,
                id=f"int_b_{i}",
                pattern="pattern_b",
                category="minimize_patterns",
                was_legitimate=1,
            )
        proposals = generate_proposals()
        assert len(proposals) <= MAX_PROPOSALS_PER_CYCLE

    def test_fp_rate_generates_pattern_refine(self, test_db):
        """High FP rate generates a pattern_refine proposal (not remove)."""
        for i in range(6):
            _insert_interception(
                test_db,
                id=f"int_{i}",
                pattern="bad_pattern",
                category="skip_patterns",
                was_legitimate=1 if i < 4 else 0,  # 67% FP
            )
        proposals = generate_proposals()
        assert len(proposals) == 1
        assert proposals[0]["proposal_type"] == "pattern_refine"


# =============================================================================
# APPLY PROPOSAL TESTS
# =============================================================================

class TestApplyProposal:
    def test_apply_pattern_add(self, test_db):
        """Applying pattern_add calls add_pattern."""
        _insert_proposal(
            test_db,
            id="fp_test_add",
            proposal_type="pattern_add",
            target="minimize_patterns",
            new_value="suspicious phrase",
        )

        with patch("enki.feedback_loop.add_pattern") as mock_add, \
             patch("enki.feedback_loop.load_patterns", return_value={"minimize_patterns": []}):
            result = apply_proposal("fp_test_add")
            assert result["status"] == "applied"
            mock_add.assert_called_once_with("suspicious phrase", "minimize_patterns")

    def test_apply_pattern_remove(self, test_db):
        """Applying pattern_remove calls remove_pattern."""
        _insert_proposal(
            test_db,
            id="fp_test_remove",
            proposal_type="pattern_remove",
            target="skip_patterns",
            old_value="noisy_pattern",
        )

        with patch("enki.feedback_loop.remove_pattern") as mock_remove, \
             patch("enki.feedback_loop.load_patterns", return_value={"skip_patterns": ["noisy_pattern"]}):
            result = apply_proposal("fp_test_remove")
            assert result["status"] == "applied"
            mock_remove.assert_called_once_with("noisy_pattern", "skip_patterns")

    def test_apply_gate_tighten(self, test_db):
        """Applying gate_tighten calls add_gate_adjustment."""
        _insert_proposal(
            test_db,
            id="fp_test_gate",
            proposal_type="gate_tighten",
            target="tdd",
            description="Tighten TDD gate",
            reason="Too many violations",
        )

        with patch("enki.feedback_loop.add_gate_adjustment") as mock_adj, \
             patch("enki.feedback_loop.load_patterns", return_value={}):
            result = apply_proposal("fp_test_gate")
            assert result["status"] == "applied"
            mock_adj.assert_called_once()

    def test_cannot_apply_non_pending(self, test_db):
        """Cannot apply a proposal that isn't pending."""
        _insert_proposal(test_db, id="fp_applied", status="applied")
        result = apply_proposal("fp_applied")
        assert "error" in result

    def test_not_found(self, test_db):
        """Returns error for missing proposal."""
        result = apply_proposal("nonexistent")
        assert "error" in result


# =============================================================================
# REGRESSION DETECTION TESTS
# =============================================================================

class TestRegressionDetection:
    def test_short_circuit_no_applied(self, test_db):
        """Returns [] immediately when no applied proposals exist."""
        result = check_for_regressions()
        assert result == []

    def test_short_circuit_not_enough_sessions(self, test_db):
        """Applied proposals with too few sessions don't trigger check."""
        _insert_proposal(
            test_db,
            id="fp_recent",
            status="applied",
            applied_at=datetime.now().isoformat(),
            sessions_since_apply=2,  # Below threshold of 5
            pre_apply_snapshot=json.dumps({"violation_count": 0}),
        )
        result = check_for_regressions()
        assert result == []

    def test_minimum_absolute_violations(self, test_db):
        """2x increase but < 5 absolute violations = not a regression."""
        applied_at = (datetime.now() - timedelta(days=7)).isoformat()
        _insert_proposal(
            test_db,
            id="fp_low",
            status="applied",
            applied_at=applied_at,
            sessions_since_apply=6,
            pre_apply_snapshot=json.dumps({"violation_count": 1}),
        )
        # Add only 2 violations after apply (2x increase but too few)
        for i in range(2):
            _insert_violation(test_db, id=f"viol_post_{i}")

        result = check_for_regressions()
        # Should NOT flag as regression because < 5 absolute violations
        regressions_for_fp_low = [r for r in result if r.get("proposal_id") == "fp_low"]
        assert len(regressions_for_fp_low) == 0

    def test_regression_detected_with_sufficient_violations(self, test_db):
        """2x+ increase AND 5+ violations = regression flagged."""
        applied_at = (datetime.now() - timedelta(days=7)).isoformat()
        _insert_proposal(
            test_db,
            id="fp_regressed",
            status="applied",
            applied_at=applied_at,
            sessions_since_apply=6,
            pre_apply_snapshot=json.dumps({"violation_count": 3}),
        )
        # Add 7 violations after apply (2.3x increase, > 5 absolute)
        for i in range(7):
            _insert_violation(test_db, id=f"viol_post_{i}")

        result = check_for_regressions()
        regressions_for_fp = [r for r in result if r.get("proposal_id") == "fp_regressed"]
        assert len(regressions_for_fp) == 1
        assert regressions_for_fp[0]["post_violations"] >= 5


# =============================================================================
# REVERT PROPOSAL TESTS
# =============================================================================

class TestRevertProposal:
    def test_revert_pattern_add(self, test_db):
        """Reverting pattern_add removes the pattern."""
        _insert_proposal(
            test_db,
            id="fp_revert_add",
            proposal_type="pattern_add",
            target="minimize_patterns",
            new_value="bad phrase",
            status="applied",
        )

        with patch("enki.feedback_loop.remove_pattern") as mock_remove, \
             patch("enki.feedback_loop.create_self_correction"):
            result = revert_proposal("fp_revert_add")
            assert result["status"] == "reverted"
            mock_remove.assert_called_once_with("bad phrase", "minimize_patterns")

    def test_revert_pattern_remove(self, test_db):
        """Reverting pattern_remove re-adds the pattern."""
        _insert_proposal(
            test_db,
            id="fp_revert_remove",
            proposal_type="pattern_remove",
            target="skip_patterns",
            old_value="good_pattern",
            status="regressed",
        )

        with patch("enki.feedback_loop.add_pattern") as mock_add, \
             patch("enki.feedback_loop.create_self_correction"):
            result = revert_proposal("fp_revert_remove")
            assert result["status"] == "reverted"
            assert result["previous_status"] == "regressed"
            mock_add.assert_called_once_with("good_pattern", "skip_patterns")

    def test_cannot_revert_pending(self, test_db):
        """Cannot revert a proposal that isn't applied/regressed."""
        _insert_proposal(test_db, id="fp_pending", status="pending")
        result = revert_proposal("fp_pending")
        assert "error" in result

    def test_revert_logs_self_correction(self, test_db):
        """Reverting creates a self-correction record."""
        _insert_proposal(
            test_db,
            id="fp_revert_log",
            proposal_type="pattern_add",
            target="minimize_patterns",
            new_value="phrase",
            status="applied",
            description="Test revert",
        )

        with patch("enki.feedback_loop.remove_pattern"), \
             patch("enki.feedback_loop.create_self_correction") as mock_correction:
            revert_proposal("fp_revert_log")
            mock_correction.assert_called_once()
            call_kwargs = mock_correction.call_args
            assert "feedback_revert" in call_kwargs[1]["pattern_type"] or call_kwargs[0][0] == "feedback_revert"


# =============================================================================
# FULL CYCLE TESTS
# =============================================================================

class TestRunFeedbackCycle:
    def test_clean_system(self, test_db):
        """Clean system returns stable status."""
        report = run_feedback_cycle()
        assert report["status"] == "stable"
        assert report["proposals_generated"] == 0

    def test_proposals_stored_not_applied(self, test_db):
        """Generated proposals are stored with pending status, never auto-applied."""
        # Create high FP pattern
        for i in range(6):
            _insert_interception(
                test_db,
                id=f"int_{i}",
                pattern="loud_pattern",
                category="skip_patterns",
                was_legitimate=1 if i < 4 else 0,
            )

        report = run_feedback_cycle()
        assert report["proposals_generated"] >= 1

        # Verify stored as pending, not applied
        for pid in report["proposals_stored"]:
            row = test_db.execute(
                "SELECT status FROM feedback_proposals WHERE id = ?", (pid,)
            ).fetchone()
            assert row["status"] == "pending"


# =============================================================================
# NEVER LOOSEN TESTS
# =============================================================================

class TestNeverLoosen:
    def test_certainty_patterns_never_loosened(self, test_db):
        """certainty_patterns are never proposed for removal."""
        for i in range(10):
            _insert_interception(
                test_db,
                id=f"int_{i}",
                pattern="certainty_rule",
                category="certainty_patterns",
                was_legitimate=1,  # 100% FP
            )
        proposals = generate_proposals()
        # Should not propose any changes to certainty_patterns
        certainty_proposals = [p for p in proposals
                              if p["target"] == "certainty_patterns"]
        assert len(certainty_proposals) == 0

    def test_min_pattern_floor_blocks_removal(self, test_db):
        """Cannot propose removal when category is at minimum pattern floor."""
        # Category with exactly MIN_PATTERNS_PER_CATEGORY patterns
        from enki.feedback_loop import MIN_PATTERNS_PER_CATEGORY

        for i in range(6):
            _insert_interception(
                test_db,
                id=f"floor_{i}",
                pattern="floor_pattern",
                category="skip_patterns",
                was_legitimate=1 if i < 4 else 0,  # 67% FP
            )

        # Mock load_patterns to return exactly MIN_PATTERNS_PER_CATEGORY patterns
        with patch("enki.feedback_loop.load_patterns", return_value={
            "skip_patterns": [f"pat_{i}" for i in range(MIN_PATTERNS_PER_CATEGORY)],
        }):
            proposals = generate_proposals()

        # Should propose refine, never remove
        assert len(proposals) >= 1
        assert all(p["proposal_type"] == "pattern_refine" for p in proposals)
        # Evidence should include the floor info
        assert proposals[0]["evidence"].get("category_count") == MIN_PATTERNS_PER_CATEGORY


# =============================================================================
# STATUS & ALERTS TESTS
# =============================================================================

class TestFeedbackSummary:
    def test_empty_summary(self, test_db):
        """No proposals = stable message."""
        summary = get_feedback_summary()
        assert "stable" in summary.lower() or "no proposals" in summary.lower()

    def test_pending_shown(self, test_db):
        """Pending proposals appear in summary."""
        _insert_proposal(test_db, id="fp_pending_1", status="pending", description="Test pending")
        summary = get_feedback_summary()
        assert "pending" in summary.lower()
        assert "fp_pending_1" in summary


class TestSessionStartAlerts:
    def test_no_alerts_when_clean(self, test_db):
        """No pending/regressed = no alerts."""
        alerts = get_session_start_alerts()
        assert alerts is None

    def test_alerts_with_pending(self, test_db):
        """Pending proposals generate alerts."""
        _insert_proposal(test_db, id="fp_alert", status="pending")
        alerts = get_session_start_alerts()
        assert alerts is not None
        assert "pending" in alerts.lower()

    def test_alerts_with_regressed(self, test_db):
        """Regressed proposals generate alerts."""
        _insert_proposal(test_db, id="fp_regressed", status="regressed")
        alerts = get_session_start_alerts()
        assert alerts is not None
        assert "regression" in alerts.lower()
