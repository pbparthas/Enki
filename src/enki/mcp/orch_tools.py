"""orch_tools.py — MCP tool definitions for EM orchestration.

These are the tools CC calls to interact with the orchestration system.
Goal, phase, triage, quick, decompose, orchestrate, mail, status, etc.
"""

import json
import hashlib
import re
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from enki.db import ENKI_ROOT, abzu_db, em_db, uru_db, wisdom_db
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
    file_bug,
    close_bug,
    list_bugs,
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

PHASE_ORDER = ["planning", "spec", "approved", "implement", "validating", "complete"]
PHASE_ALIASES = {
    "spec-review": "spec",
    "approve": "approved",
    "review": "validating",
}


# ── Goal & Triage ──


def enki_goal(
    description: str,
    project: str = ".",
    spec_path: str | None = None,
) -> dict:
    """Set active goal and initialize mechanical orchestration state."""
    tier = detect_tier(description)
    active = _get_active_goal(project)
    if active and active["tier"] and active["tier"] != tier:
        return {
            "error": (
                f"Tier is locked to {active['tier']} for this session. "
                f"Cannot change to {tier}."
            )
        }

    phase = "spec-review" if spec_path else "planning"
    goal_id = str(uuid.uuid4())
    metadata = {"tier_locked": True, "spec_path": spec_path}

    with em_db(project) as conn:
        conn.execute(
            "UPDATE task_state SET status = 'completed', completed_at = datetime('now') "
            "WHERE project_id = ? AND work_type = 'goal' AND status != 'completed'",
            (project,),
        )
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, work_type, status, started_at, agent_outputs) "
            "VALUES (?, ?, 'default', ?, ?, 'goal', 'active', datetime('now'), ?)",
            (goal_id, project, description, tier, json.dumps(metadata)),
        )
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, work_type, status, started_at) "
            "VALUES (?, ?, 'default', ?, ?, 'phase', 'active', datetime('now'))",
            (str(uuid.uuid4()), project, phase, tier),
        )

    result = {"goal_id": goal_id, "tier": tier, "phase": phase}
    if spec_path:
        result["spec_path"] = spec_path
    return result


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


def enki_phase(action: str, to: str | None = None, project: str = ".") -> dict:
    """Advance phase with DB-backed precondition checks, or return status."""
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
        return {
            "phase": current,
            "tier": tier or "not set",
            "goal": goal or "not set",
            "next_phase": next_phase,
            "pipeline": " → ".join(PHASE_ORDER),
        }

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

        missing = _phase_missing_preconditions(project, goal_id, current, target)
        if missing:
            return {"error": f"Cannot advance to {target}. Required: {missing}"}

        with em_db(project) as conn:
            conn.execute(
                "INSERT INTO task_state "
                "(task_id, project_id, sprint_id, task_name, tier, work_type, status, started_at) "
                "VALUES (?, ?, 'default', ?, ?, 'phase', 'active', datetime('now'))",
                (str(uuid.uuid4()), project, target, active.get("tier") or "standard"),
            )
        return {"phase": target, "required_next": _phase_required_next(target)}

    return {"error": f"Unknown action: {action}. Use 'advance' or 'status'."}


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
    sprint_id = "sprint-1"
    create_sprint(project, sprint_id)

    created = []
    for task_def in tasks:
        task_id = create_task(
            project=project,
            sprint_id=sprint_id,
            task_name=task_def["name"],
            tier="standard",
        )
        created.append({
            "task_id": task_id,
            "name": task_def["name"],
        })

    return {
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
    project: str = ".",
) -> dict:
    """Spawn a single role with authored prompt, persist full output, return summary."""
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    role_key = role.strip().lower()
    status = "failed"
    findings: list[str] = []
    try:
        prompt = _load_authored_prompt(role_key)
        task = get_task(project, task_id) or {}
        merged_context = {"task": task, **(context or {})}
        filtered_context = _apply_blind_wall(role_key, merged_context)

        # Mechanical agent execution: prompt + filtered context snapshot.
        execution = {
            "role": role_key,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_path": str(ENKI_ROOT / "prompts" / f"{role_key}.md"),
            "prompt": prompt,
            "context": filtered_context,
            "result": {
                "status": "completed",
                "notes": "Execution delegated mechanically by enki_spawn.",
            },
        }
        artifact = _goal_artifacts_dir(goal_id) / f"{role_key}-{task_id}.md"
        artifact.write_text(_format_md(execution))

        findings = _summarize_findings(task, filtered_context)
        status = "completed"
        _upsert_agent_status(goal_id, role_key, status)
        _upsert_agent_status(goal_id, f"{role_key}:{task_id}", status)
        _mail_em(project, role_key, task_id, status, findings)
        return {
            "role": role_key,
            "status": status,
            "key_findings": findings[:10],
            "artifact": str(artifact),
        }
    except Exception as e:
        _upsert_agent_status(goal_id, role_key, "failed")
        _upsert_agent_status(goal_id, f"{role_key}:{task_id}", "failed")
        _mail_em(project, role_key, task_id, "failed", [str(e)])
        return {
            "role": role_key,
            "status": "failed",
            "key_findings": [str(e)],
        }


