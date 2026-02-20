"""links.py — Link generation for Enki v4 notes.

When a new note is stored, finds similar existing notes via embedding
similarity and creates typed links between them.

7 link types: relates_to, supersedes, contradicts, extends, imports, uses, implements

Uses local model (Item 2.5) for relationship classification when available,
falls back to embedding-similarity heuristics otherwise.
"""

from typing import Optional

VALID_RELATIONSHIPS = (
    "relates_to", "supersedes", "contradicts", "extends",
    "imports", "uses", "implements",
)

# Similarity thresholds for heuristic link creation
LINK_THRESHOLD = 0.3       # Minimum similarity to consider linking
STRONG_THRESHOLD = 0.7     # High similarity — likely relates_to or supersedes


def generate_links(new_note_id: str, db: str, k: int = 10) -> list[dict]:
    """Find similar notes and create links.

    1. Retrieve embedding for new note
    2. Find top-k similar notes (both wisdom.db and abzu.db)
    3. Classify relationships (local model or heuristic fallback)
    4. Store links in appropriate database

    Args:
        new_note_id: ID of the newly created note.
        db: Database where the new note lives ('wisdom' or 'abzu').
        k: Number of candidates to consider.

    Returns:
        List of created link dicts: {source_id, target_id, relationship, target_db}.
    """
    from enki.db import get_abzu_db, get_wisdom_db

    # 1. Get the new note's content and embedding
    note_content, note_category, embedding = _get_note_with_embedding(new_note_id, db)
    if embedding is None:
        return []

    # 2. Find similar notes across both databases
    candidates = _find_candidates(new_note_id, embedding, k)
    if not candidates:
        return []

    # 3. Classify relationships
    links = _classify_relationships(
        new_note_id, note_content, note_category, candidates
    )

    # 4. Store links
    stored = _store_links(new_note_id, db, links)

    return stored


def _get_note_with_embedding(
    note_id: str, db: str
) -> tuple[Optional[str], Optional[str], Optional[bytes]]:
    """Fetch note content and its embedding."""
    from enki.db import get_abzu_db, get_wisdom_db

    if db == "wisdom":
        conn = get_wisdom_db()
        note_table = "notes"
        embed_table = "embeddings"
    elif db == "abzu":
        conn = get_abzu_db()
        note_table = "note_candidates"
        embed_table = "candidate_embeddings"
    else:
        raise ValueError(f"Unknown db: {db}")

    try:
        note = conn.execute(
            f"SELECT content, category FROM {note_table} WHERE id = ?",
            (note_id,),
        ).fetchone()
        if not note:
            return None, None, None

        emb_row = conn.execute(
            f"SELECT vector FROM {embed_table} WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        embedding = emb_row["vector"] if emb_row else None

        return note["content"], note["category"], embedding
    finally:
        conn.close()


def _find_candidates(
    exclude_id: str, query_embedding: bytes, k: int
) -> list[dict]:
    """Find top-k similar notes across both databases."""
    from enki.embeddings import blob_to_array, search_similar

    candidates = []

    # Search wisdom.db
    wisdom_results = search_similar(query_embedding, "wisdom", limit=k)
    for note_id, score in wisdom_results:
        if note_id != exclude_id and score >= LINK_THRESHOLD:
            candidates.append({
                "note_id": note_id,
                "score": score,
                "source_db": "wisdom",
            })

    # Search abzu.db
    abzu_results = search_similar(query_embedding, "abzu", limit=k)
    for note_id, score in abzu_results:
        if note_id != exclude_id and score >= LINK_THRESHOLD:
            candidates.append({
                "note_id": note_id,
                "score": score,
                "source_db": "abzu",
            })

    # Sort by score descending, take top k
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:k]


def _classify_relationships(
    source_id: str,
    source_content: str,
    source_category: str,
    candidates: list[dict],
) -> list[dict]:
    """Classify relationship types between source and candidates.

    Attempts local model first, falls back to heuristics.
    """
    # Try local model classification
    classified = _try_local_model(source_content, source_category, candidates)
    if classified is not None:
        return classified

    # Heuristic fallback
    return _heuristic_classify(source_content, source_category, candidates)


