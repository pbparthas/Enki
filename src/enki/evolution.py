"""Self-Evolution module for Enki.

Enki tracks her own patterns and evolves her enforcement over time.
She learns from violations, rework, and outcomes to improve.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import json
import re

from .db import get_db
from .session import ensure_project_enki_dir


@dataclass
class SelfCorrection:
    """A self-correction made by Enki."""
    id: str
    date: str
    pattern_type: str  # gate_bypass, shallow_check, missed_context, etc.
    description: str
    frequency: int  # How often this happened
    impact: str  # What went wrong as a result
    correction: str  # What Enki changed
    effective: Optional[bool] = None  # Did the correction work?
    status: str = "active"  # active, monitoring, effective, reverted

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "frequency": self.frequency,
            "impact": self.impact,
            "correction": self.correction,
            "effective": self.effective,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SelfCorrection':
        return cls(
            id=data["id"],
            date=data["date"],
            pattern_type=data["pattern_type"],
            description=data["description"],
            frequency=data.get("frequency", 0),
            impact=data.get("impact", ""),
            correction=data["correction"],
            effective=data.get("effective"),
            status=data.get("status", "active"),
        )


@dataclass
class GateAdjustment:
    """An adjustment to a gate's behavior."""
    gate: str  # phase, spec, tdd, scope
    adjustment_type: str  # tighten, loosen, add_check, remove_check
    description: str
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    active: bool = True


# Self-correction trigger thresholds
TRIGGER_THRESHOLDS = {
    "same_violation_count": 3,  # Same violation N+ times triggers analysis
    "rework_correlation": 2,  # N+ rework cases after skipped phase
    "stale_knowledge_hits": 10,  # N+ stale results suggest decay issues
}


def get_evolution_path(project_path: Path = None) -> Path:
    """Get path to local (per-project) EVOLUTION.md file.

    Backward-compatible alias for get_local_evolution_path.
    """
    return get_local_evolution_path(project_path)


def get_local_evolution_path(project_path: Path = None) -> Path:
    """Get per-project evolution path — written during sessions."""
    project_path = project_path or Path.cwd()
    return project_path / ".enki" / "EVOLUTION.md"


def get_global_evolution_path() -> Path:
    """Get cross-project evolution path — written by promotion only."""
    return Path.home() / ".enki" / "EVOLUTION.md"


def init_evolution_log(project_path: Path = None):
    """Initialize EVOLUTION.md if it doesn't exist."""
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    evolution_path = get_evolution_path(project_path)
    if not evolution_path.exists():
        content = """# Enki Self-Evolution Log

This file tracks Enki's self-corrections and evolution over time.
Enki analyzes her own patterns and adjusts her behavior to improve outcomes.

## Active Corrections

(No active corrections yet)

## Correction History

(No corrections yet)

## Gate Adjustments

(No adjustments yet)

<!-- ENKI_EVOLUTION
{
  "corrections": [],
  "adjustments": [],
  "last_review": null
}
-->
"""
        evolution_path.write_text(content)


def load_evolution_state(project_path: Path = None) -> dict:
    """Load evolution state from EVOLUTION.md.

    Args:
        project_path: Project directory path

    Returns:
        Dict with corrections, adjustments, last_review
    """
    project_path = project_path or Path.cwd()
    evolution_path = get_evolution_path(project_path)

    if not evolution_path.exists():
        init_evolution_log(project_path)
        return {"corrections": [], "adjustments": [], "last_review": None}

    content = evolution_path.read_text()

    # Extract JSON state
    match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        return {"corrections": [], "adjustments": [], "last_review": None}

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"corrections": [], "adjustments": [], "last_review": None}


