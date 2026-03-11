"""Tests for mechanical orchestration MCP tools (goal/phase/spawn/wave/complete/wrap)."""

import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod


PROJECT = "pipeline-proj"


def _make_prompts(root: Path) -> None:
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for role in ("pm", "architect", "dev", "qa", "validator", "igi"):
        (prompts / f"{role}.md").write_text(f"You are {role}.")


def _patch_env(root: Path):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=root,
        DB_DIR=root / "db",
    )


def _insert_hitl_spec_approval(project: str) -> None:
    from enki.db import em_db

    with em_db(project) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO hitl_approvals (id, project, stage, note) VALUES (?, ?, 'spec', 'approved spec')",
            ("PP-001", project),
        )
        conn.execute(
            "INSERT INTO pm_decisions (id, project_id, decision_type, proposed_action, human_response) "
            "VALUES (?, ?, 'spec_approval', 'approved spec', 'approved')",
            (str(uuid.uuid4()), project),
        )


def _insert_agent_status(goal_id: str, role: str, status: str) -> None:
    from enki.db import uru_db

    with uru_db() as conn:
        conn.execute(
            "INSERT INTO agent_status (goal_id, agent_role, status) VALUES (?, ?, ?)",
            (goal_id, role, status),
        )


def test_enki_goal_sets_bootstrap_state(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        result = enki_goal("Build API", project=PROJECT)
        assert result["phase"] == "planning"
        assert "goal_id" in result
        assert "bootstrap" in result
        assert "Challenge pass required" in result["challenge_prompt"]
        assert "enki_spawn(\"igi\", \"challenge-review\")" in result["challenge_prompt"]
        from enki.db import em_db
        with em_db(PROJECT) as conn:
            rows = conn.execute(
                "SELECT key, value FROM project_state WHERE key IN ('goal', 'tier', 'phase')"
            ).fetchall()
        state = {row["key"]: row["value"] for row in rows}
        assert state["goal"] == "Build API"
        assert state["tier"] == result["tier"]
        assert state["phase"] == "planning"
    db_mod._em_initialized = old_init


def test_enki_goal_bootstraps_all_tables_and_is_idempotent(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all, em_db
        from enki.mcp.orch_tools import enki_goal

        init_all()
        first = enki_goal("first goal", project="bootstrap-proj")
        with em_db("bootstrap-proj") as conn:
            conn.execute(
                "INSERT INTO task_state (task_id, project_id, sprint_id, task_name, tier, work_type, status) "
                "VALUES ('persist-1', 'bootstrap-proj', 's1', 'persist', 'minimal', 'task', 'pending')"
            )
        second = enki_goal("second goal", project="bootstrap-proj")

        assert first["bootstrap"]["created"]["project_dir"] is True
        assert second["bootstrap"]["existing"]["project_dir"] is True
        assert first["bootstrap"]["created"]["em_db"] is True
        assert second["bootstrap"]["existing"]["em_db"] is True

        with em_db("bootstrap-proj") as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert "project_state" in tables
            assert "mail_messages" in tables
            assert "mail_threads" in tables
            assert "task_state" in tables
            assert "sprint_state" in tables
            assert "bugs" in tables
            assert "pm_decisions" in tables
            assert "test_approvals" in tables
            assert "file_registry" in tables
            state = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM project_state").fetchall()
            }
            preserved = conn.execute(
                "SELECT 1 FROM task_state WHERE task_id = 'persist-1'"
            ).fetchone()
            assert state["goal"] == "second goal"
            assert state["phase"] == "planning"
            assert preserved is not None
    db_mod._em_initialized = old_init


def test_enki_goal_with_valid_external_spec_copies_and_sets_state(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    source_spec = tmp_path / "external-spec.md"
    source_spec.write_text("# External Spec\n\nBuild this.")
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all, em_db
        from enki.mcp.orch_tools import enki_goal

        init_all()
        result = enki_goal("Build API", project=PROJECT, spec_path=str(source_spec))
        assert result["phase"] == "planning"
        assert result["spec_mode"] == "external"
        copied = Path(result["spec_copied_to"])
        assert copied.exists()
        assert copied.read_text() == source_spec.read_text()

        with em_db(PROJECT) as conn:
            rows = conn.execute(
                "SELECT key, value FROM project_state WHERE key IN ('spec_source', 'spec_path', 'phase')"
            ).fetchall()
        state = {row["key"]: row["value"] for row in rows}
        assert state["spec_source"] == "external"
        assert state["spec_path"] == str(copied)
        assert state["phase"] == "planning"
    db_mod._em_initialized = old_init


def test_enki_goal_with_invalid_external_spec_aborts(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all, em_db
        from enki.mcp.orch_tools import enki_goal

        init_all()
        result = enki_goal("Build API", project=PROJECT, spec_path=str(tmp_path / "missing.md"))
        assert "error" in result
        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT value FROM project_state WHERE key = 'goal'"
            ).fetchone()
        assert row is None
    db_mod._em_initialized = old_init


def test_enki_goal_without_spec_path_sets_internal_mode(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all, em_db
        from enki.mcp.orch_tools import enki_goal

        init_all()
        result = enki_goal("Build API", project=PROJECT)
        assert result["spec_mode"] == "internal"
        with em_db(PROJECT) as conn:
            rows = conn.execute(
                "SELECT key, value FROM project_state WHERE key IN ('spec_source', 'spec_path')"
            ).fetchall()
        state = {row["key"]: row["value"] for row in rows}
        assert state["spec_source"] == "internal"
        assert state["spec_path"] == ""
    db_mod._em_initialized = old_init


def test_enki_goal_writes_mcp_json_to_cwd_from_template(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    template = tmp_path / "mcp-template.json"
    template.write_text(json.dumps({
        "mcpServers": {
            "enki": {
                "command": "/tmp/python",
                "args": ["-m", "enki.mcp_server"],
                "cwd": "/tmp/enki",
                "env": {"ENKI_API_URL": "u", "ENKI_API_KEY": "k"},
            }
        }
    }))

    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        with patch.dict(os.environ, {"ENKI_MCP_TEMPLATE": str(template)}), \
             patch("pathlib.Path.cwd", return_value=workdir):
            result = enki_goal("Build API", project=PROJECT)

        target = workdir / ".mcp.json"
        assert target.exists()
        assert json.loads(target.read_text()) == json.loads(template.read_text())
        assert result["bootstrap"]["created"]["mcp_json"] is True
        assert result["bootstrap"]["existing"]["mcp_json"] is False
    db_mod._em_initialized = old_init


def test_enki_goal_does_not_overwrite_existing_mcp_json(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    template = tmp_path / "mcp-template.json"
    template.write_text('{"mcpServers":{"enki":{"command":"new"}}}')
    target = workdir / ".mcp.json"
    target.write_text('{"mcpServers":{"enki":{"command":"existing"}}}')

    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        with patch.dict(os.environ, {"ENKI_MCP_TEMPLATE": str(template)}), \
             patch("pathlib.Path.cwd", return_value=workdir):
            result = enki_goal("Build API", project=PROJECT)

        assert target.read_text() == '{"mcpServers":{"enki":{"command":"existing"}}}'
        assert result["bootstrap"]["created"]["mcp_json"] is False
        assert result["bootstrap"]["existing"]["mcp_json"] is True
    db_mod._em_initialized = old_init


def test_enki_goal_missing_mcp_template_is_graceful(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        with patch.dict(os.environ, {"ENKI_MCP_TEMPLATE": str(tmp_path / "missing-template.json")}), \
             patch("pathlib.Path.cwd", return_value=workdir):
            result = enki_goal("Build API", project=PROJECT)

        assert "error" not in result
        assert (workdir / ".mcp.json").exists() is False
        assert result["bootstrap"]["created"]["mcp_json"] is False
        assert result["bootstrap"]["existing"]["mcp_json"] is False
        assert "warnings" in result["bootstrap"]
    db_mod._em_initialized = old_init


def test_enki_goal_failed_directory_creation_returns_clean_error(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("no perms")):
            result = enki_goal("Build API", project=PROJECT)
        assert "error" in result
        assert "Failed to create project directory" in result["error"]
    db_mod._em_initialized = old_init


def test_enki_goal_project_a_does_not_affect_project_b(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all, em_db
        from enki.mcp.orch_tools import enki_goal

        init_all()
        a = enki_goal("fix typo in docs", project="project-a")
        b = enki_goal("new system architecture redesign", project="project-b")

        with em_db("project-a") as conn:
            row_a = conn.execute("SELECT value FROM project_state WHERE key = 'tier'").fetchone()
        with em_db("project-b") as conn:
            row_b = conn.execute("SELECT value FROM project_state WHERE key = 'tier'").fetchone()

        assert row_a["value"] == a["tier"]
        assert row_b["value"] == b["tier"]
        assert row_a["value"] != row_b["value"]
    db_mod._em_initialized = old_init


def test_enki_phase_enforces_db_preconditions(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_phase

        init_all()
        goal = enki_goal("new system authentication authorization architecture redesign", project=PROJECT)
        goal_id = goal["goal_id"]

        blocked = enki_phase("advance", "spec", project=PROJECT)
        assert "error" in blocked
        assert "Igi (challenge review) not completed" in blocked["error"]

        _insert_agent_status(goal_id, "igi", "completed")
        blocked_challenge = enki_phase("advance", "spec", project=PROJECT)
        assert "error" in blocked_challenge
        assert "No challenge notes found" in blocked_challenge["error"]

        from enki.mcp.memory_tools import enki_remember
        remember = enki_remember(
            content="Missing orchestrator component in pipeline",
            category="challenge",
            project=PROJECT,
        )
        assert remember["stored"] in ("staging", "duplicate")

        to_spec = enki_phase("advance", "spec", project=PROJECT)
        assert to_spec["phase"] == "spec"

        blocked2 = enki_phase("advance", "approved", project=PROJECT)
        assert "error" in blocked2
        assert "HITL approval record" in blocked2["error"]

        _insert_hitl_spec_approval(PROJECT)
        to_approved = enki_phase("advance", "approved", project=PROJECT)
        assert to_approved["phase"] == "approved"
    db_mod._em_initialized = old_init


def test_enki_approve_stage_transitions_and_idempotency(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_approve, enki_goal

        init_all()
        enki_goal("build pipeline", project=PROJECT)
        spec = enki_approve(project=PROJECT, stage="spec")
        architect = enki_approve(project=PROJECT, stage="architect")
        test = enki_approve(project=PROJECT, stage="test")
        duplicate = enki_approve(project=PROJECT, stage="spec")

        assert spec["phase"] == "approved"
        assert architect["phase"] == "implement"
        assert test["phase"] == "complete"
        assert duplicate["created"] is False
        assert duplicate["approval_id"] == spec["approval_id"]


def test_enki_spawn_requires_goal_and_returns_summary(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn
        from enki.orch.task_graph import create_sprint, create_task

        init_all()
        missing = enki_spawn(role="dev", task_id="t1", project=PROJECT)
        assert missing["error"].startswith("No active goal")

        goal = enki_goal("build API endpoint", project=PROJECT)
        sprint = create_sprint(PROJECT, 1)
        task_id = create_task(PROJECT, sprint, "Implement endpoint", tier="standard")
        result = enki_spawn(role="dev", task_id=task_id, context={"assigned_files": ["src/api.py"]}, project=PROJECT)

        assert result["role"] == "dev"
        assert result["status"] == "in_progress"
        assert "instruction" in result
        assert result["prompt_path"] == "~/.enki/prompts/dev.md"
        artifact = Path(result["context_artifact"])
        assert artifact.exists()
        assert goal["goal_id"] in str(artifact)
        assert artifact.name.startswith("spawn-dev-")

        from enki.db import uru_db
        with uru_db() as conn:
            row = conn.execute(
                "SELECT status FROM agent_status WHERE goal_id = ? AND agent_role = ?",
                (goal["goal_id"], "dev"),
            ).fetchone()
        assert row["status"] == "in_progress"
    db_mod._em_initialized = old_init


def test_enki_approve_igi_creates_implied_spec_record(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import em_db, init_all
        from enki.mcp.orch_tools import enki_approve, enki_goal

        init_all()
        enki_goal("build pipeline", project=PROJECT)
        igi = enki_approve(project=PROJECT, stage="igi")

        assert igi["stage"] == "igi"
        assert igi["phase"] == "approved"
        with em_db(PROJECT) as conn:
            rows = conn.execute(
                "SELECT stage, note FROM hitl_approvals WHERE project = ? ORDER BY stage",
                (PROJECT,),
            ).fetchall()
        approvals = {(row["stage"], row["note"]) for row in rows}
        assert any(stage == "igi" for stage, _ in approvals)
        assert ("spec", "implied by igi approval") in approvals
    db_mod._em_initialized = old_init


def test_pm_context_includes_external_spec_mode(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    external_spec = tmp_path / "ext-spec.md"
    external_spec.write_text("Spec from outside.")
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn

        init_all()
        enki_goal("Build API", project=PROJECT, spec_path=str(external_spec))
        spawned = enki_spawn(role="pm", task_id="pm-endorse", context={}, project=PROJECT)
        artifact = Path(spawned["context_artifact"])
        text = artifact.read_text()
        assert "External Spec Mode" in text
        assert "PM Endorsement document" in text
        assert "Spec from outside." in text
    db_mod._em_initialized = old_init


def test_pm_context_unchanged_for_internal_spec_mode(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn

        init_all()
        enki_goal("Build API", project=PROJECT)
        spawned = enki_spawn(role="pm", task_id="pm-normal", context={}, project=PROJECT)
        artifact = Path(spawned["context_artifact"])
        text = artifact.read_text()
        assert "External Spec Mode" not in text
    db_mod._em_initialized = old_init


def test_enki_report_flow(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn, enki_report
        from enki.orch.task_graph import create_sprint, create_task

        init_all()
        goal = enki_goal("implement feature", project=PROJECT)
        sprint = create_sprint(PROJECT, 1)
        task_id = create_task(PROJECT, sprint, "Task A", tier="standard")

        no_spawn = enki_report(role="dev", task_id=task_id, summary="done", project=PROJECT)
        assert "error" in no_spawn
        assert "in_progress" in no_spawn["error"]

        enki_spawn(role="dev", task_id=task_id, context={}, project=PROJECT)
        ok = enki_report(role="dev", task_id=task_id, summary="Implemented endpoint", project=PROJECT)
        assert ok["status"] == "completed"

        from enki.db import uru_db
        with uru_db() as conn:
            row = conn.execute(
                "SELECT status FROM agent_status WHERE goal_id = ? AND agent_role = ?",
                (goal["goal_id"], "dev"),
            ).fetchone()
        assert row["status"] == "completed"

        failed = enki_spawn(role="qa", task_id=task_id, context={}, project=PROJECT)
        assert failed["status"] == "in_progress"
        fail_report = enki_report(
            role="qa",
            task_id=task_id,
            summary="Test setup failed",
            status="failed",
            project=PROJECT,
        )
        assert fail_report["status"] == "failed"
        artifact = root / "artifacts" / goal["goal_id"] / f"qa-{task_id}.md"
        assert artifact.exists()
    db_mod._em_initialized = old_init


def test_phase_blocked_without_igi(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_phase
        from enki.mcp.memory_tools import enki_remember

        init_all()
        enki_goal("new system authentication authorization architecture redesign", project=PROJECT)
        enki_remember(content="Unvalidated deployment assumptions", category="challenge", project=PROJECT)
        blocked = enki_phase("advance", "spec", project=PROJECT)
        assert "error" in blocked
        assert "Igi (challenge review) not completed" in blocked["error"]
    db_mod._em_initialized = old_init


def test_phase_allowed_with_igi_and_challenges(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn, enki_report, enki_phase
        from enki.mcp.memory_tools import enki_remember

        init_all()
        enki_goal("new system authentication authorization architecture redesign", project=PROJECT)
        spawn = enki_spawn("igi", "challenge-review", context={}, project=PROJECT)
        assert spawn["status"] == "in_progress"
        report = enki_report("igi", "challenge-review", "Found 3 gaps", project=PROJECT)
        assert report["status"] == "completed"
        enki_remember(content="Missing orchestrator", category="challenge", project=PROJECT)
        result = enki_phase("advance", "spec", project=PROJECT)
        assert result["phase"] == "spec"
    db_mod._em_initialized = old_init


def test_igi_spawn_loads_prompt(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn

        init_all()
        goal = enki_goal("challenge run", project=PROJECT)
        spawn = enki_spawn("igi", "challenge-review", context={"topic": "scope"}, project=PROJECT)
        assert spawn["status"] == "in_progress"
        artifact = Path(spawn["context_artifact"])
        text = artifact.read_text()
        assert "~/.enki/prompts/igi.md" in spawn["prompt_path"]
        assert "You are igi." in text
        assert goal["goal_id"] in str(artifact)
    db_mod._em_initialized = old_init


def test_igi_role_accepted(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_spawn, enki_report

        init_all()
        enki_goal("igi role check", project=PROJECT)
        spawn = enki_spawn("igi", "challenge-review", context={}, project=PROJECT)
        assert spawn["role"] == "igi"
        report = enki_report("igi", "challenge-review", "Done", project=PROJECT)
        assert report["status"] == "completed"
    db_mod._em_initialized = old_init


def test_enki_wave_preconditions_and_returns_spawn_list(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal, enki_wave
        from enki.orch.task_graph import create_sprint, create_task

        init_all()
        goal = enki_goal("implement feature", project=PROJECT)
        sprint = create_sprint(PROJECT, 1)
        create_task(PROJECT, sprint, "Task A", tier="standard")

        blocked = enki_wave(goal_id=goal["goal_id"], project=PROJECT)
        assert blocked["error"] == "Specs not approved."

        _insert_hitl_spec_approval(PROJECT)
        result = enki_wave(goal_id=goal["goal_id"], project=PROJECT)
        assert "wave_number" in result
        assert "instruction" in result
        roles = [a["role"] for a in result["agents"]]
        assert "dev" in roles
        assert "qa" in roles
        assert all("context_artifact" in a for a in result["agents"])
        assert all("prompt_path" in a for a in result["agents"])
    db_mod._em_initialized = old_init


def test_enki_complete_preconditions_and_success(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_complete, enki_goal
        from enki.orch.task_graph import TaskStatus, create_sprint, create_task, update_task_status

        init_all()
        goal = enki_goal("add endpoint", project=PROJECT)
        sprint = create_sprint(PROJECT, 1)
        task_id = create_task(PROJECT, sprint, "Task", tier="standard")

        blocked = enki_complete(task_id=task_id, project=PROJECT)
        assert "error" in blocked
        assert "Cannot complete. Required:" in blocked["error"]

        _insert_agent_status(goal["goal_id"], f"validator:{task_id}", "completed")
        _insert_agent_status(goal["goal_id"], f"qa:{task_id}", "completed")
        update_task_status(PROJECT, task_id, TaskStatus.COMPLETED)

        result = enki_complete(task_id=task_id, project=PROJECT)
        assert result["completion_status"] == "completed"
        assert "summary" in result
    db_mod._em_initialized = old_init


def test_enki_wrap_returns_counts(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": "Decided to add retries", "timestamp": "now"}) + "\n")
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root), patch("enki.mcp.orch_tools.ENKI_ROOT", root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_wrap

        init_all()
        with patch("enki.mcp.orch_tools._find_session_transcript", return_value=str(transcript)), \
             patch("enki.mcp.orch_tools._extract_wrap_messages", return_value=["USER: hi", "ASSISTANT: done"]), \
             patch("enki.mcp.orch_tools._chunk_wrap_messages", return_value=["USER: hi\nASSISTANT: done"]), \
             patch("enki.mcp.orch_tools._choose_ollama_model", return_value="qwen2.5:7b"), \
             patch("enki.mcp.orch_tools._run_ollama_extract", return_value="CATEGORY: DECISIONS\nCONTENT: Use retries\nKEYWORDS: retry,db\n---"), \
             patch("enki.mcp.orch_tools.gemini_review.run_api_review", return_value={"bead_decisions": []}), \
             patch("enki.mcp.orch_tools._apply_wrap_gemini_decisions", return_value=(1, 0)):
            result = enki_wrap()

        assert set(result.keys()) >= {"candidates_extracted", "promoted", "discarded", "message"}
        assert "Memory ready for next session." in result["message"]
        reports = list((root / "artifacts").glob("wrap-*.md"))
        assert reports
    db_mod._em_initialized = old_init


def test_enki_bug_returns_human_readable_id(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_bug

        init_all()
        filed = enki_bug("file", title="Failure", description="oops", project="testforge-rebuild")
        assert filed["bug_id"] == "TR-001"

        listed = enki_bug("list", project="testforge-rebuild")
        assert listed["count"] == 1
        assert listed["bugs"][0]["bug_id"] == "TR-001"

        closed = enki_bug("close", bug_id="TR-001", project="testforge-rebuild")
        assert closed["bug_id"] == "TR-001"
    db_mod._em_initialized = old_init
