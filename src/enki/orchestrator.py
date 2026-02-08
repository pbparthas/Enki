"""Orchestrator module for Enki.

Handles task execution, bug tracking, and HITL escalation.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
import json
import re

logger = logging.getLogger(__name__)

from .db import get_db
from .session import get_phase, set_phase, ensure_project_enki_dir
from .pm import (
    TaskGraph, Task, load_task_graph, save_task_graph,
    is_spec_approved, get_orchestration_status,
)
from .skills import get_skill_for_agent, enhance_agent_prompt_with_skill

# P2-09: Agent config separated from orchestration logic
from .agents_config import AGENTS, WORKER_VALIDATORS


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
    validation_commands: list = field(default_factory=list)  # Gate 5: commands to run before completion

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
            "validation_commands": self.validation_commands,
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
            validation_commands=data.get("validation_commands", []),
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


# === Gate 4.5: Validation Enforcement (Hardening Spec v2, Step 5 / GAP-03) ===


def check_gate_4_5_validation(
    task_id: str,
    project_path: Path = None,
) -> tuple[bool, str]:
    """Gate 4.5: Validation enforcement on task completion.

    Before a task can be marked complete, its validators must have
    passed. Workers with mapped validators cannot bypass validation.

    No active orchestration = pass.
    No validators for agent type = pass.
    Validators passed = pass.
    Otherwise = block.

    Args:
        task_id: Task to check
        project_path: Project directory path

    Returns:
        (allowed, reason) tuple
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if orch is None:
        return True, "No active orchestration"

    if not orch.task_graph or task_id not in orch.task_graph.tasks:
        return False, f"Task not found: {task_id}"

    task = orch.task_graph.tasks[task_id]

    # Check if this agent type has validators
    validators = WORKER_VALIDATORS.get(task.agent, [])
    if not validators:
        return True, f"No validators for agent type '{task.agent}'"

    # Task has validators — check if they've passed
    if task.validation_status == "passed":
        return True, "Validators passed"

    # Validators required but haven't passed
    return False, (
        f"GATE 4.5: Validation Required\n\n"
        f"Task {task_id} (agent: {task.agent}) requires validation by: {', '.join(validators)}\n"
        f"Current validation_status: {task.validation_status}\n\n"
        f"Task must be submitted for validation and approved before completion."
    )


# === Gate 5: Orchestration Completion (Hardening Spec v2, Step 6 / GAP-05) ===