def save_evolution_state(state: dict, project_path: Path = None):
    """Save evolution state to EVOLUTION.md.

    Args:
        state: Evolution state dict
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    evolution_path = get_evolution_path(project_path)

    # Build EVOLUTION.md content
    lines = [
        "# Enki Self-Evolution Log",
        "",
        "This file tracks Enki's self-corrections and evolution over time.",
        "Enki analyzes her own patterns and adjusts her behavior to improve outcomes.",
        "",
        "## Active Corrections",
        "",
    ]

    corrections = state.get("corrections", [])
    active_corrections = [c for c in corrections if c.get("status") == "active"]

    if active_corrections:
        for c in active_corrections:
            lines.append(f"### {c['date']}: {c['description'][:50]}")
            lines.append(f"**Pattern Detected**: {c['pattern_type']}")
            lines.append(f"**Frequency**: {c['frequency']} occurrences")
            lines.append(f"**Impact**: {c['impact']}")
            lines.append(f"**Correction**: {c['correction']}")
            lines.append(f"**Status**: {c['status']}")
            lines.append("")
    else:
        lines.append("(No active corrections)")
        lines.append("")

    lines.append("## Correction History")
    lines.append("")

    historical = [c for c in corrections if c.get("status") != "active"]
    if historical:
        for c in historical[-10:]:  # Last 10
            effective = "✓" if c.get("effective") else "✗" if c.get("effective") is False else "?"
            lines.append(f"- [{effective}] {c['date']}: {c['description'][:50]} ({c['status']})")
        lines.append("")
    else:
        lines.append("(No corrections yet)")
        lines.append("")

    lines.append("## Gate Adjustments")
    lines.append("")

    adjustments = state.get("adjustments", [])
    if adjustments:
        lines.append("| Gate | Type | Description | Active |")
        lines.append("|------|------|-------------|--------|")
        for a in adjustments[-10:]:
            active = "Yes" if a.get("active", True) else "No"
            lines.append(f"| {a['gate']} | {a['adjustment_type']} | {a['description'][:30]} | {active} |")
        lines.append("")
    else:
        lines.append("(No adjustments yet)")
        lines.append("")

    # Add JSON state
    lines.append("<!-- ENKI_EVOLUTION")
    lines.append(json.dumps(state, indent=2))
    lines.append("-->")

    evolution_path.write_text("\n".join(lines))


# === Violation Pattern Analysis ===

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

    except Exception:
        pass

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

    except Exception:
        pass

    return patterns


def find_rework_correlation(days: int = 30, project_path: Path = None) -> list:
    """Find correlation between skipped phases and rework.

    Args:
        days: Number of days to look back
        project_path: Project directory path

    Returns:
        List of correlations
    """
    # This would ideally track bugs/rework after skipped phases
    # For now, check violations followed by more violations
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

    except Exception:
        pass

    return correlations


# === Self-Correction Logic ===

def check_correction_triggers(project_path: Path = None) -> list:
    """Check for conditions that trigger self-correction.

    Args:
        project_path: Project directory path

    Returns:
        List of triggered corrections with reasons
    """
    triggers = []

    # 1. Check for repeated violations
    violation_patterns = analyze_violation_patterns(days=7, project_path=project_path)
    for pattern in violation_patterns:
        if pattern["total"] >= TRIGGER_THRESHOLDS["same_violation_count"]:
            triggers.append({
                "trigger": "repeated_violations",
                "gate": pattern["gate"],
                "count": pattern["total"],
                "suggestion": f"Gate '{pattern['gate']}' violated {pattern['total']} times. Consider gate adjustment.",
            })

    # 2. Check for escalation patterns
    escalation_patterns = analyze_escalation_patterns(days=30, project_path=project_path)
    for pattern in escalation_patterns:
        if pattern["count"] >= 2:
            triggers.append({
                "trigger": "escalation_pattern",
                "goal_pattern": pattern["goal_pattern"],
                "count": pattern["count"],
                "suggestion": f"Goals matching '{pattern['goal_pattern'][:30]}' escalate frequently. Require spec upfront.",
            })

    # 3. Check for rework correlation
    correlations = find_rework_correlation(days=30, project_path=project_path)
    for corr in correlations:
        if corr["subsequent_violations"] >= TRIGGER_THRESHOLDS["rework_correlation"]:
            triggers.append({
                "trigger": "rework_correlation",
                "gate": corr["gate"],
                "subsequent": corr["subsequent_violations"],
                "suggestion": corr["suggests"],
            })

    return triggers


def create_self_correction(
    pattern_type: str,
    description: str,
    frequency: int,
    impact: str,
    correction: str,
    project_path: Path = None,
) -> SelfCorrection:
    """Create and record a self-correction.

    Args:
        pattern_type: Type of pattern detected
        description: Description of the pattern
        frequency: How often it occurred
        impact: What went wrong
        correction: What Enki is changing
        project_path: Project directory path

    Returns:
        The created SelfCorrection
    """
    project_path = project_path or Path.cwd()

    correction_obj = SelfCorrection(
        id=f"corr_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        date=datetime.now().strftime("%Y-%m-%d"),
        pattern_type=pattern_type,
        description=description,
        frequency=frequency,
        impact=impact,
        correction=correction,
    )

    # Load and update state
    state = load_evolution_state(project_path)
    state["corrections"].append(correction_obj.to_dict())
    save_evolution_state(state, project_path)

    # Log to database
    db = get_db()
    if db:
        try:
            db.execute("""
                INSERT INTO enki_self_analysis
                (pattern_type, description, frequency, impact, correction, effective)
                VALUES (?, ?, ?, ?, ?, NULL)
            """, (pattern_type, description, frequency, impact, correction))
            db.commit()
        except Exception:
            pass

    return correction_obj


def add_gate_adjustment(
    gate: str,
    adjustment_type: str,
    description: str,
    reason: str,
    project_path: Path = None,
) -> GateAdjustment:
    """Add a gate adjustment.

    Args:
        gate: Gate name (phase, spec, tdd, scope)
        adjustment_type: Type of adjustment (tighten, loosen, add_check, remove_check)
        description: What the adjustment does
        reason: Why it was made
        project_path: Project directory path

    Returns:
        The created GateAdjustment
    """
    project_path = project_path or Path.cwd()

    adjustment = GateAdjustment(
        gate=gate,
        adjustment_type=adjustment_type,
        description=description,
        reason=reason,
    )

    # Load and update state
    state = load_evolution_state(project_path)
    state["adjustments"].append({
        "gate": adjustment.gate,
        "adjustment_type": adjustment.adjustment_type,
        "description": adjustment.description,
        "reason": adjustment.reason,
        "created_at": adjustment.created_at,
        "active": adjustment.active,
    })
    save_evolution_state(state, project_path)

    return adjustment


def mark_correction_effective(correction_id: str, effective: bool, project_path: Path = None):
    """Mark a correction as effective or not.

    Args:
        correction_id: Correction ID
        effective: Whether it was effective
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()

    state = load_evolution_state(project_path)

    for c in state.get("corrections", []):
        if c.get("id") == correction_id:
            c["effective"] = effective
            c["status"] = "effective" if effective else "reverted"
            break

    save_evolution_state(state, project_path)


