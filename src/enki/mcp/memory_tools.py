"""memory_tools.py — MCP tool implementations for Abzu memory system.

v4: Updated for note model. enki_remember/recall use v4 tables.
    enki_restore added for compaction recovery.
    enki_star/status updated for note terminology.

v3 paths retained as fallback until migration complete.
"""

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from enki.project_state import normalize_project_name, resolve_project_from_cwd

logger = logging.getLogger(__name__)


def _resolve_project(project: str | None) -> str:
    candidate = (project or "").strip()
    if candidate and candidate not in {".", "default"}:
        return normalize_project_name(candidate)
    resolved = resolve_project_from_cwd(str(Path.cwd()))
    if resolved:
        return normalize_project_name(resolved)
    return normalize_project_name(candidate) or "default"


# ---------------------------------------------------------------------------
# enki_remember — v4: stores in note_candidates or wisdom.db notes
# ---------------------------------------------------------------------------


def enki_remember(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
) -> dict:
    """Store a piece of knowledge.

    Categories: decision, learning, pattern, fix, preference, code_knowledge.
    Preferences → direct to wisdom.db notes.
    Everything else → abzu.db note_candidates (staging).
    """
    project = _resolve_project(project)
    if not content or not content.strip():
        return {"stored": "rejected", "reason": "empty content"}

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    if category == "preference":
        return _store_preference(content, content_hash, project, summary, tags)
    else:
        return _store_candidate(content, content_hash, category, project, summary, tags)


def _ensure_project(conn, project):
    """Ensure project exists in projects table (FK constraint)."""
    if not project:
        return
    existing = conn.execute(
        "SELECT name FROM projects WHERE name = ?", (project,)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO projects (name) VALUES (?)", (project,))


def _store_preference(content, content_hash, project, summary, tags):
    """Preferences bypass staging → direct to wisdom.db notes."""
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM notes WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            return {"stored": "duplicate", "id": existing["id"], "category": "preference"}

        _ensure_project(conn, project)
        note_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO notes (id, content, summary, tags, category, project, "
            "content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (note_id, content, summary, tags, "preference", project, content_hash, now),
        )
        conn.commit()

        # Compute embedding (non-blocking for preferences)
        _compute_and_store_embedding(note_id, content, "wisdom")

        return {"stored": "wisdom", "id": note_id, "category": "preference"}
    finally:
        conn.close()


def _store_candidate(content, content_hash, category, project, summary, tags):
    """Non-preference notes go to abzu.db note_candidates."""
    from enki.db import get_abzu_db

    conn = get_abzu_db()
    try:
        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM note_candidates WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            return {"stored": "duplicate", "id": existing["id"], "category": category}

        candidate_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO note_candidates "
            "(id, content, summary, tags, category, project, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, content, summary, tags, category, project,
             content_hash, "manual"),
        )
        conn.commit()

        # Compute embedding
        _compute_and_store_embedding(candidate_id, content, "abzu")

        return {"stored": "staging", "id": candidate_id, "category": category}
    finally:
        conn.close()


def _compute_and_store_embedding(note_id: str, content: str, db: str):
    """Compute and store embedding. Fails silently if model unavailable."""
    try:
        from enki.embeddings import compute_embedding
        vec = compute_embedding(content)
        if vec == b"\x00" * len(vec):
            return  # Empty embedding, skip

        if db == "wisdom":
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            table = "embeddings"
        else:
            from enki.db import get_abzu_db
            conn = get_abzu_db()
            table = "candidate_embeddings"

        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (note_id, vector) VALUES (?, ?)",
                (note_id, vec),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Embedding computation skipped: %s", e)


# ---------------------------------------------------------------------------
# enki_recall — v4: hybrid search (FTS5 + embeddings)
# ---------------------------------------------------------------------------


