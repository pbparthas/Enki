"""Orchestrator module for Enki.

Handles task execution, bug tracking, and HITL escalation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
import json
import re

from .db import get_db
from .session import get_phase, set_phase, ensure_project_enki_dir
from .pm import (
    TaskGraph, Task, load_task_graph, save_task_graph,
    is_spec_approved, get_orchestration_status,
)


# Agent definitions with their roles and allowed tools
AGENTS = {
    "Architect": {
        "role": "Design before implementation",
        "tier": "CRITICAL",
        "tools": ["Read", "Glob", "Grep", "Write"],
        "writes_to": ["docs/", "specs/"],
    },
    "QA": {
        "role": "Write tests FIRST (TDD), execute tests",
        "tier": "CRITICAL",
        "tools": ["Read", "Write", "Bash"],
        "writes_to": ["tests/"],
    },
    "Validator-Tests": {
        "role": "Verify QA tests match spec",
        "tier": "CRITICAL",
        "tools": ["Read", "Grep"],
        "writes_to": [],
    },
    "Dev": {
        "role": "Implement to pass tests (SOLID)",
        "tier": "CRITICAL",
        "tools": ["Read", "Edit", "Write"],
        "writes_to": ["src/", "lib/"],
    },
    "Validator-Code": {
        "role": "Verify implementation correctness",
        "tier": "CRITICAL",
        "tools": ["Read", "Grep", "Bash"],
        "writes_to": [],
    },
    "Reviewer": {
        "role": "Code review via Prism",
        "tier": "STANDARD",
        "tools": ["Skill"],
        "skill": "/review",
    },
    "DBA": {
        "role": "Database changes",
        "tier": "CONDITIONAL",
        "tools": ["Read", "Write", "Bash"],
        "writes_to": ["migrations/", "sql/"],
    },
    "Security": {
        "role": "Security review",
        "tier": "STANDARD",
        "tools": ["Skill"],
        "skill": "/security-review",
    },
    "Docs": {
        "role": "Documentation updates",
        "tier": "STANDARD",
        "tools": ["Read", "Write"],
        "writes_to": ["docs/", "README"],
    },
}


@dataclass
class Bug:
    """A bug found during orchestration."""
    id: str
    title: str
    description: str
    found_by: str  # Agent that found it: QA, Validator-Code, Reviewer
    assigned_to: str = "Dev"  # Usually Dev
    severity: str = "medium"  # critical, high, medium, low
    status: str = "open"  # open, fixing, verifying, closed, wontfix, hitl
    cycle: int = 0
    max_cycles: int = 3
    related_task: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolution: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "found_by": self.found_by,
            "assigned_to": self.assigned_to,
            "severity": self.severity,
            "status": self.status,
            "cycle": self.cycle,
            "max_cycles": self.max_cycles,
            "related_task": self.related_task,
            "created_at": self.created_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Bug':
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            found_by=data["found_by"],
            assigned_to=data.get("assigned_to", "Dev"),
            severity=data.get("severity", "medium"),
            status=data.get("status", "open"),
            cycle=data.get("cycle", 0),
            max_cycles=data.get("max_cycles", 3),
            related_task=data.get("related_task"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            resolution=data.get("resolution"),
        )


@dataclass
class Orchestration:
    """Active orchestration state."""
    id: str
    spec_name: str
    spec_path: str
    status: str = "active"  # active, paused, completed, failed
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    task_graph: TaskGraph = None
    bugs: dict = field(default_factory=dict)  # bug_id -> Bug
    blackboard: dict = field(default_factory=dict)  # agent -> output
    current_wave: int = 1
    hitl_required: bool = False
    hitl_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "spec_name": self.spec_name,
            "spec_path": self.spec_path,
            "status": self.status,
            "started_at": self.started_at,
            "task_graph": self.task_graph.to_dict() if self.task_graph else None,
            "bugs": {bid: b.to_dict() for bid, b in self.bugs.items()},
            "blackboard": self.blackboard,
            "current_wave": self.current_wave,
            "hitl_required": self.hitl_required,
            "hitl_reason": self.hitl_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Orchestration':
        orch = cls(
            id=data["id"],
            spec_name=data["spec_name"],
            spec_path=data["spec_path"],
            status=data.get("status", "active"),
            started_at=data.get("started_at", datetime.now().isoformat()),
            current_wave=data.get("current_wave", 1),
            hitl_required=data.get("hitl_required", False),
            hitl_reason=data.get("hitl_reason"),
        )

        if data.get("task_graph"):
            orch.task_graph = TaskGraph.from_dict(data["task_graph"])

        for bid, bdata in data.get("bugs", {}).items():
            orch.bugs[bid] = Bug.from_dict(bdata)

        orch.blackboard = data.get("blackboard", {})

        return orch


import uuid


def generate_orchestration_id() -> str:
    """Generate unique orchestration ID."""
    return f"orch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def generate_bug_id() -> str:
    """Generate unique bug ID."""
    return f"BUG-{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"


def start_orchestration(
    spec_name: str,
    task_graph: TaskGraph,
    project_path: Path = None,
) -> Orchestration:
    """Start a new orchestration.

    Args:
        spec_name: Name of the approved spec
        task_graph: Task graph to execute
        project_path: Project directory path

    Returns:
        New Orchestration instance
    """
    project_path = project_path or Path.cwd()

    if not is_spec_approved(spec_name, project_path):
        raise ValueError(f"Spec not approved: {spec_name}")

    orch = Orchestration(
        id=generate_orchestration_id(),
        spec_name=spec_name,
        spec_path=task_graph.spec_path,
        task_graph=task_graph,
    )

    save_orchestration(orch, project_path)
    log_to_running(f"ORCHESTRATION STARTED: {spec_name}", project_path)

    return orch


def save_orchestration(orch: Orchestration, project_path: Path = None):
    """Save orchestration state to STATE.md.

    Args:
        orch: Orchestration to save
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    state_path = project_path / ".enki" / "STATE.md"

    # Build STATE.md content
    content = [
        f"# Enki Orchestration - {orch.spec_name}",
        "",
        f"**Status**: {orch.status}",
        f"**Started**: {orch.started_at}",
        f"**Spec**: {orch.spec_path}",
        f"**Current Wave**: {orch.current_wave}",
        "",
    ]

    if orch.hitl_required:
        content.extend([
            "## ⚠️ HUMAN INTERVENTION REQUIRED",
            "",
            f"**Reason**: {orch.hitl_reason}",
            "",
            "Please review and resolve before continuing.",
            "",
        ])

    # Task Graph
    content.append("## Task Graph")
    content.append("")

    if orch.task_graph:
        waves = orch.task_graph.get_waves()
        for i, wave in enumerate(waves, 1):
            content.append(f"### Wave {i}")
            for task in wave:
                status_marker = {
                    "pending": "[ ]",
                    "active": "[ ] ← ACTIVE",
                    "complete": "[x]",
                    "failed": "[!] FAILED",
                    "blocked": "[-] blocked",
                }.get(task.status, "[ ]")

                content.append(f"- {status_marker} {task.id}: {task.description} ({task.agent})")
            content.append("")

    # Active Bugs
    open_bugs = [b for b in orch.bugs.values() if b.status not in ("closed", "wontfix")]
    if open_bugs:
        content.append("## Active Bugs")
        content.append("")
        content.append("| ID | Title | Severity | Status | Cycle |")
        content.append("|----|-------|----------|--------|-------|")
        for bug in open_bugs:
            content.append(f"| {bug.id} | {bug.title} | {bug.severity} | {bug.status} | {bug.cycle}/{bug.max_cycles} |")
        content.append("")

    # Files in Scope
    if orch.task_graph:
        all_files = set()
        for task in orch.task_graph.tasks.values():
            all_files.update(task.files_in_scope)

        if all_files:
            content.append("## Files in Scope")
            for f in sorted(all_files):
                content.append(f"- {f}")
            content.append("")

    # Blackboard
    if orch.blackboard:
        content.append("## Blackboard (Agent Outputs)")
        content.append("")
        content.append("| Agent | Status | Key Output |")
        content.append("|-------|--------|------------|")
        for agent, output in orch.blackboard.items():
            status = "complete"
            summary = str(output)[:50] + "..." if len(str(output)) > 50 else str(output)
            content.append(f"| {agent} | {status} | {summary} |")
        content.append("")

    # Add JSON state for programmatic access
    content.append("<!-- ENKI_ORCHESTRATION")
    content.append(json.dumps(orch.to_dict(), indent=2))
    content.append("-->")

    state_path.write_text("\n".join(content))


