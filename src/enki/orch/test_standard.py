"""test_standard.py — QA test case standard and traceability (Item 4.4).

TC ID convention: TC-{spec_section}-{sequence}
Required fields: TC ID, AC mapping, type, priority, steps, mock data, expected result.
QA generates test_traceability.md with coverage matrix.
Test directory structure template.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Required fields per test case
REQUIRED_TC_FIELDS = [
    "tc_id",
    "ac_mapping",
    "type",
    "priority",
    "steps",
    "expected_result",
]

# Valid test types
VALID_TEST_TYPES = {"unit", "integration", "e2e", "ui", "regression", "performance"}

# Valid priorities
VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}

# Standard test directory structure
TEST_DIR_STRUCTURE = {
    "unit/": "Unit tests — isolated, mocked dependencies",
    "integration/": "Integration tests — real service interactions",
    "e2e/": "End-to-end tests — full user flows",
    "ui/": "UI/visual tests (if applicable)",
    "regression/": "Regression tests for fixed bugs",
    "fixtures/": "Shared test fixtures, mock data, factories",
}

# TC ID pattern
TC_ID_PATTERN = re.compile(r"^TC-[A-Z]+-\d+$")


def validate_test_case(test_case: dict) -> dict:
    """Validate a single test case against the standard.

    Returns {"valid": bool, "issues": list[str]}.
    """
    issues = []

    # Check required fields
    for field in REQUIRED_TC_FIELDS:
        if field not in test_case or not test_case[field]:
            issues.append(f"Missing required field: {field}")

    # Validate TC ID format
    tc_id = test_case.get("tc_id", "")
    if tc_id and not TC_ID_PATTERN.match(tc_id):
        issues.append(
            f"Invalid TC ID format: '{tc_id}'. "
            f"Expected: TC-{{SECTION}}-{{SEQ}} (e.g., TC-AUTH-1)"
        )

    # Validate test type
    test_type = test_case.get("type", "")
    if test_type and test_type not in VALID_TEST_TYPES:
        issues.append(f"Invalid test type: '{test_type}'. Valid: {VALID_TEST_TYPES}")

    # Validate priority
    priority = test_case.get("priority", "")
    if priority and priority not in VALID_PRIORITIES:
        issues.append(f"Invalid priority: '{priority}'. Valid: {VALID_PRIORITIES}")

    # AC mapping format check
    ac_mapping = test_case.get("ac_mapping", "")
    if ac_mapping and not re.match(r"AC-\w+-\d+", ac_mapping):
        issues.append(
            f"Invalid AC mapping: '{ac_mapping}'. "
            f"Expected: AC-{{SECTION}}-{{SEQ}}"
        )

    return {"valid": len(issues) == 0, "issues": issues}


def validate_test_suite_output(test_cases: list[dict]) -> dict:
    """Validate a complete QA test suite output.

    Returns aggregate validation results.
    """
    total = len(test_cases)
    valid_count = 0
    all_issues = []
    tc_ids = set()
    ac_coverage = set()

    for tc in test_cases:
        result = validate_test_case(tc)
        if result["valid"]:
            valid_count += 1
        else:
            all_issues.extend(
                f"[{tc.get('tc_id', '?')}] {issue}"
                for issue in result["issues"]
            )

        # Track uniqueness
        tc_id = tc.get("tc_id", "")
        if tc_id:
            if tc_id in tc_ids:
                all_issues.append(f"Duplicate TC ID: {tc_id}")
            tc_ids.add(tc_id)

        # Track AC coverage
        ac = tc.get("ac_mapping", "")
        if ac:
            ac_coverage.add(ac)

    return {
        "valid": len(all_issues) == 0,
        "total": total,
        "valid_count": valid_count,
        "invalid_count": total - valid_count,
        "issues": all_issues,
        "ac_coverage": sorted(ac_coverage),
        "tc_ids": sorted(tc_ids),
    }


def generate_traceability_matrix(
    test_cases: list[dict],
    ac_codes: list[str] | None = None,
) -> str:
    """Generate test_traceability.md content.

    Maps AC codes to test cases for coverage verification.
    """
    lines = [
        "# Test Traceability Matrix",
        "",
        "## Coverage",
        "",
        "| AC Code | Test Cases | Type | Priority |",
        "|---------|-----------|------|----------|",
    ]

    # Build AC → TC mapping
    ac_to_tc = {}
    for tc in test_cases:
        ac = tc.get("ac_mapping", "")
        if ac:
            if ac not in ac_to_tc:
                ac_to_tc[ac] = []
            ac_to_tc[ac].append(tc)

    # Fill coverage table
    all_acs = sorted(set((ac_codes or []) + list(ac_to_tc.keys())))
    uncovered = []
    for ac in all_acs:
        tcs = ac_to_tc.get(ac, [])
        if not tcs:
            uncovered.append(ac)
            lines.append(f"| {ac} | **UNCOVERED** | - | - |")
        else:
            for tc in tcs:
                lines.append(
                    f"| {ac} | {tc.get('tc_id', '?')} "
                    f"| {tc.get('type', '?')} | {tc.get('priority', '?')} |"
                )

    # Summary
    lines.extend([
        "",
        "## Summary",
        "",
        f"- Total test cases: {len(test_cases)}",
        f"- AC codes covered: {len(ac_to_tc)}",
        f"- AC codes uncovered: {len(uncovered)}",
    ])

    if uncovered:
        lines.extend([
            "",
            "## Uncovered Acceptance Criteria",
            "",
        ])
        for ac in uncovered:
            lines.append(f"- {ac}")

    return "\n".join(lines)


def get_test_dir_template() -> dict:
    """Return the standard test directory structure template."""
    return dict(TEST_DIR_STRUCTURE)


def get_qa_output_template() -> dict:
    """Return the expected QA agent output template for test cases."""
    return {
        "tc_id": "TC-{SECTION}-{SEQ}",
        "ac_mapping": "AC-{SECTION}-{SEQ}",
        "type": "unit|integration|e2e|ui|regression|performance",
        "priority": "P0|P1|P2|P3",
        "steps": ["Step 1", "Step 2"],
        "mock_data": {},
        "expected_result": "Expected outcome",
    }
