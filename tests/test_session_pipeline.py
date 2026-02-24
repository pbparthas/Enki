"""Tests for session_pipeline.py — three-loop session-end pipeline."""

import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from enki.session_pipeline import (
    run_reflector,
    run_feedback_cycle,
    run_regression_checks,
    handle_session_end,
    NEVER_LOOSEN_GATES,
    _create_reflector_candidate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dbs(tmp_path, monkeypatch):
    """Set up temporary uru.db and abzu.db with proper schemas."""
    monkeypatch.setattr("enki.db.ENKI_ROOT", tmp_path)
    monkeypatch.setattr("enki.db.DB_DIR", tmp_path / "db")
    (tmp_path / "db").mkdir(exist_ok=True)

    # Also patch in session_pipeline's imports
    monkeypatch.setattr("enki.session_pipeline.uru_db", __import__("enki.db", fromlist=["uru_db"]).uru_db)
    monkeypatch.setattr("enki.session_pipeline.get_abzu_db", __import__("enki.db", fromlist=["get_abzu_db"]).get_abzu_db)

    # Create uru.db tables
    from enki.gates.schemas import create_tables as create_uru_tables
    from enki.db import uru_db
    with uru_db() as conn:
        create_uru_tables(conn)

    # Create abzu.db tables (note_candidates)
    from enki.db import get_abzu_db
    conn = get_abzu_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS note_candidates (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                summary TEXT,
                context_description TEXT,
                keywords TEXT,
                tags TEXT,
                category TEXT NOT NULL,
                project TEXT,
                status TEXT DEFAULT 'raw',
                file_ref TEXT,
                file_hash TEXT,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()

    return tmp_path


def _insert_enforcement(conn, session_id, action, reason="test_gate",
                        user_override=0, timestamp=None):
    """Helper to insert enforcement_log entries."""
    conn.execute(
        "INSERT INTO enforcement_log (id, session_id, hook, layer, action, "
        "reason, user_override, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id, "pre-tool", "L2",
            action, reason, user_override,
            timestamp or datetime.now(timezone.utc).isoformat(),
        ),
    )


def _insert_nudge(conn, session_id, nudge_type, fire_count, acted_on=0):
    """Helper to insert nudge_state entries."""
    conn.execute(
        "INSERT INTO nudge_state (nudge_type, session_id, fire_count, acted_on) "
        "VALUES (?, ?, ?, ?)",
        (nudge_type, session_id, fire_count, acted_on),
    )


def _insert_proposal(conn, status="applied", applied=1, reviewed_at=None,
                      description="test proposal"):
    """Helper to insert feedback_proposals."""
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO feedback_proposals (id, trigger_type, description, status, "
        "applied, reviewed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, "false_positive", description, status, applied, reviewed_at),
    )
    return pid


# ---------------------------------------------------------------------------
# Loop 1: Reflector
# ---------------------------------------------------------------------------

class TestRunReflector:
    def test_empty_session_no_insights(self, tmp_dbs):
        sid = str(uuid.uuid4())
        result = run_reflector(sid)
        assert result["candidates_created"] == 0
        assert result["insights"] == []

    def test_repeated_blocks_create_candidates(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "no_goal")

        result = run_reflector(sid)
        assert result["candidates_created"] >= 1
        assert any("no_goal" in i for i in result["insights"])

    def test_single_block_not_reported(self, tmp_dbs):
        """Only repeated blocks (>=2) are reported."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_enforcement(conn, sid, "block", "one_time_gate")

        result = run_reflector(sid)
        assert not any("one_time_gate" in i for i in result["insights"])

    def test_override_patterns_detected(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_enforcement(conn, sid, "block", "tier_gate", user_override=1)

        result = run_reflector(sid)
        assert any("override" in i.lower() for i in result["insights"])

    def test_ignored_nudges_detected(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_nudge(conn, sid, "recall_hint", fire_count=3, acted_on=0)

        result = run_reflector(sid)
        assert any("recall_hint" in i for i in result["insights"])

    def test_nudge_acted_on_not_reported(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_nudge(conn, sid, "recall_hint", fire_count=3, acted_on=1)

        result = run_reflector(sid)
        assert not any("recall_hint" in i for i in result["insights"])

    def test_high_block_rate_insight(self, tmp_dbs):
        """Block rate > 30% triggers an insight."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            # 4 blocks, 2 allows = 67% block rate
            for _ in range(4):
                _insert_enforcement(conn, sid, "block", "gate_a")
            for _ in range(2):
                _insert_enforcement(conn, sid, "allow", "gate_b")

        result = run_reflector(sid)
        assert any("block rate" in i.lower() for i in result["insights"])

    def test_low_block_rate_no_insight(self, tmp_dbs):
        """Block rate <= 30% does NOT trigger."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            # 1 block, 9 allows = 10% block rate
            _insert_enforcement(conn, sid, "block", "gate_a")
            for _ in range(9):
                _insert_enforcement(conn, sid, "allow", "gate_b")

        result = run_reflector(sid)
        assert not any("block rate" in i.lower() for i in result["insights"])

    def test_candidates_written_to_abzu(self, tmp_dbs):
        """Reflector candidates should appear in note_candidates."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db, get_abzu_db
        with uru_db() as conn:
            for _ in range(2):
                _insert_enforcement(conn, sid, "block", "test_gate")

        run_reflector(sid, project="test-proj")

        conn = get_abzu_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM note_candidates WHERE session_id = ?",
                (sid,),
            ).fetchone()[0]
            assert count >= 1

            row = conn.execute(
                "SELECT * FROM note_candidates WHERE session_id = ? LIMIT 1",
                (sid,),
            ).fetchone()
            assert row["source"] == "session_end"
            assert row["category"] == "learning"
            assert row["project"] == "test-proj"
        finally:
            conn.close()

    def test_reflector_graceful_on_db_error(self, tmp_dbs, monkeypatch):
        """Reflector should not raise — returns error in insights."""
        monkeypatch.setattr(
            "enki.session_pipeline.uru_db",
            MagicMock(side_effect=Exception("db boom")),
        )
        result = run_reflector("fake-session")
        assert result["candidates_created"] == 0
        assert any("error" in i.lower() for i in result["insights"])


