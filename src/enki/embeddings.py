"""embeddings.py — Vector embedding infrastructure for Enki v4.

Provides:
- compute_embedding(): text → 384-dim float32 BLOB via all-MiniLM-L6-v2
- search_similar(): cosine similarity search against stored embeddings
- hybrid_search(): FTS5 bm25 + embedding similarity, both DBs, link expansion
"""

import struct
import threading
from typing import Optional

import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
BLOB_SIZE = EMBEDDING_DIM * 4  # float32 = 4 bytes each

# Lazy-loaded model singleton
_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazy-load the sentence-transformers model (thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def compute_embedding(text: str) -> bytes:
    """Compute embedding for text, returned as float32 BLOB.

    Args:
        text: Input text to embed.

    Returns:
        bytes of length 1536 (384 float32 values).
    """
    if not text or not text.strip():
        return b"\x00" * BLOB_SIZE

    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return struct.pack(f"{EMBEDDING_DIM}f", *vec.tolist())


def blob_to_array(blob: bytes) -> np.ndarray:
    """Convert a BLOB back to a numpy array."""
    return np.array(struct.unpack(f"{EMBEDDING_DIM}f", blob), dtype=np.float32)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Assumes normalized input."""
    dot = np.dot(a, b)
    # Clamp to [-1, 1] to handle floating point drift
    return float(np.clip(dot, -1.0, 1.0))


def search_similar(
    query_embedding: bytes,
    db: str,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Find notes with most similar embeddings.

    Args:
        query_embedding: BLOB from compute_embedding().
        db: 'wisdom' or 'abzu'.
        limit: Max results to return.

    Returns:
        List of (note_id, similarity_score) tuples, descending by score.
    """
    from enki.db import get_abzu_db, get_wisdom_db

    query_vec = blob_to_array(query_embedding)

    if db == "wisdom":
        conn = get_wisdom_db()
        table = "embeddings"
    elif db == "abzu":
        conn = get_abzu_db()
        table = "candidate_embeddings"
    else:
        raise ValueError(f"Unknown db: {db}")

    try:
        rows = conn.execute(f"SELECT note_id, vector FROM {table}").fetchall()
    finally:
        conn.close()

    scored = []
    for row in rows:
        vec = blob_to_array(row["vector"])
        score = _cosine_similarity(query_vec, vec)
        scored.append((row["note_id"], score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def _fts_search(conn, fts_table: str, content_table: str, query: str,
                limit: int) -> dict[str, float]:
    """Run FTS5 bm25 search, return {note_id: score}."""
    # FTS5 bm25() returns negative scores (lower = better match)
    # We negate to get positive scores (higher = better)
    try:
        rows = conn.execute(
            f"SELECT {content_table}.id, -rank AS score "
            f"FROM {fts_table} "
            f"JOIN {content_table} ON {content_table}.rowid = {fts_table}.rowid "
            f"WHERE {fts_table} MATCH ? "
            f"ORDER BY rank "
            f"LIMIT ?",
            (query, limit * 2),  # Fetch extra for merging
        ).fetchall()
    except Exception:
        return {}

    return {row["id"]: float(row["score"]) for row in rows}


def _embedding_search(conn, embed_table: str, query_vec: np.ndarray,
                      limit: int) -> dict[str, float]:
    """Search embeddings table, return {note_id: score}."""
    rows = conn.execute(
        f"SELECT note_id, vector FROM {embed_table}"
    ).fetchall()

    scored = {}
    for row in rows:
        vec = blob_to_array(row["vector"])
        score = _cosine_similarity(query_vec, vec)
        scored[row["note_id"]] = score

    return scored


def _expand_links_wisdom(conn, note_ids: set[str]) -> set[str]:
    """1-hop link expansion in wisdom.db."""
    if not note_ids:
        return set()
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"SELECT target_id FROM note_links WHERE source_id IN ({placeholders}) "
        f"UNION "
        f"SELECT source_id FROM note_links WHERE target_id IN ({placeholders})",
        list(note_ids) + list(note_ids),
    ).fetchall()
    return {row["target_id" if "target_id" in row.keys() else 0] for row in rows}


def _expand_links_abzu(conn, note_ids: set[str]) -> set[str]:
    """1-hop link expansion in abzu.db."""
    if not note_ids:
        return set()
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"SELECT target_id FROM candidate_links WHERE source_id IN ({placeholders}) "
        f"UNION "
        f"SELECT source_id FROM candidate_links WHERE target_id IN ({placeholders})",
        list(note_ids) + list(note_ids),
    ).fetchall()
    return {row[0] for row in rows}


def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalize scores to [0, 1]."""
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def hybrid_search(
    query: str,
    project: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Combined FTS5 + embedding search across both databases.

    Searches wisdom.db and abzu.db. Abzu results get 0.7 rank multiplier.
    Results include 1-hop link expansion.

    Args:
        query: Search query text.
        project: Optional project filter.
        limit: Max results to return.

    Returns:
        List of dicts with keys: note_id, score, source_db, content, category.
        Sorted by descending score.
    """
    from enki.db import get_abzu_db, get_wisdom_db

    ABZU_MULTIPLIER = 0.7
    FTS_WEIGHT = 0.4
    EMBED_WEIGHT = 0.6

    # Compute query embedding
    query_vec = None
    try:
        query_blob = compute_embedding(query)
        query_vec = blob_to_array(query_blob)
    except Exception:
        pass  # Fall back to FTS-only if embedding fails

    results = {}  # note_id -> {score, source_db}

    # --- wisdom.db ---
    w_conn = get_wisdom_db()
    try:
        # FTS search
        fts_scores = _fts_search(w_conn, "notes_fts", "notes", query, limit)
        fts_norm = _normalize_scores(fts_scores)

        # Embedding search
        embed_scores = {}
        if query_vec is not None:
            embed_scores = _embedding_search(w_conn, "embeddings", query_vec, limit)
        embed_norm = _normalize_scores(embed_scores)

        # Merge scores
        all_ids = set(fts_norm) | set(embed_norm)
        for nid in all_ids:
            fts_s = fts_norm.get(nid, 0.0)
            emb_s = embed_norm.get(nid, 0.0)
            combined = FTS_WEIGHT * fts_s + EMBED_WEIGHT * emb_s
            results[nid] = {"score": combined, "source_db": "wisdom"}

        # Project filter
        if project:
            project_ids = {
                row["id"]
                for row in w_conn.execute(
                    "SELECT id FROM notes WHERE project = ?", (project,)
                ).fetchall()
            }
            results = {k: v for k, v in results.items() if k in project_ids}

        # 1-hop link expansion
        top_ids = set(sorted(results, key=lambda k: results[k]["score"], reverse=True)[:limit])
        linked = _expand_links_wisdom(w_conn, top_ids)
        for lid in linked - top_ids:
            if lid not in results:
                results[lid] = {"score": 0.0, "source_db": "wisdom", "via_link": True}
    finally:
        w_conn.close()

    # --- abzu.db ---
    a_conn = get_abzu_db()
    try:
        fts_scores = _fts_search(a_conn, "candidates_v4_fts", "note_candidates", query, limit)
        fts_norm = _normalize_scores(fts_scores)

        embed_scores = {}
        if query_vec is not None:
            embed_scores = _embedding_search(a_conn, "candidate_embeddings", query_vec, limit)
        embed_norm = _normalize_scores(embed_scores)

        all_ids = set(fts_norm) | set(embed_norm)
        for nid in all_ids:
            fts_s = fts_norm.get(nid, 0.0)
            emb_s = embed_norm.get(nid, 0.0)
            combined = (FTS_WEIGHT * fts_s + EMBED_WEIGHT * emb_s) * ABZU_MULTIPLIER
            # Only keep if better than existing wisdom result
            if nid not in results or combined > results[nid]["score"]:
                results[nid] = {"score": combined, "source_db": "abzu"}

        # Project filter for abzu
        if project:
            abzu_project_ids = {
                row["id"]
                for row in a_conn.execute(
                    "SELECT id FROM note_candidates WHERE project = ?", (project,)
                ).fetchall()
            }
            results = {
                k: v for k, v in results.items()
                if v["source_db"] != "abzu" or k in abzu_project_ids
            }

        # 1-hop link expansion for abzu results
        abzu_top = {k for k, v in results.items() if v["source_db"] == "abzu"}
        linked = _expand_links_abzu(a_conn, abzu_top)
        for lid in linked - set(results.keys()):
            results[lid] = {"score": 0.0, "source_db": "abzu", "via_link": True}
    finally:
        a_conn.close()

    # Sort and fetch content for top results
    sorted_ids = sorted(results, key=lambda k: results[k]["score"], reverse=True)[:limit]

    output = []
    # Batch-fetch content
    w_conn = get_wisdom_db()
    a_conn = get_abzu_db()
    try:
        for nid in sorted_ids:
            info = results[nid]
            row = None
            if info["source_db"] == "wisdom":
                row = w_conn.execute(
                    "SELECT id, content, category, summary, keywords FROM notes WHERE id = ?",
                    (nid,),
                ).fetchone()
            else:
                row = a_conn.execute(
                    "SELECT id, content, category, summary, keywords FROM note_candidates WHERE id = ?",
                    (nid,),
                ).fetchone()

            if row:
                output.append({
                    "note_id": row["id"],
                    "score": info["score"],
                    "source_db": info["source_db"],
                    "content": row["content"],
                    "category": row["category"],
                    "summary": row["summary"],
                    "keywords": row["keywords"],
                    "via_link": info.get("via_link", False),
                })
    finally:
        w_conn.close()
        a_conn.close()

    return output
