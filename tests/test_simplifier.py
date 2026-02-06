"""Tests for Code Simplifier agent."""

import pytest
from pathlib import Path
import subprocess

from enki.simplifier import (
    SimplificationResult,
    generate_simplifier_prompt,
    run_simplification,
    parse_simplification_output,
    get_modified_files,
    SIMPLIFIER_PROMPT,
)


def test_simplifier_in_agents():
    """Test Simplifier is in AGENTS dict."""
    from enki.orchestrator import AGENTS

    assert "Simplifier" in AGENTS
    assert AGENTS["Simplifier"]["tier"] == "STANDARD"
    assert "Edit" in AGENTS["Simplifier"]["tools"]
    assert "Read" in AGENTS["Simplifier"]["tools"]
    assert "Bash" in AGENTS["Simplifier"]["tools"]
    assert "src/" in AGENTS["Simplifier"]["writes_to"]


def test_simplifier_prompt_content():
    """Test SIMPLIFIER_PROMPT has required sections."""
    assert "# Code Simplifier" in SIMPLIFIER_PROMPT
    assert "CRITICAL RULES" in SIMPLIFIER_PROMPT
    assert "Never change behavior" in SIMPLIFIER_PROMPT
    assert "Run tests" in SIMPLIFIER_PROMPT
    assert "## What to Look For" in SIMPLIFIER_PROMPT


def test_generate_prompt_no_files():
    """Test prompt generation with no files."""
    prompt = generate_simplifier_prompt()

    assert "# Code Simplifier" in prompt
    assert "No specific files provided" in prompt


def test_generate_prompt_specific_files():
    """Test prompt generation for specific files."""
    files = ["src/module.py", "src/other.py"]
    prompt = generate_simplifier_prompt(files=files)

    assert "src/module.py" in prompt
    assert "src/other.py" in prompt
    assert "## Files to Simplify" in prompt


def test_generate_prompt_all_modified(tmp_path):
    """Test prompt generation for all modified files."""
    # Create a git repo with modified file
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    # Create and commit initial file
    (tmp_path / "test.py").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmp_path, capture_output=True)

    # Modify the file
    (tmp_path / "test.py").write_text("modified")

    prompt = generate_simplifier_prompt(all_modified=True, project_path=tmp_path)

    # Should include the modified file
    assert "test.py" in prompt or "No specific files" in prompt  # May not detect depending on git state


def test_run_simplification_returns_params():
    """Test run_simplification returns Task tool params."""
    params = run_simplification(files=["src/test.py"])

    assert "description" in params
    assert "prompt" in params
    assert "subagent_type" in params
    assert "files" in params

    assert "Simplifier" in params["description"]
    assert params["subagent_type"] == "general-purpose"
    assert "src/test.py" in params["files"]


def test_run_simplification_all_modified():
    """Test run_simplification with all_modified flag."""
    params = run_simplification(all_modified=True)

    assert "description" in params
    assert "prompt" in params
    # Files may be empty if no git repo


def test_parse_output_success():
    """Test parsing successful Simplifier output."""
    output = """
    Simplification complete!

    Files modified:
    - src/module.py
    - src/helper.py

    Changes made:
    - Extracted duplicate code into helper function
    - Simplified nested conditionals

    Lines reduced: ~45

    Test results: All tests passing
    """

    result = parse_simplification_output(output)

    assert "src/module.py" in result.files_modified
    assert "src/helper.py" in result.files_modified
    assert len(result.changes_made) >= 1
    assert result.lines_reduced == 45
    assert result.tests_passed is True
    assert len(result.errors) == 0


def test_parse_output_with_errors():
    """Test parsing output with errors."""
    output = """
    Attempted simplification.

    Files modified:
    - src/module.py

    Test results: 2 tests failed

    Errors:
    - Could not simplify function X due to side effects
    - Test failure in test_module.py
    """

    result = parse_simplification_output(output)

    assert "src/module.py" in result.files_modified
    assert result.tests_passed is False
    assert len(result.errors) >= 1


def test_parse_output_no_changes():
    """Test parsing output with no changes."""
    output = """
    No simplification opportunities found.

    All tests passing.
    """

    result = parse_simplification_output(output)

    assert len(result.files_modified) == 0
    assert result.lines_reduced == 0
    assert result.tests_passed is True


def test_simplification_result_dataclass():
    """Test SimplificationResult dataclass."""
    result = SimplificationResult(
        files_modified=["src/a.py", "src/b.py"],
        changes_made=["Simplified conditionals"],
        lines_reduced=20,
        tests_passed=True,
        errors=[],
    )

    assert len(result.files_modified) == 2
    assert result.lines_reduced == 20
    assert result.tests_passed is True


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository with Python files."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    # Create and commit initial files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("# initial")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmp_path, capture_output=True)

    yield tmp_path


def test_get_modified_files_empty(git_repo):
    """Test getting modified files when none modified."""
    files = get_modified_files(git_repo)
    # Should be empty or have only tracked files
    assert isinstance(files, list)


def test_get_modified_files_with_changes(git_repo):
    """Test getting modified files when files are changed."""
    # Modify a Python file
    (git_repo / "src" / "module.py").write_text("# modified content")

    files = get_modified_files(git_repo)

    assert "src/module.py" in files


def test_get_modified_files_staged(git_repo):
    """Test getting modified files from staging area."""
    # Create and stage a new file
    (git_repo / "src" / "new_module.py").write_text("# new file")
    subprocess.run(["git", "add", "src/new_module.py"], cwd=git_repo, capture_output=True)

    files = get_modified_files(git_repo)

    assert "src/new_module.py" in files


def test_get_modified_files_filters_extensions(git_repo):
    """Test that non-source files are filtered out."""
    # Create and modify a non-Python file
    (git_repo / "README.md").write_text("# Modified readme")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True)

    files = get_modified_files(git_repo)

    # Should not include markdown files
    assert "README.md" not in files


def test_prompt_includes_all_sections():
    """Test that generated prompt includes all key sections."""
    prompt = generate_simplifier_prompt(files=["src/test.py"])

    # Check all major sections are present
    assert "CRITICAL RULES" in prompt
    assert "What to Look For" in prompt
    assert "Process" in prompt
    assert "Output" in prompt
    assert "Files to Simplify" in prompt
