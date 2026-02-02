"""Session Summarization - Auto-compress verbose old beads.

Reduces storage while preserving key knowledge by:
1. Detecting verbose beads that haven't been accessed recently
2. Generating concise summaries preserving key insights
3. Creating new summarized beads that supersede the originals
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db import get_db, init_db
from .beads import create_bead, get_bead, update_bead, Bead


@dataclass
class SummarizationCandidate:
    """A bead that could benefit from summarization."""
    bead: Bead
    content_length: int
    age_days: int
    last_access_days: Optional[int]
    reason: str


# Thresholds for summarization
MIN_CONTENT_LENGTH = 500  # Only summarize beads with content > 500 chars
MIN_AGE_DAYS = 30  # Only summarize beads older than 30 days
MIN_INACTIVE_DAYS = 14  # Only if not accessed in 14 days
SUMMARY_TARGET_LENGTH = 200  # Target length for summaries


def find_summarization_candidates(
    project: Optional[str] = None,
    limit: int = 20,
) -> list[SummarizationCandidate]:
    """Find beads that are candidates for summarization.

    Args:
        project: Optional project filter
        limit: Maximum number of candidates to return

    Returns:
        List of SummarizationCandidate objects
    """
    init_db()
    db = get_db()

    now = datetime.now(timezone.utc)

    # Find verbose, old, inactive beads
    query = """
        SELECT * FROM beads
        WHERE superseded_by IS NULL
        AND starred = 0
        AND length(content) > ?
        AND created_at < datetime('now', ?)
        AND (last_accessed IS NULL OR last_accessed < datetime('now', ?))
    """
    params = [MIN_CONTENT_LENGTH, f"-{MIN_AGE_DAYS} days", f"-{MIN_INACTIVE_DAYS} days"]

    if project:
        query += " AND project = ?"
        params.append(project)

    query += " ORDER BY length(content) DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()

    candidates = []
    for row in rows:
        # Calculate age
        created_at = row["created_at"]
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                created_at = created_at.replace(tzinfo=timezone.utc)

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        age_days = (now - created_at).days

        # Calculate last access
        last_access_days = None
        if row["last_accessed"]:
            last_accessed = row["last_accessed"]
            if isinstance(last_accessed, str):
                try:
                    last_accessed = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
                except ValueError:
                    last_accessed = datetime.strptime(last_accessed, "%Y-%m-%d %H:%M:%S")
                    last_accessed = last_accessed.replace(tzinfo=timezone.utc)

            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=timezone.utc)

            last_access_days = (now - last_accessed).days

        bead = Bead(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            summary=row["summary"],
            project=row["project"],
            context=row["context"],
            tags=row["tags"].split(",") if row["tags"] else [],
            starred=bool(row["starred"]),
            weight=row["weight"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            superseded_by=row["superseded_by"],
        )

        content_length = len(row["content"])
        reason = f"Verbose ({content_length} chars), {age_days} days old"
        if last_access_days:
            reason += f", not accessed in {last_access_days} days"
        else:
            reason += ", never accessed"

        candidates.append(SummarizationCandidate(
            bead=bead,
            content_length=content_length,
            age_days=age_days,
            last_access_days=last_access_days,
            reason=reason,
        ))

    return candidates


def generate_summary(content: str, bead_type: str) -> str:
    """Generate a concise summary of bead content.

    This uses heuristic extraction rather than LLM to keep it simple.
    For more sophisticated summarization, integrate with persona module.

    Args:
        content: Original content
        bead_type: Type of bead (decision, solution, learning, etc.)

    Returns:
        Summarized content
    """
    # Simple heuristic summarization
    lines = content.strip().split("\n")

    # Extract key lines based on bead type
    key_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Always include short lines (likely headers or key points)
        if len(line) < 100:
            key_lines.append(line)
            continue

        # Look for key indicators
        lower_line = line.lower()
        if any(kw in lower_line for kw in [
            "decision:", "chose", "because", "reason:",
            "solution:", "fix:", "approach:",
            "learning:", "note:", "important:",
            "problem:", "issue:", "error:",
            "result:", "outcome:", "conclusion:",
        ]):
            key_lines.append(line)

    # If we found key lines, use them
    if key_lines:
        summary = "\n".join(key_lines[:10])  # Max 10 key lines
    else:
        # Fall back to first few sentences
        sentences = content.replace("\n", " ").split(". ")
        summary = ". ".join(sentences[:3])

    # Truncate if still too long
    if len(summary) > SUMMARY_TARGET_LENGTH * 2:
        summary = summary[:SUMMARY_TARGET_LENGTH * 2] + "..."

    return summary


def summarize_bead(
    bead_id: str,
    preserve_original: bool = False,
) -> Optional[str]:
    """Summarize a bead and create a new summarized version.

    Args:
        bead_id: ID of the bead to summarize
        preserve_original: If True, don't supersede the original

    Returns:
        New bead ID if created, None if failed
    """
    init_db()

    bead = get_bead(bead_id)
    if not bead:
        return None

    # Don't summarize starred beads
    if bead.starred:
        return None

    # Generate summary
    summary_content = generate_summary(bead.content, bead.type)

    # Create new bead with summary
    new_bead = create_bead(
        content=summary_content,
        bead_type=bead.type,
        summary=f"[Summarized] {bead.summary or bead.content[:50]}",
        project=bead.project,
        context=f"Summarized from {bead_id}: {bead.context or ''}",
        tags=(bead.tags or []) + ["summarized"],
        starred=False,
    )

    # Supersede the original
    if not preserve_original:
        update_bead(bead_id, superseded_by=new_bead.id)

    return new_bead.id


def run_session_summarization(
    project: Optional[str] = None,
    dry_run: bool = False,
    max_beads: int = 10,
) -> dict:
    """Run summarization on session end.

    Args:
        project: Optional project filter
        dry_run: If True, don't actually create beads
        max_beads: Maximum number of beads to summarize per run

    Returns:
        Dict with summarization results
    """
    init_db()

    candidates = find_summarization_candidates(project=project, limit=max_beads)

    results = {
        "candidates_found": len(candidates),
        "summarized": 0,
        "space_saved_chars": 0,
        "beads_processed": [],
    }

    if dry_run:
        for c in candidates:
            results["beads_processed"].append({
                "id": c.bead.id,
                "reason": c.reason,
                "would_save": c.content_length - SUMMARY_TARGET_LENGTH,
            })
        return results

    for candidate in candidates:
        try:
            new_id = summarize_bead(candidate.bead.id)
            if new_id:
                results["summarized"] += 1
                results["space_saved_chars"] += candidate.content_length - SUMMARY_TARGET_LENGTH
                results["beads_processed"].append({
                    "old_id": candidate.bead.id,
                    "new_id": new_id,
                    "saved": candidate.content_length - SUMMARY_TARGET_LENGTH,
                })
        except Exception as e:
            results["beads_processed"].append({
                "old_id": candidate.bead.id,
                "error": str(e),
            })

    return results


def get_summarization_preview(
    project: Optional[str] = None,
) -> str:
    """Get a preview of what would be summarized.

    Args:
        project: Optional project filter

    Returns:
        Formatted preview string
    """
    candidates = find_summarization_candidates(project=project)

    if not candidates:
        return "No beads found that would benefit from summarization."

    lines = [
        "## Summarization Candidates",
        "",
        f"Found {len(candidates)} beads that could be summarized:",
        "",
    ]

    total_savings = 0
    for c in candidates:
        savings = c.content_length - SUMMARY_TARGET_LENGTH
        total_savings += savings

        lines.append(f"### {c.bead.id[:8]}... [{c.bead.type}]")
        lines.append(f"- {c.reason}")
        lines.append(f"- Potential savings: {savings} chars")
        lines.append(f"- Summary: \"{c.bead.summary or c.bead.content[:50]}...\"")
        lines.append("")

    lines.append(f"**Total potential savings: {total_savings:,} characters**")
    lines.append("")
    lines.append("Run 'enki summarize --confirm' to process these beads.")

    return "\n".join(lines)


def get_summarization_stats() -> dict:
    """Get statistics about summarization.

    Returns:
        Dict with summarization statistics
    """
    init_db()
    db = get_db()

    # Count summarized beads
    summarized = db.execute("""
        SELECT COUNT(*) as count
        FROM beads
        WHERE tags LIKE '%summarized%'
    """).fetchone()["count"]

    # Count candidates
    candidates = len(find_summarization_candidates(limit=100))

    # Calculate potential savings
    potential_row = db.execute("""
        SELECT SUM(length(content)) as total
        FROM beads
        WHERE superseded_by IS NULL
        AND starred = 0
        AND length(content) > ?
        AND created_at < datetime('now', ?)
        AND (last_accessed IS NULL OR last_accessed < datetime('now', ?))
    """, (MIN_CONTENT_LENGTH, f"-{MIN_AGE_DAYS} days", f"-{MIN_INACTIVE_DAYS} days")).fetchone()

    potential_savings = (potential_row["total"] or 0) - (candidates * SUMMARY_TARGET_LENGTH)

    return {
        "summarized_count": summarized,
        "candidates_count": candidates,
        "potential_savings_chars": max(0, potential_savings),
    }
