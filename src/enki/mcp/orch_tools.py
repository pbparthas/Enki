"""orch_tools.py — MCP tool definitions for EM orchestration.

These are the tools CC calls to interact with the orchestration system.
Goal, phase, triage, quick, decompose, orchestrate, mail, status, etc.
"""

import json
import hashlib
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from enki.db import ENKI_ROOT, abzu_db, connect, em_db, uru_db, wisdom_db
from enki.project_state import (
    deprecate_global_project_marker,
    normalize_project_name,
    project_db_path,
    read_project_state,
    resolve_project_from_cwd,
    stable_goal_id,
    write_project_state,
)
from enki.orch.schemas import create_tables as create_em_tables
from enki.orch.orchestrator import Orchestrator
from enki.orch.tiers import (
    detect_tier,
    quick,
    get_project_state,
    triage
)
from enki.orch.pm import (
    validate_intake,
    create_spec,
    is_spec_approved,
    record_decision,
    get_decisions,
    detect_entry_point as pm_detect_entry,
)
from enki.orch.mail import (
    create_thread,
    get_inbox,
    get_thread_messages,
    mark_read,
    count_unread,
    query_threads,
    send,
)
from enki.orch.task_graph import (
    create_task,
    get_task,
    update_task_status,
    get_next_wave,
    get_sprint_tasks,
    is_sprint_complete,
    create_sprint,
    get_active_sprint,
    TaskStatus,
)
from enki.orch.bugs import (
    derive_project_prefix,
    file_bug,
    close_bug,
    list_bugs,
    resolve_bug_identifier,
    to_human_bug_id,
)
from enki.orch.status import generate_status_update, get_sprint_summary
from enki.orch.onboarding import (
    detect_entry_point,
    get_or_create_user_profile,
    update_user_profile,
    get_user_preference,
)
from enki.orch.bridge import extract_beads_from_project
from enki.orch.agents import AgentRole, get_blind_wall_filter
from enki.memory import gemini as gemini_review
from enki.memory.notes import create as create_note, update as update_note
from enki.memory.staging import resolve_candidate_id

logger = logging.getLogger(__name__)

PHASE_ORDER = ["planning", "spec", "approved", "implement", "validating", "complete"]
PHASE_ALIASES = {
    "spec-review": "spec",
    "approve": "approved",
    "review": "validating",
    "none": "planning",
}
VALID_AGENT_ROLES = {
    "pm", "architect", "dba", "dev", "qa", "ui_ux", "validator",
    "reviewer", "infosec", "devops", "performance", "researcher", "em",
    "igi", "cto", "devils_advocate", "tech_feasibility", "historical_context",
}
APPROVAL_STAGES = {"igi", "spec", "architect", "test", "spec-revision"}
APPROVAL_TARGET_PHASE = {
    "igi": "approved",
    "spec": "approved",
    "spec-revision": "approved",
    "architect": "implement",
    "test": "validating",
}


def _resolve_project(project: str | None) -> str:
    candidate = (project or "").strip()
    if candidate and candidate not in {".", "default"}:
        return normalize_project_name(candidate)
    resolved = resolve_project_from_cwd(str(Path.cwd()))
    if resolved:
        return normalize_project_name(resolved)
    return normalize_project_name(candidate) or "default"


def _register_project_path(project: str, cwd: Path | None = None) -> str:
    resolved_cwd = str((cwd or Path.cwd()).resolve())
    with wisdom_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                name TEXT PRIMARY KEY,
                path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO projects (name, path, last_active) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (project, resolved_cwd),
        )
    return resolved_cwd


