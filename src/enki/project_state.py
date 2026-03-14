"""Project-scoped workflow state backed by ~/.enki/projects/{name}/em.db."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path

import enki.db as db
from enki.orch.schemas import create_tables

STATE_KEYS = {"phase", "tier", "goal", "goal_id", "spec_source", "spec_path"}
DEFAULT_PROJECT = "default"

_initialized_projects: set[str] = set()


def normalize_project_name(project: str | None) -> str:
    name = (project or "").strip()
    if not name or name == ".":
        return DEFAULT_PROJECT
    return name


def project_db_path(project: str | None) -> Path:
    return db.ENKI_ROOT / "projects" / normalize_project_name(project) / "em.db"


@contextmanager
def project_em_db(project: str | None):
    name = normalize_project_name(project)
    path = project_db_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache_key = str(path.resolve())
    if cache_key not in _initialized_projects:
        with db.connect(path) as conn:
            create_tables(conn)
        _initialized_projects.add(cache_key)
    with db.connect(path) as conn:
        yield conn


def read_project_state(project: str | None, key: str, default: str | None = None) -> str | None:
    if key not in STATE_KEYS:
        raise ValueError(f"Unsupported project_state key: {key}")
    with project_em_db(project) as conn:
        row = conn.execute(
            "SELECT value FROM project_state WHERE key = ? LIMIT 1",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def write_project_state(project: str | None, key: str, value: str) -> None:
    if key not in STATE_KEYS:
        raise ValueError(f"Unsupported project_state key: {key}")
    with project_em_db(project) as conn:
        conn.execute(
            "INSERT INTO project_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )


def read_all_project_state(project: str | None) -> dict[str, str | None]:
    return {
        "phase": read_project_state(project, "phase"),
        "tier": read_project_state(project, "tier"),
        "goal": read_project_state(project, "goal"),
        "goal_id": read_project_state(project, "goal_id"),
        "spec_source": read_project_state(project, "spec_source"),
        "spec_path": read_project_state(project, "spec_path"),
    }


def stable_goal_id(project: str | None) -> str:
    """Deterministic goal_id derived from project name."""
    normalized = normalize_project_name(project)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def resolve_project_from_cwd(cwd: str) -> str | None:
    """Resolve project from cwd using longest path-prefix match in wisdom.db projects."""
    path = Path(cwd).expanduser()
    try:
        normalized_cwd = str(path.resolve())
    except OSError:
        normalized_cwd = str(path)

    try:
        with db.wisdom_db() as conn:
            rows = conn.execute(
                "SELECT name, path FROM projects WHERE path IS NOT NULL AND TRIM(path) != ''"
            ).fetchall()
    except Exception:
        return None

    best_name: str | None = None
    best_len = -1
    for row in rows:
        proj_path = str(row["path"] or "").strip()
        if not proj_path:
            continue
        try:
            normalized_proj = str(Path(proj_path).expanduser().resolve())
        except OSError:
            normalized_proj = proj_path
        if normalized_cwd == normalized_proj or normalized_cwd.startswith(normalized_proj + "/"):
            if len(normalized_proj) > best_len:
                best_len = len(normalized_proj)
                best_name = normalize_project_name(row["name"])
    return best_name


def deprecate_global_project_marker() -> None:
    """Rename legacy ~/.enki/PROJECT marker to ~/.enki/PROJECT.deprecated."""
    legacy = db.ENKI_ROOT / "PROJECT"
    deprecated = db.ENKI_ROOT / "PROJECT.deprecated"
    if legacy.exists() and not deprecated.exists():
        deprecated.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(deprecated)
