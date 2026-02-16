"""Pytest fixtures for Enki v3 tests."""

import pytest
from pathlib import Path
from unittest.mock import patch

import enki.db as db_mod


@pytest.fixture
def enki_root(tmp_path):
    """Provide a temporary ~/.enki directory with all DBs initialized."""
    root = tmp_path / ".enki"
    root.mkdir()
    db_dir = root / "db"
    db_dir.mkdir()
    old_initialized = db_mod._em_initialized.copy()
    db_mod._em_initialized.clear()
    with patch.object(db_mod, "ENKI_ROOT", root), \
         patch.object(db_mod, "DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield root
    db_mod._em_initialized = old_initialized