def enki_recall(
    query: str | None = None,
    project: str | None = None,
    limit: int = 5,
    scope: str = "all",
    files: list[str] | None = None,
) -> list[dict] | dict:
    """Search Enki knowledge and/or structural codebase context.

    scope='knowledge'  -> memory only
    scope='codebase'   -> graph only
    scope='all'        -> merged (default)

    Backward compatibility:
    scope='project'|'global' map to knowledge mode.
    """
    scope_key = (scope or "all").strip().lower()
    resolved_project = _resolve_project(project)

    if scope_key == "index":
        from enki.db import wisdom_db

        with wisdom_db() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) as cnt "
                "FROM notes GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            total = sum(r["cnt"] for r in rows)

            recent = []
            for row in rows:
                latest = conn.execute(
                    "SELECT summary, project, created_at FROM notes "
                    "WHERE category = ? ORDER BY created_at DESC LIMIT 2",
                    (row["category"],),
                ).fetchall()
                for n in latest:
                    recent.append({
                        "category": row["category"],
                        "summary": n["summary"] or "",
                        "project": n["project"],
                    })

        return {
            "scope": "index",
            "total_notes": total,
            "by_category": [{"category": r["category"], "count": r["cnt"]} for r in rows],
            "recent": recent[:8],
            "hint": (
                "Call enki_recall(scope='task', files=[...]) for task-specific "
                "context. Call enki_recall(query='...') for full search."
            ),
        }

    if scope_key == "task":
        requested_files = files or []
        if not requested_files:
            return {"error": "scope='task' requires files=[...] parameter"}

        from enki.db import graph_db, graph_db_path, wisdom_db

        relevant_files = set(requested_files)
        if graph_db_path(resolved_project).exists():
            try:
                with graph_db(resolved_project) as gconn:
                    for f in requested_files[:5]:
                        blast = gconn.execute(
                            "SELECT affected_file FROM blast_radius "
                            "WHERE file_path = ? AND blast_score > 0.2",
                            (f,),
                        ).fetchall()
                        for b in blast:
                            relevant_files.add(b["affected_file"])
            except Exception:
                pass

        results_list = []
        with wisdom_db() as conn:
            for f in list(relevant_files)[:10]:
                fname = Path(f).name
                rows = conn.execute(
                    "SELECT id, content, category, summary, rationale, "
                    "alternatives_rejected, project, created_at "
                    "FROM notes "
                    "WHERE content LIKE ? OR summary LIKE ? "
                    "ORDER BY created_at DESC LIMIT 3",
                    (f"%{fname}%", f"%{fname}%"),
                ).fetchall()
                for r in rows:
                    note = dict(r)
                    alt = note.get("alternatives_rejected")
                    if isinstance(alt, str):
                        try:
                            note["alternatives_rejected"] = json.loads(alt)
                        except Exception:
                            pass
                    if note not in results_list:
                        results_list.append(note)

        return {
            "scope": "task",
            "files_searched": list(relevant_files)[:10],
            "notes": results_list[:12],
            "count": len(results_list),
        }

    if not query or not query.strip():
        return []

    results: list[dict] = []
    legacy_scope = scope_key in {"project", "global"}
    if legacy_scope:
        knowledge_scope = scope_key
        scope_key = "knowledge"
    else:
        knowledge_scope = "project"

    if scope_key in {"knowledge", "all"}:
        try:
            from enki.embeddings import hybrid_search

            proj = resolved_project if knowledge_scope == "project" else None
            knowledge_results = hybrid_search(query, project=proj, limit=limit)
            _update_access_timestamps(
                [r["note_id"] for r in knowledge_results if r.get("source_db") == "wisdom"]
            )
            for r in knowledge_results:
                r["source"] = "knowledge"
            results.extend(knowledge_results)
        except Exception as e:
            logger.warning("v4 hybrid search failed, falling back to v3: %s", e)
            try:
                from enki.memory.abzu import recall

                fallback = recall(
                    query=query,
                    scope=knowledge_scope,
                    project=resolved_project,
                    limit=limit,
                )
                for r in fallback:
                    r["source"] = "knowledge"
                results.extend(fallback)
            except Exception:
                pass

    if scope_key in {"codebase", "all"}:
        try:
            from enki.db import graph_db_path

            if graph_db_path(resolved_project).exists():
                graph_results = _search_graph(resolved_project, query, limit=limit)
                for r in graph_results:
                    r["source"] = "codebase"
                results.extend(graph_results)
        except Exception:
            pass

    return results[: limit * 2]