def _try_local_model(
    source_content: str,
    source_category: str,
    candidates: list[dict],
) -> Optional[list[dict]]:
    """Attempt to use local model for relationship classification.

    Returns None if local model is unavailable (Item 2.5 not yet wired).
    """
    try:
        from enki.local_model import classify_links
        return classify_links(source_content, source_category, candidates)
    except (ImportError, Exception):
        return None


def _heuristic_classify(
    source_content: str,
    source_category: str,
    candidates: list[dict],
) -> list[dict]:
    """Heuristic relationship classification based on similarity and category.

    Rules:
    - Very high similarity (>= 0.85) + same category → supersedes
    - High similarity (>= 0.7) → relates_to
    - code_knowledge category → uses
    - fix/pattern categories → implements
    - Everything else above threshold → relates_to
    """
    links = []

    for cand in candidates:
        score = cand["score"]
        if score < LINK_THRESHOLD:
            continue

        # Fetch candidate content/category for heuristic
        cand_info = _get_candidate_info(cand["note_id"], cand["source_db"])
        cand_category = cand_info.get("category", "") if cand_info else ""

        relationship = _determine_relationship(
            source_category, cand_category, score, source_content,
            cand_info.get("content", "") if cand_info else "",
        )

        links.append({
            "target_id": cand["note_id"],
            "target_db": cand["source_db"],
            "relationship": relationship,
            "score": score,
        })

    return links


def _determine_relationship(
    source_cat: str,
    target_cat: str,
    score: float,
    source_content: str,
    target_content: str,
) -> str:
    """Pick relationship type based on heuristics."""
    # Very high similarity + same category → likely supersedes
    if score >= 0.85 and source_cat == target_cat:
        return "supersedes"

    # Code knowledge linking
    if source_cat == "code_knowledge" or target_cat == "code_knowledge":
        return "uses"

    # Fix/pattern → implements
    if source_cat in ("fix", "pattern") and target_cat in ("decision", "learning"):
        return "implements"

    # Default: relates_to
    return "relates_to"


def _get_candidate_info(note_id: str, source_db: str) -> Optional[dict]:
    """Fetch basic info about a candidate note."""
    from enki.db import get_abzu_db, get_wisdom_db

    if source_db == "wisdom":
        conn = get_wisdom_db()
        table = "notes"
    else:
        conn = get_abzu_db()
        table = "note_candidates"

    try:
        row = conn.execute(
            f"SELECT content, category FROM {table} WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row:
            return {"content": row["content"], "category": row["category"]}
        return None
    finally:
        conn.close()


def _store_links(
    source_id: str, source_db: str, links: list[dict]
) -> list[dict]:
    """Store generated links in the appropriate database.

    Links from wisdom.db notes go to note_links (wisdom.db).
    Links from abzu.db candidates go to candidate_links (abzu.db).
    """
    from enki.db import get_abzu_db, get_wisdom_db

    stored = []

    if source_db == "wisdom":
        conn = get_wisdom_db()
        try:
            for link in links:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO note_links "
                        "(source_id, target_id, relationship, created_by) "
                        "VALUES (?, ?, ?, ?)",
                        (source_id, link["target_id"], link["relationship"], "auto_link"),
                    )
                    stored.append({
                        "source_id": source_id,
                        "target_id": link["target_id"],
                        "relationship": link["relationship"],
                        "target_db": link.get("target_db", "wisdom"),
                    })
                except Exception:
                    pass  # Skip duplicate or FK constraint failures
            conn.commit()
        finally:
            conn.close()

    elif source_db == "abzu":
        conn = get_abzu_db()
        try:
            for link in links:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO candidate_links "
                        "(source_id, target_id, target_db, relationship) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            source_id,
                            link["target_id"],
                            link.get("target_db", "abzu"),
                            link["relationship"],
                        ),
                    )
                    stored.append({
                        "source_id": source_id,
                        "target_id": link["target_id"],
                        "relationship": link["relationship"],
                        "target_db": link.get("target_db", "abzu"),
                    })
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()

    return stored