# === Weekly Self-Review ===

def run_weekly_self_review(project_path: Path = None) -> dict:
    """Run Enki's weekly self-review.

    Analyzes patterns from the past week and generates corrections.

    Args:
        project_path: Project directory path

    Returns:
        Review report dict
    """
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


def get_last_review_date(project_path: Path = None) -> Optional[str]:
    """Get the date of the last self-review.

    Args:
        project_path: Project directory path

    Returns:
        ISO date string or None
    """
    state = load_evolution_state(project_path)
    return state.get("last_review")


def is_review_due(project_path: Path = None, days: int = 7) -> bool:
    """Check if a self-review is due.

    Args:
        project_path: Project directory path
        days: Days between reviews

    Returns:
        True if review is due
    """
    last_review = get_last_review_date(project_path)
    if not last_review:
        return True

    try:
        last_date = datetime.fromisoformat(last_review)
        return datetime.now() - last_date > timedelta(days=days)
    except ValueError:
        return True


# === Self-Awareness Queries ===

def explain_block(
    gate: str,
    reason: str,
    project_path: Path = None,
) -> str:
    """Explain why Enki blocked an action.

    Args:
        gate: Gate that blocked
        reason: Original block reason
        project_path: Project directory path

    Returns:
        Detailed explanation
    """
    project_path = project_path or Path.cwd()

    explanation = [
        f"**Gate**: {gate}",
        f"**Reason**: {reason}",
        "",
    ]

    # Check for self-corrections related to this gate
    state = load_evolution_state(project_path)
    related_corrections = [
        c for c in state.get("corrections", [])
        if gate.lower() in c.get("description", "").lower()
        or gate.lower() in c.get("correction", "").lower()
    ]

    if related_corrections:
        explanation.append("**Recent Self-Corrections**:")
        for c in related_corrections[-2:]:
            explanation.append(f"- {c['date']}: {c['correction']}")
        explanation.append("")

    # Check for related gate adjustments
    related_adjustments = [
        a for a in state.get("adjustments", [])
        if a.get("gate") == gate
    ]

    if related_adjustments:
        explanation.append("**Gate Adjustments**:")
        for a in related_adjustments[-2:]:
            explanation.append(f"- {a['adjustment_type']}: {a['description']} ({a['reason']})")
        explanation.append("")

    # Add context about violation history
    patterns = analyze_violation_patterns(days=30, project_path=project_path)
    gate_pattern = next((p for p in patterns if p.get("gate") == gate), None)

    if gate_pattern:
        explanation.append(f"**History**: This gate has blocked {gate_pattern['total']} times in the last 30 days.")
        if gate_pattern.get("reasons"):
            top_reason = gate_pattern["reasons"][0]
            explanation.append(f"  Most common reason: {top_reason['reason'][:50]}")

    return "\n".join(explanation)


