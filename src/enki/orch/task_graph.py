"""task_graph.py — DAG + waves + cyclic recovery.

Two-level DAG: sprint-level (which sprints, dependencies between them)
+ task-level (per-task workflow within a sprint).

Waves are groups of tasks whose dependencies are all met.
Cyclic recovery: max 3 retries before HITL escalation.

MAX_PARALLEL_TASKS = 2 (configurable).
Within task: QA and Dev always parallel (blind wall).
Max 4 concurrent subagents (2 tasks * 2 agents each).
"""

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional

from enki.config import get_config
from enki.db import em_db


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    HITL = "hitl"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    """Single task in the DAG."""
    task_id: str
    task_name: str
    sprint_id: str
    tier: str
    status: str = "pending"
    dependencies: list[str] = field(default_factory=list)
    assigned_files: list[str] = field(default_factory=list)
    work_type: str | None = None
    agent_outputs: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_db_row(cls, row: dict) -> "TaskNode":
        """Create TaskNode from database row."""
        return cls(
            task_id=row["task_id"],
            task_name=row["task_name"],
            sprint_id=row["sprint_id"],
            tier=row["tier"],
            status=row["status"],
            dependencies=json.loads(row["dependencies"] or "[]"),
            assigned_files=json.loads(row["assigned_files"] or "[]"),
            work_type=row.get("work_type"),
            agent_outputs=row.get("agent_outputs"),
            retry_count=row.get("retry_count", 0),
            max_retries=row.get("max_retries", 3),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
        )


@dataclass
class SprintNode:
    """A sprint containing multiple tasks."""
    sprint_id: str
    project_id: str
    sprint_number: int
    status: str = "pending"
    dependencies: list[str] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_db_row(cls, row: dict) -> "SprintNode":
        return cls(
            sprint_id=row["sprint_id"],
            project_id=row["project_id"],
            sprint_number=row["sprint_number"],
            status=row["status"],
            dependencies=json.loads(row.get("dependencies") or "[]"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
        )


# ── Task CRUD ──


def create_task(
    project: str,
    sprint_id: str,
    task_name: str,
    tier: str,
    dependencies: list[str] | None = None,
    assigned_files: list[str] | None = None,
    work_type: str | None = None,
) -> str:
    """Create a task in the DAG. Returns task_id."""
    task_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, "
            "dependencies, assigned_files, work_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, project, sprint_id, task_name, tier,
                json.dumps(dependencies or []),
                json.dumps(assigned_files or []),
                work_type,
            ),
        )
    return task_id


def create_task_from_spec(
    project: str,
    sprint_id: str,
    spec_entry: dict,
) -> str:
    """Create a task from an Architect's spec entry.

    Expected spec_entry format:
    {
        "name": "Task name",
        "files": ["src/foo.py", "src/bar.py"],
        "dependencies": ["task-id-1"],
        "work_type": "implementation",
        "tier": "standard"
    }
    """
    return create_task(
        project=project,
        sprint_id=sprint_id,
        task_name=spec_entry["name"],
        tier=spec_entry.get("tier", "standard"),
        dependencies=spec_entry.get("dependencies", []),
        assigned_files=spec_entry.get("files", []),
        work_type=spec_entry.get("work_type"),
    )


def get_task(project: str, task_id: str) -> dict | None:
    """Get task by ID. JSON fields are parsed."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM task_state WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        task = dict(row)
        task["dependencies"] = json.loads(task["dependencies"] or "[]")
        task["assigned_files"] = json.loads(task["assigned_files"] or "[]")
        return task


def get_task_node(project: str, task_id: str) -> TaskNode | None:
    """Get task as a TaskNode dataclass."""
    task = get_task(project, task_id)
    if not task:
        return None
    return TaskNode.from_db_row(task)


def update_task_status(
    project: str,
    task_id: str,
    status: str,
    agent_outputs: str | None = None,
) -> None:
    """Update task status with timestamps."""
    with em_db(project) as conn:
        updates = ["status = ?"]
        params: list = [status]

        if status == "in_progress":
            updates.append("started_at = datetime('now')")
        elif status in ("completed", "failed"):
            updates.append("completed_at = datetime('now')")

        if agent_outputs:
            updates.append("agent_outputs = ?")
            params.append(agent_outputs)

        params.append(task_id)
        conn.execute(
            f"UPDATE task_state SET {', '.join(updates)} WHERE task_id = ?",
            params,
        )


def update_task_files(project: str, task_id: str, files: list[str]) -> None:
    """Update the assigned files for a task."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE task_state SET assigned_files = ? WHERE task_id = ?",
            (json.dumps(files), task_id),
        )


