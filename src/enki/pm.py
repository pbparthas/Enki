"""PM (Project Management) module for Enki.

Handles the debate, plan, approve, and decompose phases.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import re

from .session import ensure_project_enki_dir, get_phase, set_phase


# Perspectives for debate phase
PERSPECTIVES = [
    "PM",
    "CTO",
    "Architect",
    "DBA",
    "Security",
    "Devil's Advocate",
]

PERSPECTIVE_PROMPTS = {
    "PM": """### PM Perspective
- Does this align with product goals?
- User impact and value?
- Priority vs other work?
- MVP scope - what can we cut?
- Success metrics and KPIs?
- Timeline expectations?""",

    "CTO": """### CTO Perspective
- Strategic alignment with tech vision?
- Technical debt implications?
- Team capacity and skills?
- Build vs buy consideration?
- Long-term maintainability?""",

    "Architect": """### Architect Perspective
- System impact and boundaries?
- Integration points and dependencies?
- Scalability concerns?
- Breaking changes?
- Design patterns to apply?""",

    "DBA": """### DBA Perspective
- Data model changes required?
- Migration complexity and risk?
- Query performance implications?
- Data integrity constraints?
- Backup/recovery impact?""",

    "Security": """### Security Perspective
- Authentication/authorization impact?
- Data sensitivity considerations?
- Attack surface changes?
- Compliance requirements?""",

    "Devil's Advocate": """### Devil's Advocate
- What could go wrong?
- What are we missing?
- Hidden assumptions we're making?
- Why might this fail?
- What's the worst case scenario?""",
}

SPEC_TEMPLATE = '''# {name}

## Problem Statement
What problem are we solving? Why now?

{problem_statement}

## Proposed Solution
How will we solve it? High-level approach.

{proposed_solution}

## Success Criteria
How do we know it's done? Measurable outcomes.

- [ ] {success_criteria}

## Technical Design

### Components Affected
- {components}

### API Changes
- {api_changes}

### Data Model Changes
- {data_changes}

### Dependencies
- {dependencies}

## Task Breakdown

### Wave 1: Foundation
| Task | Agent | Dependencies | Files |
|------|-------|--------------|-------|
| {task_1} | {agent_1} | - | {files_1} |

### Wave 2: Implementation
| Task | Agent | Dependencies | Files |
|------|-------|--------------|-------|
| {task_2} | {agent_2} | Wave 1 | {files_2} |

### Wave 3: Validation
| Task | Agent | Dependencies | Files |
|------|-------|--------------|-------|
| {task_3} | {agent_3} | Wave 2 | {files_3} |

## Test Strategy
- Unit tests required
- Integration tests required
- Edge cases to cover
- Performance benchmarks

## Risks & Mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| {risk} | {likelihood} | {impact} | {mitigation} |

## Open Questions
- [ ] {question_1}

## Decisions Made
| Decision | Why | Alternatives Rejected |
|----------|-----|----------------------|
'''


@dataclass
class Task:
    """A single task in the task graph."""
    id: str
    description: str
    agent: str
    status: str = "pending"  # pending, blocked, active, complete, failed
    dependencies: list = field(default_factory=list)
    files_in_scope: list = field(default_factory=list)
    output: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    wave: int = 1