def load_orchestration(project_path: Path = None) -> Optional[Orchestration]:
    """Load orchestration state from STATE.md.

    Args:
        project_path: Project directory path

    Returns:
        Orchestration or None if not found
    """
    project_path = project_path or Path.cwd()
    state_path = project_path / ".enki" / "STATE.md"

    if not state_path.exists():
        return None

    content = state_path.read_text()

    # Extract JSON state
    match = re.search(r'<!-- ENKI_ORCHESTRATION\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        # Try legacy format
        match = re.search(r'<!-- ENKI_STATE\n(.*?)\n-->', content, re.DOTALL)
        if match:
            # Convert from legacy TaskGraph format
            data = json.loads(match.group(1))
            return Orchestration(
                id=generate_orchestration_id(),
                spec_name=data.get("spec_name", "unknown"),
                spec_path=data.get("spec_path", ""),
                task_graph=TaskGraph.from_dict(data),
            )
        return None

    try:
        data = json.loads(match.group(1))
        return Orchestration.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def log_to_running(message: str, project_path: Path = None):
    """Log a message to RUNNING.md.

    Args:
        message: Message to log
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()
    running_path = project_path / ".enki" / "RUNNING.md"

    timestamp = datetime.now().strftime("%H:%M")
    with open(running_path, "a") as f:
        f.write(f"\n[{timestamp}] {message}\n")


# === Task Execution ===

def start_task(
    task_id: str,
    project_path: Path = None,
) -> Task:
    """Mark a task as active and begin execution.

    Args:
        task_id: Task ID to start
        project_path: Project directory path

    Returns:
        The started task
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]

    # Check if task is ready (dependencies complete)
    ready_ids = [t.id for t in orch.task_graph.get_ready_tasks()]
    if task_id not in ready_ids and task.status == "pending":
        raise ValueError(f"Task not ready: {task_id} (dependencies not complete)")

    task.status = "active"
    save_orchestration(orch, project_path)
    log_to_running(f"TASK STARTED: {task_id} ({task.agent})", project_path)

    return task


