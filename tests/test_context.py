"""Tests for adaptive context loading."""

import pytest
from pathlib import Path
import json

from enki.context import (
    ContextTier,
    LoadedContext,
    detect_tier,
    load_context,
    format_context_for_injection,
    preview_context,
    set_default_tier,
    get_context_config,
    save_context_config,
)


@pytest.fixture
def enki_project(tmp_path):
    """Create a mock Enki project with various state files."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()

    (enki_dir / "PHASE").write_text("implement")
    (enki_dir / "GOAL").write_text("Implement feature X")
    (enki_dir / "TIER").write_text("feature")

    specs_dir = enki_dir / "specs"
    specs_dir.mkdir()
    (specs_dir / "feature-x.md").write_text("# Feature X\n\nThis is the spec for feature X.\n\n## Requirements\n- Requirement 1\n- Requirement 2")

    yield tmp_path


@pytest.fixture
def minimal_project(tmp_path):
    """Create a minimal project without .enki dir."""
    yield tmp_path


def test_context_tier_enum():
    """Test ContextTier enum values."""
    assert ContextTier.MINIMAL.value == "minimal"
    assert ContextTier.STANDARD.value == "standard"
    assert ContextTier.FULL.value == "full"
    assert ContextTier.AUTO.value == "auto"


def test_detect_tier_no_enki_dir(minimal_project):
    """Test tier detection with no .enki directory."""
    tier = detect_tier(minimal_project)
    assert tier == ContextTier.MINIMAL


def test_detect_tier_intake_phase(tmp_path):
    """Test tier detection for intake phase."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()
    (enki_dir / "PHASE").write_text("intake")

    tier = detect_tier(tmp_path)
    assert tier == ContextTier.MINIMAL


def test_detect_tier_plan_phase(tmp_path):
    """Test tier detection for plan phase."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()
    (enki_dir / "PHASE").write_text("plan")

    tier = detect_tier(tmp_path)
    assert tier == ContextTier.STANDARD


def test_detect_tier_implement_phase(enki_project):
    """Test tier detection for implement phase."""
    tier = detect_tier(enki_project)
    # With implement phase and no STATE.md or SCOPE, should be STANDARD
    assert tier == ContextTier.STANDARD


def test_detect_tier_with_many_tasks(tmp_path):
    """Test tier detection with many tasks."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()
    (enki_dir / "PHASE").write_text("implement")

    # Create STATE.md with many tasks
    tasks = "\n".join([f"- [ ] Task {i}" for i in range(15)])
    (enki_dir / "STATE.md").write_text(f"# Tasks\n\n{tasks}")

    tier = detect_tier(tmp_path)
    assert tier == ContextTier.FULL


def test_load_context_minimal(enki_project):
    """Test loading minimal context."""
    context = load_context(tier=ContextTier.MINIMAL, project_path=enki_project)

    assert context.tier == ContextTier.MINIMAL
    assert context.phase == "implement"
    assert context.goal == "Implement feature X"
    assert context.spec is None  # Not loaded in minimal
    assert context.token_estimate > 0


def test_load_context_standard(enki_project):
    """Test loading standard context."""
    context = load_context(tier=ContextTier.STANDARD, project_path=enki_project)

    assert context.tier == ContextTier.STANDARD
    assert context.phase == "implement"
    assert context.goal == "Implement feature X"
    assert context.spec is not None
    assert "Feature X" in context.spec


def test_load_context_auto(enki_project):
    """Test auto tier detection and loading."""
    context = load_context(tier=ContextTier.AUTO, project_path=enki_project)

    # Auto with implement phase and no STATE.md/SCOPE resolves to STANDARD
    assert context.tier == ContextTier.STANDARD
    assert context.phase == "implement"


def test_load_context_respects_max_tokens(enki_project):
    """Test that context loading respects max token limit."""
    context = load_context(
        tier=ContextTier.STANDARD,
        project_path=enki_project,
        max_tokens=100,
    )

    # Should truncate to stay under limit
    assert context.token_estimate <= 200  # Some buffer


def test_format_context_for_injection(enki_project):
    """Test context formatting for injection."""
    context = load_context(tier=ContextTier.STANDARD, project_path=enki_project)
    formatted = format_context_for_injection(context)

    assert "# Enki Context" in formatted
    assert "implement" in formatted.lower() or "IMPLEMENT" in formatted
    assert "Feature X" in formatted or "feature x" in formatted.lower()


def test_preview_context(enki_project):
    """Test context preview."""
    preview = preview_context(tier=ContextTier.AUTO, project_path=enki_project)

    assert "# Context Preview" in preview
    assert "Phase:" in preview
    assert "Goal:" in preview


def test_preview_context_minimal(minimal_project):
    """Test context preview for minimal project."""
    preview = preview_context(tier=ContextTier.MINIMAL, project_path=minimal_project)

    assert "# Context Preview" in preview
    assert "minimal" in preview.lower()


def test_get_context_config_defaults(tmp_path):
    """Test getting context config with defaults."""
    config = get_context_config(tmp_path)

    assert config["context_tier"] == "auto"
    assert config["context_max_tokens"] == 50000
    assert config["context_include_beads"] is True
    assert config["context_bead_limit"] == 10


def test_get_context_config_from_file(tmp_path):
    """Test getting context config from file."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()

    config_file = enki_dir / "config.json"
    config_file.write_text(json.dumps({
        "context_tier": "full",
        "context_max_tokens": 25000,
    }))

    config = get_context_config(tmp_path)

    assert config["context_tier"] == "full"
    assert config["context_max_tokens"] == 25000
    # Defaults should still be present
    assert config["context_include_beads"] is True


def test_save_context_config(tmp_path):
    """Test saving context config."""
    save_context_config({"context_tier": "minimal"}, tmp_path)

    config_file = tmp_path / ".enki" / "config.json"
    assert config_file.exists()

    saved = json.loads(config_file.read_text())
    assert saved["context_tier"] == "minimal"


def test_save_context_config_merge(tmp_path):
    """Test that saving config merges with existing."""
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir()

    config_file = enki_dir / "config.json"
    config_file.write_text(json.dumps({"existing_key": "value"}))

    save_context_config({"context_tier": "full"}, tmp_path)

    saved = json.loads(config_file.read_text())
    assert saved["context_tier"] == "full"
    assert saved["existing_key"] == "value"


def test_set_default_tier(tmp_path):
    """Test setting default tier."""
    set_default_tier(ContextTier.FULL, tmp_path)

    config = get_context_config(tmp_path)
    assert config["context_tier"] == "full"


def test_token_estimation(enki_project):
    """Test that token estimation is reasonable."""
    context = load_context(tier=ContextTier.STANDARD, project_path=enki_project)

    # Token estimate should be positive and reasonable
    assert context.token_estimate > 0
    assert context.token_estimate < 100000  # Sanity check


def test_loaded_context_dataclass():
    """Test LoadedContext dataclass."""
    context = LoadedContext(
        tier=ContextTier.STANDARD,
        phase="implement",
        goal="Test goal",
        spec="# Spec",
        token_estimate=1000,
    )

    assert context.tier == ContextTier.STANDARD
    assert context.phase == "implement"
    assert context.goal == "Test goal"
    assert context.beads == []  # Default empty list