@dataclass
class TaskGraph:
    """Graph of tasks with dependencies."""
    spec_name: str
    spec_path: str
    tasks: dict = field(default_factory=dict)

    def add_task(self, task: Task):
        """Add a task to the graph."""
        self.tasks[task.id] = task

    def get_ready_tasks(self) -> list:
        """Get tasks with all dependencies complete."""
        ready = []
        completed = {t.id for t in self.tasks.values() if t.status == 'complete'}

        for task in self.tasks.values():
            if task.status == 'pending':
                if set(task.dependencies).issubset(completed):
                    ready.append(task)

        return ready

    def get_waves(self) -> list:
        """Group tasks into parallel execution waves."""
        waves = []
        completed = set()
        remaining = set(self.tasks.keys())

        while remaining:
            wave = []
            for task_id in list(remaining):
                task = self.tasks[task_id]
                if set(task.dependencies).issubset(completed):
                    wave.append(task)

            if not wave:
                break  # Circular dependency or error

            for task in wave:
                remaining.remove(task.id)
                completed.add(task.id)
            waves.append(wave)

        return waves

    def mark_complete(self, task_id: str, output: str = None):
        """Mark a task as complete."""
        if task_id in self.tasks:
            self.tasks[task_id].status = 'complete'
            self.tasks[task_id].output = output

    def mark_failed(self, task_id: str):
        """Mark a task as failed (with retry logic)."""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.attempts += 1

            if task.attempts >= task.max_attempts:
                task.status = 'failed'  # HITL
            else:
                task.status = 'pending'  # Retry

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "spec_name": self.spec_name,
            "spec_path": self.spec_path,
            "tasks": {
                tid: {
                    "id": t.id,
                    "description": t.description,
                    "agent": t.agent,
                    "status": t.status,
                    "dependencies": t.dependencies,
                    "files_in_scope": t.files_in_scope,
                    "output": t.output,
                    "attempts": t.attempts,
                    "max_attempts": t.max_attempts,
                    "wave": t.wave,
                }
                for tid, t in self.tasks.items()
            }
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TaskGraph':
        """Create TaskGraph from dictionary."""
        graph = cls(
            spec_name=data["spec_name"],
            spec_path=data["spec_path"],
        )
        for tid, tdata in data.get("tasks", {}).items():
            task = Task(
                id=tdata["id"],
                description=tdata["description"],
                agent=tdata["agent"],
                status=tdata.get("status", "pending"),
                dependencies=tdata.get("dependencies", []),
                files_in_scope=tdata.get("files_in_scope", []),
                output=tdata.get("output"),
                attempts=tdata.get("attempts", 0),
                max_attempts=tdata.get("max_attempts", 3),
                wave=tdata.get("wave", 1),
            )
            graph.tasks[tid] = task
        return graph


def get_perspectives_path(project_path: Path = None) -> Path:
    """Get path to perspectives.md file."""
    project_path = project_path or Path.cwd()
    return project_path / ".enki" / "perspectives.md"


def get_specs_dir(project_path: Path = None) -> Path:
    """Get path to specs directory."""
    project_path = project_path or Path.cwd()
    specs_dir = project_path / ".enki" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    return specs_dir