def check_gate_5_completion(
    project_path: Path = None,
) -> tuple[bool, str]:
    """Gate 5: Run validation_commands before orchestration completion.

    All configured validation_commands must exit with code 0.
    Fail-closed: commands configured but can't execute = block.
    No validation_commands = pass (backwards-compatible).

    Args:
        project_path: Project directory path

    Returns:
        (allowed, reason) tuple
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if orch is None:
        return True, "No active orchestration"

    if not orch.validation_commands:
        return True, "No validation_commands configured"

    failures = []
    for cmd in orch.validation_commands:
        if not isinstance(cmd, str) or not cmd.strip():
            failures.append(f"Invalid command: {cmd!r}")
            continue

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(project_path),
                timeout=300,  # 5 minute timeout per command
            )
            if result.returncode != 0:
                stderr_preview = (result.stderr or "").strip()[:200]
                failures.append(
                    f"Command failed (exit {result.returncode}): {cmd}\n"
                    f"  stderr: {stderr_preview}"
                )
        except subprocess.TimeoutExpired:
            failures.append(f"Command timed out (300s): {cmd}")
        except Exception as e:
            failures.append(f"Command error: {cmd} — {e}")

    if failures:
        return False, (
            f"GATE 5: Validation Commands Failed\n\n"
            + "\n".join(f"- {f}" for f in failures)
            + "\n\nAll validation_commands must pass before orchestration completion."
        )

    return True, "All validation_commands passed"


def complete_orchestration(
    project_path: Path = None,
) -> Orchestration:
    """Complete an orchestration after Gate 5 validation.

    Gate 5: Runs validation_commands, requires all pass.
    No bypass flag. Only path: pass checks or HITL override.

    Args:
        project_path: Project directory path

    Returns:
        The completed orchestration

    Raises:
        ValueError: If Gate 5 fails or orchestration not ready
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    # Check all tasks are complete
    if orch.task_graph:
        incomplete = [
            t for t in orch.task_graph.tasks.values()
            if t.status != "complete"
        ]
        if incomplete:
            raise ValueError(
                f"Cannot complete orchestration: {len(incomplete)} tasks not complete: "
                + ", ".join(t.id for t in incomplete[:5])
            )

    # Gate 5: Run validation commands
    allowed, reason = check_gate_5_completion(project_path)
    if not allowed:
        raise ValueError(reason)

    orch.status = "completed"
    save_orchestration(orch, project_path)
    log_to_running(f"ORCHESTRATION COMPLETED (Gate 5 passed): {orch.spec_name}", project_path)

    return orch


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
    skip_validation: bool = False,
) -> Task:
    """Mark a task as complete (or submit for validation).

    For worker tasks that need validation, this submits them
    for validation instead of marking complete directly.

    Gate 4.5 (Hardening Spec v2): skip_validation only works for
    agent types with no mapped validators. Workers with validators
    MUST go through the validation flow.

    Args:
        task_id: Task ID to complete
        output: Task output/result
        project_path: Project directory path
        skip_validation: If True, skip validation (only for validator/non-validated tasks)

    Returns:
        The task (in validating or complete state)
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    task.output = output

    # Gate 4.5: Enforce validation for tasks with mapped validators
    if skip_validation and needs_validation(task):
        # Gate 4.5: Cannot skip validation for tasks with validators
        allowed, reason = check_gate_4_5_validation(task_id, project_path)
        if not allowed:
            raise ValueError(
                f"GATE 4.5: Cannot skip validation for agent '{task.agent}'. "
                f"Validators required: {', '.join(WORKER_VALIDATORS.get(task.agent, []))}. "
                f"Use submit_for_validation() instead."
            )
        # If gate passes (validators already passed), allow completion

    # Check if this task needs validation
    if not skip_validation and needs_validation(task):
        # Don't complete yet - submit for validation
        return submit_for_validation(task_id, output, project_path)

    # No validation needed (or validators already passed) - mark complete directly
    task.status = "complete"
    task.validation_status = "passed"  # Implicitly passed

    # Add to blackboard
    orch.blackboard[f"{task.agent}:{task_id}"] = output or "completed"

    # Check if wave is complete
    current_wave_tasks = [t for t in orch.task_graph.tasks.values() if t.wave == orch.current_wave]
    if all(t.status == "complete" for t in current_wave_tasks):
        orch.current_wave += 1

    # Check if orchestration is complete — Gate 5 validates
    if all(t.status == "complete" for t in orch.task_graph.tasks.values()):
        allowed, reason = check_gate_5_completion(project_path)
        if allowed:
            orch.status = "completed"
            log_to_running(f"ORCHESTRATION COMPLETED (Gate 5 passed): {orch.spec_name}", project_path)
        else:
            log_to_running(f"GATE 5 BLOCKED COMPLETION: {reason[:100]}", project_path)

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
                INSERT INTO violations (gate, tool, file_path, reason)
                VALUES ('bug', ?, ?, ?)
            """, (found_by, related_task or "", f"Bug: {title}"))
            db.commit()
        except Exception as e:
            logger.warning("Non-fatal error in orchestrator (bug db log): %s", e)
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

# Escalation evidence validation (Hardening Spec v2, Step 3 / GAP-07)
MIN_ATTEMPTS = 3
MIN_DESCRIPTION_LEN = 20
MIN_RESULT_LEN = 20
MIN_WHY_FAILED_LEN = 30
MIN_HYPOTHESIS_LEN = 30
MIN_RESOLUTION_OPTIONS = 2


def validate_escalation_evidence(evidence: dict) -> tuple[bool, str]:
    """Validate structured escalation evidence before allowing HITL.

    Requires 3+ meaningfully distinct attempts, a hypothesis, and
    resolution options. No empty fields. No lazy escalation.

    Args:
        evidence: Dict with attempts, hypothesis, resolution_options

    Returns:
        (valid, reason) — True if valid, False with explanation if not
    """
    if not isinstance(evidence, dict):
        return False, "Evidence must be a dict"

    # Check attempts
    attempts = evidence.get("attempts")
    if not isinstance(attempts, list) or len(attempts) < MIN_ATTEMPTS:
        return False, f"Minimum {MIN_ATTEMPTS} distinct attempts required, got {len(attempts) if isinstance(attempts, list) else 0}"

    seen_descriptions = set()
    for i, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            return False, f"Attempt {i+1} must be a dict"

        desc = attempt.get("description", "")
        result = attempt.get("result", "")
        why_failed = attempt.get("why_failed", "")

        if not isinstance(desc, str) or len(desc.strip()) < MIN_DESCRIPTION_LEN:
            return False, f"Attempt {i+1}: description must be >= {MIN_DESCRIPTION_LEN} chars, got {len(desc.strip()) if isinstance(desc, str) else 0}"

        if not isinstance(result, str) or len(result.strip()) < MIN_RESULT_LEN:
            return False, f"Attempt {i+1}: result must be >= {MIN_RESULT_LEN} chars, got {len(result.strip()) if isinstance(result, str) else 0}"

        if not isinstance(why_failed, str) or len(why_failed.strip()) < MIN_WHY_FAILED_LEN:
            return False, f"Attempt {i+1}: why_failed must be >= {MIN_WHY_FAILED_LEN} chars, got {len(why_failed.strip()) if isinstance(why_failed, str) else 0}"

        # Check distinctness — descriptions must not be identical
        normalized = desc.strip().lower()
        if normalized in seen_descriptions:
            return False, f"Attempt {i+1}: description is identical to a previous attempt. Attempts must be meaningfully distinct."
        seen_descriptions.add(normalized)

    # Check hypothesis
    hypothesis = evidence.get("hypothesis", "")
    if not isinstance(hypothesis, str) or len(hypothesis.strip()) < MIN_HYPOTHESIS_LEN:
        return False, f"Hypothesis must be >= {MIN_HYPOTHESIS_LEN} chars, got {len(hypothesis.strip()) if isinstance(hypothesis, str) else 0}"

    # Check resolution options
    options = evidence.get("resolution_options")
    if not isinstance(options, list) or len(options) < MIN_RESOLUTION_OPTIONS:
        return False, f"Minimum {MIN_RESOLUTION_OPTIONS} resolution options required, got {len(options) if isinstance(options, list) else 0}"

    for i, opt in enumerate(options):
        if not isinstance(opt, str) or not opt.strip():
            return False, f"Resolution option {i+1} must be a non-empty string"

    return True, "Evidence validated"


