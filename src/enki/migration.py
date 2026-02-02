"""Migration from Odin and Freyja to Enki.

Phase 0: Migrate all existing data to the unified Enki system.
"""

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .db import init_db, get_db, ENKI_DIR, DB_PATH


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row to dict, handling None."""
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


@dataclass
class MigrationResult:
    """Result of a migration operation."""
    beads_migrated: int = 0
    sessions_migrated: int = 0
    projects_migrated: int = 0
    embeddings_generated: int = 0
    hooks_archived: int = 0
    hooks_installed: int = 0
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# Paths
ODIN_GLOBAL_DIR = Path.home() / ".odin"
ODIN_DB = ODIN_GLOBAL_DIR / "odin.db"
FREYJA_GLOBAL_DIR = Path.home() / ".freyja"
FREYJA_DB = FREYJA_GLOBAL_DIR / "wisdom.db"
HOOKS_DIR = Path.home() / ".claude" / "hooks"


def migrate_to_enki(
    generate_embeddings: bool = True,
    archive_hooks: bool = True,
    install_hooks: bool = True,
) -> MigrationResult:
    """Migrate all data from Odin and Freyja to Enki.

    Args:
        generate_embeddings: Whether to generate embeddings for migrated beads
        archive_hooks: Whether to archive old Odin/Freyja hooks
        install_hooks: Whether to install Enki hooks

    Returns:
        MigrationResult with counts of migrated items
    """
    result = MigrationResult()

    # 1. Initialize Enki database
    init_db()

    # 2. Migrate Odin data
    if ODIN_DB.exists():
        try:
            odin_result = _migrate_odin()
            result.beads_migrated += odin_result["beads"]
            result.sessions_migrated += odin_result["sessions"]
            result.projects_migrated += odin_result["projects"]
        except Exception as e:
            result.errors.append(f"Odin migration error: {e}")

    # 3. Migrate Freyja data
    if FREYJA_DB.exists():
        try:
            freyja_result = _migrate_freyja()
            result.beads_migrated += freyja_result["beads"]
        except Exception as e:
            result.errors.append(f"Freyja migration error: {e}")

    # 4. Migrate project-level data
    try:
        project_count = _migrate_all_projects()
        result.projects_migrated += project_count
    except Exception as e:
        result.errors.append(f"Project migration error: {e}")

    # 5. Generate embeddings for migrated beads
    if generate_embeddings:
        try:
            result.embeddings_generated = _generate_all_embeddings()
        except Exception as e:
            result.errors.append(f"Embedding generation error: {e}")

    # 6. Archive old hooks
    if archive_hooks:
        try:
            result.hooks_archived = _archive_old_hooks()
        except Exception as e:
            result.errors.append(f"Hook archival error: {e}")

    # 7. Install Enki hooks
    if install_hooks:
        try:
            result.hooks_installed = _install_enki_hooks()
        except Exception as e:
            result.errors.append(f"Hook installation error: {e}")

    return result


def _migrate_odin(odin_db_path: Optional[Path] = None) -> dict:
    """Migrate data from Odin database."""
    result = {"beads": 0, "sessions": 0, "projects": 0}

    db_path = odin_db_path or ODIN_DB
    if not db_path.exists():
        return result

    odin_conn = sqlite3.connect(db_path)
    odin_conn.row_factory = sqlite3.Row
    enki_db = get_db()

    # Check what tables exist in Odin
    tables = odin_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t["name"] for t in tables}

    # Migrate beads
    if "beads" in table_names:
        beads = odin_conn.execute("SELECT * FROM beads").fetchall()

        for row in beads:
            try:
                bead = _row_to_dict(row)
                # Map Odin types to Enki types if needed
                bead_type = _map_bead_type(bead.get("type") or "learning")

                # Extract project from metadata if present
                metadata = {}
                if bead.get("metadata"):
                    try:
                        metadata = json.loads(bead["metadata"])
                    except:
                        pass

                project = metadata.get("project") or bead.get("project")

                # Check if bead already exists (avoid duplicates)
                new_id = f"odin_{bead['id']}"
                existing = enki_db.execute(
                    "SELECT id FROM beads WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    enki_db.execute("""
                        INSERT INTO beads (id, content, summary, type, project, weight,
                                          starred, context, tags, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        new_id,
                        bead.get("content") or "",
                        bead.get("summary"),
                        bead_type,
                        project,
                        bead.get("weight") or 1.0,
                        bead.get("starred") or 0,
                        f"Migrated from Odin on {datetime.now().isoformat()}",
                        bead.get("tags"),
                        bead.get("created_at"),
                    ))
                    result["beads"] += 1

            except Exception as e:
                # Log but continue
                pass

    # Migrate sessions
    if "sessions" in table_names:
        sessions = odin_conn.execute("SELECT * FROM sessions").fetchall()

        for row in sessions:
            try:
                session = _row_to_dict(row)
                new_id = f"odin_{session['id']}"
                existing = enki_db.execute(
                    "SELECT id FROM sessions WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    enki_db.execute("""
                        INSERT INTO sessions (id, project_id, started_at, ended_at,
                                             goal, summary)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        new_id,
                        session.get("project_id"),
                        session.get("started_at"),
                        session.get("ended_at"),
                        session.get("goal"),
                        session.get("summary"),
                    ))
                    result["sessions"] += 1

            except Exception as e:
                pass

    # Migrate projects
    if "projects" in table_names:
        projects = odin_conn.execute("SELECT * FROM projects").fetchall()

        for row in projects:
            try:
                project = _row_to_dict(row)
                existing = enki_db.execute(
                    "SELECT id FROM projects WHERE id = ?", (project["id"],)
                ).fetchone()

                if not existing:
                    enki_db.execute("""
                        INSERT INTO projects (id, name, path, created_at, last_session)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        project["id"],
                        project.get("name") or "",
                        project.get("path") or "",
                        project.get("created_at"),
                        project.get("last_session"),
                    ))
                    result["projects"] += 1

            except Exception as e:
                pass

    enki_db.commit()
    odin_conn.close()

    return result


def _migrate_freyja() -> dict:
    """Migrate data from Freyja database."""
    result = {"beads": 0}

    freyja_conn = sqlite3.connect(FREYJA_DB)
    freyja_conn.row_factory = sqlite3.Row
    enki_db = get_db()

    # Check what tables exist
    tables = freyja_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t["name"] for t in tables}

    # Freyja's main tables are typically: decisions, solutions, learnings, wisdom
    # These map to Enki bead types

    # Migrate decisions
    if "decisions" in table_names:
        rows = freyja_conn.execute("SELECT * FROM decisions").fetchall()
        for r in rows:
            try:
                row = _row_to_dict(r)
                new_id = f"freyja_decision_{row.get('id', '')}"
                existing = enki_db.execute(
                    "SELECT id FROM beads WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    content = row.get("content") or row.get("decision") or ""
                    if row.get("why"):
                        content = f"{content}\n\nWhy: {row['why']}"

                    enki_db.execute("""
                        INSERT INTO beads (id, content, summary, type, project, weight,
                                          context, created_at)
                        VALUES (?, ?, ?, 'decision', ?, 1.0, ?, ?)
                    """, (
                        new_id,
                        content,
                        row.get("title") or row.get("summary"),
                        row.get("project"),
                        f"Migrated from Freyja on {datetime.now().isoformat()}",
                        row.get("created_at"),
                    ))
                    result["beads"] += 1
            except:
                pass

    # Migrate solutions
    if "solutions" in table_names:
        rows = freyja_conn.execute("SELECT * FROM solutions").fetchall()
        for r in rows:
            try:
                row = _row_to_dict(r)
                new_id = f"freyja_solution_{row.get('id', '')}"
                existing = enki_db.execute(
                    "SELECT id FROM beads WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    content = f"Problem: {row.get('problem') or ''}\n\nSolution: {row.get('solution') or ''}"
                    if row.get("gotcha"):
                        content += f"\n\nGotcha: {row['gotcha']}"

                    enki_db.execute("""
                        INSERT INTO beads (id, content, summary, type, project, weight,
                                          context, created_at)
                        VALUES (?, ?, ?, 'solution', ?, 1.0, ?, ?)
                    """, (
                        new_id,
                        content,
                        (row.get("problem") or "")[:100],
                        row.get("project"),
                        f"Migrated from Freyja on {datetime.now().isoformat()}",
                        row.get("created_at"),
                    ))
                    result["beads"] += 1
            except:
                pass

    # Migrate learnings
    if "learnings" in table_names:
        rows = freyja_conn.execute("SELECT * FROM learnings").fetchall()
        for r in rows:
            try:
                row = _row_to_dict(r)
                new_id = f"freyja_learning_{row.get('id', '')}"
                existing = enki_db.execute(
                    "SELECT id FROM beads WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    category = row.get("category") or "general"
                    content = f"[{category}] {row.get('content') or ''}"

                    enki_db.execute("""
                        INSERT INTO beads (id, content, summary, type, project, weight,
                                          context, created_at)
                        VALUES (?, ?, ?, 'learning', ?, 1.0, ?, ?)
                    """, (
                        new_id,
                        content,
                        (row.get("content") or "")[:100],
                        row.get("project"),
                        f"Migrated from Freyja on {datetime.now().isoformat()}",
                        row.get("created_at"),
                    ))
                    result["beads"] += 1
            except:
                pass

    # Migrate generic wisdom table if it exists
    if "wisdom" in table_names:
        rows = freyja_conn.execute("SELECT * FROM wisdom").fetchall()
        for r in rows:
            try:
                row = _row_to_dict(r)
                new_id = f"freyja_wisdom_{row.get('id', '')}"
                existing = enki_db.execute(
                    "SELECT id FROM beads WHERE id = ?", (new_id,)
                ).fetchone()

                if not existing:
                    bead_type = _map_bead_type(row.get("type") or "learning")
                    enki_db.execute("""
                        INSERT INTO beads (id, content, summary, type, project, weight,
                                          context, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        new_id,
                        row.get("content") or "",
                        row.get("summary"),
                        bead_type,
                        row.get("project"),
                        row.get("weight") or 1.0,
                        f"Migrated from Freyja on {datetime.now().isoformat()}",
                        row.get("created_at"),
                    ))
                    result["beads"] += 1
            except:
                pass

    enki_db.commit()
    freyja_conn.close()

    return result


