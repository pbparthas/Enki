"""Tests for beads module."""

import pytest

from enki.db import init_db, get_db
from enki.beads import (
    create_bead,
    get_bead,
    update_bead,
    delete_bead,
    star_bead,
    unstar_bead,
    supersede_bead,
    log_access,
    get_recent_beads,
)


class TestCreateBead:
    """Tests for create_bead."""

    def test_creates_bead_with_required_fields(self, temp_db):
        """Test creating a bead with minimal fields."""
        init_db(temp_db)

        bead = create_bead(
            content="Test decision content",
            bead_type="decision",
        )

        assert bead is not None
        assert bead.id is not None
        assert bead.content == "Test decision content"
        assert bead.type == "decision"
        assert bead.weight == 1.0
        assert bead.starred is False

    def test_creates_bead_with_all_fields(self, temp_db):
        """Test creating a bead with all fields."""
        init_db(temp_db)

        bead = create_bead(
            content="Full bead content",
            bead_type="solution",
            summary="Short summary",
            project="test-project",
            context="During testing",
            tags=["test", "example"],
            starred=True,
        )

        assert bead.content == "Full bead content"
        assert bead.type == "solution"
        assert bead.summary == "Short summary"
        assert bead.project == "test-project"
        assert bead.context == "During testing"
        assert bead.tags == ["test", "example"]
        assert bead.starred is True

    def test_creates_embedding(self, temp_db):
        """Test that create_bead also creates an embedding."""
        init_db(temp_db)

        bead = create_bead(
            content="Content for embedding",
            bead_type="learning",
        )

        db = get_db(temp_db)
        row = db.execute(
            "SELECT * FROM embeddings WHERE bead_id = ?",
            (bead.id,),
        ).fetchone()

        assert row is not None
        assert row["vector"] is not None
        assert len(row["vector"]) == 384 * 4  # 384 floats * 4 bytes


class TestGetBead:
    """Tests for get_bead."""

    def test_returns_existing_bead(self, temp_db):
        """Test getting an existing bead."""
        init_db(temp_db)

        created = create_bead(content="Test", bead_type="learning")
        fetched = get_bead(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.content == created.content

    def test_returns_none_for_missing(self, temp_db):
        """Test getting a non-existent bead."""
        init_db(temp_db)

        result = get_bead("non-existent-id")
        assert result is None


class TestUpdateBead:
    """Tests for update_bead."""

    def test_updates_content(self, temp_db):
        """Test updating bead content."""
        init_db(temp_db)

        bead = create_bead(content="Original", bead_type="learning")
        updated = update_bead(bead.id, content="Updated")

        assert updated is not None
        assert updated.content == "Updated"

    def test_updates_starred(self, temp_db):
        """Test updating starred status."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning")
        assert bead.starred is False

        updated = update_bead(bead.id, starred=True)
        assert updated.starred is True

    def test_updates_tags(self, temp_db):
        """Test updating tags."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning", tags=["old"])
        updated = update_bead(bead.id, tags=["new", "tags"])

        assert updated.tags == ["new", "tags"]


class TestDeleteBead:
    """Tests for delete_bead."""

    def test_deletes_existing(self, temp_db):
        """Test deleting an existing bead."""
        init_db(temp_db)

        bead = create_bead(content="To delete", bead_type="learning")
        result = delete_bead(bead.id)

        assert result is True
        assert get_bead(bead.id) is None

    def test_returns_false_for_missing(self, temp_db):
        """Test deleting a non-existent bead."""
        init_db(temp_db)

        result = delete_bead("non-existent")
        assert result is False


class TestStarBead:
    """Tests for star_bead and unstar_bead."""

    def test_star_bead(self, temp_db):
        """Test starring a bead."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning")
        starred = star_bead(bead.id)

        assert starred.starred is True

    def test_unstar_bead(self, temp_db):
        """Test unstarring a bead."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning", starred=True)
        unstarred = unstar_bead(bead.id)

        assert unstarred.starred is False


class TestSupersedeBead:
    """Tests for supersede_bead."""

    def test_marks_superseded(self, temp_db):
        """Test marking a bead as superseded."""
        init_db(temp_db)

        old = create_bead(content="Old knowledge", bead_type="learning")
        new = create_bead(content="New knowledge", bead_type="learning")

        result = supersede_bead(old.id, new.id)

        assert result is not None
        assert result.superseded_by == new.id
        assert result.weight == 0  # Superseded beads have weight 0


class TestLogAccess:
    """Tests for log_access."""

    def test_logs_access(self, temp_db):
        """Test logging access to a bead."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning")
        log_access(bead.id, session_id="test-session")

        db = get_db(temp_db)
        row = db.execute(
            "SELECT * FROM access_log WHERE bead_id = ?",
            (bead.id,),
        ).fetchone()

        assert row is not None
        assert row["session_id"] == "test-session"

    def test_updates_last_accessed(self, temp_db):
        """Test that log_access updates last_accessed."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning")
        assert bead.last_accessed is None

        log_access(bead.id)
        updated = get_bead(bead.id)

        assert updated.last_accessed is not None


class TestGetRecentBeads:
    """Tests for get_recent_beads."""

    def test_returns_recent(self, temp_db):
        """Test getting recent beads."""
        init_db(temp_db)

        create_bead(content="Bead 1", bead_type="learning")
        create_bead(content="Bead 2", bead_type="decision")
        create_bead(content="Bead 3", bead_type="solution")

        recent = get_recent_beads(limit=2)

        assert len(recent) == 2

    def test_excludes_superseded(self, temp_db):
        """Test that superseded beads are excluded."""
        init_db(temp_db)

        old = create_bead(content="Old", bead_type="learning")
        new = create_bead(content="New", bead_type="learning")
        supersede_bead(old.id, new.id)

        recent = get_recent_beads(limit=10)

        ids = [b.id for b in recent]
        assert new.id in ids
        assert old.id not in ids
