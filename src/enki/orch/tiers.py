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
from enki.orch.pm import is_spec_approved

PHASE_ORDER = ["intake", "debate", "spec", "approve", "implement", "review", "complete"]
IMPLEMENT_PHASES = {"implement", "review", "complete"}

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
        "message": "Quick mode active. Edit files, then enki_phase(action='advance', to='complete') when done.",
    }


def set_goal(project: str, description: str, tier: str = "auto") -> dict:
    """Set project goal and tier."""
    if tier == "auto":
        tier = detect_tier(description)

    _set_goal(project, description, tier)

    # Set initial phase
    _set_phase(project, "intake")

    # Minimal tier: skip ceremony, auto-advance to implement
    if tier == "minimal":
        _set_phase(project, "implement")

    return {"goal": description, "tier": tier, "project": project}


def set_phase(project: str, phase: str) -> dict:
    """Set project phase."""
    if phase not in PHASE_ORDER:
        return {
            "error": f"Invalid phase: {phase}. Must be one of {PHASE_ORDER}"
        }

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
            "ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()

    return {
        "project": project,
        "goal": goal_row["task_name"] if goal_row else None,
        "tier": goal_row["tier"] if goal_row else None,
        "phase": phase_row["task_name"] if phase_row else None,
    }


def advance_phase(project: str, to_phase: str) -> dict:
    """Advance project phase. Enforces sequential progression — no skipping.

    Returns: {"success": True, "phase": "debate"} or {"success": False, "reason": "..."}
    """
    state = get_project_state(project)
    current = state.get("phase")

    if to_phase not in PHASE_ORDER:
        return {
            "success": False,
            "reason": f"Unknown phase: {to_phase}. Valid phases: {', '.join(PHASE_ORDER)}",
        }

    current_idx = PHASE_ORDER.index(current) if current in PHASE_ORDER else -1
    target_idx = PHASE_ORDER.index(to_phase)

    # Can only advance by 1 step
    if target_idx > current_idx + 1:
        next_phase = (
            PHASE_ORDER[current_idx + 1]
            if current_idx + 1 < len(PHASE_ORDER)
            else "complete"
        )
        return {
            "success": False,
            "reason": f"Cannot skip from '{current}' to '{to_phase}'. Next phase is '{next_phase}'.",
        }

    # Can't go backwards
    if target_idx < current_idx:
        return {
            "success": False,
            "reason": f"Cannot go backwards from '{current}' to '{to_phase}'.",
        }

    # "implement" requires human-approved spec for Standard/Full
    if to_phase == "implement":
        tier = state.get("tier") or "minimal"
        if tier in ("standard", "full"):
            if not is_spec_approved(project):
                return {
                    "success": False,
                    "reason": "Cannot enter implement phase. Spec requires human approval first.",
                }

    _set_phase(project, to_phase)
    return {"success": True, "phase": to_phase}


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
