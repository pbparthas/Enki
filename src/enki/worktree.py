"""Git worktree management for parallel task isolation.

Enables running multiple agent tasks in parallel, each in its own isolated
git worktree with a separate branch.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
import re
import subprocess
import shutil


# Shell metacharacters that indicate injection attempts
_SHELL_METACHAR_RE = re.compile(r'[;|&$`()\n]')

# Valid task_id: alphanumeric, hyphens, underscores only
_VALID_TASK_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


def validate_task_id(task_id: str) -> str:
    """Validate task_id for safe use in paths and branch names.

    Args:
        task_id: Task identifier to validate

    Returns:
        The validated task_id

    Raises:
        ValueError: If task_id contains unsafe characters
    """
    if not task_id:
        raise ValueError("task_id cannot be empty")
    if not _VALID_TASK_ID_RE.match(task_id):
        raise ValueError(
            f"Invalid task_id: {task_id!r}. "
            f"Only alphanumeric characters, hyphens, and underscores are allowed."
        )
    if ".." in task_id:
        raise ValueError(f"Invalid task_id: {task_id!r}. Path traversal not allowed.")
    return task_id


def validate_command(command: list[str]) -> list[str]:
    """Validate command args for shell metacharacter injection.

    Args:
        command: Command as list of strings

    Returns:
        The validated command

    Raises:
        ValueError: If any argument contains shell metacharacters
    """
    if not command:
        raise ValueError("Command cannot be empty")
    for arg in command:
        if _SHELL_METACHAR_RE.search(arg):
            raise ValueError(
                f"Shell metacharacters not allowed in command arguments: {arg!r}. "
                f"Characters ;|&$`() and newlines are rejected."
            )
    return command


# P3-24: Track background processes to prevent leaks
_active_processes: dict[str, subprocess.Popen] = {}


def _track_process(task_id: str, proc: subprocess.Popen) -> None:
    """Register a background process for cleanup tracking."""
    # Clean up any completed processes first
    dead = [tid for tid, p in _active_processes.items() if p.poll() is not None]
    for tid in dead:
        del _active_processes[tid]
    _active_processes[task_id] = proc


def cleanup_processes(timeout: int = 10) -> int:
    """Terminate and clean up tracked background processes (P3-24).

    Args:
        timeout: Seconds to wait for graceful termination

    Returns:
        Number of processes cleaned up
    """
    cleaned = 0
    for task_id, proc in list(_active_processes.items()):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
            cleaned += 1
        del _active_processes[task_id]
    return cleaned


@dataclass
class Worktree:
    """A git worktree instance."""
    task_id: str
    path: Path
    branch: str
    head: Optional[str]
    base_branch: str
    created_at: Optional[str] = None


def get_worktree_root(project_path: Path = None) -> Path:
    """Get the worktrees directory for a project.

    Args:
        project_path: Project directory (defaults to cwd)

    Returns:
        Path to the worktrees root directory (../project-worktrees/)
    """
    project_path = project_path or Path.cwd()
    return project_path.parent / f"{project_path.name}-worktrees"


def is_git_repo(path: Path) -> bool:
    """Check if path is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def create_worktree(
    task_id: str,
    branch_name: Optional[str] = None,
    base_branch: str = "main",
    project_path: Path = None,
) -> Path:
    """Create a git worktree for a task.

    Args:
        task_id: Task ID (used for directory name)
        branch_name: Branch name (defaults to enki/{task_id})
        base_branch: Base branch to branch from
        project_path: Project directory

    Returns:
        Path to the new worktree directory

    Raises:
        ValueError: If not in a git repository
        subprocess.CalledProcessError: If git command fails
    """
    project_path = project_path or Path.cwd()
    validate_task_id(task_id)

    if not is_git_repo(project_path):
        raise ValueError(f"Not a git repository: {project_path}")

    branch_name = branch_name or f"enki/{task_id}"

    # Worktrees go in ../project-worktrees/
    worktree_root = get_worktree_root(project_path)
    worktree_root.mkdir(exist_ok=True)

    worktree_path = worktree_root / task_id

    # Check if worktree already exists
    if worktree_path.exists():
        raise ValueError(f"Worktree already exists: {worktree_path}")

    # Create worktree with new branch
    subprocess.run(
        [
            "git", "worktree", "add",
            "-b", branch_name,
            str(worktree_path),
            base_branch,
        ],
        cwd=project_path,
        check=True,
        capture_output=True,
        text=True,
    )

    # Copy necessary config files
    copy_worktree_config(project_path, worktree_path)

    return worktree_path


# P3-28: Files to symlink (contain secrets) vs copy (safe to duplicate)
_SYMLINK_FILES = {".env", ".envrc"}


def copy_worktree_config(source: Path, target: Path) -> None:
    """Copy or symlink config files that worktree needs.

    P3-28: .env/.envrc are symlinked (contain secrets — avoid duplication).
    Other config files are copied.

    Args:
        source: Source project directory
        target: Target worktree directory
    """
    files_to_copy = [
        ".env",
        ".envrc",
        ".enki/SPEC.md",
        ".enki/PHASE",
        ".enki/TIER",
        ".enki/GOAL",
    ]

    for f in files_to_copy:
        src = source / f
        if src.exists() and src.is_file():
            dst = target / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            if Path(f).name in _SYMLINK_FILES:
                # Symlink secrets files to avoid copying sensitive data
                if not dst.exists():
                    dst.symlink_to(src.resolve())
            else:
                shutil.copy2(src, dst)


