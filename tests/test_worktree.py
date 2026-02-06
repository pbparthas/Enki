"""Tests for worktree management."""

import pytest
from pathlib import Path
import subprocess
import tempfile
import os

from enki.worktree import (
    create_worktree,
    list_worktrees,
    remove_worktree,
    merge_worktree,
    exec_in_worktree,
    get_worktree_root,
    is_git_repo,
    is_in_worktree,
    get_worktree,
    copy_worktree_config,
    get_worktree_state,
    validate_task_id,
    validate_command,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository with 'main' as default branch."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, check=True, capture_output=True)

    yield repo


def test_is_git_repo(git_repo, tmp_path):
    """Test git repo detection."""
    assert is_git_repo(git_repo) is True
    assert is_git_repo(tmp_path) is False


def test_get_worktree_root(git_repo):
    """Test worktree root path calculation."""
    root = get_worktree_root(git_repo)
    assert root.name == "test_repo-worktrees"
    assert root.parent == git_repo.parent


def test_create_worktree(git_repo):
    """Test worktree creation."""
    path = create_worktree("task-001", project_path=git_repo)

    assert path.exists()
    assert (path / "file.txt").exists()
    assert (path / ".git").exists()  # Git worktree marker file

    # Cleanup
    remove_worktree("task-001", force=True, project_path=git_repo)


def test_create_worktree_with_custom_branch(git_repo):
    """Test worktree creation with custom branch name."""
    path = create_worktree("task-002", branch_name="custom/branch", project_path=git_repo)

    assert path.exists()

    # Check branch name
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "custom/branch"

    # Cleanup
    remove_worktree("task-002", force=True, project_path=git_repo)


def test_create_worktree_already_exists(git_repo):
    """Test error when worktree already exists."""
    create_worktree("task-003", project_path=git_repo)

    with pytest.raises(ValueError, match="already exists"):
        create_worktree("task-003", project_path=git_repo)

    # Cleanup
    remove_worktree("task-003", force=True, project_path=git_repo)


def test_list_worktrees(git_repo):
    """Test listing worktrees."""
    create_worktree("task-001", project_path=git_repo)
    create_worktree("task-002", project_path=git_repo)

    trees = list_worktrees(project_path=git_repo)

    # Should have main + 2 worktrees
    assert len(trees) >= 2

    task_ids = [t.task_id for t in trees]
    assert "task-001" in task_ids
    assert "task-002" in task_ids

    # Cleanup
    remove_worktree("task-001", force=True, project_path=git_repo)
    remove_worktree("task-002", force=True, project_path=git_repo)


def test_get_worktree(git_repo):
    """Test getting a specific worktree."""
    create_worktree("task-004", project_path=git_repo)

    tree = get_worktree("task-004", project_path=git_repo)
    assert tree is not None
    assert tree.task_id == "task-004"
    assert "enki/task-004" in tree.branch

    # Non-existent worktree
    assert get_worktree("nonexistent", project_path=git_repo) is None

    # Cleanup
    remove_worktree("task-004", force=True, project_path=git_repo)


def test_remove_worktree(git_repo):
    """Test worktree removal."""
    path = create_worktree("task-005", project_path=git_repo)
    assert path.exists()

    success = remove_worktree("task-005", project_path=git_repo)
    assert success is True
    assert not path.exists()


def test_remove_nonexistent_worktree(git_repo):
    """Test removing non-existent worktree."""
    success = remove_worktree("nonexistent", project_path=git_repo)
    assert success is False


def test_exec_in_worktree(git_repo):
    """Test command execution in worktree."""
    create_worktree("task-006", project_path=git_repo)

    result = exec_in_worktree("task-006", ["pwd"], project_path=git_repo)
    assert result.returncode == 0
    assert "task-006" in result.stdout

    # Cleanup
    remove_worktree("task-006", force=True, project_path=git_repo)


def test_exec_in_worktree_async(git_repo):
    """Test async command execution in worktree."""
    create_worktree("task-007", project_path=git_repo)

    proc = exec_in_worktree("task-007", ["sleep", "0.1"], project_path=git_repo, wait=False)
    assert proc.pid > 0

    # Wait for it to complete
    proc.wait()

    # Cleanup
    remove_worktree("task-007", force=True, project_path=git_repo)


def test_exec_in_nonexistent_worktree(git_repo):
    """Test exec in non-existent worktree."""
    with pytest.raises(ValueError, match="not found"):
        exec_in_worktree("nonexistent", ["pwd"], project_path=git_repo)


def test_copy_worktree_config(git_repo):
    """Test config file copying."""
    # Create some config files
    enki_dir = git_repo / ".enki"
    enki_dir.mkdir()
    (enki_dir / "PHASE").write_text("implement")
    (git_repo / ".env").write_text("TEST=1")

    # Create worktree
    worktree_path = create_worktree("task-008", project_path=git_repo)

    # Check files were copied
    assert (worktree_path / ".enki" / "PHASE").exists()
    assert (worktree_path / ".enki" / "PHASE").read_text() == "implement"
    assert (worktree_path / ".env").exists()
    assert (worktree_path / ".env").read_text() == "TEST=1"

    # Cleanup
    remove_worktree("task-008", force=True, project_path=git_repo)


def test_merge_worktree(git_repo):
    """Test worktree merge."""
    path = create_worktree("task-009", project_path=git_repo)

    # Make changes in worktree
    (path / "new_file.txt").write_text("new content")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], cwd=path, check=True, capture_output=True)

    # Merge back (keep worktree for verification)
    result = merge_worktree("task-009", delete_after=False, project_path=git_repo)
    assert result is True

    # Verify merged
    assert (git_repo / "new_file.txt").exists()

    # Cleanup
    remove_worktree("task-009", force=True, project_path=git_repo)


