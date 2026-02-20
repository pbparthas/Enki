"""Tests for v4 agent output templates (Item 3.3).

Tests template generation, output validation, retry prompts,
and parse failure escalation.
"""

import json

import pytest

from enki.orch.output_templates import (
    AGENT_EXTENSIONS,
    STANDARD_TEMPLATE,
    build_retry_prompt,
    get_template,
    get_template_instruction,
    validate_output,
)


# ---------------------------------------------------------------------------
# get_template
# ---------------------------------------------------------------------------


class TestGetTemplate:
    def test_standard_fields_present(self):
        template = get_template("dev")
        for field in STANDARD_TEMPLATE:
            assert field in template

    def test_dev_has_verification_commands(self):
        template = get_template("dev")
        assert "verification_commands" in template
        assert "tests_run" in template

    def test_qa_has_test_cases(self):
        template = get_template("qa")
        assert "test_cases" in template
        assert "coverage_summary" in template

    def test_architect_has_spec_sections(self):
        template = get_template("architect")
        assert "spec_sections" in template
        assert "acceptance_criteria" in template

    def test_reviewer_has_issues(self):
        template = get_template("reviewer")
        assert "issues" in template
        assert "approved" in template

    def test_infosec_has_vulnerabilities(self):
        template = get_template("infosec")
        assert "vulnerabilities" in template
        assert "risk_level" in template

    def test_unknown_role_returns_standard_only(self):
        template = get_template("unknown_role")
        assert set(template.keys()) == set(STANDARD_TEMPLATE.keys())

    def test_all_agent_extensions_covered(self):
        expected = {
            "dev", "qa", "architect", "reviewer", "infosec",
            "validator", "devops", "dba", "pm", "performance",
            "researcher", "ui_ux",
        }
        assert set(AGENT_EXTENSIONS.keys()) == expected


# ---------------------------------------------------------------------------
# get_template_instruction
# ---------------------------------------------------------------------------


class TestGetTemplateInstruction:
    def test_returns_json_instruction(self):
        instruction = get_template_instruction("dev")
        assert "valid JSON" in instruction
        assert "template" in instruction.lower()

    def test_instruction_contains_template(self):
        instruction = get_template_instruction("dev")
        # Should contain the actual template as JSON
        assert '"agent"' in instruction
        assert '"status"' in instruction
        assert '"verification_commands"' in instruction

    def test_instruction_forbids_markdown(self):
        instruction = get_template_instruction("qa")
        assert "markdown" in instruction.lower()


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def test_valid_output_passes(self):
        output = json.dumps({
            "agent": "dev",
            "task_id": "task-1",
            "status": "DONE",
            "summary": "Implemented feature",
        })
        result = validate_output(output, "dev")
        assert result["valid"] is True
        assert result["parsed"]["agent"] == "dev"
        assert result["error"] is None

    def test_missing_required_fields(self):
        output = json.dumps({"summary": "something"})
        result = validate_output(output, "dev")
        assert result["valid"] is False
        assert "agent" in result["missing_fields"]
        assert "task_id" in result["missing_fields"]
        assert "status" in result["missing_fields"]

    def test_invalid_json(self):
        result = validate_output("not json at all", "dev")
        assert result["valid"] is False
        assert result["parsed"] is None
        assert "parse error" in result["error"].lower()

    def test_non_object_json(self):
        result = validate_output("[1, 2, 3]", "dev")
        assert result["valid"] is False
        assert "object" in result["error"].lower()

    def test_markdown_fenced_json(self):
        output = '```json\n{"agent": "dev", "task_id": "t1", "status": "DONE"}\n```'
        result = validate_output(output, "dev")
        assert result["valid"] is True

    def test_invalid_status_value(self):
        output = json.dumps({
            "agent": "dev",
            "task_id": "t1",
            "status": "RUNNING",
        })
        result = validate_output(output, "dev")
        assert result["valid"] is False

    def test_lowercase_status_accepted(self):
        output = json.dumps({
            "agent": "dev",
            "task_id": "t1",
            "status": "done",
        })
        result = validate_output(output, "dev")
        assert result["valid"] is True

    def test_blocked_status_valid(self):
        output = json.dumps({
            "agent": "dev",
            "task_id": "t1",
            "status": "BLOCKED",
        })
        result = validate_output(output, "dev")
        assert result["valid"] is True

    def test_failed_status_valid(self):
        output = json.dumps({
            "agent": "dev",
            "task_id": "t1",
            "status": "FAILED",
        })
        result = validate_output(output, "dev")
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# build_retry_prompt
# ---------------------------------------------------------------------------


class TestBuildRetryPrompt:
    def test_attempt_1_gentle_reminder(self):
        prompt = build_retry_prompt("original", "parse error", "dev", 1)
        assert "IMPORTANT" in prompt
        assert "parse error" in prompt
        assert "original" in prompt

    def test_attempt_2_includes_template(self):
        prompt = build_retry_prompt("original", "missing fields", "dev", 2)
        assert "CRITICAL" in prompt
        assert '"agent"' in prompt  # Template included
        assert '"verification_commands"' in prompt

    def test_attempt_3_returns_empty(self):
        prompt = build_retry_prompt("original", "error", "dev", 3)
        assert prompt == ""

    def test_attempt_4_returns_empty(self):
        prompt = build_retry_prompt("original", "error", "dev", 4)
        assert prompt == ""
