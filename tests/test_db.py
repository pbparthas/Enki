"""Tests for database module."""

import pytest
from pathlib import Path

from enki.db import init_db, get_db, close_db


def test_init_db_creates_file(tmp_path):
    """Test that init_db creates the database file."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    assert db_path.exists()
    close_db()


def test_init_db_creates_tables(temp_db):
    """Test that init_db creates all required tables."""
    db = get_db(temp_db)

    # Check tables exist
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {row["name"] for row in tables}

    assert "beads" in table_names
    assert "embeddings" in table_names
    assert "access_log" in table_names
    assert "projects" in table_names
    assert "sessions" in table_names
    assert "interceptions" in table_names


def test_init_db_creates_fts(temp_db):
    """Test that init_db creates FTS5 virtual table."""
    db = get_db(temp_db)

    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {row["name"] for row in tables}

    assert "beads_fts" in table_names


def test_init_db_creates_indexes(temp_db):
    """Test that init_db creates indexes."""
    db = get_db(temp_db)

    indexes = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = {row["name"] for row in indexes}

    assert "idx_beads_project" in index_names
    assert "idx_beads_type" in index_names


def test_get_db_returns_same_connection(temp_db):
    """Test that get_db returns the same connection for same path."""
    conn1 = get_db(temp_db)
    conn2 = get_db(temp_db)

    assert conn1 is conn2


def test_db_foreign_keys_enabled(temp_db):
    """Test that foreign keys are enabled."""
    db = get_db(temp_db)

    result = db.execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1


def test_db_wal_mode(temp_db):
    """Test that WAL mode is enabled."""
    db = get_db(temp_db)

    result = db.execute("PRAGMA journal_mode").fetchone()
    assert result[0].lower() == "wal"