def get_evolution_summary(project_path: Path = None) -> str:
    """Get a summary of Enki's evolution.

    Args:
        project_path: Project directory path

    Returns:
        Summary text
    """
    project_path = project_path or Path.cwd()
    state = load_evolution_state(project_path)

    corrections = state.get("corrections", [])
    adjustments = state.get("adjustments", [])
    last_review = state.get("last_review")

    lines = [
        "## Enki Evolution Summary",
        "",
        f"**Last Review**: {last_review or 'Never'}",
        f"**Total Corrections**: {len(corrections)}",
        f"**Active Corrections**: {len([c for c in corrections if c.get('status') == 'active'])}",
        f"**Gate Adjustments**: {len(adjustments)}",
        "",
    ]

    # Active corrections
    active = [c for c in corrections if c.get("status") == "active"]
    if active:
        lines.append("### Active Corrections")
        for c in active[-3:]:
            lines.append(f"- {c['description'][:50]}")
        lines.append("")

    # Effectiveness
    effective = [c for c in corrections if c.get("effective") is True]
    ineffective = [c for c in corrections if c.get("effective") is False]

    if effective or ineffective:
        total_evaluated = len(effective) + len(ineffective)
        rate = len(effective) / total_evaluated if total_evaluated > 0 else 0
        lines.append(f"**Correction Effectiveness**: {rate:.0%} ({len(effective)}/{total_evaluated})")
        lines.append("")

    return "\n".join(lines)


def get_self_awareness_response(question: str, project_path: Path = None) -> str:
    """Respond to self-awareness queries.

    Args:
        question: Question about Enki's behavior
        project_path: Project directory path

    Returns:
        Response text
    """
    project_path = project_path or Path.cwd()
    question_lower = question.lower()

    # "Why did you block that?"
    if "block" in question_lower or "why" in question_lower:
        patterns = analyze_violation_patterns(days=7, project_path=project_path)
        if patterns:
            top = patterns[0]
            return (
                f"I've been enforcing gates more strictly lately. "
                f"The {top['gate']} gate has blocked {top['total']} times this week. "
                f"This is because I detected patterns that led to rework."
            )

    # "You seem stricter"
    if "strict" in question_lower:
        state = load_evolution_state(project_path)
        corrections = state.get("corrections", [])
        active = [c for c in corrections if c.get("status") == "active"]

        if active:
            return (
                f"I am stricter. I have {len(active)} active corrections. "
                f"Recent analysis showed patterns that led to bugs or rework. "
                f"The data supports tighter enforcement."
            )
        return "I'm following the standard gates. No special strictness applied."

    # "Can you loosen..."
    if "loosen" in question_lower or "relax" in question_lower:
        return (
            "I can consider loosening gates, but let me show you the data first. "
            "Run 'enki evolution summary' to see the patterns I've detected. "
            "If the data supports it, I'll adjust."
        )

    # Default
    return get_evolution_summary(project_path)