def _migrate_all_projects() -> int:
    """Migrate project-level data for all known projects."""
    count = 0

    # Find all projects with .odin or .freyja directories
    potential_projects = set()

    # Check projects registered in databases
    if ODIN_DB.exists():
        try:
            conn = sqlite3.connect(ODIN_DB)
            conn.row_factory = sqlite3.Row
            projects = conn.execute(
                "SELECT path FROM projects WHERE path IS NOT NULL"
            ).fetchall()
            for p in projects:
                if p["path"]:
                    potential_projects.add(Path(p["path"]))
            conn.close()
        except:
            pass

    if FREYJA_DB.exists():
        try:
            conn = sqlite3.connect(FREYJA_DB)
            conn.row_factory = sqlite3.Row
            # Freyja might store project paths differently
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if "projects" in {t["name"] for t in tables}:
                projects = conn.execute("SELECT path FROM projects").fetchall()
                for p in projects:
                    if p.get("path"):
                        potential_projects.add(Path(p["path"]))
            conn.close()
        except:
            pass

    # Migrate each project
    for project_path in potential_projects:
        if project_path.exists():
            try:
                _migrate_project(project_path)
                count += 1
            except:
                pass

    return count


def _migrate_project(project_path: Path) -> None:
    """Migrate a single project's data."""
    odin_dir = project_path / ".odin"
    freyja_dir = project_path / ".freyja"
    enki_dir = project_path / ".enki"

    enki_dir.mkdir(exist_ok=True)

    # Migrate MEMORY.md (prefer Freyja, fall back to Odin)
    memory_src = None
    if (freyja_dir / "MEMORY.md").exists():
        memory_src = freyja_dir / "MEMORY.md"
    elif (odin_dir / "MEMORY.md").exists():
        memory_src = odin_dir / "MEMORY.md"

    if memory_src and not (enki_dir / "MEMORY.md").exists():
        shutil.copy(memory_src, enki_dir / "MEMORY.md")

    # Migrate RUNNING.md
    running_src = None
    if (freyja_dir / "RUNNING.md").exists():
        running_src = freyja_dir / "RUNNING.md"
    elif (odin_dir / "RUNNING.md").exists():
        running_src = odin_dir / "RUNNING.md"

    if running_src:
        # Append migration note to existing or create new
        running_dest = enki_dir / "RUNNING.md"
        content = running_src.read_text() if running_src.exists() else ""
        migration_note = f"\n\n---\nMigrated to Enki on {datetime.now().isoformat()}\n"
        running_dest.write_text(content + migration_note)

    # Migrate specs
    for spec_dir in [odin_dir / "specs", freyja_dir / "specs"]:
        if spec_dir.exists():
            enki_specs_dir = enki_dir / "specs"
            enki_specs_dir.mkdir(exist_ok=True)
            for spec in spec_dir.glob("*.md"):
                dest = enki_specs_dir / spec.name
                if not dest.exists():
                    shutil.copy(spec, dest)

    # Initialize PHASE file if not exists
    phase_file = enki_dir / "PHASE"
    if not phase_file.exists():
        phase_file.write_text("intake")


