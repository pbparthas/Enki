"""
Reflector — Closes Enki's feedback loop.

Adapted from ACE (Agentic Context Engine) pattern:
  Agent → Reflector → SkillManager → Loop

Enki's version:
  Session execution → Reflect → Distill → Store beads → Next session gets smarter

The Reflector analyzes what actually happened during a session:
- What gates fired and why
- What strategies Claude used (and whether they worked)
- What patterns emerged (scope creep, phase skipping, etc.)
- What knowledge was surfaced but ignored vs. used

The SkillManager distills reflections into atomic, reusable beads
and deduplicates against existing knowledge.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import json

logger = logging.getLogger(__name__)

from .db import get_db
from .beads import create_bead, Bead
from .search import search
from .session import get_session, get_session_id, get_phase, get_goal
from .violations import get_violations, get_escalations


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ExecutionTrace:
    """Everything that happened during a session."""
    session_id: str
    project: str
    goal: Optional[str]
    phase_start: str
    phase_end: str
    tier_start: str
    tier_end: str
    files_edited: list[str] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)
    interceptions: list[dict] = field(default_factory=list)
    beads_accessed: list[dict] = field(default_factory=list)
    running_log: str = ""
    duration_minutes: Optional[float] = None


@dataclass
class Reflection:
    """A single reflection from session analysis."""
    category: str  # "worked", "failed", "pattern", "strategy", "warning"
    description: str
    evidence: str  # What data supports this
    confidence: float  # 0.0 - 1.0
    actionable: bool  # Can this become a skill/bead?
    suggested_bead_type: Optional[str] = None  # decision, solution, learning, pattern


@dataclass
class Skill:
    """A distilled, reusable piece of knowledge."""
    content: str
    bead_type: str
    tags: list[str]
    source_reflections: list[str]  # Which reflections generated this
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None  # Existing bead ID if duplicate


# =============================================================================
# STEP 1: GATHER EXECUTION TRACE
# =============================================================================

def gather_execution_trace(project_path: Path = None) -> ExecutionTrace:
    """Gather everything that happened during the current session.

    Args:
        project_path: Project directory path

    Returns:
        ExecutionTrace with all session data
    """
    project_path = project_path or Path.cwd()
    session = get_session(project_path)

    if not session:
        return ExecutionTrace(
            session_id="unknown",
            project=project_path.name,
            goal=None,
            phase_start="unknown",
            phase_end="unknown",
            tier_start="unknown",
            tier_end="unknown",
        )

    trace = ExecutionTrace(
        session_id=session.session_id,
        project=project_path.name,
        goal=session.goal,
        phase_start=session.phase,  # Will be current phase
        phase_end=session.phase,
        tier_start=session.tier,
        tier_end=session.tier,
        files_edited=list(session.edits) if session.edits else [],
    )

    # Get violations for this session
    db = get_db()
    try:
        violations = db.execute(
            "SELECT * FROM violations WHERE session_id = ? ORDER BY timestamp",
            (session.session_id,),
        ).fetchall()
        trace.violations = [dict(v) for v in violations]
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Get escalations for this session
    try:
        escalations = db.execute(
            "SELECT * FROM tier_escalations WHERE session_id = ? ORDER BY created_at",
            (session.session_id,),
        ).fetchall()
        trace.escalations = [dict(e) for e in escalations]

        # Track tier progression
        if escalations:
            trace.tier_start = escalations[0]["initial_tier"]
            trace.tier_end = escalations[-1]["final_tier"]
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Get interceptions for this session
    try:
        interceptions = db.execute(
            "SELECT * FROM interceptions WHERE session_id = ? ORDER BY timestamp",
            (session.session_id,),
        ).fetchall()
        trace.interceptions = [dict(i) for i in interceptions]
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Get beads that were accessed during this session
    try:
        accessed = db.execute(
            """
            SELECT b.id, b.content, b.type, b.summary, al.was_useful
            FROM access_log al
            JOIN beads b ON b.id = al.bead_id
            WHERE al.session_id = ?
            ORDER BY al.accessed_at
            """,
            (session.session_id,),
        ).fetchall()
        trace.beads_accessed = [dict(a) for a in accessed]
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Read RUNNING.md for activity log
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        try:
            trace.running_log = running_path.read_text()
        except Exception as e:
            logger.warning("Non-fatal error in reflector: %s", e)

    return trace


# =============================================================================
# STEP 2: REFLECT ON EXECUTION
# =============================================================================

def reflect_on_session(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze execution trace and generate reflections.

    This is the Reflector role from ACE, adapted for Enki.
    No LLM call — pure heuristic analysis of structured data.

    Args:
        trace: Execution trace from gather_execution_trace

    Returns:
        List of Reflections
    """
    reflections = []

    # --- Violation analysis ---
    reflections.extend(_reflect_violations(trace))

    # --- Escalation analysis ---
    reflections.extend(_reflect_escalations(trace))

    # --- Interception analysis ---
    reflections.extend(_reflect_interceptions(trace))

    # --- Knowledge usage analysis ---
    reflections.extend(_reflect_knowledge_usage(trace))

    # --- Process compliance ---
    reflections.extend(_reflect_process(trace))

    # --- Session productivity ---
    reflections.extend(_reflect_productivity(trace))

    return reflections


