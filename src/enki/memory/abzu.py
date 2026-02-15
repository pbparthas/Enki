"""abzu.py — Facade: public API for the Abzu memory system.

All external access to memory goes through these functions.
Hooks, MCP tools, and other pillars call this module.
"""

from pathlib import Path

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
        from enki.memory.beads import search
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
    project: str,
    operational_state: str,
    conversational_state: str,
) -> None:
    """Store pre-compact summary. Accumulates across compactions."""
    from enki.memory.sessions import update_pre_compact_summary as _update
    _update(
        session_id=session_id,
        project=project,
        operational_state=operational_state,
        conversational_state=conversational_state,
    )


def inject_post_compact(session_id: str, tier: str) -> str:
    """Load accumulated summaries for post-compact injection.

    Applies injection budget — collapses old summaries if over limit.
    """
    from enki.memory.sessions import get_post_compact_injection
    return get_post_compact_injection(session_id, tier)


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
        from enki.memory.beads import create
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
    from enki.memory.beads import search
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

        from enki.memory.beads import create
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
    from enki.memory.beads import create, get, update
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

    # Mark originals as superseded
    for bid in bead_ids:
        update(bid, superseded_by=new_bead["id"])

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
    from enki.memory.beads import update
    result = update(bead_id, gemini_flagged=1, flag_reason=reason)
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


def star(bead_id: str) -> None:
    """Mark bead as permanent (never decays)."""
    from enki.memory.beads import star as _star
    _star(bead_id, starred=True)


def status() -> dict:
    """Health check: DB sizes, bead counts, staging depth, decay stats."""
    import os

    from enki.memory.beads import count
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
                for cat in ("decision", "learning", "pattern", "fix", "preference")
            },
        },
        "staging": {
            "candidates": count_candidates(),
        },
        "decay": get_decay_stats(),
        "db_sizes": db_sizes,
    }