# ---------------------------------------------------------------------------
# Loop 2: Feedback Cycle
# ---------------------------------------------------------------------------

class TestRunFeedbackCycle:
    def test_no_blocks_no_proposal(self, tmp_dbs):
        sid = str(uuid.uuid4())
        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is None
        assert result["analysis"]["total_blocks"] == 0

    def test_low_fp_rate_no_proposal(self, tmp_dbs):
        """FP rate < 30% → no proposal."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(10):
                _insert_enforcement(conn, sid, "block", "some_gate")
            # 1 override out of 10 blocks = 10%
            _insert_enforcement(conn, sid, "block", "some_gate", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is None

    def test_insufficient_overrides_no_proposal(self, tmp_dbs):
        """Need at least 2 overrides."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_enforcement(conn, sid, "block", "some_gate")
            _insert_enforcement(conn, sid, "block", "some_gate", user_override=1)

        result = run_feedback_cycle(sid)
        # 1 override, 2 blocks = 50% but only 1 override < 2 threshold
        assert result["proposal_id"] is None

    def test_high_fp_creates_proposal(self, tmp_dbs):
        """FP rate >= 30% and overrides >= 2 → creates proposal."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "strict_gate", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is not None
        assert result["analysis"]["proposal_created"] is True

    def test_never_loosen_phase_gate(self, tmp_dbs):
        """Phase gate must never get a proposal even with high FP."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(5):
                _insert_enforcement(conn, sid, "block", "phase_check", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is None

    def test_never_loosen_spec_gate(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(5):
                _insert_enforcement(conn, sid, "block", "spec_approval", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is None

    def test_never_loosen_certainty_patterns(self, tmp_dbs):
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(5):
                _insert_enforcement(conn, sid, "block", "certainty_patterns_block", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is None

    def test_max_one_proposal_per_cycle(self, tmp_dbs):
        """Only the top overridden gate gets a proposal."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(4):
                _insert_enforcement(conn, sid, "block", "gate_a", user_override=1)
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "gate_b", user_override=1)

        result = run_feedback_cycle(sid)
        assert result["proposal_id"] is not None
        # Only one proposal, not two
        from enki.db import uru_db as udb
        with udb() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM feedback_proposals WHERE trigger_type = 'false_positive'"
            ).fetchone()[0]
            assert count == 1

    def test_duplicate_proposal_prevented(self, tmp_dbs):
        """Running feedback cycle twice shouldn't create duplicate proposals."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "noisy_gate", user_override=1)

        result1 = run_feedback_cycle(sid)
        assert result1["proposal_id"] is not None

        result2 = run_feedback_cycle(sid)
        assert result2["proposal_id"] is None  # Duplicate blocked

    def test_feedback_graceful_on_error(self, tmp_dbs, monkeypatch):
        monkeypatch.setattr(
            "enki.session_pipeline.uru_db",
            MagicMock(side_effect=Exception("db fail")),
        )
        result = run_feedback_cycle("fake")
        assert result["proposal_id"] is None
        assert "error" in result


# ---------------------------------------------------------------------------
# Loop 3: Regression Checks
# ---------------------------------------------------------------------------

class TestRunRegressionChecks:
    def test_no_applied_proposals(self, tmp_dbs):
        result = run_regression_checks()
        assert result["checked"] == 0
        assert result["regressions"] == []

    def test_applied_proposal_no_regression(self, tmp_dbs):
        """Applied proposal with low override rate → no regression."""
        from enki.db import uru_db
        reviewed_at = "2025-01-01T00:00:00"
        with uru_db() as conn:
            _insert_proposal(conn, reviewed_at=reviewed_at)
            # 1 override, 10 blocks after → 10% override rate
            for _ in range(10):
                _insert_enforcement(conn, "any", "block", "gate",
                                    timestamp="2025-01-02T00:00:00")
            _insert_enforcement(conn, "any", "block", "gate",
                                user_override=1, timestamp="2025-01-02T00:00:00")

        result = run_regression_checks()
        assert result["checked"] == 1
        assert result["regressions"] == []

    def test_applied_proposal_with_regression(self, tmp_dbs):
        """High override rate after applying → regression flagged."""
        from enki.db import uru_db
        reviewed_at = "2025-01-01T00:00:00"
        with uru_db() as conn:
            pid = _insert_proposal(conn, reviewed_at=reviewed_at)
            # Need >3 overrides and override_rate > 0.5
            # 6 block entries with user_override=1 → 6 overrides, 6 blocks
            # override_rate = 6/6 = 1.0 > 0.5 ✓ and overrides=6 > 3 ✓
            for _ in range(6):
                _insert_enforcement(conn, "any", "block", "gate",
                                    user_override=1, timestamp="2025-01-02T00:00:00")

        result = run_regression_checks()
        assert result["checked"] == 1
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["override_rate_after"] > 0.5

    def test_proposal_without_reviewed_at_skipped(self, tmp_dbs):
        """Proposals with no reviewed_at are checked but skip the comparison."""
        from enki.db import uru_db
        with uru_db() as conn:
            _insert_proposal(conn, reviewed_at=None)

        result = run_regression_checks()
        assert result["checked"] == 1
        assert result["regressions"] == []

    def test_regression_graceful_on_error(self, tmp_dbs, monkeypatch):
        monkeypatch.setattr(
            "enki.session_pipeline.uru_db",
            MagicMock(side_effect=Exception("boom")),
        )
        result = run_regression_checks()
        assert result["checked"] == 0


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class TestHandleSessionEnd:
    def test_empty_session_runs_all_loops(self, tmp_dbs):
        sid = str(uuid.uuid4())
        result = handle_session_end(sid)
        assert result["session_id"] == sid
        assert result["reflector"] is not None
        assert result["feedback"] is not None
        assert result["regression"] is not None
        assert result["errors"] == []

    def test_graceful_degradation_reflector_fails(self, tmp_dbs, monkeypatch):
        """If reflector raises, feedback and regression still run."""
        monkeypatch.setattr(
            "enki.session_pipeline.run_reflector",
            MagicMock(side_effect=RuntimeError("reflector boom")),
        )
        result = handle_session_end("test-sid")
        assert result["reflector"] is None
        assert "reflector boom" in result["errors"][0].lower()
        # Other loops still ran
        assert result["feedback"] is not None
        assert result["regression"] is not None

    def test_graceful_degradation_feedback_fails(self, tmp_dbs, monkeypatch):
        monkeypatch.setattr(
            "enki.session_pipeline.run_feedback_cycle",
            MagicMock(side_effect=RuntimeError("feedback boom")),
        )
        result = handle_session_end("test-sid")
        assert result["feedback"] is None
        assert result["reflector"] is not None
        assert result["regression"] is not None

    def test_graceful_degradation_regression_fails(self, tmp_dbs, monkeypatch):
        monkeypatch.setattr(
            "enki.session_pipeline.run_regression_checks",
            MagicMock(side_effect=RuntimeError("regression boom")),
        )
        result = handle_session_end("test-sid")
        assert result["regression"] is None
        assert result["reflector"] is not None
        assert result["feedback"] is not None

    def test_all_loops_fail_still_returns(self, tmp_dbs, monkeypatch):
        """Even if ALL loops fail, handle_session_end returns gracefully."""
        monkeypatch.setattr(
            "enki.session_pipeline.run_reflector",
            MagicMock(side_effect=RuntimeError("r")),
        )
        monkeypatch.setattr(
            "enki.session_pipeline.run_feedback_cycle",
            MagicMock(side_effect=RuntimeError("f")),
        )
        monkeypatch.setattr(
            "enki.session_pipeline.run_regression_checks",
            MagicMock(side_effect=RuntimeError("g")),
        )
        result = handle_session_end("test-sid")
        assert len(result["errors"]) == 3
        assert result["reflector"] is None
        assert result["feedback"] is None
        assert result["regression"] is None

    def test_full_pipeline_with_data(self, tmp_dbs):
        """End-to-end: session with blocks, overrides, nudges."""
        sid = str(uuid.uuid4())
        from enki.db import uru_db
        with uru_db() as conn:
            # Repeated blocks → reflector candidate
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "noisy_gate")
            # Overrides → feedback proposal
            for _ in range(3):
                _insert_enforcement(conn, sid, "block", "noisy_gate", user_override=1)
            # Ignored nudge → reflector candidate
            _insert_nudge(conn, sid, "test_nudge", fire_count=4, acted_on=0)

        result = handle_session_end(sid, project="test")
        assert result["reflector"]["candidates_created"] >= 2
        assert result["feedback"]["proposal_id"] is not None
        assert result["errors"] == []


# ---------------------------------------------------------------------------
# NEVER_LOOSEN_GATES constant
# ---------------------------------------------------------------------------

class TestNeverLoosenGates:
    def test_contains_required_gates(self):
        assert "phase" in NEVER_LOOSEN_GATES
        assert "spec" in NEVER_LOOSEN_GATES
        assert "certainty_patterns" in NEVER_LOOSEN_GATES

    def test_is_set(self):
        assert isinstance(NEVER_LOOSEN_GATES, set)