def escalate_to_hitl(
    reason: str,
    project_path: Path = None,
    evidence: dict = None,
):
    """Escalate to human-in-the-loop. Requires structured evidence.

    Hardening Spec v2, GAP-07: Escalation requires 3+ attempted
    approaches with structured evidence before HITL is accepted.

    Args:
        reason: Reason for escalation
        project_path: Project directory path
        evidence: Structured escalation evidence (required)

    Raises:
        ValueError: If evidence is missing or invalid
    """
    project_path = project_path or Path.cwd()

    # Gate: require evidence
    if evidence is None:
        raise ValueError(
            "ESCALATION EVIDENCE REQUIRED: Cannot escalate without structured evidence. "
            "Provide attempts (min 3), hypothesis, and resolution_options."
        )

    valid, msg = validate_escalation_evidence(evidence)
    if not valid:
        raise ValueError(f"ESCALATION EVIDENCE INVALID: {msg}")

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


# === Validation Flow ===

def needs_validation(task: Task) -> bool:
    """Check if task needs validation before completion."""
    return task.agent in WORKER_VALIDATORS and len(WORKER_VALIDATORS[task.agent]) > 0


def get_validators_for_task(task: Task) -> list[str]:
    """Get the validator agents for a task."""
    return WORKER_VALIDATORS.get(task.agent, [])


def get_next_validator(task: Task) -> Optional[str]:
    """Get the next validator that needs to run.

    For two-stage validation, returns first validator that hasn't passed.
    """
    validators = get_validators_for_task(task)
    if not validators:
        return None

    # For simplicity, run validators in order
    # First validator is spec compliance, second is code quality
    # In full implementation, track each validator's result separately
    return validators[0]


