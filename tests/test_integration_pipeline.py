import importlib
from pathlib import Path


def _reload_enki_modules():
    import enki.db as db_mod
    import enki.project_state as project_state_mod
    import enki.mcp.orch_tools as orch_tools_mod
    import enki.gates.uru as uru_mod

    db_mod = importlib.reload(db_mod)
    project_state_mod = importlib.reload(project_state_mod)
    orch_tools_mod = importlib.reload(orch_tools_mod)
    uru_mod = importlib.reload(uru_mod)
    return db_mod, project_state_mod, orch_tools_mod, uru_mod


def _write_prompts(enki_root: Path) -> None:
    prompts = enki_root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for role in ("pm", "architect", "dev", "qa", "validator", "igi"):
        (prompts / f"{role}.md").write_text(f"You are {role}.")


def test_full_pipeline_with_stable_goal_id_and_wave(monkeypatch, tmp_path):
    enki_root = tmp_path / ".enki"
    workdir = tmp_path / "work" / "alpha"
    workdir.mkdir(parents=True)
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))
    monkeypatch.chdir(workdir)

    db_mod, _, orch_tools_mod, _ = _reload_enki_modules()
    _write_prompts(enki_root)
    db_mod.init_all()

    from enki.orch.task_graph import create_sprint, create_task

    goal = orch_tools_mod.enki_goal("Build integration flow", project="alpha")
    assert len(goal["goal_id"]) == 16
    assert goal["phase"] == "planning"

    spec_ok = orch_tools_mod.enki_approve(stage="igi", project="alpha")
    assert spec_ok["phase"] == "approved"
    impl_ok = orch_tools_mod.enki_approve(stage="architect", project="alpha")
    assert impl_ok["phase"] == "implement"

    sprint = create_sprint("alpha", 1)
    task_id = create_task("alpha", sprint, "Implement API", tier="standard")
    wave = orch_tools_mod.enki_wave(project="alpha")
    assert wave["wave_number"] == 1
    assert "tasks" in wave
    assert any(t["task_id"] == task_id for t in wave["tasks"])
    assert wave["tasks"][0]["phase"] in {"test_design", "implementing", "verifying", "reviewing"}


def test_cross_session_stability_phase_preserved(monkeypatch, tmp_path):
    enki_root = tmp_path / ".enki"
    workdir = tmp_path / "work" / "stable"
    workdir.mkdir(parents=True)
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))
    monkeypatch.chdir(workdir)

    db_mod, project_state_mod, orch_tools_mod, _ = _reload_enki_modules()
    _write_prompts(enki_root)
    db_mod.init_all()

    first = orch_tools_mod.enki_goal("First", project="stable")
    project_state_mod.write_project_state("stable", "phase", "implement")
    orch_tools_mod._upsert_agent_status(first["goal_id"], "dev", "in_progress")
    second = orch_tools_mod.enki_goal("Second", project="stable")

    assert first["goal_id"] == second["goal_id"]
    assert second["phase"] == "implement"
    assert second["phase_preserved"] is True

    with db_mod.uru_db() as conn:
        row = conn.execute(
            "SELECT status FROM agent_status WHERE goal_id = ? AND agent_role = 'dev'",
            (second["goal_id"],),
        ).fetchone()
    assert row["status"] == "in_progress"


def test_parallel_projects_cwd_resolution_no_state_bleed(monkeypatch, tmp_path):
    enki_root = tmp_path / ".enki"
    proj_a = tmp_path / "work" / "proj-a"
    proj_b = tmp_path / "work" / "proj-b"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))

    db_mod, project_state_mod, orch_tools_mod, _ = _reload_enki_modules()
    _write_prompts(enki_root)
    db_mod.init_all()

    monkeypatch.chdir(proj_a)
    orch_tools_mod.enki_goal("Goal A", project="proj-a")
    monkeypatch.chdir(proj_b)
    orch_tools_mod.enki_goal("Goal B", project="proj-b")

    assert project_state_mod.resolve_project_from_cwd(str(proj_a / "src")) == "proj-a"
    assert project_state_mod.resolve_project_from_cwd(str(proj_b / "src")) == "proj-b"

    with db_mod.em_db("proj-a") as conn:
        goal_a = conn.execute("SELECT value FROM project_state WHERE key = 'goal'").fetchone()["value"]
    with db_mod.em_db("proj-b") as conn:
        goal_b = conn.execute("SELECT value FROM project_state WHERE key = 'goal'").fetchone()["value"]
    assert goal_a == "Goal A"
    assert goal_b == "Goal B"


def test_wave_resolves_project_from_cwd_without_goal_id(monkeypatch, tmp_path):
    enki_root = tmp_path / ".enki"
    workdir = tmp_path / "work" / "cwd-wave"
    workdir.mkdir(parents=True)
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))
    monkeypatch.chdir(workdir)

    db_mod, _, orch_tools_mod, _ = _reload_enki_modules()
    _write_prompts(enki_root)
    db_mod.init_all()

    from enki.orch.task_graph import create_sprint, create_task

    orch_tools_mod.enki_goal("Build from cwd", project="cwd-wave")
    orch_tools_mod.enki_approve(stage="igi", project="cwd-wave")
    orch_tools_mod.enki_approve(stage="architect", project="cwd-wave")
    sprint = create_sprint("cwd-wave", 1)
    create_task("cwd-wave", sprint, "Task", tier="standard")

    wave = orch_tools_mod.enki_wave()
    assert wave["wave_number"] == 1
    assert len(wave["tasks"]) >= 1


def test_gates_block_wrong_phase_and_allow_correct_phase(monkeypatch, tmp_path):
    enki_root = tmp_path / ".enki"
    workdir = tmp_path / "work" / "gate-proj"
    workdir.mkdir(parents=True)
    monkeypatch.setenv("ENKI_ROOT", str(enki_root))
    monkeypatch.chdir(workdir)

    db_mod, project_state_mod, orch_tools_mod, uru_mod = _reload_enki_modules()
    _write_prompts(enki_root)
    db_mod.init_all()

    orch_tools_mod.enki_goal("Gate check", project="gate-proj")
    blocked = uru_mod.check_pre_tool_use(
        "Write",
        {"file_path": "src/app.py"},
        hook_context={"cwd": str(workdir), "subagent_type": "dev"},
    )
    assert blocked["decision"] == "block"

    orch_tools_mod.enki_approve(stage="igi", project="gate-proj")
    orch_tools_mod.enki_approve(stage="architect", project="gate-proj")
    project_state_mod.write_project_state("gate-proj", "phase", "implement")
    allowed = uru_mod.check_pre_tool_use(
        "Write",
        {"file_path": "src/app.py"},
        hook_context={"cwd": str(workdir), "subagent_type": "dev"},
    )
    assert allowed["decision"] == "allow"