# =============================================================================
# TWO-TIER EVOLUTION: LOCAL → PROMOTE → GLOBAL
# =============================================================================

def migrate_per_project_evolution(project_path: Path):
    """One-time migration: mark existing per-project EVOLUTION.md as local.

    Idempotent. Checks for a .migrated marker to avoid re-running.
    Does NOT merge into global — only promotion does that.

    Args:
        project_path: Project directory path
    """
    local_path = get_local_evolution_path(project_path)
    marker = local_path.parent / "EVOLUTION_MIGRATED"

    if marker.exists():
        return  # already migrated

    if local_path.exists():
        # Mark as migrated — local file stays, promotion handles the rest
        marker.write_text(datetime.now().isoformat())


def promote_to_global(project_path: Path) -> dict:
    """Promote applied/acknowledged proposals from local to global.

    MECHANICAL — no AI judgment. Rules:
    1. Only proposals with status 'applied' or 'acknowledged' qualify
    2. Dedup by (proposal_type, target) — don't re-promote existing entries
    3. Tag each promoted entry with source_project for traceability
    4. Only factual fields promote — no CC reasoning text ('reason' field excluded)

    Args:
        project_path: Project directory path

    Returns:
        {"promoted": int, "skipped_duplicate": int, "skipped_status": int}
    """
    result = {"promoted": 0, "skipped_duplicate": 0, "skipped_status": 0}

    local_state = load_evolution_state(project_path)
    global_path = get_global_evolution_path()
    global_path.parent.mkdir(parents=True, exist_ok=True)

    # Load or init global state
    if global_path.exists():
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        if match:
            try:
                global_state = json.loads(match.group(1))
            except json.JSONDecodeError:
                global_state = {"corrections": [], "adjustments": [], "last_review": None}
        else:
            global_state = {"corrections": [], "adjustments": [], "last_review": None}
    else:
        global_state = {"corrections": [], "adjustments": [], "last_review": None}

    # Build dedup key set from existing global entries
    existing_keys = set()
    for c in global_state.get("corrections", []):
        key = (c.get("pattern_type", ""), c.get("correction", ""))
        existing_keys.add(key)
    for a in global_state.get("adjustments", []):
        key = (a.get("gate", ""), a.get("adjustment_type", ""), a.get("description", ""))
        existing_keys.add(key)

    project_name = project_path.name if project_path else "unknown"

    # Promote corrections
    for c in local_state.get("corrections", []):
        status = c.get("status", "")
        if status not in ("effective", "active"):
            result["skipped_status"] += 1
            continue

        key = (c.get("pattern_type", ""), c.get("correction", ""))
        if key in existing_keys:
            result["skipped_duplicate"] += 1
            continue

        # Promote — exclude 'impact' (potentially CC's interpretation)
        promoted = {
            "id": c.get("id", ""),
            "date": c.get("date", ""),
            "pattern_type": c.get("pattern_type", ""),
            "description": c.get("description", ""),
            "frequency": c.get("frequency", 0),
            "correction": c.get("correction", ""),
            "effective": c.get("effective"),
            "status": c.get("status", ""),
            "source_project": project_name,
            "promoted_at": datetime.now().isoformat(),
        }
        global_state["corrections"].append(promoted)
        existing_keys.add(key)
        result["promoted"] += 1

    # Promote adjustments
    for a in local_state.get("adjustments", []):
        if not a.get("active", True):
            result["skipped_status"] += 1
            continue

        key = (a.get("gate", ""), a.get("adjustment_type", ""), a.get("description", ""))
        if key in existing_keys:
            result["skipped_duplicate"] += 1
            continue

        # Promote — exclude 'reason' (CC's rationale, fox problem)
        promoted = {
            "gate": a.get("gate", ""),
            "adjustment_type": a.get("adjustment_type", ""),
            "description": a.get("description", ""),
            "created_at": a.get("created_at", ""),
            "active": a.get("active", True),
            "source_project": project_name,
            "promoted_at": datetime.now().isoformat(),
        }
        global_state["adjustments"].append(promoted)
        existing_keys.add(key)
        result["promoted"] += 1

    # Save global state
    if result["promoted"] > 0:
        _save_evolution_to_path(global_state, global_path)

    return result


