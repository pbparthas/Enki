"""abzu.py — Facade: public API for the Abzu memory system.

All external access to memory goes through these functions.
Hooks, MCP tools, and other pillars call this module.
"""

from pathlib import Path
from datetime import datetime

from enki.db import ENKI_ROOT


def inject_session_start(project: str, goal: str, tier: str) -> str:
    """Load and format context for session start injection.

    Returns formatted string for CC's context window.
    Tier-dependent: Minimal gets less, Full gets more.
    """
    parts = []

    # Load persona
    persona_path = ENKI_ROOT / "persona" / "PERSONA.md"
    if persona_path.exists():
        persona_text = persona_path.read_text()
        if tier == "minimal":
            # Short persona for minimal tier
            lines = persona_text.split("\n")[:20]
            parts.append("\n".join(lines))
        else:
            parts.append(persona_text)

    # Load last session summary
    if tier != "minimal":
        from enki.memory.sessions import get_last_final_summary
        last = get_last_final_summary(project)
        if last:
            parts.append("\n## Last Session")
            if last.get("goal"):
                parts.append(f"Goal: {last['goal']}")
            if last.get("phase"):
                parts.append(f"Phase: {last['phase']}")
            if last.get("operational_state"):
                parts.append(last["operational_state"])

    # Load relevant beads
    bead_limit = {"minimal": 0, "standard": 3, "full": 5}.get(tier, 3)
    if bead_limit > 0 and goal:
        from enki.memory.notes import search
        beads = search(goal, project=project, limit=bead_limit)
        if beads:
            parts.append("\n## Relevant Knowledge")
            for b in beads:
                parts.append(f"- [{b['category']}] {b['content'][:200]}")

    # Load staged candidates for full tier
    if tier == "full" and goal:
        from enki.memory.staging import search_candidates
        candidates = search_candidates(goal, limit=3)
        if candidates:
            parts.append("\n## Candidate Knowledge (unreviewed)")
            for c in candidates:
                parts.append(f"- [{c['category']}] {c['content'][:200]}")

    return "\n".join(parts)


def update_pre_compact_summary(
    session_id: str,
    project: str = None,
    transcript_path: str = None,
    operational_state: str = None,
    conversational_state: str = None,
) -> None:
    """Store pre-compact summary. Accumulates across compactions.

    If transcript_path is provided, extracts operational state from JSONL.
    This maintains backward compatibility while adding real JSONL support.
    """
    if transcript_path and not operational_state:
        from enki.memory.extraction import extract_operational_state
        state = extract_operational_state(transcript_path)
        # Format operational state as readable text
        parts = []
        if state["files_modified"]:
            parts.append(f"Files modified: {', '.join(state['files_modified'])}")
        if state["errors"]:
            parts.append(f"Errors: {'; '.join(state['errors'][:3])}")
        if state["user_messages"]:
            parts.append(f"User requests: {'; '.join(state['user_messages'][:3])}")
        if state["tasks_completed"]:
            parts.append(f"Tasks: {'; '.join(state['tasks_completed'][:3])}")
        if state["assistant_summary"]:
            parts.append(f"Summary: {state['assistant_summary'][:200]}")
        parts.append(f"Tool calls: {state['tool_calls_count']}")
        operational_state = "\n".join(parts) if parts else "No activity extracted."

    from enki.memory.sessions import update_pre_compact_summary as _update
    _update(
        session_id=session_id,
        project=project,
        operational_state=operational_state,
        conversational_state=conversational_state,
    )