def mark_complete(project: str, task_id: str, output: str | None = None) -> None:
    """Mark task complete. Unblocks dependent tasks."""
    update_task_status(project, task_id, TaskStatus.COMPLETED, agent_outputs=output)


def mark_failed(project: str, task_id: str, error: str | None = None) -> None:
    """Mark task failed. Increments retry count.

    If retries >= max_retries: status stays 'failed' (HITL escalation).
    Otherwise: resets to 'pending' for retry.
    """
    retry_count = increment_retry(project, task_id)
    if needs_hitl(project, task_id):
        update_task_status(project, task_id, TaskStatus.HITL, agent_outputs=error)
    else:
        # Status already set to 'pending' by increment_retry
        pass


# ── Retry Management ──


def increment_retry(project: str, task_id: str) -> int:
    """Increment retry count. Returns new count."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE task_state SET retry_count = retry_count + 1, "
            "status = 'pending' WHERE task_id = ?",
            (task_id,),
        )
        row = conn.execute(
            "SELECT retry_count, max_retries FROM task_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row["retry_count"]


def needs_hitl(project: str, task_id: str) -> bool:
    """Check if task has exceeded max retries."""
    task = get_task(project, task_id)
    if not task:
        return False
    return task.get("retry_count", 0) >= task.get("max_retries", 3)


# ── Wave Scheduling ──


def get_next_wave(project: str, sprint_id: str) -> list[dict]:
    """Get the next wave of tasks ready to execute.

    A task is ready when:
    1. Status is 'pending'
    2. All its dependencies are completed
    3. Total in-progress tasks < MAX_PARALLEL_TASKS
    """
    config = get_config()
    max_parallel = config["gates"]["max_parallel_tasks"]

    tasks = get_sprint_tasks(project, sprint_id)

    completed_ids = {
        t["task_id"] for t in tasks if t["status"] == "completed"
    }
    in_progress_ids = {
        t["task_id"] for t in tasks if t["status"] == "in_progress"
    }

    if len(in_progress_ids) >= max_parallel:
        return []

    ready = []
    for task in tasks:
        if task["status"] != "pending":
            continue
        deps = set(task["dependencies"])
        if deps.issubset(completed_ids):
            ready.append(task)

    slots = max_parallel - len(in_progress_ids)
    return ready[:slots]


def get_all_waves(project: str, sprint_id: str) -> list[list[dict]]:
    """Calculate all waves for visualization.

    Returns list of lists: [wave_1_tasks, wave_2_tasks, ...].
    Simulates execution order without actually running tasks.
    """
    tasks = get_sprint_tasks(project, sprint_id)
    task_map = {t["task_id"]: t for t in tasks}
    completed = set()
    waves = []

    max_iterations = len(tasks) + 1
    for _ in range(max_iterations):
        wave = []
        for task in tasks:
            if task["task_id"] in completed:
                continue
            deps = set(task["dependencies"])
            if deps.issubset(completed):
                wave.append(task)
        if not wave:
            break
        waves.append(wave)
        completed.update(t["task_id"] for t in wave)

    return waves


def get_ready_tasks(project: str, sprint_id: str | None = None) -> list[dict]:
    """Get tasks ready to spawn NOW. Respects MAX_PARALLEL_TASKS.

    If sprint_id is None, checks the active sprint.
    """
    if sprint_id is None:
        sprint_id = _get_active_sprint_id(project)
        if not sprint_id:
            return []

    return get_next_wave(project, sprint_id)


# ── Task Queries ──


def get_sprint_tasks(project: str, sprint_id: str) -> list[dict]:
    """Get all tasks in a sprint with parsed JSON fields."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM task_state WHERE sprint_id = ?",
            (sprint_id,),
        ).fetchall()
    tasks = []
    for row in rows:
        task = dict(row)
        task["dependencies"] = json.loads(task["dependencies"] or "[]")
        task["assigned_files"] = json.loads(task["assigned_files"] or "[]")
        tasks.append(task)
    return tasks


