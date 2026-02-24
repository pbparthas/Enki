"""pm.py — PM workflow + customer presentation + entry point validation.

PM is the product owner. Spawned by Enki, not by EM.
Handles intake, specs, debate, status, customer presentation, closure.
Communicates with EM via mail — never spawns EM.
"""

import uuid
from datetime import datetime

from enki.db import em_db


INTAKE_CHECKLIST = [
    "outcome",       # What does success look like?
    "audience",      # Who is this for?
    "constraints",   # Technical, time, or other limits?
    "success_criteria",  # How to measure if it works?
    "scope",         # What's explicitly IN and OUT?
    "risks",         # Known unknowns, dependencies?
]


def validate_intake(answers: dict) -> dict:
    """Validate intake answers against mandatory checklist.

    Returns dict with 'complete' bool and 'missing' list.
    """
    missing = [item for item in INTAKE_CHECKLIST if item not in answers or not answers[item]]
    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "provided": [item for item in INTAKE_CHECKLIST if item in answers and answers[item]],
    }


def create_spec(
    project: str,
    spec_type: str,
    content: str,
    created_by: str = "PM",
) -> str:
    """Record a spec creation as a PM decision."""
    decision_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO pm_decisions "
            "(id, project_id, decision_type, proposed_action, context) "
            "VALUES (?, ?, ?, ?, ?)",
            (decision_id, project, f"spec_{spec_type}", content, created_by),
        )
    return decision_id


def approve_spec(project: str, spec_type: str = "implementation") -> dict:
    """Human approval of a spec. Writes directly to em.db.

    This should be called from CLI, not from CC.
    Combined with Layer 0.5 (CC can't sqlite3), CC cannot forge approval.
    """
    decision_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO pm_decisions "
            "(id, project_id, decision_type, proposed_action, "
            "human_response, pm_was_autonomous) "
            "VALUES (?, ?, 'spec_approval', ?, 'approved', 0)",
            (decision_id, project, f"Approved {spec_type} spec"),
        )
    return {"approved": True, "project": project, "spec_type": spec_type}


def is_spec_approved(project: str) -> bool:
    """Check if spec is approved."""
    with em_db(project) as conn:
        row = conn.execute(
            "SELECT id FROM pm_decisions "
            "WHERE project_id = ? AND decision_type = 'spec_approval' "
            "AND human_response = 'approved' "
            "ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        return row is not None


def record_decision(
    project: str,
    decision_type: str,
    proposed_action: str,
    context: str | None = None,
    autonomous: bool = False,
) -> str:
    """Record a PM decision."""
    decision_id = str(uuid.uuid4())
    with em_db(project) as conn:
        conn.execute(
            "INSERT INTO pm_decisions "
            "(id, project_id, decision_type, proposed_action, context, "
            "pm_was_autonomous) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (decision_id, project, decision_type, proposed_action,
             context, 1 if autonomous else 0),
        )
    return decision_id


def get_decisions(project: str, decision_type: str | None = None) -> list[dict]:
    """Get PM decisions for a project."""
    query = "SELECT * FROM pm_decisions WHERE project_id = ?"
    params: list = [project]
    if decision_type:
        query += " AND decision_type = ?"
        params.append(decision_type)
    query += " ORDER BY created_at DESC"

    with em_db(project) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def detect_entry_point(context: dict) -> str:
    """Detect project entry point: greenfield, mid-design, or brownfield.

    Args:
        context: Dict with 'has_code', 'has_specs', 'has_claude_md'.
    """
    has_code = context.get("has_code", False)
    has_specs = context.get("has_specs", False)

    if has_code:
        return "brownfield"
    elif has_specs:
        return "mid-design"
    else:
        return "greenfield"


# ── PM Workflow (EM Spec §5) ──


DEBATE_PERSPECTIVES = [
    {
        "name": "Technical Feasibility",
        "prompt": "Can this be built as specified? What are the technical risks?",
        "focus": ["tech_stack", "complexity", "dependencies", "performance"],
    },
    {
        "name": "Devil's Advocate",
        "prompt": "What's wrong with this approach? What could fail?",
        "focus": ["edge_cases", "missing_requirements", "assumptions", "risks"],
    },
    {
        "name": "Historical Context",
        "prompt": "Have we done something similar before? What did we learn?",
        "focus": ["past_projects", "patterns", "beads", "known_pitfalls"],
    },
]