def enki_wave(goal_id: str, project: str = ".") -> dict:
    """Execute the next ready wave; always spawns both Dev and QA per task."""
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    if active["goal_id"] != goal_id:
        return {"error": "No goal set or goal_id mismatch."}
    if not is_spec_approved(project):
        return {"error": "Specs not approved."}

    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint found."}
    sprint_id = sprint["sprint_id"]
    tasks = get_next_wave(project, sprint_id)
    if not tasks:
        return {"error": "No tasks ready for next wave."}

    wave_no = _next_wave_number(goal_id)
    rows = []
    failures = []

    with ThreadPoolExecutor(max_workers=max(2, len(tasks) * 2)) as pool:
        futures = []
        for task in tasks:
            ctx = {
                "task_name": task.get("task_name"),
                "assigned_files": task.get("assigned_files", []),
                "dependencies": task.get("dependencies", []),
            }
            futures.append(pool.submit(enki_spawn, "dev", task["task_id"], ctx, project))
            futures.append(pool.submit(enki_spawn, "qa", task["task_id"], ctx, project))

        for fut in as_completed(futures):
            result = fut.result()
            rows.append(result)
            if result.get("status") != "completed":
                failures.append(result)

    report_path = _goal_artifacts_dir(goal_id) / f"wave-{wave_no}.md"
    report_path.write_text(_format_md({
        "goal_id": goal_id,
        "wave_number": wave_no,
        "tasks": [t["task_id"] for t in tasks],
        "results": rows,
        "failures": failures,
    }))

    return {
        "wave": wave_no,
        "task_count": len(tasks),
        "agents": [
            {"role": r.get("role"), "status": r.get("status")}
            for r in rows
        ],
        "warnings": [f"{f.get('role')}:{f.get('status')}" for f in failures],
        "artifact": str(report_path),
    }


def enki_complete(task_id: str, project: str = ".") -> dict:
    """Mark completion only if validator/QA/wave checks are satisfied."""
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
    project: str = ".",
) -> dict:
    """File or manage bugs."""
    priority_map = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
    priority = priority_map.get(severity, "P2")

    if action == "file":
        bug_id = file_bug(
            project=project,
            title=title or "Untitled bug",
            description=description or "",
            filed_by="Human",
            priority=priority,
        )
        return {"bug_id": bug_id, "action": "filed"}
    elif action == "close":
        if not bug_id:
            return {"error": "bug_id required for close"}
        close_bug(project, bug_id)
        return {"bug_id": bug_id, "action": "closed"}
    elif action == "list":
        bugs = list_bugs(project)
        return {"bugs": bugs, "count": len(bugs)}
    else:
        return {"error": f"Unknown action: {action}"}


# ── Status ──


def enki_status_update(project: str = ".") -> dict:
    """Generate status update."""
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


def _next_step_hint(tier: str) -> str:
    """Hint for what to do after setting goal."""
    if tier == "minimal":
        return "Phase auto-advanced to implement. You can code now, then enki_phase(action='advance', to='review'/'complete')"
    if tier == "standard":
        return "Run enki_phase(action='advance', to='intake'), then PM intake checklist"
    return "Run enki_phase(action='advance', to='intake'), then full PM intake + debate"


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
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT task_id, task_name, tier, agent_outputs FROM task_state "
            "WHERE project_id = ? AND work_type = 'goal' AND status != 'completed' "
            "ORDER BY started_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        phase_row = conn.execute(
            "SELECT task_name FROM task_state "
            "WHERE project_id = ? AND work_type = 'phase' "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (project,),
        ).fetchone()
    if not row:
        return None
    return {
        "goal_id": row["task_id"],
        "goal": row["task_name"],
        "tier": row["tier"],
        "phase": phase_row["task_name"] if phase_row else None,
    }


def _require_active_goal(project: str) -> dict:
    active = _get_active_goal(project)
    if not active:
        return {"error": "No active goal. Use enki_goal first."}
    return active


def _phase_missing_preconditions(project: str, goal_id: str, current: str, target: str) -> str | None:
    _ = current
    if target == "spec":
        if not _has_agent_status(goal_id, "pm", "completed"):
            return "PM agent completed in agent_status table"
    elif target == "approved":
        if not _has_hitl_approval(project):
            return "HITL approval record"
    elif target == "implement":
        if not _has_agent_status(goal_id, "architect", "completed"):
            return "Architect agent completed"
        if not _has_hitl_approval(project):
            return "HITL approval record"
    elif target == "validating":
        if not _all_wave_tasks_completed(project):
            return "all waves completed"
    elif target == "complete":
        if not _has_validator_signoff(project, goal_id):
            return "Validator sign-off exists"
    return None


def _phase_required_next(phase: str) -> str:
    hints = {
        "spec": "Run spec authoring and collect HITL approval.",
        "approved": "Architect review complete; then advance to implement.",
        "implement": "Execute waves with enki_wave until all tasks complete.",
        "validating": "Spawn validator and record sign-off.",
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


def _has_hitl_approval(project: str) -> bool:
    with em_db(project) as conn:
        spec = conn.execute(
            "SELECT 1 FROM pm_decisions "
            "WHERE project_id = ? AND decision_type = 'spec_approval' "
            "AND human_response = 'approved' LIMIT 1",
            (project,),
        ).fetchone()
        gate = conn.execute(
            "SELECT 1 FROM test_approvals WHERE project = ? AND hitl_approved = 1 LIMIT 1",
            (project,),
        ).fetchone()
    return bool(spec or gate)


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


def _goal_artifacts_dir(goal_id: str) -> Path:
    path = ENKI_ROOT / "artifacts" / goal_id
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


def _next_wave_number(goal_id: str) -> int:
    path = _goal_artifacts_dir(goal_id)
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