def complete_task(
    task_id: str,
    output: str = None,
    project_path: Path = None,
) -> Task:
    """Mark a task as complete.

    Args:
        task_id: Task ID to complete
        output: Task output/result
        project_path: Project directory path

    Returns:
        The completed task
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    task.status = "complete"
    task.output = output

    # Add to blackboard
    orch.blackboard[f"{task.agent}:{task_id}"] = output or "completed"

    # Check if wave is complete
    current_wave_tasks = [t for t in orch.task_graph.tasks.values() if t.wave == orch.current_wave]
    if all(t.status == "complete" for t in current_wave_tasks):
        orch.current_wave += 1

    # Check if orchestration is complete
    if all(t.status == "complete" for t in orch.task_graph.tasks.values()):
        orch.status = "completed"
        log_to_running(f"ORCHESTRATION COMPLETED: {orch.spec_name}", project_path)

    save_orchestration(orch, project_path)
    log_to_running(f"TASK COMPLETED: {task_id}", project_path)

    return task


def fail_task(
    task_id: str,
    reason: str = None,
    project_path: Path = None,
) -> Task:
    """Mark a task as failed (with retry logic).

    Args:
        task_id: Task ID that failed
        reason: Failure reason
        project_path: Project directory path

    Returns:
        The failed task
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    task.attempts += 1

    if task.attempts >= task.max_attempts:
        task.status = "failed"
        orch.hitl_required = True
        orch.hitl_reason = f"Task {task_id} failed after {task.attempts} attempts: {reason}"
        log_to_running(f"HITL REQUIRED: Task {task_id} exceeded max attempts", project_path)
    else:
        task.status = "pending"  # Retry
        log_to_running(f"TASK RETRY: {task_id} (attempt {task.attempts}/{task.max_attempts})", project_path)

    save_orchestration(orch, project_path)

    return task


# === Bug Management ===

