"""tiers.py — Tier system + auto-detection + enki_quick.

Three tiers determine workflow complexity:
- Minimal: Config, typos, bug fixes. No DAG, single cycle.
- Standard: Medium features. Single sprint, task DAG.
- Full: New systems, large features. Multi-sprint, full planning.
"""

import re
import uuid
from datetime import datetime

from enki.db import em_db

# Heuristic signals for tier detection
MINIMAL_SIGNALS = [
    "fix", "typo", "config", "update", "bump", "rename",
    "refactor", "cleanup", "lint", "format", "comment",
    "readme", "docs", "changelog", "version",
]

FULL_SIGNALS = [
    "new system", "from scratch", "architecture", "redesign",
    "multi-module", "database migration", "api design",
    "authentication", "authorization", "multi-sprint",
]


def detect_tier(description: str) -> str:
    """Auto-detect tier from task description.

    Uses impact/complexity heuristics. Escalates on low confidence.
    """
    desc_lower = description.lower()
    score = 0

    # Check for minimal signals
    for signal in MINIMAL_SIGNALS:
        if signal in desc_lower:
            score -= 1

    # Check for full signals
    for signal in FULL_SIGNALS:
        if signal in desc_lower:
            score += 2

    # Word count heuristic (longer descriptions suggest more complexity)
    word_count = len(description.split())
    if word_count > 50:
        score += 1
    elif word_count < 15:
        score -= 1

    if score <= -1:
        return "minimal"
    elif score >= 2:
        return "full"
    else:
        return "standard"


def quick(description: str, project: str) -> dict:
    """Fast-path for Minimal tier. Combines goal + triage + phase.

    Sets goal, auto-triages as Minimal, jumps to implement phase.
    Gate 1 (goal) and Gate 3 (phase) are satisfied immediately.
    Gate 2 (spec) doesn't apply to Minimal tier.
    """
    detected_tier = detect_tier(description)

    if detected_tier != "minimal":
        return {
            "error": f"Auto-detected tier is '{detected_tier}', not minimal. "
                     "Use full workflow: enki_goal → enki_triage → enki_phase.",
            "detected_tier": detected_tier,
        }

    # Set goal
    _set_goal(project, description, tier="minimal")

    # Set phase to implement
    _set_phase(project, "implement")

    return {
        "goal": description,
        "tier": "minimal",
        "phase": "implement",
        "message": "Quick mode active. Edit files, then enki_phase('ship') when done.",
    }


def set_goal(project: str, description: str, tier: str = "auto") -> dict:
    """Set project goal and tier."""
    if tier == "auto":
        tier = detect_tier(description)

    _set_goal(project, description, tier)
    return {"goal": description, "tier": tier, "project": project}


def set_phase(project: str, phase: str) -> dict:
    """Set project phase."""
    valid_phases = ["intake", "debate", "plan", "implement", "review", "ship"]
    if phase not in valid_phases:
        return {"error": f"Invalid phase: {phase}. Must be one of {valid_phases}"}

    _set_phase(project, phase)
    return {"phase": phase, "project": project}


def get_project_state(project: str) -> dict:
    """Get current goal, tier, and phase for a project."""
    with em_db(project) as conn:
        goal_row = conn.execute(
            "SELECT task_name, tier FROM task_state "
            "WHERE work_type = 'goal' AND status != 'completed' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        phase_row = conn.execute(
            "SELECT task_name FROM task_state "
            "WHERE work_type = 'phase' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    return {
        "project": project,
        "goal": goal_row["task_name"] if goal_row else None,
        "tier": goal_row["tier"] if goal_row else None,
        "phase": phase_row["task_name"] if phase_row else None,
    }


def triage(description: str) -> dict:
    """Triage a task description. Returns tier + reasoning."""
    tier = detect_tier(description)
    return {
        "description": description,
        "tier": tier,
        "reasoning": _tier_reasoning(description, tier),
    }


# ── Private helpers ──


def _set_goal(project: str, description: str, tier: str) -> None:
    """Write goal to em.db."""
    task_id = str(uuid.uuid4())
    with em_db(project) as conn:
        # Mark previous goals as completed
        conn.execute(
            "UPDATE task_state SET status = 'completed', "
            "completed_at = datetime('now') "
            "WHERE project_id = ? AND work_type = 'goal' AND status != 'completed'",
            (project,),
        )
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, work_type, "
            "status, started_at) "
            "VALUES (?, ?, 'default', ?, ?, 'goal', 'active', datetime('now'))",
            (task_id, project, description, tier),
        )


def _set_phase(project: str, phase: str) -> None:
    """Write phase to em.db."""
    task_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO task_state "
            "(task_id, project_id, sprint_id, task_name, tier, work_type, "
            "status, started_at) "
            "VALUES (?, ?, 'default', ?, 'minimal', 'phase', 'active', "
            "datetime('now'))",
            (task_id, project, phase),
        )


def _tier_reasoning(description: str, tier: str) -> str:
    """Generate reasoning for tier selection."""
    if tier == "minimal":
        return "Small scope: likely a fix, config change, or minor update."
    elif tier == "full":
        return "Large scope: new system, architecture change, or multi-sprint work."
    else:
        return "Medium scope: feature work requiring planning and testing."
