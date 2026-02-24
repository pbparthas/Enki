"""session_pipeline.py — Session-end three-loop pipeline.

Loop 1: Reflector — heuristic analysis → learnings as note candidates
Loop 2: Feedback cycle — FP/evasion analysis → propose adjustments
Loop 3: Regression checks — verify applied proposals haven't degraded enforcement

Graceful degradation: if any loop fails, log error and continue.
Never blocks session close.
"""

import logging
import uuid
from datetime import datetime, timezone

from enki.db import get_abzu_db, uru_db

logger = logging.getLogger(__name__)

# Gates that must NEVER be loosened by feedback proposals
NEVER_LOOSEN_GATES = {"phase", "spec", "certainty_patterns"}


# ---------------------------------------------------------------------------
# Loop 1: Reflector
# ---------------------------------------------------------------------------

def run_reflector(session_id: str, project: str | None = None) -> dict:
    """Analyze session enforcement log and extract learnings as note candidates.

    Analyzes:
    - Violation patterns (repeated blocks on same gate)
    - Scope escalations (tier changes mid-session)
    - Knowledge usage (recall hits vs misses)
    - Process compliance (gate blocks vs overrides)
    - Productivity signals (tool calls, files modified)

    Returns: {"candidates_created": int, "insights": [str]}
    """
    insights = []
    candidates_created = 0

    try:
        with uru_db() as conn:
            # Violation patterns: repeated blocks
            blocks = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ? AND action = 'block' "
                "GROUP BY reason HAVING cnt >= 2 ORDER BY cnt DESC",
                (session_id,),
            ).fetchall()

            for row in blocks:
                insight = (
                    f"Repeated gate block: '{row['reason']}' triggered "
                    f"{row['cnt']} times in session {session_id[:8]}"
                )
                insights.append(insight)
                _create_reflector_candidate(
                    content=insight,
                    category="learning",
                    project=project,
                    session_id=session_id,
                )
                candidates_created += 1

            # Override patterns: human overrides suggest friction
            overrides = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ? AND user_override = 1 "
                "GROUP BY reason HAVING cnt >= 1",
                (session_id,),
            ).fetchall()

            for row in overrides:
                insight = (
                    f"Gate override pattern: '{row['reason']}' overridden "
                    f"{row['cnt']} time(s) — may indicate overly strict gate"
                )
                insights.append(insight)
                _create_reflector_candidate(
                    content=insight,
                    category="learning",
                    project=project,
                    session_id=session_id,
                )
                candidates_created += 1

            # Nudge effectiveness: nudges fired but never acted on
            ignored_nudges = conn.execute(
                "SELECT nudge_type, fire_count FROM nudge_state "
                "WHERE session_id = ? AND fire_count >= 2 AND acted_on = 0",
                (session_id,),
            ).fetchall()

            for row in ignored_nudges:
                insight = (
                    f"Nudge '{row['nudge_type']}' fired {row['fire_count']} times "
                    f"but never acted on — may need sensitivity adjustment"
                )
                insights.append(insight)
                _create_reflector_candidate(
                    content=insight,
                    category="learning",
                    project=project,
                    session_id=session_id,
                )
                candidates_created += 1

            # Process compliance summary
            total_blocks = conn.execute(
                "SELECT COUNT(*) FROM enforcement_log "
                "WHERE session_id = ? AND action = 'block'",
                (session_id,),
            ).fetchone()[0]

            total_allows = conn.execute(
                "SELECT COUNT(*) FROM enforcement_log "
                "WHERE session_id = ? AND action = 'allow'",
                (session_id,),
            ).fetchone()[0]

            if total_blocks > 0 and total_allows > 0:
                block_rate = total_blocks / (total_blocks + total_allows)
                if block_rate > 0.3:
                    insight = (
                        f"High block rate ({block_rate:.0%}) in session — "
                        f"{total_blocks} blocks vs {total_allows} allows. "
                        f"May indicate workflow friction or gate misconfiguration"
                    )
                    insights.append(insight)
                    _create_reflector_candidate(
                        content=insight,
                        category="learning",
                        project=project,
                        session_id=session_id,
                    )
                    candidates_created += 1

    except Exception as e:
        logger.error("Reflector failed: %s", e)
        insights.append(f"Reflector error: {e}")

    return {"candidates_created": candidates_created, "insights": insights}