def inject_post_compact(session_id: str, tier: str) -> str:
    """Build complete post-compact context for re-injection.

    Includes persona identity, project state, accumulated session history,
    and enforcement reminder. Output kept under ~2000 tokens.
    """
    parts = []

    # 1. Persona identity (compact)
    parts.append("## Session Restored After Compaction")
    parts.append("")
    parts.append("**You ARE Enki.** Collaborator, craftsman, keeper of knowledge. Direct, opinionated, no filler.")
    parts.append("")

    # 2. Project state from DB
    try:
        from enki.gates.uru import inject_enforcement_context
        enforcement = inject_enforcement_context()
        # Parse out goal/phase/tier from enforcement context
        goal_line = phase_line = tier_line = project_line = ""
        for line in enforcement.split("\n"):
            if "Goal:" in line:
                goal_line = line.split("Goal:", 1)[1].strip()
            elif "Phase:" in line:
                phase_line = line.split("Phase:", 1)[1].strip()
            elif "Tier:" in line:
                tier_line = line.split("Tier:", 1)[1].strip()
            elif "Project:" in line:
                project_line = line.split("Project:", 1)[1].strip()

        parts.append(f"**Project:** {project_line or 'unknown'}")
        parts.append(f"**Goal:** {goal_line or 'NOT SET'}")
        parts.append(f"**Phase:** {phase_line or 'NOT SET'} | **Tier:** {tier_line or tier}")
        parts.append("")
    except Exception:
        parts.append(f"**Tier:** {tier}")
        parts.append("")

    # 3. Accumulated session state
    from enki.memory.sessions import get_accumulated_summaries
    summaries = get_accumulated_summaries(session_id)
    if summaries:
        parts.append("### Session History:")
        for i, s in enumerate(summaries):
            op_state = s.get("operational_state", "")
            if op_state:
                # Condense each compaction to one line
                condensed = op_state.replace("\n", ". ")[:300]
                parts.append(f"[Compaction {i + 1}] {condensed}")
        parts.append("")

    # 4. Enforcement reminder
    try:
        from enki.gates.uru import inject_enforcement_context
        enforcement = inject_enforcement_context()
        # Extract active gates
        gate_lines = [l for l in enforcement.split("\n") if "Gate" in l and "ACTIVE" in l]
        if gate_lines:
            parts.append("### Enforcement:")
            for gl in gate_lines:
                parts.append(gl.strip().lstrip("- "))
        else:
            parts.append("### Enforcement:")
            parts.append("All gates clear. Continue implementation within scope.")
    except Exception:
        parts.append("### Enforcement:")
        parts.append("Enforcement state unavailable. Proceed with caution.")

    result = "\n".join(parts)

    # Budget: keep under ~2000 tokens (~8000 chars)
    if len(result) > 8000:
        result = result[:8000] + "\n\n[Truncated to fit injection budget]"

    return result


def finalize_session(session_id: str, project: str) -> None:
    """Session end: reconcile summaries, extract candidates, run decay."""
    from enki.memory.extraction import extract_candidates
    from enki.memory.retention import run_decay
    from enki.memory.sessions import cleanup_old_summaries, finalize_session as _finalize
    from enki.memory.staging import add_candidate

    final = _finalize(session_id, project)
    candidates_extracted = 0
    content = final.get("content") if isinstance(final, dict) else None
    if content:
        candidates = extract_candidates(content, session_id)
        for c in candidates:
            add_candidate(
                content=c["content"],
                category=c["category"],
                project=project,
                summary=None,
                source=c.get("source", "session_end"),
                session_id=session_id,
            )
            candidates_extracted += 1
    cleanup_old_summaries(project)
    run_decay()
    summary_id = final.get("id") if isinstance(final, dict) else None
    return {
        "candidates_extracted": candidates_extracted,
        "summary_id": summary_id,
    }


def remember(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
) -> dict:
    """Store a bead. Preference -> wisdom.db direct. Others -> staging."""
    if category == "preference":
        from enki.memory.notes import create
        bead = create(
            content=content,
            category=category,
            project=project,
            summary=summary,
            tags=tags,
        )
        return {"stored": "wisdom", "id": bead["id"], "category": category}
    else:
        from enki.memory.staging import add_candidate
        cid = add_candidate(
            content=content,
            category=category,
            project=project,
            summary=summary,
            source="enki_remember",
        )
        if cid:
            return {"stored": "staging", "id": cid, "category": category}
        else:
            return {"stored": "duplicate", "id": None, "category": category}


