"""orchestrator.py — Core EM: spawn, route, advance, reconcile.

EM has no opinions. EM brokers, routes, spawns, tracks.
It does not modify specs or override agent output.
Structure enforces behavior.

Spawn authority:
- PM, EM: spawned by Enki (not each other)
- Architect, DBA: spawned by Enki at PM's request
- Dev, QA, Validator, Reviewer: spawned by EM
- InfoSec, UI/UX, DevOps, Performance, Researcher: EM (conditional)
"""

from enki.db import em_db
from enki.orch.task_graph import (
    TaskStatus,
    create_task,
    get_task,
    update_task_status,
    increment_retry,
    needs_hitl,
    get_next_wave,
    get_sprint_tasks,
    is_sprint_complete,
    detect_file_overlaps,
    create_sprint,
    get_sprint,
    update_sprint_status,
)
from enki.orch.mail import (
    create_thread,
    send,
    get_inbox,
    mark_read,
    count_unread,
)
from enki.orch.agents import (
    AgentRole,
    assemble_prompt,
    should_spawn,
    get_blind_wall_filter,
)
from enki.orch.tiers import (
    detect_tier,
    get_project_state,
    set_goal,
    set_phase,
)
from enki.orch.pm import is_spec_approved
from enki.orch.parsing import parse_agent_output, get_retry_prompt
from enki.orch.validation import validate_agent_output
from enki.orch.bugs import file_bug, has_blocking_bugs
from enki.orch.onboarding import detect_entry_point
from enki.config import get_config


MAX_RETRIES = 3