def generate_perspectives(
    goal: str,
    context: str = None,
    project_path: Path = None,
) -> str:
    """Generate perspectives.md template for debate phase.

    Creates a template with all perspectives that need to be filled out
    before proceeding to planning.

    Args:
        goal: The feature/change being debated
        context: Additional context about the change
        project_path: Project directory path

    Returns:
        Path to generated perspectives.md
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    content = [
        f"# Debate: {goal}",
        "",
        f"**Created**: {datetime.now().isoformat()}",
        f"**Status**: In Progress",
        "",
        "---",
        "",
        "## Context",
        "",
        context or "(Describe the context and background here)",
        "",
        "---",
        "",
        "## Perspectives Required",
        "",
        "Complete ALL sections below before proceeding to /plan.",
        "",
    ]

    for perspective in PERSPECTIVES:
        content.append(PERSPECTIVE_PROMPTS[perspective])
        content.append("")
        content.append("**Analysis:**")
        content.append("(Fill in your analysis here)")
        content.append("")
        content.append("---")
        content.append("")

    content.extend([
        "## Summary",
        "",
        "### Key Concerns",
        "- ",
        "",
        "### Agreed Approach",
        "- ",
        "",
        "### Deferred Decisions",
        "- ",
        "",
        "---",
        "",
        "**Gate Check**: All perspectives must be completed before /plan.",
    ])

    perspectives_path = get_perspectives_path(project_path)
    perspectives_path.write_text("\n".join(content))

    # Log to RUNNING.md
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        with open(running_path, "a") as f:
            f.write(f"\n[{datetime.now().strftime('%H:%M')}] DEBATE STARTED: {goal}\n")

    return str(perspectives_path)


def check_perspectives_complete(project_path: Path = None) -> tuple:
    """Check if all perspectives are filled in.

    Returns:
        Tuple of (is_complete: bool, missing: list)
    """
    project_path = project_path or Path.cwd()
    perspectives_path = get_perspectives_path(project_path)

    if not perspectives_path.exists():
        return False, ["perspectives.md does not exist - run /debate first"]

    content = perspectives_path.read_text()
    missing = []

    for perspective in PERSPECTIVES:
        # Check if the analysis section has content beyond the placeholder
        pattern = f"### {perspective} Perspective.*?\\*\\*Analysis:\\*\\*\\s*(.+?)(?=###|---|\n## |$)"
        if perspective == "Devil's Advocate":
            pattern = f"### Devil's Advocate.*?\\*\\*Analysis:\\*\\*\\s*(.+?)(?=###|---|\n## |$)"

        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if not match:
            missing.append(perspective)
        else:
            analysis = match.group(1).strip()
            if not analysis or analysis == "(Fill in your analysis here)":
                missing.append(perspective)

    return len(missing) == 0, missing


def create_spec(
    name: str,
    problem: str = None,
    solution: str = None,
    project_path: Path = None,
) -> str:
    """Create a new spec from template.

    Args:
        name: Spec name (will be used as filename)
        problem: Problem statement
        solution: Proposed solution
        project_path: Project directory path

    Returns:
        Path to created spec file
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    # Check if debate is complete
    is_complete, missing = check_perspectives_complete(project_path)
    if not is_complete:
        raise ValueError(
            f"Cannot create spec: perspectives not complete. "
            f"Missing: {', '.join(missing)}. Run /debate first."
        )

    specs_dir = get_specs_dir(project_path)

    # Sanitize name for filename
    safe_name = re.sub(r'[^\w\-]', '-', name.lower())
    spec_path = specs_dir / f"{safe_name}.md"

    # Fill in template
    content = SPEC_TEMPLATE.format(
        name=name,
        problem_statement=problem or "(Describe the problem)",
        proposed_solution=solution or "(Describe the solution)",
        success_criteria="Criterion 1",
        components="Component 1",
        api_changes="None",
        data_changes="None",
        dependencies="None",
        task_1="Design task",
        agent_1="Architect",
        files_1="docs/design.md",
        task_2="Implement task",
        agent_2="Dev",
        files_2="src/module.py",
        task_3="Test task",
        agent_3="QA",
        files_3="tests/test_module.py",
        risk="Risk 1",
        likelihood="Medium",
        impact="Medium",
        mitigation="Mitigation 1",
        question_1="Question 1",
    )

    spec_path.write_text(content)

    # Log to RUNNING.md
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        with open(running_path, "a") as f:
            f.write(f"\n[{datetime.now().strftime('%H:%M')}] SPEC CREATED: {name}\n")

    # Transition to plan phase
    set_phase("plan", project_path)

    return str(spec_path)


def get_spec(name: str, project_path: Path = None) -> Optional[str]:
    """Get spec content by name.

    Args:
        name: Spec name
        project_path: Project directory path

    Returns:
        Spec content or None if not found
    """
    project_path = project_path or Path.cwd()
    specs_dir = get_specs_dir(project_path)

    # Try exact match first
    safe_name = re.sub(r'[^\w\-]', '-', name.lower())
    spec_path = specs_dir / f"{safe_name}.md"

    if spec_path.exists():
        return spec_path.read_text()

    # Try glob match
    matches = list(specs_dir.glob(f"*{safe_name}*.md"))
    if matches:
        return matches[0].read_text()

    return None


