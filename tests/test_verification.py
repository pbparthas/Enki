"""Tests for objective verification protocol."""

from enki.verification import (
    MAX_VERIFICATION_RETRIES,
    VerificationResult,
    format_verification_errors,
    run_verification,
    verification_retry_loop,
)


def test_passing_command(tmp_path):
    result = run_verification(["echo hello"], cwd=str(tmp_path))
    assert result.passed is True
    assert result.results[0]["exit_code"] == 0


def test_failing_command(tmp_path):
    result = run_verification(["exit 1"], cwd=str(tmp_path))
    assert result.passed is False
    assert result.results[0]["exit_code"] == 1


def test_multiple_commands_all_pass(tmp_path):
    result = run_verification(["echo one", "echo two"], cwd=str(tmp_path))
    assert result.passed is True
    assert len(result.results) == 2


def test_multiple_commands_one_fails(tmp_path):
    result = run_verification(["echo one", "exit 2"], cwd=str(tmp_path))
    assert result.passed is False
    assert result.results[1]["exit_code"] == 2


def test_timeout_is_failure(tmp_path):
    result = run_verification(["sleep 2"], cwd=str(tmp_path), timeout=1)
    assert result.passed is False
    assert result.results[0]["timed_out"] is True


def test_empty_commands_list(tmp_path):
    result = run_verification([], cwd=str(tmp_path))
    assert result.passed is True
    assert result.results == []


def test_retry_count_tracking():
    calls = {"count": 0}

    def runner(commands, cwd):
        calls["count"] += 1
        if calls["count"] < 2:
            return VerificationResult(
                passed=False,
                results=[{"command": "x", "exit_code": 1, "stdout": "", "stderr": "", "timed_out": False}],
                summary="fail",
            )
        return VerificationResult(
            passed=True,
            results=[{"command": "x", "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}],
            summary="pass",
        )

    result = verification_retry_loop("dev", {"task_id": "t1"}, ["echo"], "/tmp", runner=runner)
    assert result["status"] == "done"
    assert result["attempts"] == 2
    assert calls["count"] == 2


def test_hitl_escalation_after_3_failures():
    calls = {"count": 0}

    def runner(commands, cwd):
        calls["count"] += 1
        return VerificationResult(
            passed=False,
            results=[{"command": "x", "exit_code": 1, "stdout": "", "stderr": "boom", "timed_out": False}],
            summary="fail",
        )

    result = verification_retry_loop("qa", {"task_id": "t2"}, ["echo"], "/tmp", runner=runner)
    assert result["status"] == "hitl_escalation"
    assert result["attempts"] == MAX_VERIFICATION_RETRIES
    assert calls["count"] == MAX_VERIFICATION_RETRIES


def test_error_output_formatting():
    result = VerificationResult(
        passed=False,
        results=[{
            "command": "pytest",
            "exit_code": 1,
            "stdout": "collected 1 item",
            "stderr": "AssertionError",
            "timed_out": False,
        }],
        summary="Verification failed",
    )
    formatted = format_verification_errors(result)
    assert "Verification failed" in formatted
    assert "pytest" in formatted
    assert "Exit code: 1" in formatted


def test_verification_result_structure(tmp_path):
    result = run_verification(["echo ok"], cwd=str(tmp_path))
    assert isinstance(result.passed, bool)
    assert isinstance(result.results, list)
    assert isinstance(result.summary, str)
    first = result.results[0]
    assert {"command", "exit_code", "stdout", "stderr", "timed_out"} <= set(first.keys())
