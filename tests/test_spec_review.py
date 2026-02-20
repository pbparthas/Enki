"""Tests for v4 spec review gate (Items 3.4 + 3.7).

Tests InfoSec mandatory review, UI/UX and Performance conditional
triggers, concern formatting, and AC code checking.
"""

import pytest

from enki.orch.spec_review import (
    check_spec_for_ac_codes,
    determine_reviewers,
    format_concerns_for_architect,
    should_review_infosec,
    should_review_performance,
    should_review_ui_ux,
)


# ---------------------------------------------------------------------------
# InfoSec review
# ---------------------------------------------------------------------------


class TestInfoSecReview:
    def test_mandatory_for_standard(self):
        assert should_review_infosec("standard") is True

    def test_mandatory_for_full(self):
        assert should_review_infosec("full") is True

    def test_not_required_for_minimal(self):
        assert should_review_infosec("minimal") is False


# ---------------------------------------------------------------------------
# UI/UX review
# ---------------------------------------------------------------------------


class TestUIUXReview:
    def test_triggers_on_frontend_keyword(self):
        assert should_review_ui_ux("Build a frontend dashboard") is True

    def test_triggers_on_component_keyword(self):
        assert should_review_ui_ux("Add a modal component for settings") is True

    def test_triggers_on_tsx_file(self):
        assert should_review_ui_ux("", files=["src/App.tsx"]) is True

    def test_triggers_on_components_dir(self):
        assert should_review_ui_ux("", files=["components/Header.jsx"]) is True

    def test_no_trigger_on_backend(self):
        assert should_review_ui_ux("Add REST API endpoint for users") is False

    def test_no_trigger_empty_spec(self):
        assert should_review_ui_ux("") is False


# ---------------------------------------------------------------------------
# Performance review
# ---------------------------------------------------------------------------


class TestPerformanceReview:
    def test_triggers_on_sla(self):
        assert should_review_performance("API must meet 200ms p99 SLA") is True

    def test_triggers_on_cache(self):
        assert should_review_performance("Add caching layer for queries") is True

    def test_triggers_on_throughput(self):
        assert should_review_performance("Must handle 1000 req/s throughput") is True

    def test_no_trigger_on_basic_crud(self):
        assert should_review_performance("Add CRUD endpoints for users") is False


# ---------------------------------------------------------------------------
# determine_reviewers
# ---------------------------------------------------------------------------


class TestDetermineReviewers:
    def test_standard_tier_always_has_infosec(self):
        reviewers = determine_reviewers("standard", "Simple backend API")
        roles = [r["role"] for r in reviewers]
        assert "infosec" in roles

    def test_minimal_tier_no_reviewers(self):
        reviewers = determine_reviewers("minimal", "Fix typo")
        assert len(reviewers) == 0

    def test_full_tier_with_frontend(self):
        reviewers = determine_reviewers(
            "full",
            "Build a responsive dashboard with React components",
        )
        roles = [r["role"] for r in reviewers]
        assert "infosec" in roles
        assert "ui_ux" in roles

    def test_standard_with_performance(self):
        reviewers = determine_reviewers(
            "standard",
            "Optimize query performance, add caching layer",
        )
        roles = [r["role"] for r in reviewers]
        assert "infosec" in roles
        assert "performance" in roles

    def test_infosec_is_mandatory(self):
        reviewers = determine_reviewers("standard", "any spec")
        infosec = [r for r in reviewers if r["role"] == "infosec"]
        assert infosec[0]["mandatory"] is True

    def test_ui_ux_is_not_mandatory(self):
        reviewers = determine_reviewers("full", "Build frontend components")
        uiux = [r for r in reviewers if r["role"] == "ui_ux"]
        assert uiux[0]["mandatory"] is False


# ---------------------------------------------------------------------------
# format_concerns
# ---------------------------------------------------------------------------


class TestFormatConcerns:
    def test_no_concerns_approved(self):
        result = format_concerns_for_architect([], "infosec")
        assert "Approved" in result

    def test_formats_concerns(self):
        concerns = [
            {
                "severity": "high",
                "title": "SQL Injection risk",
                "description": "User input not parameterized in query builder",
            },
            {
                "severity": "medium",
                "title": "Missing CORS headers",
                "content": "API endpoints lack CORS configuration",
            },
        ]
        result = format_concerns_for_architect(concerns, "infosec")
        assert "INFOSEC" in result
        assert "SQL Injection" in result
        assert "CORS" in result
        assert "Revision Required" in result

    def test_handles_missing_fields(self):
        concerns = [{"severity": "low"}]
        result = format_concerns_for_architect(concerns, "performance")
        assert "PERFORMANCE" in result


# ---------------------------------------------------------------------------
# AC code checking (Item 3.7)
# ---------------------------------------------------------------------------


class TestACCodeChecking:
    def test_detects_ac_codes(self):
        spec = """
## Authentication
AC-AUTH-1: User can log in with email/password
AC-AUTH-2: Failed login returns 401

## Dashboard
AC-DASH-1: Dashboard loads within 2 seconds
"""
        result = check_spec_for_ac_codes(spec)
        assert result["has_ac"] is True
        assert result["ac_count"] == 3
        assert "AC-AUTH-1" in result["ac_codes"]
        assert "AC-DASH-1" in result["ac_codes"]

    def test_no_ac_codes(self):
        spec = "## Feature\nJust implement it\n"
        result = check_spec_for_ac_codes(spec)
        assert result["has_ac"] is False
        assert result["ac_count"] == 0

    def test_detects_sections_without_ac(self):
        spec = """
## Authentication
AC-AUTH-1: Login works

## Logging
Just add logging everywhere
"""
        result = check_spec_for_ac_codes(spec)
        assert "Logging" in result["sections_without_ac"]

    def test_empty_spec(self):
        result = check_spec_for_ac_codes("")
        assert result["has_ac"] is False
        assert result["ac_codes"] == []