def _search_graph(project: str, query: str, limit: int = 5) -> list[dict]:
    """Search graph.db for files and symbols matching query."""
    from enki.db import graph_db

    results = []
    query_lower = query.lower()
    try:
        with graph_db(project) as conn:
            sym_rows = conn.execute(
                "SELECT s.name, s.kind, s.file_path, s.complexity, "
                "s.line_start, b.blast_score, b.risk_level "
                "FROM symbols s "
                "LEFT JOIN blast_radius b ON b.symbol_id = s.id "
                "WHERE LOWER(s.name) LIKE ? "
                "ORDER BY COALESCE(b.blast_score, -1) DESC "
                "LIMIT ?",
                (f"%{query_lower}%", limit),
            ).fetchall()

            for row in sym_rows:
                content = (
                    f"{row['kind']} `{row['name']}` "
                    f"in {row['file_path']} (line {row['line_start']})"
                )
                if row["blast_score"] and row["blast_score"] > 0.2:
                    content += (
                        f"\nBlast radius: {row['risk_level'].upper()} "
                        f"({row['blast_score']:.0%} of codebase imports this)"
                    )
                if row["complexity"] and row["complexity"] > 10:
                    content += f"\nComplexity: {row['complexity']} (high)"
                results.append({
                    "content": content,
                    "category": "codebase_symbol",
                    "file": row["file_path"],
                })

            file_rows = conn.execute(
                "SELECT f.path, f.language, f.symbol_count, "
                "MAX(b.blast_score) as max_blast "
                "FROM files f "
                "LEFT JOIN blast_radius b ON b.file_path = f.path "
                "WHERE LOWER(f.path) LIKE ? "
                "GROUP BY f.path "
                "ORDER BY COALESCE(max_blast, -1) DESC "
                "LIMIT ?",
                (f"%{query_lower}%", limit),
            ).fetchall()

            for row in file_rows:
                content = (
                    f"{row['path']} ({row['language']}, "
                    f"{row['symbol_count']} symbols)"
                )
                if row["max_blast"] and row["max_blast"] > 0.3:
                    content += "\nHigh blast radius — changes affect many files"
                results.append({
                    "content": content,
                    "category": "codebase_file",
                    "file": row["path"],
                })
    except Exception:
        pass
    return results