def _get_project_path(project: str) -> str | None:
    """Fetch registered project path from wisdom.db."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT path FROM projects WHERE name = ? LIMIT 1",
            (normalize_project_name(project),),
        ).fetchone()
    if not row:
        return None
    return (row["path"] or "").strip() or None


# ── Goal & Triage ──


def enki_goal(
    description: str | None = None,
    project: str = "default",
    spec_path: str | None = None,
    goal: str | None = None,
    tier: str | None = None,
) -> dict:
    """Set active goal and fully initialize project infrastructure."""
    requested_goal = (description or goal or "").strip()
    if not requested_goal:
        return {"error": "Goal description is required."}

    external_spec = (spec_path or "").strip()
    external_spec_path: Path | None = None
    if external_spec:
        candidate = Path(external_spec).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            return {"error": f"spec_path could not be resolved: {external_spec}"}
        if not resolved.exists():
            return {"error": f"spec_path does not exist: {resolved}"}
        if not resolved.is_file():
            return {"error": f"spec_path is not a file: {resolved}"}
        if not os.access(resolved, os.R_OK):
            return {"error": f"spec_path is not readable: {resolved}"}
        external_spec_path = resolved

    project = _resolve_project(project)
    deprecate_global_project_marker()

    db_path = project_db_path(project)
    project_dir = db_path.parent
    enki_root = db_path.parents[2]
    cwd = Path.cwd().resolve()

    created: dict[str, bool] = {}
    existing: dict[str, bool] = {}
    warnings: list[str] = []

    try:
        project_dir_exists = project_dir.exists()
        project_dir.mkdir(parents=True, exist_ok=True)
        created["project_dir"] = not project_dir_exists
        existing["project_dir"] = project_dir_exists
    except OSError as e:
        return {
            "error": f"Failed to create project directory '{project_dir}': {e}",
            "project": project,
        }

    db_existed = db_path.exists()
    with connect(db_path) as conn:
        create_em_tables(conn)
    created["em_db"] = not db_existed
    existing["em_db"] = db_existed

    detected_tier = (tier or "").strip().lower() or detect_tier(requested_goal)
    previous_goal = read_project_state(project, "goal")
    existing_phase = read_project_state(project, "phase")
    if existing_phase and existing_phase not in {"none", "planning"}:
        phase = existing_phase
        phase_preserved = True
    else:
        phase = "planning"
        phase_preserved = False

    goal_id = stable_goal_id(project)

    copied_spec: Path | None = None
    spec_mode = "internal"
    if external_spec_path is not None:
        copied_spec = project_dir / "external-spec.md"
        try:
            shutil.copy2(external_spec_path, copied_spec)
        except OSError as e:
            return {"error": f"Failed to copy external spec: {e}"}
        spec_mode = "external"

    write_project_state(project, "goal", requested_goal)
    write_project_state(project, "tier", detected_tier)
    write_project_state(project, "goal_id", goal_id)
    if not phase_preserved:
        write_project_state(project, "phase", phase)
    write_project_state(project, "spec_source", spec_mode)
    write_project_state(project, "spec_path", str(copied_spec) if copied_spec else "")
    created["project_state"] = previous_goal is None
    existing["project_state"] = previous_goal is not None

    if created["project_dir"]:
        _write_project_last_marker(enki_root, project, cwd)

    registered_cwd = _register_project_path(project, cwd=cwd)

    mcp_status = _ensure_project_mcp_json(cwd)
    created["mcp_json"] = mcp_status["created"]
    existing["mcp_json"] = mcp_status["existing"]
    warning = mcp_status.get("warning")
    if warning:
        warnings.append(warning)
    _ensure_pipeline_md(enki_root)

    result = {
        "message": _next_step_hint(detected_tier),
        "status": "initialised",
        "project": project,
        "goal_id": goal_id,
        "tier": detected_tier,
        "phase": phase,
        "phase_preserved": phase_preserved,
        "spec_mode": spec_mode,
    }
    if spec_path:
        result["spec_path"] = spec_path
    if copied_spec:
        result["spec_copied_to"] = str(copied_spec)
    result["bootstrap"] = {
        "project": project,
        "project_dir": str(project_dir),
        "db_path": str(db_path),
        "registered_path": registered_cwd,
        "created": created,
        "existing": existing,
    }
    if warnings:
        result["bootstrap"]["warnings"] = warnings
    if phase_preserved:
        result["warning"] = (
            f"Project already in progress — phase preserved at {phase}. Goal and tier updated."
        )
    return result


def enki_register(project: str | None = None, path: str | None = None) -> dict:
    """Register project path mapping in wisdom.db for CWD-based resolution."""
    resolved_project = _resolve_project(project)
    if path:
        cwd = Path(path).expanduser().resolve()
    else:
        cwd = Path.cwd().resolve()
    registered_path = _register_project_path(resolved_project, cwd=cwd)
    return {
        "registered": True,
        "project": resolved_project,
        "path": registered_path,
    }


def _write_project_last_marker(enki_root: Path, project: str, cwd: Path) -> None:
    marker = enki_root / "PROJECT.last"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"project={project}\n"
        f"cwd={cwd}\n"
        f"initialized_at={datetime.now(timezone.utc).isoformat()}\n"
    )


def _mcp_template_path() -> Path:
    raw = os.environ.get("ENKI_MCP_TEMPLATE", str(Path.home() / ".enki" / "mcp-template.json"))
    return Path(raw).expanduser()


def _ensure_project_mcp_json(cwd: Path) -> dict[str, bool | str]:
    target = cwd / ".mcp.json"
    if target.exists():
        logger.info("enki_goal: .mcp.json already exists at %s; skipping", target)
        return {"created": False, "existing": True}

    template = _mcp_template_path()
    if not template.exists():
        warning = f"MCP template missing: {template}. Skipped writing .mcp.json"
        logger.warning("enki_goal: %s", warning)
        return {"created": False, "existing": False, "warning": warning}

    try:
        template_json = json.loads(template.read_text())
        target.write_text(json.dumps(template_json, indent=2) + "\n")
        logger.info("enki_goal: wrote .mcp.json at %s from template %s", target, template)
        return {"created": True, "existing": False}
    except Exception as e:
        warning = f"Failed to write .mcp.json from template {template}: {e}"
        logger.warning("enki_goal: %s", warning)
        return {"created": False, "existing": False, "warning": warning}


def _ensure_pipeline_md(enki_root: Path) -> None:
    pipeline_path = enki_root / "PIPELINE.md"
    implement_section = (
        "### implement\n"
        "- Call enki_wave(project) to get next wave\n"
        "- Execute Dev agent in FOREGROUND via Task tool — wait for completion\n"
        "- Call enki_report(role='dev', task_id=..., status='completed')\n"
        "- Execute QA agent in FOREGROUND via Task tool — wait for completion\n"
        "- Call enki_report(role='qa', task_id=..., status='completed')\n"
        "- Repeat until enki_wave returns no more tasks\n"
        "- NEVER background agents — foreground only for permission inheritance\n"
    )
    if not pipeline_path.exists():
        pipeline_path.write_text("# Enki Pipeline — Operational Reference\n\n" + implement_section)
        return
    text = pipeline_path.read_text()
    pattern = re.compile(r"(?ms)^### implement\s*\n.*?(?=^### |\Z)")
    if pattern.search(text):
        updated = pattern.sub(implement_section, text)
    else:
        updated = text.rstrip() + "\n\n" + implement_section
    if updated != text:
        pipeline_path.write_text(updated)


def enki_triage(description: str) -> dict:
    """Auto-detect tier from description.

    Optional — enki_goal already does triage.
    """
    return triage(description)


def enki_quick(description: str, project: str = ".") -> dict:
    """Fast-path for Minimal tier.

    Combines goal + triage + phase in one call.
    Sets goal, auto-triages as Minimal, jumps to implement.
    Gate 1 (goal) and Gate 3 (phase) satisfied immediately.
    Gate 2 (spec) doesn't apply to Minimal.
    """
    return quick(description, project)


def enki_phase(
    action: str,
    to: str | None = None,
    project: str | None = None,
) -> dict:
    """Advance phase with DB-backed precondition checks, or return status."""
    project = _resolve_project(project)

    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    current_raw = active.get("phase") or "planning"
    current = PHASE_ALIASES.get(current_raw, current_raw)

    if action == "status":
        tier = active.get("tier")
        goal = active.get("goal")
        current_idx = PHASE_ORDER.index(current) if current in PHASE_ORDER else -1
        next_phase = (
            PHASE_ORDER[current_idx + 1]
            if current_idx + 1 < len(PHASE_ORDER)
            else "done"
        )
        result = {
            "phase": current,
            "tier": tier or "not set",
            "goal": goal or "not set",
            "next_phase": next_phase,
            "pipeline": " → ".join(PHASE_ORDER),
        }
        if current == "implement":
            wave_status = _wave_status(project)
            result["wave_status"] = wave_status
            if wave_status == "NOT STARTED":
                result["mandatory_next"] = (
                    f"Call enki_wave(project='{project}') to spawn Wave 1 agents. "
                    "Do not read source files or explore code — that is agent work."
                )
            elif "in progress" in wave_status:
                result["mandatory_next"] = (
                    "Call enki_report for each completed agent, then enki_wave for next wave "
                    "when current wave is complete."
                )
        return result

    if action == "advance":
        if not to:
            return {"error": "Specify target phase with 'to' parameter"}
        target = PHASE_ALIASES.get(to, to)
        if target not in PHASE_ORDER:
            return {"error": f"Unknown phase: {to}"}

        current_idx = PHASE_ORDER.index(current) if current in PHASE_ORDER else -1
        target_idx = PHASE_ORDER.index(target)
        if target_idx != current_idx + 1:
            missing = f"sequential transition from {current} to {PHASE_ORDER[current_idx + 1] if current_idx + 1 < len(PHASE_ORDER) else 'complete'}"
            return {"error": f"Cannot advance to {target}. Required: {missing}"}

        missing = _phase_missing_preconditions(
            project,
            goal_id,
            current,
            target,
            active.get("tier") or "standard",
            active.get("started_at"),
        )
        if missing:
            return {"error": f"Cannot advance to {target}. Required: {missing}"}

        write_project_state(project, "phase", target)
        return {"phase": target, "required_next": _phase_required_next(target)}

    return {"error": f"Unknown action: {action}. Use 'advance' or 'status'."}


def enki_approve(
    stage: str,
    project: str | None = None,
    note: str | None = None,
) -> dict:
    """Create HITL approval record and advance project phase."""
    project = _resolve_project(project)
    stage_key = (stage or "").strip().lower()
    if stage_key not in APPROVAL_STAGES:
        return {
            "error": f"Unknown stage: {stage}. Expected one of {sorted(APPROVAL_STAGES)}"
        }

    with em_db(project) as conn:
        _ensure_hitl_approvals_table(conn)
        existing = conn.execute(
            "SELECT id FROM hitl_approvals WHERE project = ? AND stage = ? "
            "ORDER BY approved_at DESC, rowid DESC LIMIT 1",
            (project, stage_key),
        ).fetchone()
        if existing:
            approval_id = existing["id"]
            created = False
        else:
            approval_id = _next_human_approval_id(conn, project)
            conn.execute(
                "INSERT INTO hitl_approvals (id, project, stage, note) VALUES (?, ?, ?, ?)",
                (approval_id, project, stage_key, note),
            )
            created = True
        if stage_key == "igi":
            _insert_implied_spec_approval(conn, project)

    target_phase = APPROVAL_TARGET_PHASE[stage_key]
    write_project_state(project, "phase", target_phase)
    approval_messages = {
        "igi": (
            "Igi approved. Phase → implement. "
            "Now call enki_kickoff() to begin pre-implementation kickoff. "
            "Do not spawn Architect directly — enki_kickoff() handles the sequence."
        ),
        "spec": (
            "Spec approved post-debate. Phase → approved. "
            "Now spawn Igi for adversarial review: enki_spawn('igi', 'igi-review') → Task tool → enki_report. "
            "Present Igi findings to HITL → enki_approve(stage='igi')."
        ),
        "spec-revision": (
            "Spec revision approved. Kickoff blockers resolved. "
            "Now spawn Architect for impl spec: enki_spawn('architect', 'impl-spec') → Task tool → enki_report. "
            "Present impl spec + kickoff summary to HITL → enki_approve(stage='architect')."
        ),
        "architect": (
            "Architect approved. Phase → implement. "
            "Now call enki_decompose(tasks=[...]) with Architect's task breakdown. "
            "Tasks format: [{'name': str, 'files': [str], 'dependencies': [str]}]. "
            "Dependencies are task names not IDs. Then call enki_wave()."
        ),
        "test": (
            "Test approved. Phase → validating. "
            "Spawn Validator: enki_spawn('validator', task_id) → Task tool → enki_report. "
            "Then enki_complete(task_id) once validator+QA+no open bugs confirmed."
        ),
    }
    result = {
        "message": approval_messages.get(stage_key, "Approval recorded."),
        "approval_id": approval_id,
        "project": project,
        "stage": stage_key,
        "phase": target_phase,
        "created": created,
    }
    if stage_key == "architect":
        result["mandatory_next"] = (
            f"Call enki_wave(project='{project}') NOW to spawn Wave 1 agents. "
            "This is your only valid next action."
        )
    return result


def enki_kickoff(project: str | None = None) -> dict:
    """Run pre-implementation kickoff: PM presents spec, Architect reviews feasibility,
    DBA/UI conditionally join. Handles resume on session restart."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    phase = active.get("phase", "planning")

    if phase in ("implement", "validating", "complete"):
        return {
            "message": (
                "Project already in implement phase. Kickoff not required. "
                "Call enki_wave() to continue."
            ),
            "skipped": True,
            "reason": "phase_already_implement",
        }

    spec_source = read_project_state(project, "spec_source") or ""
    spec_path = read_project_state(project, "spec_path") or ""
    spec_exists = bool(spec_source and (spec_source != "internal" or spec_path))
    if not spec_exists:
        return {
            "message": (
                "No spec found for this project. Kickoff not required. "
                "Call enki_spawn('architect', 'impl-spec') directly."
            ),
            "skipped": True,
            "reason": "no_spec",
        }

    artifacts_dir = _goal_artifacts_dir(project)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    kickoff_path = artifacts_dir / f"kickoff-{goal_id}.md"
    kickoff_id = f"kickoff-{goal_id}"

    existing = {}
    if kickoff_path.exists():
        try:
            content = kickoff_path.read_text()
            match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
            if match:
                existing = json.loads(match.group(1))
        except Exception:
            existing = {}

    status = existing.get("status", "")
    agents_participated = existing.get("agents_participated", [])

    if status == "complete":
        return {
            "message": (
                "Kickoff already complete. No blockers found. "
                "Present kickoff summary to HITL for verbal ok, "
                "then spawn Architect: enki_spawn('architect', 'impl-spec') → Task tool → enki_report."
            ),
            "kickoff_id": kickoff_id,
            "agents_participated": agents_participated,
            "blockers_found": False,
            "blockers": [],
            "summary_path": str(kickoff_path),
            "resumed": True,
        }

    if status == "resolved":
        return {
            "message": (
                "Kickoff blockers resolved. "
                "Spawn Architect for impl spec: enki_spawn('architect', 'impl-spec') → Task tool → enki_report. "
                "Present impl spec + kickoff summary to HITL → enki_approve(stage='architect')."
            ),
            "kickoff_id": kickoff_id,
            "agents_participated": agents_participated,
            "blockers_found": True,
            "blockers": existing.get("blockers", []),
            "summary_path": str(kickoff_path),
            "resumed": True,
        }

    if status == "blockers_found":
        with em_db(project) as conn:
            spec_revision_approved = conn.execute(
                "SELECT id FROM hitl_approvals WHERE project = ? AND stage = ? LIMIT 1",
                (project, "spec-revision"),
            ).fetchone()
        if not spec_revision_approved:
            return {
                "message": (
                    "Kickoff found blockers awaiting HITL resolution. "
                    "Present blockers below to HITL. Once resolved, call enki_approve(stage='spec-revision', note='<resolution>'). "
                    "Then call enki_kickoff() again to proceed."
                ),
                "kickoff_id": kickoff_id,
                "agents_participated": agents_participated,
                "blockers_found": True,
                "blockers": existing.get("blockers", []),
                "summary_path": str(kickoff_path),
                "resumed": True,
            }
        existing["status"] = "resolved"
        kickoff_path.write_text(_format_md(existing))
        return {
            "message": (
                "Spec revision approved. Blockers resolved. "
                "Spawn Architect: enki_spawn('architect', 'impl-spec') → Task tool → enki_report."
            ),
            "kickoff_id": kickoff_id,
            "agents_participated": agents_participated,
            "blockers_found": True,
            "blockers": existing.get("blockers", []),
            "summary_path": str(kickoff_path),
            "resumed": True,
        }

    if not existing:
        existing = {
            "kickoff_id": kickoff_id,
            "project": project,
            "goal_id": goal_id,
            "status": "in_progress",
            "agents_participated": [],
            "dba_triggered": False,
            "ui_triggered": False,
            "blockers": [],
            "agent_outputs": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        kickoff_path.write_text(_format_md(existing))

    agents_to_run = []
    if "pm" not in agents_participated:
        agents_to_run.append("pm")
    if "architect" not in agents_participated:
        agents_to_run.append("architect")

    spawn_instructions = []
    for role in agents_to_run:
        spawn_result = enki_spawn(
            role,
            f"kickoff-{role}",
            {
                "mode": "kickoff",
                "kickoff_id": kickoff_id,
                "spec_source": spec_source,
                "spec_path": spec_path,
            },
            project,
        )
        spawn_instructions.append({
            "role": role,
            "prompt_path": spawn_result.get("prompt_path"),
            "context_artifact": spawn_result.get("context_artifact"),
            "instruction": spawn_result.get("instruction"),
        })

    existing["status"] = "in_progress"
    existing["spawn_instructions"] = spawn_instructions
    kickoff_path.write_text(_format_md(existing))

    return {
        "message": (
            f"Kickoff initialised. Execute {len(spawn_instructions)} agents sequentially. "
            "For each: read prompt_path verbatim → read context_artifact → Task tool foreground → enki_report. "
            "After PM completes: call enki_kickoff_update(role='pm', output={...}) to record DBA/UI signals. "
            "After all agents complete: call enki_kickoff_complete(project=...) to evaluate blockers and get summary."
        ),
        "kickoff_id": kickoff_id,
        "agents_participated": agents_participated,
        "spawn_instructions": spawn_instructions,
        "summary_path": str(kickoff_path),
        "status": "in_progress",
    }


def enki_kickoff_update(
    role: str,
    output: dict,
    project: str | None = None,
) -> dict:
    """Record a kickoff agent's output and update artifact progressively.
    Call after each kickoff agent completes via Task tool.
    For PM: reads DBA/UI signals from output and triggers conditional spawning.
    """
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    artifacts_dir = _goal_artifacts_dir(project)
    kickoff_path = artifacts_dir / f"kickoff-{goal_id}.md"

    if not kickoff_path.exists():
        return {"error": "No kickoff in progress. Call enki_kickoff() first."}

    try:
        content = kickoff_path.read_text()
        match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
        existing = json.loads(match.group(1)) if match else {}
    except Exception:
        return {"error": "Failed to read kickoff artifact."}

    existing.setdefault("agent_outputs", {})
    existing.setdefault("agents_participated", [])
    existing["agent_outputs"][role] = output
    if role not in existing["agents_participated"]:
        existing["agents_participated"].append(role)

    additional_spawns = []
    if role == "pm":
        dba_needed = output.get("dba_needed", False)
        ui_needed = output.get("ui_needed", False)
        existing["dba_triggered"] = dba_needed
        existing["ui_triggered"] = ui_needed

        participated = existing["agents_participated"]
        if dba_needed and "dba" not in participated:
            spawn_result = enki_spawn(
                "dba",
                "kickoff-dba",
                {
                    "mode": "kickoff",
                    "kickoff_id": existing.get("kickoff_id"),
                },
                project,
            )
            additional_spawns.append({
                "role": "dba",
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
            })
        if ui_needed and "ui_ux" not in participated:
            spawn_result = enki_spawn(
                "ui_ux",
                "kickoff-ui_ux",
                {
                    "mode": "kickoff",
                    "kickoff_id": existing.get("kickoff_id"),
                },
                project,
            )
            additional_spawns.append({
                "role": "ui_ux",
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
            })

    kickoff_path.write_text(_format_md(existing))

    if additional_spawns:
        return {
            "message": (
                f"PM output recorded. Additional agents triggered: {[s['role'] for s in additional_spawns]}. "
                "Execute each sequentially via Task tool, then call enki_kickoff_update for each. "
                "After all complete: call enki_kickoff_complete()."
            ),
            "role_recorded": role,
            "additional_spawns": additional_spawns,
        }

    return {
        "message": (
            f"Agent {role} output recorded. "
            "Continue with remaining kickoff agents, then call enki_kickoff_complete()."
        ),
        "role_recorded": role,
        "agents_participated": existing["agents_participated"],
    }


def enki_kickoff_complete(project: str | None = None) -> dict:
    """Evaluate all kickoff agent outputs, identify blockers, write final summary.
    Call after all kickoff agents have completed and been recorded via enki_kickoff_update.
    """
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    artifacts_dir = _goal_artifacts_dir(project)
    kickoff_path = artifacts_dir / f"kickoff-{goal_id}.md"

    if not kickoff_path.exists():
        return {"error": "No kickoff in progress. Call enki_kickoff() first."}

    try:
        content = kickoff_path.read_text()
        match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
        existing = json.loads(match.group(1)) if match else {}
    except Exception:
        return {"error": "Failed to read kickoff artifact."}

    blockers = []
    for role, output in existing.get("agent_outputs", {}).items():
        agent_blockers = output.get("blockers", [])
        for blocker in agent_blockers:
            blockers.append({
                "raised_by": role,
                "concern": blocker.get("concern", ""),
                "type": blocker.get("type", "unknown"),
                "resolution_options": blocker.get("resolution_options", []),
            })

    blockers_found = len(blockers) > 0
    existing["blockers"] = blockers
    existing["blockers_found"] = blockers_found
    existing["status"] = "blockers_found" if blockers_found else "complete"
    existing["completed_at"] = datetime.now(timezone.utc).isoformat()
    kickoff_path.write_text(_format_md(existing))

    if blockers_found:
        return {
            "message": (
                f"Kickoff complete. {len(blockers)} blocker(s) found. "
                "Present kickoff summary and blockers to HITL. "
                "Once HITL resolves each blocker, call enki_approve(stage='spec-revision', note='<resolution details>'). "
                "Then call enki_kickoff() again to proceed to impl spec."
            ),
            "kickoff_id": existing.get("kickoff_id"),
            "agents_participated": existing.get("agents_participated", []),
            "blockers_found": True,
            "blockers": blockers,
            "summary_path": str(kickoff_path),
        }

    return {
        "message": (
            "Kickoff complete. No blockers found. "
            "Present kickoff summary to HITL for verbal ok. "
            "Then spawn Architect: enki_spawn('architect', 'impl-spec') → Task tool → enki_report. "
            "Present impl spec + kickoff summary to HITL → enki_approve(stage='architect')."
        ),
        "kickoff_id": existing.get("kickoff_id"),
        "agents_participated": existing.get("agents_participated", []),
        "blockers_found": False,
        "blockers": [],
        "summary_path": str(kickoff_path),
    }


def enki_debate(project: str | None = None) -> dict:
    """Run multi-round spec debate before HITL approval.

    Round 1: Each agent reads spec-draft independently -> opening position.
    Round 2: Each agent reads spec-draft + all round 1 positions -> rebuttal.
    PM reconciliation: reads full debate -> writes spec-final + debate-summary.

    Brownfield detection: includes historical_context agent if Researcher
    Codebase Profile exists in project artifacts.

    Resumable: safe to call again after session restart.
    """
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    spec_source = read_project_state(project, "spec_source") or "internal"

    project_path = _get_project_path(project)
    if not project_path:
        return {"error": f"Project path not registered for {project}. Call enki_register first."}

    docs_dir = Path(project_path) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    spec_draft_path = docs_dir / "spec-draft.md"
    spec_final_path = docs_dir / "spec-final.md"
    debate_summary_path = docs_dir / "debate-summary.md"

    if not spec_draft_path.exists():
        return {
            "error": (
                "No spec-draft.md found in docs/. "
                "PM must write the draft spec to docs/spec-draft.md before debate can begin."
            )
        }

    artifacts_dir = _goal_artifacts_dir(project)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    debate_artifact_path = artifacts_dir / f"debate-{goal_id}.md"
    debate_id = f"debate-{goal_id}"

    researcher_profiles = list(artifacts_dir.glob("spawn-researcher-*.md"))
    has_researcher_profile = len(researcher_profiles) > 0

    base_agents = ["cto", "devils_advocate", "tech_feasibility"]
    if has_researcher_profile:
        base_agents.append("historical_context")

    existing = {}
    if debate_artifact_path.exists():
        try:
            content = debate_artifact_path.read_text()
            match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
            if match:
                existing = json.loads(match.group(1))
        except Exception:
            existing = {}

    status = existing.get("status", "")

    if status == "complete":
        return {
            "message": (
                "Debate already complete. "
                "Present docs/debate-summary.md and docs/spec-final.md to HITL for review. "
                "After HITL approves call enki_approve(stage='spec')."
            ),
            "debate_id": debate_id,
            "agents_participated": existing.get("agents_participated", []),
            "rounds_completed": existing.get("rounds_completed", 2),
            "spec_final_path": str(spec_final_path),
            "debate_summary_path": str(debate_summary_path),
            "changes_made": existing.get("changes_made", False),
            "resumed": True,
        }

    if not existing:
        existing = {
            "debate_id": debate_id,
            "project": project,
            "goal_id": goal_id,
            "status": "in_progress",
            "agents": base_agents,
            "agents_participated": [],
            "has_researcher_profile": has_researcher_profile,
            "spec_source": spec_source,
            "spec_draft_path": str(spec_draft_path),
            "rounds": {
                "round_1": {},
                "round_2": {},
                "reconciliation": {},
            },
            "rounds_completed": 0,
            "changes_made": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        debate_artifact_path.write_text(_format_md(existing))

    round_1_done = set(existing["rounds"].get("round_1", {}).keys())
    round_2_done = set(existing["rounds"].get("round_2", {}).keys())
    reconciliation_done = bool(existing["rounds"].get("reconciliation", {}).get("pm"))

    agents = existing["agents"]
    round_1_pending = [a for a in agents if a not in round_1_done]
    round_2_pending = [a for a in agents if a not in round_2_done]

    spawn_instructions = []

    if round_1_pending:
        for role in round_1_pending:
            context = {
                "debate_round": 1,
                "debate_id": debate_id,
                "spec_draft_path": str(spec_draft_path),
                "instruction": (
                    "You are participating in Round 1 of a spec debate. "
                    f"Read the spec at {spec_draft_path}. "
                    "Produce your opening position challenging the spec from your role perspective. "
                    "Be specific - reference exact sections, assumptions, or decisions you challenge. "
                    "Output structured JSON with: {'opening_position': str, 'challenges': [{issue, section, severity}], 'questions': [str]}"
                ),
            }
            if role == "historical_context" and researcher_profiles:
                context["codebase_profile_path"] = str(researcher_profiles[-1])
                context["instruction"] += (
                    f" Also read the Codebase Profile at {researcher_profiles[-1]} "
                    "for historical context on past decisions and patterns."
                )

            spawn_result = enki_spawn(role, f"debate-r1-{role}", context, project)
            spawn_instructions.append({
                "round": 1,
                "role": role,
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
                "instruction": spawn_result.get("instruction"),
            })

    elif round_2_pending:
        round_1_outputs = existing["rounds"].get("round_1", {})
        for role in round_2_pending:
            context = {
                "debate_round": 2,
                "debate_id": debate_id,
                "spec_draft_path": str(spec_draft_path),
                "round_1_positions": round_1_outputs,
                "instruction": (
                    "You are participating in Round 2 of a spec debate. "
                    f"Read the spec at {spec_draft_path}. "
                    "Read all Round 1 positions from the other debate agents provided in round_1_positions. "
                    "Produce your rebuttal - agree, challenge, or build on specific points others raised. "
                    "Be direct. Reference specific agent positions by name. "
                    "Output structured JSON with: {'rebuttal': str, 'agreements': [{agent, point}], 'disagreements': [{agent, point, counter_argument}], 'new_concerns': [str]}"
                ),
            }
            spawn_result = enki_spawn(role, f"debate-r2-{role}", context, project)
            spawn_instructions.append({
                "round": 2,
                "role": role,
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
                "instruction": spawn_result.get("instruction"),
            })

    elif not reconciliation_done:
        round_1_outputs = existing["rounds"].get("round_1", {})
        round_2_outputs = existing["rounds"].get("round_2", {})
        context = {
            "debate_round": "reconciliation",
            "debate_id": debate_id,
            "spec_draft_path": str(spec_draft_path),
            "spec_final_path": str(spec_final_path),
            "debate_summary_path": str(debate_summary_path),
            "round_1_positions": round_1_outputs,
            "round_2_rebuttals": round_2_outputs,
            "instruction": (
                "You are reconciling the spec debate. "
                f"Read the draft spec at {spec_draft_path}. "
                "Read all Round 1 positions and Round 2 rebuttals. "
                "Your task: "
                "1. Identify which challenges were valid and require spec changes. "
                "2. Identify which challenges were rejected and why. "
                f"3. Write the updated final spec to {spec_final_path} - incorporate valid changes, preserve original intent where challenges were rejected. "
                f"4. Write debate-summary.md to {debate_summary_path} containing: "
                "   - Key challenges raised per agent "
                "   - How each was resolved (accepted/rejected/modified) "
                "   - What changed from draft to final spec "
                "   - What was rejected and the reasoning "
                "5. Output JSON: {'changes_made': bool, 'changes_summary': str, 'rejected_summary': str}"
            ),
        }
        spawn_result = enki_spawn("pm", "debate-reconcile", context, project)
        spawn_instructions.append({
            "round": "reconciliation",
            "role": "pm",
            "prompt_path": spawn_result.get("prompt_path"),
            "context_artifact": spawn_result.get("context_artifact"),
            "instruction": spawn_result.get("instruction"),
        })

    existing["spawn_instructions"] = spawn_instructions
    debate_artifact_path.write_text(_format_md(existing))

    if round_1_pending:
        current_round = f"Round 1 ({len(round_1_pending)} agents pending)"
        next_instruction = (
            f"Execute {len(spawn_instructions)} agents sequentially via Task tool. "
            "For each: read prompt_path verbatim -> read context_artifact -> Task tool foreground -> "
            "call enki_debate_update(role=..., round=1, output={...}). "
            "After all Round 1 agents complete: call enki_debate() again to proceed to Round 2."
        )
    elif round_2_pending:
        current_round = f"Round 2 ({len(round_2_pending)} agents pending)"
        next_instruction = (
            f"Execute {len(spawn_instructions)} agents sequentially via Task tool. "
            "For each: read prompt_path verbatim -> read context_artifact -> Task tool foreground -> "
            "call enki_debate_update(role=..., round=2, output={...}). "
            "After all Round 2 agents complete: call enki_debate() again to proceed to reconciliation."
        )
    else:
        current_round = "Reconciliation"
        next_instruction = (
            "Execute PM reconciliation via Task tool. "
            "Read prompt_path verbatim -> read context_artifact -> Task tool foreground -> "
            "call enki_debate_update(role='pm', round='reconciliation', output={...}). "
            "After PM completes: call enki_debate() again to finalize."
        )

    return {
        "message": f"Debate {current_round}. {next_instruction}",
        "debate_id": debate_id,
        "current_round": current_round,
        "agents": agents,
        "agents_participated": existing["agents_participated"],
        "spawn_instructions": spawn_instructions,
        "debate_artifact_path": str(debate_artifact_path),
        "has_researcher_profile": has_researcher_profile,
        "status": "in_progress",
    }


def enki_debate_update(
    role: str,
    round: str,
    output: dict,
    project: str | None = None,
) -> dict:
    """Record a debate agent's output progressively.
    Call after each debate agent completes via Task tool.
    round: '1', '2', or 'reconciliation'
    """
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    artifacts_dir = _goal_artifacts_dir(project)
    debate_artifact_path = artifacts_dir / f"debate-{goal_id}.md"

    if not debate_artifact_path.exists():
        return {"error": "No debate in progress. Call enki_debate() first."}

    try:
        content = debate_artifact_path.read_text()
        match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
        existing = json.loads(match.group(1)) if match else {}
    except Exception:
        return {"error": "Failed to read debate artifact."}

    round_key = f"round_{round}" if round in ("1", "2") else "reconciliation"
    existing.setdefault("rounds", {})
    existing.setdefault("agents_participated", [])

    if round_key == "reconciliation":
        existing["rounds"].setdefault("reconciliation", {})
        existing["rounds"]["reconciliation"]["pm"] = output
        existing["agents_participated"].append("pm-reconcile")
        existing["changes_made"] = output.get("changes_made", False)
        existing["status"] = "complete"
        existing["rounds_completed"] = 2
        existing["completed_at"] = datetime.now(timezone.utc).isoformat()
        debate_artifact_path.write_text(_format_md(existing))
        return {
            "message": (
                "PM reconciliation recorded. Debate complete. "
                "Call enki_debate() to get final summary and next steps."
            ),
            "role_recorded": "pm-reconcile",
            "status": "complete",
        }

    existing["rounds"].setdefault(round_key, {})
    existing["rounds"][round_key][role] = output
    agent_round_key = f"{role}-r{round}"
    if agent_round_key not in existing["agents_participated"]:
        existing["agents_participated"].append(agent_round_key)

    agents = existing.get("agents", [])
    round_done = set(existing["rounds"][round_key].keys())
    all_done = all(a in round_done for a in agents)

    debate_artifact_path.write_text(_format_md(existing))

    if all_done:
        existing["rounds_completed"] = int(round)
        debate_artifact_path.write_text(_format_md(existing))
        return {
            "message": (
                f"Round {round} complete - all agents recorded. "
                "Call enki_debate() to proceed to next round."
            ),
            "role_recorded": agent_round_key,
            "round_complete": True,
            "rounds_completed": int(round),
        }

    remaining = [a for a in agents if a not in round_done]
    return {
        "message": (
            f"Agent {role} Round {round} recorded. "
            f"Remaining agents this round: {remaining}. "
            "Continue executing remaining agents, then call enki_debate_update for each."
        ),
        "role_recorded": agent_round_key,
        "round_complete": False,
        "remaining_agents": remaining,
    }


# ── PM ──


def enki_intake(answers: dict, project: str = ".") -> dict:
    """Validate intake answers against PM checklist.

    Required: outcome, audience, constraints, success_criteria, scope, risks.
    """
    return validate_intake(answers)


def enki_spec(
    spec_type: str,
    content: str,
    project: str = ".",
) -> dict:
    """Create a spec (product or implementation)."""
    decision_id = create_spec(project, spec_type, content)
    return {"spec_type": spec_type, "decision_id": decision_id}


def enki_is_approved(project: str = ".") -> dict:
    """Check if spec is approved."""
    return {"approved": is_spec_approved(project)}


def enki_decision(
    decision_type: str,
    proposed_action: str,
    context: str | None = None,
    project: str = ".",
) -> dict:
    """Record a PM decision."""
    decision_id = record_decision(
        project, decision_type, proposed_action, context,
    )
    return {"decision_id": decision_id}


# ── Orchestration ──


def enki_decompose(tasks: list[dict], project: str = ".") -> dict:
    """Break spec into task DAG.

    Args:
        tasks: List of {name, files, dependencies} dicts
        project: Project ID
    """
    sprint_id = create_sprint(project, "sprint-1")
    with em_db(project) as conn:
        conn.execute(
            "UPDATE sprint_state SET status = 'active' WHERE sprint_id = ?",
            (sprint_id,),
        )

    # Pass 1: create all tasks, build name→id map
    name_to_id = {}
    created = []
    for task_def in tasks:
        task_id = create_task(
            project=project,
            sprint_id=sprint_id,
            task_name=task_def["name"],
            tier="standard",
            assigned_files=task_def.get("files", []),
            dependencies=[],
        )
        name_to_id[task_def["name"]] = task_id
        created.append({
            "task_id": task_id,
            "name": task_def["name"],
            "files": task_def.get("files", []),
            "dependencies": task_def.get("dependencies", []),
        })

    # Pass 2: resolve dependency names to IDs and update task_state
    with em_db(project) as conn:
        for item in created:
            resolved_deps = [
                name_to_id.get(d, d)
                for d in item["dependencies"]
            ]
            conn.execute(
                "UPDATE task_state SET dependencies = ? WHERE task_id = ?",
                (json.dumps(resolved_deps), item["task_id"]),
            )
            item["resolved_dependencies"] = resolved_deps

    return {
        "message": (
            f"Sprint created with {len(created)} tasks. "
            "Now call enki_wave() to spawn Dev+QA agents for Wave 1. "
            "Do not read source files or plan implementation yourself — that is agent work."
        ),
        "sprint_id": sprint_id,
        "tasks": created,
        "total_tasks": len(created),
    }


def enki_orchestrate(project: str = ".") -> dict:
    """Begin execution — EM starts spawning tasks.

    Prerequisites: goal set, phase >= implement, spec approved (Standard/Full).
    """
    orch = Orchestrator(project)
    state = get_project_state(project)

    if not state.get("goal"):
        return {"error": "No goal set. Use enki_goal first."}

    phase = state.get("phase")
    if phase not in ("implement", "validating", "review", "complete"):
        return {"error": f"Phase must be implement+, currently: {phase}"}

    tier = state.get("tier", "minimal")
    if tier != "minimal" and not is_spec_approved(project):
        return {"error": "Spec must be approved for Standard/Full tier"}

    next_actions = orch.get_next_actions()
    return {
        "status": "started",
        "project": project,
        "tier": tier,
        "next_actions": next_actions,
    }


def enki_spawn(
    role: str,
    task_id: str,
    context: dict | None = None,
    project: str | None = None,
) -> dict:
    """Prepare an agent run (step 1) and mark status in_progress."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    role_key = role.strip().lower()
    if role_key not in VALID_AGENT_ROLES:
        return {"error": f"Unknown role: {role_key}"}
    try:
        prompt = _load_authored_prompt(role_key)
        task = get_task(project, task_id) or {}
        merged_context = {"task": task, **(context or {})}
        merged_context = _inject_external_spec_mode(project, role_key, merged_context)
        filtered_context = _apply_blind_wall(role_key, merged_context)

        spawn_payload = {
            "role": role_key,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_path": str(ENKI_ROOT / "prompts" / f"{role_key}.md"),
            "prompt": prompt,
            "context": filtered_context,
            "execution_mode": "foreground_sequential",
            "instruction": (
                "Run this agent in foreground via Task tool. "
                "Wait for completion before starting next agent."
            ),
        }
        artifact = _goal_artifacts_dir(project) / f"spawn-{role_key}-{task_id}.md"
        artifact.write_text(_format_md(spawn_payload))

        _upsert_agent_status(goal_id, role_key, "in_progress")
        _upsert_agent_status(goal_id, f"{role_key}:{task_id}", "in_progress")
        return {
            "message": (
                f"Agent {role_key} prepared. "
                f"1. Read prompt file verbatim: ~/.enki/prompts/{role_key}.md — never substitute your own prompt. "
                f"2. Read context artifact in chunks if large: {artifact} — never skip, never summarize. "
                "3. Run via Task tool in foreground — never Background tool. "
                f"4. After Task completes: call enki_report(role='{role_key}', task_id='{task_id}', summary=..., status='completed'|'failed')."
            ),
            "role": role_key,
            "status": "in_progress",
            "execution_mode": "foreground_sequential",
            "instruction": (
                "Run this agent in foreground via Task tool. "
                "Wait for completion before starting next agent."
            ),
            "prompt_path": f"~/.enki/prompts/{role_key}.md",
            "context_artifact": str(artifact),
            "task_id": task_id,
        }
    except Exception as e:
        return {
            "role": role_key,
            "status": "failed",
            "task_id": task_id,
            "message": str(e),
        }


def enki_report(
    role: str,
    task_id: str,
    summary: str,
    status: str = "completed",
    project: str | None = None,
) -> dict:
    """Record agent completion/failure (step 2) after Task execution."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    goal_id = active["goal_id"]
    role_key = role.strip().lower()
    if role_key not in VALID_AGENT_ROLES:
        return {"error": f"Unknown role: {role_key}"}
    normalized_status = (status or "completed").strip().lower()
    if normalized_status not in {"completed", "failed"}:
        return {"error": "status must be 'completed' or 'failed'"}

    if not _has_agent_status(goal_id, f"{role_key}:{task_id}", "in_progress") and not _has_agent_status(goal_id, role_key, "in_progress"):
        return {"error": f"Cannot report for {role_key}. Required: agent_status is in_progress."}

    _upsert_agent_status(goal_id, role_key, normalized_status)
    _upsert_agent_status(goal_id, f"{role_key}:{task_id}", normalized_status)

    artifact = _goal_artifacts_dir(project) / f"{role_key}-{task_id}.md"
    artifact.write_text(
        _format_md(
            {
                "role": role_key,
                "task_id": task_id,
                "status": normalized_status,
                "summary": summary,
                "reported_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )

    findings = [summary]
    if normalized_status == "failed":
        findings.append("failure reported")
    _mail_em(project, role_key, task_id, normalized_status, findings)

    return {
        "message": (
            f"Agent {role_key} recorded as {normalized_status} for task {task_id}. "
            + (
                "Run next agent in sequence, then enki_report. "
                "After all wave agents reported: enki_mail_inbox() then enki_wave()."
                if normalized_status == "completed"
                else
                "Agent failed. Call enki_escalate(task_id, reason) immediately — never take over agent work."
            )
        ),
        "role": role_key,
        "task_id": task_id,
        "status": normalized_status,
    }


def enki_wave(project: str | None = None) -> dict:
    """Prepare next wave's Dev+QA agent runs for external Task execution."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    goal_id = active["goal_id"]
    if not (_has_hitl_approval(project, "spec") or _has_hitl_approval(project, "igi")):
        return {"error": "Specs not approved."}

    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint found."}
    sprint_id = sprint["sprint_id"]
    tasks = get_next_wave(project, sprint_id)
    if not tasks:
        return {"error": "No tasks ready for next wave."}

    wave_no = _next_wave_number(project)
    agents = []
    for task in tasks:
        ctx = {
            "task_name": task.get("task_name"),
            "assigned_files": task.get("assigned_files", []),
            "dependencies": task.get("dependencies", []),
        }
        dev = enki_spawn("dev", task["task_id"], ctx, project)
        qa = enki_spawn("qa", task["task_id"], ctx, project)
        for item in (dev, qa):
            agents.append({
                "role": item.get("role"),
                "task_id": item.get("task_id"),
                "prompt_path": item.get("prompt_path"),
                "context_artifact": item.get("context_artifact"),
            })

    report_path = _goal_artifacts_dir(project) / f"wave-{wave_no}.md"
    wave_instruction = (
        "Execute agents sequentially in foreground — one at a time, not in parallel. "
        "Start Dev agent first. Wait for completion. "
        "Call enki_report(role='dev', task_id=..., status='completed'). "
        "Then start QA agent. Wait for completion. "
        "Call enki_report(role='qa', task_id=..., status='completed'). "
        "Do not background agents — foreground execution inherits write permissions."
    )
    report_path.write_text(
        _format_md(
            {
                "goal_id": goal_id,
                "wave_number": wave_no,
                "task_ids": [t["task_id"] for t in tasks],
                "agents": agents,
                "execution_mode": "foreground_sequential",
                "execution_order": ["dev", "qa"],
                "instruction": wave_instruction,
            }
        )
    )

    return {
        "message": (
            f"Wave {wave_no} ready with {len(agents)} agents. "
            "Execute sequentially — Dev first, then QA. Never parallel. Never Background. "
            "For each agent: read prompt_path verbatim → read context_artifact in chunks → Task tool foreground → enki_report. "
            "After all agents reported: call enki_mail_inbox() to read agent messages. "
            "Then call enki_wave() for next wave, or enki_complete(task_id) if this is the final wave."
        ),
        "wave_number": wave_no,
        "agents": agents,
        "execution_mode": "foreground_sequential",
        "instruction": wave_instruction,
    }


def enki_complete(task_id: str, project: str | None = None) -> dict:
    """Mark completion only if validator/QA/wave checks are satisfied."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    goal_id = active["goal_id"]

    missing = []
    if not _has_agent_status(goal_id, f"validator:{task_id}", "completed"):
        missing.append("validator completion for task")

    qa_ok = _has_agent_status(goal_id, f"qa:{task_id}", "completed")
    if not qa_ok:
        missing.append("QA pass for task")

    with em_db(project) as conn:
        open_bugs = conn.execute(
            "SELECT COUNT(*) AS c FROM bugs WHERE task_id = ? AND status != 'closed'",
            (task_id,),
        ).fetchone()["c"]
    if open_bugs:
        missing.append("no unresolved QA failures")

    if not _all_wave_tasks_completed(project):
        missing.append("all wave tasks completed")

    if missing:
        return {"error": f"Cannot complete. Required: {', '.join(missing)}"}

    update_task_status(project, task_id, TaskStatus.COMPLETED)
    return {
        "message": (
            f"Task {task_id} complete. "
            "Call enki_wave() for next wave. "
            "If all waves done: enki_phase(action='status') to confirm, then enki_wrap() to close session."
        ),
        "completion_status": "completed",
        "summary": f"Task {task_id} completed with validator+QA gates satisfied.",
    }


def enki_wrap() -> dict:
    """Run transcript-based session-end memory curation pipeline."""
    project = Path.cwd().name
    transcript = _find_session_transcript()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = ENKI_ROOT / "artifacts" / f"wrap-{timestamp}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if not transcript:
        payload = {
            "project": project,
            "status": "no_transcript",
            "message": "No transcript found. Session wrap skipped.",
        }
        report_path.write_text(_format_md(payload))
        return {
            "candidates_extracted": 0,
            "promoted": 0,
            "discarded": 0,
            "message": "No transcript found. Session wrap skipped.",
        }

    messages = _extract_wrap_messages(Path(transcript))
    if not messages:
        payload = {
            "project": project,
            "transcript": transcript,
            "status": "empty_transcript",
            "message": "Session wrapped. Transcript empty after filtering.",
        }
        report_path.write_text(_format_md(payload))
        return {
            "candidates_extracted": 0,
            "promoted": 0,
            "discarded": 0,
            "message": "Session wrapped. Transcript empty after filtering.",
        }

    chunks = _chunk_wrap_messages(messages)
    model = _choose_ollama_model()
    if not model:
        payload = {
            "project": project,
            "transcript": transcript,
            "messages": len(messages),
            "chunks": len(chunks),
            "status": "ollama_unavailable",
            "message": "Ollama unavailable — candidates not extracted",
        }
        report_path.write_text(_format_md(payload))
        return {
            "candidates_extracted": 0,
            "promoted": 0,
            "discarded": 0,
            "message": "Ollama unavailable — candidates not extracted",
        }

    extracted_items = []
    for chunk in chunks:
        prompt = _wrap_extraction_prompt(chunk)
        output = _run_ollama_extract(model, prompt)
        extracted_items.extend(_parse_ollama_items(output))

    session_id = Path(transcript).stem
    staged, duplicates = _stage_wrap_candidates(extracted_items, project, session_id)

    promoted = 0
    discarded = 0
    gemini_error = None
    try:
        decisions = gemini_review.run_api_review(project=project).get("bead_decisions", [])
        promoted, discarded = _apply_wrap_gemini_decisions(project, decisions)
    except Exception as e:
        gemini_error = str(e)

    payload = {
        "project": project,
        "transcript": transcript,
        "messages_extracted": len(messages),
        "chunks": len(chunks),
        "model": model,
        "extracted_items": extracted_items,
        "staged": staged,
        "duplicates": duplicates,
        "promoted": promoted,
        "discarded": discarded,
        "gemini_error": gemini_error,
    }
    report_path.write_text(_format_md(payload))

    if gemini_error:
        msg = (
            f"Session wrapped. Transcript: {len(messages)} messages extracted. "
            f"Candidates: {staged} staged, {promoted} promoted, {discarded} discarded. "
            "Gemini review failed; raw candidates retained."
        )
    else:
        msg = (
            f"Session wrapped. Transcript: {len(messages)} messages extracted. "
            f"Candidates: {staged} staged, {promoted} promoted, {discarded} discarded. "
            "Memory ready for next session."
        )
    return {
        "candidates_extracted": staged,
        "promoted": promoted,
        "discarded": discarded,
        "message": msg,
    }


def enki_next_actions(project: str = ".") -> list[dict]:
    """Get next tasks ready to spawn."""
    orch = Orchestrator(project)
    return orch.get_next_actions()


def enki_task_status(project: str = ".", task_id: str | None = None) -> dict:
    """Get status of tasks (all or specific)."""
    if task_id:
        task = get_task(project, task_id)
        return task if task else {"error": f"Task {task_id} not found"}

    state = get_project_state(project)
    return {
        "project": project,
        "state": state,
    }


def enki_mark_done(task_id: str, output: dict, project: str = ".") -> dict:
    """Mark task complete with output. Triggers next wave."""
    orch = Orchestrator(project)
    return orch.process_agent_output(task_id, str(output))


def enki_mark_blocked(task_id: str, reason: str, project: str = ".") -> dict:
    """Mark task blocked."""
    update_task_status(project, task_id, TaskStatus.BLOCKED)
    return {"task_id": task_id, "status": "blocked", "reason": reason}


def enki_escalate(task_id: str, reason: str, project: str = ".") -> dict:
    """Escalate to human (HITL)."""
    orch = Orchestrator(project)
    msg_id = orch.escalate_to_human(task_id, reason)
    return {"task_id": task_id, "status": "hitl", "message_id": msg_id}


# ── Mail ──


def enki_mail_inbox(agent: str = "EM", project: str = ".") -> list[dict]:
    """Get unread messages."""
    return get_inbox(project, agent)


def enki_mail_thread(thread_id: str, project: str = ".") -> list[dict]:
    """Get full thread history."""
    return get_thread_messages(project, thread_id)


# ── Bugs ──


def enki_bug(
    action: str,
    title: str | None = None,
    description: str | None = None,
    severity: str = "medium",
    bug_id: str | None = None,
    project: str | None = None,
) -> dict:
    """File or manage bugs."""
    project = _resolve_project(project)
    priority_map = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
    priority = priority_map.get(severity, "P2")

    if action == "file":
        internal_id = file_bug(
            project=project,
            title=title or "Untitled bug",
            description=description or "",
            filed_by="Human",
            priority=priority,
        )
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT bug_number FROM bugs WHERE id = ?",
                (internal_id,),
            ).fetchone()
        human_id = to_human_bug_id(project, int(row["bug_number"]))
        return {"bug_id": human_id, "action": "filed"}
    elif action == "close":
        if not bug_id:
            return {"error": "bug_id required for close"}
        resolved = resolve_bug_identifier(project, bug_id)
        if not resolved:
            return {"error": f"bug_id not found: {bug_id}"}
        internal_id, human_id = resolved
        close_bug(project, internal_id)
        return {"bug_id": human_id, "action": "closed"}
    elif action == "list":
        bugs = list_bugs(project)
        normalized = []
        for bug in bugs:
            item = dict(bug)
            bug_number = item.get("bug_number")
            item["bug_id"] = to_human_bug_id(project, int(bug_number)) if bug_number else None
            item["internal_id"] = item.get("id")
            normalized.append(item)
        return {"bugs": normalized, "count": len(normalized)}
    else:
        return {"error": f"Unknown action: {action}"}


# ── Status ──


def enki_status_update(project: str | None = None) -> dict:
    """Generate status update."""
    project = _resolve_project(project)
    text = generate_status_update(project)
    return {"status_text": text}


def enki_sprint_summary(sprint_id: str, project: str = ".") -> dict:
    """Get sprint summary."""
    return get_sprint_summary(project, sprint_id)


# ── Memory Bridge ──


def enki_extract_beads(project: str = ".") -> dict:
    """Extract beads from completed project's em.db."""
    candidates = extract_beads_from_project(project)
    return {"candidates": candidates, "count": len(candidates)}


# ── Onboarding ──


def enki_detect_entry(signals: dict) -> dict:
    """Detect entry point."""
    entry = detect_entry_point(signals)
    return {"entry_point": entry}


def enki_profile(
    action: str = "get",
    key: str | None = None,
    value: str | None = None,
) -> dict:
    """Manage user profile."""
    if action == "get":
        return get_or_create_user_profile()
    elif action == "set" and key and value:
        update_user_profile(key, value, source="explicit")
        return {"key": key, "value": value, "updated": True}
    else:
        return {"error": "Invalid action or missing key/value"}


# ── Private helpers ──


def _inject_external_spec_mode(project: str, role: str, context: dict) -> dict:
    """Inject PM endorsement mode when an external spec is configured."""
    if role != "pm":
        return context
    source = (read_project_state(project, "spec_source", "internal") or "internal").strip().lower()
    spec_path = (read_project_state(project, "spec_path", "") or "").strip()
    if source != "external" or not spec_path:
        return context

    try:
        spec_text = Path(spec_path).read_text()
    except OSError as e:
        logger.warning("Failed to read external spec at %s: %s", spec_path, e)
        return context

    external_block = (
        "## External Spec Mode\n\n"
        "A spec has been provided externally and is attached below. Your job is NOT\n"
        "to write a new spec from scratch. Your job is to:\n\n"
        "1. Read and fully internalise the provided spec\n"
        "2. Identify any gaps, ambiguities, unstated assumptions, or conflicts\n"
        "3. Document your findings as PM notes\n"
        "4. Endorse the spec with your notes — or flag blockers if the spec is\n"
        "   insufficient to proceed\n"
        "5. Produce a PM Endorsement document (not a rewrite) that confirms the\n"
        "   spec is understood and ready for debate and Igi challenge\n\n"
        "The spec you are reviewing:\n"
    )
    enriched = dict(context)
    enriched["pm_mode"] = "external_spec_endorsement"
    enriched["pm_instruction"] = external_block
    enriched["external_spec_path"] = spec_path
    enriched["external_spec_contents"] = spec_text
    enriched["pm_output_requirement"] = "PM output in this mode: a PM Endorsement document, not a full spec rewrite."
    return enriched


def _next_step_hint(tier: str) -> str:
    """Hint for what to do after setting goal."""
    base = (
        "Goal set. Tier: {tier}. "
        "Call enki_recall to load relevant context. "
        "Perform planning phase checks (see CLAUDE.md — architectural completeness, assumption surfacing, scope pressure test). "
        "Then begin Q&A intake with human to flesh out requirements. "
        "When requirements are clear, spawn PM: enki_spawn('pm', 'spec-draft') → Task tool → enki_report."
    )
    return base.format(tier=tier)


def _phase_hint(phase: str) -> str:
    """Hint for current phase."""
    hints = {
        "intake": "Answer PM intake checklist: outcome, audience, constraints, scope, risks",
        "debate": "Run multi-perspective analysis on approach",
        "spec": "Create product and implementation specs",
        "approve": "Human approves spec",
        "implement": "Gate 3 satisfied — code changes allowed",
        "review": "Sprint-level review of all changes",
        "complete": "Qualify, deploy, verify",
    }
    return hints.get(phase, "")


def _get_active_goal(project: str) -> dict | None:
    project = normalize_project_name(project)
    goal = (read_project_state(project, "goal") or "").strip()
    if not goal or goal.lower() == "none":
        return None
    stable_id = stable_goal_id(project)
    gid = read_project_state(project, "goal_id")
    if gid != stable_id:
        gid = stable_id
        write_project_state(project, "goal_id", gid)
    return {
        "goal_id": gid,
        "goal": goal,
        "tier": read_project_state(project, "tier"),
        "phase": read_project_state(project, "phase"),
        "started_at": None,
    }


def _require_active_goal(project: str) -> dict:
    active = _get_active_goal(project)
    if not active:
        return {"error": "No active goal. Use enki_goal first."}
    return active


def _phase_missing_preconditions(
    project: str,
    goal_id: str,
    current: str,
    target: str,
    tier: str,
    goal_started_at: str | None,
) -> str | None:
    if target == "spec":
        if current == "planning" and tier != "minimal":
            igi_status = _get_agent_status(goal_id, "igi")
            if igi_status != "completed":
                return (
                    "BLOCKED. Igi (challenge review) not completed.\n\n"
                    "Spawn Igi for independent challenge review:\n"
                    "  enki_spawn('igi', 'challenge-review')\n"
                    "Then execute via Task tool, then call enki_report."
                )
            challenge_count = _count_challenge_notes(project, goal_started_at)
            if challenge_count == 0:
                return (
                    "BLOCKED. No challenge notes found.\n\n"
                    "Record Igi's findings with enki_remember(category='challenge').\n"
                    "Each gap, assumption, or risk should be a separate note."
                )
    elif target == "approved":
        if not _has_hitl_approval(project, "spec"):
            return "HITL approval record for stage 'spec'"
    elif target == "implement":
        if not _has_agent_status(goal_id, "architect", "completed"):
            return "Architect agent completed"
        if not _has_hitl_approval(project, "architect"):
            return "HITL approval record for stage 'architect'"
    elif target == "validating":
        if not _all_wave_tasks_completed(project):
            return "all waves completed"
    elif target == "complete":
        if not _has_hitl_approval(project, "test"):
            return "HITL approval record for stage 'test'"
        if not _has_validator_signoff(project, goal_id):
            return "Validator sign-off exists"
    return None


def _phase_required_next(phase: str) -> str:
    hints = {
        "spec": "Run spec authoring, then record HITL approval with enki_approve(stage='spec').",
        "approved": "Complete architecture and record HITL approval with enki_approve(stage='architect').",
        "implement": "Execute waves with enki_wave until all tasks complete.",
        "validating": "Spawn validator, record sign-off, then enki_approve(stage='test').",
        "complete": "Pipeline complete.",
    }
    return hints.get(phase, "Continue pipeline.")


def _upsert_agent_status(goal_id: str, agent_role: str, status: str) -> None:
    with uru_db() as conn:
        conn.execute(
            "INSERT INTO agent_status (goal_id, agent_role, status, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(goal_id, agent_role) DO UPDATE SET "
            "status = excluded.status, updated_at = datetime('now')",
            (goal_id, agent_role, status),
        )


def _has_agent_status(goal_id: str, agent_role: str, status: str) -> bool:
    with uru_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM agent_status WHERE goal_id = ? AND agent_role = ? AND status = ? LIMIT 1",
            (goal_id, agent_role, status),
        ).fetchone()
    return row is not None


def _get_agent_status(goal_id: str, agent_role: str) -> str | None:
    with uru_db() as conn:
        row = conn.execute(
            "SELECT status FROM agent_status WHERE goal_id = ? AND agent_role = ? LIMIT 1",
            (goal_id, agent_role),
        ).fetchone()
    return row["status"] if row else None


def _count_challenge_notes(project: str, goal_started_at: str | None) -> int:
    threshold = goal_started_at or "1970-01-01T00:00:00"
    total = 0
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notes "
            "WHERE category = 'challenge' AND project = ? AND created_at >= ?",
            (project, threshold),
        ).fetchone()
        total += int(row["c"] if row else 0)
    with abzu_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM note_candidates "
            "WHERE category = 'challenge' AND project = ? AND created_at >= ?",
            (project, threshold),
        ).fetchone()
        total += int(row["c"] if row else 0)
    return total


def _has_hitl_approval(project: str, stage: str) -> bool:
    with em_db(project) as conn:
        _ensure_hitl_approvals_table(conn)
        row = conn.execute(
            "SELECT 1 FROM hitl_approvals WHERE project = ? AND stage = ? LIMIT 1",
            (project, stage),
        ).fetchone()
    return row is not None


def _ensure_hitl_approvals_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hitl_approvals (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            stage TEXT NOT NULL,
            note TEXT,
            approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _next_human_approval_id(conn, project: str) -> str:
    prefix = derive_project_prefix(project)
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, INSTR(id, '-') + 1) AS INTEGER)), 0) + 1 "
        "AS next_num FROM hitl_approvals WHERE project = ?",
        (project,),
    ).fetchone()
    return f"{prefix}-{int(row['next_num']):03d}"