MAX_DEBATE_CYCLES = 2


def pm_intake(project: str, answers: dict | None = None) -> dict:
    """Conversational intake loop checking 6-item checklist (EM Spec §5).

    If answers are provided, validates them against the checklist.
    Returns status with missing items for follow-up, or complete status.

    Args:
        project: Project name.
        answers: Dict mapping checklist items to answers.
    """
    answers = answers or {}
    validation = validate_intake(answers)

    if validation["complete"]:
        # Record all answers as PM decisions
        for item in INTAKE_CHECKLIST:
            record_decision(
                project=project,
                decision_type=f"intake_{item}",
                proposed_action=answers[item],
                context="pm_intake",
                autonomous=False,
            )

        return {
            "status": "complete",
            "answers": answers,
            "next_step": "Write Product Spec from intake answers",
        }
    else:
        # Generate follow-up questions for missing items
        questions = []
        question_map = {
            "outcome": "What does success look like? What's the end goal?",
            "audience": "Who is this for? What's the user persona?",
            "constraints": "What are the technical, time, or other limits?",
            "success_criteria": "How do we measure if it works?",
            "scope": "What's explicitly IN scope and OUT of scope?",
            "risks": "Any known unknowns, dependencies, or risks?",
        }
        for item in validation["missing"]:
            questions.append({
                "id": item,
                "question": question_map.get(item, f"Please provide: {item}"),
            })

        return {
            "status": "incomplete",
            "provided": validation["provided"],
            "missing": validation["missing"],
            "questions": questions,
        }


def pm_debate_round(
    project: str,
    product_spec: str,
    cycle: int = 1,
) -> dict:
    """Sequential 3-perspective review of a spec (EM Spec §5).

    Perspectives (in order):
    1. Technical Feasibility — can it be built?
    2. Devil's Advocate — what's wrong?
    3. Historical Context — have we done this before?

    Max 2 cycles before HITL escalation.

    Args:
        project: Project name.
        product_spec: The spec text to debate.
        cycle: Current debate cycle (1 or 2).

    Returns dict with perspective feedback and whether another cycle is needed.
    """
    if cycle > MAX_DEBATE_CYCLES:
        return {
            "status": "hitl_escalation",
            "reason": f"Max debate cycles ({MAX_DEBATE_CYCLES}) exceeded. "
                      "Human decision required.",
            "cycle": cycle,
        }

    feedback = []
    for perspective in DEBATE_PERSPECTIVES:
        # Build perspective context
        entry = {
            "perspective": perspective["name"],
            "prompt": perspective["prompt"],
            "focus_areas": perspective["focus"],
            "spec_excerpt": product_spec[:2000],  # Limit for context
        }

        # Check historical context from beads if available
        if perspective["name"] == "Historical Context":
            try:
                from enki.memory.notes import search
                related = search(product_spec[:200], project=project, limit=3)
                entry["historical_beads"] = [
                    {"category": b["category"], "content": b["content"][:200]}
                    for b in related
                ]
            except Exception:
                entry["historical_beads"] = []

        feedback.append(entry)

    # Record the debate round
    record_decision(
        project=project,
        decision_type="debate_round",
        proposed_action=f"Cycle {cycle}: 3-perspective review completed",
        context=f"cycle={cycle}",
    )

    return {
        "status": "feedback_ready",
        "cycle": cycle,
        "max_cycles": MAX_DEBATE_CYCLES,
        "perspectives": feedback,
        "next_step": (
            "Reconcile feedback into revised spec"
            if cycle < MAX_DEBATE_CYCLES
            else "Final round — reconcile or escalate to HITL"
        ),
    }


