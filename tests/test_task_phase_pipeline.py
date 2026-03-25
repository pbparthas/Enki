"""Tests for task_phase pipeline transitions."""

from pathlib import Path
from unittest.mock import patch

import pytest

import enki.db as db_mod


PROJECT = "phase-proj"


def _make_prompts(root: Path) -> None:
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for role in (
        "pm", "architect", "dev", "qa", "validator", "igi",
        "reviewer", "infosec", "performance", "ui_ux",
    ):
        (prompts / f"{role}.md").write_text(f"You are {role}.")


def _patch_env(root: Path):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=root,
        DB_DIR=root / "db",
    )


def _setup_project(tmp_path: Path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    ctx = _patch_env(root)
    ctx.__enter__()
    patcher = patch("enki.mcp.orch_tools.ENKI_ROOT", root)
    patcher.__enter__()
    from enki.db import init_all
    from enki.mcp.orch_tools import enki_goal
    from enki.orch.task_graph import create_sprint, create_task

    init_all()
    goal = enki_goal("phase pipeline", project=PROJECT)
    sprint = create_sprint(PROJECT, 1)
    task_id = create_task(PROJECT, sprint, "Task A", tier="standard")
    return root, goal, sprint, task_id, old_init, ctx, patcher


def _teardown(old_init, ctx, patcher):
    patcher.__exit__(None, None, None)
    ctx.__exit__(None, None, None)
    db_mod._em_initialized = old_init


def test_task_phase_defaults_to_test_design(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT task_phase FROM task_state WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        assert row["task_phase"] == "test_design"
    finally:
        _teardown(old_init, ctx, patcher)


@pytest.mark.parametrize(
    "role,phase,expected_mode",
    [
        ("qa", "test_design", "write"),
        ("qa", "verifying", "execute"),
        ("validator", "test_design", "review-tests"),
        ("validator", "verifying", "compliance"),
    ],
)
def test_spawn_mode_injection_by_phase(tmp_path, role, phase, expected_mode):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn

        with em_db(PROJECT) as conn:
            conn.execute(
                "UPDATE task_state SET task_phase = ? WHERE task_id = ?",
                (phase, task_id),
            )

        spawned = enki_spawn(role=role, task_id=task_id, project=PROJECT)
        assert spawned["status"] == "in_progress"
        artifact = Path(spawned["context_artifact"])
        text = artifact.read_text()
        assert expected_mode in text
    finally:
        _teardown(old_init, ctx, patcher)


@pytest.mark.parametrize("role", ["dev", "qa", "validator", "reviewer", "infosec", "performance"])
def test_report_requires_spawn(role, tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_report
        resp = enki_report(role=role, task_id=task_id, summary="x", project=PROJECT)
        assert "error" in resp
        assert "not spawned" in resp["error"]
    finally:
        _teardown(old_init, ctx, patcher)


def test_qa_write_then_validator_advances_to_implementing(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_spawn, enki_report

        enki_spawn("qa", task_id, project=PROJECT)
        qa = enki_report("qa", task_id, "tests written", project=PROJECT)
        assert qa["status"] == "completed"
        assert qa["task_phase"] == "test_design"

        enki_spawn("validator", task_id, project=PROJECT)
        val = enki_report("validator", task_id, "coverage ok", project=PROJECT, output={"concerns": []})
        assert val["status"] == "completed"
        assert val["task_phase"] == "implementing"
    finally:
        _teardown(old_init, ctx, patcher)


def test_dev_complete_advances_to_verifying(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn, enki_report

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'implementing' WHERE task_id = ?", (task_id,))

        enki_spawn("dev", task_id, project=PROJECT)
        dev = enki_report("dev", task_id, "impl done", project=PROJECT)
        assert dev["status"] == "completed"
        assert dev["task_phase"] == "verifying"
    finally:
        _teardown(old_init, ctx, patcher)


def test_validator_compliance_pass_advances_to_reviewing(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn, enki_report

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'verifying' WHERE task_id = ?", (task_id,))

        enki_spawn("qa", task_id, project=PROJECT)
        qa = enki_report("qa", task_id, "ran tests", project=PROJECT)
        assert qa["status"] == "completed"
        assert qa["task_phase"] == "verifying"

        enki_spawn("validator", task_id, project=PROJECT)
        val = enki_report("validator", task_id, "compliant", project=PROJECT, output={"concerns": []})
        assert val["status"] == "completed"
        assert val["task_phase"] == "reviewing"
    finally:
        _teardown(old_init, ctx, patcher)


def test_reviewer_pass_advances_to_complete(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn, enki_report

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'reviewing' WHERE task_id = ?", (task_id,))

        enki_spawn("reviewer", task_id, project=PROJECT)
        review = enki_report("reviewer", task_id, "clean", project=PROJECT, output={"concerns": []})
        assert review["status"] == "completed"
        assert review["task_phase"] == "complete"
    finally:
        _teardown(old_init, ctx, patcher)


def test_reviewer_p1_resets_to_implementing(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn, enki_report

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'reviewing' WHERE task_id = ?", (task_id,))

        enki_spawn("reviewer", task_id, project=PROJECT)
        review = enki_report(
            "reviewer",
            task_id,
            "p1 issues",
            project=PROJECT,
            output={"concerns": [{"content": "P1: missing auth", "severity": "P1"}]},
        )
        assert review["status"] == "completed"
        assert review["task_phase"] == "implementing"
    finally:
        _teardown(old_init, ctx, patcher)


@pytest.mark.parametrize("phase", ["test_design", "implementing", "verifying", "reviewing"])
def test_enki_complete_blocks_wrong_phase(tmp_path, phase):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_complete

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = ? WHERE task_id = ?", (phase, task_id))

        blocked = enki_complete(task_id=task_id, project=PROJECT)
        assert "error" in blocked
        assert "not ready for completion" in blocked["error"]
    finally:
        _teardown(old_init, ctx, patcher)


def test_enki_complete_blocks_open_p1_bugs(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_bug, enki_complete

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'complete' WHERE task_id = ?", (task_id,))

        enki_bug(
            action="file",
            title="P1 bug",
            description="must fix",
            severity="P1",
            filed_by="reviewer",
            task_id=task_id,
            project=PROJECT,
        )
        blocked = enki_complete(task_id=task_id, project=PROJECT)
        assert "error" in blocked
        assert "open P1 bug" in blocked["error"]
    finally:
        _teardown(old_init, ctx, patcher)


def test_infosec_bugs_routed_to_architect(tmp_path):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn, enki_report

        with em_db(PROJECT) as conn:
            conn.execute("UPDATE task_state SET task_phase = 'reviewing' WHERE task_id = ?", (task_id,))

        enki_spawn("infosec", task_id, project=PROJECT)
        rep = enki_report(
            "infosec",
            task_id,
            "security issue",
            project=PROJECT,
            output={"concerns": [{"content": "critical secret leak", "severity": "P0"}]},
        )
        assert rep["status"] == "completed"
        with em_db(PROJECT) as conn:
            bug = conn.execute(
                "SELECT assigned_to, priority FROM bugs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        assert bug is not None
        assert bug["assigned_to"] == "architect"
        assert bug["priority"] == "P0"
    finally:
        _teardown(old_init, ctx, patcher)


def test_enki_wave_returns_test_design_phase_task(tmp_path):
    root, _goal, sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve, enki_wave

        enki_approve(stage="igi", project=PROJECT)
        enki_approve(stage="architect", project=PROJECT)
        wave = enki_wave(project=PROJECT)
        assert wave["wave_number"] == 1
        assert wave["tasks"]
        assert wave["tasks"][0]["phase"] == "test_design"
        assert "PHASE: test_design" in "\n".join(wave["instructions"])
    finally:
        _teardown(old_init, ctx, patcher)


def test_enki_sprint_close_message_shape(tmp_path):
    root, _goal, _sprint, _task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_sprint_close
        result = enki_sprint_close(project=PROJECT)
        assert "sprint_id" in result
        assert result["steps"] == [
            "test_consolidation", "full_test_run", "infosec", "sprint_review", "verify_clean"
        ]
        assert "STEP 1" in result["message"]
        assert "STEP 5" in result["message"]
    finally:
        _teardown(old_init, ctx, patcher)


@pytest.mark.parametrize(
    "severity,expected",
    [
        ("critical", "P0"),
        ("high", "P1"),
        ("medium", "P2"),
        ("low", "P3"),
        ("P0", "P0"),
        ("P1", "P1"),
        ("P2", "P2"),
        ("P3", "P3"),
    ],
)
def test_enki_bug_severity_mapping(tmp_path, severity, expected):
    root, _goal, _sprint, task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_bug

        enki_bug(
            action="file",
            title="bug",
            description="desc",
            severity=severity,
            filed_by="qa",
            task_id=task_id,
            project=PROJECT,
        )
        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT priority FROM bugs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        assert row["priority"] == expected
    finally:
        _teardown(old_init, ctx, patcher)