def submit_for_validation(
    task_id: str,
    output: str,
    project_path: Path = None,
) -> Task:
    """Submit a task for validation (called by worker when done).

    Does NOT mark task complete. Marks it as awaiting validation.

    Args:
        task_id: Task ID
        output: Worker's output
        project_path: Project directory path

    Returns:
        The task in validation state
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    task.output = output
    task.status = "validating"
    task.validation_status = "pending"

    save_orchestration(orch, project_path)
    log_to_running(f"TASK SUBMITTED FOR VALIDATION: {task_id}", project_path)

    return task


def get_validation_prompt(
    task: Task,
    validator_agent: str,
    orch: 'Orchestration',
    project_path: Path = None,
) -> str:
    """Generate BLIND validation prompt.

    CRITICAL: Validator sees ONLY:
    - The spec requirements
    - The actual files (code/tests) to read themselves
    - What they're validating against

    Validator does NOT see:
    - Worker's reasoning or thought process
    - Worker's output text or claims
    - How the worker approached the problem

    This prevents validators from being biased by worker explanations.

    Args:
        task: Task being validated
        validator_agent: Which validator (Validator-Tests, Validator-Code)
        orch: Current orchestration
        project_path: Project directory path

    Returns:
        Prompt string for validator
    """
    project_path = project_path or Path.cwd()

    # Read spec
    spec_content = ""
    if orch.spec_path:
        spec_path = project_path / orch.spec_path
        if spec_path.exists():
            spec_content = spec_path.read_text()

    prompt_parts = [
        f"# Validation Task",
        f"",
        f"You are **{validator_agent}**.",
        f"",
        f"## CRITICAL: Blind Validation Rules",
        f"",
        f"1. You must READ THE ACTUAL FILES yourself",
        f"2. Do NOT trust any descriptions or claims about what was done",
        f"3. Form your own independent judgment",
        f"4. Your job is to VERIFY, not to trust",
        f"",
    ]

    # Spec reference (this is allowed - it's the source of truth)
    if spec_content:
        prompt_parts.extend([
            "## Spec Requirements (Source of Truth)",
            "",
            "```",
            spec_content[:3000],
            "```",
            "",
        ])

    # Files to validate - validator must READ these themselves
    if task.files_in_scope:
        prompt_parts.extend([
            "## Files to Validate",
            "",
            "You MUST read these files yourself and verify their contents:",
            "",
        ])
        for f in task.files_in_scope:
            prompt_parts.append(f"- `{f}`")
        prompt_parts.append("")

    # Validator-specific instructions
    if validator_agent == "Validator-Tests":
        prompt_parts.extend([
            "## Your Task: Validate Test Coverage",
            "",
            "### Step 1: Read the test files",
            "Use the Read tool to examine each test file in scope.",
            "",
            "### Step 2: Check against spec",
            "For EACH requirement in the spec, verify:",
            "- [ ] Is there a test for this requirement?",
            "- [ ] Does the test actually test the right thing?",
            "- [ ] Are edge cases covered?",
            "",
            "### Step 3: Check test quality",
            "- [ ] Are tests runnable (proper syntax, imports)?",
            "- [ ] Do test names describe what they test?",
            "- [ ] Are assertions meaningful (not just `assert True`)?",
            "",
            "### Step 4: Deliver verdict",
            "",
            "You MUST end your response with exactly one of:",
            "",
            "```",
            "VERDICT: PASS",
            "```",
            "",
            "OR",
            "",
            "```",
            "VERDICT: FAIL",
            "ISSUES:",
            "- [specific issue 1]",
            "- [specific issue 2]",
            "```",
            "",
            "Be SPECIFIC about failures. Vague feedback wastes cycles.",
            "",
        ])
    elif validator_agent == "Validator-Code":
        prompt_parts.extend([
            "## Your Task: Validate Implementation",
            "",
            "### Step 1: Run the tests",
            "```bash",
            "pytest  # or appropriate test command",
            "```",
            "",
            "### Step 2: Read the implementation",
            "Use the Read tool to examine each implementation file.",
            "",
            "### Step 3: Verify correctness",
            "- [ ] Do ALL tests pass?",
            "- [ ] Does code match spec requirements?",
            "- [ ] Any obvious bugs or logic errors?",
            "- [ ] Does code handle edge cases?",
            "",
            "### Step 4: Deliver verdict",
            "",
            "You MUST end your response with exactly one of:",
            "",
            "```",
            "VERDICT: PASS",
            "```",
            "",
            "OR",
            "",
            "```",
            "VERDICT: FAIL",
            "ISSUES:",
            "- [specific issue 1]",
            "- [specific issue 2]",
            "```",
            "",
            "Be SPECIFIC about failures. Include test output if tests fail.",
            "",
        ])

    prompt_parts.extend([
        "## Reminders",
        "",
        "- You are validating INDEPENDENTLY - read files yourself",
        "- Do NOT suggest improvements - only PASS or FAIL on requirements",
        "- Do NOT be lenient - if requirements aren't met, FAIL",
        "- Your verdict will be parsed, so use the exact format above",
        "",
    ])

    return "\n".join(prompt_parts)


def spawn_validators(
    task_id: str,
    project_path: Path = None,
) -> list[dict]:
    """Spawn validator agents for a task.

    Args:
        task_id: Task ID to validate
        project_path: Project directory path

    Returns:
        List of Task tool call parameters for validators
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    validators = get_validators_for_task(task)

    if not validators:
        return []

    spawn_calls = []
    for validator in validators:
        prompt = get_validation_prompt(task, validator, orch, project_path)

        spawn_calls.append({
            "task_id": f"{task_id}_validate_{validator}",
            "original_task_id": task_id,
            "validator": validator,
            "params": {
                "description": f"{validator}: Validate {task_id}",
                "prompt": prompt,
                "subagent_type": "Explore",  # Read-only validation
            },
        })

    log_to_running(f"VALIDATORS SPAWNED: {validators} for {task_id}", project_path)

    return spawn_calls


def parse_validation_verdict(output: str) -> tuple[bool, Optional[str]]:
    """Parse validator output to extract verdict.

    Args:
        output: Validator's full output

    Returns:
        (passed, feedback) tuple
    """
    output_upper = output.upper()

    # Look for explicit verdict
    if "VERDICT: PASS" in output_upper:
        return True, None

    if "VERDICT: FAIL" in output_upper:
        # Extract issues after FAIL
        fail_idx = output_upper.find("VERDICT: FAIL")
        feedback = output[fail_idx:].strip()
        return False, feedback

    # Fallback heuristics
    if "PASS" in output_upper and "FAIL" not in output_upper:
        return True, None

    if "FAIL" in output_upper:
        return False, output

    # Ambiguous - treat as fail to be safe
    return False, f"Ambiguous verdict. Full output:\n{output}"


