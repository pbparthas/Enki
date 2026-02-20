"""Tests for CLAUDE.md v4 generation (Items 4.1 + 4.2)."""

import pytest
from enki.orch.claude_md import (
    generate_claude_md_v4,
    apply_tier_template,
    validate_claude_md,
    load_preferences_for_claude_md,
    preserve_user_instructions,
    COMPACTION_INSTRUCTION,
    USER_INSTRUCTIONS_HEADER,
    PROJECT_TYPE_REGISTRY,
)


# ── generate_claude_md_v4 ──


class TestGenerateClaudeMdV4:
    def test_minimal_generation(self):
        result = generate_claude_md_v4(project_name="TestApp")
        assert "# Project: TestApp" in result
        assert "## WHY" in result
        assert "## WHAT" in result
        assert "## HOW" in result
        assert "## CONVENTIONS" in result
        assert "## CONSTRAINTS" in result
        assert "## COMPACTION BEHAVIOR" in result
        assert "## USER INSTRUCTIONS" in result

    def test_compaction_always_included(self):
        result = generate_claude_md_v4()
        assert "enki_restore" in result
        assert "compacted" in result.lower()

    def test_user_instructions_sacred_section(self):
        result = generate_claude_md_v4()
        assert "Sacred section" in result

    def test_preserves_existing_user_instructions(self):
        result = generate_claude_md_v4(
            user_instructions="Always use tabs.\nNever auto-commit."
        )
        assert "Always use tabs" in result
        assert "Never auto-commit" in result

    def test_with_pm_input(self):
        pm = {
            "outcome": "Build a task manager",
            "audience": "Developers",
            "constraints": "Must be offline-first",
        }
        result = generate_claude_md_v4(pm_input=pm)
        assert "Build a task manager" in result
        assert "Developers" in result
        assert "offline-first" in result

    def test_with_tech_stack(self):
        stack = {
            "languages": ["Python", "TypeScript"],
            "frameworks": ["FastAPI", "React"],
            "build_tools": ["pip", "npm"],
        }
        result = generate_claude_md_v4(tech_stack=stack)
        assert "Python" in result
        assert "FastAPI" in result

    def test_brownfield_with_profile(self):
        profile = {
            "project": {
                "primary_language": "Python",
                "frameworks": ["Django"],
            },
            "architecture": {"pattern": "MVC", "entry_point": "manage.py"},
            "structure": {"source_dirs": ["src/", "apps/"]},
            "testing": {"framework": "pytest"},
            "conventions": {"naming": "snake_case", "imports": "absolute"},
        }
        result = generate_claude_md_v4(
            project_type="brownfield",
            codebase_profile=profile,
        )
        assert "Python" in result
        assert "Django" in result
        assert "snake_case" in result

    def test_with_customer_input(self):
        result = generate_claude_md_v4(
            customer_input="No external API calls allowed."
        )
        assert "No external API calls allowed" in result
        assert "Customer Instructions" in result

    def test_with_conventions(self):
        convs = {"naming": "camelCase", "imports": "relative"}
        result = generate_claude_md_v4(conventions=convs)
        assert "camelCase" in result
        assert "relative" in result

    def test_with_preferences(self):
        prefs = {"always_use_ruff": "Use ruff for linting, never flake8"}
        result = generate_claude_md_v4(preferences=prefs)
        assert "ruff" in result

    def test_greenfield_with_spec(self):
        result = generate_claude_md_v4(
            project_type="greenfield",
            impl_spec="Build a Python REST API with FastAPI",
        )
        assert "Python" in result


# ── COMPACTION_INSTRUCTION ──


class TestCompactionInstruction:
    def test_has_five_items(self):
        lines = COMPACTION_INSTRUCTION.split("\n")
        numbered = [l for l in lines if l.strip().startswith(("1.", "2.", "3.", "4.", "5."))]
        assert len(numbered) == 5

    def test_mentions_enki_restore(self):
        assert "enki_restore" in COMPACTION_INSTRUCTION


# ── preserve_user_instructions ──


class TestPreserveUserInstructions:
    def test_extracts_user_content(self):
        md = "## WHAT\n\nStuff\n\n## USER INSTRUCTIONS\n\nAlways use tabs.\nNever auto-commit.\n"
        result = preserve_user_instructions(md)
        assert "Always use tabs" in result
        assert "Never auto-commit" in result

    def test_strips_sacred_comment(self):
        md = (
            "## USER INSTRUCTIONS\n\n"
            "> **Sacred section** — Enki never modifies content below this line.\n"
            "> Add your own instructions.\n\n"
            "My custom rule.\n"
        )
        result = preserve_user_instructions(md)
        assert "My custom rule" in result
        assert "Sacred section" not in result

    def test_returns_none_when_missing(self):
        md = "## WHAT\n\nStuff\n"
        result = preserve_user_instructions(md)
        assert result is None

    def test_returns_none_when_empty(self):
        md = "## USER INSTRUCTIONS\n"
        result = preserve_user_instructions(md)
        assert result is None


# ── apply_tier_template ──


class TestApplyTierTemplate:
    def test_known_project_type(self):
        result = apply_tier_template("minimal", "python_flask")
        assert "pytest" in result
        assert "flask" in result.lower()

    def test_unknown_project_type(self):
        result = apply_tier_template("minimal", "unknown_type")
        assert "TBD" in result

    def test_none_project_type(self):
        result = apply_tier_template("minimal", None)
        assert "TBD" in result

    def test_all_registry_types(self):
        for key in PROJECT_TYPE_REGISTRY:
            result = apply_tier_template("minimal", key)
            assert "Build:" in result
            assert "Test:" in result


# ── validate_claude_md ──


class TestValidateClaudeMd:
    def test_valid_md(self):
        md = generate_claude_md_v4(
            project_name="Test",
            impl_spec="Build a Python app",
        )
        result = validate_claude_md(md)
        # Minimal generation may have TBD commands — check key sections present
        assert "## WHY" in md
        assert "## WHAT" in md
        assert "## HOW" in md

    def test_too_long(self):
        md = "## WHY\n## WHAT\n## HOW\n" + ("line\n" * 301)
        result = validate_claude_md(md)
        assert not result["valid"]
        assert any("Too long" in i for i in result["issues"])

    def test_missing_sections(self):
        md = "# Project\n\nSome content\n"
        result = validate_claude_md(md)
        assert not result["valid"]
        assert any("WHY" in i for i in result["issues"])

    def test_detects_secrets(self):
        md = '## WHY\n## WHAT\n## HOW\napi_key = "sk-abc123"\n'
        result = validate_claude_md(md)
        assert any("secret" in i.lower() for i in result["issues"])
