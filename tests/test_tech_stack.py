"""Tests for v4 tech stack confirmation (Item 3.5).

Tests codebase scanning, tech stack storage/retrieval,
and deviation detection.
"""

import json
from unittest.mock import patch

import pytest

from enki.orch.tech_stack import (
    check_deviation,
    get_tech_stack,
    scan_tech_stack,
    store_tech_stack,
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
# scan_tech_stack
# ---------------------------------------------------------------------------


class TestScanTechStack:
    def test_detects_python(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        result = scan_tech_stack(str(tmp_path))
        assert "python" in result["languages"]

    def test_detects_javascript(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"dependencies": {"express": "^4.0"}}))
        result = scan_tech_stack(str(tmp_path))
        assert "javascript" in result["languages"]
        assert "express" in result["frameworks"]

    def test_detects_typescript(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text("{}")
        result = scan_tech_stack(str(tmp_path))
        assert "typescript" in result["languages"]

    def test_detects_react(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"dependencies": {"react": "^18.0"}}))
        result = scan_tech_stack(str(tmp_path))
        assert "react" in result["frameworks"]

    def test_detects_pyproject_frameworks(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["flask", "pytest"]'
        )
        result = scan_tech_stack(str(tmp_path))
        assert "flask" in result["frameworks"]
        assert "pytest" in result["frameworks"]

    def test_detects_build_tools(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        (tmp_path / "Makefile").write_text("all:\n\techo hi")
        result = scan_tech_stack(str(tmp_path))
        assert "docker" in result["build_tools"]
        assert "make" in result["build_tools"]

    def test_nonexistent_path(self):
        result = scan_tech_stack("/nonexistent/path")
        assert "error" in result

    def test_empty_directory(self, tmp_path):
        result = scan_tech_stack(str(tmp_path))
        assert result["languages"] == []
        assert result["primary_language"] is None

    def test_primary_language_set(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1")
        result = scan_tech_stack(str(tmp_path))
        assert result["primary_language"] == "python"

    def test_detects_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("django==4.2\ncelery==5.3")
        result = scan_tech_stack(str(tmp_path))
        assert "django" in result["frameworks"]


# ---------------------------------------------------------------------------
# store_tech_stack / get_tech_stack
# ---------------------------------------------------------------------------


class TestTechStackStorage:
    def test_store_and_retrieve(self, tmp_enki):
        with _patch_db(tmp_enki):
            stack = {"languages": ["python"], "frameworks": ["flask"]}
            assert store_tech_stack("my-proj", stack) is True

            retrieved = get_tech_stack("my-proj")
            assert retrieved == stack

    def test_store_creates_project(self, tmp_enki):
        with _patch_db(tmp_enki):
            stack = {"languages": ["go"]}
            store_tech_stack("new-proj", stack)
            assert get_tech_stack("new-proj") == stack

    def test_store_updates_existing(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                conn.execute("INSERT INTO projects (name) VALUES (?)", ("proj",))
                conn.commit()
            finally:
                conn.close()

            stack = {"languages": ["rust"]}
            store_tech_stack("proj", stack)
            assert get_tech_stack("proj") == stack

    def test_get_returns_none_for_no_stack(self, tmp_enki):
        with _patch_db(tmp_enki):
            assert get_tech_stack("nonexistent") is None

    def test_get_returns_none_for_null_stack(self, tmp_enki):
        with _patch_db(tmp_enki):
            from enki.db import get_wisdom_db
            conn = get_wisdom_db()
            try:
                conn.execute("INSERT INTO projects (name) VALUES (?)", ("proj",))
                conn.commit()
            finally:
                conn.close()
            assert get_tech_stack("proj") is None


# ---------------------------------------------------------------------------
# check_deviation
# ---------------------------------------------------------------------------


class TestCheckDeviation:
    def test_no_deviation(self):
        stack = {"languages": ["python"], "frameworks": ["flask"]}
        deviations = check_deviation(["python", "flask"], stack)
        assert deviations == []

    def test_detects_new_language(self):
        stack = {"languages": ["python"], "frameworks": ["flask"]}
        deviations = check_deviation(["java"], stack)
        assert len(deviations) == 1
        assert deviations[0]["proposed"] == "java"
        assert "deviation" in deviations[0]["status"]

    def test_detects_new_framework(self):
        stack = {"languages": ["python"], "frameworks": ["flask"]}
        deviations = check_deviation(["django"], stack)
        assert len(deviations) == 1

    def test_case_insensitive(self):
        stack = {"languages": ["Python"], "frameworks": ["Flask"]}
        deviations = check_deviation(["python", "flask"], stack)
        assert deviations == []

    def test_empty_confirmed_stack(self):
        assert check_deviation(["python"], {}) == []
        assert check_deviation(["python"], None) == []

    def test_multiple_deviations(self):
        stack = {"languages": ["python"], "frameworks": []}
        deviations = check_deviation(["java", "spring", "gradle"], stack)
        assert len(deviations) == 3
