"""Tests for HITL test approval gate."""

from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod
import pytest


PROJECT = "approval-proj"
TASK_ID = "task-001"


@pytest.fixture
def em_root(tmp_path):
    root = tmp_path / ".enki"
    root.mkdir()
    db_dir = root / "db"
    db_dir.mkdir()
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with patch.object(db_mod, "ENKI_ROOT", root), patch.object(db_mod, "DB_DIR", db_dir):
        db_mod.init_all()
        yield root
    db_mod._em_initialized = old_init


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_gate_blocks_without_tests_written(em_root):
    from enki.gates.test_approval import can_execute_tests

    result = can_execute_tests(TASK_ID, project=PROJECT)
    assert result.blocked is True
    assert "tests not written" in result.reason.lower()


def test_gate_blocks_without_validator_check(em_root):
    from enki.gates.test_approval import can_execute_tests, mark_tests_written

    mark_tests_written(TASK_ID, project=PROJECT, written=True)
    result = can_execute_tests(TASK_ID, project=PROJECT)
    assert result.blocked is True
    assert "validator not run" in result.reason.lower()


def test_gate_blocks_without_hitl_approval(em_root):
    from enki.gates.test_approval import (
        can_execute_tests,
        mark_tests_written,
        mark_validator_result,
    )

    mark_tests_written(TASK_ID, project=PROJECT, written=True)
    mark_validator_result(TASK_ID, [], project=PROJECT)
    result = can_execute_tests(TASK_ID, project=PROJECT)
    assert result.blocked is True
    assert "approval missing" in result.reason.lower()


def test_gate_allows_after_full_approval(em_root):
    from enki.gates.test_approval import (
        can_execute_tests,
        mark_tests_written,
        mark_validator_result,
        set_hitl_approval,
    )

    mark_tests_written(TASK_ID, project=PROJECT, written=True)
    mark_validator_result(TASK_ID, [], project=PROJECT)
    set_hitl_approval(TASK_ID, approved=True, notes="ok", project=PROJECT)
    result = can_execute_tests(TASK_ID, project=PROJECT)
    assert result.blocked is False


def test_validator_catches_missing_ac_coverage(em_root, tmp_path):
    from enki.gates.test_approval import validate_test_suite

    spec = tmp_path / "spec.md"
    tests_dir = tmp_path / "testsuite"
    _write(spec, "AC1: login works\nAC2: logout works\n")
    _write(
        tests_dir / "traceability.md",
        "TC-auth-1\nAC1\ntype: functional\npriority: high\nsteps: do x\nmock: user_123\nexpected: pass\n",
    )
    result = validate_test_suite(TASK_ID, str(spec), str(tests_dir))
    assert any("AC2" in i["description"] for i in result.issues)


def test_validator_catches_missing_tc_metadata(em_root, tmp_path):
    from enki.gates.test_approval import validate_test_suite

    spec = tmp_path / "spec.md"
    tests_dir = tmp_path / "testsuite"
    _write(spec, "AC1: login works\n")
    _write(tests_dir / "t.md", "TC-auth-1\nAC1\npriority: high\n")
    result = validate_test_suite(TASK_ID, str(spec), str(tests_dir))
    assert any("Missing required metadata field" in i["description"] for i in result.issues)


def test_validator_catches_duplicate_tc_ids(em_root, tmp_path):
    from enki.gates.test_approval import validate_test_suite

    spec = tmp_path / "spec.md"
    tests_dir = tmp_path / "testsuite"
    _write(spec, "AC1: login works\n")
    _write(
        tests_dir / "dup.md",
        (
            "TC-auth-1\nAC1\ntype: functional\npriority: high\nsteps: a\nmock: u1\nexpected: pass\n"
            "TC-auth-1\nAC1\ntype: functional\npriority: high\nsteps: b\nmock: u2\nexpected: pass\n"
        ),
    )
    result = validate_test_suite(TASK_ID, str(spec), str(tests_dir))
    assert any(i["description"] == "Duplicate TC ID" for i in result.issues)


def test_validator_flags_lazy_mock_data(em_root, tmp_path):
    from enki.gates.test_approval import validate_test_suite

    spec = tmp_path / "spec.md"
    tests_dir = tmp_path / "testsuite"
    _write(spec, "AC1: login works\n")
    _write(
        tests_dir / "lazy.md",
        "TC-auth-1\nAC1\ntype: functional\npriority: high\nsteps: a\nmock: some placeholder\nexpected: pass\n",
    )
    result = validate_test_suite(TASK_ID, str(spec), str(tests_dir))
    assert any(i["severity"] == "warning" for i in result.issues)


def test_validator_passes_clean_suite(em_root, tmp_path):
    from enki.gates.test_approval import validate_test_suite

    spec = tmp_path / "spec.md"
    tests_dir = tmp_path / "testsuite"
    _write(spec, "AC1: login works\nAC2: logout works\n")
    _write(
        tests_dir / "clean.md",
        (
            "TC-auth-1\nAC1\ntype: functional\npriority: high\nsteps: step1\nmock: user_alpha\nexpected: pass\n"
            "TC-auth-2\nAC2\ntype: regression\npriority: medium\nsteps: step2\nmock: user_beta\nexpected: pass\n"
        ),
    )
    result = validate_test_suite(TASK_ID, str(spec), str(tests_dir))
    assert result.passed is True


def test_approval_state_persists_in_db(em_root):
    from enki.gates.test_approval import (
        get_test_approval_state,
        mark_tests_written,
        mark_validator_result,
        set_hitl_approval,
    )

    mark_tests_written(TASK_ID, project=PROJECT, written=True)
    mark_validator_result(TASK_ID, [{"severity": "warning"}], project=PROJECT)
    set_hitl_approval(TASK_ID, approved=True, notes="approved", project=PROJECT)
    state = get_test_approval_state(TASK_ID, project=PROJECT)
    assert state.tests_written is True
    assert state.validator_checked is True
    assert state.hitl_approved is True
    assert state.hitl_notes == "approved"


def test_hitl_change_request_resets_approval(em_root):
    from enki.gates.test_approval import (
        get_test_approval_state,
        set_hitl_approval,
    )

    set_hitl_approval(TASK_ID, approved=True, notes="ok", project=PROJECT)
    set_hitl_approval(TASK_ID, approved=False, notes="needs changes", project=PROJECT)
    state = get_test_approval_state(TASK_ID, project=PROJECT)
    assert state.hitl_approved is False
    assert state.hitl_notes == "needs changes"
