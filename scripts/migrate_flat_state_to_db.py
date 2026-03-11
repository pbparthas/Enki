#!/usr/bin/env python3
"""One-time migration: flat PHASE/TIER/GOAL files -> project_state table."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


STATE_FILES = ("PHASE", "TIER", "GOAL")
KEY_MAP = {"PHASE": "phase", "TIER": "tier", "GOAL": "goal"}


def _enki_root() -> Path:
    return Path(os.environ.get("ENKI_ROOT", str(Path.home() / ".enki")))


def _ensure_project_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_state (
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (key)
        )
        """
    )


def _discover_projects(root: Path) -> list[str]:
    names: set[str] = {"default"}
    projects_dir = root / "projects"
    if projects_dir.exists():
        for entry in projects_dir.iterdir():
            if entry.is_dir() and (entry / "em.db").exists():
                names.add(entry.name)

    wisdom = root / "db" / "wisdom.db"
    if not wisdom.exists():
        legacy = root / "wisdom.db"
        if legacy.exists():
            wisdom = legacy
    if wisdom.exists():
        conn = sqlite3.connect(str(wisdom))
        try:
            rows = conn.execute("SELECT name FROM projects").fetchall()
            for row in rows:
                if row and row[0]:
                    names.add(str(row[0]))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return sorted(names)


def _migrate_bare_projects_db(root: Path) -> None:
    bare = root / "projects" / "em.db"
    target = root / "projects" / "default" / "em.db"
    if not bare.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        print(f"skip bare db rename (target exists): {target}")
        return
    bare.rename(target)
    print(f"renamed bare projects db: {bare} -> {target}")


def _flat_paths_for_project(root: Path, project: str) -> list[Path]:
    paths = [root / "projects" / project / name for name in STATE_FILES]
    if project == "default":
        paths.extend(root / name for name in STATE_FILES)
    return paths


def _write_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO project_state (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _migrate_project(root: Path, project: str) -> None:
    db_path = root / "projects" / project / "em.db"
    if not db_path.exists():
        print(f"skip project '{project}' (no em.db)")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_project_state_table(conn)
        migrated_any = False
        for src in _flat_paths_for_project(root, project):
            if not src.exists():
                continue
            name = src.name
            key = KEY_MAP.get(name)
            if not key:
                continue
            value = src.read_text().strip()
            if value:
                _write_state(conn, key, value)
                print(f"migrated {src} -> project_state[{key}] for '{project}'")
                migrated_any = True
            dst = src.with_name(f"{name}.migrated")
            if not dst.exists():
                src.rename(dst)
                print(f"renamed {src} -> {dst}")
            else:
                print(f"kept existing migrated file: {dst}")
        conn.commit()
        if not migrated_any:
            print(f"no flat state files for '{project}'")
    finally:
        conn.close()


def _ensure_table_for_all_project_dbs(root: Path) -> None:
    projects_dir = root / "projects"
    if not projects_dir.exists():
        print("projects directory missing; pass 2 skipped")
        return
    for entry in projects_dir.iterdir():
        if not entry.is_dir():
            continue
        db_path = entry / "em.db"
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path))
        try:
            _ensure_project_state_table(conn)
            count_row = conn.execute(
                "SELECT COUNT(*) FROM project_state"
            ).fetchone()
            count = int(count_row[0]) if count_row else 0
            if count == 0:
                _write_state(conn, "phase", "none")
                _write_state(conn, "tier", "minimal")
                _write_state(conn, "goal", "none")
                print(f"seeded default project_state for '{entry.name}'")
            else:
                print(f"project_state already populated for '{entry.name}'")
            conn.commit()
            print(f"ensured project_state table for '{entry.name}'")
        finally:
            conn.close()


def main() -> None:
    root = _enki_root()
    projects_dir = root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    _migrate_bare_projects_db(root)

    for project in _discover_projects(root):
        _migrate_project(root, project)

    # Pass 2: ensure project_state table exists and defaults are seeded
    # for existing em.db files that had no flat files.
    _ensure_table_for_all_project_dbs(root)


if __name__ == "__main__":
    main()
