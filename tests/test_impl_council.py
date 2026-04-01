"""Tests for Implementation Council (Task E).

Covers:
- enki_impl_council analysis mode (propose panel, no spawn)
- enki_impl_council execution mode (spawn specialists)
- enki_impl_council_update (record output, mark complete)
- enki_approve council gate (blocks without council for standard/full)
- enki_approve skip_council=True (bypasses gate with reason)
- agent_briefs injection in enki_spawn (correct field per role)
- enki_decompose stores agent_briefs from task definitions
"""

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod

PROJECT = "test-impl-council"


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
        "infosec", "dba", "devops", "ui_ux", "performance",
        "researcher", "igi",
        "typescript-dev-reviewer", "typescript-qa-reviewer",
        "typescript-reviewer", "typescript-infosec",
        "python-dev-reviewer", "python-qa-reviewer",
        "python-reviewer", "python-infosec",
        "security-auditor", "ai-engineer",
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

    init_all()
    enki_goal("impl council test", project=PROJECT, tier="full")
    return root, old_init, ctx, patcher


def _teardown(old_init, ctx, patcher):
    patcher.__exit__(None, None, None)
    ctx.__exit__(None, None, None)
    db_mod._em_initialized = old_init


def _artifacts_dir(root: Path) -> Path:
    return root / "artifacts" / PROJECT


def _write_impl_spec(root: Path, tasks=None) -> None:
    """Write a minimal architect impl-spec artifact for council to read."""
    artifacts = _artifacts_dir(root)
    artifacts.mkdir(parents=True, exist_ok=True)
    spec = {
        "tech_stack": {"primary": "TypeScript", "frameworks": ["NestJS"]},
        "tasks": tasks or [
            {
                "name": "Implement PipelineEngine",
                "description": "Build the main pipeline execution engine with retry logic",
                "files": ["src/pipeline/engine.ts", "src/pipeline/types.ts"],
                "dependencies": [],
                "acceptance_criteria": ["Handles concurrent pipelines", "Retries on failure"],
            },
            {
                "name": "Add authentication middleware",
                "description": "Implement JWT authentication with RBAC for API endpoints",
                "files": ["src/auth/middleware.ts", "src/auth/jwt.ts"],
                "dependencies": ["Implement PipelineEngine"],
                "acceptance_criteria": ["Validates JWT tokens", "Enforces role permissions"],
            },
        ],
    }
    artifact_path = artifacts / "spawn-architect-impl-spec-test.md"
    artifact_path.write_text(f"```json\n{json.dumps(spec, indent=2)}\n```")


def _insert_hitl_approval(project: str, stage: str) -> None:
    from enki.db import em_db

    with em_db(project) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO hitl_approvals (id, project, stage, note) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project, stage, f"approved {stage}"),
        )


