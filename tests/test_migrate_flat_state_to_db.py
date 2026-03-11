import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from enki.db import connect
from enki.memory.schemas import create_tables as create_memory_tables
from enki.orch.schemas import create_tables as create_em_tables


SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_flat_state_to_db.py"


def _run_migration(enki_root: Path) -> None:
    env = dict(os.environ)
    env["ENKI_ROOT"] = str(enki_root)
    subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def test_migrates_flat_files_to_project_state_and_renames(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "projects" / "default").mkdir(parents=True)
    (enki_root / "db").mkdir(parents=True)

    with connect(enki_root / "projects" / "default" / "em.db") as conn:
        create_em_tables(conn)
    with connect(enki_root / "db" / "wisdom.db") as conn:
        create_memory_tables(conn, "wisdom")
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("default", "/tmp/default"))

    (enki_root / "PHASE").write_text("implement")
    (enki_root / "TIER").write_text("standard")
    (enki_root / "GOAL").write_text("Ship feature")

    _run_migration(enki_root)

    conn = sqlite3.connect(str(enki_root / "projects" / "default" / "em.db"))
    try:
        rows = conn.execute("SELECT key, value FROM project_state").fetchall()
    finally:
        conn.close()
    state = {k: v for k, v in rows}
    assert state["phase"] == "implement"
    assert state["tier"] == "standard"
    assert state["goal"] == "Ship feature"

    assert (enki_root / "PHASE").exists() is False
    assert (enki_root / "PHASE.migrated").exists()
    assert (enki_root / "TIER.migrated").exists()
    assert (enki_root / "GOAL.migrated").exists()

    # Idempotent re-run
    _run_migration(enki_root)


def test_renames_bare_projects_em_db_to_default(tmp_path):
    enki_root = tmp_path / ".enki"
    projects = enki_root / "projects"
    projects.mkdir(parents=True)
    (enki_root / "db").mkdir(parents=True)

    with connect(projects / "em.db") as conn:
        create_em_tables(conn)

    _run_migration(enki_root)

    assert not (projects / "em.db").exists()
    assert (projects / "default" / "em.db").exists()


def test_pass2_creates_and_seeds_project_state_for_existing_em_db(tmp_path):
    enki_root = tmp_path / ".enki"
    projects = enki_root / "projects" / "legacy-proj"
    projects.mkdir(parents=True)
    (enki_root / "db").mkdir(parents=True)

    with connect(projects / "em.db") as conn:
        # Deliberately no create_em_tables call: emulate legacy db without project_state
        conn.execute("CREATE TABLE IF NOT EXISTS marker (id TEXT PRIMARY KEY)")

    _run_migration(enki_root)

    with sqlite3.connect(str(projects / "em.db")) as conn:
        rows = conn.execute("SELECT key, value FROM project_state").fetchall()
    state = {k: v for k, v in rows}
    assert state["phase"] == "none"
    assert state["tier"] == "minimal"
    assert state["goal"] == "none"


def test_pass2_keeps_existing_project_state_untouched(tmp_path):
    enki_root = tmp_path / ".enki"
    projects = enki_root / "projects" / "existing-proj"
    projects.mkdir(parents=True)
    (enki_root / "db").mkdir(parents=True)

    with connect(projects / "em.db") as conn:
        create_em_tables(conn)
        conn.execute(
            "INSERT INTO project_state (key, value) VALUES "
            "('phase', 'implement'), ('tier', 'standard'), ('goal', 'ship')"
        )

    _run_migration(enki_root)

    with sqlite3.connect(str(projects / "em.db")) as conn:
        rows = conn.execute("SELECT key, value FROM project_state").fetchall()
    state = {k: v for k, v in rows}
    assert state["phase"] == "implement"
    assert state["tier"] == "standard"
    assert state["goal"] == "ship"


def test_pass2_handles_empty_projects_directory(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "projects").mkdir(parents=True)
    (enki_root / "db").mkdir(parents=True)
    _run_migration(enki_root)
