"""Tests for QA test case standard and traceability (Item 4.4)."""

import pytest
from enki.orch.test_standard import (
    validate_test_case,
    validate_test_suite_output,
    generate_traceability_matrix,
    get_test_dir_template,
    get_qa_output_template,
    REQUIRED_TC_FIELDS,
    VALID_TEST_TYPES,
    VALID_PRIORITIES,
    TC_ID_PATTERN,
)


def _valid_tc(**overrides):
    """Build a valid test case dict with optional overrides."""
    tc = {
        "tc_id": "TC-AUTH-1",
        "ac_mapping": "AC-AUTH-1",
        "type": "unit",
        "priority": "P0",
        "steps": ["Step 1", "Step 2"],
        "expected_result": "User is authenticated",
    }
    tc.update(overrides)
    return tc


class TestValidateTestCase:
    def test_valid_case(self):
        result = validate_test_case(_valid_tc())
        assert result["valid"] is True
        assert result["issues"] == []

    def test_missing_required_field(self):
        tc = _valid_tc()
        del tc["tc_id"]
        result = validate_test_case(tc)
        assert not result["valid"]
        assert any("tc_id" in i for i in result["issues"])

    def test_empty_required_field(self):
        result = validate_test_case(_valid_tc(steps=[]))
        assert not result["valid"]

    def test_invalid_tc_id_format(self):
        result = validate_test_case(_valid_tc(tc_id="BAD-FORMAT"))
        assert not result["valid"]
        assert any("TC ID" in i for i in result["issues"])

    def test_valid_tc_id_formats(self):
        for tc_id in ["TC-AUTH-1", "TC-DB-42", "TC-PERF-100"]:
            result = validate_test_case(_valid_tc(tc_id=tc_id))
            assert result["valid"], f"Failed for {tc_id}"

    def test_invalid_test_type(self):
        result = validate_test_case(_valid_tc(type="smoke"))
        assert not result["valid"]
        assert any("test type" in i.lower() for i in result["issues"])

    def test_all_valid_types(self):
        for t in VALID_TEST_TYPES:
            result = validate_test_case(_valid_tc(type=t))
            assert result["valid"], f"Failed for type {t}"

    def test_invalid_priority(self):
        result = validate_test_case(_valid_tc(priority="critical"))
        assert not result["valid"]

    def test_all_valid_priorities(self):
        for p in VALID_PRIORITIES:
            result = validate_test_case(_valid_tc(priority=p))
            assert result["valid"], f"Failed for priority {p}"

    def test_invalid_ac_mapping(self):
        result = validate_test_case(_valid_tc(ac_mapping="BAD"))
        assert not result["valid"]
        assert any("AC mapping" in i for i in result["issues"])


class TestValidateTestSuiteOutput:
    def test_valid_suite(self):
        suite = [_valid_tc(), _valid_tc(tc_id="TC-AUTH-2", ac_mapping="AC-AUTH-2")]
        result = validate_test_suite_output(suite)
        assert result["valid"] is True
        assert result["total"] == 2
        assert result["valid_count"] == 2

    def test_duplicate_tc_ids(self):
        suite = [_valid_tc(), _valid_tc()]  # Same TC-AUTH-1
        result = validate_test_suite_output(suite)
        assert not result["valid"]
        assert any("Duplicate" in i for i in result["issues"])

    def test_mixed_valid_invalid(self):
        suite = [_valid_tc(), _valid_tc(tc_id="BAD")]
        result = validate_test_suite_output(suite)
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 1

    def test_ac_coverage_tracking(self):
        suite = [
            _valid_tc(ac_mapping="AC-AUTH-1"),
            _valid_tc(tc_id="TC-AUTH-2", ac_mapping="AC-AUTH-2"),
        ]
        result = validate_test_suite_output(suite)
        assert "AC-AUTH-1" in result["ac_coverage"]
        assert "AC-AUTH-2" in result["ac_coverage"]

    def test_empty_suite(self):
        result = validate_test_suite_output([])
        assert result["valid"] is True
        assert result["total"] == 0


class TestGenerateTraceabilityMatrix:
    def test_basic_matrix(self):
        tcs = [_valid_tc()]
        result = generate_traceability_matrix(tcs)
        assert "# Test Traceability Matrix" in result
        assert "AC-AUTH-1" in result
        assert "TC-AUTH-1" in result

    def test_uncovered_ac(self):
        tcs = [_valid_tc()]
        result = generate_traceability_matrix(tcs, ac_codes=["AC-AUTH-1", "AC-DB-1"])
        assert "UNCOVERED" in result
        assert "AC-DB-1" in result

    def test_summary_section(self):
        tcs = [_valid_tc()]
        result = generate_traceability_matrix(tcs)
        assert "## Summary" in result
        assert "Total test cases: 1" in result

    def test_empty_test_cases(self):
        result = generate_traceability_matrix([])
        assert "Total test cases: 0" in result


class TestHelpers:
    def test_dir_template_has_standard_dirs(self):
        tmpl = get_test_dir_template()
        assert "unit/" in tmpl
        assert "integration/" in tmpl
        assert "e2e/" in tmpl
        assert "fixtures/" in tmpl

    def test_qa_output_template_fields(self):
        tmpl = get_qa_output_template()
        for field in REQUIRED_TC_FIELDS:
            assert field in tmpl

    def test_tc_id_pattern(self):
        assert TC_ID_PATTERN.match("TC-AUTH-1")
        assert TC_ID_PATTERN.match("TC-DB-42")
        assert not TC_ID_PATTERN.match("auth-1")
        assert not TC_ID_PATTERN.match("TC-auth-1")
