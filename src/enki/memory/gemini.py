"""gemini.py — Gemini review interface (no API — exports package).

Generates a review package (markdown) for an external LLM.
User takes the package to any LLM, gets structured response,
runs `enki review apply` to process results.

No API keys stored. No external calls from codebase. No attack surface.
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from enki.db import ENKI_ROOT, uru_db, wisdom_db

logger = logging.getLogger(__name__)

# ── Gemini CLI availability ──
GEMINI_CLI_AVAILABLE = shutil.which("gemini") is not None
if not GEMINI_CLI_AVAILABLE:
    logger.info("Gemini CLI not found — manual review package workflow only")
from enki.memory.retention import get_decay_stats
from enki.memory.staging import count_candidates, list_candidates


def generate_review_package(output_dir: str | None = None) -> str:
    """Generate a review package for external LLM review.

    Creates a markdown file with:
    - All staged candidates
    - Current wisdom.db bead stats
    - Enforcement log summary
    - Feedback proposals
    - Instructions for the reviewer

    Returns the path to the generated file.
    """
    output_dir = output_dir or str(ENKI_ROOT / "reviews")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    quarter = f"Q{(now.month - 1) // 3 + 1}"
    filename = f"review-{now.year}-{quarter}.md"
    filepath = Path(output_dir) / filename

    sections = []

    # Header
    sections.append(f"# Enki Review Package — {now.year} {quarter}")
    sections.append(f"Generated: {now.isoformat()}\n")

    # Section 1: Staged candidates
    candidates = list_candidates(limit=200)
    sections.append("## 1. Staged Bead Candidates")
    sections.append(f"Total candidates awaiting review: {len(candidates)}\n")

    if candidates:
        sections.append("| # | Category | Project | Content | Source |")
        sections.append("|---|----------|---------|---------|--------|")
        for i, c in enumerate(candidates, 1):
            content_preview = c["content"][:80].replace("|", "\\|")
            sections.append(
                f"| {i} | {c['category']} | {c.get('project', '-')} | "
                f"{content_preview} | {c['source']} |"
            )
    sections.append("")

    # Section 2: Wisdom.db stats
    decay_stats = get_decay_stats()
    sections.append("## 2. Current Wisdom Stats")
    sections.append(f"- Total beads: {decay_stats['total']}")
    sections.append(f"- Hot (weight >= 0.9): {decay_stats['hot']}")
    sections.append(f"- Warm (0.4-0.9): {decay_stats['warm']}")
    sections.append(f"- Cold (0.1-0.4): {decay_stats['cold']}")
    sections.append(f"- Frozen (< 0.1): {decay_stats['frozen']}")
    sections.append(f"- Starred: {decay_stats['starred']}")
    sections.append("")

    # Section 3: Feedback proposals
    try:
        with uru_db() as conn:
            proposals = conn.execute(
                "SELECT * FROM feedback_proposals WHERE status = 'pending' "
                "ORDER BY created_at DESC"
            ).fetchall()
    except Exception:
        proposals = []

    sections.append("## 3. Pending Feedback Proposals")
    if proposals:
        for p in proposals:
            sections.append(f"- **{p['trigger_type']}**: {p['description']}")
    else:
        sections.append("No pending proposals.")
    sections.append("")

    # Section 4: Review instructions
    sections.append("## 4. Review Instructions")
    sections.append("""
For each staged candidate, decide:
- **PROMOTE**: Move to permanent wisdom.db
- **CONSOLIDATE**: Merge with existing bead (specify which)
- **DISCARD**: Remove from staging (low value)
- **FLAG**: Mark existing bead for potential deletion

For each feedback proposal, decide:
- **APPROVE**: Apply the proposed change
- **REJECT**: With reason
- **MODIFY**: Suggest alternative