def _generate_all_embeddings() -> int:
    """Generate embeddings for all beads that don't have them."""
    try:
        from .embeddings import generate_embedding
    except ImportError:
        # Embeddings module may not exist yet
        return 0

    db = get_db()
    count = 0

    # Find beads without embeddings
    beads = db.execute("""
        SELECT b.id, b.content FROM beads b
        LEFT JOIN embeddings e ON b.id = e.bead_id
        WHERE e.bead_id IS NULL
    """).fetchall()

    for bead in beads:
        try:
            vector = generate_embedding(bead["content"])
            if vector is not None:
                import struct
                vector_bytes = struct.pack(f'{len(vector)}f', *vector)
                db.execute("""
                    INSERT OR REPLACE INTO embeddings (bead_id, vector)
                    VALUES (?, ?)
                """, (bead["id"], vector_bytes))
                count += 1
        except:
            pass

    db.commit()
    return count


def _archive_old_hooks() -> int:
    """Archive Odin and Freyja hooks."""
    count = 0

    if not HOOKS_DIR.exists():
        return 0

    archive_dir = HOOKS_DIR / "archived"
    archive_dir.mkdir(exist_ok=True)

    for pattern in ["odin-*.sh", "freyja-*.sh"]:
        for hook in HOOKS_DIR.glob(pattern):
            try:
                dest = archive_dir / f"{hook.name}.{datetime.now().strftime('%Y%m%d')}"
                shutil.move(hook, dest)
                count += 1
            except:
                pass

    return count


