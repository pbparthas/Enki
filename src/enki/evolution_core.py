"""Evolution core logic — correction lifecycle, gate validation, self-awareness.

P2-12: Split from evolution.py (SRP). Handles:
- Data classes (SelfCorrection, GateAdjustment)
- Constants (IMMUTABLE_GATES)
- Correction create/approve/reject/mark-effective
- Gate adjustment with immutability enforcement
- Self-awareness queries (explain_block, summary, ask)
- Two-tier merge + format for session injection
- Review scheduling
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import get_db
from .evolution_store import (
    get_global_evolution_path,
    load_evolution_state,
    save_evolution_state,
)

logger = logging.getLogger(__name__)


# --- Data Classes ---


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
    status: str = "proposed"  # proposed, active, monitoring, effective, reverted

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

    _REQUIRED_KEYS = {"id", "date", "pattern_type", "description", "correction"}

    @classmethod
    def from_dict(cls, data: dict) -> 'SelfCorrection':
        missing = cls._REQUIRED_KEYS - data.keys()
        if missing:
            raise ValueError(f"SelfCorrection missing required keys: {missing}")
        return cls(
            id=data["id"],
            date=data["date"],
            pattern_type=data["pattern_type"],
            description=data["description"],
            frequency=data.get("frequency", 0),
            impact=data.get("impact", ""),
            correction=data["correction"],
            effective=data.get("effective"),
            status=data.get("status", "proposed"),
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


# Immutable gates — cannot be loosened via evolution or feedback loop.
# Mirrors NEVER_LOOSEN in feedback_loop.py.
IMMUTABLE_GATES = {"phase", "spec", "scope", "enforcement_integrity"}


# --- Correction Lifecycle ---


def create_self_correction(
    pattern_type: str,
    description: str,
    frequency: int,
    impact: str,
    correction: str,
    project_path: Path = None,
    caller: str = "system",
) -> SelfCorrection:
    """Create and record a self-correction.

    Args:
        pattern_type: Type of pattern detected
        description: Description of the pattern
        frequency: How often it occurred
        impact: What went wrong
        correction: What Enki is changing
        project_path: Project directory path
        caller: Who triggered this — 'human', 'system', or agent name (P3-16)

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
        except Exception as e:
            logger.warning("Non-fatal error in evolution (self_correction db log): %s", e)

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

    # Hard floor: immutable gates cannot be loosened
    if adjustment_type == "loosen" and gate in IMMUTABLE_GATES:
        logger.warning(
            f"BLOCKED: Cannot loosen immutable gate '{gate}'. "
            f"Immutable gates: {IMMUTABLE_GATES}"
        )
        raise ValueError(
            f"Cannot loosen immutable gate '{gate}'. "
            f"Gates {IMMUTABLE_GATES} have a hard floor and cannot be weakened."
        )

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


def approve_correction(correction_id: str, project_path: Path = None) -> bool:
    """Approve a proposed correction — moves it from proposed to active.

    Only human-approved corrections affect enforcement.

    Args:
        correction_id: Correction ID to approve
        project_path: Project directory path

    Returns:
        True if found and approved, False if not found or already active
    """
    project_path = project_path or Path.cwd()
    state = load_evolution_state(project_path)

    for c in state.get("corrections", []):
        if c.get("id") == correction_id and c.get("status") == "proposed":
            c["status"] = "active"
            c["approved_at"] = datetime.now().isoformat()
            save_evolution_state(state, project_path)
            return True

    return False


def reject_correction(correction_id: str, project_path: Path = None) -> bool:
    """Reject a proposed correction — marks it as rejected.

    Args:
        correction_id: Correction ID to reject
        project_path: Project directory path

    Returns:
        True if found and rejected, False if not found
    """
    project_path = project_path or Path.cwd()
    state = load_evolution_state(project_path)

    for c in state.get("corrections", []):
        if c.get("id") == correction_id and c.get("status") == "proposed":
            c["status"] = "rejected"
            c["rejected_at"] = datetime.now().isoformat()
            save_evolution_state(state, project_path)
            return True

    return False


# --- Review Scheduling ---


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


# --- Self-Awareness Queries ---


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
    from .evolution_analytics import analyze_violation_patterns

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
    from .evolution_analytics import analyze_violation_patterns

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


# --- Two-Tier: Merge & Format ---


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