Output your decisions as JSON:
```json
{
  "bead_decisions": [
    {"candidate_id": "...", "action": "promote|consolidate|discard|flag", "reason": "..."}
  ],
  "proposal_decisions": [
    {"proposal_id": "...", "action": "approve|reject|modify", "reason": "..."}
  ]
}
```
""")

    content = "\n".join(sections)
    filepath.write_text(content)
    return str(filepath)


def prepare_mini_review(project: str) -> str:
    """Generate a project-scoped mini review package (Abzu Spec §11).

    Unlike full review, scoped to a single project's candidates.
    Lighter weight — for mid-project checkpoints.
    Returns path to the generated file.
    """
    output_dir = str(ENKI_ROOT / "reviews")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    filename = f"mini-review-{project}-{now.strftime('%Y%m%d')}.md"
    filepath = Path(output_dir) / filename

    sections = []
    sections.append(f"# Mini Review — {project}")
    sections.append(f"Generated: {now.isoformat()}\n")

    # Project-scoped candidates only
    candidates = list_candidates(project=project, limit=100)
    sections.append(f"## Candidates ({len(candidates)})")

    if candidates:
        sections.append("| # | Category | Content | Source |")
        sections.append("|---|----------|---------|--------|")
        for i, c in enumerate(candidates, 1):
            content_preview = c["content"][:80].replace("|", "\\|")
            sections.append(
                f"| {i} | {c['category']} | {content_preview} | {c['source']} |"
            )
    sections.append("")

    # Project bead stats
    with wisdom_db() as conn:
        bead_count = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE project = ?", (project,)
        ).fetchone()[0]
        category_counts = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM beads "
            "WHERE project = ? GROUP BY category",
            (project,),
        ).fetchall()

    sections.append("## Current Project Beads")
    sections.append(f"- Total: {bead_count}")
    for row in category_counts:
        sections.append(f"- {row['category']}: {row['cnt']}")
    sections.append("")

    # Review instructions (same format as full)
    sections.append("## Instructions")
    sections.append(
        "For each candidate: PROMOTE, CONSOLIDATE, DISCARD, or FLAG.\n"
        "Output as JSON: `{\"bead_decisions\": [{\"candidate_id\": \"...\", "
        "\"action\": \"promote|consolidate|discard|flag\", \"reason\": \"...\"}]}`"
    )

    content = "\n".join(sections)
    filepath.write_text(content)
    return str(filepath)


def validate_gemini_response(response_json: str) -> dict:
    """Validate that Gemini review response is well-formed (Abzu Spec §11).

    Checks:
    - Valid JSON
    - Has 'bead_decisions' array
    - Each decision has required fields (candidate_id, action)
    - Actions are valid values

    Returns dict with 'valid', 'errors', and optionally 'parsed'.
    """
    errors = []

    try:
        parsed = json.loads(response_json)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"Invalid JSON: {e}"], "parsed": None}

    if not isinstance(parsed, dict):
        return {"valid": False, "errors": ["Response must be a JSON object"], "parsed": None}

    # Check bead_decisions
    bead_decisions = parsed.get("bead_decisions", [])
    if not isinstance(bead_decisions, list):
        errors.append("'bead_decisions' must be an array")
    else:
        valid_actions = {"promote", "consolidate", "discard", "flag"}
        for i, decision in enumerate(bead_decisions):
            if not isinstance(decision, dict):
                errors.append(f"bead_decisions[{i}] must be an object")
                continue
            if "candidate_id" not in decision:
                errors.append(f"bead_decisions[{i}] missing 'candidate_id'")
            if "action" not in decision:
                errors.append(f"bead_decisions[{i}] missing 'action'")
            elif decision["action"] not in valid_actions:
                errors.append(
                    f"bead_decisions[{i}] invalid action: '{decision['action']}'. "
                    f"Must be one of {valid_actions}"
                )

    # Check proposal_decisions (optional)
    proposal_decisions = parsed.get("proposal_decisions", [])
    if proposal_decisions and not isinstance(proposal_decisions, list):
        errors.append("'proposal_decisions' must be an array")
    elif isinstance(proposal_decisions, list):
        valid_proposal_actions = {"approve", "reject", "modify"}
        for i, decision in enumerate(proposal_decisions):
            if not isinstance(decision, dict):
                errors.append(f"proposal_decisions[{i}] must be an object")
                continue
            if "proposal_id" not in decision:
                errors.append(f"proposal_decisions[{i}] missing 'proposal_id'")
            if "action" not in decision:
                errors.append(f"proposal_decisions[{i}] missing 'action'")
            elif decision["action"] not in valid_proposal_actions:
                errors.append(
                    f"proposal_decisions[{i}] invalid action: '{decision['action']}'"
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "parsed": parsed,
    }


def apply_promotions(actions: list[dict]) -> dict:
    """Bulk apply bead decisions from Gemini response (Abzu Spec §11).

    Each action: {"candidate_id": "...", "action": "promote|discard|flag", ...}
    Returns stats dict.
    """
    from enki.memory.staging import discard, promote

    stats = {"promoted": 0, "discarded": 0, "flagged": 0, "consolidated": 0, "errors": 0}

    for action in actions:
        cid = action.get("candidate_id", "")
        act = action.get("action", "")

        try:
            if act == "promote":
                result = promote(cid)
                if result:
                    stats["promoted"] += 1
                else:
                    stats["errors"] += 1
            elif act == "discard":
                if discard(cid):
                    stats["discarded"] += 1
                else:
                    stats["errors"] += 1
            elif act == "flag":
                bead_id = action.get("bead_id")
                if bead_id:
                    with wisdom_db() as conn:
                        conn.execute(
                            "UPDATE beads SET gemini_flagged = 1, "
                            "flag_reason = ? WHERE id = ?",
                            (action.get("reason", ""), bead_id),
                        )
                    stats["flagged"] += 1
                # Also discard the candidate if it exists
                discard(cid)
            elif act == "consolidate":
                # Consolidate: merge candidate into existing bead
                merge_target = action.get("merge_with")
                if merge_target:
                    from enki.memory.staging import get_candidate
                    candidate = get_candidate(cid)
                    if candidate:
                        with wisdom_db() as conn:
                            # Append content to existing bead
                            existing = conn.execute(
                                "SELECT content FROM beads WHERE id = ?",
                                (merge_target,),
                            ).fetchone()
                            if existing:
                                merged = existing["content"] + "\n\n" + candidate["content"]
                                conn.execute(
                                    "UPDATE beads SET content = ?, "
                                    "last_accessed = datetime('now') WHERE id = ?",
                                    (merged, merge_target),
                                )
                                discard(cid)
                                stats["consolidated"] += 1
                            else:
                                stats["errors"] += 1
                    else:
                        stats["errors"] += 1
                else:
                    stats["errors"] += 1
        except Exception:
            stats["errors"] += 1

    return stats


def generate_review_report(actions: list[dict]) -> str:
    """Generate a markdown summary of review actions taken (Abzu Spec §11).

    Args:
        actions: List of action dicts with results.

    Returns markdown report string.
    """
    lines = ["# Review Report", f"Generated: {datetime.now().isoformat()}", ""]

    promoted = [a for a in actions if a.get("action") == "promote"]
    discarded = [a for a in actions if a.get("action") == "discard"]
    flagged = [a for a in actions if a.get("action") == "flag"]
    consolidated = [a for a in actions if a.get("action") == "consolidate"]

    lines.append(f"## Summary")
    lines.append(f"- Promoted: {len(promoted)}")
    lines.append(f"- Discarded: {len(discarded)}")
    lines.append(f"- Flagged for deletion: {len(flagged)}")
    lines.append(f"- Consolidated: {len(consolidated)}")
    lines.append("")

    if promoted:
        lines.append("## Promoted to Wisdom")
        for a in promoted:
            reason = a.get("reason", "No reason given")
            lines.append(f"- `{a.get('candidate_id', '?')[:8]}...`: {reason}")
        lines.append("")

    if discarded:
        lines.append("## Discarded")
        for a in discarded:
            reason = a.get("reason", "No reason given")
            lines.append(f"- `{a.get('candidate_id', '?')[:8]}...`: {reason}")
        lines.append("")

    if flagged:
        lines.append("## Flagged for Deletion")
        for a in flagged:
            reason = a.get("reason", "No reason given")
            lines.append(f"- Bead `{a.get('bead_id', '?')[:8]}...`: {reason}")
        lines.append("")

    if consolidated:
        lines.append("## Consolidated")
        for a in consolidated:
            lines.append(
                f"- `{a.get('candidate_id', '?')[:8]}...` → "
                f"`{a.get('merge_with', '?')[:8]}...`"
            )
        lines.append("")

    return "\n".join(lines)


def process_review_response(response_json: str) -> dict:
    """Process structured response from external LLM review.

    Args:
        response_json: JSON string with bead_decisions and proposal_decisions.

    Returns:
        Stats dict with counts of actions taken.
    """
    from enki.gates.feedback import apply_proposal, reject_proposal
    from enki.memory.staging import discard, promote

    response = json.loads(response_json)
    stats = {"promoted": 0, "discarded": 0, "flagged": 0,
             "proposals_approved": 0, "proposals_rejected": 0}

    # Process bead decisions
    for decision in response.get("bead_decisions", []):
        cid = decision["candidate_id"]
        action = decision["action"]

        if action == "promote":
            if promote(cid):
                stats["promoted"] += 1
        elif action == "discard":
            if discard(cid):
                stats["discarded"] += 1
        elif action == "flag":
            # Flag existing bead for deletion
            bead_id = decision.get("bead_id")
            if bead_id:
                with wisdom_db() as conn:
                    conn.execute(
                        "UPDATE beads SET gemini_flagged = 1, "
                        "flag_reason = ? WHERE id = ?",
                        (decision.get("reason", ""), bead_id),
                    )
                    stats["flagged"] += 1

    # Process proposal decisions
    for decision in response.get("proposal_decisions", []):
        pid = decision["proposal_id"]
        action = decision["action"]

        if action == "approve":
            apply_proposal(pid)
            stats["proposals_approved"] += 1
        elif action in ("reject", "modify"):
            reject_proposal(pid, decision.get("reason", ""))
            stats["proposals_rejected"] += 1

    return stats