def _update_access_timestamps(note_ids: list[str]):
    """Update last_accessed for retrieved notes."""
    if not note_ids:
        return
    try:
        from enki.db import get_wisdom_db
        conn = get_wisdom_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" for _ in note_ids)
            conn.execute(
                f"UPDATE notes SET last_accessed = ? WHERE id IN ({placeholders})",
                [now] + note_ids,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # Access tracking is best-effort


# ---------------------------------------------------------------------------
# enki_star — v4: stars notes (not beads)
# ---------------------------------------------------------------------------


def enki_star(bead_id: str) -> dict:
    """Star a note — starred notes never decay.

    Accepts both note IDs (v4) and bead IDs (v3) for backward compat.
    """
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        # Try v4 notes table first
        cursor = conn.execute(
            "UPDATE notes SET starred = 1 WHERE id = ?", (bead_id,)
        )
        if cursor.rowcount > 0:
            conn.commit()
            return {"starred": True, "note_id": bead_id}

        # Fall back to v3 beads table
        cursor = conn.execute(
            "UPDATE beads SET starred = 1 WHERE id = ?", (bead_id,)
        )
        if cursor.rowcount > 0:
            conn.commit()
            return {"starred": True, "bead_id": bead_id}

        return {"starred": False, "error": "Note not found"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# enki_status — v4: note model terminology
# ---------------------------------------------------------------------------


def enki_status(project: str | None = None) -> dict:
    """Get memory system health: note counts, staging depth, decay stats."""
    from enki.db import DB_DIR, ENKI_ROOT, get_abzu_db, get_wisdom_db
    resolved_project = _resolve_project(project) if project is not None else None

    # v4 note counts
    v4_notes = {}
    try:
        conn = get_wisdom_db()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM notes").fetchone()["c"]
            v4_notes["total"] = total
            for cat in ("decision", "learning", "pattern", "fix",
                        "preference", "code_knowledge"):
                cnt = conn.execute(
                    "SELECT COUNT(*) as c FROM notes WHERE category = ?", (cat,)
                ).fetchone()["c"]
                if cnt > 0:
                    v4_notes[cat] = cnt
            starred = conn.execute(
                "SELECT COUNT(*) as c FROM notes WHERE starred = 1"
            ).fetchone()["c"]
            v4_notes["starred"] = starred
        finally:
            conn.close()
    except Exception:
        v4_notes["total"] = 0

    # v4 staging counts
    v4_staging = {}
    try:
        conn = get_abzu_db()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM note_candidates"
            ).fetchone()["c"]
            v4_staging["candidates"] = total
            raw = conn.execute(
                "SELECT COUNT(*) as c FROM note_candidates WHERE status = 'raw'"
            ).fetchone()["c"]
            enriched = conn.execute(
                "SELECT COUNT(*) as c FROM note_candidates WHERE status = 'enriched'"
            ).fetchone()["c"]
            v4_staging["raw"] = raw
            v4_staging["enriched"] = enriched
            proposals = conn.execute(
                "SELECT COUNT(*) as c FROM evolution_proposals WHERE status = 'pending'"
            ).fetchone()["c"]
            v4_staging["pending_proposals"] = proposals
        finally:
            conn.close()
    except Exception:
        v4_staging["candidates"] = 0

    # v3 bead counts (backward compat)
    v3_beads = {}
    try:
        conn = get_wisdom_db()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM beads").fetchone()["c"]
            v3_beads["total"] = total
        finally:
            conn.close()
    except Exception:
        v3_beads["total"] = 0

    # DB sizes
    db_sizes = {}
    for db_name in ["wisdom.db", "abzu.db"]:
        for base in [DB_DIR, ENKI_ROOT]:
            path = base / db_name
            if path.exists():
                db_sizes[db_name] = os.path.getsize(path)
                break

    return {
        "project": resolved_project,
        "notes": v4_notes,
        "staging": v4_staging,
        "v3_beads": v3_beads,
        "db_sizes": db_sizes,
    }


def enki_memory_lint(project: str | None = None) -> dict:
    """Health check for wisdom.db memory. Report-only; never mutates notes."""
    from datetime import datetime, timedelta
    from enki.db import wisdom_db

    _ = project
    issues = {
        "contradictions": [],
        "stale": [],
        "orphans": [],
        "missing_rationale": [],
    }

    with wisdom_db() as conn:
        rows = conn.execute(
            "SELECT id, summary, created_at FROM notes "
            "WHERE category = 'decision' "
            "AND (rationale IS NULL OR rationale = '') "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            issues["missing_rationale"].append({
                "id": r["id"][:8],
                "summary": r["summary"] or "(no summary)",
                "created_at": r["created_at"],
            })

        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        rows = conn.execute(
            "SELECT id, category, summary, created_at FROM notes "
            "WHERE created_at < ? ORDER BY created_at ASC LIMIT 20",
            (cutoff,),
        ).fetchall()
        for r in rows:
            age_days = None
            try:
                age_days = (datetime.now() - datetime.fromisoformat(r["created_at"])).days
            except Exception:
                age_days = 0
            issues["stale"].append({
                "id": r["id"][:8],
                "category": r["category"],
                "summary": r["summary"] or "(no summary)",
                "age_days": age_days,
            })

        rows = conn.execute(
            "SELECT n.id, n.category, n.summary FROM notes n "
            "LEFT JOIN note_links l ON n.id = l.source_id "
            "WHERE l.source_id IS NULL "
            "LIMIT 20"
        ).fetchall()
        for r in rows:
            issues["orphans"].append({
                "id": r["id"][:8],
                "category": r["category"],
                "summary": r["summary"] or "(no summary)",
            })

    report_path = (
        Path.home() / ".enki"
        / f"memory-lint-{datetime.now().strftime('%Y-%m-%d')}.md"
    )
    lines = [
        f"# Memory lint — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        f"- Missing rationale (decisions): {len(issues['missing_rationale'])}",
        f"- Stale notes (90+ days): {len(issues['stale'])}",
        f"- Orphan notes (no links): {len(issues['orphans'])}",
        "",
    ]

    if issues["missing_rationale"]:
        lines += ["## Decisions missing rationale", ""]
        for item in issues["missing_rationale"]:
            lines.append(f"- [{item['id']}] {item['summary']}")
        lines.append("")

    if issues["stale"]:
        lines += ["## Stale notes", ""]
        for item in issues["stale"]:
            lines.append(
                f"- [{item['id']}] ({item['category']}, "
                f"{item['age_days']}d old) {item['summary']}"
            )
        lines.append("")

    if issues["orphans"]:
        lines += ["## Orphan notes (no links)", ""]
        for item in issues["orphans"]:
            lines.append(f"- [{item['id']}] ({item['category']}) {item['summary']}")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines))

    total_issues = sum(len(v) for v in issues.values())
    return {
        "message": f"Memory lint complete. {total_issues} issues found.",
        "issues": {k: len(v) for k, v in issues.items()},
        "report_path": str(report_path),
        "next": (
            "Review the lint report. "
            "Missing rationale: add context to those decisions. "
            "Stale notes: verify still accurate or discard. "
            "Orphans: connect to related notes or discard."
        ),
    }


