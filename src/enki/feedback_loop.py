"""
Feedback Loop — Closes Enki's enforcement feedback loop.

Pipeline: Accumulated Data → Analyze FP/Evasions → Propose Adjustments
         → Present to Human → Human Decides → Track Outcome

All proposals require human approval (HITL). No auto-apply, no auto-revert.
No LLM anywhere — pure heuristic analysis.
"""

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import get_db
from .ereshkigal import (
    load_patterns,
    save_patterns,
    add_pattern,
    remove_pattern,
    find_evasions_with_bugs,
)
from .evolution import (
    load_evolution_state,
    save_evolution_state,
    add_gate_adjustment,
    create_self_correction,
)
from .session import get_session_id


# =============================================================================
# CONSTANTS
# =============================================================================

FEEDBACK_THRESHOLDS = {
    "fp_rate_to_loosen": 0.40,
    "min_evaluations_to_loosen": 5,
    "evasion_bug_count_to_tighten": 2,
    "violation_count_to_tighten": 5,
    "regression_sessions_to_check": 5,
    "regression_violation_increase": 2.0,
    "regression_min_violations_post": 5,
}

NEVER_LOOSEN = {
    "gates": {"phase", "spec", "scope", "enforcement_integrity"},
    "pattern_categories": {"certainty_patterns", "infra_integrity_patterns"},
}

MAX_PROPOSALS_PER_CYCLE = 1


# =============================================================================
# ANALYSIS: FP RATES
# =============================================================================

def analyze_pattern_fp_rates(days: int = 14) -> list[dict]:
    """Compute per-pattern false positive rates from interceptions table.

    Args:
        days: Number of days to look back

    Returns:
        List of {pattern, category, total_blocks, false_positives, fp_rate}
        sorted by fp_rate descending.
    """
    db = get_db()
    cutoff = f"-{days} days"

    try:
        rows = db.execute("""
            SELECT
                pattern,
                category,
                COUNT(*) as total_blocks,
                SUM(CASE WHEN was_legitimate = 1 THEN 1 ELSE 0 END) as false_positives
            FROM interceptions
            WHERE result = 'blocked'
            AND timestamp > datetime('now', ?)
            AND pattern IS NOT NULL
            AND pattern != ''
            GROUP BY pattern, category
            HAVING total_blocks >= ?
            ORDER BY false_positives * 1.0 / total_blocks DESC
        """, (cutoff, FEEDBACK_THRESHOLDS["min_evaluations_to_loosen"])).fetchall()

        results = []
        for row in rows:
            total = row["total_blocks"]
            fps = row["false_positives"] or 0
            fp_rate = fps / total if total > 0 else 0.0

            results.append({
                "pattern": row["pattern"],
                "category": row["category"],
                "total_blocks": total,
                "false_positives": fps,
                "fp_rate": fp_rate,
            })

        return results
    except Exception:
        return []


# =============================================================================
# ANALYSIS: EVASION PATTERNS
# =============================================================================

def analyze_evasion_patterns(days: int = 30) -> list[dict]:
    """Find common phrases in evasion reasoning that could become patterns.

    Uses find_evasions_with_bugs() with tightened correlation (same tool OR file).
    Extracts common n-grams from reasoning text — pure text analysis, no LLM.

    Args:
        days: Number of days to look back

    Returns:
        List of {phrase, count, example_reasonings} for human review.
    """
    evasions = find_evasions_with_bugs(days)
    if len(evasions) < 2:
        return []  # not enough signal

    # Extract 2-4 word ngrams from reasoning text
    all_ngrams: Counter = Counter()
    ngram_examples: dict[str, list[str]] = {}

    for evasion in evasions:
        reasoning = evasion.get("reasoning", "")
        if not reasoning:
            continue

        # Clean and tokenize
        words = re.findall(r'[a-z]+', reasoning.lower())

        # Generate 2-4 word ngrams
        for n in range(2, 5):
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i:i + n])

                # Skip very common/boring ngrams
                if _is_stop_ngram(ngram):
                    continue

                all_ngrams[ngram] += 1
                if ngram not in ngram_examples:
                    ngram_examples[ngram] = []
                if len(ngram_examples[ngram]) < 3:
                    # Store truncated reasoning as example
                    ngram_examples[ngram].append(reasoning[:200])

    # Return phrases appearing in 2+ distinct evasions
    results = []
    for phrase, count in all_ngrams.most_common(20):
        if count >= 2:
            results.append({
                "phrase": phrase,
                "count": count,
                "example_reasonings": ngram_examples.get(phrase, []),
            })

    return results