def get_evolution_context_for_session(project_path: Path) -> str:
    """Build evolution context from both local and global.

    Local takes precedence on conflicts (a gate might be correctly
    tight for project A but loose for project B).

    Args:
        project_path: Project directory path

    Returns:
        Formatted evolution context for session injection
    """
    local_state = load_evolution_state(project_path)
    global_path = get_global_evolution_path()

    if global_path.exists():
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        if match:
            try:
                global_state = json.loads(match.group(1))
            except json.JSONDecodeError:
                global_state = {"corrections": [], "adjustments": []}
        else:
            global_state = {"corrections": [], "adjustments": []}
    else:
        global_state = {"corrections": [], "adjustments": []}

    # Merge: local overrides global on same (type, target)
    merged = _merge_evolution_states(global_state, local_state)
    return _format_evolution_for_injection(merged)


def _merge_evolution_states(global_state: dict, local_state: dict) -> dict:
    """Merge global and local evolution states, local precedence.

    Args:
        global_state: Global evolution state
        local_state: Local (per-project) evolution state

    Returns:
        Merged state dict
    """
    merged = {
        "corrections": [],
        "adjustments": [],
        "last_review": local_state.get("last_review") or global_state.get("last_review"),
    }

    # For corrections: local overrides global by (pattern_type, correction)
    seen_corrections = set()

    # Local first (takes precedence)
    for c in local_state.get("corrections", []):
        key = (c.get("pattern_type", ""), c.get("correction", ""))
        if key not in seen_corrections:
            merged["corrections"].append(c)
            seen_corrections.add(key)

    # Then global (only if not overridden)
    for c in global_state.get("corrections", []):
        key = (c.get("pattern_type", ""), c.get("correction", ""))
        if key not in seen_corrections:
            merged["corrections"].append(c)
            seen_corrections.add(key)

    # For adjustments: local overrides global by (gate, adjustment_type)
    seen_adjustments = set()

    for a in local_state.get("adjustments", []):
        key = (a.get("gate", ""), a.get("adjustment_type", ""))
        if key not in seen_adjustments:
            merged["adjustments"].append(a)
            seen_adjustments.add(key)

    for a in global_state.get("adjustments", []):
        key = (a.get("gate", ""), a.get("adjustment_type", ""))
        if key not in seen_adjustments:
            merged["adjustments"].append(a)
            seen_adjustments.add(key)

    return merged


def _format_evolution_for_injection(state: dict) -> str:
    """Format merged evolution state for session injection.

    Args:
        state: Merged evolution state

    Returns:
        Human-readable summary
    """
    lines = []

    active_corrections = [
        c for c in state.get("corrections", [])
        if c.get("status") == "active"
    ]
    active_adjustments = [
        a for a in state.get("adjustments", [])
        if a.get("active", True)
    ]

    if active_corrections:
        lines.append("Active corrections:")
        for c in active_corrections[:5]:
            source = f" (from {c['source_project']})" if c.get("source_project") else ""
            lines.append(f"  - {c['description'][:60]}{source}")

    if active_adjustments:
        lines.append("Gate adjustments:")
        for a in active_adjustments[:5]:
            source = f" (from {a['source_project']})" if a.get("source_project") else ""
            lines.append(f"  - {a['gate']}: {a['adjustment_type']} — {a['description'][:40]}{source}")

    if not lines:
        return ""

    return "\n".join(lines)


