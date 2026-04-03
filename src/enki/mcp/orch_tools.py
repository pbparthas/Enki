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
    _project_slug,
    compute_checkpoint_interval,
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

PHASE_ORDER = ["planning", "spec", "approved", "implement", "validating", "closing", "complete"]
PHASE_ALIASES = {
    "spec-review": "spec",
    "approve": "approved",
    "review": "validating",
    "none": "planning",
}
VALID_AGENT_ROLES = {
    "pm", "architect", "dba", "dev", "qa", "ui_ux", "validator",
    "reviewer", "infosec", "devops", "performance", "researcher", "em",
    "technical-writer",
    "igi", "cto", "devils_advocate", "tech_feasibility", "historical_context",
    "security-architect",
    "typescript-dev-reviewer", "typescript-qa-reviewer",
    "typescript-reviewer", "typescript-infosec",
    "python-dev-reviewer", "python-qa-reviewer",
    "python-reviewer", "python-infosec",
    "security-auditor", "ai-engineer",
}
APPROVAL_STAGES = {"igi", "spec", "architect", "test", "spec-revision"}
APPROVAL_TARGET_PHASE = {
    "igi": "approved",
    "spec": "approved",
    "spec-revision": "approved",
    "architect": "implement",
    "test": "validating",
}

PROMPT_ROLE_ALIASES = {
    "typescript-dev-reviewer": "dev",
    "typescript-qa-reviewer": "qa",
    "typescript-reviewer": "reviewer",
    "typescript-infosec": "infosec",
    "python-dev-reviewer": "dev",
    "python-qa-reviewer": "qa",
    "python-reviewer": "reviewer",
    "python-infosec": "infosec",
    "security-auditor": "infosec",
    "security-architect": "infosec",
    "ai-engineer": "reviewer",
    "technical-writer": "pm",
}

BRIEF_FIELD_MAP = {
    "dev": "build_instructions",
    "typescript-dev-reviewer": "build_instructions",
    "python-dev-reviewer": "build_instructions",
    "qa": "qa_test_strategy",
    "typescript-qa-reviewer": "qa_test_strategy",
    "python-qa-reviewer": "qa_test_strategy",
    "reviewer": "review_checklist",
    "typescript-reviewer": "review_checklist",
    "python-reviewer": "review_checklist",
    "infosec": "security_requirements",
    "typescript-infosec": "security_requirements",
    "python-infosec": "security_requirements",
    "security-auditor": "security_requirements",
    "validator": "validation_criteria",
    "performance": "performance_notes",
    "devops": "devops_notes",
}

GRAPH_AWARE_ROLES = {
    "dev", "reviewer", "architect", "qa", "infosec",
    "security-auditor", "performance",
}

PLAYBOOK_CONTENT = """# Enki PLAYBOOK — Exact Operational Sequences

This is your step-by-step guide for every phase. Follow it exactly.
When in doubt: `enki_phase(action='status')` to orient, then return here.

---

## HOW TO START ANY SESSION

```
1. enki_recall(query="project context recent decisions")
2. enki_phase(action='status') → read current phase
3. Go to the section for that phase below
```

If no project is active:
```
→ New project: enki_goal(description="...", project="name")
→ Existing project not registered: enki_register(path=".")
```

---

## PHASE: planning

### What this phase is
Requirements gathering. No code, no spec yet. You are understanding what to build.

### Exact sequence

**Greenfield (new codebase):**
```
1. enki_goal(description="...", project="name")
2. enki_recall(query="similar projects past decisions")
3. Q&A with human — validate: outcome, audience, constraints, success criteria, scope, risks
4. enki_spawn('pm', 'spec-draft') → READ prompt_path verbatim → READ context_artifact
   → Task tool FOREGROUND → wait for completion
5. enki_report(role='pm', task_id='spec-draft', summary=..., status='completed')
   → PM writes docs/spec-draft.md
→ Go to PHASE: spec
```

**Brownfield (existing codebase):**
```
1. enki_goal(description="...", project="name")
2. enki_recall(query="codebase patterns decisions")
3. enki_spawn('researcher', 'codebase-profile') → Task tool FOREGROUND → wait
4. enki_report(role='researcher', task_id='codebase-profile', summary=..., status='completed')
5. Present codebase profile to human — confirm tech stack
6. Q&A with human → same intake checklist
7. enki_spawn('pm', 'spec-draft') → Task tool FOREGROUND → wait
8. enki_report(role='pm', task_id='spec-draft', summary=..., status='completed')
→ Go to PHASE: spec
```

**External spec (spec already exists):**
```
1. enki_goal(spec_path="/path/to/spec.md", project="name")
2. enki_spawn('pm', 'spec-review') → Task tool FOREGROUND → wait
   (PM reviews and endorses existing spec — does NOT rewrite)
3. enki_report(role='pm', task_id='spec-review', summary=..., status='completed')
→ Go to PHASE: spec (skip debate if spec is already final)
```

### NEVER in this phase
- Do not call enki_wave
- Do not call enki_decompose
- Do not write any code

---

## PHASE: spec

### What this phase is
Spec debate, adversarial review, approval. The spec gets stress-tested before
any implementation planning begins.

### Exact sequence

**Step 1 — Run debate (always for Standard/Full tier):**
```
enki_debate()
→ Returns Round 1 spawn instructions

For each agent in Round 1 (sequential, foreground):
  enki_spawn(role, 'debate-r1-{role}') → Task tool FOREGROUND → wait
  enki_report(role=role, task_id='debate-r1-{role}', summary=..., status='completed')
  enki_debate_update(role=role, round='1', output={...from agent JSON output...})

enki_debate() → Returns Round 2 spawn instructions

For each agent in Round 2 (sequential, foreground):
  enki_spawn(role, 'debate-r2-{role}') → Task tool FOREGROUND → wait
  enki_report(role=role, task_id='debate-r2-{role}', summary=..., status='completed')
  enki_debate_update(role=role, round='2', output={...from agent JSON output...})

enki_debate() → Returns reconciliation spawn instructions

enki_spawn('pm', 'debate-reconcile') → Task tool FOREGROUND → wait
enki_report(role='pm', task_id='debate-reconcile', summary=..., status='completed')
enki_debate_update(role='pm', round='reconciliation', output={...from PM JSON output...})

enki_debate() → Returns complete with spec-final.md and debate-summary.md paths
```

**Step 2 — HITL review:**
```
Present docs/debate-summary.md to human
Present docs/spec-final.md to human
Wait for verbal approval
enki_approve(stage='spec')
```

**Step 3 — Igi adversarial review:**
```
enki_spawn('igi', 'igi-review') → Task tool FOREGROUND → wait
enki_report(role='igi', task_id='igi-review', summary=..., status='completed')
Present Igi findings to human
Wait for verbal approval
enki_approve(stage='igi')
→ Phase advances to 'approved' automatically
```

### NEVER in this phase
- Do not skip debate for Standard or Full tier
- Do not call enki_approve(stage='spec') before debate is complete
- Do not call enki_approve(stage='igi') before presenting Igi findings to human

---

## PHASE: approved

### What this phase is
Pre-implementation kickoff and Architect impl spec. Feasibility confirmed,
task DAG created.

### Exact sequence

**Step 1 — Kickoff:**
```
enki_kickoff()
→ Returns PM + Architect spawn instructions

enki_spawn('pm', 'kickoff-pm') → Task tool FOREGROUND → wait
enki_report(role='pm', task_id='kickoff-pm', summary=..., status='completed')
enki_kickoff_update(role='pm', output={...from PM JSON output...})

enki_spawn('architect', 'kickoff-architect') → Task tool FOREGROUND → wait
enki_report(role='architect', task_id='kickoff-architect', summary=..., status='completed')
enki_kickoff_update(role='architect', output={...from Architect JSON output...})

[If PM output signals dba_needed=true:]
  enki_spawn('dba', 'kickoff-dba') → Task tool FOREGROUND → wait
  enki_report(role='dba', task_id='kickoff-dba', summary=..., status='completed')
  enki_kickoff_update(role='dba', output={...})

[If PM output signals ui_needed=true:]
  enki_spawn('ui_ux', 'kickoff-ui_ux') → Task tool FOREGROUND → wait
  enki_report(role='ui_ux', task_id='kickoff-ui_ux', summary=..., status='completed')
  enki_kickoff_update(role='ui_ux', output={...})

enki_kickoff_complete()
```

If blockers found:
```
→ Present blockers to human
→ Wait for resolution
→ enki_approve(stage='spec-revision', note='resolution details')
→ enki_kickoff() again → repeat from Step 1
```

If no blockers:
```
→ Present kickoff summary to human (verbal ok)
→ Proceed to Step 2
```

**Step 2 — Architect impl spec:**
```
enki_spawn('architect', 'impl-spec') → Task tool FOREGROUND → wait
enki_report(role='architect', task_id='impl-spec', summary=..., status='completed')
```

Architect output MUST contain a JSON block with tasks array:
```json
{
  "tasks": [
    {
      "name": "Task name",
      "description": "Exact description of what to implement",
      "files": ["path/to/file.ts"],
      "dependencies": ["Other task name"],
      "acceptance_criteria": ["criterion 1", "criterion 2"]
    }
  ]
}
```

**Step 3 — HITL approval:**
```
Present impl spec to human
Wait for verbal approval
enki_approve(stage='architect')
→ Phase advances to 'implement' automatically
```

**Step 4 — Decompose:**
```
enki_decompose(tasks=[
  {
    "name": "...",
    "description": "...",    ← REQUIRED — copy from Architect JSON output exactly
    "files": [...],
    "dependencies": [...]
  },
  ...
])
→ Creates sprint and task records in em.db
```

### NEVER in this phase
- Do not call enki_wave before enki_decompose
- Do not call enki_decompose without description for each task
- Do not skip kickoff — always run it before Architect impl spec

---

## PHASE: implement

### What this phase is
Wave execution. You are the orchestrator. You NEVER implement code yourself.
You spawn agents and report results.

### Exact sequence per wave

```
enki_wave()
→ Returns list of tasks and agents for this wave
→ ALWAYS note the sprint_branch in the response
```

**For EACH task returned (one at a time, never parallel):**

```
Step 1: Dev
  enki_spawn(role='dev', task_id='{task_id}')
  → Read prompt_path verbatim (never substitute your own prompt)
  → Read context_artifact completely (read in chunks if large)
  → Task tool FOREGROUND — wait for completion
  enki_report(role='dev', task_id='{task_id}', summary='...', status='completed')

Step 2: QA
  enki_spawn(role='qa', task_id='{task_id}')
  → Read prompt_path verbatim
  → Read context_artifact completely
  → Task tool FOREGROUND — wait for completion
  enki_report(role='qa', task_id='{task_id}', summary='...', status='completed')

Step 3: Complete task ← MANDATORY, NEVER SKIP
  enki_complete(task_id='{task_id}')
  → This marks the task done, queues merge, releases session claim
  → Without this, the wave will return the same task again forever
```

**After ALL tasks in the wave are complete:**
```
enki_mail_inbox()  → read agent messages
enki_wave()        → get next wave OR sprint_complete signal
```

**When enki_wave returns sprint_complete=True:**
```
enki_phase(action='status')  → confirm all tasks done
enki_phase(action='advance', to='validating')
→ Go to PHASE: validating
```

**When enki_wave returns no tasks but sprint not complete:**
```
→ Some tasks are in_progress by other sessions or blocked
→ enki_phase(action='status') to see which
→ Wait or escalate if blocked
```

### NEVER in this phase
- NEVER use Agent tool — ALWAYS use Task tool (Task tool sets required permissions)
- NEVER run Dev and QA in parallel — sequential only
- NEVER call enki_wave again before enki_complete for each task in the current wave
- NEVER implement code yourself — spawn agents for all implementation work
- NEVER call enki_report without having run the Task tool first

### Conditional agent spawning (after Dev+QA complete)
```
If task files include .tsx/.jsx/.vue/.css → also spawn ui_ux
If task involves auth/token/session/encrypt → also spawn infosec
If task modifies hot path identified in codebase profile → also spawn performance

For each conditional agent:
  enki_spawn(role='{role}', task_id='{task_id}')
  → Task tool FOREGROUND → wait
  enki_report(role='{role}', task_id='{task_id}', summary=..., status='completed')
  (then proceed to enki_complete as normal)
```

---

## PHASE: validating

### What this phase is
Sprint-level review and final validation before completion.

### Exact sequence
```
1. enki_spawn('validator', '{sprint_id}-validation') → Task tool FOREGROUND → wait
2. enki_report(role='validator', task_id='{sprint_id}-validation', summary=..., status='completed')
3. Present validator findings to human
4. If issues found: spawn Dev to fix, re-run QA, re-run validator
5. enki_spawn('reviewer', '{sprint_id}-sprint-review') → Task tool FOREGROUND → wait
   (reviewer runs in SPRINT mode — reviews all files modified across sprint)
6. enki_report(role='reviewer', task_id='{sprint_id}-sprint-review', summary=..., status='completed')
7. Present review to human
8. Wait for verbal approval
9. enki_approve(stage='test')
→ Phase advances to 'complete'
```

---

## PHASE: complete

### What this phase is
Session wrap-up and memory persistence.

### Exact sequence
```
1. enki_wrap()  → runs transcript → memory pipeline
2. Present final summary to human
```

---

## COMMON MISTAKES AND FIXES

| Mistake | Why it happens | Fix |
|---------|---------------|-----|
| Agent tool instead of Task tool | Forgetting the rule | ALWAYS use Task tool for agent spawning. Agent tool bypasses permission grants. |
| enki_wave returns same tasks | enki_complete not called | After dev+qa report for each task, call enki_complete(task_id) before calling enki_wave again |
| Gate blocks with "architect not completed" | enki_report not called after architect | Always call enki_report after every agent Task completion |
| enki_debate returns error about spec-draft | PM wrote spec to wrong path | PM must write to docs/spec-draft.md exactly |
| Dev explores codebase instead of building | description missing from task | Check context_artifact — description should be there. If empty, impl spec had no description. |
| Wave returns no tasks but sprint not done | Tasks in_progress from dead session | enki_wave auto-recovers on next call via tmux liveness check |
| enki_complete fails validator gate | Validator was never spawned | Validator gate only fires if validator was actually spawned. Check task context. |

---

## QUICK ORIENTATION COMMANDS

```bash
# What phase am I in and what's next?
enki_phase(action='status')

# What's the sprint progress?
enki_sprint_summary(sprint_id='...')

# What tasks are ready now?
enki_next_actions()

# What's in my inbox?
enki_mail_inbox()

# Generate sprint DAG diagram
enki_diagram(type='dag')

# Generate pipeline status diagram  
enki_diagram(type='pipeline')
```

---

## TOOL QUICK REFERENCE

| Tool | When | Never |
|------|------|-------|
| enki_wave | Start of each wave, after all enki_complete calls | Before enki_decompose, before enki_complete for current wave |
| enki_spawn | When pipeline requires an agent | Use Agent tool — always use Task tool to run the agent |
| enki_report | After EVERY agent Task completion | Call without running Task tool first |
| enki_complete | After dev+qa both reported for a task | Skip it — wave will loop forever without it |
| enki_decompose | Once, after architect approved, before first wave | Multiple times for same sprint |
| enki_approve | After every human verbal approval | Auto-advance without human seeing the output |
| enki_escalate | When blocked and human input needed | Improvise around blockers |
| enki_diagram | On demand for visualization | — |

---

## SESSION START — MANDATORY FIRST ACTION

**Every session, before any other action, print this banner:**

```
𒀭 Enki — {project} | Phase: {phase} | Tier: {tier}
Goal: {goal}
{sprint_status if implement/validating}
→ {next_action}
```

Values are in the injected context above. Print them verbatim.
Then proceed with next_action immediately without waiting for human input.
Do not ask "what would you like to work on?" — you already know.
"""

# Valid task phases in order
TASK_PHASES = ["test_design", "implementing", "verifying", "complete"]


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
    force: bool = False,
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

    # Guard: if actively in sprint, preserve state unless explicitly forced.
    if existing_phase in {"implement", "validating"} and not force:
        active_sprint = get_active_sprint(project)
        if active_sprint:
            return {
                "warning": (
                    f"Project '{project}' is in '{existing_phase}' phase with active sprint "
                    f"'{active_sprint['sprint_id']}'. enki_goal will not overwrite goal or tier. "
                    "Call enki_phase(action='status') to see current state. "
                    "Pass force=True only if you intend to restart from planning."
                ),
                "phase": existing_phase,
                "sprint_id": active_sprint["sprint_id"],
                "goal": read_project_state(project, "goal"),
                "tier": read_project_state(project, "tier"),
            }

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