def get_project_tasks(project: str, status: str | None = None) -> list[dict]:
    """Get all tasks for a project, optionally filtered by status."""
    query = "SELECT * FROM task_state WHERE project_id = ?"
    params: list = [project]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY started_at"

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
    tasks = []
    for row in rows:
        task = dict(row)
        task["dependencies"] = json.loads(task["dependencies"] or "[]")
        task["assigned_files"] = json.loads(task["assigned_files"] or "[]")
        tasks.append(task)
    return tasks


def is_sprint_complete(project: str, sprint_id: str) -> bool:
    """Check if all tasks in sprint are completed or skipped."""
    tasks = get_sprint_tasks(project, sprint_id)
    return all(t["status"] in ("completed", "skipped") for t in tasks)


def count_tasks_by_status(project: str, sprint_id: str) -> dict:
    """Count tasks in each status for a sprint."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM task_state "
            "WHERE sprint_id = ? GROUP BY status",
            (sprint_id,),
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


# ── Dependencies ──


def add_dependency(project: str, task_id: str, depends_on: str) -> None:
    """Add a dependency to a task."""
    task = get_task(project, task_id)
    if not task:
        return
    deps = task["dependencies"]
    if depends_on not in deps:
        deps.append(depends_on)
        with em_db(project) as conn:
            conn.execute(
                "UPDATE task_state SET dependencies = ? WHERE task_id = ?",
                (json.dumps(deps), task_id),
            )


def remove_dependency(project: str, task_id: str, depends_on: str) -> None:
    """Remove a dependency from a task."""
    task = get_task(project, task_id)
    if not task:
        return
    deps = task["dependencies"]
    if depends_on in deps:
        deps.remove(depends_on)
        with em_db(project) as conn:
            conn.execute(
                "UPDATE task_state SET dependencies = ? WHERE task_id = ?",
                (json.dumps(deps), task_id),
            )


def insert_dependency_for_overlap(
    project: str,
    sprint_id: str,
) -> list[tuple[str, str]]:
    """Auto-add dependencies between tasks that touch the same files.

    Returns list of (dependent_task_id, blocking_task_id) pairs added.
    """
    tasks = get_sprint_tasks(project, sprint_id)
    overlaps = detect_file_overlaps(tasks)
    added = []

    for t1_id, t2_id in overlaps:
        t1 = get_task(project, t1_id)
        t2 = get_task(project, t2_id)
        if not t1 or not t2:
            continue

        # The task created later depends on the one created earlier
        if t2_id not in t1["dependencies"] and t1_id not in t2["dependencies"]:
            add_dependency(project, t2_id, t1_id)
            added.append((t2_id, t1_id))

    return added


# ── Cycle Detection & Recovery ──


def detect_cycles(project: str, sprint_id: str) -> list[list[str]]:
    """Detect circular dependencies in task graph. Returns list of cycles."""
    tasks = get_sprint_tasks(project, sprint_id)
    graph: dict[str, list[str]] = {}
    for t in tasks:
        graph[t["task_id"]] = t["dependencies"]

    cycles = []
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> None:
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        path_set.add(node)
        for dep in graph.get(node, []):
            dfs(dep)
        path.pop()
        path_set.remove(node)

    for node in graph:
        dfs(node)

    return cycles


def recover_from_cycle(
    project: str,
    sprint_id: str,
    cycle: list[str],
) -> bool:
    """Attempt to recover from a cyclic dependency.

    Strategy: break the cycle by removing the dependency that creates it
    (the last edge in the cycle). If that's not possible, escalate to HITL.

    Returns True if recovered, False if unrecoverable.
    """
    if len(cycle) < 2:
        return False

    # Break the cycle at the last edge: remove dependency from cycle[-1] to cycle[-2]
    last_task_id = cycle[-2]  # The task that depends on...
    dep_id = cycle[-1]        # ...this task (which creates the cycle)

    # Actually cycle[-1] == cycle[0], so: remove dep from cycle[-2] → cycle[0]
    if len(cycle) >= 3:
        task_with_dep = cycle[-2]
        dep_to_remove = cycle[0]
        remove_dependency(project, task_with_dep, dep_to_remove)
        return True

    return False


def validate_dag(project: str, sprint_id: str) -> dict:
    """Validate the DAG for a sprint.

    Checks:
    1. No circular dependencies
    2. All dependency targets exist
    3. No orphan tasks (tasks with deps on non-existent tasks)
    4. File overlap warnings

    Returns {"valid": bool, "issues": list[str]}.
    """
    tasks = get_sprint_tasks(project, sprint_id)
    task_ids = {t["task_id"] for t in tasks}
    issues = []

    # Check for missing dependency targets
    for task in tasks:
        for dep in task["dependencies"]:
            if dep not in task_ids:
                issues.append(
                    f"Task '{task['task_name']}' depends on non-existent task {dep}"
                )

    # Check for cycles
    cycles = detect_cycles(project, sprint_id)
    for cycle in cycles:
        issues.append(f"Circular dependency detected: {' → '.join(cycle)}")

    # Check file overlaps
    overlaps = detect_file_overlaps(tasks)
    for t1_id, t2_id in overlaps:
        t1 = next((t for t in tasks if t["task_id"] == t1_id), None)
        t2 = next((t for t in tasks if t["task_id"] == t2_id), None)
        if t1 and t2:
            # Only warn if there's no dependency between them
            if t2_id not in t1["dependencies"] and t1_id not in t2["dependencies"]:
                issues.append(
                    f"File overlap: '{t1['task_name']}' and '{t2['task_name']}' "
                    f"touch the same files without a dependency"
                )

    return {"valid": len(issues) == 0, "issues": issues}


# ── File Overlap Detection ──


def detect_file_overlaps(tasks: list[dict]) -> list[tuple[str, str]]:
    """Detect file overlaps between tasks. Returns pairs of conflicting task IDs."""
    file_map: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for f in task.get("assigned_files", []):
            file_map[f].append(task["task_id"])

    conflicts = []
    for filepath, task_ids in file_map.items():
        if len(task_ids) > 1:
            for i in range(len(task_ids)):
                for j in range(i + 1, len(task_ids)):
                    conflicts.append((task_ids[i], task_ids[j]))

    return list(set(conflicts))


def get_file_overlap_map(
    project: str,
    sprint_id: str,
) -> dict[str, list[str]]:
    """Get map of files to task IDs that touch them.

    Returns: {filename: [task_ids]}.
    Only includes files touched by 2+ tasks.
    """
    tasks = get_sprint_tasks(project, sprint_id)
    file_map: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for f in task.get("assigned_files", []):
            file_map[f].append(task["task_id"])

    return {f: ids for f, ids in file_map.items() if len(ids) > 1}


# ── Sprint Management ──


def create_sprint(
    project: str,
    sprint_number: int,
    dependencies: list[str] | None = None,
) -> str:
    """Create a sprint. Returns sprint_id."""
    sprint_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO sprint_state "
            "(sprint_id, project_id, sprint_number, dependencies) "
            "VALUES (?, ?, ?, ?)",
            (sprint_id, project, sprint_number,
             json.dumps(dependencies or [])),
        )
    return sprint_id


def get_sprint(project: str, sprint_id: str) -> dict | None:
    """Get sprint state."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM sprint_state WHERE sprint_id = ?", (sprint_id,)
        ).fetchone()
        return dict(row) if row else None


