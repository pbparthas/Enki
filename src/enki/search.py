"""Hybrid search (FTS5 + semantic)."""

from typing import Optional
from dataclasses import dataclass

from .db import get_db
from .beads import Bead, log_access
from .embeddings import embed, blob_to_vector, cosine_similarity
from .retention import calculate_weight


@dataclass
class SearchResult:
    """A search result with scoring info."""
    bead: Bead
    score: float
    sources: list[str]  # 'keyword', 'semantic', or both


def search(
    query: str,
    project: Optional[str] = None,
    bead_type: Optional[str] = None,
    limit: int = 10,
    min_weight: float = 0.0,
    log_accesses: bool = True,
    session_id: Optional[str] = None,
) -> list[SearchResult]:
    """Hybrid search combining FTS5 and semantic similarity.

    Args:
        query: Search query
        project: Optional project filter (includes global beads)
        bead_type: Optional type filter
        limit: Maximum results to return
        min_weight: Minimum weight threshold
        log_accesses: Whether to log access to returned beads
        session_id: Session ID for access logging

    Returns:
        List of SearchResult ordered by score
    """
    db = get_db()
    combined = {}

    # 1. Keyword search (FTS5)
    keyword_results = _keyword_search(db, query, project, bead_type)
    for row, fts_score in keyword_results:
        weight = calculate_weight(dict(row))
        if weight < min_weight:
            continue

        bead_id = row["id"]
        score = abs(fts_score) * weight * 0.5  # Keyword contribution
        combined[bead_id] = {
            "row": row,
            "score": score,
            "sources": ["keyword"],
            "weight": weight,
        }

    # 2. Semantic search
    query_vector = embed(query)
    semantic_results = _semantic_search(db, query_vector, project, bead_type)
    for row, similarity in semantic_results:
        weight = calculate_weight(dict(row))
        if weight < min_weight:
            continue

        bead_id = row["id"]
        semantic_score = similarity * weight * 0.5  # Semantic contribution

        if bead_id in combined:
            combined[bead_id]["score"] += semantic_score
            combined[bead_id]["sources"].append("semantic")
        else:
            combined[bead_id] = {
                "row": row,
                "score": semantic_score,
                "sources": ["semantic"],
                "weight": weight,
            }

    # 3. Sort by combined score
    ranked = sorted(combined.values(), key=lambda x: x["score"], reverse=True)

    # 4. Build results
    results = []
    for item in ranked[:limit]:
        bead = Bead.from_row(item["row"])
        results.append(SearchResult(
            bead=bead,
            score=item["score"],
            sources=item["sources"],
        ))

    # 5. Log access for returned results
    if log_accesses:
        for result in results:
            log_access(result.bead.id, session_id=session_id)

    return results


def _keyword_search(db, query: str, project: Optional[str], bead_type: Optional[str]) -> list:
    """Perform FTS5 keyword search."""
    # Escape FTS5 special characters
    safe_query = _escape_fts_query(query)

    if not safe_query.strip():
        return []

    sql = """
        SELECT b.*, bm25(beads_fts) as fts_score
        FROM beads b
        JOIN beads_fts ON b.rowid = beads_fts.rowid
        WHERE beads_fts MATCH ?
        AND b.superseded_by IS NULL
    """
    params = [safe_query]

    if project:
        sql += " AND (b.project = ? OR b.project IS NULL)"
        params.append(project)

    if bead_type:
        sql += " AND b.type = ?"
        params.append(bead_type)

    try:
        rows = db.execute(sql, params).fetchall()
        return [(row, row["fts_score"]) for row in rows]
    except Exception:
        # FTS query failed, return empty
        return []


def _semantic_search(db, query_vector, project: Optional[str], bead_type: Optional[str]) -> list:
    """Perform semantic similarity search."""
    sql = """
        SELECT b.*, e.vector
        FROM beads b
        JOIN embeddings e ON b.id = e.bead_id
        WHERE b.superseded_by IS NULL
    """
    params = []

    if project:
        sql += " AND (b.project = ? OR b.project IS NULL)"
        params.append(project)

    if bead_type:
        sql += " AND b.type = ?"
        params.append(bead_type)

    rows = db.execute(sql, params).fetchall()

    results = []
    for row in rows:
        vector = blob_to_vector(row["vector"])
        similarity = cosine_similarity(query_vector, vector)
        if similarity > 0.3:  # Minimum similarity threshold
            results.append((row, similarity))

    return results


def _escape_fts_query(query: str) -> str:
    """Escape special characters for FTS5 query."""
    # Remove FTS5 operators that could cause parse errors
    special_chars = ['"', "'", "(", ")", "*", ":", "-", "+", "^", "~"]
    result = query
    for char in special_chars:
        result = result.replace(char, " ")

    # Split into words and rejoin
    words = result.split()
    if not words:
        return ""

    # Use OR to match any word
    return " OR ".join(words)


def search_similar(bead_id: str, limit: int = 5) -> list[SearchResult]:
    """Find beads similar to a given bead.

    Args:
        bead_id: The bead to find similar items for
        limit: Maximum results to return

    Returns:
        List of similar beads (excluding the source bead)
    """
    db = get_db()

    # Get the source bead's embedding
    row = db.execute(
        "SELECT vector FROM embeddings WHERE bead_id = ?",
        (bead_id,),
    ).fetchone()

    if not row:
        return []

    source_vector = blob_to_vector(row["vector"])

    # Find similar beads
    results = _semantic_search(db, source_vector, project=None, bead_type=None)

    # Filter out the source bead and build results
    output = []
    for row, similarity in sorted(results, key=lambda x: x[1], reverse=True):
        if row["id"] != bead_id:
            bead = Bead.from_row(row)
            output.append(SearchResult(
                bead=bead,
                score=similarity,
                sources=["semantic"],
            ))
            if len(output) >= limit:
                break

    return output