def _is_stop_ngram(ngram: str) -> bool:
    """Filter out common/meaningless n-grams."""
    stop_phrases = {
        "the", "is", "in", "it", "to", "a", "and", "or", "of", "for",
        "this", "that", "with", "not", "but", "be", "as", "at", "on",
        "i", "we", "can", "will", "do", "if", "so", "an", "by",
    }
    words = ngram.split()
    # All stop words = boring
    if all(w in stop_phrases for w in words):
        return True
    # Too short words
    if all(len(w) <= 2 for w in words):
        return True
    return False


# =============================================================================
# PROPOSAL GENERATION
# =============================================================================

def generate_proposals(project_path: Optional[Path] = None) -> list[dict]:
    """Main analysis — generates max 1 proposal per cycle.

    Checks FP rates and evasion patterns, proposes the highest-priority change.

    Args:
        project_path: Project directory path

    Returns:
        List of proposal dicts (max 1)
    """
    proposals = []

    # 1. Check FP rates — propose loosening if a pattern is too aggressive
    fp_data = analyze_pattern_fp_rates()
    for fp_info in fp_data:
        if fp_info["fp_rate"] >= FEEDBACK_THRESHOLDS["fp_rate_to_loosen"]:
            category = fp_info.get("category", "")

            # Never loosen protected categories
            if category in NEVER_LOOSEN["pattern_categories"]:
                continue

            proposals.append({
                "proposal_type": "pattern_remove",
                "target": category,
                "description": f"Remove pattern '{fp_info['pattern']}' — {fp_info['fp_rate']:.0%} false positive rate",
                "reason": f"{fp_info['false_positives']}/{fp_info['total_blocks']} blocks were legitimate actions",
                "old_value": fp_info["pattern"],
                "new_value": None,
                "evidence": {
                    "fp_rate": fp_info["fp_rate"],
                    "total_blocks": fp_info["total_blocks"],
                    "false_positives": fp_info["false_positives"],
                    "days_analyzed": 14,
                },
                "priority": fp_info["fp_rate"],  # Higher FP = higher priority
            })

            if len(proposals) >= MAX_PROPOSALS_PER_CYCLE:
                break

    if proposals:
        return proposals[:MAX_PROPOSALS_PER_CYCLE]

    # 2. Check evasion patterns — propose tightening if evasions are correlated
    evasion_data = analyze_evasion_patterns()
    for evasion_info in evasion_data:
        if evasion_info["count"] >= FEEDBACK_THRESHOLDS["evasion_bug_count_to_tighten"]:
            proposals.append({
                "proposal_type": "pattern_add",
                "target": "minimize_patterns",  # Default category for evasion-derived patterns
                "description": f"Add pattern for evasion phrase: '{evasion_info['phrase']}'",
                "reason": f"Phrase appeared in {evasion_info['count']} correlated evasions",
                "old_value": None,
                "new_value": evasion_info["phrase"],
                "evidence": {
                    "evasion_count": evasion_info["count"],
                    "example_reasonings": evasion_info["example_reasonings"][:2],
                    "days_analyzed": 30,
                },
                "priority": evasion_info["count"],
            })

            if len(proposals) >= MAX_PROPOSALS_PER_CYCLE:
                break

    if proposals:
        return proposals[:MAX_PROPOSALS_PER_CYCLE]

    # 3. Check violation rates per gate — propose tightening
    db = get_db()
    try:
        cutoff = f"-14 days"
        gate_violations = db.execute("""
            SELECT gate, COUNT(*) as count,
                   COUNT(DISTINCT session_id) as sessions
            FROM violations
            WHERE timestamp > datetime('now', ?)
            GROUP BY gate
            HAVING count >= ?
            ORDER BY count DESC
        """, (cutoff, FEEDBACK_THRESHOLDS["violation_count_to_tighten"])).fetchall()

        for row in gate_violations:
            gate = row["gate"]

            # Never loosen protected gates — but tightening is fine
            proposals.append({
                "proposal_type": "gate_tighten",
                "target": gate,
                "description": f"Tighten {gate} gate — {row['count']} violations across {row['sessions']} sessions",
                "reason": f"High violation count suggests gate isn't catching issues early enough",
                "old_value": None,
                "new_value": None,
                "evidence": {
                    "violation_count": row["count"],
                    "session_count": row["sessions"],
                    "days_analyzed": 14,
                },
                "priority": row["count"],
            })

            if len(proposals) >= MAX_PROPOSALS_PER_CYCLE:
                break
    except Exception:
        pass

    return proposals[:MAX_PROPOSALS_PER_CYCLE]