def list_worktrees(project_path: Path = None) -> list[Worktree]:
    """List all worktrees for this project.

    Args:
        project_path: Project directory

    Returns:
        List of Worktree objects
    """
    project_path = project_path or Path.cwd()

    if not is_git_repo(project_path):
        return []

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return []

    worktrees = []
    current: dict = {}

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif line == "bare":
            current["bare"] = True

    if current:
        worktrees.append(current)

    # Convert to Worktree objects
    worktree_root = get_worktree_root(project_path)
    result_list = []

    for w in worktrees:
        path = Path(w.get("path", ""))

        # Extract task_id from path if it's in our worktree directory
        task_id = ""
        if worktree_root in path.parents or path.parent == worktree_root:
            task_id = path.name

        branch = w.get("branch", "").replace("refs/heads/", "")

        result_list.append(Worktree(
            task_id=task_id,
            path=path,
            branch=branch,
            head=w.get("head"),
            base_branch="main",  # Can't easily determine this
        ))

    return result_list


def get_worktree(task_id: str, project_path: Path = None) -> Optional[Worktree]:
    """Get a specific worktree by task ID.

    Args:
        task_id: Task ID to find
        project_path: Project directory

    Returns:
        Worktree if found, None otherwise
    """
    trees = list_worktrees(project_path)
    for tree in trees:
        if tree.task_id == task_id:
            return tree
    return None


def remove_worktree(
    task_id: str,
    force: bool = False,
    project_path: Path = None,
) -> bool:
    """Remove a worktree after task completion.

    Args:
        task_id: Task ID
        force: Force removal even with uncommitted changes
        project_path: Project directory

    Returns:
        True if removed successfully
    """
    project_path = project_path or Path.cwd()
    worktree_root = get_worktree_root(project_path)
    worktree_path = worktree_root / task_id

    if not worktree_path.exists():
        return False

    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))

    result = subprocess.run(
        cmd,
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    return result.returncode == 0


def merge_worktree(
    task_id: str,
    target_branch: str = "main",
    delete_after: bool = True,
    project_path: Path = None,
) -> bool:
    """Merge worktree branch back to target.

    Args:
        task_id: Task ID
        target_branch: Branch to merge into
        delete_after: Remove worktree after merge
        project_path: Project directory

    Returns:
        True if merge succeeded
    """
    project_path = project_path or Path.cwd()
    branch_name = f"enki/{task_id}"

    # P1-08: Check for uncommitted changes before switching branches
    dirty_check = subprocess.run(
        ["git", "diff", "--quiet"],
        cwd=project_path,
        capture_output=True,
    )
    cached_check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=project_path,
        capture_output=True,
    )
    if dirty_check.returncode != 0 or cached_check.returncode != 0:
        raise ValueError(
            "Working tree has uncommitted changes. "
            "Commit or stash changes before merging worktree."
        )

    # Switch to target branch
    result = subprocess.run(
        ["git", "checkout", target_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False

    # Merge
    result = subprocess.run(
        ["git", "merge", "--no-ff", "-m", f"Merge {task_id}", branch_name],
        cwd=project_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False

    if delete_after:
        remove_worktree(task_id, project_path=project_path)
        # Delete branch
        subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=project_path,
            capture_output=True,
            text=True,
        )

    return True


def exec_in_worktree(
    task_id: str,
    command: list[str],
    project_path: Path = None,
    wait: bool = True,
) -> Union[subprocess.CompletedProcess, subprocess.Popen]:
    """Execute a command in a worktree directory.

    Args:
        task_id: Task ID
        command: Command to run (as list)
        project_path: Project directory
        wait: If True, wait for completion and return CompletedProcess.
              If False, return Popen immediately for async execution.

    Returns:
        CompletedProcess if wait=True, Popen if wait=False

    Raises:
        ValueError: If worktree doesn't exist
    """
    project_path = project_path or Path.cwd()
    validate_task_id(task_id)
    validate_command(command)
    worktree_root = get_worktree_root(project_path)
    worktree_path = worktree_root / task_id

    if not worktree_path.exists():
        raise ValueError(f"Worktree not found: {task_id}")

    if wait:
        return subprocess.run(
            command,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=300,  # P3-24: 5 minute timeout
        )
    else:
        proc = subprocess.Popen(
            command,
            cwd=worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _track_process(task_id, proc)
        return proc


def is_in_worktree(path: Path = None) -> bool:
    """Check if the current directory is a worktree (not main).

    Args:
        path: Path to check (defaults to cwd)

    Returns:
        True if in a worktree directory
    """
    path = path or Path.cwd()

    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False

    # If common dir is different from git dir, we're in a worktree
    common_dir = result.stdout.strip()

    git_dir_result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=path,
        capture_output=True,
        text=True,
    )

    git_dir = git_dir_result.stdout.strip()

    # Normalize paths for comparison
    return Path(common_dir).resolve() != Path(git_dir).resolve()


def get_worktree_state(project_path: Path = None) -> dict:
    """Get state of all worktrees (for STATUS.md).

    Args:
        project_path: Project directory

    Returns:
        Dict with worktree state information
    """
    project_path = project_path or Path.cwd()
    trees = list_worktrees(project_path)

    # Filter to only our managed worktrees
    worktree_root = get_worktree_root(project_path)
    managed_trees = [
        t for t in trees
        if t.task_id and worktree_root == t.path.parent
    ]

    return {
        "count": len(managed_trees),
        "worktrees": [
            {
                "task_id": t.task_id,
                "path": str(t.path),
                "branch": t.branch,
            }
            for t in managed_trees
        ],
    }