def _create_reflector_candidate(
    content: str,
    category: str,
    project: str | None,
    session_id: str,
) -> str | None:
    """Insert a reflector-generated note candidate into abzu.db."""
    import hashlib

    cid = str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    try:
        conn = get_abzu_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO note_candidates "
                "(id, content, category, project, status, content_hash, "
                "source, session_id, created_at) "
                "VALUES (?, ?, ?, ?, 'raw', ?, 'session_end', ?, ?)",
                (
                    cid, content, category, project, content_hash,
                    session_id, datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return cid
    except Exception as e:
        logger.warning("Failed to create reflector candidate: %s", e)
        return None


# ---------------------------------------------------------------------------
# Loop 2: Feedback Cycle
# ---------------------------------------------------------------------------

def run_feedback_cycle(session_id: str) -> dict:
    """Analyze false positive rate and propose gate adjustments.

    Rules:
    - Max 1 proposal per cycle
    - NEVER_LOOSEN gates cannot be loosened
    - All proposals require HITL approval (no auto-apply)

    Returns: {"proposal_id": str|None, "analysis": dict}
    """
    analysis = {
        "total_blocks": 0,
        "total_overrides": 0,
        "fp_rate": 0.0,
        "proposal_created": False,
    }

    try:
        with uru_db() as conn:
            # Count blocks and overrides
            blocks = conn.execute(
                "SELECT COUNT(*) FROM enforcement_log "
                "WHERE session_id = ? AND action = 'block'",
                (session_id,),
            ).fetchone()[0]

            overrides = conn.execute(
                "SELECT COUNT(*) FROM enforcement_log "
                "WHERE session_id = ? AND user_override = 1",
                (session_id,),
            ).fetchone()[0]

            analysis["total_blocks"] = blocks
            analysis["total_overrides"] = overrides

            if blocks == 0:
                return {"proposal_id": None, "analysis": analysis}

            # FP rate = overrides / blocks (overrides suggest false positives)
            fp_rate = overrides / blocks if blocks > 0 else 0.0
            analysis["fp_rate"] = fp_rate

            # Only propose if FP rate is significant
            if fp_rate < 0.3 or overrides < 2:
                return {"proposal_id": None, "analysis": analysis}

            # Find the most-overridden gate
            top_override = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM enforcement_log "
                "WHERE session_id = ? AND user_override = 1 "
                "GROUP BY reason ORDER BY cnt DESC LIMIT 1",
                (session_id,),
            ).fetchone()

            if not top_override:
                return {"proposal_id": None, "analysis": analysis}

            gate_name = top_override["reason"]
            override_count = top_override["cnt"]

            # Check NEVER_LOOSEN gates
            gate_lower = gate_name.lower()
            for protected in NEVER_LOOSEN_GATES:
                if protected in gate_lower:
                    logger.info(
                        "Skipping proposal for protected gate: %s", gate_name
                    )
                    return {"proposal_id": None, "analysis": analysis}

            # Check we haven't already proposed for this gate this session
            existing = conn.execute(
                "SELECT COUNT(*) FROM feedback_proposals "
                "WHERE trigger_type = 'false_positive' "
                "AND description LIKE ? "
                "AND created_at > datetime('now', '-1 hour')",
                (f"%{gate_name}%",),
            ).fetchone()[0]

            if existing > 0:
                return {"proposal_id": None, "analysis": analysis}

        # Create proposal (max 1 per cycle)
        from enki.gates.feedback import create_proposal

        proposal_id = create_proposal(
            trigger_type="false_positive",
            description=(
                f"Gate '{gate_name}' was overridden {override_count} times "
                f"in session {session_id[:8]} (FP rate: {fp_rate:.0%}). "
                f"Consider reviewing threshold. Requires HITL approval."
            ),
        )
        analysis["proposal_created"] = True

        return {"proposal_id": proposal_id, "analysis": analysis}

    except Exception as e:
        logger.error("Feedback cycle failed: %s", e)
        return {"proposal_id": None, "analysis": analysis, "error": str(e)}