def handle_change_request(
    project: str,
    request: str,
    impact_areas: list[str] | None = None,
) -> dict:
    """Handle mid-project scope change request (EM Spec §5).

    Records the change request, assesses impact, and determines
    whether PM can handle autonomously or needs human approval.

    Args:
        project: Project name.
        request: Description of the change.
        impact_areas: Affected areas (e.g., ["auth", "api", "database"]).
    """
    decision_id = record_decision(
        project=project,
        decision_type="change_request",
        proposed_action=request,
        context=f"impact_areas={impact_areas or []}",
        autonomous=False,
    )

    # Assess scope impact
    impact_level = "low"
    if impact_areas and len(impact_areas) >= 3:
        impact_level = "high"
    elif impact_areas and len(impact_areas) >= 2:
        impact_level = "medium"

    return {
        "decision_id": decision_id,
        "change_request": request,
        "impact_level": impact_level,
        "impact_areas": impact_areas or [],
        "requires_human_approval": impact_level != "low",
        "next_step": (
            "PM can apply autonomously"
            if impact_level == "low"
            else "Requires human approval before proceeding"
        ),
    }


def customer_presentation(
    project: str,
    deliverables: dict,
    acceptance_criteria: dict,
) -> dict:
    """Formal acceptance gate at Full tier completion (EM Spec §5).

    Compares deliverables against original Product Spec acceptance criteria.

    Args:
        project: Project name.
        deliverables: Dict of what was delivered (feature → status).
        acceptance_criteria: Dict of original criteria (criterion → description).

    Returns acceptance result with unmet criteria.
    """
    met = []
    unmet = []

    for criterion, description in acceptance_criteria.items():
        # Check if criterion is addressed in deliverables
        criterion_lower = criterion.lower()
        delivered = False
        for feature, status in deliverables.items():
            if (criterion_lower in feature.lower() or
                    feature.lower() in criterion_lower):
                if status in ("done", "complete", "shipped"):
                    delivered = True
                    break
        if delivered:
            met.append(criterion)
        else:
            unmet.append({"criterion": criterion, "description": description})

    accepted = len(unmet) == 0

    # Record decision
    record_decision(
        project=project,
        decision_type="customer_presentation",
        proposed_action=f"{'Accepted' if accepted else 'Rejected'}: "
                        f"{len(met)}/{len(acceptance_criteria)} criteria met",
        autonomous=False,
    )

    return {
        "accepted": accepted,
        "total_criteria": len(acceptance_criteria),
        "met": len(met),
        "unmet_count": len(unmet),
        "unmet_details": unmet,
        "next_step": (
            "Project closure — trigger memory bridge"
            if accepted
            else "Unmet criteria become bugs/change requests"
        ),
    }


def should_decide_autonomously(
    decision_type: str,
    project: str,
) -> bool:
    """Check if PM has enough history to decide autonomously (EM Spec §5).

    Looks at past decisions of the same type and human override rates.
    If PM has made 5+ correct autonomous decisions of this type,
    confidence is high enough to proceed.
    """
    with em_db(project) as conn:
        # Count autonomous decisions that were NOT overridden
        good = conn.execute(
            "SELECT COUNT(*) FROM pm_decisions "
            "WHERE project_id = ? AND decision_type = ? "
            "AND pm_was_autonomous = 1 "
            "AND (human_override IS NULL OR human_override = 'right')",
            (project, decision_type),
        ).fetchone()[0]

        # Count autonomous decisions that WERE overridden
        bad = conn.execute(
            "SELECT COUNT(*) FROM pm_decisions "
            "WHERE project_id = ? AND decision_type = ? "
            "AND pm_was_autonomous = 1 AND human_override = 'wrong'",
            (project, decision_type),
        ).fetchone()[0]

    # Need at least 5 good decisions and <20% override rate
    if good >= 5 and (bad / (good + bad) if (good + bad) > 0 else 0) < 0.2:
        return True
    return False


def pm_autonomous_decision(
    project: str,
    decision_type: str,
    proposed_action: str,
    context: str | None = None,
) -> dict:
    """Make an autonomous PM decision if confidence allows (EM Spec §5).

    Returns the decision with whether it was autonomous or deferred to human.
    """
    if should_decide_autonomously(decision_type, project):
        decision_id = record_decision(
            project=project,
            decision_type=decision_type,
            proposed_action=proposed_action,
            context=context,
            autonomous=True,
        )
        return {
            "decision_id": decision_id,
            "autonomous": True,
            "action": proposed_action,
        }
    else:
        decision_id = record_decision(
            project=project,
            decision_type=decision_type,
            proposed_action=proposed_action,
            context=context,
            autonomous=False,
        )
        return {
            "decision_id": decision_id,
            "autonomous": False,
            "action": proposed_action,
            "requires_human": True,
        }