def file_bug(
    title: str,
    description: str,
    found_by: str,
    severity: str = "medium",
    related_task: str = None,
    project_path: Path = None,
) -> Bug:
    """File a new bug.

    Args:
        title: Bug title
        description: Bug description
        found_by: Agent that found the bug
        severity: Bug severity
        related_task: Related task ID
        project_path: Project directory path

    Returns:
        The created bug
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    bug = Bug(
        id=generate_bug_id(),
        title=title,
        description=description,
        found_by=found_by,
        severity=severity,
        related_task=related_task,
    )

    orch.bugs[bug.id] = bug
    save_orchestration(orch, project_path)
    log_to_running(f"BUG FILED: {bug.id} - {title} ({severity})", project_path)

    # Also log to database
    db = get_db()
    if db:
        try:
            db.execute("""
                INSERT INTO violations (gate, tool, file_path, reason, was_overridden, created_at)
                VALUES ('bug', ?, ?, ?, 0, CURRENT_TIMESTAMP)
            """, (found_by, related_task or "", f"Bug: {title}"))
            db.commit()
        except Exception:
            pass  # Non-critical

    return bug


def assign_bug(
    bug_id: str,
    assigned_to: str = "Dev",
    project_path: Path = None,
) -> Bug:
    """Assign a bug to an agent.

    Args:
        bug_id: Bug ID
        assigned_to: Agent to assign to
        project_path: Project directory path

    Returns:
        The updated bug
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if bug_id not in orch.bugs:
        raise ValueError(f"Bug not found: {bug_id}")

    bug = orch.bugs[bug_id]
    bug.assigned_to = assigned_to
    bug.status = "fixing"

    save_orchestration(orch, project_path)
    log_to_running(f"BUG ASSIGNED: {bug_id} -> {assigned_to}", project_path)

    return bug


def start_bug_verification(
    bug_id: str,
    project_path: Path = None,
) -> Bug:
    """Move bug to verification status.

    Args:
        bug_id: Bug ID
        project_path: Project directory path

    Returns:
        The updated bug
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if bug_id not in orch.bugs:
        raise ValueError(f"Bug not found: {bug_id}")

    bug = orch.bugs[bug_id]
    bug.status = "verifying"
    bug.cycle += 1

    save_orchestration(orch, project_path)
    log_to_running(f"BUG VERIFICATION: {bug_id} (cycle {bug.cycle})", project_path)

    return bug


def close_bug(
    bug_id: str,
    resolution: str = "fixed",
    project_path: Path = None,
) -> Bug:
    """Close a bug.

    Args:
        bug_id: Bug ID
        resolution: Resolution type (fixed, wontfix)
        project_path: Project directory path

    Returns:
        The closed bug
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if bug_id not in orch.bugs:
        raise ValueError(f"Bug not found: {bug_id}")

    bug = orch.bugs[bug_id]
    bug.status = "closed" if resolution == "fixed" else "wontfix"
    bug.resolution = resolution

    save_orchestration(orch, project_path)
    log_to_running(f"BUG CLOSED: {bug_id} ({resolution})", project_path)

    return bug


def reopen_bug(
    bug_id: str,
    project_path: Path = None,
) -> Bug:
    """Reopen a bug (verification failed).

    Args:
        bug_id: Bug ID
        project_path: Project directory path

    Returns:
        The reopened bug
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if bug_id not in orch.bugs:
        raise ValueError(f"Bug not found: {bug_id}")

    bug = orch.bugs[bug_id]

    if bug.cycle >= bug.max_cycles:
        bug.status = "hitl"
        orch.hitl_required = True
        orch.hitl_reason = f"Bug {bug_id} exceeded {bug.max_cycles} fix cycles"
        log_to_running(f"HITL REQUIRED: Bug {bug_id} exceeded max cycles", project_path)
    else:
        bug.status = "fixing"
        log_to_running(f"BUG REOPENED: {bug_id} (cycle {bug.cycle}/{bug.max_cycles})", project_path)

    save_orchestration(orch, project_path)

    return bug


def get_open_bugs(project_path: Path = None) -> list:
    """Get all open bugs.

    Args:
        project_path: Project directory path

    Returns:
        List of open bugs
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return []

    return [b for b in orch.bugs.values() if b.status not in ("closed", "wontfix")]


# === HITL Escalation ===