def record_validation_result(
    task_id: str,
    validator: str,
    passed: bool,
    feedback: str = None,
    project_path: Path = None,
) -> Task:
    """Record a validator's verdict.

    Args:
        task_id: Original task ID
        validator: Which validator
        passed: Whether validation passed
        feedback: Feedback if failed
        project_path: Project directory path

    Returns:
        Updated task
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]

    if passed:
        log_to_running(f"VALIDATION PASSED: {validator} approved {task_id}", project_path)

        # Check if all validators have passed
        validators = get_validators_for_task(task)
        validator_idx = validators.index(validator) if validator in validators else 0

        if validator_idx >= len(validators) - 1:
            # Last validator passed - task complete
            task.validation_status = "passed"
            task.status = "complete"

            # Add to blackboard
            orch.blackboard[f"{task.agent}:{task_id}"] = task.output or "completed"

            # Check if wave is complete
            current_wave_tasks = [t for t in orch.task_graph.tasks.values() if t.wave == orch.current_wave]
            if all(t.status == "complete" for t in current_wave_tasks):
                orch.current_wave += 1

            # Check if orchestration is complete — Gate 5 validates
            if all(t.status == "complete" for t in orch.task_graph.tasks.values()):
                allowed, reason = check_gate_5_completion(project_path)
                if allowed:
                    orch.status = "completed"
                    log_to_running(f"ORCHESTRATION COMPLETED (Gate 5 passed): {orch.spec_name}", project_path)
                else:
                    log_to_running(f"GATE 5 BLOCKED COMPLETION: {reason[:100]}", project_path)

            log_to_running(f"TASK COMPLETED (validated): {task_id}", project_path)
        else:
            # More validators to run
            log_to_running(f"STAGE {validator_idx + 1} PASSED: {task_id} awaiting next validator", project_path)

    else:
        log_to_running(f"VALIDATION FAILED: {validator} rejected {task_id}", project_path)

        task.validation_status = "failed"
        task.validator_feedback = feedback
        task.rejection_count += 1

        if task.rejection_count >= task.max_rejections:
            # Too many rejections - escalate to human
            task.status = "failed"
            orch.hitl_required = True
            orch.hitl_reason = f"Task {task_id} rejected {task.rejection_count} times. Last feedback: {feedback[:200] if feedback else 'None'}"
            log_to_running(f"HITL REQUIRED: {task_id} exceeded rejection limit", project_path)
        else:
            # Send back to worker for fixes
            task.status = "rejected"
            log_to_running(f"TASK REJECTED: {task_id} (rejection {task.rejection_count}/{task.max_rejections})", project_path)

    save_orchestration(orch, project_path)

    return task


def get_rejection_feedback_prompt(
    task_id: str,
    project_path: Path = None,
) -> str:
    """Generate prompt for worker to fix rejected task.

    Args:
        task_id: Rejected task ID
        project_path: Project directory path

    Returns:
        Prompt with validator feedback
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]

    prompt_parts = [
        f"# Task Rejected - Fixes Required",
        f"",
        f"Your work on **{task_id}** was rejected by validation.",
        f"",
        f"## Validator Feedback",
        f"",
        f"```",
        task.validator_feedback or "No specific feedback provided.",
        f"```",
        f"",
        f"## Rejection Count",
        f"",
        f"This is rejection **{task.rejection_count}** of **{task.max_rejections}**.",
    ]

    if task.rejection_count >= task.max_rejections - 1:
        prompt_parts.extend([
            f"",
            f"**WARNING**: One more rejection will escalate to human intervention.",
            f"",
        ])

    prompt_parts.extend([
        f"",
        f"## Instructions",
        f"",
        f"1. **READ** the validator feedback carefully",
        f"2. **FIX** the specific issues mentioned",
        f"3. **DO NOT** make unrelated changes",
        f"4. **SUBMIT** for validation again when done",
        f"",
        f"## Original Task",
        f"",
        f"{task.description}",
        f"",
    ])

    if task.files_in_scope:
        prompt_parts.extend([
            "## Files in Scope",
            "",
        ])
        for f in task.files_in_scope:
            prompt_parts.append(f"- `{f}`")
        prompt_parts.append("")

    return "\n".join(prompt_parts)