def _save_evolution_to_path(state: dict, evolution_path: Path):
    """Save evolution state to a specific path.

    Args:
        state: Evolution state dict
        evolution_path: Path to EVOLUTION.md file
    """
    evolution_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Enki Self-Evolution Log",
        "",
        "This file tracks Enki's self-corrections and evolution over time.",
        "",
        "## Active Corrections",
        "",
    ]

    corrections = state.get("corrections", [])
    active_corrections = [c for c in corrections if c.get("status") == "active"]

    if active_corrections:
        for c in active_corrections:
            source = f" (from {c['source_project']})" if c.get("source_project") else ""
            lines.append(f"### {c.get('date', '?')}: {c.get('description', '')[:50]}{source}")
            lines.append(f"**Pattern**: {c.get('pattern_type', '')}")
            lines.append(f"**Correction**: {c.get('correction', '')}")
            lines.append(f"**Status**: {c.get('status', '')}")
            lines.append("")
    else:
        lines.append("(No active corrections)")
        lines.append("")

    lines.append("## Gate Adjustments")
    lines.append("")

    adjustments = state.get("adjustments", [])
    active = [a for a in adjustments if a.get("active", True)]
    if active:
        lines.append("| Gate | Type | Description | Source |")
        lines.append("|------|------|-------------|--------|")
        for a in active[-15:]:
            source = a.get("source_project", "")
            lines.append(f"| {a['gate']} | {a['adjustment_type']} | {a['description'][:30]} | {source} |")
        lines.append("")
    else:
        lines.append("(No adjustments)")
        lines.append("")

    # Embed JSON state
    lines.append("<!-- ENKI_EVOLUTION")
    lines.append(json.dumps(state, indent=2))
    lines.append("-->")

    evolution_path.write_text("\n".join(lines))


# =============================================================================
# PRUNING
# =============================================================================

def prune_local_evolution(project_path: Path):
    """Prune local (per-project) evolution state.

    - Keep last 30 corrections and 15 adjustments
    - Archive completed/reverted corrections older than 90 days

    Args:
        project_path: Project directory path
    """
    state = load_evolution_state(project_path)
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()

    corrections = state.get("corrections", [])
    adjustments = state.get("adjustments", [])

    # Separate active from archivable
    active = [c for c in corrections if c.get("status") == "active"]
    archivable = [
        c for c in corrections
        if c.get("status") in ("effective", "reverted")
        and c.get("date", "") < cutoff[:10]
    ]
    keep = [
        c for c in corrections
        if c not in archivable
    ]

    # Archive old corrections
    if archivable:
        archive_path = project_path / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            f.write(f"\n## Archived {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for c in archivable:
                f.write(f"- [{c.get('status')}] {c.get('date')}: {c.get('description', '')[:60]}\n")

    # Trim to limits
    state["corrections"] = keep[-30:]
    state["adjustments"] = adjustments[-15:]

    save_evolution_state(state, project_path)


def prune_global_evolution():
    """Prune global evolution state.

    - Archive reverted entries older than 180 days
    - Applied/acknowledged entries stay indefinitely
    """
    global_path = get_global_evolution_path()
    if not global_path.exists():
        return

    content = global_path.read_text()
    match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        return

    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return

    cutoff = (datetime.now() - timedelta(days=180)).isoformat()

    corrections = state.get("corrections", [])
    archivable = [
        c for c in corrections
        if c.get("status") == "reverted"
        and c.get("date", "") < cutoff[:10]
    ]

    if archivable:
        archive_path = Path.home() / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            f.write(f"\n## Global Archive {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for c in archivable:
                source = f" (from {c.get('source_project', '?')})" if c.get("source_project") else ""
                f.write(f"- [{c.get('status')}] {c.get('date')}: {c.get('description', '')[:60]}{source}\n")

        state["corrections"] = [c for c in corrections if c not in archivable]
        _save_evolution_to_path(state, global_path)
