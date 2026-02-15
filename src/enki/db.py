"""db.py — Shared database connection management.

Every connection uses WAL mode and busy_timeout.
Every connection is scoped to a specific database.
No module bypasses this.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ENKI_ROOT = Path(os.environ.get("ENKI_ROOT", str(Path.home() / ".enki")))


def _configure(conn: sqlite3.Connection) -> None:
    """Apply mandatory SQLite configuration."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row


@contextmanager
def connect(db_path: str | Path):
    """Context manager for database connections.

    Usage:
        with connect(ENKI_ROOT / "wisdom.db") as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(str(db_path))
    _configure(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wisdom_db():
    """Connection to wisdom.db (permanent beads)."""
    return connect(ENKI_ROOT / "wisdom.db")


def abzu_db():
    """Connection to abzu.db (session summaries + staging)."""
    return connect(ENKI_ROOT / "abzu.db")


def uru_db():
    """Connection to uru.db (enforcement logs)."""
    return connect(ENKI_ROOT / "uru.db")


_em_initialized: set[str] = set()


def em_db(project: str):
    """Connection to per-project em.db. Auto-initializes tables."""
    path = ENKI_ROOT / "projects" / project / "em.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    if project not in _em_initialized:
        from enki.orch.schemas import create_tables as create_em
        with connect(path) as conn:
            create_em(conn)
        _em_initialized.add(project)

    return connect(path)


def init_all():
    """Create all databases and tables. Idempotent."""
    ENKI_ROOT.mkdir(parents=True, exist_ok=True)

    from enki.gates.schemas import create_tables as create_uru
    from enki.memory.schemas import create_tables as create_memory

    with wisdom_db() as conn:
        create_memory(conn, "wisdom")
    with abzu_db() as conn:
        create_memory(conn, "abzu")
    with uru_db() as conn:
        create_uru(conn)