def _insert_implied_spec_approval(conn, project: str) -> None:
    """Create synthetic spec approval when igi approval is recorded."""
    existing_spec = conn.execute(
        "SELECT 1 FROM hitl_approvals WHERE project = ? AND stage = 'spec' LIMIT 1",
        (project,),
    ).fetchone()
    if existing_spec:
        return
    implied_id = _next_human_approval_id(conn, project)
    conn.execute(
        "INSERT INTO hitl_approvals (id, project, stage, note) VALUES (?, ?, 'spec', ?)",
        (implied_id, project, "implied by igi approval"),
    )


def _all_wave_tasks_completed(project: str) -> bool:
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status IN ('completed', 'skipped') THEN 1 ELSE 0 END) AS done "
            "FROM task_state WHERE project_id = ? "
            "AND (work_type IS NULL OR work_type NOT IN ('goal', 'phase'))",
            (project,),
        ).fetchone()
    total = row["total"] or 0
    done = row["done"] or 0
    return total > 0 and done == total


def _has_validator_signoff(project: str, goal_id: str) -> bool:
    if _has_agent_status(goal_id, "validator", "completed"):
        return True
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT 1 FROM pm_decisions WHERE project_id = ? "
            "AND decision_type IN ('validator_signoff', 'validation_signoff') "
            "AND COALESCE(human_response, 'approved') = 'approved' LIMIT 1",
            (project,),
        ).fetchone()
    return row is not None