# ---------------------------------------------------------------------------
# enki_restore — NEW: compaction recovery
# ---------------------------------------------------------------------------


def enki_restore(project: str | None = None) -> dict:
    """Return latest pre-compact snapshot + persona + enforcement state.

    Used as fallback when CLAUDE.md compaction instructions fail.
    Capped at ~1.5-2K tokens (~6000 chars).
    """
    parts = []
    MAX_CHARS = 6000
    resolved_project = _resolve_project(project) if project is not None else project

    # 1. Persona identity (compact)
    parts.append("# Session Restored")
    parts.append("")
    parts.append("**You ARE Enki.** Collaborator, craftsman, keeper of knowledge.")
    parts.append("Direct, opinionated, no filler.")
    parts.append("")

    # 2. Enforcement state
    try:
        from enki.gates.uru import inject_enforcement_context
        enforcement = inject_enforcement_context()
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

        parts.append(f"**Project:** {resolved_project or project_line or 'unknown'}")
        parts.append(f"**Goal:** {goal_line or 'NOT SET'}")
        parts.append(f"**Phase:** {phase_line or 'NOT SET'} | **Tier:** {tier_line or 'unknown'}")
        parts.append("")
    except Exception:
        if resolved_project:
            parts.append(f"**Project:** {resolved_project}")
        parts.append("")

    # 3. Latest session summary
    try:
        from enki.db import get_abzu_db
        conn = get_abzu_db()
        try:
            row = conn.execute(
                "SELECT goal, phase, operational_state, conversational_state "
                "FROM session_summaries "
                "WHERE is_final = 0 "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                if row["goal"]:
                    parts.append(f"**Active Goal:** {row['goal']}")
                if row["operational_state"]:
                    state = row["operational_state"][:1000]
                    parts.append(f"**Working State:** {state}")
                parts.append("")
        finally:
            conn.close()
    except Exception:
        pass

    # 4. Recent relevant notes (up to 3)
    if resolved_project:
        try:
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                rows = conn.execute(
                    "SELECT content, category FROM notes "
                    "WHERE project = ? ORDER BY last_accessed DESC LIMIT 3",
                    (resolved_project,),
                ).fetchall()
                if rows:
                    parts.append("**Recent Knowledge:**")
                    for r in rows:
                        parts.append(f"- [{r['category']}] {r['content'][:150]}")
                    parts.append("")
            finally:
                conn.close()
        except Exception:
            pass

    # 5. Enforcement reminder
    parts.append("**Enforcement:** Gates active. Goal before code. Spec before implementation.")

    result = "\n".join(parts)
    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + "\n\n[Truncated to fit token budget]"

    return {
        "restored": True,
        "content": result,
        "chars": len(result),
    }