def escalate_to_hitl(
    reason: str,
    project_path: Path = None,
):
    """Escalate to human-in-the-loop.

    Args:
        reason: Reason for escalation
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    orch.hitl_required = True
    orch.hitl_reason = reason
    orch.status = "paused"

    save_orchestration(orch, project_path)
    log_to_running(f"HITL ESCALATION: {reason}", project_path)


def resolve_hitl(
    resolution: str,
    project_path: Path = None,
):
    """Resolve HITL escalation.

    Args:
        resolution: How it was resolved
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    orch.hitl_required = False
    orch.hitl_reason = None
    orch.status = "active"

    save_orchestration(orch, project_path)
    log_to_running(f"HITL RESOLVED: {resolution}", project_path)


def check_hitl_required(project_path: Path = None) -> tuple:
    """Check if HITL is required.

    Args:
        project_path: Project directory path

    Returns:
        Tuple of (required: bool, reason: str)
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return False, None

    return orch.hitl_required, orch.hitl_reason


# === Status and Reporting ===

def get_full_orchestration_status(project_path: Path = None) -> dict:
    """Get complete orchestration status.

    Args:
        project_path: Project directory path

    Returns:
        Complete status dictionary
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return {
            "active": False,
            "spec": None,
        }

    total_tasks = len(orch.task_graph.tasks) if orch.task_graph else 0
    completed_tasks = len([t for t in orch.task_graph.tasks.values() if t.status == "complete"]) if orch.task_graph else 0
    failed_tasks = len([t for t in orch.task_graph.tasks.values() if t.status == "failed"]) if orch.task_graph else 0

    open_bugs = [b for b in orch.bugs.values() if b.status not in ("closed", "wontfix")]
    critical_bugs = [b for b in open_bugs if b.severity == "critical"]

    ready_tasks = [t.id for t in orch.task_graph.get_ready_tasks()] if orch.task_graph else []

    return {
        "active": True,
        "orchestration_id": orch.id,
        "spec": orch.spec_name,
        "spec_path": orch.spec_path,
        "status": orch.status,
        "current_wave": orch.current_wave,
        "started_at": orch.started_at,

        "tasks": {
            "total": total_tasks,
            "completed": completed_tasks,
            "failed": failed_tasks,
            "progress": completed_tasks / total_tasks if total_tasks > 0 else 0.0,
            "ready": ready_tasks,
        },

        "bugs": {
            "total": len(orch.bugs),
            "open": len(open_bugs),
            "critical": len(critical_bugs),
        },

        "hitl": {
            "required": orch.hitl_required,
            "reason": orch.hitl_reason,
        },

        "blackboard": orch.blackboard,
    }


def get_next_action(project_path: Path = None) -> dict:
    """Get the recommended next action.

    Args:
        project_path: Project directory path

    Returns:
        Dict with action type and details
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return {"action": "no_orchestration", "message": "No active orchestration"}

    # Check for HITL
    if orch.hitl_required:
        return {
            "action": "hitl_required",
            "message": f"Human intervention required: {orch.hitl_reason}",
        }

    # Check for critical bugs
    critical_bugs = [b for b in orch.bugs.values() if b.severity == "critical" and b.status == "open"]
    if critical_bugs:
        bug = critical_bugs[0]
        return {
            "action": "fix_bug",
            "bug_id": bug.id,
            "message": f"Fix critical bug: {bug.title}",
        }

    # Check for bugs in fixing status
    fixing_bugs = [b for b in orch.bugs.values() if b.status == "fixing"]
    if fixing_bugs:
        bug = fixing_bugs[0]
        return {
            "action": "verify_bug",
            "bug_id": bug.id,
            "message": f"Verify bug fix: {bug.title}",
        }

    # Check for ready tasks
    if orch.task_graph:
        ready_tasks = orch.task_graph.get_ready_tasks()
        if ready_tasks:
            task = ready_tasks[0]
            return {
                "action": "run_task",
                "task_id": task.id,
                "agent": task.agent,
                "message": f"Run task: {task.description} ({task.agent})",
            }

    # Check if complete
    if orch.status == "completed":
        return {
            "action": "completed",
            "message": f"Orchestration complete: {orch.spec_name}",
        }

    return {
        "action": "blocked",
        "message": "No tasks ready - check dependencies",
    }