def _goal_artifacts_dir(project: str) -> Path:
    path = ENKI_ROOT / "artifacts" / normalize_project_name(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_authored_prompt(role: str) -> str:
    prompt_path = ENKI_ROOT / "prompts" / f"{role}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing authored prompt: {prompt_path}")
    return prompt_path.read_text()


def _apply_blind_wall(role: str, context: dict) -> dict:
    filtered = dict(context)
    exclude: list[str] = []
    try:
        exclude = get_blind_wall_filter(AgentRole(role)).get("exclude", [])
    except Exception:
        if role == "dev":
            exclude = ["qa_output", "test_results", "test_code"]
        elif role == "qa":
            exclude = ["dev_output", "implementation_details", "source_code"]
    for key in exclude:
        filtered.pop(key, None)
    return filtered


def _mail_em(project: str, role: str, task_id: str, status: str, findings: list[str]) -> None:
    threads = query_threads(project, thread_type="agent_output", limit=1)
    if threads:
        thread_id = threads[0]["thread_id"]
    else:
        thread_id = create_thread(project, "agent_output")
    body = (
        f"role={role}\n"
        f"task_id={task_id}\n"
        f"status={status}\n"
        f"findings={'; '.join(findings[:5])}"
    )
    send(
        project=project,
        thread_id=thread_id,
        from_agent=role.upper(),
        to_agent="EM",
        subject=f"{role}:{task_id} {status}",
        body=body,
        importance="normal",
        task_id=task_id,
    )