# =============================================================================
# PROPOSAL STORAGE
# =============================================================================

def store_proposal(proposal: dict) -> str:
    """Insert a proposal into the feedback_proposals table.

    Args:
        proposal: Proposal dict from generate_proposals

    Returns:
        Proposal ID
    """
    db = get_db()
    proposal_id = f"fp_{uuid.uuid4().hex[:12]}"
    session_id = get_session_id()

    db.execute("""
        INSERT INTO feedback_proposals
        (id, session_id, proposal_type, target, description, reason,
         old_value, new_value, evidence_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        proposal_id,
        session_id,
        proposal["proposal_type"],
        proposal["target"],
        proposal["description"],
        proposal["reason"],
        proposal.get("old_value"),
        proposal.get("new_value"),
        json.dumps(proposal.get("evidence", {})),
    ))
    db.commit()

    return proposal_id


# =============================================================================
# HITL: APPLY / REJECT / REVERT / ACKNOWLEDGE
# =============================================================================

def apply_proposal(proposal_id: str) -> dict:
    """Apply a human-approved proposal.

    Steps:
    1. Fetch proposal (must be 'pending')
    2. Snapshot current state
    3. Execute the change
    4. Update proposal status to 'applied'
    5. Log to evolution

    Args:
        proposal_id: ID of the proposal to apply

    Returns:
        Result dict
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM feedback_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()

    if not row:
        return {"error": f"Proposal {proposal_id} not found"}
    if row["status"] != "pending":
        return {"error": f"Proposal {proposal_id} is {row['status']}, not pending"}

    proposal_type = row["proposal_type"]
    target = row["target"]
    old_value = row["old_value"]
    new_value = row["new_value"]

    # Snapshot pre-apply state
    pre_snapshot = _take_snapshot(target, proposal_type)

    # Execute the change
    try:
        if proposal_type == "pattern_add":
            if new_value:
                add_pattern(new_value, target)

        elif proposal_type == "pattern_remove":
            if old_value:
                remove_pattern(old_value, target)

        elif proposal_type == "pattern_refine":
            if old_value:
                remove_pattern(old_value, target)
            if new_value:
                add_pattern(new_value, target)

        elif proposal_type in ("gate_tighten", "gate_loosen"):
            adj_type = "tighten" if proposal_type == "gate_tighten" else "loosen"
            add_gate_adjustment(
                gate=target,
                adjustment_type=adj_type,
                description=row["description"],
                reason=row["reason"],
            )

    except Exception as e:
        return {"error": f"Failed to apply: {e}"}

    # Snapshot post-apply state
    post_snapshot = _take_snapshot(target, proposal_type)

    # Update proposal status
    now = datetime.now().isoformat()
    db.execute("""
        UPDATE feedback_proposals
        SET status = 'applied',
            applied_at = ?,
            pre_apply_snapshot = ?,
            post_apply_snapshot = ?,
            sessions_since_apply = 0
        WHERE id = ?
    """, (now, json.dumps(pre_snapshot), json.dumps(post_snapshot), proposal_id))
    db.commit()

    return {
        "proposal_id": proposal_id,
        "status": "applied",
        "timestamp": now,
        "change_summary": row["description"],
    }


def reject_proposal(proposal_id: str) -> dict:
    """Human rejects a proposal.

    Args:
        proposal_id: ID of the proposal to reject

    Returns:
        Result dict
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM feedback_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()

    if not row:
        return {"error": f"Proposal {proposal_id} not found"}
    if row["status"] != "pending":
        return {"error": f"Proposal {proposal_id} is {row['status']}, not pending"}

    db.execute(
        "UPDATE feedback_proposals SET status = 'rejected' WHERE id = ?",
        (proposal_id,)
    )
    db.commit()

    return {
        "proposal_id": proposal_id,
        "status": "rejected",
        "timestamp": datetime.now().isoformat(),
    }


def revert_proposal(proposal_id: str) -> dict:
    """Revert a previously applied proposal.

    Called by human after regression detected.

    Args:
        proposal_id: ID of the proposal to revert

    Returns:
        Result dict
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM feedback_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()

    if not row:
        return {"error": f"Proposal {proposal_id} not found"}
    if row["status"] not in ("applied", "regressed"):
        return {"error": f"Proposal {proposal_id} is {row['status']}, cannot revert"}

    proposal_type = row["proposal_type"]
    target = row["target"]
    old_value = row["old_value"]
    new_value = row["new_value"]

    # Reverse the change
    try:
        if proposal_type == "pattern_add":
            if new_value:
                remove_pattern(new_value, target)

        elif proposal_type == "pattern_remove":
            if old_value:
                add_pattern(old_value, target)

        elif proposal_type == "pattern_refine":
            if new_value:
                remove_pattern(new_value, target)
            if old_value:
                add_pattern(old_value, target)

        elif proposal_type in ("gate_tighten", "gate_loosen"):
            # Log the revert as a new adjustment
            revert_type = "loosen" if proposal_type == "gate_tighten" else "tighten"
            add_gate_adjustment(
                gate=target,
                adjustment_type=revert_type,
                description=f"Revert: {row['description']}",
                reason=f"Regression detected after applying proposal {proposal_id}",
            )

    except Exception as e:
        return {"error": f"Failed to revert: {e}"}

    # Update proposal status
    now = datetime.now().isoformat()
    previous_status = row["status"]
    db.execute("""
        UPDATE feedback_proposals
        SET status = 'reverted', reverted_at = ?
        WHERE id = ?
    """, (now, proposal_id))
    db.commit()

    # Log self-correction
    create_self_correction(
        pattern_type="feedback_revert",
        description=f"Reverted proposal {proposal_id}: {row['description']}",
        frequency=1,
        impact="Regression detected after change",
        correction=f"Reverted {proposal_type} on {target}",
    )

    return {
        "proposal_id": proposal_id,
        "status": "reverted",
        "previous_status": previous_status,
        "timestamp": now,
    }


def acknowledge_regression(proposal_id: str) -> dict:
    """Human acknowledges regression as expected — keep the change.

    Args:
        proposal_id: ID of the proposal to acknowledge

    Returns:
        Result dict
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM feedback_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()

    if not row:
        return {"error": f"Proposal {proposal_id} not found"}
    if row["status"] != "regressed":
        return {"error": f"Proposal {proposal_id} is {row['status']}, not regressed"}

    db.execute(
        "UPDATE feedback_proposals SET status = 'acknowledged' WHERE id = ?",
        (proposal_id,)
    )
    db.commit()

    return {
        "proposal_id": proposal_id,
        "status": "acknowledged",
        "timestamp": datetime.now().isoformat(),
    }


# =============================================================================
# REGRESSION DETECTION
# =============================================================================

def check_for_regressions() -> list[dict]:
    """Check applied proposals for regression. Short-circuits if nothing to check.

    Runs every session-end. Requires BOTH conditions:
    1. Violation rate increased by regression_violation_increase (2.0x)
    2. At least regression_min_violations_post (5) violations in post-apply window

    Returns:
        List of regression dicts for human review
    """
    db = get_db()

    # Fast path: any applied proposals at all?
    try:
        applied_count = db.execute(
            "SELECT COUNT(*) FROM feedback_proposals WHERE status = 'applied'"
        ).fetchone()[0]
    except Exception:
        return []

    if applied_count == 0:
        return []  # nothing to check, skip all the metric queries

    regressions = []
    threshold = FEEDBACK_THRESHOLDS

    try:
        # Get applied proposals with enough sessions elapsed
        applied = db.execute("""
            SELECT * FROM feedback_proposals
            WHERE status = 'applied'
            AND sessions_since_apply >= ?
        """, (threshold["regression_sessions_to_check"],)).fetchall()

        for proposal in applied:
            # Increment session counter for all applied proposals
            db.execute("""
                UPDATE feedback_proposals
                SET sessions_since_apply = sessions_since_apply + 1
                WHERE status = 'applied'
            """)

            # Check violation rate change
            regression = _check_proposal_regression(proposal)
            if regression:
                regressions.append(regression)

                # Mark as regressed
                db.execute(
                    "UPDATE feedback_proposals SET status = 'regressed' WHERE id = ?",
                    (proposal["id"],)
                )

        db.commit()
    except Exception:
        pass

    # Increment session counter for proposals not yet at threshold
    try:
        db.execute("""
            UPDATE feedback_proposals
            SET sessions_since_apply = sessions_since_apply + 1
            WHERE status = 'applied'
            AND sessions_since_apply < ?
        """, (threshold["regression_sessions_to_check"],))
        db.commit()
    except Exception:
        pass

    return regressions


def _check_proposal_regression(proposal) -> Optional[dict]:
    """Check if a single applied proposal shows regression.

    Args:
        proposal: Row from feedback_proposals table

    Returns:
        Regression dict or None
    """
    db = get_db()
    threshold = FEEDBACK_THRESHOLDS

    applied_at = proposal["applied_at"]
    if not applied_at:
        return None

    # Get violation count before apply
    try:
        pre_snapshot = json.loads(proposal["pre_apply_snapshot"] or "{}")
    except (json.JSONDecodeError, TypeError):
        pre_snapshot = {}

    pre_violations = pre_snapshot.get("violation_count", 0)

    # Get violation count after apply
    try:
        post_violations = db.execute("""
            SELECT COUNT(*) FROM violations
            WHERE timestamp > ?
        """, (applied_at,)).fetchone()[0]
    except Exception:
        return None

    # Check both conditions
    if post_violations < threshold["regression_min_violations_post"]:
        return None  # Not enough absolute violations to be meaningful

    if pre_violations == 0:
        # Can't compute ratio with zero baseline, but if we have 5+ violations now
        # that's potentially concerning
        if post_violations >= threshold["regression_min_violations_post"]:
            return {
                "proposal_id": proposal["id"],
                "description": proposal["description"],
                "pre_violations": pre_violations,
                "post_violations": post_violations,
                "increase_ratio": float("inf"),
                "message": f"Violations appeared after change: 0 → {post_violations}",
            }
        return None

    ratio = post_violations / pre_violations
    if ratio >= threshold["regression_violation_increase"]:
        return {
            "proposal_id": proposal["id"],
            "description": proposal["description"],
            "pre_violations": pre_violations,
            "post_violations": post_violations,
            "increase_ratio": ratio,
            "message": f"Violations up {ratio:.1f}x: {pre_violations} → {post_violations}",
        }

    return None


# =============================================================================
# SNAPSHOTS
# =============================================================================

def _take_snapshot(target: str, proposal_type: str) -> dict:
    """Take a snapshot of current state for pre/post comparison.

    Args:
        target: Pattern category or gate name
        proposal_type: Type of proposal

    Returns:
        Snapshot dict
    """
    db = get_db()
    snapshot = {
        "timestamp": datetime.now().isoformat(),
    }

    if proposal_type.startswith("pattern_"):
        # Snapshot pattern state
        try:
            patterns = load_patterns()
            snapshot["patterns"] = patterns.get(target, [])
        except Exception:
            snapshot["patterns"] = []

    # Snapshot recent violation count (for regression baseline)
    try:
        count = db.execute("""
            SELECT COUNT(*) FROM violations
            WHERE timestamp > datetime('now', '-14 days')
        """).fetchone()[0]
        snapshot["violation_count"] = count
    except Exception:
        snapshot["violation_count"] = 0

    return snapshot


# =============================================================================
# FULL CYCLE
# =============================================================================

def run_feedback_cycle(project_path: Optional[Path] = None) -> dict:
    """Full cycle: analyze → propose → store (never apply).

    Args:
        project_path: Project directory path

    Returns:
        Report dict
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "proposals_generated": 0,
        "proposals_stored": [],
        "fp_patterns_analyzed": 0,
        "evasion_patterns_analyzed": 0,
        "status": "stable",
    }

    # Analyze
    fp_data = analyze_pattern_fp_rates()
    report["fp_patterns_analyzed"] = len(fp_data)

    evasion_data = analyze_evasion_patterns()
    report["evasion_patterns_analyzed"] = len(evasion_data)

    # Generate proposals
    proposals = generate_proposals(project_path)
    report["proposals_generated"] = len(proposals)

    # Store proposals (never apply)
    for proposal in proposals:
        proposal_id = store_proposal(proposal)
        report["proposals_stored"].append(proposal_id)
        report["status"] = "proposals_pending"

    return report


