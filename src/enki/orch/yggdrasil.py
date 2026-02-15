"""yggdrasil.py — Project tracking (Enki's Jira + Confluence).

Living project documentation from inception to closure.
v3 stub: minimal version using em.db task_state + pm_decisions.
Full Yggdrasil design parked for later phase.
"""

from datetime import datetime

from enki.db import em_db


def create_project(
    project: str,
    name: str,
    goal: str,
    scope: str,
    tier: str,
) -> dict:
    """Create project entry. Stores as PM decision + goal in task_state."""
    from enki.orch.tiers import set_goal
    from enki.orch.pm import record_decision

    # Set goal (creates task_state entry)
    result = set_goal(project, goal, tier)

    # Record project creation as PM decision
    record_decision(
        project=project,
        decision_type="project_creation",
        proposed_action=f"Created project: {name}",
        context=f"Goal: {goal}\nScope: {scope}\nTier: {tier}",
    )

    return {
        "project": project,
        "name": name,
        "goal": goal,
        "tier": tier,
        "created": True,
    }


def add_specs_to_project(
    project: str,
    product_spec: str | None = None,
    impl_spec: str | None = None,
) -> dict:
    """Store specs as PM decisions."""
    from enki.orch.pm import create_spec

    stored = []
    if product_spec:
        create_spec(project, "product", product_spec)
        stored.append("product")
    if impl_spec:
        create_spec(project, "implementation", impl_spec)
        stored.append("implementation")

    return {"project": project, "specs_stored": stored}


def add_sprint_milestone(
    project: str,
    sprint_id: str,
    status: str,
    summary: str,
) -> None:
    """Track sprint completion as PM decision."""
    from enki.orch.pm import record_decision

    record_decision(
        project=project,
        decision_type="sprint_milestone",
        proposed_action=f"Sprint {sprint_id}: {status}",
        context=summary,
    )


def add_change_request(project: str, request: dict) -> str:
    """Track change request."""
    from enki.orch.pm import record_decision

    return record_decision(
        project=project,
        decision_type="change_request",
        proposed_action=request.get("description", ""),
        context=str(request),
    )


def get_project_history(project: str) -> dict:
    """Get full project history from em.db."""
    from enki.orch.tiers import get_project_state
    from enki.orch.pm import get_decisions
    from enki.orch.bugs import list_bugs

    state = get_project_state(project)
    decisions = get_decisions(project)
    bugs = list_bugs(project)

    with em_db(project) as conn:
        sprints = conn.execute(
            "SELECT * FROM sprint_state WHERE project_id = ? "
            "ORDER BY started_at",
            (project,),
        ).fetchall()

        tasks = conn.execute(
            "SELECT task_id, task_name, status, tier FROM task_state "
            "WHERE project_id = ? AND work_type = 'task' "
            "ORDER BY started_at",
            (project,),
        ).fetchall()

    return {
        "project": project,
        "state": state,
        "decisions": decisions,
        "bugs": bugs,
        "sprints": [dict(s) for s in sprints],
        "tasks": [dict(t) for t in tasks],
    }


def close_project(project: str, summary: dict) -> dict:
    """Final closure with summary."""
    from enki.orch.pm import record_decision

    record_decision(
        project=project,
        decision_type="project_closure",
        proposed_action="Project closed",
        context=str(summary),
    )

    # Mark goal as completed
    with em_db(project) as conn:
        conn.execute(
            "UPDATE task_state SET status = 'completed', "
            "completed_at = datetime('now') "
            "WHERE project_id = ? AND work_type = 'goal' "
            "AND status != 'completed'",
            (project,),
        )

    return {
        "project": project,
        "closed": True,
        "summary": summary,
    }


def raise_conflict(project: str, description: str) -> str:
    """EM raises dependency/conflict."""
    from enki.orch.pm import record_decision

    return record_decision(
        project=project,
        decision_type="conflict",
        proposed_action=description,
    )


def resolve_conflict(project: str, conflict_id: str, resolution: str) -> None:
    """PM resolves conflict."""
    with em_db(project) as conn:
        conn.execute(
            "UPDATE pm_decisions SET human_response = ? "
            "WHERE id = ? AND project_id = ?",
            (resolution, conflict_id, project),
        )