def _summarize_findings(task: dict, context: dict) -> list[str]:
    findings = []
    if task.get("task_name"):
        findings.append(f"Task: {task['task_name']}")
    files = context.get("assigned_files") or task.get("assigned_files") or []
    if files:
        findings.append(f"Files: {', '.join(files[:3])}")
    deps = context.get("dependencies") or task.get("dependencies") or []
    if deps:
        findings.append(f"Dependencies met: {len(deps)}")
    findings.append("Prompt/context merged from authored prompt + EM task context.")
    findings.append("Blind-wall filters applied before execution.")
    return findings


def _next_wave_number(project: str) -> int:
    path = _goal_artifacts_dir(project)
    existing = list(path.glob("wave-*.md"))
    if not existing:
        return 1
    nums = []
    for p in existing:
        try:
            nums.append(int(p.stem.split("-", 1)[1]))
        except Exception:
            continue
    return (max(nums) + 1) if nums else 1


def _wave_status(project: str) -> str:
    wave_files = list(_goal_artifacts_dir(project).glob("wave-*.md"))
    if not wave_files:
        return "NOT STARTED"
    wave_numbers: list[int] = []
    for p in wave_files:
        try:
            wave_numbers.append(int(p.stem.split("-", 1)[1]))
        except Exception:
            continue
    active_wave = max(wave_numbers) if wave_numbers else 1
    try:
        goal_id = read_project_state(project, "goal_id")
        if goal_id:
            with uru_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_status "
                    "WHERE goal_id = ? AND status = 'in_progress' "
                    "AND (agent_role = 'dev' OR agent_role = 'qa' "
                    "OR agent_role LIKE 'dev:%' OR agent_role LIKE 'qa:%')",
                    (goal_id,),
                ).fetchone()
            if row and int(row["c"] or 0) > 0:
                return f"Wave {active_wave} in progress"
    except Exception:
        pass
    return f"Wave {active_wave} in progress"


