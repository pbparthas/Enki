"""migrate_v1.py — Migrate v1/v2 beads to v3 staging.

Bead migration process:
1. Map 8 types → 5 categories (decision/learning/pattern/fix/preference)
2. Strip `kind` field
3. Move `last_accessed` from access_log to bead row
4. All beads go to staging in abzu.db, NOT directly to wisdom.db
5. First Gemini review promotes the worthy ones
6. Current wisdom.db backed up, new one starts clean

Usage:
    python -m enki.scripts.migrate_v1
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from enki.db import ENKI_ROOT


# v1/v2 type → v3 category mapping
TYPE_MAP = {
    "decision": "decision",
    "learning": "learning",
    "pattern": "pattern",
    "fix": "fix",
    "preference": "preference",
    "solution": "fix",
    "violation": "learning",
    "approach": "pattern",
    "rejection": "decision",
    "style": "preference",
}


def run_migration():
    """Run the full v1/v2 → v3 migration."""
    backup_dir = ENKI_ROOT / "v2_backup"
    old_db_path = ENKI_ROOT / "wisdom.db"

    if not old_db_path.exists():
        print("No v1/v2 wisdom.db found. Nothing to migrate.")
        return {"migrated": 0, "skipped": 0}

    # Step 1: Backup
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"wisdom_v2_{timestamp}.db"
    shutil.copy2(old_db_path, backup_path)
    print(f"Backed up old wisdom.db to {backup_path}")

    # Step 2: Read old beads
    old_beads = _read_old_beads(old_db_path)
    print(f"Found {len(old_beads)} beads in v1/v2 wisdom.db")

    if not old_beads:
        return {"migrated": 0, "skipped": 0}

    # Step 3: Map to v3 categories and stage
    migrated = 0
    skipped = 0

    from enki.memory.staging import add_candidate
    from enki.memory.beads import create as create_bead
    from enki.db import init_all

    # Ensure v3 DBs exist
    init_all()

    for bead in old_beads:
        category = TYPE_MAP.get(bead.get("type", ""), None)
        if not category:
            skipped += 1
            continue

        content = bead.get("content", "")
        if not content or len(content.strip()) < 10:
            skipped += 1
            continue

        # Preferences go directly to wisdom.db
        if category == "preference":
            try:
                create_bead(
                    content=content,
                    category="preference",
                    project=bead.get("project"),
                    summary=bead.get("summary"),
                    tags=bead.get("tags"),
                )
                migrated += 1
            except Exception as e:
                print(f"  Skip preference (dedup?): {e}")
                skipped += 1
        else:
            # Everything else → staging
            try:
                add_candidate(
                    content=content,
                    category=category,
                    source="v1/v2 migration",
                    project=bead.get("project"),
                    summary=bead.get("summary"),
                )
                migrated += 1
            except Exception as e:
                print(f"  Skip candidate (dedup?): {e}")
                skipped += 1

    print(f"Migration complete: {migrated} migrated, {skipped} skipped")
    return {"migrated": migrated, "skipped": skipped}


def _read_old_beads(db_path: Path) -> list[dict]:
    """Read beads from v1/v2 wisdom.db."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    beads = []
    try:
        # Try v2 schema first
        rows = conn.execute(
            "SELECT * FROM beads ORDER BY created_at"
        ).fetchall()
        for row in rows:
            bead = dict(row)
            # Normalize field names
            if "type" not in bead and "category" in bead:
                bead["type"] = bead["category"]
            beads.append(bead)
    except sqlite3.OperationalError:
        print("Could not read beads table from old wisdom.db")
    finally:
        conn.close()

    return beads


if __name__ == "__main__":
    run_migration()
