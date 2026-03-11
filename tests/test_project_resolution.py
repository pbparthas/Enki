import importlib
import os
import uuid
from pathlib import Path
from unittest.mock import patch

from enki.db import connect
from enki.memory.schemas import create_tables as create_memory_tables


def _setup_wisdom(enki_root: Path) -> None:
    (enki_root / "db").mkdir(parents=True, exist_ok=True)
    with connect(enki_root / "db" / "wisdom.db") as conn:
        create_memory_tables(conn, "wisdom")


def test_resolve_project_from_cwd_exact_match(tmp_path):
    enki_root = tmp_path / ".enki"
    _setup_wisdom(enki_root)
    proj_path = tmp_path / "workspace" / "proj-a"
    proj_path.mkdir(parents=True)

    with connect(enki_root / "db" / "wisdom.db") as conn:
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("proj-a", str(proj_path)))

    with patch("enki.db.ENKI_ROOT", enki_root), patch("enki.db.DB_DIR", enki_root / "db"):
        from enki.project_state import resolve_project_from_cwd
        assert resolve_project_from_cwd(str(proj_path)) == "proj-a"


def test_resolve_project_from_cwd_subdirectory_and_longest_match(tmp_path):
    enki_root = tmp_path / ".enki"
    _setup_wisdom(enki_root)
    root = tmp_path / "workspace"
    p1 = root / "a"
    p2 = root / "a" / "b"
    cwd = p2 / "src"
    cwd.mkdir(parents=True)

    with connect(enki_root / "db" / "wisdom.db") as conn:
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("proj-a", str(p1)))
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("proj-ab", str(p2)))

    with patch("enki.db.ENKI_ROOT", enki_root), patch("enki.db.DB_DIR", enki_root / "db"):
        from enki.project_state import resolve_project_from_cwd
        assert resolve_project_from_cwd(str(cwd)) == "proj-ab"


def test_project_file_presence_has_no_effect_on_resolution(tmp_path):
    enki_root = tmp_path / ".enki"
    _setup_wisdom(enki_root)
    (enki_root / "PROJECT").write_text("fallback-proj")

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.db.DB_DIR", enki_root / "db"), \
         patch("enki.gates.uru.ENKI_ROOT", enki_root), \
         patch("enki.gates.layer0.ENKI_ROOT", enki_root):
        from enki.gates.uru import _project_from_context
        result = _project_from_context({}, {"cwd": str(tmp_path / "unknown")})
        assert result is None


def test_enki_goal_registers_path_for_cwd_resolution(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "db").mkdir(parents=True, exist_ok=True)
    workdir = tmp_path / "workspace" / "proj-reg"
    workdir.mkdir(parents=True)

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.db.DB_DIR", enki_root / "db"), \
         patch("enki.mcp.orch_tools.ENKI_ROOT", enki_root), \
         patch("enki.gates.uru.ENKI_ROOT", enki_root), \
         patch("enki.gates.layer0.ENKI_ROOT", enki_root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal
        from enki.project_state import resolve_project_from_cwd

        init_all()
        with patch("pathlib.Path.cwd", return_value=workdir):
            enki_goal("bootstrap", project="proj-reg")
        assert resolve_project_from_cwd(str(workdir / "nested")) == "proj-reg"


def test_parallel_sessions_resolve_independent_projects(tmp_path):
    enki_root = tmp_path / ".enki"
    _setup_wisdom(enki_root)
    root = tmp_path / "workspace"
    proj_a = root / "a"
    proj_b = root / "b"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)

    with connect(enki_root / "db" / "wisdom.db") as conn:
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("proj-a", str(proj_a)))
        conn.execute("INSERT INTO projects (name, path) VALUES (?, ?)", ("proj-b", str(proj_b)))

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.db.DB_DIR", enki_root / "db"), \
         patch("enki.gates.uru.ENKI_ROOT", enki_root), \
         patch("enki.gates.layer0.ENKI_ROOT", enki_root):
        from enki.gates.uru import _project_from_context
        a = _project_from_context({}, {"cwd": str(proj_a / "src")})
        b = _project_from_context({}, {"cwd": str(proj_b / "src")})
        assert a == "proj-a"
        assert b == "proj-b"


def test_enki_goal_does_not_write_project_marker_and_renames_legacy(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "db").mkdir(parents=True, exist_ok=True)
    legacy = enki_root / "PROJECT"
    legacy.write_text("old-project")
    workdir = tmp_path / "workspace" / "proj"
    workdir.mkdir(parents=True)

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.db.DB_DIR", enki_root / "db"), \
         patch("enki.mcp.orch_tools.ENKI_ROOT", enki_root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        with patch("pathlib.Path.cwd", return_value=workdir):
            enki_goal("bootstrap", project="proj-reg")

    assert not (enki_root / "PROJECT").exists()
    assert (enki_root / "PROJECT.deprecated").exists()
    assert (enki_root / "PROJECT.last").exists()


def test_no_project_match_fails_closed_for_mutations(tmp_path):
    enki_root = tmp_path / ".enki"
    _setup_wisdom(enki_root)
    with connect(enki_root / "uru.db") as conn:
        from enki.gates.schemas import create_tables as create_uru
        create_uru(conn)

    with patch("enki.db.ENKI_ROOT", enki_root), \
         patch("enki.db.DB_DIR", enki_root / "db"), \
         patch("enki.gates.uru.ENKI_ROOT", enki_root), \
         patch("enki.gates.layer0.ENKI_ROOT", enki_root):
        from enki.gates.uru import check_pre_tool_use
        blocked = check_pre_tool_use("Write", {"file_path": "src/app.py"}, hook_context={"cwd": str(tmp_path / "no-match")})
        allowed = check_pre_tool_use("Read", {"file_path": "src/app.py"}, hook_context={"cwd": str(tmp_path / "no-match")})

    assert blocked["decision"] == "block"
    assert "No active goal" in blocked["reason"]
    assert allowed["decision"] == "allow"


def test_resolve_project_from_cwd_uses_real_db_path_without_mocks(tmp_path, monkeypatch):
    original_root = os.environ.get("ENKI_ROOT")
    enki_root = tmp_path / ".enki-real"
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))

    import enki.db as db_mod
    import enki.project_state as project_state_mod

    db_mod = importlib.reload(db_mod)
    project_state_mod = importlib.reload(project_state_mod)

    try:
        db_path = db_mod._db_path("wisdom.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with db_mod.connect(db_path) as conn:
            create_memory_tables(conn, "wisdom")
            project_name = f"real-proj-{uuid.uuid4().hex[:8]}"
            project_path = tmp_path / "workspace" / project_name
            (project_path / "src").mkdir(parents=True)
            conn.execute(
                "INSERT INTO projects (name, path) VALUES (?, ?)",
                (project_name, str(project_path)),
            )

        resolved = project_state_mod.resolve_project_from_cwd(str(project_path / "src"))
        assert resolved == project_name
    finally:
        if original_root is None:
            monkeypatch.delenv("ENKI_ROOT", raising=False)
        else:
            monkeypatch.setenv("ENKI_ROOT", original_root)
        importlib.reload(db_mod)
        importlib.reload(project_state_mod)
