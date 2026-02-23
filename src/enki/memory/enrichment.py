"""enrichment.py — Batch enrichment of raw note_candidates via local model.

Processes raw candidates through:
1. construct_note() → keywords, context_description, tags, summary
2. compute_embedding() → candidate_embeddings
3. classify_links() → candidate_links

Runs at session-end or via CLI: enki batch run
"""

import json
import logging
from typing import Optional

from enki.db import get_abzu_db, get_wisdom_db

logger = logging.getLogger(__name__)


def enrich_raw_candidates(limit: int = 50) -> dict:
    """Process raw note_candidates through local model enrichment.

    For each raw candidate:
    1. construct_note() → keywords, context_description, tags, summary
    2. compute_embedding()
    3. Update candidate with enriched fields, set status='enriched'

    Returns:
        {"processed": int, "failed": int, "errors": [str]}
    """
    from enki.local_model import construct_note, is_available

    if not is_available():
        return {"processed": 0, "failed": 0, "errors": ["Ollama not available"]}

    conn = get_abzu_db()
    try:
        rows = conn.execute(
            "SELECT id, content, category FROM note_candidates "
            "WHERE status = 'raw' ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"processed": 0, "failed": 0, "errors": []}

    processed = 0
    failed = 0
    errors = []

    for row in rows:
        cid = row["id"]
        content = row["content"]
        category = row["category"]

        try:
            # Step 1: Enrich via local model
            enriched = construct_note(content, category)

            keywords = enriched.get("keywords", [])
            if isinstance(keywords, list):
                keywords = json.dumps(keywords)
            context_desc = enriched.get("context_description", "")
            tags = enriched.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            summary = enriched.get("summary", "")

            # Step 2: Compute embedding
            from enki.embeddings import compute_embedding
            embedding = compute_embedding(content)

            # Step 3: Update candidate + insert embedding
            conn = get_abzu_db()
            try:
                conn.execute(
                    "UPDATE note_candidates SET "
                    "keywords = ?, context_description = ?, tags = ?, "
                    "summary = ?, status = 'enriched' "
                    "WHERE id = ?",
                    (keywords, context_desc, tags, summary, cid),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO candidate_embeddings "
                    "(note_id, vector) VALUES (?, ?)",
                    (cid, embedding),
                )
                conn.commit()
            finally:
                conn.close()

            processed += 1
            logger.info("Enriched candidate %s", cid[:12])

        except Exception as e:
            failed += 1
            msg = f"[{cid[:12]}] {e}"
            errors.append(msg)
            logger.warning("Failed to enrich %s: %s", cid[:12], e)

    return {"processed": processed, "failed": failed, "errors": errors}


def generate_links_batch(limit: int = 50) -> dict:
    """Generate links for enriched candidates that don't have links yet.

    For each enriched candidate without links:
    1. Find similar notes via embedding search
    2. classify_links() against matches
    3. Store links in candidate_links

    Returns:
        {"processed": int, "links_created": int, "errors": [str]}
    """
    from enki.local_model import classify_links, is_available

    if not is_available():
        return {"processed": 0, "links_created": 0, "errors": ["Ollama not available"]}

    conn = get_abzu_db()
    try:
        # Find enriched candidates without any outgoing links
        rows = conn.execute(
            "SELECT nc.id, nc.content, nc.category "
            "FROM note_candidates nc "
            "WHERE nc.status = 'enriched' "
            "AND nc.id NOT IN (SELECT source_id FROM candidate_links) "
            "ORDER BY nc.created_at LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"processed": 0, "links_created": 0, "errors": []}

    processed = 0
    links_created = 0
    errors = []

    for row in rows:
        cid = row["id"]
        content = row["content"]
        category = row["category"]

        try:
            # Find similar notes to link against
            candidates = _find_link_candidates(cid, content)
            if not candidates:
                processed += 1
                continue

            # Classify links
            links = classify_links(content, category, candidates)

            if links:
                conn = get_abzu_db()
                try:
                    for link in links:
                        conn.execute(
                            "INSERT OR IGNORE INTO candidate_links "
                            "(source_id, target_id, target_db, relationship) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                cid,
                                link["target_id"],
                                link.get("target_db", "wisdom"),
                                link["relationship"],
                            ),
                        )
                        links_created += 1
                    conn.commit()
                finally:
                    conn.close()

            processed += 1
            logger.info("Linked candidate %s: %d links", cid[:12], len(links))

        except Exception as e:
            msg = f"[{cid[:12]}] {e}"
            errors.append(msg)
            logger.warning("Failed to link %s: %s", cid[:12], e)

    return {"processed": processed, "links_created": links_created, "errors": errors}


def _find_link_candidates(
    source_id: str,
    content: str,
    limit: int = 5,
) -> list[dict]:
    """Find candidate notes to link against from both DBs."""
    from enki.embeddings import compute_embedding, search_similar

    try:
        query_embedding = compute_embedding(content)
    except Exception:
        return []

    candidates = []

    # Search wisdom.db notes
    try:
        wisdom_matches = search_similar(query_embedding, "wisdom", limit=limit)
        w_conn = get_wisdom_db()
        try:
            for note_id, score in wisdom_matches:
                if score < 0.3:
                    continue
                row = w_conn.execute(
                    "SELECT id, content, category FROM notes WHERE id = ?",
                    (note_id,),
                ).fetchone()
                if row:
                    candidates.append({
                        "note_id": row["id"],
                        "content": row["content"][:200],
                        "category": row["category"],
                        "source_db": "wisdom",
                        "score": score,
                    })
        finally:
            w_conn.close()
    except Exception:
        pass

    # Search abzu.db enriched candidates
    try:
        abzu_matches = search_similar(query_embedding, "abzu", limit=limit)
        a_conn = get_abzu_db()
        try:
            for note_id, score in abzu_matches:
                if note_id == source_id or score < 0.3:
                    continue
                row = a_conn.execute(
                    "SELECT id, content, category FROM note_candidates WHERE id = ?",
                    (note_id,),
                ).fetchone()
                if row:
                    candidates.append({
                        "note_id": row["id"],
                        "content": row["content"][:200],
                        "category": row["category"],
                        "source_db": "abzu",
                        "score": score,
                    })
        finally:
            a_conn.close()
    except Exception:
        pass

    # Sort by score descending, cap at limit
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]


def run_daily_batch() -> dict:
    """Full batch: enrich_raw_candidates() then generate_links_batch().

    Returns combined results.
    """
    enrich_result = enrich_raw_candidates()
    links_result = generate_links_batch()

    return {
        "enrich": enrich_result,
        "links": links_result,
    }