def _format_md(payload: dict) -> str:
    return (
        f"# Enki Artifact\n\n"
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n"
    )


def _find_session_transcript() -> str | None:
    project_path = str(Path.cwd().resolve())
    encoded = project_path.replace("/", "-")
    base = Path.home() / ".claude" / "projects" / encoded
    if not base.exists():
        return None
    candidates = []
    for path in base.glob("*.jsonl"):
        if "subagents" in path.parts:
            continue
        candidates.append(path)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def _extract_wrap_messages(transcript_path: Path) -> list[str]:
    messages: list[str] = []
    if not transcript_path.exists():
        return messages
    for raw in transcript_path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_type = entry.get("type")
        if msg_type not in {"user", "assistant"}:
            continue
        role = "USER" if msg_type == "user" else "ASSISTANT"
        message = entry.get("message")
        if isinstance(message, dict):
            raw_content = message.get("content")
        else:
            raw_content = message
        text = _extract_wrap_text(raw_content)
        if not text:
            continue
        text = _truncate_long_code_blocks(text)
        if text.strip():
            messages.append(f"{role}: {text.strip()}")
    return messages


def _extract_wrap_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content.get("text", "")
        return ""
    if isinstance(content, list):
        chunks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "\n".join(chunks)
    return ""


def _truncate_long_code_blocks(text: str, max_lines: int = 20) -> str:
    pattern = re.compile(r"```(?:[^\n]*)\n(.*?)```", re.DOTALL)

    def repl(match: re.Match) -> str:
        body = match.group(1)
        lines = body.splitlines()
        if len(lines) <= max_lines:
            return match.group(0)
        head = "\n".join(lines[:3])
        return f"```\n{head}\n... [code truncated]\n```"

    return pattern.sub(repl, text)


