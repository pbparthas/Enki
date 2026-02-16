"""github.py — GitHub Issues integration for Enki task tracking.

Bi-directional sync: Enki DAG tasks ↔ GitHub Issues.
Sprints map to milestones. Agent roles map to labels.

Entirely optional — Enki works without this.
Requires: `requests` library + GitHub token in enki.toml.
"""

import json
import logging

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None  # type: ignore[assignment]
    REQUESTS_AVAILABLE = False
    logger.info("requests not installed — GitHub integration disabled")


ROLE_LABELS = {
    "Dev": "dev",
    "QA": "qa",
    "Reviewer": "review",
    "Validator": "validator",
    "Researcher": "research",
}

API_BASE = "https://api.github.com"


def _get_github_config() -> dict | None:
    """Load GitHub config from enki.toml. Returns None if not configured."""
    from enki.config import get_config

    config = get_config()
    gh = config.get("integrations", {}).get("github", {})
    if not gh.get("token") or not gh.get("repo"):
        return None
    return gh


def _headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _check_available() -> dict | None:
    """Check if GitHub integration is available. Returns config or None."""
    if not REQUESTS_AVAILABLE:
        return None
    config = _get_github_config()
    if not config:
        return None
    return config


def sync_tasks_to_issues(project: str) -> dict:
    """Create GitHub Issues from DAG tasks.

    Reads tasks from em.db, creates issues for any that don't have
    a github_issue_number yet. Updates the task with the issue number.

    Returns: {"created": count, "skipped": count, "errors": list}
    """
    config = _check_available()
    if not config:
        return _unavailable_result()

    from enki.db import em_db

    repo = config["repo"]
    token = config["token"]

    stats = {"created": 0, "skipped": 0, "errors": []}

    with em_db(project) as conn:
        tasks = conn.execute(
            "SELECT task_id, task_name, status, tier, work_type, assigned_files "
            "FROM task_state WHERE work_type NOT IN ('goal', 'phase') "
            "ORDER BY started_at"
        ).fetchall()

    for task in tasks:
        task = dict(task)
        # Skip tasks that already have issues (tracked via agent_outputs JSON)
        outputs = {}
        try:
            with em_db(project) as conn:
                row = conn.execute(
                    "SELECT agent_outputs FROM task_state WHERE task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                if row and row["agent_outputs"]:
                    outputs = json.loads(row["agent_outputs"])
        except (json.JSONDecodeError, TypeError):
            pass

        if outputs.get("github_issue_number"):
            stats["skipped"] += 1
            continue

        # Create issue
        labels = []
        if task.get("work_type") in ROLE_LABELS:
            labels.append(ROLE_LABELS[task["work_type"]])
        labels.append(f"tier:{task.get('tier', 'standard')}")

        body = f"**Task ID:** `{task['task_id']}`\n"
        body += f"**Status:** {task.get('status', 'pending')}\n"
        if task.get("assigned_files"):
            body += f"**Files:** {task['assigned_files']}\n"
        body += f"\n_Synced from Enki project: {project}_"

        payload = {
            "title": task["task_name"],
            "body": body,
            "labels": labels,
        }

        try:
            resp = requests.post(
                f"{API_BASE}/repos/{repo}/issues",
                headers=_headers(token),
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            issue_number = resp.json()["number"]

            # Store issue number back in task
            outputs["github_issue_number"] = issue_number
            with em_db(project) as conn:
                conn.execute(
                    "UPDATE task_state SET agent_outputs = ? WHERE task_id = ?",
                    (json.dumps(outputs), task["task_id"]),
                )
            stats["created"] += 1
        except Exception as e:
            stats["errors"].append(f"Task {task['task_id']}: {e}")

    return stats


def sync_issues_to_tasks(project: str) -> dict:
    """Update task states from GitHub issue states.

    For tasks with a github_issue_number, check the issue state
    and update the task status accordingly.

    Returns: {"updated": count, "unchanged": count, "errors": list}
    """
    config = _check_available()
    if not config:
        return _unavailable_result()

    from enki.db import em_db

    repo = config["repo"]
    token = config["token"]

    stats = {"updated": 0, "unchanged": 0, "errors": []}

    with em_db(project) as conn:
        tasks = conn.execute(
            "SELECT task_id, task_name, status, agent_outputs "
            "FROM task_state WHERE agent_outputs IS NOT NULL"
        ).fetchall()

    for task in tasks:
        task = dict(task)
        try:
            outputs = json.loads(task.get("agent_outputs") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue

        issue_number = outputs.get("github_issue_number")
        if not issue_number:
            continue

        try:
            resp = requests.get(
                f"{API_BASE}/repos/{repo}/issues/{issue_number}",
                headers=_headers(token),
                timeout=10,
            )
            resp.raise_for_status()
            issue = resp.json()

            # Map GitHub state to Enki status
            gh_state = issue.get("state", "open")
            new_status = "completed" if gh_state == "closed" else "active"

            if new_status != task["status"]:
                with em_db(project) as conn:
                    conn.execute(
                        "UPDATE task_state SET status = ? WHERE task_id = ?",
                        (new_status, task["task_id"]),
                    )
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        except Exception as e:
            stats["errors"].append(f"Issue #{issue_number}: {e}")

    return stats


def create_milestone(project: str, sprint_id: str) -> dict:
    """Map an Enki sprint to a GitHub milestone.

    Returns: {"milestone_number": int} or error.
    """
    config = _check_available()
    if not config:
        return _unavailable_result()

    from enki.db import em_db

    repo = config["repo"]
    token = config["token"]

    with em_db(project) as conn:
        sprint = conn.execute(
            "SELECT sprint_id, sprint_number, status "
            "FROM sprint_state WHERE sprint_id = ?",
            (sprint_id,),
        ).fetchone()

    if not sprint:
        return {"error": f"Sprint not found: {sprint_id}"}

    sprint = dict(sprint)

    payload = {
        "title": f"Sprint {sprint['sprint_number']}",
        "state": "open" if sprint["status"] != "completed" else "closed",
        "description": f"Enki sprint {sprint_id} for project {project}",
    }

    try:
        resp = requests.post(
            f"{API_BASE}/repos/{repo}/milestones",
            headers=_headers(token),
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return {"milestone_number": resp.json()["number"]}
    except Exception as e:
        return {"error": str(e)}


def _unavailable_result() -> dict:
    """Standard result when GitHub integration is not available."""
    reasons = []
    if not REQUESTS_AVAILABLE:
        reasons.append("requests library not installed (pip install requests)")
    config = _get_github_config()
    if not config:
        reasons.append("GitHub not configured in enki.toml ([integrations.github] token/repo)")
    return {
        "skipped": True,
        "reason": "; ".join(reasons) if reasons else "GitHub integration not available",
    }
