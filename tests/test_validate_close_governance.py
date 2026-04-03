"""Tests for Task K validate/close governance fixes."""

import json
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod


PROJECT = "validate-k-proj"


def _patch_env(root: Path):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=root,
        DB_DIR=root / "db",
    )


def _make_prompts(root: Path) -> None:
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for role in [
        "pm", "architect", "dev", "qa", "validator", "reviewer",
        "infosec", "devops", "ui_ux", "performance", "igi", "researcher",
    ]:
        (prompts / f"{role}.md").write_text(f"# {role} prompt stub")


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
    enki_goal("validate governance", project=PROJECT, tier="full")
    sprint_id = create_sprint(PROJECT, 1)
    task_id = create_task(PROJECT, sprint_id, "Task A", tier="full")
    return root, sprint_id, task_id, old_init, ctx, patcher


def _teardown(old_init, ctx, patcher):
    patcher.__exit__(None, None, None)
    ctx.__exit__(None, None, None)
    db_mod._em_initialized = old_init


def test_validate_awaiting_priority_requires_hitl_confirmation(tmp_path):
    root, sprint_id, _task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.orch.bugs import file_bug
        from enki.mcp.orch_tools import enki_validate

        bug_id = file_bug(
            project=PROJECT,
            title="Blocking auth issue",
            description="Auth bypass in middleware",
            filed_by="reviewer",
            priority="P1",
            sprint_id=sprint_id,
        )
        assert bug_id

        with em_db(PROJECT) as conn:
            conn.execute(
                "UPDATE sprint_state SET validate_state=? WHERE sprint_id=?",
                (json.dumps({"scope": "sprint", "status": "awaiting_priority"}), sprint_id),
            )

        blocked = enki_validate(scope="sprint", project=PROJECT)
        assert blocked.get("hitl_required") is True
        assert blocked.get("blocking_bugs") == 1
        assert "confirm" in blocked.get("message", "").lower()

        resumed = enki_validate(scope="sprint", project=PROJECT, hitl_confirmed=True)
        assert resumed.get("hitl_required") is not True
        assert "error" not in resumed
    finally:
        _teardown(old_init, ctx, patcher)


def test_validate_revalidating_blocks_clear_when_p0p1_open(tmp_path):
    root, sprint_id, _task_id, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.orch.bugs import file_bug
        from enki.mcp.orch_tools import enki_validate

        bug_id = file_bug(
            project=PROJECT,
            title="Critical data leak",
            description="Sensitive fields exposed",
            filed_by="infosec",
            priority="P0",
            sprint_id=sprint_id,
        )
        assert bug_id

        with em_db(PROJECT) as conn:
            conn.execute(
                "UPDATE sprint_state SET validate_state=? WHERE sprint_id=?",
                (json.dumps({"scope": "sprint", "status": "revalidating"}), sprint_id),
            )
            conn.execute(
                "UPDATE bugs SET reporter_revalidation_required=0, status='open' WHERE id=?",
                (bug_id,),
            )

        result = enki_validate(scope="sprint", project=PROJECT, hitl_confirmed=True)
        assert "error" in result
        assert "still open" in result["error"].lower()
        assert result.get("still_open")

        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT validate_state FROM sprint_state WHERE sprint_id=?",
                (sprint_id,),
            ).fetchone()
        state = json.loads(row["validate_state"])
        assert state["status"] == "fixing"
    finally:
        _teardown(old_init, ctx, patcher)


def test_sprint_close_signature_has_no_is_final_sprint():
    import inspect
    from enki.mcp.orch_tools import enki_sprint_close

    params = inspect.signature(enki_sprint_close).parameters
    assert "is_final_sprint" not in params