def test_get_worktree_state(git_repo):
    """Test worktree state reporting."""
    create_worktree("task-010", project_path=git_repo)
    create_worktree("task-011", project_path=git_repo)

    state = get_worktree_state(project_path=git_repo)

    assert state["count"] == 2
    assert len(state["worktrees"]) == 2

    task_ids = [w["task_id"] for w in state["worktrees"]]
    assert "task-010" in task_ids
    assert "task-011" in task_ids

    # Cleanup
    remove_worktree("task-010", force=True, project_path=git_repo)
    remove_worktree("task-011", force=True, project_path=git_repo)


def test_is_in_worktree(git_repo):
    """Test worktree detection."""
    # Main repo is not a worktree
    assert is_in_worktree(git_repo) is False

    # Create and check worktree
    path = create_worktree("task-012", project_path=git_repo)
    assert is_in_worktree(path) is True

    # Cleanup
    remove_worktree("task-012", force=True, project_path=git_repo)


# =============================================================================
# P0-12: Task ID validation (path traversal prevention)
# =============================================================================

class TestValidateTaskId:
    def test_valid_task_ids(self):
        """Valid task IDs are accepted."""
        assert validate_task_id("task-001") == "task-001"
        assert validate_task_id("my_task_123") == "my_task_123"
        assert validate_task_id("ABC-def-456") == "ABC-def-456"

    def test_path_traversal_rejected(self):
        """Path traversal attempts are rejected."""
        with pytest.raises(ValueError, match="Invalid task_id"):
            validate_task_id("../../etc/passwd")

    def test_slash_rejected(self):
        """Forward slashes are rejected."""
        with pytest.raises(ValueError, match="Invalid task_id"):
            validate_task_id("task/evil")

    def test_backslash_rejected(self):
        """Backslashes are rejected."""
        with pytest.raises(ValueError, match="Invalid task_id"):
            validate_task_id("task\\evil")

    def test_whitespace_rejected(self):
        """Whitespace is rejected."""
        with pytest.raises(ValueError, match="Invalid task_id"):
            validate_task_id("task evil")

    def test_empty_rejected(self):
        """Empty task_id is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_task_id("")

    def test_dotdot_rejected(self):
        """Double dot in task_id is rejected."""
        with pytest.raises(ValueError):
            validate_task_id("task..id")


# =============================================================================
# P0-11: Command validation (shell injection prevention)
# =============================================================================

class TestValidateCommand:
    def test_valid_commands(self):
        """Normal commands are accepted."""
        assert validate_command(["git", "status"]) == ["git", "status"]
        assert validate_command(["python", "-m", "pytest"]) == ["python", "-m", "pytest"]

    def test_semicolon_rejected(self):
        """Semicolons are rejected."""
        with pytest.raises(ValueError, match="Shell metacharacters"):
            validate_command(["echo", "hello; rm -rf /"])

    def test_pipe_rejected(self):
        """Pipes are rejected."""
        with pytest.raises(ValueError, match="Shell metacharacters"):
            validate_command(["cat", "file | evil"])

    def test_ampersand_rejected(self):
        """Ampersands are rejected."""
        with pytest.raises(ValueError, match="Shell metacharacters"):
            validate_command(["cmd", "arg & evil"])

    def test_dollar_rejected(self):
        """Dollar signs are rejected."""
        with pytest.raises(ValueError, match="Shell metacharacters"):
            validate_command(["echo", "$HOME"])

    def test_backtick_rejected(self):
        """Backticks are rejected."""
        with pytest.raises(ValueError, match="Shell metacharacters"):
            validate_command(["echo", "`whoami`"])

    def test_empty_rejected(self):
        """Empty command is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_command([])

    def test_create_worktree_rejects_bad_task_id(self, git_repo):
        """create_worktree rejects path traversal task_id."""
        with pytest.raises(ValueError, match="Invalid task_id"):
            create_worktree("../../etc", project_path=git_repo)