def _ensure_pipeline_md(enki_root: Path | None = None) -> None:
    root = enki_root or ENKI_ROOT
    playbook_path = root / "PLAYBOOK.md"
    if not playbook_path.exists():
        playbook_path.write_text(PLAYBOOK_CONTENT)
    pipeline_path = root / "PIPELINE.md"
    implement_section = (
        "### implement\n"
        "- Call enki_wave(project) to get next wave tasks and agents\n"
        "- For EACH task in the wave:\n"
        "  1. Run Dev agent via Task tool foreground — wait for completion\n"
        "  2. Call enki_report(role='dev', task_id=..., status='completed')\n"
        "  3. Run QA agent via Task tool foreground — wait for completion\n"
        "  4. Call enki_report(role='qa', task_id=..., status='completed')\n"
        "  5. Call enki_complete(task_id=...) — MANDATORY, marks task done\n"
        "- After ALL tasks in wave have enki_complete called:\n"
        "  - Call enki_mail_inbox() to read agent messages\n"
        "  - Call enki_wave() for next wave\n"
        "- When enki_wave returns sprint_complete=True: call enki_phase(action='status')\n"
        "- NEVER background agents — foreground only for permission inheritance\n"
        "- NEVER call enki_wave() before enki_complete() for each task\n"
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
    skip_council: bool = False,
    skip_council_reason: str | None = None,
) -> dict:
    """Create HITL approval record and advance project phase."""
    project = _resolve_project(project)
    stage_key = (stage or "").strip().lower()
    if stage_key not in APPROVAL_STAGES:
        return {
            "error": f"Unknown stage: {stage}. Expected one of {sorted(APPROVAL_STAGES)}"
        }
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    # Impl council gate for architect approval
    if stage_key == "architect":
        tier = (read_project_state(project, "tier") or active.get("tier") or "standard").strip().lower()
        if tier in ("standard", "full") and not skip_council:
            goal_id = active["goal_id"]
            artifacts_dir = _goal_artifacts_dir(project)
            council_artifact_path = artifacts_dir / f"impl-council-{goal_id}.json"
            council_complete = False
            if council_artifact_path.exists():
                try:
                    state = json.loads(council_artifact_path.read_text())
                    council_complete = state.get("status") == "complete"
                except Exception:
                    council_complete = False
            if not council_complete:
                return {
                    "error": (
                        "Implementation Council required before architect approval "
                        f"for {tier} tier. "
                        "Call enki_impl_council() to run the specialist panel. "
                        "To skip: enki_approve(stage='architect', skip_council=True, "
                        "skip_council_reason='reason')"
                    ),
                    "tier": tier,
                    "council_status": "incomplete",
                }

        if skip_council and tier in ("standard", "full"):
            if not skip_council_reason:
                return {
                    "error": "skip_council_reason required when skipping council for standard/full tier."
                }
            try:
                with em_db(project) as conn:
                    conn.execute(
                        "INSERT INTO pm_decisions "
                        "(id, project_id, decision_type, proposed_action, context, human_response) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"council-skip-{active['goal_id']}",
                            project,
                            "impl_council_skipped",
                            "architect_approval_skip_council",
                            skip_council_reason,
                            "approved",
                        ),
                    )
            except Exception:
                pass

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
            "Call enki_decompose(tasks=[...]) with Architect's task breakdown. "
            "Tasks format: ["
            "{'name': str, 'description': str, 'files': [str], 'dependencies': [str]}]. "
            "description is MANDATORY — copy verbatim from Architect JSON output. "
            "After enki_decompose: call enki_wave()."
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