def get_sprint_node(project: str, sprint_id: str) -> SprintNode | None:
    """Get sprint as a SprintNode dataclass."""
    sprint = get_sprint(project, sprint_id)
    if not sprint:
        return None
    return SprintNode.from_db_row(sprint)


def get_sprints_ordered(project: str) -> list[dict]:
    """Return sprints in order (by sprint_number)."""
    with em_db(project) as conn:
        rows = conn.execute(
            "SELECT * FROM sprint_state WHERE project_id = ? "
            "ORDER BY sprint_number",
            (project,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_sprint(project: str) -> dict | None:
    """Get the currently active sprint for a project."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT * FROM sprint_state "
            "WHERE project_id = ? AND status IN ('active', 'in_progress', 'pending') "
            "ORDER BY sprint_number DESC LIMIT 1",
            (project,),
        ).fetchone()
        return dict(row) if row else None


def update_sprint_status(project: str, sprint_id: str, status: str) -> None:
    """Update sprint status."""
    with em_db(project) as conn:
        updates = ["status = ?"]
        if status == "in_progress":
            updates.append("started_at = datetime('now')")
        elif status == "completed":
            updates.append("completed_at = datetime('now')")

        conn.execute(
            f"UPDATE sprint_state SET {', '.join(updates)} WHERE sprint_id = ?",
            [status, sprint_id],
        )


def advance_to_next_sprint(project: str) -> dict:
    """Complete current sprint and advance to next.

    Returns info about the transition or error.
    """
    current = get_active_sprint(project)
    if not current:
        return {"error": "No active sprint"}

    current_id = current["sprint_id"]
    if not is_sprint_complete(project, current_id):
        return {"error": "Current sprint not complete"}

    update_sprint_status(project, current_id, "completed")

    # Find or create next sprint
    sprints = get_sprints_ordered(project)
    current_num = current["sprint_number"]
    next_sprint = None
    for s in sprints:
        if s["sprint_number"] > current_num:
            next_sprint = s
            break

    if next_sprint:
        update_sprint_status(project, next_sprint["sprint_id"], "in_progress")
        return {
            "previous_sprint": current_id,
            "new_sprint": next_sprint["sprint_id"],
            "status": "advanced",
        }
    else:
        return {
            "previous_sprint": current_id,
            "status": "all_sprints_complete",
        }


# ── State Export/Import ──


def export_state(project: str, sprint_id: str) -> dict:
    """Serialize sprint DAG state for storage or resume."""
    sprint = get_sprint(project, sprint_id)
    tasks = get_sprint_tasks(project, sprint_id)

    return {
        "sprint": sprint,
        "tasks": tasks,
        "exported_at": datetime.now().isoformat(),
    }


def import_state(project: str, state: dict) -> str:
    """Restore DAG from serialized state. Returns sprint_id.

    Used for mid-flight resume after crash.
    """
    sprint_data = state["sprint"]
    sprint_id = sprint_data["sprint_id"]

    # Create sprint if not exists
    existing = get_sprint(project, sprint_id)
    if not existing:
        with em_db(project) as conn:
            conn.execute(
                "INSERT INTO sprint_state "
                "(sprint_id, project_id, sprint_number, status, dependencies) "
                "VALUES (?, ?, ?, ?, ?)",
                (sprint_id, project, sprint_data["sprint_number"],
                 sprint_data["status"],
                 json.dumps(sprint_data.get("dependencies") or [])),
            )

    # Restore tasks
    for task in state["tasks"]:
        existing_task = get_task(project, task["task_id"])
        if not existing_task:
            with em_db(project) as conn:
                deps = task["dependencies"]
                if isinstance(deps, list):
                    deps = json.dumps(deps)
                files = task.get("assigned_files", [])
                if isinstance(files, list):
                    files = json.dumps(files)

                conn.execute(
                    "INSERT INTO task_state "
                    "(task_id, project_id, sprint_id, task_name, tier, "
                    "status, dependencies, assigned_files, work_type, "
                    "retry_count, max_retries) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task["task_id"], project, sprint_id,
                     task["task_name"], task["tier"], task["status"],
                     deps, files, task.get("work_type"),
                     task.get("retry_count", 0), task.get("max_retries", 3)),
                )

    return sprint_id


# ── Sprint Summary ──


def get_sprint_summary(project: str, sprint_id: str) -> dict:
    """Get summary of sprint progress."""
    sprint = get_sprint(project, sprint_id)
    tasks = get_sprint_tasks(project, sprint_id)
    counts = count_tasks_by_status(project, sprint_id)

    total = len(tasks)
    completed = counts.get("completed", 0) + counts.get("skipped", 0)
    failed = counts.get("failed", 0) + counts.get("hitl", 0)

    return {
        "sprint_id": sprint_id,
        "sprint_number": sprint["sprint_number"] if sprint else 0,
        "status": sprint["status"] if sprint else "unknown",
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "in_progress": counts.get("in_progress", 0),
        "pending": counts.get("pending", 0),
        "blocked": counts.get("blocked", 0),
        "progress_pct": round(completed / total * 100, 1) if total > 0 else 0,
        "is_complete": is_sprint_complete(project, sprint_id),
    }


# ── Private helpers ──


def _get_active_sprint_id(project: str) -> str | None:
    """Get the active sprint ID for a project."""
    sprint = get_active_sprint(project)
    return sprint["sprint_id"] if sprint else None
