"""Session-start context assembly helpers."""
from __future__ import annotations
from pathlib import Path
from enki.db import ENKI_ROOT


def _split_h2_sections(content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []
    for line in content.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_header, "\n".join(current_lines)))
    return sections


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rstrip()
    last_break = max(clipped.rfind("\n"), clipped.rfind(". "))
    if last_break > 0:
        clipped = clipped[:last_break].rstrip()
    return f"{clipped}\n\n[truncated for session context budget]"


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


def get_playbook_section(phase: str) -> str:
    """Extract only the relevant phase section from PLAYBOOK.md.

    Always includes: SESSION START, COMMON MISTAKES, TOOL QUICK REFERENCE.
    Phase-specific: only the current phase section.
    Saves ~1,600 tokens vs injecting full PLAYBOOK.
    """
    playbook_path = ENKI_ROOT / "PLAYBOOK.md"
    if not playbook_path.exists():
        # Fallback to PIPELINE.md
        pipeline_path = ENKI_ROOT / "PIPELINE.md"
        if pipeline_path.exists():
            return pipeline_path.read_text().strip()
        return ""

    content = playbook_path.read_text()
    phase_key = (phase or "none").strip().lower()

    # Sections always included regardless of phase
    always_include = {
        "## SESSION START",
        "## COMMON MISTAKES",
        "## TOOL QUICK REFERENCE",
        "## HOW TO START ANY SESSION",
    }

    sections = _split_h2_sections(content)

    # Target phase header
    phase_header = f"## PHASE: {phase_key}"

    # Prefer latest copy when duplicate sections exist.
    selected: dict[str, str] = {}
    for header, body in sections:
        header_upper = header.upper()
        is_always = any(h.upper() in header_upper for h in always_include)
        is_phase = header.upper() == phase_header.upper()
        if is_always or is_phase:
            selected[header_upper] = body.strip()

    ordered_headers = [
        "## HOW TO START ANY SESSION",
        "## SESSION START — MANDATORY FIRST ACTION",
        phase_header.upper(),
        "## TOOL QUICK REFERENCE",
        "## COMMON MISTAKES AND FIXES",
    ]
    result: list[str] = []
    for wanted in ordered_headers:
        match = next((v for k, v in selected.items() if wanted in k), "")
        if match:
            result.append(match)

    combined = "\n\n".join(result)
    if phase_key in {"implement", "validating"}:
        return _truncate_text(combined, 2200)
    return combined


def get_skill_essentials() -> str:
    """Extract only tool reference tables and enum values from SKILL.md.

    Strips the Pipeline Sequence section (duplicated in PLAYBOOK.md).
    Saves ~500 tokens vs injecting full SKILL.md.
    """
    skill_path = ENKI_ROOT / "SKILL.md"
    if not skill_path.exists():
        return ""
    content = skill_path.read_text()
    sections = _split_h2_sections(content)

    essentials: list[str] = []
    for header, body in sections:
        header_upper = header.upper()
        if "## COMPLETE MCP TOOL REFERENCE" in header_upper:
            lines = body.split("\n")
            keep: list[str] = []
            in_orch = False
            for line in lines:
                if line.startswith("### "):
                    in_orch = line.strip().upper().startswith("### ORCHESTRATION TOOLS")
                if in_orch:
                    keep.append(line)
            if keep:
                essentials.append("\n".join(keep).strip())
        elif "## VALID ENUM VALUES" in header_upper:
            essentials.append(body.strip())

    if not essentials:
        cutoff_markers = [
            "## Pipeline Sequence",
            "## Agent Execution Mechanics",
        ]
        cutoff = len(content)
        for marker in cutoff_markers:
            pos = content.find(marker)
            if pos > 0 and pos < cutoff:
                cutoff = pos
        return _truncate_text(content[:cutoff].strip(), 700)

    return _truncate_text("\n\n".join(essentials), 700)


def get_persona(phase: str) -> str:
    """Return full or compact persona based on phase.

    Full persona for planning/spec/approved — personality matters.
    Compact persona for implement/validating/complete — CC is mostly calling tools.
    Saves ~420 tokens per implement-phase session.
    """
    phase_key = (phase or "none").strip().lower()
    high_interaction_phases = {"planning", "spec", "approved"}

    # Try compact persona first for execution phases
    if phase_key not in high_interaction_phases:
        compact_path = ENKI_ROOT / "persona" / "PERSONA_COMPACT.md"
        if compact_path.exists():
            compact = compact_path.read_text().strip()
            if compact:
                return compact

    # Full persona for planning phases or if compact doesn't exist
    full_path = ENKI_ROOT / "persona" / "PERSONA.md"
    if full_path.exists():
        return full_path.read_text().strip()
    return ""


def get_abzu_memory_cached(project: str, goal: str, tier: str) -> str:
    """Run Abzu memory injection with 2-hour cache.

    During long sprints the memory results are stable.
    Cache avoids re-running FTS5 search on every session restart.
    """
    import hashlib
    import time

    cache_dir = ENKI_ROOT / "cache"
    cache_dir.mkdir(exist_ok=True)
    # Use project + goal hash as cache key
    cache_key = hashlib.md5(f"{project}:{goal}".encode()).hexdigest()[:8]
    cache_path = cache_dir / f"abzu-{project}-{cache_key}.txt"

    # Use cache if less than 2 hours old
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 7200:
            try:
                return cache_path.read_text().strip()
            except Exception:
                pass

    # Re-run and cache
    try:
        from enki.memory.abzu import inject_session_start
        result = (inject_session_start(project, goal, tier) or "").strip()
        if result:
            try:
                cache_path.write_text(result)
            except Exception:
                pass
        return result
    except Exception:
        return ""


def build_session_start_context(project: str, goal: str, tier: str, phase: str) -> str:
    """Assemble phase-aware session-start context.

    Token budget by phase:
    - Banner:          ~100 tokens  (always)
    - Sprint status:   ~80 tokens   (implement/validating only)
    - PLAYBOOK section: ~400 tokens (phase-relevant section only)
    - Persona:         ~80 tokens   (compact for implement/validating)
                      ~500 tokens  (full for planning/spec/approved)
    - Uru state:       ~50 tokens   (always)
    - Abzu memory:     ~200 tokens  (cached, always)
    Total implement:   ~910 tokens  (vs ~3,500 before)
    Total planning:    ~1,330 tokens (vs ~3,500 before)
    """
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

    # 3) Phase-relevant PLAYBOOK section (not full file)
    playbook_section = get_playbook_section(phase)
    if playbook_section:
        parts.append(playbook_section)

    # 4) Phase-appropriate persona
    persona = get_persona(phase)
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

    # 6) Abzu memory (cached)
    memory_ctx = get_abzu_memory_cached(project, goal, tier)
    if memory_ctx:
        parts.append(memory_ctx)

    return "\n\n".join(parts).strip()