def _chunk_wrap_messages(messages: list[str], max_chars: int = 15000) -> list[str]:
    if not messages:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for msg in messages:
        add = len(msg) + 1
        if current and size + add > max_chars:
            chunks.append("\n".join(current))
            current = [msg]
            size = len(msg)
        else:
            current.append(msg)
            size += add
    if current:
        chunks.append("\n".join(current))
    return chunks


def _choose_ollama_model() -> str | None:
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return None
    first = lines[1].split()
    return first[0] if first else None


def _wrap_extraction_prompt(chunk: str) -> str:
    return (
        "You are a knowledge extraction system. Read this development\n"
        "session transcript and extract items in these categories:\n\n"
        "DECISIONS: Technical decisions made and their reasoning\n"
        "PATTERNS: Code patterns, conventions, or approaches adopted\n"
        "BUGS: Bugs found and how they were fixed\n"
        "ARCHITECTURE: Structural or design choices\n"
        "LEARNINGS: Things that went wrong, surprises, insights\n\n"
        "For each item output EXACTLY this format, one per item:\n"
        "CATEGORY: <category>\n"
        "CONTENT: <1-3 sentence description>\n"
        "KEYWORDS: <comma-separated keywords>\n"
        "---\n\n"
        "Extract 5-15 items. Only extract substantive items, not trivial\n"
        "actions like \"opened a file\" or \"ran tests\".\n\n"
        "TRANSCRIPT:\n"
        f"{chunk}"
    )