class Orchestrator:
    """Core Engineering Manager."""

    def __init__(self, project: str):
        self.project = project
        self._config = get_config()

    # ── Entry Point Dispatch ──

    def handle_project_start(self, description: str, signals: dict) -> dict:
        """Detect entry point, assign tier, begin appropriate flow."""
        entry_point = detect_entry_point(signals)
        tier = detect_tier(description)

        # Set goal
        set_goal(self.project, description, tier)

        return {
            "project": self.project,
            "entry_point": entry_point,
            "tier": tier,
            "next_step": self._next_step_for_entry(entry_point, tier),
        }

    def _next_step_for_entry(self, entry_point: str, tier: str) -> str:
        """Determine next step based on entry point and tier."""
        if tier == "minimal":
            return "enki_quick — skip to implement"
        if entry_point == "brownfield":
            return "Spawn Researcher for Codebase Profile, then PM intake"
        if entry_point == "mid_design":
            return "PM reviews existing artifacts, then Implementation Spec"
        return "PM intake (full): outcome, audience, constraints, scope, risks"

    # ── Agent Spawning ──

    def spawn_agent(
        self,
        agent_role: str,
        task_id: str,
        context: dict,
    ) -> dict:
        """Prepare agent spawn (prompt + context).

        Returns prompt and filtered context for Task tool.
        Does NOT actually spawn — caller (MCP tool) does that.
        """
        role = AgentRole(agent_role)
        blind_filter = get_blind_wall_filter(role)

        # Filter context through blind wall
        filtered_context = {}
        for key, value in context.items():
            if key not in blind_filter.get("exclude", set()):
                filtered_context[key] = value

        prompt = assemble_prompt(role, filtered_context)

        return {
            "agent": agent_role,
            "task_id": task_id,
            "prompt": prompt,
            "context": filtered_context,
        }

    def should_spawn_conditional(
        self,
        agent_role: str,
        scope: dict,
    ) -> bool:
        """Determine if conditional agent should spawn."""
        return should_spawn(AgentRole(agent_role), scope)

    # ── Mail Routing ──

    def process_agent_output(self, task_id: str, raw_output: str) -> dict:
        """Parse agent output, validate, extract messages, route mail.

        Returns processing result with next actions.
        """
        # Parse JSON from output
        parse_result = parse_agent_output(raw_output)
        if not parse_result["success"]:
            return {
                "status": "parse_error",
                "error": parse_result["error"],
                "retry_prompt": get_retry_prompt(1),
            }

        parsed = parse_result["parsed"]

        # Validate structure
        validation = validate_agent_output(raw_output)
        if not validation["valid"]:
            return {
                "status": "validation_error",
                "error": validation["error"],
                "parsed": parsed,
            }

        # Route mail messages
        messages_routed = 0
        for msg in parsed.get("messages", []):
            if msg.get("to") and msg.get("content"):
                thread_id = create_thread(self.project, "agent_output")
                send(
                    project=self.project,
                    thread_id=thread_id,
                    from_agent=parsed.get("agent", "Unknown"),
                    to_agent=msg["to"],
                    body=msg["content"],
                    subject=msg.get("subject", f"From {parsed.get('agent', 'Unknown')}"),
                    importance=msg.get("importance", "normal"),
                )
                messages_routed += 1

        # Update task status
        status = parsed.get("status", "DONE")
        if status == "DONE":
            update_task_status(self.project, task_id, TaskStatus.COMPLETED)
        elif status == "BLOCKED":
            update_task_status(self.project, task_id, TaskStatus.BLOCKED)
        elif status == "FAILED":
            self._handle_task_failure(task_id, parsed)

        # File bugs from concerns
        bugs_filed = 0
        for concern in parsed.get("concerns", []):
            if concern.get("severity") in ("high", "critical"):
                file_bug(
                    project=self.project,
                    title=concern.get("title", "Concern from agent"),
                    description=concern.get("content", ""),
                    filed_by=parsed.get("agent", "Unknown"),
                    priority="P1" if concern.get("severity") == "critical" else "P2",
                    task_id=task_id,
                )
                bugs_filed += 1

        return {
            "status": "processed",
            "agent": parsed.get("agent"),
            "task_status": status,
            "messages_routed": messages_routed,
            "bugs_filed": bugs_filed,
            "files_modified": parsed.get("files_modified", []),
            "files_created": parsed.get("files_created", []),
            "tests_run": parsed.get("tests_run", 0),
            "tests_passed": parsed.get("tests_passed", 0),
            "tests_failed": parsed.get("tests_failed", 0),
        }

    def _handle_task_failure(self, task_id: str, output: dict) -> None:
        """Handle failed task: retry or escalate."""
        retry_count = increment_retry(self.project, task_id)
        if needs_hitl(self.project, task_id):
            update_task_status(self.project, task_id, TaskStatus.HITL)
        else:
            update_task_status(self.project, task_id, TaskStatus.FAILED)

    # ── Task DAG & Execution ──

    def get_next_actions(self) -> list[dict]:
        """Get next tasks ready to spawn.

        Respects parallelism limits and blind wall.
        """
        state = get_project_state(self.project)
        if not state.get("goal"):
            return []

        # Get current sprint
        with em_db(self.project) as conn:
            sprint_row = conn.execute(
                "SELECT sprint_id FROM sprint_state "
                "WHERE project_id = ? AND status = 'active' "
                "ORDER BY started_at DESC LIMIT 1",
                (self.project,),
            ).fetchone()

        if not sprint_row:
            return []

        sprint_id = sprint_row["sprint_id"]
        wave = get_next_wave(self.project, sprint_id)

        actions = []
        for task in wave:
            actions.append({
                "task_id": task["task_id"],
                "agent": task.get("tier", "Dev"),
                "task_name": task["task_name"],
                "status": task["status"],
            })

        return actions

    def mark_task_done(self, task_id: str, output: dict) -> dict:
        """Mark task complete and trigger next wave."""
        update_task_status(self.project, task_id, TaskStatus.COMPLETED)

        # Check if sprint is complete
        with em_db(self.project) as conn:
            task_row = conn.execute(
                "SELECT sprint_id FROM task_state "
                "WHERE task_id = ? AND project_id = ?",
                (task_id, self.project),
            ).fetchone()

        sprint_id = task_row["sprint_id"] if task_row else None
        sprint_complete = (
            is_sprint_complete(self.project, sprint_id)
            if sprint_id
            else False
        )

        next_actions = self.get_next_actions()

        return {
            "task_id": task_id,
            "status": "complete",
            "sprint_complete": sprint_complete,
            "next_actions": next_actions,
        }

    def escalate_to_human(self, task_id: str, reason: str) -> str:
        """HITL escalation via mail."""
        thread_id = create_thread(self.project, "escalation")
        msg_id = send(
            project=self.project,
            thread_id=thread_id,
            from_agent="EM",
            to_agent="Human",
            body=f"Task {task_id} requires human intervention.\n\nReason: {reason}",
            subject=f"HITL Escalation: {reason}",
            importance="critical",
        )
        update_task_status(self.project, task_id, TaskStatus.HITL)
        return msg_id

    # ── Sprint Management ──

    def advance_sprint(self) -> dict:
        """Move to next sprint after current completes."""
        with em_db(self.project) as conn:
            current = conn.execute(
                "SELECT sprint_id FROM sprint_state "
                "WHERE project_id = ? AND status = 'active' "
                "ORDER BY started_at DESC LIMIT 1",
                (self.project,),
            ).fetchone()

        if not current:
            return {"error": "No active sprint"}

        current_id = current["sprint_id"]

        if not is_sprint_complete(self.project, current_id):
            return {"error": "Current sprint not complete"}

        if has_blocking_bugs(self.project):
            return {"error": "Blocking bugs (P0/P1) must be resolved first"}

        # Complete current sprint
        update_sprint_status(self.project, current_id, "completed")

        # Determine next sprint number
        try:
            next_num = int(current_id.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_num = 2

        next_id = f"sprint-{next_num}"
        create_sprint(self.project, next_id)

        return {
            "previous_sprint": current_id,
            "new_sprint": next_id,
            "status": "advanced",
        }

    # ── State Injection ──

    def inject_session_state(self) -> str:
        """Return state for post-compact injection."""
        state = get_project_state(self.project)
        if not state.get("goal"):
            return ""

        lines = [
            f"Active project: {self.project}",
            f"Goal: {state.get('goal', 'None')}",
            f"Tier: {state.get('tier', 'Unknown')}",
            f"Phase: {state.get('phase', 'Unknown')}",
        ]

        # Sprint status
        with em_db(self.project) as conn:
            sprints = conn.execute(
                "SELECT sprint_id, status FROM sprint_state "
                "WHERE project_id = ? ORDER BY started_at",
                (self.project,),
            ).fetchall()

            for sprint in sprints:
                tasks = conn.execute(
                    "SELECT task_name, status FROM task_state "
                    "WHERE project_id = ? AND sprint_id = ? "
                    "AND work_type = 'task' ORDER BY started_at",
                    (self.project, sprint["sprint_id"]),
                ).fetchall()

                task_summary = ", ".join(
                    f"{t['task_name']} ({t['status']})" for t in tasks
                )
                lines.append(
                    f"{sprint['sprint_id']} ({sprint['status']}): {task_summary or 'no tasks'}"
                )

        # Unread mail count
        unread = count_unread(self.project, "EM")
        if unread > 0:
            lines.append(f"Unread mail: {unread}")

        return "\n".join(lines)

    def reconcile_after_crash(self) -> dict:
        """Reconcile em.db task_state against mail thread.

        Mail wins on discrepancies.
        """
        reconciled = []

        with em_db(self.project) as conn:
            # Find tasks marked as running but with no recent mail
            running = conn.execute(
                "SELECT task_id, task_name FROM task_state "
                "WHERE project_id = ? AND status = 'active' "
                "AND work_type = 'task'",
                (self.project,),
            ).fetchall()

            for task in running:
                # Check if there's a completion message in mail
                completion = conn.execute(
                    "SELECT content FROM mail_messages "
                    "WHERE thread_id LIKE ? "
                    "AND content LIKE '%DONE%' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (f"%{task['task_id']}%",),
                ).fetchone()

                if completion:
                    conn.execute(
                        "UPDATE task_state SET status = 'completed', "
                        "completed_at = datetime('now') "
                        "WHERE task_id = ? AND project_id = ?",
                        (task["task_id"], self.project),
                    )
                    reconciled.append({
                        "task_id": task["task_id"],
                        "action": "marked_complete",
                        "reason": "completion message found in mail",
                    })

        return {"reconciled": reconciled, "count": len(reconciled)}

    # ── Tier-Specific Flows ──

    def minimal_flow(self, description: str) -> dict:
        """Single cycle: Dev → QA → done."""
        from enki.orch.tiers import quick

        result = quick(description, self.project)
        if "error" in result:
            return result

        sprint_id = "sprint-1"
        create_sprint(self.project, sprint_id)

        task_id = create_task(
            project=self.project,
            sprint_id=sprint_id,
            task_name=description,
            tier="minimal",
        )

        return {
            "flow": "minimal",
            "sprint_id": sprint_id,
            "task_id": task_id,
            "next": "Dev implements, then QA validates",
        }

    def standard_flow(self, spec_text: str) -> dict:
        """Single sprint, task DAG from spec."""
        if not is_spec_approved(self.project):
            return {"error": "Spec must be approved before standard flow"}

        set_phase(self.project, "implement")
        sprint_id = "sprint-1"
        create_sprint(self.project, sprint_id)

        return {
            "flow": "standard",
            "sprint_id": sprint_id,
            "next": "Decompose spec into tasks, then orchestrate",
        }

    def full_flow(self, description: str) -> dict:
        """Multi-sprint, full planning."""
        if not is_spec_approved(self.project):
            return {"error": "Spec must be approved before full flow"}

        return {
            "flow": "full",
            "next": "PM intake → debate → spec → approve → decompose → orchestrate",
        }

    # ── DAG Building (EM Spec §10) ──

    def build_dag(self, spec: dict) -> dict:
        """Parse Architect spec table format, create sprints + tasks in task_graph.

        Expected spec format:
        {
            "sprints": [
                {
                    "name": "Sprint 1: Core Auth",
                    "number": 1,
                    "dependencies": [],
                    "tasks": [
                        {
                            "name": "JWT middleware",
                            "files": ["src/auth.py"],
                            "dependencies": [],
                            "work_type": "implementation",
                        },
                        ...
                    ]
                },
                ...
            ]
        }

        Returns dict with created sprint_ids and task_ids.
        """
        sprints_data = spec.get("sprints", [])
        if not sprints_data:
            return {"error": "No sprints in spec"}

        state = get_project_state(self.project)
        tier = state.get("tier", "standard")

        created_sprints = []
        created_tasks = []
        task_name_to_id = {}  # Map task names to IDs for dependency resolution

        for sprint_data in sprints_data:
            sprint_num = sprint_data.get("number", len(created_sprints) + 1)
            sprint_deps = []

            # Resolve sprint dependencies
            for dep_name in sprint_data.get("dependencies", []):
                # Find the sprint_id for the dependency
                for cs in created_sprints:
                    if cs["name"] == dep_name:
                        sprint_deps.append(cs["sprint_id"])
                        break

            sprint_id = create_sprint(
                self.project,
                sprint_num,
                dependencies=sprint_deps,
            )
            created_sprints.append({
                "sprint_id": sprint_id,
                "name": sprint_data.get("name", f"Sprint {sprint_num}"),
                "number": sprint_num,
            })

            # Create tasks within sprint
            for task_data in sprint_data.get("tasks", []):
                # Resolve task dependencies (by name)
                task_deps = []
                for dep_name in task_data.get("dependencies", []):
                    if dep_name in task_name_to_id:
                        task_deps.append(task_name_to_id[dep_name])

                task_id = create_task(
                    project=self.project,
                    sprint_id=sprint_id,
                    task_name=task_data["name"],
                    tier=tier,
                    dependencies=task_deps,
                    assigned_files=task_data.get("files", []),
                    work_type=task_data.get("work_type"),
                )
                task_name_to_id[task_data["name"]] = task_id
                created_tasks.append({
                    "task_id": task_id,
                    "name": task_data["name"],
                    "sprint_id": sprint_id,
                })

        # Validate the DAG
        from enki.orch.task_graph import validate_dag, insert_dependency_for_overlap
        for sprint_info in created_sprints:
            # Auto-add dependencies for file overlaps
            overlap_deps = insert_dependency_for_overlap(
                self.project, sprint_info["sprint_id"]
            )

            # Validate
            validation = validate_dag(self.project, sprint_info["sprint_id"])
            if not validation["valid"]:
                return {
                    "error": "DAG validation failed",
                    "issues": validation["issues"],
                    "sprints_created": len(created_sprints),
                    "tasks_created": len(created_tasks),
                }

        # Activate first sprint
        if created_sprints:
            update_sprint_status(
                self.project, created_sprints[0]["sprint_id"], "active"
            )

        return {
            "status": "dag_built",
            "sprints": created_sprints,
            "tasks": created_tasks,
            "total_sprints": len(created_sprints),
            "total_tasks": len(created_tasks),
        }

    # ── Sprint Completion (EM Spec §14) ──

    def on_sprint_complete(self, sprint_id: str) -> dict:
        """Handle sprint completion: PM update + reviewer spawn + advance.

        Triggered when all tasks in a sprint are completed.

        1. Send PM status update via mail
        2. Spawn sprint-level Reviewer for cross-task consistency
        3. Check for blocking bugs
        4. Advance to next sprint
        """
        from enki.orch.validation import prepare_sprint_reviewer_context

        sprint = get_sprint(self.project, sprint_id)
        if not sprint:
            return {"error": f"Sprint {sprint_id} not found"}

        if not is_sprint_complete(self.project, sprint_id):
            return {"error": "Sprint not complete — tasks still pending"}

        actions_taken = []

        # 1. PM status update via mail
        pm_thread = create_thread(self.project, "status")
        sprint_tasks = get_sprint_tasks(self.project, sprint_id)
        task_summary = "\n".join(
            f"- {t['task_name']}: {t['status']}" for t in sprint_tasks
        )
        send(
            project=self.project,
            thread_id=pm_thread,
            from_agent="EM",
            to_agent="PM",
            body=f"Sprint {sprint['sprint_number']} complete.\n\n{task_summary}",
            subject=f"Sprint {sprint['sprint_number']} Complete",
            importance="high",
            sprint_id=sprint_id,
        )
        actions_taken.append("pm_status_update")

        # 2. Prepare sprint reviewer context
        reviewer_context = prepare_sprint_reviewer_context(
            self.project, sprint_id
        )
        actions_taken.append("sprint_reviewer_prepared")

        # 3. Check for blocking bugs
        blocking = has_blocking_bugs(self.project)
        if blocking:
            return {
                "status": "blocked",
                "sprint_id": sprint_id,
                "reason": "Blocking bugs (P0/P1) must be resolved first",
                "actions_taken": actions_taken,
                "reviewer_context": reviewer_context,
            }

        # 4. Advance sprint
        advance_result = self.advance_sprint()
        actions_taken.append("sprint_advanced")

        return {
            "status": "sprint_complete",
            "sprint_id": sprint_id,
            "actions_taken": actions_taken,
            "reviewer_context": reviewer_context,
            "advance_result": advance_result,
        }

    # ── Conditional Agent Spawning (EM Spec §8) ──

    def get_conditional_agents(self, scope: dict) -> list[str]:
        """Determine which conditional agents should spawn.

        Args:
            scope: Dict with 'files', 'keywords', 'spec_text', etc.

        Returns list of agent role names to spawn.
        """
        conditional_roles = [
            AgentRole.UI_UX,
            AgentRole.INFOSEC,
            AgentRole.PERFORMANCE,
            AgentRole.RESEARCHER,
        ]

        to_spawn = []
        for role in conditional_roles:
            if should_spawn(role, scope):
                to_spawn.append(role.value)

        return to_spawn

    # ── Entry Point Flow Dispatch (EM Spec §1, §3) ──

    def dispatch_entry_flow(
        self,
        description: str,
        signals: dict,
    ) -> dict:
        """Wire entry point flows based on detected context.

        Greenfield: full PM intake → spec → DAG
        Mid-design: validate artifacts → PM gaps → spec
        Brownfield: Researcher profile → PM scoped intake → constrained spec
        """
        start_result = self.handle_project_start(description, signals)
        entry_point = start_result["entry_point"]
        tier = start_result["tier"]

        if tier == "minimal":
            return self.minimal_flow(description)

        if entry_point == "greenfield":
            set_phase(self.project, "intake")
            return {
                **start_result,
                "flow": "greenfield",
                "actions": [
                    {"step": 1, "action": "PM intake (full)", "status": "pending"},
                    {"step": 2, "action": "PM writes Product Spec", "status": "pending"},
                    {"step": 3, "action": "Debate (3-perspective)", "status": "pending"},
                    {"step": 4, "action": "Human approves spec", "status": "pending"},
                    {"step": 5, "action": "Architect writes Impl Spec", "status": "pending"},
                    {"step": 6, "action": "EM builds DAG", "status": "pending"},
                ],
            }

        elif entry_point == "mid_design":
            set_phase(self.project, "intake")
            return {
                **start_result,
                "flow": "mid_design",
                "actions": [
                    {"step": 1, "action": "PM reviews existing artifacts", "status": "pending"},
                    {"step": 2, "action": "PM fills gaps in intake checklist", "status": "pending"},
                    {"step": 3, "action": "PM adopts artifacts as Product Spec", "status": "pending"},
                    {"step": 4, "action": "Architect writes Impl Spec", "status": "pending"},
                    {"step": 5, "action": "EM builds DAG", "status": "pending"},
                ],
            }

        elif entry_point == "brownfield":
            set_phase(self.project, "intake")
            return {
                **start_result,
                "flow": "brownfield",
                "actions": [
                    {"step": 1, "action": "Spawn Researcher for Codebase Profile", "status": "pending"},
                    {"step": 2, "action": "PM scoped intake (with profile)", "status": "pending"},
                    {"step": 3, "action": "Architect constrained Impl Spec", "status": "pending"},
                    {"step": 4, "action": "EM builds DAG", "status": "pending"},
                ],
            }

        return start_result

    # ── Reconciliation (EM Spec §14) ──

    def reconcile_state(self) -> dict:
        """Full state reconciliation on session restart (EM Spec §14).

        Verifies SQLite task_state against mail thread.
        Mail wins on discrepancies. Recovers from crash.
        """
        reconciled = []
        issues = []

        with em_db(self.project) as conn:
            # 1. Find tasks marked as running but with no recent activity
            running = conn.execute(
                "SELECT task_id, task_name, started_at FROM task_state "
                "WHERE project_id = ? AND status IN ('active', 'in_progress') "
                "AND work_type = 'task'",
                (self.project,),
            ).fetchall()

            for task in running:
                # Check mail for completion/failure messages
                completion = conn.execute(
                    "SELECT body FROM mail_messages "
                    "WHERE project_id = ? AND task_id = ? "
                    "AND body LIKE '%DONE%' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.project, task["task_id"]),
                ).fetchone()

                failure = conn.execute(
                    "SELECT body FROM mail_messages "
                    "WHERE project_id = ? AND task_id = ? "
                    "AND body LIKE '%FAILED%' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.project, task["task_id"]),
                ).fetchone()

                if completion:
                    update_task_status(
                        self.project, task["task_id"], TaskStatus.COMPLETED
                    )
                    reconciled.append({
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "action": "marked_complete",
                        "reason": "completion message found in mail",
                    })
                elif failure:
                    update_task_status(
                        self.project, task["task_id"], TaskStatus.FAILED
                    )
                    reconciled.append({
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "action": "marked_failed",
                        "reason": "failure message found in mail",
                    })
                else:
                    issues.append({
                        "task_id": task["task_id"],
                        "task_name": task["task_name"],
                        "issue": "Running with no mail — possible orphan",
                    })

            # 2. Check sprint consistency
            sprints = conn.execute(
                "SELECT sprint_id, status FROM sprint_state "
                "WHERE project_id = ? AND status = 'active'",
                (self.project,),
            ).fetchall()

            for sprint in sprints:
                if is_sprint_complete(self.project, sprint["sprint_id"]):
                    reconciled.append({
                        "sprint_id": sprint["sprint_id"],
                        "action": "sprint_marked_complete",
                        "reason": "all tasks done but sprint still active",
                    })

            # 3. Check for unread critical mail
            critical = conn.execute(
                "SELECT COUNT(*) FROM mail_messages "
                "WHERE project_id = ? AND importance = 'critical' "
                "AND status = 'unread'",
                (self.project,),
            ).fetchone()[0]

        return {
            "reconciled": reconciled,
            "issues": issues,
            "critical_unread": critical,
            "count": len(reconciled),
        }