def list_specs(project_path: Path = None) -> list:
    """List all specs in project.

    Args:
        project_path: Project directory path

    Returns:
        List of spec names
    """
    project_path = project_path or Path.cwd()
    specs_dir = get_specs_dir(project_path)

    specs = []
    for spec_file in specs_dir.glob("*.md"):
        specs.append({
            "name": spec_file.stem,
            "path": str(spec_file),
            "approved": is_spec_approved(spec_file.stem, project_path),
        })

    return specs


def is_spec_approved(name: str, project_path: Path = None) -> bool:
    """Check if a spec is approved.

    Args:
        name: Spec name
        project_path: Project directory path

    Returns:
        True if approved
    """
    project_path = project_path or Path.cwd()
    running_path = project_path / ".enki" / "RUNNING.md"

    if not running_path.exists():
        return False

    content = running_path.read_text()
    safe_name = re.sub(r'[^\w\-]', '-', name.lower())

    # Check for approval marker
    return f"SPEC APPROVED: {safe_name}" in content or f"SPEC APPROVED: {name}" in content


def approve_spec(name: str, project_path: Path = None) -> bool:
    """Approve a spec.

    Args:
        name: Spec name to approve
        project_path: Project directory path

    Returns:
        True if successfully approved
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    specs_dir = get_specs_dir(project_path)
    safe_name = re.sub(r'[^\w\-]', '-', name.lower())
    spec_path = specs_dir / f"{safe_name}.md"

    if not spec_path.exists():
        # Try glob match
        matches = list(specs_dir.glob(f"*{safe_name}*.md"))
        if not matches:
            raise ValueError(f"Spec not found: {name}")
        spec_path = matches[0]
        safe_name = spec_path.stem

    # Check if already approved
    if is_spec_approved(safe_name, project_path):
        return True

    # Log approval to RUNNING.md
    running_path = project_path / ".enki" / "RUNNING.md"
    with open(running_path, "a") as f:
        f.write(f"\n[{datetime.now().strftime('%H:%M')}] SPEC APPROVED: {safe_name}\n")

    # Transition to implement phase
    set_phase("implement", project_path)

    return True


def decompose_spec(
    name: str,
    project_path: Path = None,
) -> TaskGraph:
    """Decompose a spec into a task graph.

    Parses the spec file's Task Breakdown section to create
    a TaskGraph with proper dependencies.

    Args:
        name: Spec name
        project_path: Project directory path

    Returns:
        TaskGraph with tasks from the spec
    """
    project_path = project_path or Path.cwd()

    specs_dir = get_specs_dir(project_path)
    safe_name = re.sub(r'[^\w\-]', '-', name.lower())
    spec_path = specs_dir / f"{safe_name}.md"

    if not spec_path.exists():
        matches = list(specs_dir.glob(f"*{safe_name}*.md"))
        if not matches:
            raise ValueError(f"Spec not found: {name}")
        spec_path = matches[0]
        safe_name = spec_path.stem

    content = spec_path.read_text()

    graph = TaskGraph(
        spec_name=safe_name,
        spec_path=str(spec_path),
    )

    # Parse waves and tasks from spec
    # Look for ### Wave N: ... sections
    wave_pattern = r'### Wave (\d+):.*?\n\|.*?\|.*?\|.*?\|.*?\|\n\|[-\s|]+\|\n((?:\|.*?\|.*?\|.*?\|.*?\|\n)+)'

    wave_deps = {}  # wave_num -> list of previous wave task ids
    task_counter = 0

    for match in re.finditer(wave_pattern, content):
        wave_num = int(match.group(1))
        table_rows = match.group(2)

        # Parse table rows
        row_pattern = r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|'

        wave_task_ids = []
        for row_match in re.finditer(row_pattern, table_rows):
            task_desc = row_match.group(1).strip()
            agent = row_match.group(2).strip()
            deps_str = row_match.group(3).strip()
            files_str = row_match.group(4).strip()

            task_counter += 1
            task_id = f"task_{task_counter}"

            # Parse dependencies
            dependencies = []
            if deps_str != "-" and wave_num > 1:
                # Depend on all tasks from previous wave
                prev_wave = wave_num - 1
                if prev_wave in wave_deps:
                    dependencies = wave_deps[prev_wave]

            # Parse files
            files = [f.strip() for f in files_str.split(",") if f.strip()]

            task = Task(
                id=task_id,
                description=task_desc,
                agent=agent,
                dependencies=dependencies,
                files_in_scope=files,
                wave=wave_num,
            )
            graph.add_task(task)
            wave_task_ids.append(task_id)

        wave_deps[wave_num] = wave_task_ids

    # If no tasks parsed from spec, create default tasks
    if not graph.tasks:
        graph.add_task(Task(
            id="task_1",
            description="Design phase",
            agent="Architect",
            wave=1,
        ))
        graph.add_task(Task(
            id="task_2",
            description="Write tests",
            agent="QA",
            dependencies=["task_1"],
            wave=2,
        ))
        graph.add_task(Task(
            id="task_3",
            description="Implement",
            agent="Dev",
            dependencies=["task_2"],
            wave=3,
        ))

    return graph


def save_task_graph(graph: TaskGraph, project_path: Path = None):
    """Save task graph to STATE.md.

    Args:
        graph: TaskGraph to save
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    state_path = project_path / ".enki" / "STATE.md"

    # Build STATE.md content
    content = [
        f"# Enki Orchestration - {graph.spec_name}",
        "",
        f"**Status**: active",
        f"**Started**: {datetime.now().isoformat()}",
        f"**Spec**: {graph.spec_path}",
        "",
        "## Task Graph",
        "",
    ]

    # Group by wave
    waves = graph.get_waves()
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

    # Files in scope
    all_files = set()
    for task in graph.tasks.values():
        all_files.update(task.files_in_scope)

    if all_files:
        content.append("## Files in Scope")
        for f in sorted(all_files):
            content.append(f"- {f}")
        content.append("")

    # Add JSON state for programmatic access
    content.append("<!-- ENKI_STATE")
    content.append(json.dumps(graph.to_dict(), indent=2))
    content.append("-->")

    state_path.write_text("\n".join(content))


