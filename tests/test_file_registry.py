"""Tests for v4 EM file registry (Item 3.2).

Tests file registration, lookup, reuse hints, and registry queries.
"""

from unittest.mock import patch

import pytest

from enki.orch.file_registry import (
    build_reuse_hint,
    get_all_files,
    lookup_files,
    register_files,
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


# ---------------------------------------------------------------------------
# register_files
# ---------------------------------------------------------------------------


class TestRegisterFiles:
    def test_registers_created_files(self, tmp_enki):
        with _patch_db(tmp_enki):
            count = register_files(
                "proj", "task-1",
                files_created=["src/auth.py", "src/middleware.py"],
            )
            assert count == 2

    def test_registers_modified_files(self, tmp_enki):
        with _patch_db(tmp_enki):
            count = register_files(
                "proj", "task-1",
                files_modified=["src/main.py"],
            )
            assert count == 1

    def test_registers_both_created_and_modified(self, tmp_enki):
        with _patch_db(tmp_enki):
            count = register_files(
                "proj", "task-1",
                files_created=["src/new.py"],
                files_modified=["src/old.py"],
                description="Added auth feature",
            )
            assert count == 2

    def test_empty_lists_returns_zero(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert register_files("proj", "task-1") == 0
            assert register_files("proj", "task-1", [], []) == 0

    def test_replaces_on_duplicate_path(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files(
                "proj", "task-1",
                files_created=["src/auth.py"],
                description="Initial",
            )
            register_files(
                "proj", "task-2",
                files_modified=["src/auth.py"],
                description="Updated",
            )
            all_files = get_all_files("proj")
            auth_entries = [f for f in all_files if f["file_path"] == "src/auth.py"]
            assert len(auth_entries) == 1
            assert auth_entries[0]["task_id"] == "task-2"

    def test_with_description(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files(
                "proj", "task-1",
                files_created=["src/auth.py"],
                description="JWT authentication module",
            )
            files = get_all_files("proj")
            assert files[0]["description"] == "JWT authentication module"


# ---------------------------------------------------------------------------
# lookup_files
# ---------------------------------------------------------------------------


class TestLookupFiles:
    def test_matches_by_file_path(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files("proj", "task-1", files_created=["src/auth.py"])
            matches = lookup_files("proj", "auth")
            assert len(matches) == 1
            assert matches[0]["file_path"] == "src/auth.py"

    def test_matches_by_description(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files(
                "proj", "task-1",
                files_created=["src/middleware.py"],
                description="JWT authentication middleware",
            )
            matches = lookup_files("proj", "authentication")
            assert len(matches) == 1

    def test_no_matches_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files("proj", "task-1", files_created=["src/auth.py"])
            assert lookup_files("proj", "database") == []

    def test_empty_query_returns_empty(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert lookup_files("proj", "") == []
            assert lookup_files("proj", "   ") == []

    def test_multiple_keyword_match(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files(
                "proj", "task-1",
                files_created=["src/db.py"],
                description="Database connection pool",
            )
            register_files(
                "proj", "task-2",
                files_created=["src/cache.py"],
                description="Redis cache layer",
            )
            matches = lookup_files("proj", "database connection")
            assert len(matches) == 1
            assert matches[0]["file_path"] == "src/db.py"

    def test_case_insensitive(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files(
                "proj", "task-1",
                files_created=["src/Auth.py"],
                description="Authentication Module",
            )
            matches = lookup_files("proj", "auth")
            assert len(matches) == 1


# ---------------------------------------------------------------------------
# build_reuse_hint
# ---------------------------------------------------------------------------


class TestBuildReuseHint:
    def test_builds_hint_from_matches(self):
        matches = [
            {"file_path": "src/auth.py", "task_id": "task-1",
             "action": "created", "description": "JWT auth"},
        ]
        hint = build_reuse_hint(matches)
        assert hint is not None
        assert "src/auth.py" in hint
        assert "Evaluate for reuse" in hint

    def test_returns_none_for_no_matches(self):
        assert build_reuse_hint([]) is None

    def test_caps_at_5_hints(self):
        matches = [
            {"file_path": f"src/file{i}.py", "task_id": f"task-{i}",
             "action": "created", "description": None}
            for i in range(10)
        ]
        hint = build_reuse_hint(matches)
        # Should only have 5 file entries
        assert hint.count("src/file") == 5

    def test_handles_missing_description(self):
        matches = [
            {"file_path": "src/x.py", "task_id": "t1",
             "action": "modified", "description": None},
        ]
        hint = build_reuse_hint(matches)
        assert "src/x.py" in hint


# ---------------------------------------------------------------------------
# get_all_files
# ---------------------------------------------------------------------------


class TestGetAllFiles:
    def test_returns_all_entries(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files("proj", "t1", files_created=["a.py"])
            register_files("proj", "t2", files_created=["b.py"])
            files = get_all_files("proj")
            assert len(files) == 2

    def test_returns_empty_for_no_entries(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert get_all_files("proj") == []

    def test_project_isolation(self, tmp_enki):
        with _patch_db(tmp_enki):
            register_files("proj-a", "t1", files_created=["a.py"])
            register_files("proj-b", "t2", files_created=["b.py"])
            assert len(get_all_files("proj-a")) == 1
            assert len(get_all_files("proj-b")) == 1
