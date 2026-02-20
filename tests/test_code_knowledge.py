"""Tests for v4 code knowledge (Item 2.6).

Tests file hashing, staleness detection, code scanning,
heuristic extraction, and storage.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from enki.code_knowledge import (
    check_staleness,
    compute_file_hash,
    get_changed_files,
    mark_stale,
    scan_changed_files,
    store_code_knowledge,
    verify_note,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_enki(tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    with patch("enki.db.ENKI_ROOT", tmp_path), \
         patch("enki.db.DB_DIR", db_dir):
        from enki.db import init_all
        init_all()
        yield tmp_path


def _patch_db(tmp_enki):
    return patch.multiple(
        "enki.db",
        ENKI_ROOT=tmp_enki,
        DB_DIR=tmp_enki / "db",
    )


def _insert_code_note(conn, file_path, content="code note", file_hash=None,
                       project=None, last_verified=None):
    nid = str(uuid.uuid4())
    chash = hashlib.sha256(content.encode()).hexdigest()
    if file_hash is None:
        file_hash = "stored_hash_abc"
    if last_verified is None:
        last_verified = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO notes (id, content, category, content_hash, "
        "file_ref, file_hash, last_verified, project) "
        "VALUES (?, ?, 'code_knowledge', ?, ?, ?, ?, ?)",
        (nid, content, chash, file_path, file_hash, last_verified, project),
    )
    conn.commit()
    return nid


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    def test_computes_sha256(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        result = compute_file_hash(str(f))
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert result == expected

    def test_nonexistent_file_returns_none(self):
        assert compute_file_hash("/nonexistent/path/file.py") is None

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("content a")
        f2.write_text("content b")
        assert compute_file_hash(str(f1)) != compute_file_hash(str(f2))

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("same content")
        f2.write_text("same content")
        assert compute_file_hash(str(f1)) == compute_file_hash(str(f2))


# ---------------------------------------------------------------------------
# check_staleness
# ---------------------------------------------------------------------------


class TestCheckStaleness:
    def test_detects_stale_note(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            f = tmp_path / "src" / "main.py"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("updated content")

            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(
                    conn, str(f), file_hash="old_hash_different"
                )
            finally:
                conn.close()

            results = check_staleness()
            stale = [r for r in results if r["status"] == "stale"]
            assert len(stale) == 1
            assert stale[0]["note_id"] == nid

    def test_detects_missing_file(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(
                    conn, "/nonexistent/file.py", file_hash="some_hash"
                )
            finally:
                conn.close()

            results = check_staleness()
            missing = [r for r in results if r["status"] == "missing"]
            assert len(missing) == 1

    def test_detects_current_note(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            f = tmp_path / "current.py"
            f.write_text("unchanged content")
            current_hash = compute_file_hash(str(f))

            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(conn, str(f), file_hash=current_hash)
            finally:
                conn.close()

            results = check_staleness()
            current = [r for r in results if r["status"] == "current"]
            assert len(current) == 1

    def test_project_filter(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                conn.execute(
                    "INSERT INTO projects (name) VALUES (?)", ("proj-a",)
                )
                conn.execute(
                    "INSERT INTO projects (name) VALUES (?)", ("proj-b",)
                )
                _insert_code_note(
                    conn, "/fake/a.py", project="proj-a"
                )
                _insert_code_note(
                    conn, "/fake/b.py", project="proj-b"
                )
            finally:
                conn.close()

            results = check_staleness(project="proj-a")
            assert len(results) == 1


# ---------------------------------------------------------------------------
# mark_stale
# ---------------------------------------------------------------------------


class TestMarkStale:
    def test_clears_last_verified(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(conn, "/fake.py")
            finally:
                conn.close()

            count = mark_stale([nid])
            assert count == 1

            conn = get_wisdom_db()
            try:
                row = conn.execute(
                    "SELECT last_verified FROM notes WHERE id = ?", (nid,)
                ).fetchone()
                assert row["last_verified"] is None
            finally:
                conn.close()

    def test_does_not_delete_note(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(conn, "/fake.py")
            finally:
                conn.close()

            mark_stale([nid])

            conn = get_wisdom_db()
            try:
                row = conn.execute(
                    "SELECT content FROM notes WHERE id = ?", (nid,)
                ).fetchone()
                assert row is not None  # Note still exists
            finally:
                conn.close()

    def test_empty_list_returns_zero(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert mark_stale([]) == 0

    def test_only_marks_code_knowledge(self, tmp_enki):
        """mark_stale only affects code_knowledge notes."""
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                # Insert a learning note (not code_knowledge)
                nid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO notes (id, content, category, content_hash) "
                    "VALUES (?, ?, 'learning', ?)",
                    (nid, "learning note", "lh1"),
                )
                conn.commit()
            finally:
                conn.close()

            count = mark_stale([nid])
            assert count == 0  # Not code_knowledge, so not marked


# ---------------------------------------------------------------------------
# verify_note
# ---------------------------------------------------------------------------


class TestVerifyNote:
    def test_updates_hash_and_timestamp(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            f = tmp_path / "verify.py"
            f.write_text("verified content")

            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(
                    conn, str(f), file_hash="old_hash", last_verified=None
                )
            finally:
                conn.close()

            result = verify_note(nid)
            assert result is True

            conn = get_wisdom_db()
            try:
                row = conn.execute(
                    "SELECT file_hash, last_verified FROM notes WHERE id = ?",
                    (nid,),
                ).fetchone()
                assert row["file_hash"] == compute_file_hash(str(f))
                assert row["last_verified"] is not None
            finally:
                conn.close()

    def test_missing_file_returns_false(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                nid = _insert_code_note(conn, "/nonexistent.py")
            finally:
                conn.close()

            assert verify_note(nid) is False

    def test_nonexistent_note_returns_false(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert verify_note("fake-id") is False


# ---------------------------------------------------------------------------
# get_changed_files
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    def test_returns_empty_for_non_git_dir(self, tmp_path):
        result = get_changed_files(str(tmp_path))
        assert result == []

    def test_returns_empty_for_nonexistent_dir(self):
        result = get_changed_files("/nonexistent/dir")
        assert result == []


# ---------------------------------------------------------------------------
# store_code_knowledge
# ---------------------------------------------------------------------------


class TestStoreCodeKnowledge:
    def test_stores_items(self, tmp_enki):
        with _patch_db(tmp_enki):
            items = [
                {
                    "content": "Module uses singleton pattern for DB",
                    "keywords": "singleton,database",
                    "summary": "DB singleton pattern",
                    "file_ref": "/src/db.py",
                    "file_hash": "hash123",
                    "project": None,
                },
            ]
            ids = store_code_knowledge(items)
            assert len(ids) == 1

            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                row = conn.execute(
                    "SELECT category, file_ref, file_hash, last_verified "
                    "FROM notes WHERE id = ?",
                    (ids[0],),
                ).fetchone()
                assert row["category"] == "code_knowledge"
                assert row["file_ref"] == "/src/db.py"
                assert row["file_hash"] == "hash123"
                assert row["last_verified"] is not None
            finally:
                conn.close()

    def test_skips_duplicates(self, tmp_enki):
        with _patch_db(tmp_enki):
            items = [
                {"content": "same content", "file_ref": "/a.py", "file_hash": "h1"},
                {"content": "same content", "file_ref": "/b.py", "file_hash": "h2"},
            ]
            ids = store_code_knowledge(items)
            assert len(ids) == 1  # Second is a duplicate by content_hash

    def test_skips_empty_content(self, tmp_enki):
        with _patch_db(tmp_enki):
            items = [{"content": "", "file_ref": "/a.py"}]
            ids = store_code_knowledge(items)
            assert len(ids) == 0

    def test_empty_list_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert store_code_knowledge([]) == []


# ---------------------------------------------------------------------------
# scan_changed_files
# ---------------------------------------------------------------------------


class TestScanChangedFiles:
    def test_scans_python_files(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            # Create a Python file with a module docstring
            src = tmp_path / "project" / "src" / "main.py"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(
                '"""Main application module.\n\n'
                'This handles the core API routing and request processing.\n'
                '"""\n\nimport flask\n'
            )

            project_path = str(tmp_path / "project")
            with patch(
                "enki.code_knowledge.get_changed_files",
                return_value=["src/main.py"],
            ):
                items = scan_changed_files(project_path, "test-proj")
                # Should extract at least the module docstring
                assert len(items) >= 1
                assert all(i["category"] == "code_knowledge" for i in items)
                assert all(i["project"] == "test-proj" for i in items)
                assert all(i["file_hash"] is not None for i in items)

    def test_skips_non_code_files(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            img = tmp_path / "project" / "logo.png"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(b"\x89PNG\r\n\x1a\n")

            project_path = str(tmp_path / "project")
            with patch(
                "enki.code_knowledge.get_changed_files",
                return_value=["logo.png"],
            ):
                items = scan_changed_files(project_path, "test-proj")
                assert items == []

    def test_skips_tiny_files(self, tmp_enki, tmp_path):
        with _patch_db(tmp_enki):
            f = tmp_path / "project" / "tiny.py"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x=1")

            project_path = str(tmp_path / "project")
            with patch(
                "enki.code_knowledge.get_changed_files",
                return_value=["tiny.py"],
            ):
                items = scan_changed_files(project_path, "test-proj")
                assert items == []

    def test_no_changed_files_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            with patch(
                "enki.code_knowledge.get_changed_files",
                return_value=[],
            ):
                items = scan_changed_files("/fake", "proj")
                assert items == []
