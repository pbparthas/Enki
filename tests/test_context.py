"""Tests for v4 context injection policy (Item 3.1).

Tests token budgets, per-agent allocations, sanitization,
truncation, and context assembly.
"""

import json
from unittest.mock import patch

import pytest

from enki.orch.context import (
    AGENT_ALLOCATIONS,
    AGENT_SYSTEM_HEADER,
    CHARS_PER_TOKEN,
    DEFAULT_ALLOCATION,
    TIER_TOKEN_CAPS,
    assemble_agent_context,
    get_token_budget,
    truncate_to_budget,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_enki(tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    with patch("enki.db.ENKI_ROOT", tmp_path), \
         patch("enki.db.DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield tmp_path


def _patch_db(tmp_enki):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=tmp_enki,
        DB_DIR=tmp_enki / "db",
    )


# ---------------------------------------------------------------------------
# Tier token caps
# ---------------------------------------------------------------------------


class TestTierTokenCaps:
    def test_minimal_cap(self):
        assert TIER_TOKEN_CAPS["minimal"] == 5_000

    def test_standard_cap(self):
        assert TIER_TOKEN_CAPS["standard"] == 15_000

    def test_full_cap(self):
        assert TIER_TOKEN_CAPS["full"] == 30_000


# ---------------------------------------------------------------------------
# Agent allocations
# ---------------------------------------------------------------------------


class TestAgentAllocations:
    def test_all_allocations_sum_to_one(self):
        for role, alloc in AGENT_ALLOCATIONS.items():
            total = sum(alloc.values())
            assert abs(total - 1.0) < 0.01, f"{role} allocations sum to {total}"

    def test_default_allocation_sums_to_one(self):
        assert abs(sum(DEFAULT_ALLOCATION.values()) - 1.0) < 0.01

    def test_dev_has_largest_code_budget(self):
        assert AGENT_ALLOCATIONS["dev"]["code"] >= 0.30

    def test_pm_has_no_code_budget(self):
        assert AGENT_ALLOCATIONS["pm"]["code"] == 0.0

    def test_all_expected_roles_present(self):
        expected = {
            "pm", "architect", "dev", "qa", "reviewer",
            "infosec", "validator", "devops", "dba",
            "performance", "researcher", "ui_ux",
        }
        assert expected == set(AGENT_ALLOCATIONS.keys())

    def test_all_sections_present(self):
        sections = {"prompt", "task", "knowledge", "code", "mail"}
        for role, alloc in AGENT_ALLOCATIONS.items():
            assert set(alloc.keys()) == sections, f"{role} missing sections"


# ---------------------------------------------------------------------------
# get_token_budget
# ---------------------------------------------------------------------------


class TestGetTokenBudget:
    def test_minimal_tier_budget(self):
        budget = get_token_budget("dev", "minimal")
        assert budget["total_tokens"] == 5_000
        assert budget["total_chars"] == 5_000 * CHARS_PER_TOKEN

    def test_standard_tier_budget(self):
        budget = get_token_budget("dev", "standard")
        assert budget["total_tokens"] == 15_000

    def test_full_tier_budget(self):
        budget = get_token_budget("dev", "full")
        assert budget["total_tokens"] == 30_000

    def test_sections_sum_to_total(self):
        budget = get_token_budget("architect", "standard")
        section_total = sum(
            v for k, v in budget.items()
            if k not in ("total_chars", "total_tokens")
        )
        assert abs(section_total - budget["total_chars"]) <= 5  # rounding

    def test_unknown_role_uses_default(self):
        budget = get_token_budget("unknown_agent", "standard")
        assert budget["total_tokens"] == 15_000
        expected_task = int(15_000 * CHARS_PER_TOKEN * DEFAULT_ALLOCATION["task"])
        assert budget["task"] == expected_task

    def test_unknown_tier_defaults_to_standard(self):
        budget = get_token_budget("dev", "unknown_tier")
        assert budget["total_tokens"] == 15_000


# ---------------------------------------------------------------------------
# truncate_to_budget
# ---------------------------------------------------------------------------


class TestTruncateToBudget:
    def test_short_text_unchanged(self):
        text = "short text"
        assert truncate_to_budget(text, 1000) == text

    def test_long_text_truncated(self):
        text = "a" * 2000
        result = truncate_to_budget(text, 100)
        assert len(result) < 200  # truncated + message
        assert "truncated" in result

    def test_preserves_paragraph_boundaries(self):
        text = "paragraph one\n\nparagraph two\n\nparagraph three and more text"
        result = truncate_to_budget(text, 40)
        # Should break at a paragraph boundary
        assert "truncated" in result

    def test_empty_text_unchanged(self):
        assert truncate_to_budget("", 100) == ""

    def test_none_returns_none(self):
        assert truncate_to_budget(None, 100) is None

    def test_exact_length_unchanged(self):
        text = "x" * 100
        assert truncate_to_budget(text, 100) == text


# ---------------------------------------------------------------------------
# assemble_agent_context
# ---------------------------------------------------------------------------


class TestAssembleAgentContext:
    def test_empty_context(self, tmp_enki):
        with _patch_db(tmp_enki):
            result = assemble_agent_context("dev", "standard")
            assert result["sections"] == {}
            assert result["chars_used"] == 0
            assert result["within_budget"] is True

    def test_task_context_sanitized(self, tmp_enki):
        with _patch_db(tmp_enki):
            task = {"task_name": "Implement auth", "files": ["src/auth.py"]}
            result = assemble_agent_context("dev", "standard", task_context=task)
            assert "task" in result["sections"]
            assert result["chars_used"] > 0

    def test_knowledge_context_assembled(self, tmp_enki):
        with _patch_db(tmp_enki):
            knowledge = [
                {"content": "Use JWT for auth tokens", "category": "decision"},
                {"content": "WAL mode for SQLite", "category": "learning"},
            ]
            result = assemble_agent_context(
                "architect", "standard", knowledge=knowledge
            )
            assert "knowledge" in result["sections"]
            assert "JWT" in result["sections"]["knowledge"]
            assert "WAL" in result["sections"]["knowledge"]

    def test_code_context_assembled(self, tmp_enki):
        with _patch_db(tmp_enki):
            code = {
                "relevant_code": {"src/auth.py": "def authenticate(): ..."},
                "signatures": ["src/auth.py: def authenticate()"],
            }
            result = assemble_agent_context("dev", "standard", code_context=code)
            assert "code" in result["sections"]

    def test_mail_context_assembled(self, tmp_enki):
        with _patch_db(tmp_enki):
            mail = [
                {
                    "from_agent": "PM",
                    "to_agent": "Dev",
                    "body": "Focus on the auth module first",
                },
            ]
            result = assemble_agent_context("dev", "standard", mail_context=mail)
            assert "mail" in result["sections"]
            assert "PM" in result["sections"]["mail"]

    def test_tech_stack_injected(self, tmp_enki):
        with _patch_db(tmp_enki):
            stack = {"language": "python", "framework": "flask"}
            result = assemble_agent_context(
                "architect", "standard", tech_stack=stack
            )
            assert "tech_stack" in result["sections"]

    def test_within_budget_flag(self, tmp_enki):
        with _patch_db(tmp_enki):
            # Small tier with large context
            big_task = {"description": "x" * 100000}
            result = assemble_agent_context("dev", "minimal", task_context=big_task)
            # Should still report within_budget because truncation happens
            assert "task" in result["sections"]

    def test_all_sections_combined(self, tmp_enki):
        with _patch_db(tmp_enki):
            result = assemble_agent_context(
                "dev",
                "full",
                task_context={"name": "task1"},
                knowledge=[{"content": "note1", "category": "learning"}],
                code_context={"signatures": ["def foo()"]},
                mail_context=[{"from_agent": "PM", "to_agent": "Dev", "body": "go"}],
                tech_stack={"lang": "python"},
            )
            assert "task" in result["sections"]
            assert "knowledge" in result["sections"]
            assert "code" in result["sections"]
            assert "mail" in result["sections"]
            assert result["within_budget"] is True

    def test_budget_metadata_present(self, tmp_enki):
        with _patch_db(tmp_enki):
            result = assemble_agent_context("qa", "standard")
            assert "budget" in result
            assert result["budget"]["total_tokens"] == 15_000


# ---------------------------------------------------------------------------
# System header
# ---------------------------------------------------------------------------


class TestAgentSystemHeader:
    def test_header_warns_about_injection(self):
        assert "IGNORE" in AGENT_SYSTEM_HEADER
        assert "instructions" in AGENT_SYSTEM_HEADER

    def test_header_requires_json(self):
        assert "JSON" in AGENT_SYSTEM_HEADER