def test_impl_council_requires_active_goal(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "goal", "")
        result = enki_impl_council(project=PROJECT)
        assert "error" in result
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_analysis_mode_requires_impl_spec(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        result = enki_impl_council(project=PROJECT)
        assert "error" in result
        assert "spec" in result["error"].lower()
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_analysis_proposes_typescript_specialists(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        _write_impl_spec(root)
        result = enki_impl_council(project=PROJECT)
        assert "error" not in result
        assert "proposed_specialists" in result
        roles = [s["role"] for s in result["proposed_specialists"]]
        assert any("typescript" in r for r in roles)
        assert "security-auditor" in roles
        assert "infosec" in roles
        assert "reviewer" in roles
        assert "spawn_instructions" not in result
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_analysis_proposes_ai_engineer_for_ai_tasks(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        _write_impl_spec(
            root,
            tasks=[
                {
                    "name": "Integrate LLM gateway",
                    "description": "Build RAG pipeline with LLM inference and embedding generation",
                    "files": ["src/llm/gateway.ts"],
                    "dependencies": [],
                    "acceptance_criteria": ["Supports streaming LLM responses"],
                }
            ],
        )
        result = enki_impl_council(project=PROJECT)
        assert "error" not in result
        roles = [s["role"] for s in result["proposed_specialists"]]
        assert "ai-engineer" in roles
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_analysis_includes_not_proposed(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        _write_impl_spec(
            root,
            tasks=[
                {
                    "name": "Build data processor",
                    "description": "Implement batch data processing pipeline in Python",
                    "files": ["src/processor.py", "src/models.py"],
                    "dependencies": [],
                    "acceptance_criteria": ["Processes 1000 records/sec"],
                }
            ],
        )
        result = enki_impl_council(project=PROJECT)
        assert "not_proposed" in result
        not_proposed_roles = [s["role"] for s in result["not_proposed"]]
        assert "ai-engineer" in not_proposed_roles
        assert any("typescript" in r for r in not_proposed_roles)
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_execution_rejects_unknown_specialist(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        result = enki_impl_council(
            project=PROJECT,
            approved_specialists=["made-up-role", "infosec"],
        )
        assert "error" in result
        assert "made-up-role" in result["error"]
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_execution_returns_spawn_instructions(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        result = enki_impl_council(
            project=PROJECT,
            approved_specialists=["infosec", "reviewer"],
        )
        assert "error" not in result
        assert "spawn_instructions" in result
        assert len(result["spawn_instructions"]) == 2
        roles_spawned = [s["specialist"] for s in result["spawn_instructions"]]
        assert "infosec" in roles_spawned
        assert "reviewer" in roles_spawned
        for instr in result["spawn_instructions"]:
            assert "prompt_path" in instr
            assert "context_artifact" in instr
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_resumable_after_restart(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council, enki_impl_council_update

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        enki_impl_council(project=PROJECT, approved_specialists=["infosec"])
        enki_impl_council_update(
            specialist="infosec",
            output={
                "concerns": [
                    {
                        "task": "Add authentication middleware",
                        "severity": "blocking",
                        "concern": "JWT stored in localStorage in proposed design",
                        "agent_briefs": {
                            "dev": "Store JWT in httpOnly cookie, not localStorage",
                            "reviewer": "Verify no localStorage JWT usage",
                        },
                    }
                ]
            },
            project=PROJECT,
        )
        result = enki_impl_council(project=PROJECT, approved_specialists=["infosec"])
        assert "error" not in result
        if "pending" in result:
            assert "infosec" not in result["pending"]
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_update_records_concerns(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council, enki_impl_council_update

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        enki_impl_council(project=PROJECT, approved_specialists=["infosec"])

        concerns = [
            {
                "task": "Add authentication middleware",
                "severity": "blocking",
                "concern": "No rate limiting on login endpoint",
                "agent_briefs": {
                    "dev": "Add rate limiting middleware before auth handler",
                    "infosec": "Verify rate limiting is per-IP not per-session",
                },
            }
        ]
        result = enki_impl_council_update(
            specialist="infosec",
            output={"concerns": concerns},
            project=PROJECT,
        )
        assert "error" not in result
        assert result["concerns_recorded"] == 1

        council_files = list(_artifacts_dir(root).glob("impl-council-*.json"))
        assert len(council_files) == 1
        state = json.loads(council_files[0].read_text())
        assert "specialist_outputs" in state
        assert "infosec" in state["specialist_outputs"]
        assert len(state["specialist_outputs"]["infosec"]) == 1
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_update_architect_marks_complete(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council, enki_impl_council_update

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        enki_impl_council(project=PROJECT, approved_specialists=["infosec"])
        enki_impl_council_update(
            specialist="infosec",
            output={"concerns": []},
            project=PROJECT,
        )

        enriched_tasks = [
            {
                "name": "Implement PipelineEngine",
                "description": "Build pipeline engine with retry",
                "files": ["src/pipeline/engine.ts"],
                "dependencies": [],
                "acceptance_criteria": ["Retries on failure"],
                "agent_briefs": {
                    "dev": "Use exponential backoff for retries",
                    "qa": "Test retry behavior with mock failures",
                    "reviewer": "Verify no infinite retry loops",
                    "infosec": "Check for resource exhaustion in retry logic",
                },
            }
        ]
        result = enki_impl_council_update(
            specialist="architect",
            output={
                "tasks": enriched_tasks,
                "council_decisions": [
                    {"raised_by": "infosec", "concern": "rate limiting", "resolution": "accepted"}
                ],
                "spec_changes": "Added rate limiting to auth tasks",
            },
            project=PROJECT,
        )
        assert "error" not in result
        assert result["tasks_enriched"] == 1

        council_files = list(_artifacts_dir(root).glob("impl-council-*.json"))
        state = json.loads(council_files[0].read_text())
        assert state["status"] == "complete"
        assert len(state["enriched_tasks"]) == 1
    finally:
        _teardown(old_init, ctx, patcher)


def test_impl_council_complete_state_returns_resumed(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_impl_council, enki_impl_council_update

        _write_impl_spec(root)
        enki_impl_council(project=PROJECT)
        enki_impl_council(project=PROJECT, approved_specialists=["infosec"])
        enki_impl_council_update(
            specialist="infosec",
            output={"concerns": []},
            project=PROJECT,
        )
        enki_impl_council_update(
            specialist="architect",
            output={"tasks": [], "council_decisions": [], "spec_changes": ""},
            project=PROJECT,
        )
        result = enki_impl_council(project=PROJECT)
        assert "error" not in result
        assert result.get("resumed") is True
    finally:
        _teardown(old_init, ctx, patcher)


def test_approve_blocks_architect_without_council_full_tier(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "tier", "full")
        write_project_state(PROJECT, "phase", "approved")
        _insert_hitl_approval(PROJECT, "igi")
        _insert_hitl_approval(PROJECT, "spec")

        result = enki_approve(stage="architect", project=PROJECT)
        assert "error" in result
        assert "council" in result["error"].lower()
    finally:
        _teardown(old_init, ctx, patcher)


def test_approve_blocks_architect_without_council_standard_tier(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve, enki_goal
        from enki.project_state import write_project_state

        enki_goal("standard tier test", project=PROJECT, tier="standard")
        write_project_state(PROJECT, "phase", "approved")
        result = enki_approve(stage="architect", project=PROJECT)
        assert "error" in result
        assert "council" in result["error"].lower()
    finally:
        _teardown(old_init, ctx, patcher)


def test_approve_allows_architect_without_council_minimal_tier(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve, enki_goal
        from enki.project_state import write_project_state

        enki_goal("minimal tier test", project=PROJECT, tier="minimal")
        write_project_state(PROJECT, "phase", "approved")
        result = enki_approve(stage="architect", project=PROJECT)
        assert "council" not in result.get("error", "").lower()
        assert result.get("stage") == "architect"
    finally:
        _teardown(old_init, ctx, patcher)


def test_approve_skip_council_requires_reason(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "tier", "full")
        write_project_state(PROJECT, "phase", "approved")
        result = enki_approve(
            stage="architect",
            project=PROJECT,
            skip_council=True,
        )
        assert "error" in result
        assert "reason" in result["error"].lower()
    finally:
        _teardown(old_init, ctx, patcher)


def test_approve_skip_council_with_reason_bypasses_gate(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.mcp.orch_tools import enki_approve
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "tier", "full")
        write_project_state(PROJECT, "phase", "approved")
        result = enki_approve(
            stage="architect",
            project=PROJECT,
            skip_council=True,
            skip_council_reason="Hotfix sprint - no time for council review",
        )
        assert "council" not in result.get("error", "").lower()
        assert result.get("stage") == "architect"
    finally:
        _teardown(old_init, ctx, patcher)


def test_agent_briefs_injected_for_dev_role(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn
        from enki.orch.task_graph import create_sprint, create_task
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "phase", "implement")
        sprint = create_sprint(PROJECT, 1)
        task_id = create_task(PROJECT, sprint, "Build engine", tier="full")
        briefs = {
            "dev": "Use exponential backoff. No console.log.",
            "qa": "Test retry with mock failures at boundaries.",
            "reviewer": "Verify no infinite loops.",
            "infosec": "Check resource exhaustion.",
        }
        with em_db(PROJECT) as conn:
            conn.execute(
                "UPDATE task_state SET agent_briefs = ? WHERE task_id = ?",
                (json.dumps(briefs), task_id),
            )

        result = enki_spawn("dev", task_id, project=PROJECT)
        assert "error" not in result
        artifact_content = Path(result["context_artifact"]).read_text()
        assert "build_instructions" in artifact_content
        assert "exponential backoff" in artifact_content
    finally:
        _teardown(old_init, ctx, patcher)


def test_agent_briefs_injected_for_qa_role(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_spawn
        from enki.orch.task_graph import create_sprint, create_task
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "phase", "implement")
        sprint = create_sprint(PROJECT, 1)
        task_id = create_task(PROJECT, sprint, "Build engine", tier="full")
        briefs = {
            "dev": "Use exponential backoff.",
            "qa": "Test with mock failures. Cover boundary integers.",
            "reviewer": "Check loops.",
        }
        with em_db(PROJECT) as conn:
            conn.execute(
                "UPDATE task_state SET agent_briefs = ?, task_phase = ? WHERE task_id = ?",
                (json.dumps(briefs), "implementing", task_id),
            )

        result = enki_spawn("qa", task_id, project=PROJECT)
        assert "error" not in result
        artifact_content = Path(result["context_artifact"]).read_text()
        assert "qa_test_strategy" in artifact_content
        assert "mock failures" in artifact_content
    finally:
        _teardown(old_init, ctx, patcher)


def test_decompose_stores_agent_briefs_from_task_definitions(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_decompose
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "phase", "approved")
        tasks = [
            {
                "name": "Build pipeline engine",
                "description": "Implement the core pipeline execution engine with retry logic",
                "files": ["src/pipeline/engine.ts"],
                "dependencies": [],
                "acceptance_criteria": ["Retries on failure"],
                "agent_briefs": {
                    "dev": "Use exponential backoff for retries",
                    "qa": "Test retry with mock failures",
                    "reviewer": "No infinite loops",
                    "infosec": "Check resource exhaustion",
                },
            }
        ]
        result = enki_decompose(tasks=tasks, project=PROJECT)
        assert "error" not in result

        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT agent_briefs FROM task_state WHERE task_name = ?",
                ("Build pipeline engine",),
            ).fetchone()
        assert row is not None
        assert row["agent_briefs"] is not None
        briefs = json.loads(row["agent_briefs"])
        assert briefs["dev"] == "Use exponential backoff for retries"
        assert briefs["qa"] == "Test retry with mock failures"
    finally:
        _teardown(old_init, ctx, patcher)


def test_decompose_without_agent_briefs_succeeds(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db
        from enki.mcp.orch_tools import enki_decompose
        from enki.project_state import write_project_state

        write_project_state(PROJECT, "phase", "approved")
        tasks = [
            {
                "name": "Simple task",
                "description": "Implement a simple utility function for string formatting",
                "files": ["src/utils/format.ts"],
                "dependencies": [],
                "acceptance_criteria": ["Returns formatted string"],
            }
        ]
        result = enki_decompose(tasks=tasks, project=PROJECT)
        assert "error" not in result

        with em_db(PROJECT) as conn:
            row = conn.execute(
                "SELECT agent_briefs FROM task_state WHERE task_name = ?",
                ("Simple task",),
            ).fetchone()
        assert row is not None
        assert row["agent_briefs"] is None
    finally:
        _teardown(old_init, ctx, patcher)


def test_schema_agent_briefs_column_exists(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db

        with em_db(PROJECT) as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(task_state)").fetchall()]
        assert "agent_briefs" in cols
    finally:
        _teardown(old_init, ctx, patcher)


def test_schema_impl_council_state_column_exists(tmp_path):
    root, old_init, ctx, patcher = _setup_project(tmp_path)
    try:
        from enki.db import em_db

        with em_db(PROJECT) as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(sprint_state)").fetchall()]
        assert "impl_council_state" in cols
    finally:
        _teardown(old_init, ctx, patcher)
