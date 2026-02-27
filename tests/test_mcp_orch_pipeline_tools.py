"""Tests for mechanical orchestration MCP tools (goal/phase/spawn/wave/complete/wrap)."""

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod


PROJECT = "pipeline-proj"


def _make_prompts(root: Path) -> None:
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for role in ("pm", "architect", "dev", "qa", "validator"):
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


def test_enki_goal_sets_spec_review_and_locks_tier(tmp_path):
    root = tmp_path / ".enki"
    (root / "db").mkdir(parents=True)
    _make_prompts(root)
    old_init = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with _patch_env(root):
        from enki.db import init_all
        from enki.mcp.orch_tools import enki_goal

        init_all()
        result = enki_goal("Build API", project=PROJECT, spec_path="docs/spec.md")
        assert result["phase"] == "spec-review"
        assert result["spec_path"] == "docs/spec.md"
        assert "goal_id" in result

        locked = enki_goal("new system architecture redesign", project=PROJECT)
        assert "error" in locked
        assert "Tier is locked" in locked["error"]
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
        goal = enki_goal("build an auth flow", project=PROJECT)
        goal_id = goal["goal_id"]

        blocked = enki_phase("advance", "spec", project=PROJECT)
        assert "error" in blocked
        assert "PM agent completed" in blocked["error"]

        _insert_agent_status(goal_id, "pm", "completed")
        to_spec = enki_phase("advance", "spec", project=PROJECT)
        assert to_spec["phase"] == "spec"

        blocked2 = enki_phase("advance", "approved", project=PROJECT)
        assert "error" in blocked2
        assert "HITL approval record" in blocked2["error"]

        _insert_hitl_spec_approval(PROJECT)
        to_approved = enki_phase("advance", "approved", project=PROJECT)
        assert to_approved["phase"] == "approved"
    db_mod._em_initialized = old_init


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