def _install_enki_hooks() -> int:
    """Install Enki hooks."""
    count = 0

    HOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # Source hooks from the package
    package_hooks_dir = Path(__file__).parent.parent.parent / "scripts" / "hooks"

    if not package_hooks_dir.exists():
        # Try relative to working directory
        package_hooks_dir = Path("scripts/hooks")

    if package_hooks_dir.exists():
        for hook in package_hooks_dir.glob("enki-*.sh"):
            dest = HOOKS_DIR / hook.name
            if not dest.exists() or _should_update_hook(dest, hook):
                shutil.copy(hook, dest)
                dest.chmod(0o755)  # Make executable
                count += 1

    return count


def _should_update_hook(existing: Path, new: Path) -> bool:
    """Check if an existing hook should be updated."""
    # Simple size comparison for now
    return existing.stat().st_size != new.stat().st_size


def _map_bead_type(old_type: str) -> str:
    """Map old bead types to Enki types."""
    type_map = {
        "decision": "decision",
        "solution": "solution",
        "learning": "learning",
        "violation": "violation",
        "pattern": "pattern",
        # Legacy types
        "knowledge": "learning",
        "tip": "learning",
        "gotcha": "learning",
        "mistake": "violation",
        "error": "violation",
        "bug": "violation",
    }
    return type_map.get(old_type.lower(), "learning")


def validate_migration() -> dict:
    """Validate that migration was successful."""
    checks = {
        "enki_db_exists": DB_PATH.exists(),
        "beads_count": 0,
        "embeddings_count": 0,
        "beads_without_embeddings": 0,
        "odin_hooks_archived": True,
        "freyja_hooks_archived": True,
        "enki_hooks_installed": 0,
        "errors": [],
    }

    if not checks["enki_db_exists"]:
        checks["errors"].append("Enki database does not exist")
        return checks

    try:
        db = get_db()

        # Count beads
        row = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()
        checks["beads_count"] = row["count"]

        # Count embeddings
        row = db.execute("SELECT COUNT(*) as count FROM embeddings").fetchone()
        checks["embeddings_count"] = row["count"]

        # Beads without embeddings
        row = db.execute("""
            SELECT COUNT(*) as count FROM beads b
            LEFT JOIN embeddings e ON b.id = e.bead_id
            WHERE e.bead_id IS NULL
        """).fetchone()
        checks["beads_without_embeddings"] = row["count"]

    except Exception as e:
        checks["errors"].append(f"Database error: {e}")

    # Check hooks
    if HOOKS_DIR.exists():
        for pattern in ["odin-*.sh", "freyja-*.sh"]:
            if list(HOOKS_DIR.glob(pattern)):
                if "odin" in pattern:
                    checks["odin_hooks_archived"] = False
                else:
                    checks["freyja_hooks_archived"] = False

        checks["enki_hooks_installed"] = len(list(HOOKS_DIR.glob("enki-*.sh")))

    return checks


def rollback_migration() -> None:
    """Rollback migration by restoring archived hooks.

    Note: Does not delete migrated beads as they might have been modified.
    """
    if not HOOKS_DIR.exists():
        return

    archive_dir = HOOKS_DIR / "archived"
    if not archive_dir.exists():
        return

    # Restore archived hooks
    for hook in archive_dir.glob("*.sh.*"):
        original_name = hook.name.rsplit(".", 1)[0]
        dest = HOOKS_DIR / original_name
        if not dest.exists():
            shutil.copy(hook, dest)
            dest.chmod(0o755)

    # Remove Enki hooks
    for hook in HOOKS_DIR.glob("enki-*.sh"):
        hook.unlink()