def load_task_graph(project_path: Path = None) -> Optional[TaskGraph]:
    """Load task graph from STATE.md.

    Args:
        project_path: Project directory path

    Returns:
        TaskGraph or None if not found
    """
    project_path = project_path or Path.cwd()
    state_path = project_path / ".enki" / "STATE.md"

    if not state_path.exists():
        return None

    content = state_path.read_text()

    # Extract JSON state
    match = re.search(r'<!-- ENKI_STATE\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        return TaskGraph.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def get_orchestration_status(project_path: Path = None) -> dict:
    """Get current orchestration status.

    Args:
        project_path: Project directory path

    Returns:
        Status dict with spec, tasks, progress
    """
    project_path = project_path or Path.cwd()

    graph = load_task_graph(project_path)
    if not graph:
        return {
            "active": False,
            "spec": None,
            "tasks": [],
            "progress": 0.0,
        }

    total = len(graph.tasks)
    completed = len([t for t in graph.tasks.values() if t.status == "complete"])
    failed = len([t for t in graph.tasks.values() if t.status == "failed"])

    return {
        "active": True,
        "spec": graph.spec_name,
        "spec_path": graph.spec_path,
        "tasks": [
            {
                "id": t.id,
                "description": t.description,
                "agent": t.agent,
                "status": t.status,
                "wave": t.wave,
            }
            for t in graph.tasks.values()
        ],
        "total": total,
        "completed": completed,
        "failed": failed,
        "progress": completed / total if total > 0 else 0.0,
        "ready_tasks": [t.id for t in graph.get_ready_tasks()],
    }