def _reflect_violations(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze violation patterns."""
    reflections = []

    if not trace.violations:
        if trace.files_edited:
            # Edited files with zero violations — process was followed
            reflections.append(Reflection(
                category="worked",
                description="Clean session — no gate violations while editing files",
                evidence=f"{len(trace.files_edited)} files edited, 0 violations",
                confidence=0.9,
                actionable=True,
                suggested_bead_type="learning",
            ))
        return reflections

    # Group violations by gate
    by_gate: dict[str, list] = {}
    for v in trace.violations:
        gate = v.get("gate", "unknown")
        by_gate.setdefault(gate, []).append(v)

    for gate, violations in by_gate.items():
        count = len(violations)

        if count >= 3:
            # Repeated violations on same gate = pattern
            reasons = [v.get("reason", "") for v in violations]
            unique_reasons = set(reasons)

            reflections.append(Reflection(
                category="pattern",
                description=f"Repeated {gate} gate violations ({count}x) — Claude keeps hitting the same wall",
                evidence=f"Reasons: {', '.join(list(unique_reasons)[:3])}",
                confidence=0.95,
                actionable=True,
                suggested_bead_type="pattern",
            ))

        elif count == 1:
            # Single violation followed by compliance = learning moment
            v = violations[0]
            reflections.append(Reflection(
                category="worked",
                description=f"Single {gate} violation caught and corrected — gate working as intended",
                evidence=f"Blocked: {v.get('reason', 'unknown')[:100]}",
                confidence=0.7,
                actionable=False,
            ))

    # Check for violation → success pattern (blocked, then did it right)
    if trace.violations and trace.files_edited:
        violation_tools = {v.get("tool") for v in trace.violations}
        if violation_tools & {"Edit", "Write", "MultiEdit"}:
            reflections.append(Reflection(
                category="worked",
                description="Gate blocked premature edits, then edits succeeded after compliance",
                evidence=f"{len(trace.violations)} blocks → {len(trace.files_edited)} successful edits",
                confidence=0.8,
                actionable=True,
                suggested_bead_type="learning",
            ))

    return reflections


def _reflect_escalations(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze tier escalations."""
    reflections = []

    if not trace.escalations:
        return reflections

    for esc in trace.escalations:
        initial = esc.get("initial_tier", "unknown")
        final = esc.get("final_tier", "unknown")
        files = esc.get("files_at_escalation", 0)
        lines = esc.get("lines_at_escalation", 0)

        reflections.append(Reflection(
            category="warning",
            description=f"Scope creep: {initial} → {final} ({files} files, {lines} lines)",
            evidence=f"Goal was '{trace.goal or 'unset'}' — work grew beyond initial estimate",
            confidence=0.95,
            actionable=True,
            suggested_bead_type="pattern",
        ))

    # Multiple escalations in one session = serious underestimation
    if len(trace.escalations) > 1:
        reflections.append(Reflection(
            category="failed",
            description=f"Multiple escalations ({len(trace.escalations)}x) — initial scope assessment was significantly off",
            evidence=f"{trace.tier_start} → {trace.tier_end}",
            confidence=0.95,
            actionable=True,
            suggested_bead_type="learning",
        ))

    return reflections


def _reflect_interceptions(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze Ereshkigal interceptions."""
    reflections = []

    if not trace.interceptions:
        return reflections

    blocked = [i for i in trace.interceptions if i.get("result") == "blocked"]
    allowed = [i for i in trace.interceptions if i.get("result") == "allowed"]
    legitimate = [i for i in trace.interceptions if i.get("was_legitimate")]

    if blocked:
        # Check for false positives (blocked but later marked legitimate)
        false_positives = [i for i in blocked if i.get("was_legitimate")]
        if false_positives:
            reflections.append(Reflection(
                category="pattern",
                description=f"Ereshkigal false positives: {len(false_positives)} legitimate actions blocked",
                evidence=f"Categories: {', '.join(set(i.get('category', '?') for i in false_positives))}",
                confidence=0.85,
                actionable=True,
                suggested_bead_type="pattern",
            ))

        # True catches
        true_catches = [i for i in blocked if not i.get("was_legitimate")]
        if true_catches:
            categories = [i.get("category", "unknown") for i in true_catches]
            reflections.append(Reflection(
                category="worked",
                description=f"Ereshkigal caught {len(true_catches)} genuine issues",
                evidence=f"Categories: {', '.join(set(categories))}",
                confidence=0.85,
                actionable=True,
                suggested_bead_type="learning",
            ))

    return reflections


def _reflect_knowledge_usage(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze how beads/knowledge were used."""
    reflections = []

    if not trace.beads_accessed:
        if trace.goal:
            # Had a goal but never checked memory
            reflections.append(Reflection(
                category="warning",
                description="Goal set but no beads accessed — past knowledge wasn't consulted",
                evidence=f"Goal: '{trace.goal}', beads accessed: 0",
                confidence=0.6,
                actionable=True,
                suggested_bead_type="pattern",
            ))
        return reflections

    useful = [b for b in trace.beads_accessed if b.get("was_useful")]
    not_useful = [b for b in trace.beads_accessed if b.get("was_useful") is False]
    unrated = [b for b in trace.beads_accessed if b.get("was_useful") is None]

    if useful:
        reflections.append(Reflection(
            category="worked",
            description=f"{len(useful)} beads were useful during this session",
            evidence=f"Types: {', '.join(set(b.get('type', '?') for b in useful))}",
            confidence=0.8,
            actionable=False,
        ))

    if not_useful and len(not_useful) > len(useful):
        reflections.append(Reflection(
            category="failed",
            description="More beads were unhelpful than helpful — search relevance may be poor",
            evidence=f"Useful: {len(useful)}, Not useful: {len(not_useful)}",
            confidence=0.7,
            actionable=True,
            suggested_bead_type="pattern",
        ))

    return reflections


def _reflect_process(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze overall process compliance."""
    reflections = []

    # No goal set — nothing to evaluate, skip entirely
    if not trace.goal:
        return reflections

    # Phase progression
    if trace.phase_start != trace.phase_end:
        reflections.append(Reflection(
            category="worked",
            description=f"Phase progressed: {trace.phase_start} → {trace.phase_end}",
            evidence="Natural phase progression through work",
            confidence=0.7,
            actionable=False,
        ))

    # Check RUNNING.md for spec flow
    if trace.running_log:
        has_spec_created = "SPEC CREATED:" in trace.running_log
        has_spec_approved = "SPEC APPROVED:" in trace.running_log
        has_impl = len(trace.files_edited) > 0

        if has_spec_created and has_spec_approved and has_impl:
            reflections.append(Reflection(
                category="worked",
                description="Full flow completed: spec → approval → implementation",
                evidence="RUNNING.md shows spec created, approved, and files edited",
                confidence=0.9,
                actionable=True,
                suggested_bead_type="learning",
            ))
        elif has_impl and not has_spec_created:
            reflections.append(Reflection(
                category="warning",
                description="Implementation without spec — skipped planning phase",
                evidence=f"{len(trace.files_edited)} files edited without spec creation",
                confidence=0.8,
                actionable=True,
                suggested_bead_type="pattern",
            ))

    return reflections


def _reflect_productivity(trace: ExecutionTrace) -> list[Reflection]:
    """Analyze session productivity."""
    reflections = []

    # High violation-to-edit ratio = fighting the system
    if trace.violations and trace.files_edited:
        ratio = len(trace.violations) / len(trace.files_edited)
        if ratio > 2.0:
            reflections.append(Reflection(
                category="failed",
                description=f"High friction: {len(trace.violations)} violations for {len(trace.files_edited)} edits (ratio: {ratio:.1f})",
                evidence="More time fighting gates than writing code",
                confidence=0.8,
                actionable=True,
                suggested_bead_type="pattern",
            ))

    # Many files edited in one session
    if len(trace.files_edited) > 15:
        reflections.append(Reflection(
            category="warning",
            description=f"Large session: {len(trace.files_edited)} files edited — consider breaking into smaller chunks",
            evidence=f"Files: {', '.join(trace.files_edited[:5])}...",
            confidence=0.7,
            actionable=True,
            suggested_bead_type="learning",
        ))

    return reflections


# =============================================================================
# STEP 3: DISTILL INTO SKILLS (BEADS)
# =============================================================================

def distill_reflections(
    reflections: list[Reflection],
    project: Optional[str] = None,
) -> list[Skill]:
    """Distill reflections into atomic, storable skills.

    This is the SkillManager role from ACE.
    Filters non-actionable reflections, deduplicates against existing beads.

    Args:
        reflections: Reflections from reflect_on_session
        project: Optional project name for scoping

    Returns:
        List of Skills ready to store
    """
    skills = []

    # Only process actionable reflections with sufficient confidence
    actionable = [
        r for r in reflections
        if r.actionable and r.confidence >= 0.7 and r.suggested_bead_type
    ]

    for reflection in actionable:
        # Build skill content — atomic and reusable
        content = _format_skill_content(reflection)
        tags = _derive_tags(reflection)

        skill = Skill(
            content=content,
            bead_type=reflection.suggested_bead_type,
            tags=tags,
            source_reflections=[reflection.description],
        )

        # Exact-content dedup via SHA-256 hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        try:
            db = get_db()
            existing_hash = db.execute(
                "SELECT id FROM beads WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing_hash:
                skill.is_duplicate = True
                skill.duplicate_of = existing_hash[0]
                skills.append(skill)
                continue
        except Exception as e:
            logger.warning("Non-fatal error in reflector: %s", e)

        # Fallback: embedding similarity check
        try:
            existing = search(content, limit=3, log_accesses=False)
            for result in existing:
                if result.score > 0.85:
                    skill.is_duplicate = True
                    skill.duplicate_of = result.bead.id
                    break
        except Exception as e:
            logger.warning("Non-fatal error in reflector: %s", e)

        skills.append(skill)

    return skills


def _format_skill_content(reflection: Reflection) -> str:
    """Format a reflection into atomic bead content."""
    prefix = {
        "worked": "EFFECTIVE: ",
        "failed": "INEFFECTIVE: ",
        "pattern": "PATTERN: ",
        "strategy": "STRATEGY: ",
        "warning": "WATCH: ",
    }.get(reflection.category, "")

    return f"{prefix}{reflection.description}\n\nEvidence: {reflection.evidence}"


def _derive_tags(reflection: Reflection) -> list[str]:
    """Derive tags from a reflection."""
    tags = [reflection.category, "auto-reflected"]

    # Add gate-specific tags
    desc_lower = reflection.description.lower()
    if "tdd" in desc_lower or "test" in desc_lower:
        tags.append("tdd")
    if "spec" in desc_lower or "plan" in desc_lower:
        tags.append("spec")
    if "scope" in desc_lower or "escalat" in desc_lower:
        tags.append("scope")
    if "ereshkigal" in desc_lower or "intercept" in desc_lower:
        tags.append("ereshkigal")
    if "phase" in desc_lower:
        tags.append("phase")

    return tags


# =============================================================================
# STEP 4: STORE SKILLS AS BEADS
# =============================================================================

def store_skills(
    skills: list[Skill],
    project: Optional[str] = None,
) -> list[str]:
    """Store non-duplicate skills as beads.

    Args:
        skills: Skills from distill_reflections
        project: Project name

    Returns:
        List of created bead IDs
    """
    stored_ids = []

    for skill in skills:
        if skill.is_duplicate:
            continue

        try:
            bead = create_bead(
                content=skill.content,
                bead_type=skill.bead_type,
                summary=skill.content[:100],
                project=project,
                context="auto-reflection",
                tags=skill.tags,
            )
            stored_ids.append(bead.id)
        except Exception as e:
            logger.warning("Non-fatal error in reflector: %s", e)

    return stored_ids


# =============================================================================
# MAIN ENTRY POINT: CLOSE THE FEEDBACK LOOP
# =============================================================================

def close_feedback_loop(project_path: Path = None) -> dict:
    """Run the complete reflection → distill → store pipeline.

    This is the function to call at session end / pre-compact.

    Args:
        project_path: Project directory path

    Returns:
        Report dict with all steps
    """
    project_path = project_path or Path.cwd()

    report = {
        "timestamp": datetime.now().isoformat(),
        "session_id": None,
        "reflections": [],
        "skills_generated": 0,
        "skills_stored": 0,
        "skills_duplicate": 0,
        "stored_bead_ids": [],
    }

    # Step 1: Gather
    trace = gather_execution_trace(project_path)
    report["session_id"] = trace.session_id

    # Short-circuit: nothing to reflect on
    if trace.goal is None and len(trace.files_edited) == 0:
        return report

    # Step 2: Reflect
    reflections = reflect_on_session(trace)
    report["reflections"] = [
        {
            "category": r.category,
            "description": r.description,
            "confidence": r.confidence,
            "actionable": r.actionable,
        }
        for r in reflections
    ]

    # Step 3: Distill
    skills = distill_reflections(reflections, project=trace.project)
    report["skills_generated"] = len(skills)
    report["skills_duplicate"] = len([s for s in skills if s.is_duplicate])

    # Step 4: Store
    stored = store_skills(skills, project=trace.project)
    report["skills_stored"] = len(stored)
    report["stored_bead_ids"] = stored

    return report


# =============================================================================
# CROSS-SESSION PATTERN ANALYSIS
# =============================================================================

def analyze_cross_session_patterns(
    days: int = 30,
    project_path: Path = None,
) -> list[Reflection]:
    """Analyze patterns across multiple sessions.

    Called during weekly review to find recurring themes.

    Args:
        days: Number of days to look back
        project_path: Project directory path

    Returns:
        List of cross-session Reflections
    """
    db = get_db()
    reflections = []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Find recurring violation patterns
    try:
        recurring = db.execute(
            """
            SELECT gate, reason, COUNT(*) as count,
                   COUNT(DISTINCT session_id) as sessions
            FROM violations
            WHERE timestamp > ?
            GROUP BY gate, reason
            HAVING count > 3 AND sessions > 1
            ORDER BY count DESC
            LIMIT 10
            """,
            (cutoff,),
        ).fetchall()

        for row in recurring:
            reflections.append(Reflection(
                category="pattern",
                description=f"Recurring violation: {row['gate']} gate ({row['count']}x across {row['sessions']} sessions)",
                evidence=f"Reason: {row['reason'][:100]}",
                confidence=0.95,
                actionable=True,
                suggested_bead_type="pattern",
            ))
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Find sessions with high violation counts (problematic sessions)
    try:
        problem_sessions = db.execute(
            """
            SELECT session_id, COUNT(*) as violation_count
            FROM violations
            WHERE timestamp > ?
            GROUP BY session_id
            HAVING violation_count > 5
            ORDER BY violation_count DESC
            LIMIT 5
            """,
            (cutoff,),
        ).fetchall()

        if problem_sessions:
            avg_violations = sum(r["violation_count"] for r in problem_sessions) / len(problem_sessions)
            reflections.append(Reflection(
                category="pattern",
                description=f"{len(problem_sessions)} high-friction sessions (avg {avg_violations:.0f} violations each)",
                evidence="These sessions spent more time on gate compliance than coding",
                confidence=0.85,
                actionable=True,
                suggested_bead_type="pattern",
            ))
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    # Check for Ereshkigal effectiveness
    try:
        interception_stats = db.execute(
            """
            SELECT result,
                   COUNT(*) as count,
                   SUM(CASE WHEN was_legitimate = 1 THEN 1 ELSE 0 END) as legitimate
            FROM interceptions
            WHERE timestamp > ?
            GROUP BY result
            """,
            (cutoff,),
        ).fetchall()

        total_blocked = 0
        total_legitimate_blocked = 0
        for row in interception_stats:
            if row["result"] == "blocked":
                total_blocked = row["count"]
                total_legitimate_blocked = row["legitimate"] or 0

        if total_blocked > 0:
            false_positive_rate = total_legitimate_blocked / total_blocked
            if false_positive_rate > 0.4:
                reflections.append(Reflection(
                    category="failed",
                    description=f"Ereshkigal false positive rate: {false_positive_rate:.0%} — patterns too aggressive",
                    evidence=f"{total_legitimate_blocked}/{total_blocked} blocked actions were legitimate",
                    confidence=0.9,
                    actionable=True,
                    suggested_bead_type="pattern",
                ))
            elif false_positive_rate < 0.1 and total_blocked > 5:
                reflections.append(Reflection(
                    category="worked",
                    description=f"Ereshkigal accuracy: {1 - false_positive_rate:.0%} — patterns well-tuned",
                    evidence=f"Only {total_legitimate_blocked}/{total_blocked} false positives",
                    confidence=0.85,
                    actionable=True,
                    suggested_bead_type="learning",
                ))
    except Exception as e:
        logger.warning("Non-fatal error in reflector: %s", e)

    return reflections


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "ExecutionTrace",
    "Reflection",
    "Skill",
    "gather_execution_trace",
    "reflect_on_session",
    "distill_reflections",
    "store_skills",
    "close_feedback_loop",
    "analyze_cross_session_patterns",
]