# =============================================================================
# STATUS & ALERTS
# =============================================================================

def get_feedback_summary() -> str:
    """Human-readable status with pending + regressed proposals.

    Returns:
        Formatted summary string
    """
    db = get_db()

    try:
        counts = db.execute("""
            SELECT status, COUNT(*) as count
            FROM feedback_proposals
            GROUP BY status
        """).fetchall()
    except Exception:
        return "Feedback loop: No data available."

    status_counts = {row["status"]: row["count"] for row in counts}

    pending = status_counts.get("pending", 0)
    applied = status_counts.get("applied", 0)
    regressed = status_counts.get("regressed", 0)
    reverted = status_counts.get("reverted", 0)
    rejected = status_counts.get("rejected", 0)
    acknowledged = status_counts.get("acknowledged", 0)

    lines = ["## Feedback Loop Status\n"]

    if pending:
        lines.append(f"**Pending proposals:** {pending}")
        # Show pending details
        try:
            pending_rows = db.execute(
                "SELECT id, description FROM feedback_proposals WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            for row in pending_rows:
                lines.append(f"  - `{row['id']}`: {row['description']}")
        except Exception:
            pass

    if regressed:
        lines.append(f"\n**Regressed (action needed):** {regressed}")
        try:
            regressed_rows = db.execute(
                "SELECT id, description FROM feedback_proposals WHERE status = 'regressed' ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            for row in regressed_rows:
                lines.append(f"  - `{row['id']}`: {row['description']}")
        except Exception:
            pass

    if applied:
        lines.append(f"\nApplied (monitoring): {applied}")
    if reverted:
        lines.append(f"Reverted: {reverted}")
    if rejected:
        lines.append(f"Rejected: {rejected}")
    if acknowledged:
        lines.append(f"Acknowledged: {acknowledged}")

    if not any([pending, applied, regressed, reverted, rejected, acknowledged]):
        lines.append("No proposals yet. System is stable.")

    return "\n".join(lines)


def get_session_start_alerts() -> Optional[str]:
    """Get pending proposals + regressions for session-start injection.

    Returns:
        Alert string or None if no alerts
    """
    db = get_db()

    try:
        pending = db.execute(
            "SELECT COUNT(*) FROM feedback_proposals WHERE status = 'pending'"
        ).fetchone()[0]

        regressed = db.execute(
            "SELECT COUNT(*) FROM feedback_proposals WHERE status = 'regressed'"
        ).fetchone()[0]
    except Exception:
        return None

    if pending == 0 and regressed == 0:
        return None

    parts = []
    if pending:
        parts.append(f"Pending feedback proposals: {pending}")
    if regressed:
        parts.append(f"Regressions flagged: {regressed}")

    parts.append("Run `enki_feedback_loop status` to review.")

    return " | ".join(parts)


# =============================================================================
# MAINTENANCE
# =============================================================================

def cleanup_old_proposals(days: int = 180) -> int:
    """Clean up old rejected/reverted proposals.

    Called during enki_maintain.

    Args:
        days: Age threshold for cleanup

    Returns:
        Number of proposals cleaned
    """
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    try:
        result = db.execute("""
            DELETE FROM feedback_proposals
            WHERE status IN ('rejected', 'reverted')
            AND created_at < ?
        """, (cutoff,))
        db.commit()
        return result.rowcount
    except Exception:
        return 0


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "FEEDBACK_THRESHOLDS",
    "NEVER_LOOSEN",
    "MAX_PROPOSALS_PER_CYCLE",
    "analyze_pattern_fp_rates",
    "analyze_evasion_patterns",
    "generate_proposals",
    "store_proposal",
    "apply_proposal",
    "reject_proposal",
    "revert_proposal",
    "acknowledge_regression",
    "check_for_regressions",
    "run_feedback_cycle",
    "get_feedback_summary",
    "get_session_start_alerts",
    "cleanup_old_proposals",
]