# ---------------------------------------------------------------------------
# Loop 3: Regression Checks
# ---------------------------------------------------------------------------

def run_regression_checks() -> dict:
    """Check if previously applied proposals degraded enforcement.

    Compares block rates before and after proposal application.
    Flags but does NOT auto-revert.

    Returns: {"checked": int, "regressions": [dict]}
    """
    regressions = []
    checked = 0

    try:
        with uru_db() as conn:
            # Find applied proposals
            applied = conn.execute(
                "SELECT id, description, reviewed_at FROM feedback_proposals "
                "WHERE status = 'applied' AND applied = 1 "
                "ORDER BY reviewed_at DESC LIMIT 10",
            ).fetchall()

            for proposal in applied:
                checked += 1
                reviewed_at = proposal["reviewed_at"]
                if not reviewed_at:
                    continue

                # Count blocks before and after the proposal was applied
                blocks_before = conn.execute(
                    "SELECT COUNT(*) FROM enforcement_log "
                    "WHERE action = 'block' AND timestamp < ?",
                    (reviewed_at,),
                ).fetchone()[0]

                blocks_after = conn.execute(
                    "SELECT COUNT(*) FROM enforcement_log "
                    "WHERE action = 'block' AND timestamp >= ?",
                    (reviewed_at,),
                ).fetchone()[0]

                overrides_after = conn.execute(
                    "SELECT COUNT(*) FROM enforcement_log "
                    "WHERE user_override = 1 AND timestamp >= ?",
                    (reviewed_at,),
                ).fetchone()[0]

                # Regression: if overrides increased significantly after applying
                if overrides_after > 3 and blocks_after > 0:
                    override_rate = overrides_after / blocks_after
                    if override_rate > 0.5:
                        regressions.append({
                            "proposal_id": proposal["id"],
                            "description": proposal["description"][:100],
                            "override_rate_after": override_rate,
                            "blocks_before": blocks_before,
                            "blocks_after": blocks_after,
                            "overrides_after": overrides_after,
                        })
                        logger.warning(
                            "Regression detected for proposal %s: "
                            "override rate %.0f%% after application",
                            proposal["id"][:12], override_rate * 100,
                        )

    except Exception as e:
        logger.error("Regression check failed: %s", e)

    return {"checked": checked, "regressions": regressions}


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

def handle_session_end(session_id: str, project: str | None = None) -> dict:
    """Execute the full session-end three-loop pipeline.

    Loop 1: Reflector → note candidates
    Loop 2: Feedback cycle → max 1 proposal
    Loop 3: Regression checks → flag degradation

    Graceful degradation: each loop runs independently.
    If one fails, the others still execute.
    """
    results = {
        "session_id": session_id,
        "reflector": None,
        "feedback": None,
        "regression": None,
        "errors": [],
    }

    # Loop 1: Reflector
    try:
        results["reflector"] = run_reflector(session_id, project)
    except Exception as e:
        msg = f"Reflector failed: {e}"
        logger.error(msg)
        results["errors"].append(msg)

    # Loop 2: Feedback cycle
    try:
        results["feedback"] = run_feedback_cycle(session_id)
    except Exception as e:
        msg = f"Feedback cycle failed: {e}"
        logger.error(msg)
        results["errors"].append(msg)

    # Loop 3: Regression checks
    try:
        results["regression"] = run_regression_checks()
    except Exception as e:
        msg = f"Regression check failed: {e}"
        logger.error(msg)
        results["errors"].append(msg)

    return results
