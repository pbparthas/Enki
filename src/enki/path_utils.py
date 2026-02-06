"""Path validation utilities — prevents path traversal attacks.

P1-02: All project_path entry points must validate that the resolved
path is within CWD or an approved project root.

P2-17: atomic_write helper + flock-based file locking for shared state files.

P3-08: normalize_timestamp() — shared utility replacing 6+ duplicate implementations.
"""

import fcntl
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


@contextmanager
def file_lock(filepath: Path, shared: bool = False):
    """Acquire an advisory file lock (fcntl.flock).

    P2-17: Prevents concurrent agents from corrupting shared state files
    during read-modify-write operations on STATE.md, RUNNING.md,
    EVOLUTION.md, .session_edits, etc.

    Args:
        filepath: The file to lock (a .lock sidecar is used)
        shared: If True, acquire a shared (read) lock; otherwise exclusive (write)

    Usage:
        with file_lock(Path("STATE.md")):
            content = Path("STATE.md").read_text()
            # ... modify content ...
            Path("STATE.md").write_text(new_content)
    """
    lock_path = Path(str(filepath) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        op = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(lock_fd, op)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


@contextmanager
def atomic_write(filepath: Path, mode: str = "w"):
    """Write to a file atomically using tmp + os.replace pattern.

    Acquires an exclusive file lock, then writes via temp file + os.replace.
    Prevents concurrent agents from corrupting shared state files
    (STATE.md, RUNNING.md, EVOLUTION.md, .session_edits, etc.).

    Usage:
        with atomic_write(Path("file.md")) as f:
            f.write("content")
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(filepath):
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent,
            prefix=f".{filepath.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, mode) as f:
                yield f
            os.replace(tmp_path, filepath)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def normalize_timestamp(
    ts: Union[str, int, float, datetime, None],
    default: datetime = None,
) -> datetime:
    """Normalize a timestamp to a timezone-aware UTC datetime (P3-08).

    Handles:
    - ISO-format strings (with or without tz)
    - SQLite timestamp strings (YYYY-MM-DD HH:MM:SS)
    - Unix timestamps (int/float)
    - datetime objects (adds UTC if naive)
    - None (returns default or now)

    Args:
        ts: Raw timestamp value from DB or input
        default: Fallback value (defaults to utcnow)

    Returns:
        Timezone-aware UTC datetime
    """
    if default is None:
        default = datetime.now(timezone.utc)

    if ts is None:
        return default

    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return default
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    return default


def validate_project_path(
    project_path: Optional[str],
    allowed_roots: Optional[list[Path]] = None,
) -> Optional[Path]:
    """Validate and resolve a project path, rejecting traversal attempts.

    Args:
        project_path: Raw project path string (from user/MCP input)
        allowed_roots: Additional allowed root directories.
            Always allows CWD and home directory.

    Returns:
        Resolved Path if valid, None if input was None/empty.

    Raises:
        ValueError: If path escapes allowed roots.
    """
    if not project_path:
        return None

    resolved = Path(project_path).resolve()

    # Build set of allowed roots
    roots = {
        Path.cwd().resolve(),
        Path.home().resolve(),
    }
    if allowed_roots:
        roots.update(r.resolve() for r in allowed_roots)

    # Check that resolved path is within at least one allowed root
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue

    raise ValueError(
        f"Path traversal rejected: {project_path!r} resolves to {resolved}, "
        f"which is outside allowed roots."
    )