def enki_impl_council(
    project: str | None = None,
    approved_specialists: list[str] | None = None,
) -> dict:
    """Implementation Council — specialist peer review of Architect impl spec."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    artifacts_dir = _goal_artifacts_dir(project)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    council_artifact_path = artifacts_dir / f"impl-council-{goal_id}.json"

    state: dict = {}
    if council_artifact_path.exists():
        try:
            state = json.loads(council_artifact_path.read_text())
        except Exception:
            state = {}

    council_status = state.get("status", "")

    if council_status == "complete":
        return {
            "message": "Implementation Council already complete.",
            "council_id": f"impl-council-{goal_id}",
            "specialists_ran": state.get("approved_specialists", []),
            "tasks_enriched": state.get("tasks_enriched", 0),
            "resumed": True,
        }

    if approved_specialists is None:
        impl_spec = _load_impl_spec(project, goal_id)
        if not impl_spec:
            return {
                "error": (
                    "No impl spec found. Architect must write impl spec before calling "
                    "enki_impl_council. Call enki_spawn('architect', 'impl-spec') first."
                )
            }

        proposal = _propose_specialist_panel(project, impl_spec)
        state = {
            "status": "proposed",
            "goal_id": goal_id,
            "proposal": proposal,
            "impl_spec_summary": {
                "task_count": len(impl_spec.get("tasks", [])),
                "tech_stack": impl_spec.get("tech_stack", {}),
            },
        }
        council_artifact_path.write_text(json.dumps(state, indent=2))
        return {
            "message": "Implementation Council — specialist panel proposed. Review and approve.",
            "council_id": f"impl-council-{goal_id}",
            "proposed_specialists": proposal["proposed"],
            "not_proposed": proposal["not_proposed"],
            "next": (
                "Review proposed panel, then call "
                "enki_impl_council(approved_specialists=[...]). "
                "You may add or remove specialists from the proposal."
            ),
        }

    valid_roles = {
        "typescript-dev-reviewer", "typescript-qa-reviewer",
        "typescript-reviewer", "typescript-infosec",
        "python-dev-reviewer", "python-qa-reviewer",
        "python-reviewer", "python-infosec",
        "infosec", "reviewer", "security-auditor",
        "ai-engineer", "performance",
    }
    invalid = [s for s in approved_specialists if s not in valid_roles]
    if invalid:
        return {
            "error": f"Unknown specialist roles: {invalid}. Valid roles: {sorted(valid_roles)}"
        }

    if council_status in ("", "proposed"):
        state["status"] = "running"
        state["approved_specialists"] = approved_specialists
        state["completed_specialists"] = state.get("completed_specialists", [])
        council_artifact_path.write_text(json.dumps(state, indent=2))

    completed = state.get("completed_specialists", [])
    approved = state.get("approved_specialists", approved_specialists)
    pending = [s for s in approved if s not in completed]
    spawn_instructions = []

    if pending:
        impl_spec = _load_impl_spec(project, goal_id)
        if not impl_spec:
            return {"error": "Impl spec not found. Cannot proceed with council."}

        for specialist in pending:
            context = {
                "mode": "impl-spec-review",
                "council_id": f"impl-council-{goal_id}",
                "impl_spec": impl_spec,
                "instruction": (
                    f"You are participating in the Implementation Council as {specialist}. "
                    "Review the implementation spec from your specialist perspective. "
                    "Output spec-level concerns only — no code, no implementation details. "
                    "For each concern, specify which task it affects, what the problem is, "
                    "and what each relevant agent needs to know: "
                    "build_instructions for Dev, qa_test_strategy for QA, "
                    "review_checklist for Reviewer, security_requirements for InfoSec, "
                    "validation_criteria for Validator. "
                    "Output structured JSON per _base.md schema with concerns array."
                ),
            }
            spawn_result = enki_spawn(specialist, f"impl-council-{specialist}", context, project)
            spawn_instructions.append({
                "specialist": specialist,
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
                "instruction": spawn_result.get("instruction"),
            })

        council_artifact_path.write_text(json.dumps(state, indent=2))
        return {
            "message": f"Implementation Council — {len(pending)} specialist(s) pending.",
            "council_id": f"impl-council-{goal_id}",
            "spawn_instructions": spawn_instructions,
            "completed": completed,
            "pending": pending,
            "next": (
                "Run each specialist via Task tool (foreground, sequential). "
                "After each: call enki_impl_council_update(specialist=..., output=...). "
                "After all specialists complete: call enki_impl_council() again "
                "to trigger Architect reconciliation."
            ),
        }

    if council_status != "reconciling" and not state.get("reconciliation_done"):
        all_concerns = state.get("specialist_outputs", {})
        impl_spec = _load_impl_spec(project, goal_id)
        context = {
            "mode": "impl-council-reconcile",
            "council_id": f"impl-council-{goal_id}",
            "impl_spec": impl_spec,
            "specialist_concerns": all_concerns,
            "instruction": (
                "You are reconciling the Implementation Council findings. "
                "Read all specialist concerns and the original impl spec. "
                "For each concern: decide accept/reject/modify with rationale. "
                "Update task descriptions to incorporate accepted changes. "
                "For EVERY task, produce agent_briefs with fields for each relevant role: "
                "dev (build_instructions), qa (qa_test_strategy), "
                "reviewer (review_checklist), infosec (security_requirements), "
                "validator (validation_criteria), performance (performance_notes), "
                "devops (devops_notes). "
                "Output JSON per impl-council-reconcile schema."
            ),
        }
        spawn_result = enki_spawn("architect", "impl-council-reconcile", context, project)
        state["status"] = "reconciling"
        council_artifact_path.write_text(json.dumps(state, indent=2))
        return {
            "message": "All specialists complete. Architect reconciliation required.",
            "council_id": f"impl-council-{goal_id}",
            "spawn_instructions": [{
                "specialist": "architect",
                "role": "reconciler",
                "prompt_path": spawn_result.get("prompt_path"),
                "context_artifact": spawn_result.get("context_artifact"),
                "instruction": spawn_result.get("instruction"),
            }],
            "next": (
                "Run Architect via Task tool (foreground). "
                "After completion: call "
                "enki_impl_council_update(specialist='architect', output={...})."
            ),
        }

    return {
        "message": "Implementation Council complete. Call enki_approve(stage='architect').",
        "council_id": f"impl-council-{goal_id}",
        "specialists_ran": approved,
        "tasks_enriched": state.get("tasks_enriched", 0),
    }


def enki_impl_council_update(
    specialist: str,
    output: dict,
    project: str | None = None,
) -> dict:
    """Record Implementation Council specialist output after Task completion."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    artifacts_dir = _goal_artifacts_dir(project)
    council_artifact_path = artifacts_dir / f"impl-council-{goal_id}.json"

    if not council_artifact_path.exists():
        return {"error": "No active impl council found. Call enki_impl_council() first."}

    state = json.loads(council_artifact_path.read_text())

    if specialist == "architect":
        enriched_tasks = output.get("tasks", [])
        council_decisions = output.get("council_decisions", [])
        state["enriched_tasks"] = enriched_tasks
        state["council_decisions"] = council_decisions
        state["tasks_enriched"] = len(enriched_tasks)
        state["reconciliation_done"] = True
        state["status"] = "complete"

        try:
            with em_db(project) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO pm_decisions "
                    "(id, project_id, decision_type, proposed_action, context, human_response) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"impl-council-{goal_id}",
                        project,
                        "impl_council_reconciliation",
                        f"tasks_enriched:{len(enriched_tasks)}",
                        json.dumps({
                            "enriched_tasks": enriched_tasks,
                            "council_decisions": council_decisions,
                        })[:10000],
                        "approved",
                    ),
                )
        except Exception:
            pass

        council_artifact_path.write_text(json.dumps(state, indent=2))
        return {
            "message": f"Implementation Council complete. {len(enriched_tasks)} tasks enriched.",
            "tasks_enriched": len(enriched_tasks),
            "decisions_recorded": len(council_decisions),
            "next": "Call enki_approve(stage='architect') then enki_decompose with enriched tasks.",
        }

    concerns = output.get("concerns", [])
    if "specialist_outputs" not in state:
        state["specialist_outputs"] = {}
    state["specialist_outputs"][specialist] = concerns

    completed = state.get("completed_specialists", [])
    if specialist not in completed:
        completed.append(specialist)
    state["completed_specialists"] = completed

    council_artifact_path.write_text(json.dumps(state, indent=2))
    approved = state.get("approved_specialists", [])
    remaining = [s for s in approved if s not in completed]
    return {
        "message": f"Specialist '{specialist}' output recorded. {len(concerns)} concerns.",
        "concerns_recorded": len(concerns),
        "completed": completed,
        "remaining": remaining,
        "next": (
            f"Run next specialist: {remaining[0]}"
            if remaining
            else "All specialists complete. Call enki_impl_council() to trigger reconciliation."
        ),
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
    tier = read_project_state(project, "tier") or "standard"
    if tier in ("standard", "full"):
        base_agents.append("security-architect")
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

        # Anonymize Round 2 positions — evaluate on merit, not identity.
        role_order = sorted(round_1_outputs.keys())
        anon_map = {role_name: chr(65 + idx) for idx, role_name in enumerate(role_order)}
        anonymized_positions = {
            anon_map[role_name]: output
            for role_name, output in round_1_outputs.items()
        }

        for role in round_2_pending:
            context = {
                "debate_round": 2,
                "debate_id": debate_id,
                "spec_draft_path": str(spec_draft_path),
                "round_1_positions": anonymized_positions,
                "anonymization_note": (
                    "Round 1 positions are labeled Response A, B, C (not by role name). "
                    "Evaluate each position on its merits. "
                    "Reference positions as 'Response A', 'Response B', etc. in your rebuttal."
                ),
                "instruction": (
                    "You are participating in Round 2 of a spec debate. "
                    f"Read the spec at {spec_draft_path}. "
                    "Read all Round 1 positions (anonymized as A/B/C). "
                    "Produce your rebuttal - agree, challenge, or build on specific points others raised. "
                    "Be direct. Reference positions as 'Response A', 'Response B', not by role. "
                    "Output structured JSON with: {'rebuttal': str, 'agreements': [{'position': str, 'point': str}], "
                    "'disagreements': [{'position': str, 'point': str, 'counter_argument': str}], 'new_concerns': [str]}"
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
        tasks: List of {name, description, files, dependencies} dicts
               description is MANDATORY — it is what Dev implements from
        project: Project ID
    """
    sprint_id = create_sprint(project, "sprint-1")
    with em_db(project) as conn:
        conn.execute(
            "UPDATE sprint_state SET status = 'active' WHERE sprint_id = ?",
            (sprint_id,),
        )

    # Detect and store sprint base branch
    _project_path = _get_project_path(project)
    _sprint_base = "main"
    if _project_path:
        _cur = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=_project_path, timeout=30,
        )
        _cur_branch = _cur.stdout.strip()
        if _cur_branch and _cur_branch not in {"main", "master"}:
            _sprint_base = _cur_branch
        elif _cur_branch in {"main", "master"}:
            _r = subprocess.run(
                ["git", "checkout", "-b", sprint_id],
                capture_output=True, text=True, cwd=_project_path, timeout=30,
            )
            if _r.returncode == 0:
                _sprint_base = sprint_id
    try:
        write_project_state(project, f"sprint_base_{sprint_id}", _sprint_base)
    except Exception:
        with em_db(project) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO project_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (f"sprint_base_{sprint_id}", _sprint_base),
            )

    # Pass 1: create all tasks, build name→id map
    name_to_id = {}
    created = []
    for task_def in tasks:
        # Quality gate: reject thin task definitions
        name = (task_def.get("name") or "").strip()
        description = (task_def.get("description") or "").strip()
        files = task_def.get("files") or []
        agent_briefs = task_def.get("agent_briefs")

        if not name:
            return {
                "error": "Task missing required 'name' field.",
                "task": task_def,
            }
        if len(description) < 30:
            return {
                "error": (
                    f"Task '{name}' has insufficient description ({len(description)} chars). "
                    "Minimum 30 characters required. "
                    "Description must tell Dev exactly what to implement."
                ),
                "task": task_def,
            }
        if not files:
            return {
                "error": (
                    f"Task '{name}' has no assigned_files. "
                    "Every task must specify which files it creates or modifies."
                ),
                "task": task_def,
            }

        task_id = create_task(
            project=project,
            sprint_id=sprint_id,
            task_name=name,
            tier="standard",
            assigned_files=files,
            dependencies=[],
            description=description,
        )
        if agent_briefs:
            try:
                with em_db(project) as conn:
                    conn.execute(
                        "UPDATE task_state SET agent_briefs = ? WHERE task_id = ?",
                        (json.dumps(agent_briefs), task_id),
                    )
            except Exception:
                pass
        name_to_id[name] = task_id
        created.append({
            "task_id": task_id,
            "name": name,
            "description": description,
            "files": files,
            "dependencies": task_def.get("dependencies", []),
            "agent_briefs": agent_briefs,
        })

    # Pass 2: resolve dependency names to IDs
    with em_db(project) as conn:
        for item in created:
            resolved_deps = [
                name_to_id.get(d, d) for d in item["dependencies"]
            ]
            conn.execute(
                "UPDATE task_state SET dependencies = ? WHERE task_id = ?",
                (json.dumps(resolved_deps), item["task_id"]),
            )
            item["resolved_dependencies"] = resolved_deps

    total_tasks = len(tasks)
    interval = compute_checkpoint_interval(total_tasks)
    try:
        write_project_state(
            project,
            "reviewer_checkpoint_interval",
            str(interval) if interval else "0",
        )
    except Exception:
        with em_db(project) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO project_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("reviewer_checkpoint_interval", str(interval) if interval else "0"),
            )
    try:
        write_project_state(project, "sprint_total_tasks", str(total_tasks))
    except Exception:
        with em_db(project) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO project_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("sprint_total_tasks", str(total_tasks)),
            )

    # Pass 3: auto-insert dependencies for file overlaps
    from enki.orch.task_graph import insert_dependency_for_overlap
    overlap_deps = insert_dependency_for_overlap(project, sprint_id)

    return {
        "message": (
            f"Sprint created with {len(created)} tasks. "
            + (f"{len(overlap_deps)} file-overlap dependencies auto-inserted. "
               if overlap_deps else "")
            + "Call enki_wave() to begin. "
            "Do not read source files or implement directly — that is agent work."
        ),
        "sprint_id": sprint_id,
        "sprint_branch": _sprint_base,
        "tasks": created,
        "total_tasks": len(created),
        "overlap_dependencies_added": len(overlap_deps) if overlap_deps else 0,
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
        prompt_path = _resolve_prompt_path(role_key)
        prompt = _load_authored_prompt(role_key)
        prompt_display = f"~/.enki/prompts/{prompt_path.name}"
        task = get_task(project, task_id) or {}

        # Deep Thought — transparent model routing.
        try:
            from enki.orch.deep_thought import select_model
            complexity_score, model_recommended = select_model(
                role=role_key,
                task=task or {},
                graph_context=None,
            )
        except Exception:
            complexity_score, model_recommended = 0, "claude-sonnet-4-6"

        if task_id:
            try:
                with em_db(project) as conn:
                    conn.execute(
                        "UPDATE task_state SET model_used = ? WHERE task_id = ?",
                        (model_recommended, task_id),
                    )
            except Exception:
                pass

        merged_context = {"task": task, **(context or {})}
        merged_context = _inject_external_spec_mode(project, role_key, merged_context)
        merged_context = _inject_architect_context(project, role_key, merged_context)

        # Inject role-specific council briefs if available for this task.
        if task_id and role_key in BRIEF_FIELD_MAP:
            try:
                with em_db(project) as conn:
                    row = conn.execute(
                        "SELECT agent_briefs FROM task_state WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                if row and row["agent_briefs"]:
                    briefs = json.loads(row["agent_briefs"])
                    brief_field = BRIEF_FIELD_MAP[role_key]
                    brief_content = briefs.get(brief_field) or briefs.get(role_key)
                    if brief_content:
                        merged_context[brief_field] = brief_content
            except Exception:
                pass

        if role_key in GRAPH_AWARE_ROLES:
            assigned_files = merged_context.get("assigned_files") or []
            if not assigned_files:
                task_payload = merged_context.get("task") or {}
                if isinstance(task_payload, dict):
                    assigned_files = task_payload.get("assigned_files") or []
            if isinstance(assigned_files, str):
                try:
                    assigned_files = json.loads(assigned_files or "[]")
                except Exception:
                    assigned_files = []
            if isinstance(assigned_files, list) and assigned_files:
                try:
                    codebase_ctx = _build_codebase_context(project, assigned_files)
                    if codebase_ctx:
                        merged_context["codebase_context"] = codebase_ctx
                except Exception:
                    pass

        filtered_context = _apply_blind_wall(role_key, merged_context)

        # Strip conversation context from adversarial review agents.
        adversarial_roles = {"igi", "cto", "devils_advocate", "tech_feasibility", "historical_context"}
        if role_key in adversarial_roles:
            keep_keys = {
                "spec_content", "spec_path", "spec_draft_path",
                "debate_round", "debate_id", "instruction",
                "round_1_positions", "anonymization_note", "codebase_profile",
                "codebase_profile_path", "mode", "task_id",
            }
            filtered_context = {
                k: v for k, v in filtered_context.items()
                if k in keep_keys
            }
            if filtered_context.get("debate_round") == 1:
                filtered_context.pop("round_1_positions", None)
        # Inject mode and test_path based on task_phase for QA and Validator
        if role_key in ("qa", "validator") and task_id:
            task_phase = _get_task_phase(project, task_id)
            mode_from_context = (context or {}).get("mode")
            if not mode_from_context:
                if role_key == "qa":
                    if task_phase == "test_design":
                        filtered_context["mode"] = "write"
                        filtered_context["test_path"] = f"tests/tasks/{task_id}/"
                        filtered_context["instruction"] = (
                            "Write tests from acceptance criteria and API contracts only. "
                            "Do NOT run tests. Implementation does not exist yet. "
                            f"Write to tests/tasks/{task_id}/"
                        )
                    elif task_phase == "verifying":
                        filtered_context["mode"] = "execute"
                        filtered_context["test_path"] = f"tests/tasks/{task_id}/"
                        filtered_context["instruction"] = (
                            f"Run the pre-written test suite in tests/tasks/{task_id}/. "
                            "Do NOT modify tests. Do NOT read Dev source code. "
                            "Report exact failures with stack traces."
                        )
                elif role_key == "validator":
                    if task_phase == "test_design":
                        filtered_context["mode"] = "review-tests"
                        filtered_context["instruction"] = (
                            "Review QA's test suite for coverage completeness. "
                            "Check: are all acceptance criteria covered? "
                            "Flag gaps as concerns. Do NOT run tests."
                        )
                    elif task_phase == "verifying":
                        filtered_context["mode"] = "compliance"
                        filtered_context["instruction"] = (
                            "Check Dev output against impl spec. "
                            "Check QA execution results are complete. "
                            "Flag hallucinations (extra features) and omissions (missing spec items)."
                        )

        spawn_payload = {
            "role": role_key,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_path": prompt_display,
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

        _upsert_agent_status(goal_id, f"{role_key}:{task_id}", "in_progress")
        return {
            "message": (
                f"Agent {role_key} prepared. Model: {model_recommended} "
                f"(complexity score: {complexity_score}).\n"
                f"1. Read prompt file verbatim: {prompt_display}\n"
                f"2. Read context artifact from disk: {artifact}\n"
                "   (read in chunks if large — never skip, never summarize)\n"
                f"3. Run via Task tool in foreground (model={model_recommended}) — never Background tool.\n"
                f"4. After Task completes: call enki_report(role='{role_key}', task_id='{task_id}', summary=..., status='completed'|'failed')."
            ),
            "role": role_key,
            "status": "in_progress",
            "execution_mode": "foreground_sequential",
            "instruction": (
                "Run this agent in foreground via Task tool. "
                "Wait for completion before starting next agent."
            ),
            "prompt_path": prompt_display,
            "context_artifact": str(artifact),
            "task_id": task_id,
            "model_recommended": model_recommended,
            "complexity_score": complexity_score,
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
    mode: str | None = None,
    project: str | None = None,
    output: dict | None = None,
) -> dict:
    """Record agent completion. Advances task_phase and auto-files bugs."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    goal_id = active["goal_id"]
    role_key = role.strip().lower()
    if role_key not in VALID_AGENT_ROLES:
        return {"error": f"Unknown role: {role_key}"}
    status_key = status.strip().lower()

    # Validate agent was spawned (check in_progress status)
    current = _get_agent_status(goal_id, f"{role_key}:{task_id}")
    if current != "in_progress":
        return {
            "error": (
                f"Cannot report for {role_key}:{task_id}. "
                "Agent was not spawned or already reported."
            )
        }

    # Record completion in uru_db
    final_status = "completed" if status_key in ("completed", "done", "pass", "passed") else "failed"
    _upsert_agent_status(goal_id, f"{role_key}:{task_id}", final_status)

    # Store summary in task agent_outputs
    try:
        with em_db(project) as conn:
            existing = conn.execute(
                "SELECT agent_outputs FROM task_state WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            outputs = {}
            if existing and existing["agent_outputs"]:
                try:
                    outputs = json.loads(existing["agent_outputs"])
                except Exception:
                    outputs = {}
            outputs[role_key] = {"summary": summary, "status": final_status}
            conn.execute(
                "UPDATE task_state SET agent_outputs = ? WHERE task_id = ?",
                (json.dumps(outputs), task_id),
            )
    except Exception:
        pass

    # Auto-file bugs from output concerns array
    filed_bugs = []
    if output and isinstance(output.get("concerns"), list):
        filed_bugs = _auto_file_concerns(
            project, task_id, role_key, output["concerns"], goal_id
        )

    # Persist Architect impl spec if role=architect
    if role_key == "architect":
        try:
            artifact_path = (
                _goal_artifacts_dir(project) / f"spawn-architect-{task_id}.md"
            )
            if artifact_path.exists():
                content = artifact_path.read_text()
                import re as _re
                match = _re.search(r"```json\n(.*?)\n```", content, _re.DOTALL)
                if match:
                    import uuid as _uuid
                    with em_db(project) as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO pm_decisions "
                            "(id, project_id, decision_type, proposed_action, context) "
                            "VALUES (?, ?, 'architect_impl_spec', ?, ?)",
                            (
                                str(_uuid.uuid4()),
                                project,
                                f"Architect impl spec for task {task_id}",
                                match.group(1)[:10000],
                            ),
                        )
        except Exception:
            pass

    # Preserve artifact + mail trail
    artifact = _goal_artifacts_dir(project) / f"{role_key}-{task_id}.md"
    artifact.write_text(
        _format_md(
            {
                "role": role_key,
                "task_id": task_id,
                "status": final_status,
                "summary": summary,
                "reported_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    findings = [summary]
    if final_status == "failed":
        findings.append("failure reported")
    _mail_em(project, role_key, task_id, final_status, findings)

    # Task phase transitions
    current_phase = _get_task_phase(project, task_id)
    next_phase = None
    next_action = ""
    mode_key = (mode or "").strip().lower()

    if role_key == "qa" and (
        (current_phase == "test_design" and final_status == "completed")
        or (mode_key == "write" and final_status == "completed")
    ):
        # QA finished writing tests — Validator will review them next
        next_action = (
            f"enki_spawn('validator', '{task_id}', {{'mode': 'review-tests'}}) "
            "→ Task tool → enki_report"
        )

    elif role_key == "validator" and (
        current_phase == "test_design" or mode_key == "review-tests"
    ):
        if final_status == "completed" and not filed_bugs:
            # Validator approved test coverage.
            if current_phase == "test_design" and mode_key != "review-tests":
                next_phase = "implementing"
            next_action = (
                "Test review approved. "
                "Continue flow: QA execute then Validator compliance."
            )
        else:
            # Validator found gaps → back to QA
            next_action = (
                f"Test coverage gaps found ({len(filed_bugs)} bugs). "
                f"enki_spawn('qa', '{task_id}', {{'mode': 'write'}}) → Task tool → enki_report"
            )

    elif role_key == "dev" and current_phase == "implementing":
        if final_status == "completed":
            next_phase = "verifying"
            next_action = (
                "Dev complete. "
                f"enki_spawn('qa', '{task_id}', {{'mode': 'execute'}}) → Task tool → enki_report"
            )
        else:
            next_action = "Dev failed. Check bugs. Re-spawn dev after fixes."

    elif role_key == "qa" and current_phase == "verifying":
        next_action = (
            "QA execution complete. "
            f"enki_spawn('validator', '{task_id}', {{'mode': 'compliance'}}) "
            "→ Task tool → enki_report"
        )

    elif role_key == "validator" and (
        current_phase == "verifying" or mode_key == "compliance"
    ):
        if final_status == "completed" and not filed_bugs:
            # Task H path: explicit compliance mode closes task directly.
            # Legacy fallback (mode omitted): advance to reviewing.
            next_phase = "complete" if mode_key == "compliance" else "reviewing"
            if next_phase == "complete":
                next_action = (
                    "Compliance passed. "
                    f"Call enki_complete(task_id='{task_id}')"
                )
            else:
                next_action = (
                    "Compliance passed. "
                    f"enki_spawn('reviewer', '{task_id}') → Task tool → enki_report"
                )
        else:
            # Reset to implementing — Dev must fix
            next_phase = "implementing"
            next_action = (
                f"Compliance failed ({len(filed_bugs)} bugs). "
                f"enki_spawn('dev', '{task_id}') → Task tool → enki_report after fixes"
            )

    elif role_key == "reviewer" and current_phase == "reviewing":
        # Backward-compatible path for pre-Task-H reviewing phase states.
        if final_status == "completed" and not filed_bugs:
            next_phase = "complete"
            next_action = f"Call enki_complete(task_id='{task_id}')"
        else:
            next_phase = "implementing"
            next_action = (
                f"Reviewer found issues ({len(filed_bugs)} bugs). "
                f"enki_spawn('dev', '{task_id}') → Task tool → enki_report after fixes"
            )

    # Apply phase transition
    if next_phase:
        _advance_task_phase(project, task_id, next_phase)

    # Mark sprint_close complete when both infosec and sprint reviewer done
    sprint = get_active_sprint(project)
    if sprint and role_key in ("infosec", "reviewer"):
        sid = sprint["sprint_id"]
        infosec_ok = _has_agent_status(goal_id, f"infosec:{sid}-infosec-audit", "completed")
        reviewer_ok = _has_agent_status(goal_id, f"reviewer:{sid}-sprint-review", "completed")
        if infosec_ok and reviewer_ok:
            _upsert_agent_status(goal_id, f"sprint_close:{sid}", "completed")

    return {
        "status": final_status,
        "recorded": True,
        "role": role_key,
        "task_id": task_id,
        "agent_status": final_status,
        "task_phase": next_phase or current_phase,
        "bugs_filed": len(filed_bugs),
        "next": next_action,
    }


def enki_wave(project: str | None = None) -> dict:
    """Get next wave of tasks. Phase-aware dispatch with worktree isolation."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    goal_id = active["goal_id"]
    if not (_has_hitl_approval(project, "spec") or _has_hitl_approval(project, "igi")):
        return {"error": "Specs not approved."}

    sprint = get_active_sprint(project)
    if not sprint:
        return {
            "error": (
                "No active sprint. Call enki_decompose(tasks=[...]) first. "
                "Each task must include name, description, files, dependencies."
            )
        }
    sprint_id = sprint["sprint_id"]

    # Branch safety check
    project_path = _get_project_path(project)
    sprint_branch = _get_sprint_base_branch(project, sprint_id)
    if project_path:
        _cur = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        _cur_branch = _cur.stdout.strip()
        if _cur_branch in {"main", "master"} and sprint_branch not in {"main", "master", _cur_branch, ""}:
            return {
                "error": (
                    f"Cannot start wave from protected branch '{_cur_branch}'. "
                    f"Checkout sprint branch '{sprint_branch}' first."
                )
            }

    # Process pending merges before getting next wave
    merge_results = _process_merge_queue(project)

    # Recover tasks from dead sessions
    recovered = _recover_dead_session_tasks(project)

    # Get session identity
    session_id = os.environ.get("ENKI_SESSION_ID", "")
    if not session_id:
        _sid_file = ENKI_ROOT / "current_session_id"
        if _sid_file.exists():
            try:
                session_id = _sid_file.read_text().strip()
            except Exception:
                pass
    if not session_id:
        session_id = str(uuid.uuid4())[:12]

    # Phase-aware task dispatch
    with em_db(project) as conn:
        test_design_tasks = conn.execute(
            "SELECT * FROM task_state WHERE sprint_id=? AND task_phase='test_design' "
            "AND status='pending' ORDER BY task_id",
            (sprint_id,),
        ).fetchall()
        implement_tasks = conn.execute(
            "SELECT * FROM task_state WHERE sprint_id=? AND task_phase='implementing' "
            "AND status='pending' ORDER BY task_id",
            (sprint_id,),
        ).fetchall()
        verify_tasks = conn.execute(
            "SELECT * FROM task_state WHERE sprint_id=? AND task_phase='verifying' "
            "AND status='pending' ORDER BY task_id",
            (sprint_id,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM task_state WHERE sprint_id=?",
            (sprint_id,),
        ).fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(*) as c FROM task_state WHERE sprint_id=? "
            "AND task_phase='complete' AND status='completed'",
            (sprint_id,),
        ).fetchone()["c"]

    # Sprint complete check
    if done == total and total > 0:
        sprint_close_done = _has_agent_status(goal_id, f"sprint_close:{sprint_id}", "completed")
        if not sprint_close_done:
            return {
                "message": (
                    "All tasks complete. Sprint close pipeline required before validating. "
                    "Call enki_sprint_close() to run InfoSec + sprint Reviewer."
                ),
                "sprint_complete": False,
                "sprint_close_required": True,
                "sprint_id": sprint_id,
            }
        return {
            "message": (
                "Sprint complete and close pipeline done. "
                "Call enki_phase(action='status') then "
                "enki_phase(action='advance', to='validating')."
            ),
            "sprint_complete": True,
            "sprint_id": sprint_id,
        }

    all_ready = (
        list(test_design_tasks) + list(implement_tasks) +
        list(verify_tasks)
    )
    if not all_ready:
        return {
            "message": (
                "No tasks ready. Tasks may be in_progress or awaiting phase gates. "
                "Call enki_phase(action='status') to diagnose."
            ),
            "sprint_complete": False,
            "sprint_id": sprint_id,
            "recovered_tasks": recovered,
        }

    MAX_PARALLEL = 2
    wave_tasks: list[tuple[str, dict]] = []

    def _claim_task(task_row) -> dict:
        t = dict(task_row)
        task_id_inner = t["task_id"]
        sprint_slug = sprint_id.replace(f"{_project_slug(project)}-", "")
        branch_name = f"{_project_slug(project)}/{sprint_slug}/{task_id_inner}"
        worktree_path = None
        if project_path:
            worktree_path = _create_task_worktree(
                project_path, task_id_inner, branch_name, sprint_branch
            )
        with em_db(project) as conn:
            conn.execute(
                "UPDATE task_state SET status='in_progress', session_id=?, "
                "worktree_path=?, started_at=datetime('now') WHERE task_id=?",
                (session_id, worktree_path, task_id_inner),
            )
        t["worktree_path"] = worktree_path
        t["branch_name"] = branch_name
        return t

    candidates = (
        [("verifying", t) for t in verify_tasks] +
        [("implementing", t) for t in implement_tasks] +
        [("test_design", t) for t in test_design_tasks]
    )

    for phase, task_row in candidates:
        if len(wave_tasks) >= MAX_PARALLEL:
            break
        t = dict(task_row)
        raw_deps = t.get("dependencies")
        if isinstance(raw_deps, str):
            deps = json.loads(raw_deps or "[]")
        else:
            deps = raw_deps or []
        deps_ok = True
        with em_db(project) as conn:
            for dep_id in deps:
                dep = conn.execute(
                    "SELECT task_phase FROM task_state WHERE task_id=?",
                    (dep_id,),
                ).fetchone()
                if not dep or dep["task_phase"] != "complete":
                    deps_ok = False
                    break
        if not deps_ok:
            continue
        claimed = _claim_task(task_row)
        wave_tasks.append((phase, claimed))

    if not wave_tasks:
        return {
            "message": "Tasks exist but dependencies not yet satisfied. Wait for in-progress tasks to complete.",
            "sprint_complete": False,
            "sprint_id": sprint_id,
        }

    wave_no = _next_wave_number(project)
    phase_instructions = {
        "test_design": (
            "PHASE: test_design\n"
            "1. enki_spawn('qa', '{task_id}', {{'mode': 'write'}}) → Task tool → enki_report(mode='write')\n"
            "2. enki_spawn('dev', '{task_id}') IMMEDIATELY after QA-write (do NOT wait for Validator)\n"
            "3. enki_spawn('validator', '{task_id}', {{'mode': 'review-tests'}}) → enki_report(mode='review-tests')\n"
            "4. enki_spawn('qa', '{task_id}', {{'mode': 'execute'}}) → enki_report(mode='execute')\n"
            "5. enki_spawn('validator', '{task_id}', {{'mode': 'compliance'}}) → enki_report(mode='compliance')\n"
            "6. enki_complete(task_id='{task_id}')"
        ),
        "implementing": (
            "PHASE: implementing\n"
            "1. enki_spawn('dev', '{task_id}') → Task tool FOREGROUND → wait\n"
            "2. enki_report(role='dev', task_id='{task_id}', summary=..., status='completed')\n"
            "→ Task advances to verifying, call enki_wave()"
        ),
        "verifying": (
            "PHASE: verifying\n"
            "1. enki_spawn('qa', '{task_id}', {{'mode': 'execute', "
            "'test_path': 'tests/tasks/{task_id}/'}}) "
            "→ Task tool FOREGROUND → wait\n"
            "2. enki_report(role='qa', task_id='{task_id}', summary=..., status='completed')\n"
            "3. enki_spawn('validator', '{task_id}', {{'mode': 'compliance'}}) "
            "→ Task tool FOREGROUND → wait\n"
            "4. enki_report(role='validator', task_id='{task_id}', summary=..., "
            "status='completed', output={{...validator JSON...}})\n"
            "→ If passes: advances to complete\n"
            "→ If fails: bugs filed, resets to implementing"
        ),
    }

    task_instructions = []
    spawn_plans = []
    for phase, t in wave_tasks:
        instr = phase_instructions.get(phase, "").format(task_id=t["task_id"])
        task_instructions.append(
            f"Task {t['task_id']} [{t['task_name'][:30]}] — {instr}"
        )
        if phase == "test_design":
            qa_spawn = enki_spawn(
                "qa",
                t["task_id"],
                {"mode": "write", "test_path": f"tests/tasks/{t['task_id']}/"},
                project,
            )
            dev_spawn = enki_spawn("dev", t["task_id"], {}, project)
            spawn_plans.append(
                {
                    "task_id": t["task_id"],
                    "task_name": t["task_name"],
                    "agents": [
                        {
                            "role": "qa",
                            "mode": "write",
                            "prompt_path": qa_spawn.get("prompt_path"),
                            "context_artifact": qa_spawn.get("context_artifact"),
                            "instruction": (
                                "Run QA-write via Task tool (foreground). "
                                "After completion call enki_report(role='qa', mode='write', ...)."
                            ),
                            "order": 1,
                        },
                        {
                            "role": "dev",
                            "prompt_path": dev_spawn.get("prompt_path"),
                            "context_artifact": dev_spawn.get("context_artifact"),
                            "instruction": (
                                "Run Dev via Task tool (foreground) IMMEDIATELY after QA-write — "
                                "do NOT wait for Validator before starting Dev. "
                                "After completion call enki_report(role='dev', ...)."
                            ),
                            "order": 2,
                        },
                    ],
                    "parallel_note": (
                        "Spawn QA-write first, then Dev immediately after. "
                        "After BOTH complete: run Validator review-tests, "
                        "then QA execute, then Validator compliance."
                    ),
                }
            )
            _advance_task_phase(project, t["task_id"], "implementing")

    wave_artifact = _goal_artifacts_dir(project) / f"wave-{wave_no}.md"
    wave_artifact.write_text(_format_md({
        "wave_number": wave_no,
        "sprint_id": sprint_id,
        "sprint_branch": sprint_branch,
        "session_id": session_id,
        "tasks": [
            {"task_id": t["task_id"], "task_name": t["task_name"],
             "phase": ph, "worktree_path": t.get("worktree_path")}
            for ph, t in wave_tasks
        ],
        "recovered_tasks": recovered,
        "merge_results": merge_results,
    }))

    return {
        "message": (
            f"Wave {wave_no} — {len(wave_tasks)} task(s). "
            + (f"{recovered} orphaned task(s) recovered. " if recovered else "")
            + "ALWAYS use Task tool, NEVER Agent tool. "
            "Follow exact phase sequence for each task below."
        ),
        "wave_number": wave_no,
        "sprint_id": sprint_id,
        "sprint_branch": sprint_branch,
        "session_id": session_id,
        "tasks": [
            {
                "task_id": t["task_id"],
                "task_name": t["task_name"],
                "phase": ph,
                "description": t.get("description", ""),
                "assigned_files": (
                    json.loads(t.get("assigned_files") or "[]")
                    if isinstance(t.get("assigned_files"), str)
                    else (t.get("assigned_files") or [])
                ),
                "worktree_path": t.get("worktree_path"),
                "branch_name": t.get("branch_name"),
            }
            for ph, t in wave_tasks
        ],
        "instructions": task_instructions,
        "spawn_instructions": spawn_plans,
        "recovered_tasks": recovered,
        "merge_results": merge_results,
        "checkpoint_reviewer_required": _should_fire_checkpoint(project, sprint_id),
        "checkpoint_scope_task_count": int(
            _read_project_state_loose(project, "reviewer_checkpoint_interval") or "0"
        ),
    }


def enki_complete(task_id: str, project: str | None = None) -> dict:
    """Finalise a task. Gates: task_phase=complete, no open P1 bugs."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active
    _ = active["goal_id"]

    task = get_task(project, task_id)
    if not task:
        return {"error": f"Task {task_id} not found."}

    # Gate 1: task_phase must be 'complete'
    task_phase = task.get("task_phase") or _get_task_phase(project, task_id)
    if task_phase != "complete":
        return {
            "error": (
                f"Task {task_id} is in phase '{task_phase}', not ready for completion. "
                "Full sequence required: test_design → implementing → verifying → complete. "
                f"Current phase: {task_phase}."
            )
        }

    # Gate 2: no open P1 bugs for this task
    open_p1 = _get_open_bugs(project, task_id, severity="P1")
    if open_p1:
        bug_ids = [b.get("id", "?") for b in open_p1]
        return {
            "error": (
                f"Task {task_id} has {len(open_p1)} open P1 bug(s): {bug_ids}. "
                "Resolve all P1 bugs before completing."
            )
        }

    # Ship gate: assigned files must exist on disk before completion.
    task_files = task.get("assigned_files") or []
    if isinstance(task_files, str):
        try:
            task_files = json.loads(task_files)
        except Exception:
            task_files = []

    if task_files:
        project_path = _get_project_path(project)
        if project_path:
            missing_files = []
            for fp in task_files:
                full_path = Path(project_path) / fp
                if not full_path.exists():
                    missing_files.append(fp)
            if missing_files:
                return {
                    "error": (
                        f"Ship gate: {len(missing_files)} assigned file(s) not found on disk. "
                        f"Missing: {missing_files}. "
                        "Dev must create all assigned files before task can complete."
                    ),
                    "missing_files": missing_files,
                }

    update_task_status(project, task_id, TaskStatus.COMPLETED)

    # Register assigned files for diagram support
    task_files = task.get("assigned_files") or []
    if isinstance(task_files, str):
        try:
            task_files = json.loads(task_files)
        except Exception:
            task_files = []
    if task_files:
        with em_db(project) as conn:
            for fp in task_files:
                if fp:
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO file_registry "
                            "(project_id, file_path, task_id, action) "
                            "VALUES (?, ?, ?, 'modified')",
                            (project, fp, task_id),
                        )
                    except Exception:
                        pass

    # Queue merge into sprint base branch
    worktree_path = task.get("worktree_path")
    sprint_branch_val = None
    if worktree_path:
        sprint = get_active_sprint(project)
        _sid = sprint["sprint_id"] if sprint else task.get("sprint_id", "sprint-1")
        sprint_branch_val = _get_sprint_base_branch(project, _sid)
        _slug = _sid.replace(f"{_project_slug(project)}-", "")
        _branch = f"{_project_slug(project)}/{_slug}/{task_id}"
        with em_db(project) as conn:
            conn.execute(
                "INSERT INTO merge_queue "
                "(task_id, project_id, branch_name, worktree_path, sprint_branch) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, project, _branch, worktree_path, sprint_branch_val),
            )
            conn.execute(
                "UPDATE task_state SET session_id=NULL WHERE task_id=?",
                (task_id,),
            )

    return {
        "status": "completed",
        "task_id": task_id,
        "merge_queued": worktree_path is not None,
        "next": "enki_mail_inbox() then enki_wave() for next wave.",
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
    gemini_review_payload = None
    try:
        pending = _count_staged_candidates(project)
        if pending > 0:
            decisions = gemini_review.run_api_review(project=project).get("bead_decisions", [])
            promoted, discarded = _apply_wrap_gemini_decisions(project, decisions)
            gemini_review_payload = {
                "candidates_reviewed": pending,
                "promoted": promoted,
                "discarded": discarded,
            }
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
        "gemini_review": gemini_review_payload,
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


def enki_mail_inbox(
    agent: str = "EM",
    project: str = ".",
    ack_ids: list[str] | None = None,
) -> list[dict]:
    """Read inbox with explicit ack semantics.

    Fetch marks messages as delivered; pass ack_ids to mark specific messages read.
    """
    project = _resolve_project(project)

    if ack_ids:
        with em_db(project) as conn:
            for msg_id in ack_ids:
                conn.execute(
                    "UPDATE mail_messages SET status='read' WHERE id=? AND project_id=?",
                    (msg_id, project),
                )
        return [{"acked": len(ack_ids), "message": "Messages acknowledged."}]

    messages = get_inbox(project, agent)
    with em_db(project) as conn:
        for msg in messages:
            conn.execute(
                "UPDATE mail_messages SET status='delivered' WHERE id=? AND project_id=?",
                (msg.get("id"), project),
            )

    if messages:
        for msg in messages:
            msg["_ack_instruction"] = (
                f"After processing, call enki_mail_inbox(ack_ids=['{msg.get('id')}']) "
                "to mark as fully consumed."
            )

    return messages


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
    filed_by: str = "Human",
    task_id: str | None = None,
    project: str | None = None,
) -> dict:
    """File or manage bugs."""
    project = _resolve_project(project)
    priority_map = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
    sev = (severity or "medium").upper()
    priority = sev if sev in {"P0", "P1", "P2", "P3"} else priority_map.get((severity or "medium").lower(), "P2")

    if action == "file":
        assigned_to = "architect" if filed_by == "infosec" else None
        internal_id = file_bug(
            project=project,
            title=title or "Untitled bug",
            description=description or "",
            filed_by=filed_by,
            priority=priority,
            task_id=task_id,
        )
        if assigned_to:
            with em_db(project) as conn:
                conn.execute(
                    "UPDATE bugs SET assigned_to = ? WHERE id = ?",
                    (assigned_to, internal_id),
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


def enki_graph_rebuild(
    project: str | None = None,
    incremental: bool = False,
) -> dict:
    """Build or rebuild the codebase knowledge graph for a project."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    project_path = _get_project_path(project)
    if not project_path:
        return {
            "error": (
                f"Project path not registered for '{project}'. "
                "Call enki_register(path='.') first."
            )
        }

    try:
        from enki.graph.scanner import run_full_scan, run_incremental_update

        if incremental:
            stats = run_incremental_update(project, project_path)
        else:
            stats = run_full_scan(project, project_path)
    except ImportError:
        return {
            "error": (
                "tree-sitter-languages not installed. "
                "Run: pip install tree-sitter-languages==1.10.2"
            )
        }
    except Exception as e:
        return {"error": f"Graph build failed: {e}"}

    return {
        "message": f"Graph {'updated' if incremental else 'built'} for {project}.",
        "project": project,
        "incremental": incremental,
        **stats,
    }


def enki_graph_query(
    query_type: str,
    target: str,
    project: str | None = None,
    limit: int = 10,
) -> dict:
    """Query the codebase knowledge graph."""
    project = _resolve_project(project)

    from enki.db import graph_db, graph_db_path

    if not graph_db_path(project).exists():
        return {
            "error": (
                "No graph.db found for this project. "
                "Call enki_graph_rebuild() first."
            )
        }

    try:
        with graph_db(project) as conn:
            if query_type == "blast_radius":
                rows = conn.execute(
                    "SELECT b.*, f.language FROM blast_radius b "
                    "JOIN files f ON b.file_path = f.path "
                    "WHERE b.file_path = ? OR b.symbol_id LIKE ?",
                    (target, f"{target}%"),
                ).fetchall()
                return {
                    "query_type": query_type,
                    "target": target,
                    "results": [dict(r) for r in rows[:limit]],
                }

            if query_type == "importers":
                rows = conn.execute(
                    "SELECT from_id, line_number FROM edges "
                    "WHERE to_id = ? AND edge_type = 'imports' "
                    "LIMIT ?",
                    (target, limit),
                ).fetchall()
                return {
                    "query_type": query_type,
                    "target": target,
                    "importers": [r["from_id"] for r in rows],
                    "count": len(rows),
                }

            if query_type == "imports":
                rows = conn.execute(
                    "SELECT to_id, line_number FROM edges "
                    "WHERE from_id = ? AND edge_type = 'imports' "
                    "LIMIT ?",
                    (target, limit),
                ).fetchall()
                return {
                    "query_type": query_type,
                    "target": target,
                    "imports": [r["to_id"] for r in rows],
                    "count": len(rows),
                }

            if query_type == "symbols":
                rows = conn.execute(
                    "SELECT name, kind, line_start, complexity, is_exported "
                    "FROM symbols WHERE file_path = ? "
                    "ORDER BY line_start LIMIT ?",
                    (target, limit),
                ).fetchall()
                return {
                    "query_type": query_type,
                    "target": target,
                    "symbols": [dict(r) for r in rows],
                    "count": len(rows),
                }

            if query_type == "complexity":
                rows = conn.execute(
                    "SELECT name, kind, line_start, complexity "
                    "FROM symbols WHERE file_path = ? "
                    "ORDER BY complexity DESC LIMIT ?",
                    (target, limit),
                ).fetchall()
                return {
                    "query_type": query_type,
                    "target": target,
                    "hotspots": [dict(r) for r in rows],
                }

            return {
                "error": (
                    f"Unknown query_type '{query_type}'. "
                    "Options: blast_radius, importers, imports, "
                    "symbols, complexity, duplicates, callers"
                )
            }
    except Exception as e:
        return {"error": f"Graph query failed: {e}"}


def enki_validate(
    scope: str = "sprint",
    project: str | None = None,
    hitl_confirmed: bool = False,
) -> dict:
    """Validation state machine for sprint-end and project-end."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    scope_key = (scope or "sprint").strip().lower()
    if scope_key not in ("sprint", "project"):
        return {"error": "scope must be 'sprint' or 'project'"}

    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint. Cannot validate."}
    sprint_id = sprint["sprint_id"]

    state = _load_validate_state(project, sprint_id)
    if not state:
        state = {
            "scope": scope_key,
            "status": "init",
            "audits_complete": [],
            "bugs_filed": [],
            "fix_cycles": 0,
            "spawn_instructions": [],
        }
    else:
        state["scope"] = state.get("scope", scope_key)

    status = state.get("status", "init")

    if status == "init":
        modified_files = _get_sprint_modified_files(project, sprint_id)
        spec_path = _get_spec_final_path(project)
        impl_spec_path = _get_impl_spec_path(project)
        spawn_instructions = []

        devops_context = {
            "mode": "full-regression" if scope_key == "project" else "sprint-tests",
            "modified_files": modified_files,
            "sprint_id": sprint_id,
        }
        devops_spawn = enki_spawn("devops", f"{sprint_id}-tests", devops_context, project)
        spawn_instructions.append({
            "role": "devops",
            "mode": devops_context["mode"],
            "task_id": f"{sprint_id}-tests",
            "prompt_path": devops_spawn.get("prompt_path"),
            "context_artifact": devops_spawn.get("context_artifact"),
        })

        infosec_context = {
            "mode": "sprint-audit",
            "spec_final_path": str(spec_path) if spec_path else None,
            "impl_spec_path": str(impl_spec_path) if impl_spec_path else None,
            "modified_files": modified_files,
            "sprint_id": sprint_id,
        }
        infosec_spawn = enki_spawn(
            "infosec",
            f"{sprint_id}-infosec-audit",
            infosec_context,
            project,
        )
        spawn_instructions.append({
            "role": "infosec",
            "mode": "sprint-audit",
            "task_id": f"{sprint_id}-infosec-audit",
            "prompt_path": infosec_spawn.get("prompt_path"),
            "context_artifact": infosec_spawn.get("context_artifact"),
        })

        reviewer_context = {
            "mode": "sprint-review",
            "spec_final_path": str(spec_path) if spec_path else None,
            "impl_spec_path": str(impl_spec_path) if impl_spec_path else None,
            "modified_files": modified_files,
            "sprint_id": sprint_id,
        }
        reviewer_spawn = enki_spawn(
            "reviewer",
            f"{sprint_id}-sprint-review",
            reviewer_context,
            project,
        )
        spawn_instructions.append({
            "role": "reviewer",
            "mode": "sprint-review",
            "task_id": f"{sprint_id}-sprint-review",
            "prompt_path": reviewer_spawn.get("prompt_path"),
            "context_artifact": reviewer_spawn.get("context_artifact"),
        })

        if scope_key == "project":
            validator_context = {
                "mode": "project-compliance",
                "spec_final_path": str(spec_path) if spec_path else None,
                "modified_files": modified_files,
                "sprint_id": sprint_id,
            }
            validator_spawn = enki_spawn(
                "validator",
                f"{sprint_id}-project-compliance",
                validator_context,
                project,
            )
            spawn_instructions.append({
                "role": "validator",
                "mode": "project-compliance",
                "task_id": f"{sprint_id}-project-compliance",
                "prompt_path": validator_spawn.get("prompt_path"),
                "context_artifact": validator_spawn.get("context_artifact"),
            })

        state["status"] = "auditing"
        state["spawn_instructions"] = spawn_instructions
        _save_validate_state(project, sprint_id, state)
        return {
            "message": f"Validation started ({scope_key} scope). Run all auditors.",
            "scope": scope_key,
            "spawn_instructions": spawn_instructions,
            "next": (
                "Run each auditor via Task tool (foreground, sequential). "
                "After each: call enki_validate_update(role=..., output=...). "
                "After all complete: call enki_validate() again."
            ),
        }

    if status == "auditing":
        audits_complete = state.get("audits_complete", [])
        required = ["devops", "infosec", "reviewer"]
        if state.get("scope") == "project":
            required.append("validator")
        pending = [r for r in required if r not in audits_complete]
        if pending:
            return {
                "message": f"Waiting for auditors to complete: {pending}",
                "completed": audits_complete,
                "pending": pending,
            }

        if state.get("scope") == "project" and "codex-reviewer" not in audits_complete:
            project_path = _get_project_path(project) or ""
            modified_files = _get_sprint_modified_files(project, sprint_id)
            spec_path = _get_spec_final_path(project)
            impl_spec_path = _get_impl_spec_path(project)
            spec_content = _read_text_safe(spec_path)
            impl_content = _read_text_safe(impl_spec_path)
            codex_result = _spawn_codex_reviewer(
                project=project,
                spec_content=spec_content,
                impl_spec_content=impl_content,
                modified_files=modified_files,
                project_path=project_path,
            )
            if codex_result:
                state["codex_review"] = codex_result
                audits_complete.append("codex-reviewer")
                state["audits_complete"] = audits_complete
                _save_validate_state(project, sprint_id, state)

                reconcile_context = {
                    "mode": "multi-model-reconcile",
                    "review_a": state.get("reviewer_output", {}),
                    "review_b": state.get("infosec_output", {}),
                    "review_c": codex_result,
                    "instruction": (
                        "Three independent reviewers have reviewed the codebase. "
                        "Their outputs are labeled Review A, B, C (not by reviewer identity). "
                        "Identify: (1) agreements — issues all/most found (high confidence, file as bugs), "
                        "(2) disagreements — only one reviewer found (present to HITL). "
                        "Output JSON: {agreements: [...], disagreements: [...], "
                        "bugs_to_file: [{title, description, severity, files}]}"
                    ),
                }
                arch_spawn = enki_spawn(
                    "architect",
                    f"{sprint_id}-reconcile",
                    reconcile_context,
                    project,
                )
                state["status"] = "reconciling"
                _save_validate_state(project, sprint_id, state)
                return {
                    "message": "Codex review complete. Architect reconciliation required.",
                    "spawn_instructions": [{
                        "role": "architect",
                        "mode": "multi-model-reconcile",
                        "task_id": f"{sprint_id}-reconcile",
                        "prompt_path": arch_spawn.get("prompt_path"),
                        "context_artifact": arch_spawn.get("context_artifact"),
                    }],
                    "next": (
                        "Run Architect via Task tool. After completion: "
                        "call enki_validate_update(role='architect-reconcile', output={...})."
                    ),
                }
            state["codex_review"] = None
            audits_complete.append("codex-reviewer")
            state["audits_complete"] = audits_complete
            _save_validate_state(project, sprint_id, state)

        state["status"] = "prioritizing"
        _save_validate_state(project, sprint_id, state)
        status = "prioritizing"

    if status == "reconciling":
        return {
            "message": "Awaiting Architect reconciliation output.",
            "next": (
                "Run Architect with the provided reconcile context, then call "
                "enki_validate_update(role='architect-reconcile', output={...})."
            ),
        }

    if status == "prioritizing":
        open_bugs = _get_draft_bugs(project)
        if not open_bugs:
            state["status"] = "clear"
            _save_validate_state(project, sprint_id, state)
            status = "clear"
        else:
            spec_path = _get_spec_final_path(project)
            priority_context = {
                "mode": "bug-prioritization",
                "bugs": open_bugs,
                "spec_final_path": str(spec_path) if spec_path else None,
                "instruction": (
                    "Review all draft bugs and assign final priority. "
                    "P0: exploitable/blocking. P1: significant/required fix. "
                    "P2: important but not blocking. P3: minor/informational. "
                    "Output JSON: {bugs: [{bug_id, priority, rationale}]}"
                ),
            }
            arch_spawn = enki_spawn(
                "architect",
                f"{sprint_id}-bug-priority",
                priority_context,
                project,
            )
            state["status"] = "awaiting_priority"
            _save_validate_state(project, sprint_id, state)
            return {
                "message": "Bugs filed. Architect priority review required.",
                "bugs_to_prioritize": len(open_bugs),
                "spawn_instructions": [{
                    "role": "architect",
                    "mode": "bug-prioritization",
                    "task_id": f"{sprint_id}-bug-priority",
                    "prompt_path": arch_spawn.get("prompt_path"),
                    "context_artifact": arch_spawn.get("context_artifact"),
                }],
                "next": (
                    "Run Architect via Task tool. After completion call "
                    "enki_validate_update(role='architect', output={bugs:[...]})."
                ),
            }

    if status == "awaiting_priority":
        blocking_bugs = _get_bugs_by_severity(project, ["P0", "P1"])
        if not hitl_confirmed:
            return {
                "message": (
                    f"Architect priority review complete. "
                    f"{len(blocking_bugs)} P0/P1 bug(s) require fixing. "
                    "Review the prioritized bug list and confirm to proceed."
                ),
                "blocking_bugs": len(blocking_bugs),
                "p0p1_bugs": [
                    {
                        "id": b["id"],
                        "title": b.get("title"),
                        "priority": b.get("priority"),
                    }
                    for b in blocking_bugs
                ],
                "hitl_required": True,
                "next": (
                    "Review bugs above. "
                    "Call enki_validate(hitl_confirmed=True) to start the fix loop."
                    if blocking_bugs else
                    "No P0/P1 bugs found. "
                    "Call enki_validate(hitl_confirmed=True) to complete validation."
                ),
            }
        state["status"] = "fixing" if blocking_bugs else "clear"
        _save_validate_state(project, sprint_id, state)
        status = state["status"]

    if status == "fixing":
        blocking_bugs = _get_bugs_by_severity(project, ["P0", "P1"])
        unfixed = [b for b in blocking_bugs if b.get("status") == "open"]
        if not unfixed:
            state["status"] = "revalidating"
            _save_validate_state(project, sprint_id, state)
            status = "revalidating"
        else:
            fix_cycle = int(state.get("fix_cycles", 0)) + 1
            state["fix_cycles"] = fix_cycle
            spawn_instructions = []
            for bug in unfixed[:3]:
                if int(bug.get("revalidation_cycle") or 0) >= 3:
                    _escalate_bug(project, bug["id"])
                    continue
                fix_context = {
                    "mode": "bug-fix",
                    "bug_id": bug["id"],
                    "bug_title": bug.get("title", ""),
                    "bug_description": bug.get("description", ""),
                    "files_to_fix": _bug_affected_files(bug),
                    "reported_by": _bug_reporter(bug),
                }
                dev_spawn = enki_spawn("dev", f"bugfix-{bug['id']}", fix_context, project)
                spawn_instructions.append({
                    "role": "dev",
                    "bug_id": bug["id"],
                    "task_id": f"bugfix-{bug['id']}",
                    "prompt_path": dev_spawn.get("prompt_path"),
                    "context_artifact": dev_spawn.get("context_artifact"),
                })

            _save_validate_state(project, sprint_id, state)
            if spawn_instructions:
                return {
                    "message": f"Fix cycle {fix_cycle}. {len(unfixed)} P0/P1 bugs to fix.",
                    "spawn_instructions": spawn_instructions,
                    "next": (
                        "For each bug: run Dev -> QA execute -> Validator compliance. "
                        "After all fixed: call enki_validate_update(role='fix-complete', "
                        "output={fixed_bugs:[...]})."
                    ),
                }

    if status == "revalidating":
        needs_revalidation = _get_bugs_needing_revalidation(project)
        if not needs_revalidation:
            still_open = [
                b for b in _get_bugs_by_severity(project, ["P0", "P1"])
                if b.get("status") != "closed"
            ]
            if still_open:
                state["status"] = "fixing"
                _save_validate_state(project, sprint_id, state)
                return {
                    "error": (
                        f"{len(still_open)} P0/P1 bug(s) still open in DB. "
                        "Fix loop must continue."
                    ),
                    "still_open": [
                        {
                            "id": b["id"],
                            "title": b.get("title"),
                            "priority": b.get("priority"),
                        }
                        for b in still_open
                    ],
                }
            state["status"] = "clear"
            _save_validate_state(project, sprint_id, state)
            status = "clear"
        else:
            spawn_instructions = []
            for bug in needs_revalidation:
                reporter = _bug_reporter(bug) or "infosec"
                reval_context = {
                    "mode": "revalidate",
                    "bug_id": bug["id"],
                    "original_concern": bug.get("description", ""),
                    "files_to_check": _bug_affected_files(bug),
                    "fix_summary": bug.get("fix_summary", ""),
                }
                reporter_spawn = enki_spawn(
                    reporter,
                    f"revalidate-{bug['id']}",
                    reval_context,
                    project,
                )
                spawn_instructions.append({
                    "role": reporter,
                    "bug_id": bug["id"],
                    "task_id": f"revalidate-{bug['id']}",
                    "prompt_path": reporter_spawn.get("prompt_path"),
                    "context_artifact": reporter_spawn.get("context_artifact"),
                })
            return {
                "message": f"Reporter revalidation required for {len(needs_revalidation)} bugs.",
                "spawn_instructions": spawn_instructions,
                "next": (
                    "Run each reporter via Task tool. After each: "
                    "call enki_validate_update(role=reporter, "
                    "output={bug_id:..., cleared:bool})."
                ),
            }

    if status == "clear":
        p2p3 = _get_bugs_by_severity(project, ["P2", "P3"])
        scope_done = state.get("scope", scope_key)
        disposition = "carry_to_next_sprint" if scope_done == "sprint" else "tech_debt"
        return {
            "message": f"Validation complete ({scope_done} scope). All P0/P1 resolved.",
            "scope": scope_done,
            "p2p3_bugs": len(p2p3),
            "p2p3_disposition": disposition,
            "next": (
                "Call enki_sprint_close() to complete sprint and generate summary."
                if scope_done == "sprint"
                else "Call enki_project_close() to finalize the project."
            ),
        }

    return {"error": f"Unknown validate state: {status}"}


def enki_validate_update(
    role: str,
    output: dict,
    project: str | None = None,
) -> dict:
    """Record auditor/fixer output during validation."""
    project = _resolve_project(project)
    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint."}
    sprint_id = sprint["sprint_id"]
    state = _load_validate_state(project, sprint_id) or {}
    role_key = (role or "").strip().lower()

    if role_key in ("devops", "infosec", "reviewer", "validator"):
        completed = state.get("audits_complete", [])
        if role_key not in completed:
            completed.append(role_key)
        state["audits_complete"] = completed
        state[f"{role_key}_output"] = output

        bugs_filed = []
        findings = []
        for key in (
            "violations",
            "spec_gaps",
            "pattern_issues",
            "architectural_issues",
            "cross_cutting_issues",
            "quality_violations",
        ):
            val = output.get(key, [])
            if isinstance(val, list):
                findings.extend(val)
        for issue in findings:
            if not isinstance(issue, dict):
                continue
            severity_raw = str(
                issue.get("severity", issue.get("priority", "P2"))
            ).strip()
            if severity_raw in ("error", "P0", "blocking"):
                sev = "P0"
            elif severity_raw in ("high", "P1"):
                sev = "P1"
            elif severity_raw in ("warning", "medium", "P2"):
                sev = "P2"
            else:
                sev = "P3"
            title = (
                issue.get("rule")
                or issue.get("issue")
                or issue.get("requirement")
                or issue.get("pattern")
                or "Untitled finding"
            )
            description = (
                issue.get("description")
                or issue.get("gap")
                or issue.get("recommendation")
                or str(issue)
            )
            bug_id = _file_bug(
                project=project,
                title=title,
                description=description,
                severity=sev,
                filed_by=role_key,
                reporter=role_key,
                affected_files=issue.get("files", issue.get("files_affected", [])),
            )
            if bug_id:
                bugs_filed.append(bug_id)

        state["bugs_filed"] = state.get("bugs_filed", []) + bugs_filed
        _save_validate_state(project, sprint_id, state)
        return {
            "message": f"{role_key} output recorded. {len(bugs_filed)} bugs filed.",
            "bugs_filed": bugs_filed,
            "audits_complete": completed,
        }

    if role_key == "architect-reconcile":
        bugs_filed = []
        for issue in output.get("bugs_to_file", []):
            if not isinstance(issue, dict):
                continue
            severity_raw = str(issue.get("severity", "P2")).strip()
            if severity_raw in ("error", "P0", "blocking"):
                sev = "P0"
            elif severity_raw in ("high", "P1"):
                sev = "P1"
            elif severity_raw in ("warning", "medium", "P2"):
                sev = "P2"
            else:
                sev = "P3"
            bug_id = _file_bug(
                project=project,
                title=issue.get("title", "Reconciled issue"),
                description=issue.get("description", ""),
                severity=sev,
                filed_by="architect",
                reporter="architect",
                affected_files=issue.get("files", []),
            )
            if bug_id:
                bugs_filed.append(bug_id)
        state["architect_reconcile_output"] = output
        state["status"] = "awaiting_priority"
        _save_validate_state(project, sprint_id, state)
        return {
            "message": "Architect reconciliation recorded.",
            "bugs_filed": bugs_filed,
        }

    if role_key == "architect":
        for bug_update in output.get("bugs", []):
            if isinstance(bug_update, dict):
                _update_bug_priority(
                    project,
                    bug_update.get("bug_id", ""),
                    bug_update.get("priority", "P2"),
                )
        _save_validate_state(project, sprint_id, state)
        return {"message": "Bug priorities assigned."}

    if role_key == "fix-complete":
        fix_summary = output.get("fix_summary", "")
        for bug_id in output.get("fixed_bugs", []):
            _mark_bug_needs_revalidation(project, bug_id, fix_summary)
        state["status"] = "revalidating"
        _save_validate_state(project, sprint_id, state)
        return {"message": "Fixes recorded. Reporter revalidation queued."}

    bug_id = output.get("bug_id")
    cleared = bool(output.get("cleared", False))
    if bug_id:
        if cleared:
            _close_bug(project, bug_id)
        else:
            _increment_revalidation_cycle(project, bug_id)
            state["status"] = "fixing"
            _save_validate_state(project, sprint_id, state)
    return {
        "message": (
            f"Bug {bug_id} cleared."
            if cleared
            else f"Bug {bug_id} still open — fix cycle continues."
        )
    }


def enki_sprint_summary(sprint_id: str, project: str = ".") -> dict:
    """Get sprint summary."""
    project = _resolve_project(project)
    result = get_sprint_summary(project, sprint_id)
    if result.get("error"):
        return result

    try:
        session_id = _get_current_session_id()
        if session_id:
            with uru_db() as conn:
                drift_row = conn.execute(
                    "SELECT cumulative_score, nudge_count, escalated "
                    "FROM session_drift WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            if drift_row:
                score = float(drift_row["cumulative_score"] or 0.0)
                escalated = bool(drift_row["escalated"])
                result["drift"] = {
                    "cumulative_score": round(score, 1),
                    "nudge_count": int(drift_row["nudge_count"] or 0),
                    "escalated": escalated,
                    "status": (
                        "escalated"
                        if escalated
                        else "warning" if score >= 8
                        else "clean"
                    ),
                }
    except Exception:
        pass

    return result


def enki_sprint_close(
    project: str | None = None,
) -> dict:
    """Close current sprint after validation completes."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint to close."}
    sprint_id = sprint["sprint_id"]

    validate_state = _load_validate_state(project, sprint_id)
    steps = [
        "test_consolidation",
        "full_test_run",
        "infosec",
        "sprint_review",
        "verify_clean",
    ]
    if not validate_state or validate_state.get("status") != "clear":
        return {
            "message": (
                "Sprint close pipeline prepared. "
                "STEP 1: consolidate tests and artifacts. "
                "STEP 2: run full test sweep. "
                "STEP 3: run InfoSec sprint-audit. "
                "STEP 4: run Reviewer sprint-review. "
                "STEP 5: resolve P0/P1 findings and verify clean before validating. "
                "Validation not complete yet; call enki_validate(scope='sprint') first."
            ),
            "sprint_id": sprint_id,
            "steps": steps,
            "steps_detail": [
                "infosec_sprint_audit",
                "sprint_review",
                "fix_p0_p1",
                "advance_validating",
            ],
            "validate_status": (validate_state or {}).get("status", "not_started"),
        }

    summary = _generate_sprint_summary(project, sprint_id)
    p2p3_bugs = _get_bugs_by_severity(project, ["P2", "P3"])
    carried_tasks = []
    for bug in p2p3_bugs:
        carried_tasks.append({
            "name": f"[Tech Debt] {bug.get('title', 'Untitled bug')}",
            "description": bug.get("description", ""),
            "files": _bug_affected_files(bug),
            "source_bug_id": bug.get("id"),
            "priority": bug.get("priority", bug.get("severity", "P2")),
        })

    try:
        with em_db(project) as conn:
            conn.execute(
                "UPDATE sprint_state SET summary=?, status='completed' WHERE sprint_id=?",
                (json.dumps(summary), sprint_id),
            )
    except Exception:
        pass

    merge_result = _merge_sprint_branch(project, sprint_id)

    return {
        "message": "Sprint closed successfully.",
        "sprint_id": sprint_id,
        "summary": summary,
        "p2p3_carried": len(carried_tasks),
        "merge_result": merge_result,
        "steps": steps,
        "steps_detail": [
            "infosec_sprint_audit",
            "sprint_review",
            "fix_p0_p1",
            "advance_validating",
        ],
        "next": (
            "HITL decision required: is this the final sprint?\n"
            "  Yes (final) -> call enki_validate(scope='project') "
            "then enki_project_close()\n"
            "  No (more sprints) -> call enki_goal(description='...') "
            "to start next sprint"
        ),
        "next_sprint_seed_tasks": carried_tasks,
    }


def enki_project_close(project: str | None = None) -> dict:
    """Close the project after project-level validation completes."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    sprint = get_active_sprint(project)
    if sprint:
        validate_state = _load_validate_state(project, sprint["sprint_id"])
        if not validate_state or validate_state.get("status") != "clear":
            return {
                "error": (
                    "Project validation not complete. "
                    "Call enki_validate(scope='project') first."
                )
            }

    project_path = _get_project_path(project)
    if not project_path:
        return {"error": "Project path not registered."}

    results = {}
    results["worktrees_merged"] = _merge_all_worktrees(project, project_path)
    results["sprint_merged"] = _merge_sprint_to_main(project, project_path)
    results["pushed"] = _git_push_main(project_path)
    try:
        _ = enki_wrap()
        results["memory_wrap"] = "complete"
    except Exception as e:
        results["memory_wrap"] = f"failed: {e}"

    write_project_state(project, "phase", "closing")
    return {
        "message": "Project close complete. Pending HITL acceptance.",
        "project": project,
        "results": results,
        "hitl_required": True,
        "hitl_question": (
            "Project is ready for final acceptance.\n"
            "Review project output, then call enki_phase(action='advance', to='complete').\n"
            "Optionally call enki_document() to generate handover documentation."
        ),
    }


def enki_document(
    project: str | None = None,
    docs: list[str] | None = None,
) -> dict:
    """Generate project documentation via staged agent summaries."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    project_path = _get_project_path(project)
    if not project_path:
        return {"error": "Project path not registered."}

    if docs is None:
        docs = _detect_required_docs(project, project_path)

    spec_path = _get_spec_final_path(project)
    impl_spec_path = _get_impl_spec_path(project)
    sprint = get_active_sprint(project)
    all_files = _get_sprint_modified_files(project, sprint["sprint_id"]) if sprint else []

    pm_context = {
        "mode": "project-summary",
        "spec_final_path": str(spec_path) if spec_path else None,
        "impl_spec_path": str(impl_spec_path) if impl_spec_path else None,
        "docs_to_generate": docs,
        "instruction": (
            "Generate a structured JSON project summary. "
            "Read spec and summarize tasks, bugs, and key outcomes."
        ),
    }
    pm_spawn = enki_spawn("pm", "project-summary", pm_context, project)

    arch_context = {
        "mode": "architecture-summary",
        "impl_spec_path": str(impl_spec_path) if impl_spec_path else None,
        "modified_files": all_files,
        "instruction": (
            "Generate structured architecture summary. "
            "Use enki_graph_query for topology. Return components, relationships, decisions."
        ),
    }
    arch_spawn = enki_spawn("architect", "architecture-summary", arch_context, project)

    artifacts_dir = _goal_artifacts_dir(project)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    doc_state_path = artifacts_dir / "document-state.json"
    state = {"docs_to_generate": docs}
    if doc_state_path.exists():
        try:
            existing = json.loads(doc_state_path.read_text())
            if isinstance(existing, dict):
                state.update(existing)
        except Exception:
            pass
    doc_state_path.write_text(json.dumps(state, indent=2))

    return {
        "message": f"Documentation generation started. {len(docs)} documents to generate.",
        "docs_to_generate": docs,
        "spawn_instructions": [
            {
                "role": "pm",
                "mode": "project-summary",
                "prompt_path": pm_spawn.get("prompt_path"),
                "context_artifact": pm_spawn.get("context_artifact"),
                "instruction": "Run PM first, then enki_document_update(role='pm', output={...}).",
            },
            {
                "role": "architect",
                "mode": "architecture-summary",
                "prompt_path": arch_spawn.get("prompt_path"),
                "context_artifact": arch_spawn.get("context_artifact"),
                "instruction": "Run Architect next, then enki_document_update(role='architect', output={...}).",
            },
        ],
        "next": (
            "Run PM then Architect via Task tool. After both complete: "
            "call enki_document_update for each, then call enki_document() again if needed."
        ),
    }


def enki_document_update(
    role: str,
    output: dict,
    project: str | None = None,
) -> dict:
    """Record agent output during documentation generation."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    artifacts_dir = _goal_artifacts_dir(project)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    doc_state_path = artifacts_dir / "document-state.json"

    state: dict = {}
    if doc_state_path.exists():
        try:
            state = json.loads(doc_state_path.read_text())
        except Exception:
            state = {}
    state[(role or "").strip().lower()] = output
    doc_state_path.write_text(json.dumps(state, indent=2))

    if "pm" in state and "architect" in state and "technical-writer" not in state:
        docs_to_generate = state.get(
            "docs_to_generate",
            _detect_required_docs(project, _get_project_path(project) or ""),
        )
        spec_path = _get_spec_final_path(project)
        tw_context = {
            "mode": "generate-docs",
            "pm_summary": state["pm"],
            "architecture_summary": state["architect"],
            "spec_final_path": str(spec_path) if spec_path else None,
            "docs_to_generate": docs_to_generate,
            "project_path": _get_project_path(project),
            "instruction": (
                "Generate all requested documentation files under docs/. "
                "Use PM + Architect summaries as sources."
            ),
        }
        tw_spawn = enki_spawn("technical-writer", "generate-docs", tw_context, project)
        return {
            "message": "PM + Architect complete. Technical Writer ready.",
            "spawn_instructions": [{
                "role": "technical-writer",
                "mode": "generate-docs",
                "prompt_path": tw_spawn.get("prompt_path"),
                "context_artifact": tw_spawn.get("context_artifact"),
            }],
            "next": "Run Technical Writer via Task tool.",
        }

    if "technical-writer" in state:
        files_written = output.get("files_written", [])
        return {
            "message": f"Documentation complete. {len(files_written)} files written.",
            "files_written": files_written,
        }

    return {
        "message": f"{role} output recorded.",
        "pending": [r for r in ["pm", "architect"] if r not in state],
    }


def enki_wave_reconcile(project: str | None = None) -> dict:
    """Diagnose and recover stuck wave states."""
    project = _resolve_project(project)
    active = _require_active_goal(project)
    if active.get("error"):
        return active

    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint to reconcile."}

    sprint_id = sprint["sprint_id"]
    fixes: list[str] = []

    with em_db(project) as conn:
        # 1) Tasks in_progress with no session or dead session.
        rows = conn.execute(
            "SELECT task_id, session_id, task_phase FROM task_state "
            "WHERE sprint_id=? AND status='in_progress'",
            (sprint_id,),
        ).fetchall()
        for row in rows:
            session_alive = _is_tmux_session_alive(row["session_id"]) if row["session_id"] else False
            if not session_alive:
                conn.execute(
                    "UPDATE task_state SET status='pending', session_id=NULL, "
                    "started_at=NULL WHERE task_id=?",
                    (row["task_id"],),
                )
                fixes.append(
                    f"Reset orphaned task {row['task_id']} "
                    f"(session: {row['session_id'] or 'none'}) to pending"
                )

        # 2) Completed tasks with non-complete task_phase.
        rows = conn.execute(
            "SELECT task_id, task_phase FROM task_state "
            "WHERE sprint_id=? AND status='completed' AND task_phase != 'complete'",
            (sprint_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE task_state SET task_phase='complete' WHERE task_id=?",
                (row["task_id"],),
            )
            fixes.append(
                f"Advanced task {row['task_id']} task_phase to complete "
                f"(was {row['task_phase']})"
            )

        # 3) Stuck merge queue rows.
        rows = conn.execute(
            "SELECT id, task_id FROM merge_queue "
            "WHERE project_id=? AND status='merging'",
            (project,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE merge_queue SET status='queued' WHERE id=?",
                (row["id"],),
            )
            fixes.append(f"Reset stuck merge queue item for task {row['task_id']}")

        counts = {}
        for status in ("pending", "in_progress", "completed", "failed"):
            counts[status] = conn.execute(
                "SELECT COUNT(*) AS c FROM task_state WHERE sprint_id=? AND status=?",
                (sprint_id, status),
            ).fetchone()["c"]

    if not fixes:
        return {
            "message": "No stuck states found. Wave is healthy.",
            "sprint_id": sprint_id,
            "task_counts": counts,
            "fixes_applied": 0,
        }

    return {
        "message": f"Reconciliation complete. {len(fixes)} fix(es) applied.",
        "sprint_id": sprint_id,
        "fixes_applied": len(fixes),
        "fixes": fixes,
        "task_counts": counts,
        "next": "Call enki_wave() to resume.",
    }


def enki_diagram(type: str = "dag", project: str | None = None) -> dict:
    """Generate Mermaid diagrams from project state on demand.
    Types: dag (sprint task graph), files (file ownership),
           pipeline (phase status), codebase (directory structure)
    """
    project = _resolve_project(project)
    dtype = (type or "dag").strip().lower()
    dispatch = {
        "dag": _diagram_dag,
        "files": _diagram_files,
        "pipeline": _diagram_pipeline,
        "codebase": _diagram_codebase,
    }
    fn = dispatch.get(dtype)
    if not fn:
        return {"error": f"Unknown type '{type}'. Use: dag, files, pipeline, codebase"}
    return fn(project)


def _diagram_dag(project: str) -> dict:
    sprint = get_active_sprint(project)
    if not sprint:
        return {"error": "No active sprint."}
    tasks = get_sprint_tasks(project, sprint["sprint_id"])
    if not tasks:
        return {"error": "No tasks in sprint."}
    icons = {
        "completed": "✅", "in_progress": "🔄", "pending": "⏳",
        "failed": "❌", "blocked": "🚫", "hitl": "👤", "skipped": "⏭️",
    }
    lines = ["graph LR"]
    for t in tasks:
        icon = icons.get(t["status"], "⏳")
        name = t["task_name"][:28].replace('"', "'")
        lines.append(f'  {t["task_id"]}["{icon} {t["task_id"]}<br/>{name}"]')
        if t["status"] == "completed":
            lines.append(
                f'  style {t["task_id"]} fill:#90EE90,stroke:#228B22,color:#000'
            )
        elif t["status"] == "in_progress":
            lines.append(
                f'  style {t["task_id"]} fill:#FFD700,stroke:#B8860B,color:#000'
            )
        elif t["status"] in ("failed", "hitl"):
            lines.append(
                f'  style {t["task_id"]} fill:#FFB6C1,stroke:#DC143C,color:#000'
            )
    name_to_id = {t["task_name"]: t["task_id"] for t in tasks}
    for t in tasks:
        for dep in (t.get("dependencies") or []):
            lines.append(f"  {name_to_id.get(dep, dep)} --> {t['task_id']}")
    return {
        "message": f"Sprint DAG for {sprint['sprint_id']}.",
        "diagram_type": "dag",
        "sprint_id": sprint["sprint_id"],
        "mermaid": "```mermaid\n" + "\n".join(lines) + "\n```",
        "task_count": len(tasks),
    }


def _diagram_files(project: str) -> dict:
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT fr.file_path, fr.task_id, fr.action, ts.task_name "
            "FROM file_registry fr LEFT JOIN task_state ts ON fr.task_id=ts.task_id "
            "WHERE fr.project_id=? ORDER BY fr.file_path", (project,)
        ).fetchall()
    if not rows:
        return {
            "error": "No files registered yet.",
            "hint": "Files register as tasks complete via enki_complete.",
        }
    lines = ["graph LR"]
    seen: set[str] = set()
    for row in rows:
        tid = row["task_id"]
        fname = row["file_path"].split("/")[-1]
        safe = row["file_path"].replace("/", "_").replace(".", "_").replace("-", "_")
        act = "✏️" if row["action"] == "modified" else "🆕"
        if tid not in seen:
            lines.append(f'  {tid}["{tid}<br/>{(row["task_name"] or "")[:20]}"]')
            seen.add(tid)
        lines.append(f'  {tid} -->|"{act}"| {safe}["{fname}"]')
    return {
        "message": f"File map for {project}.",
        "diagram_type": "files",
        "mermaid": "```mermaid\n" + "\n".join(lines) + "\n```",
        "file_count": len(rows),
    }


def _diagram_pipeline(project: str) -> dict:
    active = _get_active_goal(project)
    if not active:
        return {"error": "No active goal."}
    current = (active.get("phase") or "planning").lower()
    phases = ["planning", "spec", "approved", "implement", "validating", "complete"]
    lines = ["graph LR"]
    for p in phases:
        if p == current:
            lines.append(f'  {p}["🎯 {p.upper()}"]')
            lines.append(
                f"  style {p} fill:#FFD700,stroke:#B8860B,color:#000,font-weight:bold"
            )
        elif phases.index(p) < phases.index(current):
            lines.append(f'  {p}["✅ {p}"]')
            lines.append(f"  style {p} fill:#90EE90,stroke:#228B22,color:#000")
        else:
            lines.append(f'  {p}["⏳ {p}"]')
    for i in range(len(phases) - 1):
        lines.append(f"  {phases[i]} --> {phases[i + 1]}")
    return {
        "message": f"Pipeline: {project} at {current}.",
        "diagram_type": "pipeline",
        "current_phase": current,
        "mermaid": "```mermaid\n" + "\n".join(lines) + "\n```",
    }


def _diagram_codebase(project: str) -> dict:
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT fr.file_path FROM file_registry fr "
            "WHERE fr.project_id=? ORDER BY fr.file_path", (project,)
        ).fetchall()
    if not rows:
        return {
            "error": "No files registered yet.",
            "hint": "Files populate as tasks complete.",
        }
    from collections import defaultdict
    dirs: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        parts = row["file_path"].split("/")
        dirs[parts[0] if len(parts) > 1 else "root"].append(parts[-1])
    lines = ["graph TD"]
    for dname, files in sorted(dirs.items()):
        safe_d = dname.replace("-", "_").replace(".", "_")
        lines.append(f'  {safe_d}["{dname}/"]')
        for fname in files[:8]:
            safe_f = (dname + "_" + fname).replace(".", "_").replace("-", "_")
            lines.append(f'  {safe_d} --> {safe_f}["{fname}"]')
        if len(files) > 8:
            lines.append(f'  {safe_d} --> more_{safe_d}["...+{len(files)-8} more"]')
    return {
        "message": f"Codebase: {project} ({len(rows)} files).",
        "diagram_type": "codebase",
        "mermaid": "```mermaid\n" + "\n".join(lines) + "\n```",
        "total_files": len(rows),
        "directories": sorted(dirs.keys()),
    }


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


def _count_completed_tasks(project: str, sprint_id: str) -> int:
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM task_state WHERE sprint_id = ? AND status = 'completed'",
                (sprint_id,),
            ).fetchone()
        return int(row["c"] if row else 0)
    except Exception:
        return 0


def _should_fire_checkpoint(project: str, sprint_id: str) -> bool:
    """Check if a Reviewer checkpoint should fire after this wave."""
    try:
        interval = int(_read_project_state_loose(project, "reviewer_checkpoint_interval") or "0")
        if interval == 0:
            return False
        total = int(_read_project_state_loose(project, "sprint_total_tasks") or "0")
        completed = _count_completed_tasks(project, sprint_id)
        if completed == 0:
            return False
        remaining = total - completed
        if remaining < (interval // 2):
            return False
        return completed % interval == 0
    except Exception:
        return False


def _read_project_state_loose(project: str, key: str) -> str | None:
    """Read project_state key even if key is not in strict STATE_KEYS."""
    try:
        return read_project_state(project, key)
    except Exception:
        try:
            with em_db(project) as conn:
                row = conn.execute(
                    "SELECT value FROM project_state WHERE key = ? LIMIT 1",
                    (key,),
                ).fetchone()
            return row["value"] if row else None
        except Exception:
            return None


def _get_sprint_modified_files(project: str, sprint_id: str) -> list[str]:
    """Get all files modified across the sprint from task assignments."""
    try:
        with em_db(project) as conn:
            rows = conn.execute(
                "SELECT assigned_files FROM task_state WHERE sprint_id = ? AND status = 'completed'",
                (sprint_id,),
            ).fetchall()
        files: list[str] = []
        for row in rows:
            if row["assigned_files"]:
                try:
                    task_files = json.loads(row["assigned_files"])
                    files.extend(task_files)
                except Exception:
                    pass
        return list(dict.fromkeys(files))
    except Exception:
        return []


def _get_spec_final_path(project: str) -> Path | None:
    project_path = _get_project_path(project)
    if not project_path:
        return None
    p = Path(project_path) / "docs" / "spec-final.md"
    return p if p.exists() else None


def _get_impl_spec_path(project: str) -> Path | None:
    artifacts_dir = _goal_artifacts_dir(project)
    candidates = list(artifacts_dir.glob("spawn-architect-impl-spec*.md"))
    return sorted(candidates)[-1] if candidates else None


def _build_codebase_context(project: str, assigned_files: list[str]) -> str | None:
    """Build a codebase context block for assigned files from graph.db."""
    from enki.db import graph_db, graph_db_path

    if not graph_db_path(project).exists():
        return None

    lines = ["## Codebase Context (from knowledge graph)"]
    try:
        with graph_db(project) as conn:
            for file_path in assigned_files[:5]:
                importers = conn.execute(
                    "SELECT COUNT(*) as c FROM edges "
                    "WHERE to_id=? AND edge_type='imports'",
                    (file_path,),
                ).fetchone()["c"]

                blast = conn.execute(
                    "SELECT MAX(blast_score) as s, MAX(risk_level) as r "
                    "FROM blast_radius WHERE file_path=?",
                    (file_path,),
                ).fetchone()

                complexity = conn.execute(
                    "SELECT MAX(complexity) as c FROM symbols WHERE file_path=?",
                    (file_path,),
                ).fetchone()["c"]

                file_lines = [f"\n**{file_path}**"]
                if importers > 0:
                    file_lines.append(f"  - Imported by {importers} file(s)")
                if blast and blast["s"] and blast["s"] > 0.1:
                    risk = (blast["r"] or "low").upper()
                    file_lines.append(
                        "  - Blast radius: "
                        f"{risk} — changes ripple widely"
                        if blast["s"] > 0.5
                        else f"  - Blast radius: {risk}"
                    )
                if complexity and complexity > 15:
                    file_lines.append(
                        "  - Max symbol complexity: "
                        f"{complexity} (above threshold — consider splitting)"
                    )

                dupe = conn.execute(
                    "SELECT to_id FROM edges "
                    "WHERE from_id=? AND edge_type='duplicates' LIMIT 1",
                    (file_path,),
                ).fetchone()
                if dupe:
                    file_lines.append(
                        f"  - Similar code exists in: {dupe['to_id']} — "
                        "consider extracting shared logic"
                    )

                lines.extend(file_lines)
    except Exception:
        return None

    if len(lines) <= 1:
        return None
    return "\n".join(lines)


def _load_validate_state(project: str, sprint_id: str) -> dict | None:
    try:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT validate_state FROM sprint_state WHERE sprint_id=?",
                (sprint_id,),
            ).fetchone()
            if row and row["validate_state"]:
                return json.loads(row["validate_state"])
    except Exception:
        pass
    return None


def _save_validate_state(project: str, sprint_id: str, state: dict) -> None:
    try:
        with em_db(project) as conn:
            conn.execute(
                "UPDATE sprint_state SET validate_state=? WHERE sprint_id=?",
                (json.dumps(state), sprint_id),
            )
    except Exception:
        pass


def _bugs_has_column(project: str, col: str) -> bool:
    try:
        with em_db(project) as conn:
            rows = conn.execute("PRAGMA table_info(bugs)").fetchall()
            return any(r["name"] == col for r in rows)
    except Exception:
        return False


def _get_draft_bugs(project: str) -> list[dict]:
    try:
        with em_db(project) as conn:
            if _bugs_has_column(project, "severity"):
                rows = conn.execute(
                    "SELECT * FROM bugs WHERE status='open' AND severity='draft'"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bugs WHERE status='open' AND priority='draft'"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _get_bugs_by_severity(project: str, severities: list[str]) -> list[dict]:
    if not severities:
        return []
    placeholders = ",".join("?" for _ in severities)
    try:
        with em_db(project) as conn:
            if _bugs_has_column(project, "severity"):
                rows = conn.execute(
                    f"SELECT * FROM bugs WHERE status='open' AND severity IN ({placeholders})",
                    severities,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM bugs WHERE status='open' AND priority IN ({placeholders})",
                    severities,
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _get_bugs_needing_revalidation(project: str) -> list[dict]:
    try:
        with em_db(project) as conn:
            rows = conn.execute(
                "SELECT * FROM bugs WHERE reporter_revalidation_required=1 AND status='open'"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _mark_bug_needs_revalidation(project: str, bug_id: str, fix_summary: str) -> None:
    try:
        with em_db(project) as conn:
            if _bugs_has_column(project, "fix_summary"):
                conn.execute(
                    "UPDATE bugs SET reporter_revalidation_required=1, "
                    "revalidation_cycle=revalidation_cycle+1, fix_summary=? WHERE id=?",
                    (fix_summary, bug_id),
                )
            else:
                conn.execute(
                    "UPDATE bugs SET reporter_revalidation_required=1, "
                    "revalidation_cycle=revalidation_cycle+1 WHERE id=?",
                    (bug_id,),
                )
    except Exception:
        pass


def _increment_revalidation_cycle(project: str, bug_id: str) -> None:
    try:
        with em_db(project) as conn:
            conn.execute(
                "UPDATE bugs SET revalidation_cycle=revalidation_cycle+1 WHERE id=?",
                (bug_id,),
            )
    except Exception:
        pass


def _close_bug(project: str, bug_id: str) -> None:
    try:
        with em_db(project) as conn:
            conn.execute(
                "UPDATE bugs SET status='closed', reporter_revalidation_required=0 WHERE id=?",
                (bug_id,),
            )
    except Exception:
        pass


def _update_bug_priority(project: str, bug_id: str, priority: str) -> None:
    resolved = resolve_bug_identifier(project, bug_id)
    internal_id = resolved[0] if resolved else bug_id
    try:
        with em_db(project) as conn:
            if _bugs_has_column(project, "severity"):
                conn.execute(
                    "UPDATE bugs SET severity=?, priority=? WHERE id=?",
                    (priority, priority, internal_id),
                )
            else:
                conn.execute(
                    "UPDATE bugs SET priority=? WHERE id=?",
                    (priority, internal_id),
                )
    except Exception:
        pass


def _escalate_bug(project: str, bug_id: str) -> None:
    try:
        with em_db(project) as conn:
            conn.execute("UPDATE bugs SET status='escalated' WHERE id=?", (bug_id,))
    except Exception:
        pass


def _file_bug(
    project: str,
    title: str,
    description: str,
    severity: str,
    filed_by: str,
    reporter: str,
    affected_files: list[str] | None = None,
) -> str | None:
    affected_files = affected_files or []
    try:
        bug_id = file_bug(
            project=project,
            title=title,
            description=description,
            filed_by=filed_by,
            priority=severity,
        )
        try:
            with em_db(project) as conn:
                if _bugs_has_column(project, "reporter"):
                    conn.execute("UPDATE bugs SET reporter=? WHERE id=?", (reporter, bug_id))
                if _bugs_has_column(project, "affected_files"):
                    conn.execute(
                        "UPDATE bugs SET affected_files=? WHERE id=?",
                        (json.dumps(affected_files), bug_id),
                    )
        except Exception:
            pass
        return bug_id
    except Exception:
        return None


def _bug_reporter(bug: dict) -> str:
    reporter = bug.get("reporter") or bug.get("filed_by") or "infosec"
    return str(reporter)


def _bug_affected_files(bug: dict) -> list[str]:
    files = bug.get("affected_files", [])
    if isinstance(files, str):
        try:
            decoded = json.loads(files)
            if isinstance(decoded, list):
                return [str(x) for x in decoded]
        except Exception:
            return []
    if isinstance(files, list):
        return [str(x) for x in files]
    return []


def _generate_sprint_summary(project: str, sprint_id: str) -> dict:
    try:
        with em_db(project) as conn:
            tasks = conn.execute(
                "SELECT status, COUNT(*) as c FROM task_state "
                "WHERE sprint_id=? GROUP BY status",
                (sprint_id,),
            ).fetchall()
            task_counts = {r["status"]: r["c"] for r in tasks}
            bugs = conn.execute(
                "SELECT priority, status, COUNT(*) as c FROM bugs "
                "WHERE project_id=? GROUP BY priority, status",
                (project,),
            ).fetchall()
            bug_summary = {f"{r['priority']}_{r['status']}": r["c"] for r in bugs}
        return {
            "sprint_id": sprint_id,
            "tasks": task_counts,
            "bugs": bug_summary,
            "total_tasks": sum(task_counts.values()),
            "completed_tasks": task_counts.get("completed", 0),
            "failed_tasks": task_counts.get("failed", 0),
        }
    except Exception:
        return {"sprint_id": sprint_id, "error": "summary generation failed"}


def _merge_all_worktrees(project: str, project_path: str) -> dict:
    _ = project
    results = {}
    worktrees_dir = Path(project_path) / ".worktrees"
    if not worktrees_dir.exists():
        return {"message": "No worktrees directory found"}
    for wt in worktrees_dir.iterdir():
        if wt.is_dir():
            try:
                r = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt)],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                results[wt.name] = "removed" if r.returncode == 0 else (r.stderr or r.stdout)[:200]
            except Exception as e:
                results[wt.name] = str(e)
    return results


def _merge_sprint_to_main(project: str, project_path: str) -> dict:
    try:
        sprint = get_active_sprint(project)
        if not sprint:
            return {"message": "No active sprint"}
        sprint_branch = _get_sprint_base_branch(project, sprint["sprint_id"])
        if not sprint_branch or sprint_branch in ("main", "master"):
            return {"message": "Sprint already on main/master"}
        checkout = subprocess.run(
            ["git", "checkout", "main"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if checkout.returncode != 0:
            return {"merged": False, "output": (checkout.stderr or checkout.stdout)[:200]}
        r = subprocess.run(
            ["git", "merge", sprint_branch, "--no-ff", "-m", f"Merge {sprint_branch} into main"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "merged": r.returncode == 0,
            "branch": sprint_branch,
            "output": (r.stdout if r.returncode == 0 else r.stderr)[:200],
        }
    except Exception as e:
        return {"error": str(e)}


def _merge_sprint_branch(project: str, sprint_id: str) -> dict:
    project_path = _get_project_path(project)
    if not project_path:
        return {"merged": False, "warning": "project path not registered"}
    _ = sprint_id
    return _merge_sprint_to_main(project, project_path)


def _git_push_main(project_path: str) -> dict:
    try:
        r = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").lower()
            if "no configured push destination" in err or "no such remote" in err:
                return {"pushed": False, "warning": (r.stderr or r.stdout)[:200]}
        return {
            "pushed": r.returncode == 0,
            "output": (r.stdout if r.returncode == 0 else r.stderr)[:200],
        }
    except Exception as e:
        return {"warning": str(e)}


def _detect_required_docs(project: str, project_path: str) -> list[str]:
    docs = [
        "README.md",
        "CLAUDE.md",
        "docs/HANDOVER.md",
        "docs/ARCHITECTURE.md",
        "docs/SECURITY.md",
        "docs/FEATURES.md",
        "docs/TESTING.md",
        "docs/CONTRIBUTING.md",
    ]
    try:
        with em_db(project) as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM pm_decisions").fetchone()["c"]
            if count > 0:
                docs.append("docs/ADR/")
    except Exception:
        pass

    if not project_path:
        return docs

    try:
        all_files = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [
                d for d in dirs if d not in {
                    "node_modules", ".git", "__pycache__", ".venv",
                    "dist", "build", ".worktrees",
                }
            ]
            for f in files:
                all_files.append(os.path.join(root, f))

        file_str = " ".join(all_files).lower()
        tech_stack_raw = read_project_state(project, "tech_stack") or "{}"
        try:
            tech_stack = json.loads(tech_stack_raw)
        except Exception:
            tech_stack = {}
        combined = file_str + " " + str(tech_stack).lower()

        if any(x in combined for x in [
            "fastapi", "express", "nestjs", "django", "flask",
            "fastify", "routes", "endpoints", "swagger",
        ]):
            docs.append("docs/API.md")
        if any(x in combined for x in [
            "llm", "openai", "anthropic", "prompt", "embedding",
            "agent", "langchain",
        ]):
            docs.append("docs/AGENTS.md")
        ui_files = [f for f in all_files if f.endswith((".tsx", ".jsx", ".vue", ".svelte"))]
        if len(ui_files) > 3:
            docs.append("docs/COMPONENTS.md")
        if any(x in combined for x in [
            "click", "argparse", "commander", "yargs", "cli.py", "bin/", "#!/usr/bin",
        ]):
            docs.append("docs/CLI.md")
        if any(x in file_str for x in [
            "dockerfile", "docker-compose", "kubernetes", ".yaml", "terraform", "heroku", "vercel",
        ]):
            docs.append("docs/OPERATIONS.md")
            docs.append("docs/DEPLOYMENT.md")
        db_files = [f for f in all_files if any(x in f.lower() for x in ["schema", "migration", "model", "entity", ".sql"])]
        if len(db_files) > 3:
            docs.append("docs/DATA_MODEL.md")
        if len(all_files) > 50:
            docs.append("docs/TROUBLESHOOTING.md")
        try:
            with em_db(project) as conn:
                sprint_count = conn.execute("SELECT COUNT(*) as c FROM sprint_state").fetchone()["c"]
                if sprint_count > 1:
                    docs.append("CHANGELOG.md")
        except Exception:
            pass
    except Exception:
        pass
    return list(dict.fromkeys(docs))


def _ensure_config_template() -> None:
    """Create ~/.enki/config.json template if missing."""
    config_path = ENKI_ROOT / "config.json"
    if not config_path.exists():
        template = {
            "openrouter_api_key": "YOUR_OPENROUTER_API_KEY",
            "codex_review_model": "openai/gpt-4o",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        }
        try:
            config_path.write_text(json.dumps(template, indent=2))
        except Exception:
            pass


def _openrouter_configured() -> bool:
    """Check if OpenRouter API key is configured."""
    _ensure_config_template()
    config_path = ENKI_ROOT / "config.json"
    if not config_path.exists():
        return False
    try:
        config = json.loads(config_path.read_text())
        key = config.get("openrouter_api_key", "")
        return bool(key and key != "YOUR_OPENROUTER_API_KEY")
    except Exception:
        return False


def _read_text_safe(path_obj: Path | None) -> str:
    if not path_obj:
        return ""
    try:
        return path_obj.read_text()
    except Exception:
        return ""


def _spawn_codex_reviewer(
    project: str,
    spec_content: str,
    impl_spec_content: str,
    modified_files: list[str],
    project_path: str,
) -> dict | None:
    """Run Codex (GPT-4o) code review via OpenRouter."""
    try:
        from enki.integrations.openrouter import call_openrouter, normalize_review_output
    except Exception:
        return None

    if not _openrouter_configured():
        return None

    codex_prompt_path = ENKI_ROOT / "prompts" / "codex-reviewer.md"
    if codex_prompt_path.exists():
        try:
            system_prompt = codex_prompt_path.read_text()
        except Exception:
            system_prompt = ""
    else:
        system_prompt = ""
    if not system_prompt:
        system_prompt = (
            "You are an expert code reviewer. Review the provided codebase "
            "against the spec. Output JSON matching the reviewer sprint-review schema."
        )

    file_contents = []
    for fp in modified_files[:20]:
        full_path = Path(project_path) / fp
        try:
            if full_path.exists() and full_path.stat().st_size < 50000:
                content = full_path.read_text()[:3000]
                file_contents.append(f"### {fp}\n```\n{content}\n```")
        except Exception:
            pass

    user_message = (
        f"## Product Spec\n{spec_content[:5000]}\n\n"
        f"## Implementation Spec\n{impl_spec_content[:3000]}\n\n"
        f"## Modified Files\n{'\n\n'.join(file_contents)}"
    )

    result = call_openrouter(
        system_prompt=system_prompt,
        user_message=user_message,
        timeout=180,
    )
    if result.get("error"):
        return {
            "mode": "sprint-review",
            "status": "failed",
            "summary": f"Codex review failed: {result['error']}",
            "spec_alignment_issues": [],
            "architectural_issues": [],
            "quality_violations": [],
            "approved": True,
            "notes": "OpenRouter call failed — Codex review skipped",
        }

    normalized = normalize_review_output(result.get("content", ""))
    normalized["_model"] = result.get("model", "openai/gpt-4o")
    return normalized


def _propose_specialist_panel(project: str, impl_spec: dict) -> dict:
    """Analyse impl spec and propose a specialist panel with reasoning."""
    tasks = impl_spec.get("tasks", [])
    all_files = [f for t in tasks for f in (t.get("files") or [])]
    all_text = " ".join(
        (t.get("description", "") + " " + t.get("name", ""))
        for t in tasks
    ).lower()
    tier = read_project_state(project, "tier") or "standard"

    proposed: list[dict] = []
    not_proposed: list[dict] = []

    extensions = {Path(f).suffix.lower() for f in all_files}
    ts_files = bool({".ts", ".tsx"} & extensions)
    py_files = bool({".py"} & extensions)

    if ts_files:
        proposed.append({
            "role": "typescript-dev-reviewer",
            "reason": (
                f"{sum(1 for f in all_files if f.endswith(('.ts', '.tsx')))} TypeScript files. "
                "Will check type safety strategy, generic patterns, strict mode compliance."
            ),
        })
        proposed.append({
            "role": "typescript-qa-reviewer",
            "reason": "TypeScript services need Vitest patterns, async testing, proper mocking.",
        })
    else:
        not_proposed.append({"role": "typescript-dev-reviewer", "reason": "No TypeScript files"})
        not_proposed.append({"role": "typescript-qa-reviewer", "reason": "No TypeScript files"})

    if py_files:
        proposed.append({
            "role": "python-dev-reviewer",
            "reason": (
                f"{sum(1 for f in all_files if f.endswith('.py'))} Python files. "
                "Will check type hints, Pydantic patterns, async patterns."
            ),
        })
        proposed.append({
            "role": "python-qa-reviewer",
            "reason": "Python services need pytest patterns, fixture design, mock boundaries.",
        })
    else:
        not_proposed.append({"role": "python-dev-reviewer", "reason": "No Python files"})
        not_proposed.append({"role": "python-qa-reviewer", "reason": "No Python files"})

    if tier in ("standard", "full"):
        proposed.append({
            "role": "infosec",
            "reason": "Always included for Standard/Full tier — code-level vulnerability review.",
        })
        proposed.append({
            "role": "reviewer",
            "reason": "Always included — SOLID/DRY/coupling review at spec level.",
        })

    auth_keywords = {"auth", "jwt", "token", "session", "password", "oauth",
                     "permission", "rbac", "role", "encrypt"}
    if auth_keywords & set(all_text.split()):
        proposed.append({
            "role": "security-auditor",
            "reason": "Auth/security patterns detected in task descriptions. Threat modeling required.",
        })
    else:
        not_proposed.append({"role": "security-auditor", "reason": "No auth/security patterns detected"})

    ai_keywords = {"llm", "embedding", "rag", "model", "inference", "prompt",
                   "agent", "vector", "openai", "anthropic", "completion"}
    if ai_keywords & set(all_text.split()):
        proposed.append({
            "role": "ai-engineer",
            "reason": "AI/LLM integration detected. Evaluation strategy and prompt design review needed.",
        })
    else:
        not_proposed.append({"role": "ai-engineer", "reason": "No AI/LLM integration detected"})

    perf_keywords = {"performance", "latency", "throughput", "cache", "optimization",
                     "concurrent", "async", "queue", "batch", "index"}
    if perf_keywords & set(all_text.split()):
        proposed.append({
            "role": "performance",
            "reason": "Performance-sensitive operations detected.",
        })
    else:
        not_proposed.append({"role": "performance", "reason": "No performance-critical paths detected"})

    return {"proposed": proposed, "not_proposed": not_proposed}


def _load_impl_spec(project: str, goal_id: str) -> dict | None:
    """Load Architect impl spec from artifact storage."""
    _ = goal_id
    artifacts_dir = _goal_artifacts_dir(project)
    impl_spec_artifacts = list(artifacts_dir.glob("spawn-architect-impl-spec*.md"))
    if not impl_spec_artifacts:
        return None

    artifact = sorted(impl_spec_artifacts)[-1]
    try:
        content = artifact.read_text()
        match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except Exception:
        return None
    return None


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


def _inject_architect_context(project: str, role: str, context: dict) -> dict:
    """Inject spec and codebase profile for Architect role."""
    if role != "architect":
        return context
    enriched = dict(context)

    # Inject spec content
    project_path = _get_project_path(project)
    if project_path:
        for spec_name in ("spec-final.md", "spec-draft.md"):
            spec_path = Path(project_path) / "docs" / spec_name
            if spec_path.exists():
                try:
                    enriched["spec_content"] = spec_path.read_text()
                    enriched["spec_path"] = str(spec_path)
                    break
                except Exception:
                    pass

    # Inject most recent researcher profile if exists
    artifacts_dir = _goal_artifacts_dir(project)
    if artifacts_dir.exists():
        researcher_files = sorted(
            artifacts_dir.glob("spawn-researcher-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if researcher_files:
            try:
                enriched["codebase_profile"] = researcher_files[0].read_text()
                enriched["codebase_profile_path"] = str(researcher_files[0])
            except Exception:
                pass

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
            igi_completed = _has_agent_status(goal_id, "igi", "completed") or _has_any_scoped_agent_status(
                goal_id, "igi", "completed"
            )
            if not igi_completed:
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
        architect_completed = _has_agent_status(goal_id, "architect", "completed") or _has_any_scoped_agent_status(
            goal_id, "architect", "completed"
        )
        if not architect_completed:
            return "Architect agent completed"
        if not _has_hitl_approval(project, "architect"):
            return "HITL approval record for stage 'architect'"
    elif target == "validating":
        sprint = get_active_sprint(project)
        if sprint:
            sprint_id = sprint["sprint_id"]
            # Check sprint close pipeline ran
            if not _has_agent_status(goal_id, f"sprint_close:{sprint_id}", "completed"):
                # Check if enki_sprint_close was called and InfoSec ran
                infosec_done = _has_agent_status(
                    goal_id, f"infosec:{sprint_id}-infosec-audit", "completed"
                )
                if not infosec_done:
                    return (
                        "Sprint close pipeline not complete. "
                        "Call enki_sprint_close() and complete all steps "
                        "(InfoSec + sprint Reviewer) before advancing to validating."
                    )
            # Check no open P0 bugs across sprint
            with em_db(project) as conn:
                open_p0 = conn.execute(
                    "SELECT COUNT(*) as c FROM bugs "
                    "WHERE project_id=? AND status='open' AND (severity='P0' OR priority='P0')",
                    (project,),
                ).fetchone()["c"]
            if open_p0 > 0:
                return (
                    f"{open_p0} open P0 bug(s) must be resolved or marked "
                    "accepted-risk before advancing to validating."
                )
        if not _all_wave_tasks_completed(project):
            return "all waves completed"
    elif target == "complete":
        if current == "closing":
            return None
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
        "closing": "Run enki_project_close(), optionally enki_document(), then HITL accept and advance to complete.",
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


def _get_task_phase(project: str, task_id: str) -> str:
    """Get current task_phase for a task. Defaults to test_design."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT task_phase FROM task_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if not row or not row["task_phase"]:
        return "test_design"
    return row["task_phase"]


def _advance_task_phase(project: str, task_id: str, new_phase: str) -> bool:
    """Advance task to new_phase. Returns True if advanced, False if invalid."""
    if new_phase not in TASK_PHASES:
        return False
    with em_db(project) as conn:
        conn.execute(
            "UPDATE task_state SET task_phase = ? WHERE task_id = ?",
            (new_phase, task_id),
        )
    return True


def _has_agent_completed(goal_id: str, role_key: str) -> bool:
    """Check if an agent has completed for a goal."""
    return _has_agent_status(goal_id, role_key, "completed")


def _auto_file_concerns(
    project: str,
    task_id: str,
    role: str,
    concerns: list[dict],
    goal_id: str,
) -> list[str]:
    """Auto-file bugs from agent concerns array. Returns list of bug IDs filed."""
    _ = goal_id
    if not concerns:
        return []

    route_map = {
        "infosec": "architect",
        "validator": "dev",
        "reviewer": "dev",
        "qa": "dev",
        "performance": "dev",
    }
    assigned_to = route_map.get(role, "dev")

    severity_map = {
        "infosec": "P0",
        "validator": "P1",
        "reviewer": "P1",
        "qa": "P1",
        "performance": "P2",
    }
    default_severity = severity_map.get(role, "P2")

    filed_ids: list[str] = []
    for concern in concerns:
        if not isinstance(concern, dict):
            continue
        content = concern.get("content", str(concern))
        severity = concern.get("severity", default_severity)
        if role == "reviewer" and "P2" in content:
            severity = "P2"

        try:
            result = enki_bug(
                action="file",
                title=f"[{role.upper()}] {content[:100]}",
                description=content,
                severity=severity,
                filed_by=role,
                task_id=task_id,
                project=project,
            )
            if result.get("bug_id"):
                filed_ids.append(result["bug_id"])

            # Ensure assignment target is set as routing policy
            if assigned_to and result.get("bug_id"):
                resolved = resolve_bug_identifier(project, result["bug_id"])
                if resolved:
                    internal_id, _human_id = resolved
                    with em_db(project) as conn:
                        conn.execute(
                            "UPDATE bugs SET assigned_to = ? WHERE id = ?",
                            (assigned_to, internal_id),
                        )

            if role == "infosec" and result.get("bug_id"):
                _mail_security_escalation(
                    project, task_id, result["bug_id"], content
                )
        except Exception:
            pass

    return filed_ids


def _mail_security_escalation(
    project: str,
    task_id: str,
    bug_id: str,
    description: str,
) -> None:
    """File a mail thread routing InfoSec bug to Architect via EM."""
    try:
        thread_id = create_thread(project, "security_review")
        body = (
            f"Bug {bug_id} filed for task {task_id}.\n\n"
            f"Finding: {description}\n\n"
            "Requires Architect scope triage:\n"
            "  Scope A: Implementation fix only → assign to Dev\n"
            "  Scope B: Design change required → escalate to HITL\n"
            "  Scope C: Accepted risk → document in pm_decisions\n\n"
            "Spawn Architect with this bug context to determine scope."
        )
        send(
            project=project,
            thread_id=thread_id,
            from_agent="INFOSEC",
            to_agent="EM",
            subject=f"Security finding requires Architect scope decision: {bug_id}",
            body=body,
            importance="high",
            task_id=task_id,
        )
    except Exception:
        pass


def _get_open_bugs(project: str, task_id: str, severity: str | None = None) -> list[dict]:
    """Get open bugs for a task, optionally filtered by severity."""
    with em_db(project) as conn:
        if severity:
            rows = conn.execute(
                "SELECT * FROM bugs WHERE task_id = ? AND status = 'open' "
                "AND priority = ?",
                (task_id, severity),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bugs WHERE task_id = ? AND status = 'open'",
                (task_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def _has_any_scoped_agent_status(goal_id: str, role_key: str, status: str) -> bool:
    with uru_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM agent_status WHERE goal_id = ? AND status = ? "
            "AND agent_role LIKE ? LIMIT 1",
            (goal_id, status, f"{role_key}:%"),
        ).fetchone()
    return row is not None


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
    if _has_agent_status(goal_id, "validator", "completed") or _has_any_scoped_agent_status(
        goal_id, "validator", "completed"
    ):
        return True
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT 1 FROM pm_decisions WHERE project_id = ? "
            "AND decision_type IN ('validator_signoff', 'validation_signoff') "
            "AND COALESCE(human_response, 'approved') = 'approved' LIMIT 1",
            (project,),
        ).fetchone()
    return row is not None


def _is_tmux_session_alive(session_id: str) -> bool:
    """Check if a tmux session is still running."""
    if not session_id:
        return False
    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", session_id],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _recover_dead_session_tasks(project: str) -> int:
    """Reset in_progress tasks whose session is dead. Returns count reset."""
    reset = 0
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT task_id, session_id FROM task_state "
            "WHERE status = 'in_progress' AND session_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            if not _is_tmux_session_alive(row["session_id"]):
                conn.execute(
                    "UPDATE task_state SET status='pending', session_id=NULL, "
                    "started_at=NULL WHERE task_id=?",
                    (row["task_id"],)
                )
                reset += 1
    return reset


def _get_sprint_base_branch(project: str, sprint_id: str) -> str:
    """Get sprint base branch from project state. Falls back to current branch."""
    try:
        stored = read_project_state(project, f"sprint_base_{sprint_id}")
    except Exception:
        with em_db(project) as conn:
            row = conn.execute(
                "SELECT value FROM project_state WHERE key = ? LIMIT 1",
                (f"sprint_base_{sprint_id}",),
            ).fetchone()
        stored = row["value"] if row else None
    if stored:
        return stored
    project_path = _get_project_path(project)
    if project_path:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        branch = r.stdout.strip()
        if branch and branch not in {"main", "master"}:
            return branch
    return "main"


def _process_merge_queue(project: str) -> list[dict]:
    """Process pending merges FIFO into sprint base. Returns results."""
    project_path = _get_project_path(project)
    if not project_path:
        return []
    results = []
    with em_db(project) as conn:
        queued = conn.execute(
            "SELECT id, task_id, branch_name, worktree_path, sprint_branch "
            "FROM merge_queue WHERE project_id=? AND status='queued' "
            "ORDER BY queued_at ASC",
            (project,)
        ).fetchall()
    for item in queued:
        try:
            subprocess.run(
                ["git", "checkout", item["sprint_branch"]],
                capture_output=True, text=True, cwd=project_path, timeout=30,
            )
            r = subprocess.run(
                ["git", "merge", "--no-ff", item["branch_name"],
                 "-m", f"Merge task {item['task_id']}"],
                capture_output=True, text=True, timeout=60, cwd=project_path,
            )
            if r.returncode == 0:
                with em_db(project) as conn:
                    conn.execute(
                        "UPDATE merge_queue SET status='merged', "
                        "merged_at=datetime('now') WHERE id=?",
                        (item["id"],)
                    )
                worktree = item["worktree_path"]
                if worktree and Path(worktree).exists():
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", worktree],
                        capture_output=True, timeout=30, cwd=project_path,
                    )
                    subprocess.run(
                        ["git", "branch", "-d", item["branch_name"]],
                        capture_output=True, timeout=30, cwd=project_path,
                    )
                results.append({"task_id": item["task_id"], "status": "merged"})
            else:
                conflict = (r.stdout + r.stderr)[:2000]
                with em_db(project) as conn:
                    conn.execute(
                        "UPDATE merge_queue SET status='conflict', "
                        "conflict_files=? WHERE id=?",
                        (conflict, item["id"])
                    )
                    conn.execute(
                        "UPDATE task_state SET status='pending', session_id=NULL, "
                        "worktree_path=NULL, started_at=NULL, agent_outputs=? "
                        "WHERE task_id=?",
                        (f"Merge conflict: {conflict[:500]}", item["task_id"])
                    )
                subprocess.run(
                    ["git", "merge", "--abort"],
                    capture_output=True, timeout=30, cwd=project_path,
                )
                results.append({"task_id": item["task_id"], "status": "conflict"})
        except Exception as e:
            results.append({"task_id": item["task_id"], "status": "error", "error": str(e)})
    return results


def _create_task_worktree(
    project_path: str,
    task_id: str,
    branch_name: str,
    sprint_branch: str,
) -> str | None:
    """Create git worktree for a task branched from sprint base. Returns path or None."""
    worktree_path = str(Path(project_path) / ".worktrees" / task_id)
    try:
        r = subprocess.run(
            ["git", "worktree", "add", worktree_path,
             "-b", branch_name, sprint_branch],
            capture_output=True, text=True, timeout=30, cwd=project_path,
        )
        if r.returncode == 0:
            return worktree_path
        r2 = subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            capture_output=True, text=True, timeout=30, cwd=project_path,
        )
        return worktree_path if r2.returncode == 0 else None
    except Exception:
        return None


def _goal_artifacts_dir(project: str) -> Path:
    path = ENKI_ROOT / "artifacts" / normalize_project_name(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_current_session_id() -> str | None:
    """Return current session id from env or ENKI_ROOT marker file."""
    session_id = (os.environ.get("ENKI_SESSION_ID") or "").strip()
    if session_id:
        return session_id
    sid_file = ENKI_ROOT / "current_session_id"
    if sid_file.exists():
        try:
            sid = sid_file.read_text().strip()
            return sid or None
        except Exception:
            return None
    return None


def _resolve_prompt_path(role: str) -> Path:
    prompt_path = ENKI_ROOT / "prompts" / f"{role}.md"
    if prompt_path.exists():
        return prompt_path
    alias_role = PROMPT_ROLE_ALIASES.get(role)
    if alias_role:
        alias_path = ENKI_ROOT / "prompts" / f"{alias_role}.md"
        if alias_path.exists():
            return alias_path
    return prompt_path


def _load_authored_prompt(role: str) -> str:
    prompt_path = _resolve_prompt_path(role)
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
                    "AND (agent_role LIKE 'dev:%' OR agent_role LIKE 'qa:%')",
                    (goal_id,),
                ).fetchone()
            if row and int(row["c"] or 0) > 0:
                return f"Wave {active_wave} in progress"
            return f"Wave {active_wave} ready — agents not yet running"
    except Exception:
        pass
    return f"Wave {active_wave} status unknown"


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


def _count_staged_candidates(project: str) -> int:
    """Count wrap candidates still staged for Gemini review."""
    try:
        with abzu_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM note_candidates "
                "WHERE status='staged' AND project=?",
                (project,),
            ).fetchone()
            return int(row["c"] or 0) if row else 0
    except Exception:
        return 0


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