def recall(
    query: str,
    scope: str = "project",
    project: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search beads. Searches both wisdom.db and staging, ranks appropriately."""
    from enki.memory.notes import search
    from enki.memory.staging import search_candidates

    # Search wisdom.db
    wisdom_results = search(
        query, project=project, scope=scope, limit=limit
    )

    # Search staging (lower priority)
    staging_results = search_candidates(query, limit=limit)

    # Combine — wisdom results ranked higher
    combined = []
    for r in wisdom_results:
        r["source_db"] = "wisdom"
        combined.append(r)
    for r in staging_results:
        r["source_db"] = "staging"
        # Staging results get a penalty
        r["final_score"] = abs(r.get("fts_score", 0)) * 0.7
        combined.append(r)

    combined.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return combined[:limit]


def get_staged_candidates(
    project: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """List staged bead candidates awaiting Gemini review (Abzu Spec §4).

    Args:
        project: Filter by project. None for all.
        limit: Max results. None for default (50).
    """
    from enki.memory.staging import list_candidates
    return list_candidates(project=project, limit=limit or 50)


def promote_candidate(
    candidate_id: str,
    consolidated_content: str | None = None,
) -> dict:
    """Promote a staged candidate to wisdom.db (Abzu Spec §4).

    If consolidated_content is provided, the bead is created with
    the merged content instead of the original candidate content.
    Returns dict with promoted bead info.
    """
    from enki.memory.staging import get_candidate, promote, discard

    if consolidated_content:
        # Manual consolidation — create bead with new content, discard candidate
        candidate = get_candidate(candidate_id)
        if not candidate:
            return {"error": "Candidate not found", "promoted": False}

        from enki.memory.notes import create
        bead = create(
            content=consolidated_content,
            category=candidate["category"],
            project=candidate.get("project"),
            summary=candidate.get("summary"),
        )
        discard(candidate_id)
        return {"promoted": True, "bead_id": bead["id"], "method": "consolidated"}
    else:
        bead_id = promote(candidate_id)
        if bead_id:
            return {"promoted": True, "bead_id": bead_id, "method": "direct"}
        return {"promoted": False, "error": "Candidate not found or already promoted"}


def discard_candidate(candidate_id: str, reason: str | None = None) -> dict:
    """Remove a candidate from staging (Abzu Spec §4).

    Args:
        candidate_id: ID of the candidate.
        reason: Optional reason for discarding.
    """
    from enki.memory.staging import discard
    success = discard(candidate_id)
    return {"discarded": success, "reason": reason}


def consolidate_beads(bead_ids: list[str], merged_content: str) -> dict:
    """Merge multiple beads into one, superseding the originals (Abzu Spec §4).

    Creates a new bead with merged_content, marks originals as superseded.
    Returns the new bead.
    """
    from enki.memory.notes import create, get, update
    from enki.db import wisdom_db as _wisdom_db

    if not bead_ids or not merged_content:
        return {"error": "bead_ids and merged_content are required"}

    # Get category from first bead
    first = get(bead_ids[0])
    if not first:
        return {"error": f"Bead {bead_ids[0]} not found"}

    new_bead = create(
        content=merged_content,
        category=first["category"],
        project=first.get("project"),
        summary=f"Consolidated from {len(bead_ids)} beads",
    )

    # Mark originals as evolved after consolidation
    for bid in bead_ids:
        update(bid, evolved_at=datetime.now().isoformat())

    return {
        "new_bead_id": new_bead["id"],
        "superseded": bead_ids,
        "category": first["category"],
    }


def flag_for_deletion(bead_id: str, reason: str) -> dict:
    """Flag an existing bead for deletion (Abzu Spec §4).

    Only sets gemini_flagged=1. Actual deletion is via
    retention.process_flagged_deletions().
    """
    from enki.memory.notes import get, update
    note = get(bead_id)
    if note:
        tags = note.get("tags") or ""
        if "gemini_flagged" not in tags:
            tags = f"{tags},gemini_flagged".strip(",")
        result = update(bead_id, tags=tags, context_description=reason)
    else:
        result = None
    if result:
        return {"flagged": True, "bead_id": bead_id, "reason": reason}
    return {"flagged": False, "error": "Bead not found"}


def get_user_profile(key: str | None = None) -> dict:
    """Get user profile data from wisdom.db (Abzu Spec §4).

    If key is provided, returns just that preference.
    Otherwise returns full profile dict.
    """
    from enki.orch.onboarding import get_or_create_user_profile, get_user_preference
    if key:
        value = get_user_preference(key)
        return {"key": key, "value": value}
    return get_or_create_user_profile()


def set_user_profile(key: str, value: str, source: str = "explicit") -> None:
    """Set a user profile entry in wisdom.db (Abzu Spec §4).

    Args:
        key: Preference key (e.g., "update_frequency", "default_project_type").
        value: Preference value.
        source: "explicit" | "inferred" | "codebase"
    """
    from enki.orch.onboarding import update_user_profile
    update_user_profile(key, value, source=source)


def register_project(name: str, path: str | None = None) -> dict:
    """Register a project in wisdom.db (Abzu Spec §4).

    Creates or updates the projects table entry.
    """
    from enki.db import wisdom_db as _wisdom_db
    with _wisdom_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects (name, path, last_active) "
            "VALUES (?, ?, datetime('now'))",
            (name, path),
        )
    return {"registered": True, "project": name, "path": path}


def get_project_registry() -> list[dict]:
    """Get all registered projects (Abzu Spec §4)."""
    from enki.db import wisdom_db as _wisdom_db
    with _wisdom_db() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY last_active DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def recall_for_nudge(goal_text: str, limit: int = 3) -> list[dict]:
    """Search beads relevant to the current goal for proactive nudging.

    Uses FTS5 search across wisdom.db + prioritizes starred beads.
    Returns top beads most relevant to the goal text.
    Read-only: never creates, modifies, or deletes beads.
    """
    from enki.memory.notes import search
    from enki.db import wisdom_db as _wisdom_db

    if not goal_text or not goal_text.strip():
        return []

    results = []

    # FTS5 search for relevant beads
    try:
        fts_results = search(goal_text, limit=limit * 2)
        for r in fts_results:
            r["nudge_source"] = "fts"
            results.append(r)
    except Exception:
        pass  # Nudge failure must not propagate

    # Also fetch starred beads that might be relevant
    try:
        with _wisdom_db() as conn:
            starred = conn.execute(
                "SELECT id, content, summary, category, starred, created_at "
                "FROM notes WHERE starred = 1 ORDER BY created_at DESC LIMIT ?",
                (limit * 2,),
            ).fetchall()

        # Simple keyword overlap scoring for starred beads
        goal_words = set(goal_text.lower().split())
        for row in starred:
            bead = dict(row)
            bead_words = set(bead["content"].lower().split())
            overlap = len(goal_words & bead_words)
            if overlap > 0:
                bead["nudge_source"] = "starred"
                bead["overlap_score"] = overlap
                # Avoid duplicates with FTS results
                if not any(r["id"] == bead["id"] for r in results):
                    results.append(bead)
    except Exception:
        pass  # Nudge failure must not propagate

    # Sort: starred first, then by overlap/relevance
    def sort_key(b):
        is_starred = 1 if b.get("starred") else 0
        overlap = b.get("overlap_score", 0)
        return (is_starred, overlap)

    results.sort(key=sort_key, reverse=True)
    return results[:limit]


def format_nudge(beads: list[dict]) -> str:
    """Format beads as a nudge message for injection into context.

    Produces a readable block showing related past decisions,
    with starred beads marked.
    """
    if not beads:
        return ""

    from datetime import datetime

    lines = [
        "───────────────────────────────",
        "Related decisions from past sessions:",
        "",
    ]

    for bead in beads:
        # Calculate relative time
        age_label = ""
        created = bead.get("created_at", "")
        if created:
            try:
                if "T" in str(created):
                    created_dt = datetime.fromisoformat(str(created))
                else:
                    created_dt = datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
                delta = datetime.now() - created_dt
                days = delta.days
                if days < 1:
                    age_label = "today"
                elif days < 7:
                    age_label = f"{days} day{'s' if days != 1 else ''} ago"
                elif days < 30:
                    weeks = days // 7
                    age_label = f"{weeks} week{'s' if weeks != 1 else ''} ago"
                else:
                    months = days // 30
                    age_label = f"{months} month{'s' if months != 1 else ''} ago"
            except (ValueError, TypeError):
                age_label = ""

        star_prefix = "\u2b50 " if bead.get("starred") else ""
        time_bracket = f"[{age_label}] " if age_label else ""
        content_preview = bead["content"][:200]

        lines.append(f"\u2022 {star_prefix}{time_bracket}\"{content_preview}\"")
        lines.append("")

    lines.append("───────────────────────────────")
    return "\n".join(lines)


def star(bead_id: str) -> None:
    """Mark bead as permanent (never decays)."""
    from enki.memory.notes import star as _star
    _star(bead_id, starred=True)


def status() -> dict:
    """Health check: DB sizes, bead counts, staging depth, decay stats."""
    import os

    from enki.memory.notes import count
    from enki.memory.retention import get_decay_stats
    from enki.memory.staging import count_candidates

    db_sizes = {}
    for db_name in ["wisdom.db", "abzu.db"]:
        path = ENKI_ROOT / db_name
        if path.exists():
            db_sizes[db_name] = os.path.getsize(path)

    return {
        "beads": {
            "total": count(),
            "by_category": {
                cat: count(category=cat)
                for cat in ("decision", "learning", "pattern", "fix", "preference", "code_knowledge")
            },
        },
        "staging": {
            "candidates": count_candidates(),
        },
        "decay": get_decay_stats(),
        "db_sizes": db_sizes,
    }
