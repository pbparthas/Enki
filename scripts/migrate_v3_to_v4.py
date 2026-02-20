#!/usr/bin/env python3
"""migrate_v3_to_v4.py — Migrate v3 beads to v4 note schema (Item 5.1).

Migrates:
- wisdom.db beads (preference) → wisdom.db notes directly
- wisdom.db beads (non-preference) → abzu.db note_candidates (status=raw)
- abzu.db bead_candidates → abzu.db note_candidates (status=raw)

Category mapping:
  decision → decision, learning → learning, pattern → pattern,
  fix → fix, solution → fix, violation → learning

Pre-migration: backs up DB files as *.v3.bak
Post-migration: v3 tables remain (manual DROP after verification)

Usage:
    python scripts/migrate_v3_to_v4.py [--dry-run] [--enki-root ~/.enki]
"""

import argparse
import hashlib
import logging
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Category mapping: v3 → v4
CATEGORY_MAP = {
    "decision": "decision",
    "learning": "learning",
    "pattern": "pattern",
    "fix": "fix",
    "solution": "fix",
    "violation": "learning",
    "preference": "preference",
}


def _connect(path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_project(conn: sqlite3.Connection, project: str | None) -> None:
    """Ensure project exists in projects table for FK constraint."""
    if not project:
        return
    existing = conn.execute(
        "SELECT name FROM projects WHERE name = ?", (project,)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO projects (name) VALUES (?)", (project,))


def backup_databases(enki_root: Path) -> dict:
    """Back up DB files as *.v3.bak. Returns paths backed up."""
    db_dir = enki_root / "db"
    backed_up = {}

    for name in ("wisdom.db", "abzu.db"):
        # Check both new and old locations
        for parent in (db_dir, enki_root):
            src = parent / name
            if src.exists():
                dst = src.with_suffix(".db.v3.bak")
                if dst.exists():
                    logger.warning("Backup already exists: %s (skipping)", dst)
                else:
                    shutil.copy2(str(src), str(dst))
                    logger.info("Backed up: %s → %s", src, dst)
                backed_up[name] = str(dst)
                break

    return backed_up


def read_v3_beads(wisdom_conn: sqlite3.Connection) -> list[dict]:
    """Read all v3 beads from wisdom.db."""
    if not _table_exists(wisdom_conn, "beads"):
        logger.info("No v3 beads table found — nothing to migrate")
        return []

    rows = wisdom_conn.execute(
        "SELECT id, content, summary, category, project, weight, starred, "
        "content_hash, tags, context, created_at, last_accessed, promoted_at "
        "FROM beads ORDER BY created_at"
    ).fetchall()

    return [dict(r) for r in rows]


def read_v3_candidates(abzu_conn: sqlite3.Connection) -> list[dict]:
    """Read all v3 bead_candidates from abzu.db."""
    if not _table_exists(abzu_conn, "bead_candidates"):
        logger.info("No v3 bead_candidates table found — nothing to migrate")
        return []

    rows = abzu_conn.execute(
        "SELECT id, content, summary, category, project, content_hash, "
        "source, session_id, created_at "
        "FROM bead_candidates ORDER BY created_at"
    ).fetchall()

    return [dict(r) for r in rows]


def map_category(v3_category: str) -> str:
    """Map v3 category to v4 category."""
    mapped = CATEGORY_MAP.get(v3_category)
    if not mapped:
        logger.warning("Unknown v3 category '%s' — mapping to 'learning'", v3_category)
        return "learning"
    return mapped


def migrate_bead_to_note(bead: dict) -> dict:
    """Convert a v3 bead to a v4 note record (for preferences → wisdom.db)."""
    v4_category = map_category(bead["category"])
    now = datetime.now(timezone.utc).isoformat()

    return {
        "id": bead["id"],
        "content": bead["content"],
        "summary": bead.get("summary"),
        "context_description": bead.get("context"),
        "keywords": None,
        "tags": bead.get("tags"),
        "category": v4_category,
        "project": bead.get("project"),
        "file_ref": None,
        "file_hash": None,
        "last_verified": None,
        "weight": bead.get("weight", 1.0),
        "starred": bead.get("starred", 0),
        "content_hash": bead["content_hash"],
        "created_at": bead.get("created_at"),
        "last_accessed": bead.get("last_accessed"),
        "evolved_at": None,
        "promoted_at": now,
    }


def migrate_bead_to_candidate(bead: dict) -> dict:
    """Convert a v3 bead to a v4 note_candidate record."""
    v4_category = map_category(bead["category"])

    return {
        "id": bead["id"],
        "content": bead["content"],
        "summary": bead.get("summary"),
        "context_description": bead.get("context"),
        "keywords": None,
        "tags": bead.get("tags"),
        "category": v4_category,
        "project": bead.get("project"),
        "status": "raw",
        "file_ref": None,
        "file_hash": None,
        "content_hash": bead["content_hash"],
        "source": "manual",
        "session_id": None,
        "created_at": bead.get("created_at"),
    }


def migrate_v3_candidate_to_v4(candidate: dict) -> dict:
    """Convert a v3 bead_candidate to a v4 note_candidate record."""
    v4_category = map_category(candidate["category"])
    source = candidate.get("source", "manual")

    # Validate source against v4 CHECK constraint
    valid_sources = {"manual", "session_end", "code_scan", "onboarding", "rescan", "em_distill"}
    if source not in valid_sources:
        source = "manual"

    return {
        "id": candidate["id"],
        "content": candidate["content"],
        "summary": candidate.get("summary"),
        "context_description": None,
        "keywords": None,
        "tags": None,
        "category": v4_category,
        "project": candidate.get("project"),
        "status": "raw",
        "file_ref": None,
        "file_hash": None,
        "content_hash": candidate["content_hash"],
        "source": source,
        "session_id": candidate.get("session_id"),
        "created_at": candidate.get("created_at"),
    }


def insert_note(conn: sqlite3.Connection, note: dict) -> bool:
    """Insert a note into wisdom.db notes table. Returns True if inserted."""
    try:
        _ensure_project(conn, note.get("project"))
        conn.execute(
            "INSERT OR IGNORE INTO notes "
            "(id, content, summary, context_description, keywords, tags, "
            "category, project, file_ref, file_hash, last_verified, "
            "weight, starred, content_hash, created_at, last_accessed, "
            "evolved_at, promoted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note["id"], note["content"], note["summary"],
                note["context_description"], note["keywords"], note["tags"],
                note["category"], note["project"],
                note["file_ref"], note["file_hash"], note["last_verified"],
                note["weight"], note["starred"], note["content_hash"],
                note["created_at"], note["last_accessed"],
                note["evolved_at"], note["promoted_at"],
            ),
        )
        return True
    except Exception as e:
        logger.error("Failed to insert note %s: %s", note["id"], e)
        return False


def insert_candidate(conn: sqlite3.Connection, candidate: dict) -> bool:
    """Insert a note_candidate into abzu.db. Returns True if inserted."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO note_candidates "
            "(id, content, summary, context_description, keywords, tags, "
            "category, project, status, file_ref, file_hash, content_hash, "
            "source, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate["id"], candidate["content"], candidate["summary"],
                candidate["context_description"], candidate["keywords"],
                candidate["tags"], candidate["category"], candidate["project"],
                candidate["status"], candidate["file_ref"], candidate["file_hash"],
                candidate["content_hash"], candidate["source"],
                candidate["session_id"], candidate["created_at"],
            ),
        )
        return True
    except Exception as e:
        logger.error("Failed to insert candidate %s: %s", candidate["id"], e)
        return False


def run_migration(enki_root: Path, dry_run: bool = False) -> dict:
    """Execute the full v3 → v4 migration.

    Returns summary dict with counts and status.
    """
    db_dir = enki_root / "db"

    # Find DB paths
    wisdom_path = db_dir / "wisdom.db" if (db_dir / "wisdom.db").exists() else enki_root / "wisdom.db"
    abzu_path = db_dir / "abzu.db" if (db_dir / "abzu.db").exists() else enki_root / "abzu.db"

    if not wisdom_path.exists():
        return {"error": f"wisdom.db not found at {wisdom_path}"}

    summary = {
        "beads_found": 0,
        "preferences_to_notes": 0,
        "beads_to_candidates": 0,
        "v3_candidates_migrated": 0,
        "errors": 0,
        "dry_run": dry_run,
        "skipped_existing": 0,
    }

    # Phase 1: Read v3 data
    wisdom_conn = _connect(wisdom_path)
    beads = read_v3_beads(wisdom_conn)
    summary["beads_found"] = len(beads)
    logger.info("Found %d v3 beads in wisdom.db", len(beads))

    abzu_conn = None
    v3_candidates = []
    if abzu_path.exists():
        abzu_conn = _connect(abzu_path)
        v3_candidates = read_v3_candidates(abzu_conn)
        logger.info("Found %d v3 bead_candidates in abzu.db", len(v3_candidates))

    if dry_run:
        # Count what would happen
        for bead in beads:
            cat = map_category(bead["category"])
            if cat == "preference":
                summary["preferences_to_notes"] += 1
            else:
                summary["beads_to_candidates"] += 1
        summary["v3_candidates_migrated"] = len(v3_candidates)
        wisdom_conn.close()
        if abzu_conn:
            abzu_conn.close()
        return summary

    # Phase 2: Backup
    backup_databases(enki_root)

    # Phase 3: Migrate beads
    if not abzu_conn and abzu_path.parent.exists():
        abzu_conn = _connect(abzu_path)

    for bead in beads:
        v4_category = map_category(bead["category"])

        if v4_category == "preference":
            # Preferences → wisdom.db notes directly
            note = migrate_bead_to_note(bead)
            if insert_note(wisdom_conn, note):
                summary["preferences_to_notes"] += 1
            else:
                summary["errors"] += 1
        else:
            # Non-preferences → abzu.db note_candidates
            candidate = migrate_bead_to_candidate(bead)
            if abzu_conn and insert_candidate(abzu_conn, candidate):
                summary["beads_to_candidates"] += 1
            else:
                summary["errors"] += 1

    # Phase 4: Migrate v3 bead_candidates → v4 note_candidates
    for v3_cand in v3_candidates:
        v4_cand = migrate_v3_candidate_to_v4(v3_cand)
        if abzu_conn and insert_candidate(abzu_conn, v4_cand):
            summary["v3_candidates_migrated"] += 1
        else:
            summary["errors"] += 1

    # Commit and close
    wisdom_conn.commit()
    wisdom_conn.close()
    if abzu_conn:
        abzu_conn.commit()
        abzu_conn.close()

    logger.info("Migration complete: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Enki v3 beads to v4 note schema"
    )
    parser.add_argument(
        "--enki-root",
        type=Path,
        default=Path.home() / ".enki",
        help="Enki root directory (default: ~/.enki)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    args = parser.parse_args()

    if not args.enki_root.exists():
        logger.error("Enki root not found: %s", args.enki_root)
        sys.exit(1)

    result = run_migration(args.enki_root, dry_run=args.dry_run)

    if "error" in result:
        logger.error(result["error"])
        sys.exit(1)

    print("\n=== Migration Summary ===")
    print(f"  Beads found:              {result['beads_found']}")
    print(f"  Preferences → notes:      {result['preferences_to_notes']}")
    print(f"  Beads → candidates:       {result['beads_to_candidates']}")
    print(f"  v3 candidates migrated:   {result['v3_candidates_migrated']}")
    print(f"  Errors:                   {result['errors']}")
    if result["dry_run"]:
        print("\n  (DRY RUN — no changes made)")


if __name__ == "__main__":
    main()