def _run_ollama_extract(model: str, prompt: str) -> str:
    proc = subprocess.run(
        ["ollama", "run", model, prompt],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _parse_ollama_items(text: str) -> list[dict]:
    items: list[dict] = []
    if not text.strip():
        return items
    blocks = [b.strip() for b in text.split("---") if b.strip()]
    for block in blocks:
        category = ""
        content = ""
        keywords = ""
        for line in block.splitlines():
            if line.upper().startswith("CATEGORY:"):
                category = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("CONTENT:"):
                content = line.split(":", 1)[1].strip()
            elif line.upper().startswith("KEYWORDS:"):
                keywords = line.split(":", 1)[1].strip()
        if not content:
            continue
        mapped = _map_wrap_category(category)
        items.append({
            "category": mapped,
            "content": content,
            "keywords": keywords,
            "summary": content[:180],
        })
    return items


def _map_wrap_category(category: str) -> str:
    c = category.lower().strip()
    if c.startswith("decision"):
        return "decision"
    if c.startswith("pattern"):
        return "pattern"
    if c.startswith("bug"):
        return "fix"
    if c.startswith("architecture"):
        return "pattern"
    if c.startswith("learning"):
        return "learning"
    return "learning"


def _stage_wrap_candidates(items: list[dict], project: str, session_id: str) -> tuple[int, int]:
    staged = 0
    duplicates = 0
    now = datetime.now(timezone.utc).isoformat()
    with abzu_db() as conn:
        for item in items:
            content = (item.get("content") or "").strip()
            if not content:
                continue
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            exists = conn.execute(
                "SELECT 1 FROM note_candidates WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if exists:
                duplicates += 1
                continue
            params = (
                str(uuid.uuid4()),
                content,
                item.get("summary"),
                item.get("category", "learning"),
                project,
                content_hash,
                session_id,
                item.get("keywords"),
                now,
            )
            try:
                conn.execute(
                    "INSERT INTO note_candidates "
                    "(id, content, summary, category, project, status, content_hash, source, session_id, keywords, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'raw', ?, 'transcript-extraction', ?, ?, ?)",
                    params,
                )
            except Exception:
                conn.execute(
                    "INSERT INTO note_candidates "
                    "(id, content, summary, category, project, status, content_hash, source, session_id, keywords, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'raw', ?, 'session_end', ?, ?, ?)",
                    params,
                )
            staged += 1
    return staged, duplicates


def _apply_wrap_gemini_decisions(project: str, decisions: list[dict]) -> tuple[int, int]:
    promoted = 0
    discarded = 0
    for decision in decisions:
        cid = resolve_candidate_id((decision.get("candidate_id") or "").strip())
        if not cid:
            continue
        action = (decision.get("action") or "").strip().lower()
        candidate = _get_candidate_by_id(cid)
        if not candidate:
            continue
        if action == "promote":
            note = create_note(
                content=candidate["content"],
                category=candidate["category"],
                project=project,
                summary=candidate.get("summary"),
                tags=candidate.get("tags"),
                context=candidate.get("context_description"),
            )
            if note:
                update_note(note["id"], promoted_at=datetime.now(timezone.utc).isoformat())
                _mark_candidate_status(cid, "enriched")
                promoted += 1
        elif action == "discard":
            _mark_candidate_status(cid, "discarded")
            discarded += 1
        elif action == "consolidate":
            merged = _consolidate_candidate_into_note(candidate, decision)
            _mark_candidate_status(cid, "discarded")
            if merged:
                promoted += 1
            else:
                discarded += 1
    return promoted, discarded


def _get_candidate_by_id(candidate_id: str) -> dict | None:
    with abzu_db() as conn:
        row = conn.execute(
            "SELECT * FROM note_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    return dict(row) if row else None


def _mark_candidate_status(candidate_id: str, status: str) -> None:
    with abzu_db() as conn:
        try:
            conn.execute(
                "UPDATE note_candidates SET status = ? WHERE id = ?",
                (status, candidate_id),
            )
        except Exception:
            # Older DBs may not support status='discarded'; keep candidate non-raw.
            conn.execute(
                "UPDATE note_candidates SET status = 'enriched' WHERE id = ?",
                (candidate_id,),
            )


def _consolidate_candidate_into_note(candidate: dict, decision: dict) -> bool:
    target = (
        decision.get("merge_with")
        or decision.get("target")
        or decision.get("candidate_id_to_consolidate_with")
        or decision.get("bead_id")
        or ""
    )
    note_id = _resolve_note_id_prefix(target) if target else None
    if note_id:
        from enki.memory.notes import get as get_note
        note = get_note(note_id)
        if note:
            old = note.get("content", "")
            new_content = old if candidate["content"] in old else f"{old}\n\n{candidate['content']}".strip()
            update_note(note_id, content=new_content, evolved_at=datetime.now(timezone.utc).isoformat())
            return True
    # No merge target found; promote as fallback.
    note = create_note(
        content=candidate["content"],
        category=candidate["category"],
        project=candidate.get("project"),
        summary=candidate.get("summary"),
    )
    if note:
        update_note(note["id"], promoted_at=datetime.now(timezone.utc).isoformat())
        return True
    return False


def _resolve_note_id_prefix(value: str) -> str | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    with wisdom_db() as conn:
        rows = conn.execute("SELECT id FROM notes WHERE id LIKE ?", (f"{v}%",)).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    return None
