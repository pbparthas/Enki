"""Evolution analytics — DB queries for violation/escalation pattern analysis.

P2-12: Split from evolution.py (SRP). Handles:
- Violation pattern analysis (grouped by gate)
- Escalation pattern detection
- Rework correlation
- Trigger threshold checking
- Weekly self-review orchestration
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from .db import get_db
from .evolution_store import load_evolution_state, save_evolution_state

logger = logging.getLogger(__name__)

# Self-correction trigger thresholds (P3-06: all analysis windows in one place)
TRIGGER_THRESHOLDS = {
    "same_violation_count": 3,  # Same violation N+ times triggers analysis
    "rework_correlation": 2,  # N+ rework cases after skipped phase
    "stale_knowledge_hits": 10,  # N+ stale results suggest decay issues
    "violation_window_days": 7,  # Default window for violation analysis
    "escalation_window_days": 30,  # Default window for escalation analysis
    "rework_window_days": 30,  # Default window for rework correlation
}


def analyze_violation_patterns(days: int = 7, project_path: Path = None) -> list:
    """Analyze violation patterns from the database.

    Args:
        days: Number of days to look back
        project_path: Project directory path

    Returns:
        List of pattern dicts with gate, count, common_reasons
    """
    db = get_db()
    if not db:
        return []

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Group violations by gate and reason
    patterns = []

    try:
        by_gate = db.execute("""
            SELECT gate, reason, COUNT(*) as count
            FROM violations
            WHERE timestamp > ?
            GROUP BY gate, reason
            ORDER BY count DESC
        """, (cutoff,)).fetchall()

        # Aggregate by gate
        gate_patterns = {}
        for row in by_gate:
            gate = row["gate"]
            if gate not in gate_patterns:
                gate_patterns[gate] = {
                    "gate": gate,
                    "total": 0,
                    "reasons": [],
                }
            gate_patterns[gate]["total"] += row["count"]
            gate_patterns[gate]["reasons"].append({
                "reason": row["reason"],
                "count": row["count"],
            })

        patterns = list(gate_patterns.values())

    except Exception as e:
        logger.warning("Non-fatal error in evolution (violation patterns): %s", e)

    return patterns


def analyze_escalation_patterns(days: int = 30, project_path: Path = None) -> list:
    """Analyze tier escalation patterns.

    Args:
        days: Number of days to look back
        project_path: Project directory path

    Returns:
        List of escalation patterns
    """
    db = get_db()
    if not db:
        return []

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    patterns = []

    try:
        # Find goals that frequently escalate
        escalations = db.execute("""
            SELECT initial_tier, final_tier, goal, COUNT(*) as count
            FROM tier_escalations
            WHERE created_at > ?
            GROUP BY initial_tier, final_tier, goal
            HAVING count >= 2
            ORDER BY count DESC
        """, (cutoff,)).fetchall()

        for row in escalations:
            patterns.append({
                "initial_tier": row["initial_tier"],
                "final_tier": row["final_tier"],
                "goal_pattern": row["goal"],
                "count": row["count"],
            })

    except Exception as e:
        logger.warning("Non-fatal error in evolution (escalation patterns): %s", e)

    return patterns


def find_rework_correlation(days: int = 30, project_path: Path = None) -> list:
    """Find correlation between skipped phases and rework.

    Args:
        days: Number of days to look back
        project_path: Project directory path

    Returns:
        List of correlations
    """
    db = get_db()
    if not db:
        return []

    correlations = []

    try:
        # Find sessions with violations that had subsequent violations
        results = db.execute("""
            SELECT v1.gate, COUNT(DISTINCT v2.id) as subsequent_violations
            FROM violations v1
            JOIN violations v2 ON v1.gate = v2.gate
                AND v2.timestamp > v1.timestamp
                AND v2.timestamp < datetime(v1.timestamp, '+1 hour')
            WHERE v1.timestamp > datetime('now', '-30 days')
            GROUP BY v1.gate
            HAVING subsequent_violations >= 2
        """).fetchall()

        for row in results:
            correlations.append({
                "gate": row["gate"],
                "subsequent_violations": row["subsequent_violations"],
                "suggests": "Pattern of repeated violations - gate may need adjustment",
            })

    except Exception as e:
        logger.warning("Non-fatal error in evolution (rework correlation): %s", e)

    return correlations


def check_correction_triggers(project_path: Path = None) -> list:
    """Check for conditions that trigger self-correction.

    Args:
        project_path: Project directory path

    Returns:
        List of triggered corrections with reasons
    """
    triggers = []

    # 1. Check for repeated violations
    viol_days = TRIGGER_THRESHOLDS["violation_window_days"]
    violation_patterns = analyze_violation_patterns(days=viol_days, project_path=project_path)
    for pattern in violation_patterns:
        if pattern["total"] >= TRIGGER_THRESHOLDS["same_violation_count"]:
            triggers.append({
                "trigger": "repeated_violations",
                "gate": pattern["gate"],
                "count": pattern["total"],
                "suggestion": f"Gate '{pattern['gate']}' violated {pattern['total']} times. Consider gate adjustment.",
            })

    # 2. Check for escalation patterns
    esc_days = TRIGGER_THRESHOLDS["escalation_window_days"]
    escalation_patterns = analyze_escalation_patterns(days=esc_days, project_path=project_path)
    for pattern in escalation_patterns:
        if pattern["count"] >= 2:
            triggers.append({
                "trigger": "escalation_pattern",
                "goal_pattern": pattern["goal_pattern"],
                "count": pattern["count"],
                "suggestion": f"Goals matching '{pattern['goal_pattern'][:30]}' escalate frequently. Require spec upfront.",
            })

    # 3. Check for rework correlation
    rework_days = TRIGGER_THRESHOLDS["rework_window_days"]
    correlations = find_rework_correlation(days=rework_days, project_path=project_path)
    for corr in correlations:
        if corr["subsequent_violations"] >= TRIGGER_THRESHOLDS["rework_correlation"]:
            triggers.append({
                "trigger": "rework_correlation",
                "gate": corr["gate"],
                "subsequent": corr["subsequent_violations"],
                "suggestion": corr["suggests"],
            })

    return triggers


def run_weekly_self_review(project_path: Path = None) -> dict:
    """Run Enki's weekly self-review.

    Analyzes patterns from the past week and generates corrections.
    Imports create_self_correction lazily to avoid circular imports.

    Args:
        project_path: Project directory path

    Returns:
        Review report dict
    """
    from .evolution_core import create_self_correction

    project_path = project_path or Path.cwd()

    report = {
        "date": datetime.now().isoformat(),
        "violation_patterns": [],
        "escalation_patterns": [],
        "triggers": [],
        "corrections_made": [],
        "recommendations": [],
    }

    # 1. Analyze violation patterns
    report["violation_patterns"] = analyze_violation_patterns(days=7, project_path=project_path)

    # 2. Analyze escalation patterns
    report["escalation_patterns"] = analyze_escalation_patterns(days=30, project_path=project_path)

    # 3. Check for correction triggers
    report["triggers"] = check_correction_triggers(project_path)

    # 4. Generate corrections for high-frequency patterns
    for trigger in report["triggers"]:
        if trigger["trigger"] == "repeated_violations" and trigger["count"] >= 5:
            correction = create_self_correction(
                pattern_type="gate_bypass",
                description=f"Frequent {trigger['gate']} violations",
                frequency=trigger["count"],
                impact="Process skipped repeatedly",
                correction=f"Tightening {trigger['gate']} gate checks",
                project_path=project_path,
            )
            report["corrections_made"].append(correction.to_dict())

        elif trigger["trigger"] == "escalation_pattern":
            report["recommendations"].append({
                "type": "watchlist",
                "description": f"Add '{trigger['goal_pattern'][:30]}' to watchlist - requires spec upfront",
            })

    # 5. Update last review timestamp
    state = load_evolution_state(project_path)
    state["last_review"] = datetime.now().isoformat()
    save_evolution_state(state, project_path)

    return report
