"""evolution.py — Memory evolution for Enki v4 notes.

When a new note arrives, checks if existing related notes should have their
metadata (context_description, keywords, tags) updated based on new context.

Key invariant: `content` field NEVER changes after creation.

For abzu.db candidates: direct metadata evolution.
For wisdom.db notes: creates evolution_proposals in abzu.db (never direct update).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional


# Fields that can evolve (content is IMMUTABLE)
EVOLVABLE_FIELDS = ("context_description", "keywords", "tags")


def check_evolution(
    new_note_id: str,
    new_note_db: str,
    related_notes: Optional[list[dict]] = None,
) -> list[dict]:
    """Check if a new note triggers evolution of existing notes.

    Args:
        new_note_id: ID of the newly created note.
        new_note_db: Database where the new note lives ('wisdom' or 'abzu').
        related_notes: Pre-computed related notes from link generation.
            If None, finds related notes via embedding search.

    Returns:
        List of evolution actions taken:
        - For abzu candidates: direct updates applied
        - For wisdom notes: proposals created in evolution_proposals
    """
    from enki.db import get_abzu_db, get_wisdom_db

    # Get the new note's content
    new_content, new_category, new_keywords = _get_note_metadata(
        new_note_id, new_note_db
    )
    if new_content is None:
        return []

    # Find related notes if not provided
    if related_notes is None:
        related_notes = _find_related_notes(new_note_id, new_note_db)

    if not related_notes:
        return []

    # Check each related note for evolution
    actions = []
    for related in related_notes:
        target_id = related["note_id"]
        target_db = related.get("source_db", related.get("target_db", "wisdom"))

        # Get target note metadata
        target_content, target_category, target_keywords = _get_note_metadata(
            target_id, target_db
        )
        if target_content is None:
            continue

        # Determine if evolution is needed
        proposed = _propose_evolution(
            new_content, new_category, new_keywords,
            target_content, target_category, target_keywords,
        )
        if not proposed:
            continue

        # Apply or propose
        if target_db == "abzu":
            _apply_direct_evolution(target_id, proposed)
            actions.append({
                "target_id": target_id,
                "target_db": "abzu",
                "action": "direct_update",
                "changes": proposed,
            })
        elif target_db == "wisdom":
            proposal_id = _create_evolution_proposal(
                target_id, new_note_id, proposed
            )
            actions.append({
                "target_id": target_id,
                "target_db": "wisdom",
                "action": "proposal_created",
                "proposal_id": proposal_id,
                "changes": proposed,
            })

    return actions


def apply_proposal(proposal_id: str) -> bool:
    """Apply an approved evolution proposal to a wisdom.db note.

    Called by external review (Gemini) after approving a proposal.

    Returns True if applied successfully.
    """
    from enki.db import get_abzu_db, get_wisdom_db

    a_conn = get_abzu_db()
    try:
        proposal = a_conn.execute(
            "SELECT * FROM evolution_proposals WHERE id = ? AND status = 'pending'",
            (proposal_id,),
        ).fetchone()
        if not proposal:
            return False
    finally:
        a_conn.close()

    # Apply changes to wisdom.db note
    w_conn = get_wisdom_db()
    try:
        updates = []
        params = []
        if proposal["proposed_context"]:
            updates.append("context_description = ?")
            params.append(proposal["proposed_context"])
        if proposal["proposed_keywords"]:
            updates.append("keywords = ?")
            params.append(proposal["proposed_keywords"])
        if proposal["proposed_tags"]:
            updates.append("tags = ?")
            params.append(proposal["proposed_tags"])

        if updates:
            now = datetime.now(timezone.utc).isoformat()
            updates.append("evolved_at = ?")
            params.append(now)
            params.append(proposal["target_note_id"])

            w_conn.execute(
                f"UPDATE notes SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            w_conn.commit()
    finally:
        w_conn.close()

    # Mark proposal as approved
    a_conn = get_abzu_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        a_conn.execute(
            "UPDATE evolution_proposals SET status = 'approved', reviewed_at = ? "
            "WHERE id = ?",
            (now, proposal_id),
        )
        a_conn.commit()
    finally:
        a_conn.close()

    return True


def reject_proposal(proposal_id: str) -> bool:
    """Reject an evolution proposal."""
    from enki.db import get_abzu_db

    conn = get_abzu_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE evolution_proposals SET status = 'rejected', reviewed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, proposal_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _get_note_metadata(
    note_id: str, db: str
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Fetch note content, category, and keywords."""
    from enki.db import get_abzu_db, get_wisdom_db

    if db == "wisdom":
        conn = get_wisdom_db()
        table = "notes"
    elif db == "abzu":
        conn = get_abzu_db()
        table = "note_candidates"
    else:
        return None, None, None

    try:
        row = conn.execute(
            f"SELECT content, category, keywords, context_description, tags "
            f"FROM {table} WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row:
            return row["content"], row["category"], row["keywords"]
        return None, None, None
    finally:
        conn.close()


def _find_related_notes(note_id: str, db: str) -> list[dict]:
    """Find related notes via embedding similarity."""
    from enki.embeddings import search_similar

    # Get embedding
    if db == "wisdom":
        from enki.db import get_wisdom_db
        conn = get_wisdom_db()
        try:
            row = conn.execute(
                "SELECT vector FROM embeddings WHERE note_id = ?", (note_id,)
            ).fetchone()
            embedding = row["vector"] if row else None
        finally:
            conn.close()
    elif db == "abzu":
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            row = conn.execute(
                "SELECT vector FROM candidate_embeddings WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            embedding = row["vector"] if row else None
        finally:
            conn.close()
    else:
        return []

    if embedding is None:
        return []

    related = []
    for target_db in ("wisdom", "abzu"):
        results = search_similar(embedding, target_db, limit=5)
        for rid, score in results:
            if rid != note_id and score >= 0.4:
                related.append({
                    "note_id": rid,
                    "source_db": target_db,
                    "score": score,
                })

    return related


def _propose_evolution(
    new_content: str,
    new_category: str,
    new_keywords: Optional[str],
    target_content: str,
    target_category: str,
    target_keywords: Optional[str],
) -> Optional[dict]:
    """Determine if evolution is needed and propose changes.

    Tries local model first, falls back to heuristic keyword merging.
    """
    # Try local model
    proposed = _try_local_model_evolution(
        new_content, new_category, new_keywords,
        target_content, target_category, target_keywords,
    )
    if proposed is not None:
        return proposed

    # Heuristic: merge keywords
    return _heuristic_evolution(
        new_content, new_category, new_keywords,
        target_content, target_category, target_keywords,
    )


def _try_local_model_evolution(
    new_content, new_category, new_keywords,
    target_content, target_category, target_keywords,
) -> Optional[dict]:
    """Attempt local model evolution check. Returns None if unavailable."""
    try:
        from enki.local_model import check_evolution as lm_check
        return lm_check(
            new_content, new_category, new_keywords,
            target_content, target_category, target_keywords,
        )
    except (ImportError, Exception):
        return None


def _heuristic_evolution(
    new_content: str,
    new_category: str,
    new_keywords: Optional[str],
    target_content: str,
    target_category: str,
    target_keywords: Optional[str],
) -> Optional[dict]:
    """Heuristic keyword merging.

    If the new note has keywords that the target doesn't, propose adding them.
    Only proposes if there's meaningful new information.
    """
    if not new_keywords:
        return None

    new_kw_set = {k.strip().lower() for k in new_keywords.split(",") if k.strip()}
    target_kw_set = set()
    if target_keywords:
        target_kw_set = {k.strip().lower() for k in target_keywords.split(",") if k.strip()}

    new_additions = new_kw_set - target_kw_set
    if not new_additions:
        return None

    # Require meaningful content overlap to avoid cross-pollinating unrelated notes
    target_words = set(target_content.lower().split())
    new_words = set(new_content.lower().split())
    shared_words = target_words & new_words
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                 "to", "of", "in", "for", "on", "with", "at", "by", "from",
                 "and", "or", "not", "it", "this", "that", "as"}
    meaningful_shared = shared_words - stopwords
    if len(meaningful_shared) < 1:
        return None

    merged = sorted(target_kw_set | new_additions)
    return {
        "proposed_keywords": ",".join(merged),
    }


def _apply_direct_evolution(candidate_id: str, proposed: dict) -> None:
    """Apply evolution directly to an abzu.db candidate."""
    import json as _json
    from enki.db import get_abzu_db

    conn = get_abzu_db()
    try:
        updates = []
        params = []
        if "proposed_context" in proposed and proposed["proposed_context"]:
            updates.append("context_description = ?")
            params.append(proposed["proposed_context"])
        if "proposed_keywords" in proposed and proposed["proposed_keywords"]:
            updates.append("keywords = ?")
            val = proposed["proposed_keywords"]
            params.append(",".join(val) if isinstance(val, list) else val)
        if "proposed_tags" in proposed and proposed["proposed_tags"]:
            updates.append("tags = ?")
            val = proposed["proposed_tags"]
            params.append(_json.dumps(val) if isinstance(val, list) else val)

        if not updates:
            return

        params.append(candidate_id)
        conn.execute(
            f"UPDATE note_candidates SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def _create_evolution_proposal(
    target_note_id: str,
    triggered_by: str,
    proposed: dict,
) -> str:
    """Create an evolution proposal in abzu.db for a wisdom.db note."""
    from enki.db import get_abzu_db

    proposal_id = str(uuid.uuid4())
    reason = f"New related note {triggered_by} has additional context"

    kw = proposed.get("proposed_keywords")
    if isinstance(kw, list):
        kw = ",".join(kw)
    tags = proposed.get("proposed_tags")
    if isinstance(tags, list):
        import json as _json
        tags = _json.dumps(tags)

    conn = get_abzu_db()
    try:
        conn.execute(
            "INSERT INTO evolution_proposals "
            "(id, target_note_id, triggered_by, proposed_context, "
            "proposed_keywords, proposed_tags, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                target_note_id,
                triggered_by,
                proposed.get("proposed_context"),
                kw,
                tags,
                reason,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return proposal_id
