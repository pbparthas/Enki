"""status.py — Status updates for human consumption.

Generates and sends high-level status updates based on triggers:
- Sprint completion
- Blocker/HITL escalation
- Bug filed
- Project milestones
"""

from datetime import datetime

from enki.db import em_db


# Triggers that warrant a status update
STATUS_TRIGGERS = {
    "sprint_complete",
    "blocker_escalation",
    "bug_filed",
    "spec_approved",
    "first_sprint_done",
    "project_complete",
    "hitl_required",
}


def generate_status_update(project: str) -> str:
    """Generate high-level status update.

    Includes sprint progress, bugs, decisions, blockers.
    """
    sections = []

    with em_db(project) as conn:
        # Sprint progress
        sprints = conn.execute(
            "SELECT sprint_id, status FROM sprint_state "
            "WHERE project_id = ? ORDER BY started_at DESC LIMIT 3",
            (project,),
        ).fetchall()

        if sprints:
            sprint_lines = []
            for s in sprints:
                tasks = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM task_state "
                    "WHERE project_id = ? AND sprint_id = ? "
                    "AND work_type = 'task' GROUP BY status",
                    (project, s["sprint_id"]),
                ).fetchall()
                task_summary = ", ".join(
                    f"{t['status']}: {t['cnt']}" for t in tasks
                )
                sprint_lines.append(
                    f"  Sprint {s['sprint_id']} ({s['status']}): {task_summary or 'no tasks'}"
                )
            sections.append("## Sprint Progress\n" + "\n".join(sprint_lines))

        # Open bugs
        bugs = conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM bugs "
            "WHERE project_id = ? AND status = 'open' "
            "GROUP BY priority ORDER BY priority",
            (project,),
        ).fetchall()

        if bugs:
            bug_lines = [f"  {b['priority']}: {b['cnt']}" for b in bugs]
            sections.append("## Open Bugs\n" + "\n".join(bug_lines))

        # Recent decisions
        decisions = conn.execute(
            "SELECT decision_type, proposed_action FROM pm_decisions "
            "WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
            (project,),
        ).fetchall()

        if decisions:
            dec_lines = [
                f"  - [{d['decision_type']}] {d['proposed_action']}"
                for d in decisions
            ]
            sections.append("## Recent Decisions\n" + "\n".join(dec_lines))

        # Blockers
        blocked_tasks = conn.execute(
            "SELECT task_name FROM task_state "
            "WHERE project_id = ? AND status = 'blocked' "
            "AND work_type = 'task'",
            (project,),
        ).fetchall()

        if blocked_tasks:
            blocker_lines = [f"  - {t['task_name']}" for t in blocked_tasks]
            sections.append("## Blockers\n" + "\n".join(blocker_lines))

    if not sections:
        return f"# Status: {project}\n\nNo activity recorded yet."

    return f"# Status: {project}\n\n" + "\n\n".join(sections)


def should_send_status(trigger: str) -> bool:
    """Check if this trigger warrants a status update."""
    return trigger in STATUS_TRIGGERS


def send_status_to_thread(project: str, status_text: str) -> str:
    """Store status update as mail message in a status thread."""
    from enki.orch.mail import create_thread, send

    thread_id = create_thread(project, "status")
    msg_id = send(
        project=project,
        thread_id=thread_id,
        from_agent="EM",
        to_agent="Human",
        body=status_text,
        subject=f"Status Update {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        importance="normal",
    )
    return msg_id


def get_sprint_summary(project: str, sprint_id: str) -> dict:
    """Get summary for a specific sprint."""
    with em_db(project) as conn:
        sprint = conn.execute(
            "SELECT * FROM sprint_state WHERE sprint_id = ? AND project_id = ?",
            (sprint_id, project),
        ).fetchone()

        if not sprint:
            return {"error": f"Sprint {sprint_id} not found"}

        tasks = conn.execute(
            "SELECT task_id, task_name, status, tier FROM task_state "
            "WHERE project_id = ? AND sprint_id = ? AND work_type = 'task' "
            "ORDER BY started_at",
            (project, sprint_id),
        ).fetchall()

        bugs = conn.execute(
            "SELECT id, title, priority, status FROM bugs "
            "WHERE project_id = ? AND sprint_id = ?",
            (project, sprint_id),
        ).fetchall()

    return {
        "sprint_id": sprint_id,
        "status": sprint["status"],
        "tasks": [dict(t) for t in tasks],
        "bugs": [dict(b) for b in bugs],
        "total_tasks": len(tasks),
        "completed": sum(1 for t in tasks if t["status"] == "completed"),
    }
