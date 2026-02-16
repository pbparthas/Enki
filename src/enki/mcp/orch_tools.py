"""orch_tools.py — MCP tool definitions for EM orchestration.

These are the tools CC calls to interact with the orchestration system.
Goal, phase, triage, quick, decompose, orchestrate, mail, status, etc.
"""

from enki.orch.orchestrator import Orchestrator
from enki.orch.tiers import (
    detect_tier,
    quick,
    set_goal,
    set_phase,
    get_project_state,
    triage,
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
    get_inbox,
    get_thread_messages,
    mark_read,
    count_unread,
)
from enki.orch.task_graph import (
    create_task,
    get_task,
    update_task_status,
    get_next_wave,
    get_sprint_tasks,
    is_sprint_complete,
    create_sprint,
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


# ── Goal & Triage ──


def enki_goal(description: str, project: str = ".") -> dict:
    """Set active goal. Satisfies Uru Gate 1.

    Auto-detects tier from description.
    Surfaces relevant past decisions as nudges.
    """
    tier = detect_tier(description)
    result = set_goal(project, description, tier)

    # Nudge: surface relevant past decisions (read-only, fail-safe)
    nudge_text = ""
    try:
        from enki.memory.abzu import recall_for_nudge, format_nudge
        related = recall_for_nudge(description)
        if related:
            nudge_text = format_nudge(related)
    except Exception:
        pass  # Nudge failure must never block goal setting

    response = {
        "goal": description,
        "tier": tier,
        "phase": "intake",
        "next_step": _next_step_hint(tier),
    }
    if nudge_text:
        response["nudge"] = nudge_text

    return response


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


def enki_phase(phase: str, project: str = ".") -> dict:
    """Set current phase. Satisfies Uru Gate 3 for implement+.

    Valid phases: intake, debate, plan, implement, review, ship.
    """
    result = set_phase(project, phase)
    if "error" in result:
        return result
    return {
        "phase": phase,
        "next_step": _phase_hint(phase),
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
    if phase not in ("implement", "review", "ship"):
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
        return "Use enki_quick for fast-path, or enki_phase('implement')"
    if tier == "standard":
        return "Run enki_phase('intake'), then PM intake checklist"
    return "Run enki_phase('intake'), then full PM intake + debate"


def _phase_hint(phase: str) -> str:
    """Hint for current phase."""
    hints = {
        "intake": "Answer PM intake checklist: outcome, audience, constraints, scope, risks",
        "debate": "Run multi-perspective analysis on approach",
        "plan": "Create specs (product + implementation)",
        "implement": "Gate 3 satisfied — code changes allowed",
        "review": "Sprint-level review of all changes",
        "ship": "Qualify, deploy, verify",
    }
    return hints.get(phase, "")