def retry_rejected_task(
    task_id: str,
    project_path: Path = None,
) -> dict:
    """Get spawn parameters to retry a rejected task.

    Args:
        task_id: Rejected task ID
        project_path: Project directory path

    Returns:
        Task tool call parameters for retry
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]

    if task.status != "rejected":
        raise ValueError(f"Task not in rejected state: {task_id} (status: {task.status})")

    # Mark as active again
    task.status = "active"
    task.validation_status = "none"
    save_orchestration(orch, project_path)

    prompt = get_rejection_feedback_prompt(task_id, project_path)

    log_to_running(f"TASK RETRY: {task_id} (attempt {task.rejection_count + 1})", project_path)

    return {
        "task_id": task_id,
        "description": f"{task.agent}: Fix {task_id} (attempt {task.rejection_count + 1})",
        "prompt": prompt,
        "subagent_type": "Explore",
    }


def get_tasks_needing_validation(project_path: Path = None) -> list[Task]:
    """Get all tasks currently awaiting validation.

    Args:
        project_path: Project directory path

    Returns:
        List of tasks in validating state
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch or not orch.task_graph:
        return []

    return [t for t in orch.task_graph.tasks.values() if t.status == "validating"]


def get_rejected_tasks(project_path: Path = None) -> list[Task]:
    """Get all tasks that were rejected and need retry.

    Args:
        project_path: Project directory path

    Returns:
        List of tasks in rejected state
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch or not orch.task_graph:
        return []

    return [t for t in orch.task_graph.tasks.values() if t.status == "rejected"]


# === Validation Hierarchy (Hardening Spec v2, Step 7) ===
#
# Tier 1: deterministic checks (tests, linters, type-checkers) — MANDATORY, gate completion
# Tier 2: LLM review (sentinel agents) — ADVISORY, findings surfaced but don't gate
# Tier 3: human override — ONLY path to override Tier 1 failure
#
# INVARIANT: No code path allows Tier 2 to override a Tier 1 failure.

from .agents_config import VALIDATION_TIERS, VALIDATOR_TIERS


@dataclass
class ValidationResult:
    """Result from running validation hierarchy."""
    tier1_passed: bool  # All deterministic checks passed
    tier1_failures: list  # List of (command, error) tuples
    tier2_findings: list  # Advisory findings from LLM review (don't gate)
    can_complete: bool  # True only if tier1_passed
    override_required: bool = False  # True if tier1 failed (needs HITL)

    @property
    def summary(self) -> str:
        parts = []
        if self.tier1_passed:
            parts.append("Tier 1 (deterministic): PASSED")
        else:
            parts.append(f"Tier 1 (deterministic): FAILED ({len(self.tier1_failures)} failures)")
        if self.tier2_findings:
            parts.append(f"Tier 2 (advisory): {len(self.tier2_findings)} findings")
        else:
            parts.append("Tier 2 (advisory): clean")
        return " | ".join(parts)


def get_validator_tier(validator: str) -> int:
    """Get the validation tier for a validator agent.

    Returns 1 for deterministic (mandatory), 2 for LLM (advisory).
    Unknown validators default to Tier 1 (fail-closed / mandatory).
    """
    return VALIDATOR_TIERS.get(validator, 1)


def run_validation_hierarchy(
    deterministic_commands: list[str] = None,
    tier2_findings: list[str] = None,
    project_path: Path = None,
) -> ValidationResult:
    """Run the validation hierarchy.

    Step 1: Run Tier 1 (deterministic) commands — all must pass.
    Step 2: Collect Tier 2 (LLM review) findings — advisory only.
    Step 3: Determine outcome:
      - Tier 1 passed → can_complete=True
      - Tier 1 failed → can_complete=False, override_required=True (HITL only)
      - Tier 2 findings never gate completion.

    INVARIANT: Tier 2 cannot override Tier 1 failure.

    Args:
        deterministic_commands: Shell commands for Tier 1
        tier2_findings: Pre-collected advisory findings (from sentinel agents)
        project_path: Project directory path

    Returns:
        ValidationResult
    """
    project_path = project_path or Path.cwd()
    tier2_findings = tier2_findings or []

    # === Tier 1: Deterministic checks ===
    tier1_failures = []
    for cmd in (deterministic_commands or []):
        if not isinstance(cmd, str) or not cmd.strip():
            tier1_failures.append((cmd, "Invalid command"))
            continue

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(project_path),
                timeout=300,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()[:200]
                tier1_failures.append((cmd, f"exit {result.returncode}: {stderr}"))
        except subprocess.TimeoutExpired:
            tier1_failures.append((cmd, "timed out (300s)"))
        except Exception as e:
            tier1_failures.append((cmd, str(e)))

    tier1_passed = len(tier1_failures) == 0

    # === Tier 2: LLM advisory (collected externally) ===
    # tier2_findings are surfaced but NEVER gate completion.
    # This is enforced structurally: can_complete depends ONLY on tier1_passed.

    # === Outcome ===
    # INVARIANT: can_complete = tier1_passed (Tier 2 has no influence)
    return ValidationResult(
        tier1_passed=tier1_passed,
        tier1_failures=tier1_failures,
        tier2_findings=tier2_findings,
        can_complete=tier1_passed,
        override_required=not tier1_passed,
    )


def classify_validation_verdict(
    validator: str,
    passed: bool,
    feedback: str = None,
) -> dict:
    """Classify a validation verdict by tier.

    Tier 1 verdicts gate completion (mandatory).
    Tier 2 verdicts are advisory (surfaced but don't gate).

    Args:
        validator: Validator agent name
        passed: Whether validation passed
        feedback: Feedback if failed

    Returns:
        Dict with tier, mandatory, and impact fields
    """
    tier = get_validator_tier(validator)
    is_mandatory = VALIDATION_TIERS.get(tier, {}).get("mandatory", True)

    return {
        "validator": validator,
        "tier": tier,
        "tier_name": VALIDATION_TIERS.get(tier, {}).get("name", "unknown"),
        "passed": passed,
        "mandatory": is_mandatory,
        "gates_completion": is_mandatory and not passed,
        "feedback": feedback,
    }


# === Agent Spawning ===

def generate_agent_prompt(
    task: Task,
    orch: Orchestration,
    project_path: Path = None,
) -> str:
    """Generate the prompt for an agent to execute a task.

    Args:
        task: Task to execute
        orch: Current orchestration
        project_path: Project directory path

    Returns:
        Prompt string for the agent
    """
    project_path = project_path or Path.cwd()
    agent_info = AGENTS.get(task.agent, {})

    # Build context from spec and blackboard
    spec_content = ""
    if orch.spec_path:
        spec_path = project_path / orch.spec_path
        if spec_path.exists():
            spec_content = spec_path.read_text()

    # Get outputs from dependent tasks
    dependency_outputs = []
    for dep_id in task.dependencies:
        if dep_id in orch.task_graph.tasks:
            dep_task = orch.task_graph.tasks[dep_id]
            if dep_task.output:
                dependency_outputs.append(f"## Output from {dep_id} ({dep_task.agent}):\n{dep_task.output}")

    # Build prompt based on agent type
    prompt_parts = [
        f"# Task: {task.description}",
        f"Agent: {task.agent}",
        f"Role: {agent_info.get('role', 'Execute the task')}",
        "",
    ]

    if task.files_in_scope:
        prompt_parts.append(f"## Files in Scope")
        prompt_parts.append("You may ONLY modify these files:")
        for f in task.files_in_scope:
            prompt_parts.append(f"- {f}")
        prompt_parts.append("")

    if spec_content:
        prompt_parts.append("## Spec Reference")
        prompt_parts.append(spec_content[:2000])  # Truncate if very long
        prompt_parts.append("")

    if dependency_outputs:
        prompt_parts.append("## Previous Task Outputs")
        prompt_parts.extend(dependency_outputs)
        prompt_parts.append("")

    # Agent-specific instructions
    if task.agent == "QA":
        prompt_parts.extend([
            "## QA Instructions",
            "1. Write tests FIRST (TDD)",
            "2. Tests must cover all acceptance criteria from spec",
            "3. Include edge cases and error handling tests",
            "4. Do NOT implement the feature - only write tests",
            "",
        ])
    elif task.agent == "Dev":
        prompt_parts.extend([
            "## Dev Instructions",
            "1. Implement code to pass the tests written by QA",
            "2. Follow SOLID principles",
            "3. Keep implementation minimal - only what's needed to pass tests",
            "4. Do NOT modify test files",
            "",
        ])
    elif task.agent == "Validator-Tests":
        prompt_parts.extend([
            "## Validator-Tests Instructions",
            "1. Review the tests written by QA",
            "2. Verify tests match the spec requirements",
            "3. Check for missing edge cases",
            "4. Report any gaps or issues found",
            "",
        ])
    elif task.agent == "Validator-Code":
        prompt_parts.extend([
            "## Validator-Code Instructions",
            "1. Run the tests to verify implementation",
            "2. Review code for correctness and adherence to spec",
            "3. Check for potential bugs or issues",
            "4. Report any problems found",
            "",
        ])
    elif task.agent == "Reviewer":
        prompt_parts.extend([
            "## Reviewer Instructions",
            "1. Use /review skill to perform code review",
            "2. Check for code quality issues",
            "3. Verify adherence to project standards",
            "",
        ])
    elif task.agent == "Security":
        prompt_parts.extend([
            "## Security Instructions",
            "1. Use /security-review skill to audit the code",
            "2. Check for OWASP Top 10 vulnerabilities",
            "3. Report any security concerns",
            "",
        ])
    elif task.agent == "Architect":
        prompt_parts.extend([
            "## Architect Instructions",
            "1. Design the solution architecture",
            "2. Create interface definitions",
            "3. Document in docs/ or specs/",
            "4. Do NOT write implementation code",
            "",
        ])
    elif task.agent == "Docs":
        prompt_parts.extend([
            "## Docs Instructions",
            "1. Update relevant documentation",
            "2. Ensure README reflects changes",
            "3. Add inline comments where needed",
            "",
        ])

    prompt_parts.extend([
        "## Completion",
        "When done, report your output clearly so it can be passed to subsequent tasks.",
        f"Task ID for completion: {task.id}",
    ])

    base_prompt = "\n".join(prompt_parts)

    # Enhance with skill invocation for skill-based agents
    skill = get_skill_for_agent(task.agent)
    if skill:
        base_prompt = enhance_agent_prompt_with_skill(
            base_prompt,
            task.agent,
            task.files_in_scope,
        )

    return base_prompt


def get_spawn_task_call(
    task_id: str,
    project_path: Path = None,
) -> dict:
    """Get the Task tool call parameters for spawning an agent.

    This returns the parameters that should be passed to the Task tool
    to spawn the appropriate agent for this task.

    Args:
        task_id: Task ID to spawn agent for
        project_path: Project directory path

    Returns:
        Dict with Task tool parameters:
        - description: Short description
        - prompt: Full agent prompt
        - subagent_type: Agent type to use
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        raise ValueError("No active orchestration")

    if task_id not in orch.task_graph.tasks:
        raise ValueError(f"Task not found: {task_id}")

    task = orch.task_graph.tasks[task_id]
    agent_info = AGENTS.get(task.agent, {})

    prompt = generate_agent_prompt(task, orch, project_path)

    # Map agent to subagent_type
    # Skill-based agents need special handling
    if "skill" in agent_info:
        # For skill-based agents, the prompt tells them to use the skill
        subagent_type = "Explore"
    else:
        subagent_type = "Explore"

    return {
        "description": f"{task.agent}: {task.description[:30]}",
        "prompt": prompt,
        "subagent_type": subagent_type,
    }


def spawn_agent_for_task(
    task_id: str,
    project_path: Path = None,
) -> dict:
    """Start a task and return the spawn parameters.

    This is a convenience function that:
    1. Marks the task as active
    2. Returns the Task tool call parameters

    Args:
        task_id: Task ID to spawn
        project_path: Project directory path

    Returns:
        Dict with Task tool parameters
    """
    project_path = project_path or Path.cwd()

    # Mark task as active
    task = start_task(task_id, project_path)

    # Get spawn parameters
    return get_spawn_task_call(task_id, project_path)


def get_parallel_spawn_calls(
    project_path: Path = None,
) -> list:
    """Get Task tool calls for all ready tasks (for parallel execution).

    Use this when you want to spawn multiple agents in parallel.

    Args:
        project_path: Project directory path

    Returns:
        List of dicts with Task tool parameters for each ready task
    """
    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return []

    ready_tasks = orch.task_graph.get_ready_tasks() if orch.task_graph else []

    spawn_calls = []
    for task in ready_tasks:
        try:
            # Mark as active
            task.status = "active"
            spawn_calls.append({
                "task_id": task.id,
                "params": get_spawn_task_call(task.id, project_path),
            })
        except Exception as e:
            logger.warning("Non-fatal error in orchestrator (parallel spawn): %s", e)
            continue

    # Save state with tasks marked active
    if spawn_calls:
        save_orchestration(orch, project_path)

    return spawn_calls


def spawn_parallel_tasks_with_worktrees(
    task_ids: list[str] = None,
    project_path: Path = None,
) -> list[dict]:
    """Spawn parallel tasks, each in its own worktree.

    Creates isolated git worktrees for each task, allowing parallel
    development without conflicts.

    Args:
        task_ids: List of task IDs to spawn (if None, spawns all ready tasks)
        project_path: Project directory path

    Returns:
        List of dicts with:
            - task_id: Task ID
            - worktree_path: Path to the worktree
            - params: Task tool parameters for spawning
    """
    from .worktree import create_worktree, get_worktree

    project_path = project_path or Path.cwd()

    orch = load_orchestration(project_path)
    if not orch:
        return []

    # If no task_ids specified, get all ready tasks
    if task_ids is None:
        ready_tasks = orch.task_graph.get_ready_tasks() if orch.task_graph else []
        task_ids = [t.id for t in ready_tasks]

    spawn_calls = []
    for task_id in task_ids:
        try:
            # Check if worktree already exists
            existing = get_worktree(task_id, project_path)
            if existing:
                worktree_path = existing.path
            else:
                # Create worktree
                worktree_path = create_worktree(task_id, project_path=project_path)

            # Get spawn params
            params = get_spawn_task_call(task_id, project_path)

            # Mark task as active
            task = next((t for t in orch.task_graph.tasks if t.id == task_id), None)
            if task:
                task.status = "active"

            spawn_calls.append({
                "task_id": task_id,
                "worktree_path": str(worktree_path),
                "params": params,
            })
        except (ValueError, subprocess.CalledProcessError) as e:
            import logging
            logging.getLogger(__name__).error(
                f"Failed to spawn worktree for task {task_id}: {e}"
            )
            continue

    # Save state with tasks marked active
    if spawn_calls:
        save_orchestration(orch, project_path)

    return spawn_calls
