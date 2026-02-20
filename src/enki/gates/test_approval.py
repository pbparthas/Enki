"""HITL test approval gate for QA test execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from enki.db import em_db


@dataclass
class TestApprovalState:
    task_id: str
    tests_written: bool
    validator_checked: bool
    validator_issues: list
    hitl_approved: bool
    hitl_approved_at: str | None
    hitl_notes: str


@dataclass
class GateResult:
    blocked: bool
    reason: str


@dataclass
class ValidatorResult:
    passed: bool
    issues: list[dict]
    coverage: dict


REQUIRED_TC_FIELDS = ("AC", "type", "priority", "steps", "mock", "expected")
LAZY_MOCK_WORDS = ("some", "example", "test123", "placeholder", "todo")


def _ensure_task_row(project: str, task_id: str) -> None:
    """Ensure foreign-key-compatible task row exists for approval records."""
    with em_db(project) as conn:
        existing = conn.execute(
            "SELECT task_id FROM task_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, project, "approval-gate", "QA Test Approval", "standard", "pending"),
        )


def _ensure_state_row(project: str, task_id: str) -> None:
    _ensure_task_row(project, task_id)
    with em_db(project) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO test_approvals (task_id, project) VALUES (?, ?)",
            (task_id, project),
        )


def get_test_approval_state(task_id: str, project: str = ".") -> TestApprovalState:
    _ensure_state_row(project, task_id)
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM test_approvals WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    issues = json.loads(row["validator_issues"]) if row and row["validator_issues"] else []
    return TestApprovalState(
        task_id=task_id,
        tests_written=bool(row["tests_written"]),
        validator_checked=bool(row["validator_checked"]),
        validator_issues=issues,
        hitl_approved=bool(row["hitl_approved"]),
        hitl_approved_at=row["hitl_approved_at"],
        hitl_notes=row["hitl_notes"] or "",
    )


def mark_tests_written(task_id: str, project: str = ".", written: bool = True) -> None:
    _ensure_state_row(project, task_id)
    with em_db(project) as conn:
        conn.execute(
            "UPDATE test_approvals SET tests_written = ? WHERE task_id = ?",
            (1 if written else 0, task_id),
        )


def mark_validator_result(task_id: str, issues: list[dict], project: str = ".") -> None:
    _ensure_state_row(project, task_id)
    with em_db(project) as conn:
        conn.execute(
            "UPDATE test_approvals SET validator_checked = 1, validator_issues = ? "
            "WHERE task_id = ?",
            (json.dumps(issues), task_id),
        )


def set_hitl_approval(
    task_id: str,
    approved: bool,
    notes: str = "",
    project: str = ".",
) -> None:
    _ensure_state_row(project, task_id)
    approved_at = datetime.now(timezone.utc).isoformat() if approved else None
    with em_db(project) as conn:
        conn.execute(
            "UPDATE test_approvals SET hitl_approved = ?, hitl_approved_at = ?, "
            "hitl_notes = ? WHERE task_id = ?",
            (1 if approved else 0, approved_at, notes, task_id),
        )


def can_execute_tests(task_id: str, project: str = ".") -> GateResult:
    """Check if QA can execute tests against Dev's code."""
    state = get_test_approval_state(task_id, project=project)
    if not state.tests_written:
        return GateResult(blocked=True, reason="Test execution blocked: tests not written.")
    if not state.validator_checked:
        return GateResult(blocked=True, reason="Test execution blocked: validator not run.")
    if not state.hitl_approved:
        return GateResult(blocked=True, reason="Test execution blocked: HITL approval missing.")
    return GateResult(blocked=False, reason="Test execution approved.")


def _extract_ac_ids(spec_text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\bAC[-_ ]?([A-Za-z0-9]+)\b", spec_text)}


def _extract_tc_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"\b(TC-[A-Za-z0-9]+-[0-9]+)\b", text))
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[start:end]))
    return sections


def validate_test_suite(task_id: str, spec_path: str, test_dir: str) -> ValidatorResult:
    """Run automated test-suite quality checks."""
    issues: list[dict] = []
    spec_text = Path(spec_path).read_text(encoding="utf-8") if spec_path and Path(spec_path).exists() else ""
    ac_ids = _extract_ac_ids(spec_text)

    tc_sections: list[tuple[str, str, str]] = []
    test_root = Path(test_dir)
    if test_root.exists():
        for path in test_root.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".txt", ".py"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for tc_id, section in _extract_tc_sections(text):
                    tc_sections.append((tc_id, section, str(path)))

    tc_ids = [tc_id for tc_id, _, _ in tc_sections]
    seen = set()
    duplicates = set()
    for tc_id in tc_ids:
        if tc_id in seen:
            duplicates.add(tc_id)
        seen.add(tc_id)

    if duplicates:
        for tc_id in sorted(duplicates):
            issues.append(
                {
                    "severity": "error",
                    "description": "Duplicate TC ID",
                    "file": "",
                    "tc_id": tc_id,
                }
            )

    covered_acs: set[str] = set()
    for tc_id, section, file_path in tc_sections:
        if not re.match(r"^TC-[A-Za-z0-9]+-[0-9]+$", tc_id):
            issues.append(
                {
                    "severity": "error",
                    "description": "TC ID does not follow convention TC-{section}-{seq}",
                    "file": file_path,
                    "tc_id": tc_id,
                }
            )

        section_lower = section.lower()
        for field in REQUIRED_TC_FIELDS:
            if field.lower() not in section_lower:
                issues.append(
                    {
                        "severity": "error",
                        "description": f"Missing required metadata field: {field}",
                        "file": file_path,
                        "tc_id": tc_id,
                    }
                )

        ac_matches = {m.group(1) for m in re.finditer(r"\bAC[-_ ]?([A-Za-z0-9]+)\b", section)}
        covered_acs.update(ac_matches)

        if "mock" in section_lower and any(word in section_lower for word in LAZY_MOCK_WORDS):
            issues.append(
                {
                    "severity": "warning",
                    "description": "Mock data appears too generic.",
                    "file": file_path,
                    "tc_id": tc_id,
                }
            )

    missing_acs = sorted(ac_ids - covered_acs)
    for ac in missing_acs:
        issues.append(
            {
                "severity": "error",
                "description": f"No test case mapped for AC{ac}",
                "file": spec_path,
                "tc_id": "",
            }
        )

    total_acs = len(ac_ids)
    covered_count = len(ac_ids & covered_acs)
    coverage = {
        "total_acs": total_acs,
        "covered_acs": covered_count,
        "percentage": (covered_count / total_acs * 100.0) if total_acs else 100.0,
    }

    blocking_issues = [i for i in issues if i["severity"] == "error"]
    return ValidatorResult(
        passed=len(blocking_issues) == 0,
        issues=issues,
        coverage=coverage,
    )
