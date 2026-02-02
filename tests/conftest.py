"""Pytest fixtures for Enki tests."""

import pytest
import tempfile
from pathlib import Path

from enki.db import init_db, get_db, close_db, set_db_path


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_wisdom.db"
    init_db(db_path)  # This also sets the current db path
    yield db_path
    close_db()
    set_db_path(None)  # Reset to default after test


@pytest.fixture
def db(temp_db):
    """Get database connection for testing."""
    return get_db(temp_db)
