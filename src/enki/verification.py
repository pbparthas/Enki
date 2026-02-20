"""Objective verification protocol for execution-agent completion claims."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

MAX_VERIFICATION_RETRIES = 3


@dataclass
class VerificationResult:
    passed: bool
    results: list[dict]
    summary: str


def run_verification(
    commands: list[str],
    cwd: str,
    timeout: int = 120,
) -> VerificationResult:
    """Run verification commands and determine pass/fail by exit codes."""
    if not commands:
        return VerificationResult(
            passed=True,
            results=[],
            summary="No verification commands provided.",
        )

    command_results: list[dict] = []
    all_passed = True

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            result = {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "exit_code": -1,
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + f"\nCommand timed out after {timeout}s.",
                "timed_out": True,
            }

        command_results.append(result)
        if result["exit_code"] != 0:
            all_passed = False

    passed_count = sum(1 for r in command_results if r["exit_code"] == 0)
    summary = (
        f"Verification {'passed' if all_passed else 'failed'}: "
        f"{passed_count}/{len(command_results)} commands passed."
    )
    return VerificationResult(passed=all_passed, results=command_results, summary=summary)


def format_verification_errors(result: VerificationResult) -> str:
    """Format failing verification output for retry context."""
    if result.passed:
        return "Verification passed."

    lines = [result.summary, "", "Failed commands:"]
    for item in result.results:
        if item["exit_code"] == 0:
            continue
        lines.append(f"- Command: {item['command']}")
        lines.append(f"  Exit code: {item['exit_code']}")
        if item.get("timed_out"):
            lines.append("  Failure: timeout")
        if item.get("stdout"):
            lines.append(f"  stdout: {item['stdout'][:500]}")
        if item.get("stderr"):
            lines.append(f"  stderr: {item['stderr'][:500]}")

    return "\n".join(lines)


def verification_retry_loop(
    agent_role: str,
    task: dict,
    verification_commands: list[str],
    cwd: str,
    runner=run_verification,
) -> dict:
    """Run objective verification with max retries, then escalate."""
    for attempt in range(MAX_VERIFICATION_RETRIES):
        result = runner(verification_commands, cwd)
        if result.passed:
            return {
                "status": "done",
                "attempts": attempt + 1,
                "verification": result,
            }

        error_context = format_verification_errors(result)
        _ = (agent_role, task, error_context)  # Documented retry inputs for orchestrator integration.

    return {
        "status": "hitl_escalation",
        "attempts": MAX_VERIFICATION_RETRIES,
        "reason": "Verification failed after max retries.",
    }
