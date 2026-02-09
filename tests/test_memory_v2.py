"""Tests for Memory v2 (Spec 1) — keywords, beads, context, hooks."""

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from enki.db import init_db, get_db, set_db_path, reset_connection
from enki.keywords import extract_keywords, STOP_WORDS
from enki.beads import (
    Bead, BeadType, BeadKind, assign_kind, create_bead, get_bead,
)
from enki.context import generate_context_md, MAX_BYTES
from enki.hooks import (
    extract_pre_compact_snapshot,
    find_transcript,
    read_jsonl_since_last_snapshot,
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """Set up a fresh test database."""
    db_path = tmp_path / "test.db"
    reset_connection()
    set_db_path(db_path)
    init_db(db_path)
    yield
    reset_connection()
    set_db_path(None)


# ========== keywords.py ==========


class TestExtractKeywords:
    def test_basic_extraction(self):
        result = extract_keywords("implement user authentication with OAuth")
        assert "user" in result
        assert "authentication" in result
        assert "oauth" in result
        # "implement" is a stop word
        assert "implement" not in result

    def test_max_keywords(self):
        text = "alpha bravo charlie delta echo foxtrot golf hotel india"
        result = extract_keywords(text, max_keywords=3)
        assert len(result) == 3

    def test_deduplication(self):
        text = "redis redis redis cache cache"
        result = extract_keywords(text)
        assert result.count("redis") == 1
        assert result.count("cache") == 1

    def test_short_words_filtered(self):
        text = "go to do it me we"
        result = extract_keywords(text)
        assert len(result) == 0  # All words are <= 2 chars or stop words

    def test_stop_words_removed(self):
        for word in ["the", "implement", "create", "function"]:
            assert word in STOP_WORDS

    def test_empty_input(self):
        assert extract_keywords("") == []

    def test_no_llm_imports(self):
        """M-2 / C3: keywords.py must have zero LLM imports."""
        import enki.keywords as kw
        source = Path(kw.__file__).read_text()
        # Check import lines only — docstrings can mention LLM
        import_lines = [l for l in source.split("\n") if l.strip().startswith(("import ", "from "))]
        for line in import_lines:
            for forbidden in ["embed", "anthropic", "gemini", "openai", "torch"]:
                assert forbidden not in line.lower(), \
                    f"Found forbidden import '{forbidden}' in keywords.py: {line}"


# ========== beads.py — assign_kind ==========


class TestAssignKind:
    def test_style_always_preference(self):
        assert assign_kind("style", "prefers dark mode") == "preference"

    def test_decision_always_decision(self):
        assert assign_kind("decision", "chose Redis") == "decision"

    def test_violation_always_fact(self):
        assert assign_kind("violation", "gate blocked") == "fact"

    def test_rejection_always_fact(self):
        assert assign_kind("rejection", "rejected approach X") == "fact"

    def test_learning_always_fact(self):
        assert assign_kind("learning", "learned something") == "fact"

    def test_pattern_always_pattern(self):
        assert assign_kind("pattern", "recurring bug pattern") == "pattern"

    def test_approach_default_fact(self):
        assert assign_kind("approach", "used composition") == "fact"

    def test_approach_with_recurring_becomes_pattern(self):
        assert assign_kind("approach", "always uses early returns") == "pattern"
        assert assign_kind("approach", "consistently prefers composition") == "pattern"

    def test_solution_default_fact(self):
        assert assign_kind("solution", "fixed by updating config") == "fact"

    def test_unknown_type_defaults_fact(self):
        assert assign_kind("unknown_type", "something") == "fact"

    def test_deterministic_no_external_deps(self):
        """G-6: assign_kind is deterministic with no external dependencies."""
        # Same input always produces same output
        for _ in range(10):
            assert assign_kind("style", "test") == "preference"
            assert assign_kind("approach", "always does X") == "pattern"


# ========== beads.py — Bead class ==========


class TestBeadClass:
    def test_bead_has_kind_field(self):
        bead = Bead(id="test", content="test", type="decision", kind="decision")
        assert bead.kind == "decision"

    def test_bead_has_archived_at_field(self):
        bead = Bead(id="test", content="test", type="learning")
        assert bead.archived_at is None

    def test_bead_has_content_hash_field(self):
        bead = Bead(id="test", content="test", type="learning")
        assert bead.content_hash is None

    def test_bead_kind_default_fact(self):
        bead = Bead(id="test", content="test", type="learning")
        assert bead.kind == "fact"


class TestCreateBeadV2:
    def test_creates_with_kind(self):
        bead = create_bead(
            content="prefers early returns",
            bead_type="style",
            kind="preference",
        )
        assert bead.kind == "preference"

    def test_auto_assigns_kind(self):
        bead = create_bead(content="chose Redis", bead_type="decision")
        assert bead.kind == "decision"

    def test_auto_assigns_kind_from_type(self):
        bead = create_bead(content="some pattern", bead_type="pattern")
        assert bead.kind == "pattern"

    def test_content_hash_computed(self):
        bead = create_bead(content="unique content 123", bead_type="learning")
        expected = hashlib.sha256("unique content 123".encode()).hexdigest()
        assert bead.content_hash == expected

    def test_content_hash_dedup(self):
        """G-7: content_hash uses SHA-256 for dedup."""
        bead1 = create_bead(content="duplicate content", bead_type="learning")
        bead2 = create_bead(content="duplicate content", bead_type="learning")
        assert bead1.id == bead2.id  # Same bead returned

    def test_new_bead_types(self):
        """Style, approach, rejection types work."""
        for bead_type in ("style", "approach", "rejection"):
            bead = create_bead(
                content=f"test {bead_type} content unique_{bead_type}",
                bead_type=bead_type,
            )
            assert bead.type == bead_type


# ========== context.py — generate_context_md ==========


class TestGenerateContextMd:
    def test_never_returns_empty(self, tmp_path):
        """M-3 / G-8: generate_context_md never returns empty string."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".enki").mkdir()
        result = generate_context_md(project)
        assert result != ""
        assert len(result) > 0

    def test_has_all_sections(self, tmp_path):
        """All section headers are present even with no data."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".enki").mkdir()
        result = generate_context_md(project)
        assert "## Current State" in result
        assert "## Recent Decisions" in result
        assert "## Working Style" in result
        assert "## Active Preferences" in result
        assert "## Open Questions" in result
        assert "## Last Session" in result

    def test_within_3kb(self, tmp_path):
        """M-6: CONTEXT.md capped at 3KB."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".enki").mkdir()
        result = generate_context_md(project)
        assert len(result.encode("utf-8")) <= MAX_BYTES

    def test_max_bytes_is_3072(self):
        """M-6: MAX_BYTES = 3072 hardcoded."""
        assert MAX_BYTES == 3072

    def test_shows_phase_and_goal(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        enki_dir = project / ".enki"
        enki_dir.mkdir()
        (enki_dir / "PHASE").write_text("implement")
        (enki_dir / "GOAL").write_text("build authentication")
        result = generate_context_md(project)
        assert "implement" in result
        assert "build authentication" in result

    def test_preferences_section_always_present(self, tmp_path):
        """C11: Active Preferences section always present."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".enki").mkdir()
        result = generate_context_md(project)
        assert "## Active Preferences (apply to all work)" in result
        assert "(none yet)" in result


# ========== hooks.py — Snapshot extraction ==========


class TestSnapshotExtraction:
    def test_returns_error_on_missing_transcript(self, tmp_path):
        """Fail-closed: error when transcript not found."""
        result = extract_pre_compact_snapshot(tmp_path, "nonexistent-session")
        assert "error" in result
        assert result["error"] == "transcript_not_found"

    def test_snapshot_bounded_to_10(self, tmp_path):
        """M-5: Snapshots bounded to 10 per session."""
        # Create .enki dir
        enki_dir = tmp_path / ".enki"
        enki_dir.mkdir()

        # Write 15 fake snapshots
        snapshots = [{"session_id": f"s{i}", "entries": []} for i in range(15)]
        (enki_dir / "SNAPSHOT.json").write_text(json.dumps(snapshots))

        # Create a fake transcript
        session_id = "test-session"
        claude_dir = Path.home() / ".claude" / "projects" / "test-hash"
        claude_dir.mkdir(parents=True, exist_ok=True)
        transcript = claude_dir / f"{session_id}.jsonl"
        transcript.write_text("")

        try:
            result = extract_pre_compact_snapshot(tmp_path, session_id)
            # Read back snapshots
            stored = json.loads((enki_dir / "SNAPSHOT.json").read_text())
            assert len(stored) <= 10
        finally:
            transcript.unlink(missing_ok=True)
            claude_dir.rmdir()

    def test_extracts_user_messages(self, tmp_path):
        """Snapshot captures user messages over 20 chars."""
        enki_dir = tmp_path / ".enki"
        enki_dir.mkdir()

        session_id = "test-extract"
        claude_dir = Path.home() / ".claude" / "projects" / "test-hash2"
        claude_dir.mkdir(parents=True, exist_ok=True)
        transcript = claude_dir / f"{session_id}.jsonl"

        entries = [
            {"type": "user", "content": "This is a sufficiently long user message for extraction"},
            {"type": "user", "content": "short"},  # Should be skipped (< 20 chars)
        ]
        transcript.write_text("\n".join(json.dumps(e) for e in entries))

        try:
            result = extract_pre_compact_snapshot(tmp_path, session_id)
            user_entries = [e for e in result["entries"] if e["type"] == "user"]
            assert len(user_entries) == 1
            assert "sufficiently long" in user_entries[0]["content"]
        finally:
            transcript.unlink(missing_ok=True)
            claude_dir.rmdir()


# ========== Constraint Verification ==========


class TestMemoryV2Constraints:
    def test_m4_content_hash_sha256(self):
        """M-4: content_hash uses SHA-256."""
        import enki.beads
        source = Path(enki.beads.__file__).read_text()
        assert "hashlib.sha256" in source

    def test_m7_kind_assignment_hardcoded(self):
        """M-7: Kind assignment rules are hardcoded Python, not config."""
        import enki.beads
        source = Path(enki.beads.__file__).read_text()
        # No YAML, JSON config loading, or env var for kind mapping
        assert "yaml" not in source.lower()
        assert "os.environ" not in source.split("assign_kind")[1].split("class Bead")[0]
