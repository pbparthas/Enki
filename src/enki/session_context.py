"""Session-start context assembly helpers."""
from __future__ import annotations
from pathlib import Path
from enki.db import ENKI_ROOT


def _get_em_db_path(project: str) -> Path | None:
    """Return em.db path for a project if it exists."""
    try:
        from enki.db import em_db
        from enki.orch.task_graph import get_active_sprint
        sprint = get_active_sprint(project)
        return sprint is not None
    except Exception:
        return None


def _project_has_active_sprint(project: str) -> bool:
    """Check if project has an active sprint in em.db."""
    try:
        from enki.orch.task_graph import get_active_sprint
        return get_active_sprint(project) is not None
    except Exception:
        return False


def generate_sprint_status_block(project: str) -> str:
    """Query em.db for live sprint/task state."""
    try:
        from enki.orch.task_graph import get_active_sprint, get_sprint_tasks
        sprint = get_active_sprint(project)
        if not sprint:
            return ""
        tasks = get_sprint_tasks(project, sprint["sprint_id"])
        if not tasks:
            return ""
        counts: dict[str, int] = {}
        in_progress: list[str] = []
        for t in tasks:
            s = t.get("status", "pending")
            counts[s] = counts.get(s, 0) + 1
            if s == "in_progress":
                in_progress.append(
                    f"  - {t['task_id']} (session: {t.get('session_id') or 'unknown'})"
                )
        total = len(tasks)
        done = counts.get("completed", 0) + counts.get("skipped", 0)
        pct = round(done / total * 100, 1) if total else 0.0
        lines = [
            f"## Sprint Status — {sprint['sprint_id']}",
            f"Progress: {done}/{total} ({pct}%)",
            f"  pending:{counts.get('pending',0)} "
            f"in_progress:{counts.get('in_progress',0)} "
            f"completed:{done} "
            f"failed:{counts.get('failed',0) + counts.get('hitl',0)}",
        ]
        if in_progress:
            lines.append("Currently claimed by active sessions:")
            lines.extend(in_progress)
            lines.append("-> Call enki_wave() to claim available tasks.")
        else:
            lines.append("-> No tasks in progress. Call enki_wave() to begin.")
        return "\n".join(lines)
    except Exception:
        return ""


def generate_orientation_block(project: str, phase: str, goal: str, tier: str) -> str:
    """Generate a short dynamic orientation block for session start."""
    phase_key = (phase or "none").strip().lower()
    action_map = {
        "none": "Call enki_goal to initialise this project.",
        "planning": "Call enki_goal to initialise this project.",
        "spec": "Igi challenge is pending or complete. Check findings, present to operator, then call enki_approve(stage='igi').",
        "approved": "Write Architect implementation spec, present to operator, call enki_approve(stage='architect').",
        "implement": "ORCHESTRATOR MODE ONLY. Your first and only action is enki_wave(project='{project}'). Do NOT read source files, explore code, or implement directly. Spawn agents and report results.",
        "validating": "Validation in progress. Present results to operator, call enki_approve(stage='test').",
        "complete": "Sprint complete. Run session end pipeline.",
    }
    next_action = action_map.get(
        phase_key, "Check enki_phase status and continue pipeline."
    ).format(project=project)
    return (
        f"## 𒀭 Enki Session — {project}\n"
        f"- Goal: {goal}\n"
        f"- Phase: {phase} | Tier: {tier}\n"
        f"- Next action: {next_action}"
    )


def generate_new_project_block() -> str:
    """Banner for when no active project is found in current directory."""
    return "\n".join([
        "━" * 50,
        "𒀭 Enki — No active project detected.",
        "━" * 50,
        "Options:",
        "  New project (no spec):    enki_goal(description='...', project='name')",
        "  New project (have spec):  enki_goal(spec_path='/path/to/spec.md', project='name')",
        "  Resume existing project:  enki_goal(project='name')  ← if already registered",
        "  Register existing path:   enki_register(path='.')",
        "━" * 50,
    ])


def build_session_start_context(project: str, goal: str, tier: str, phase: str) -> str:
    """Assemble session-start context in strict operational order."""
    parts: list[str] = []

    # 1) Orientation banner
    if project and goal:
        parts.append(generate_orientation_block(project, phase, goal, tier))
    else:
        parts.append(generate_new_project_block())

    # 2) Live sprint status (implement/validating phases only)
    if project and (phase or "").strip().lower() in ("implement", "validating"):
        sprint_status = generate_sprint_status_block(project)
        if sprint_status:
            parts.append(sprint_status)

    # 3) PLAYBOOK (operational reference)
    playbook_path = ENKI_ROOT / "PLAYBOOK.md"
    pipeline_path = ENKI_ROOT / "PIPELINE.md"
    ref_path = playbook_path if playbook_path.exists() else pipeline_path
    if ref_path.exists():
        text = ref_path.read_text().strip()
        if text:
            parts.append(text)

    # 4) Persona
    persona_path = ENKI_ROOT / "persona" / "PERSONA.md"
    if persona_path.exists():
        persona = persona_path.read_text().strip()
        if persona:
            parts.append(persona)

    # 5) Uru enforcement state
    try:
        from enki.gates.uru import inject_enforcement_context
        uru_ctx = (inject_enforcement_context() or "").strip()
        if uru_ctx:
            parts.append(uru_ctx)
    except Exception:
        pass

    # 6) Abzu memory
    try:
        from enki.memory.abzu import inject_session_start
        memory_ctx = (inject_session_start(project, goal, tier) or "").strip()
        if memory_ctx:
            parts.append(memory_ctx)
    except Exception:
        pass

    return "\n\n".join(parts).strip()
