"""Tests for retention module."""

import pytest
from datetime import datetime, timedelta, timezone

from enki.db import init_db, get_db
from enki.beads import create_bead, star_bead, supersede_bead, log_access
from enki.retention import calculate_weight, update_all_weights


class TestCalculateWeight:
    """Tests for calculate_weight."""

    def test_starred_bead_has_weight_1(self, temp_db):
        """Test that starred beads always have weight 1.0."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning", starred=True)

        weight = calculate_weight(bead)
        assert weight == 1.0

    def test_superseded_bead_has_weight_0(self, temp_db):
        """Test that superseded beads have weight 0.0."""
        init_db(temp_db)

        old = create_bead(content="Old", bead_type="learning")
        new = create_bead(content="New", bead_type="learning")
        supersede_bead(old.id, new.id)

        old_updated = supersede_bead(old.id, new.id)
        weight = calculate_weight(old_updated)
        assert weight == 0.0

    def test_new_bead_has_high_weight(self, temp_db):
        """Test that new beads have high weight."""
        init_db(temp_db)

        bead = create_bead(content="Fresh bead", bead_type="learning")
        weight = calculate_weight(bead)

        assert weight >= 0.9  # Should be close to 1.0 for new beads

    def test_weight_from_dict(self, temp_db):
        """Test calculating weight from dict-like row."""
        init_db(temp_db)

        bead_dict = {
            "id": "test-id",
            "starred": 0,
            "superseded_by": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_accessed": None,
        }

        weight = calculate_weight(bead_dict)
        assert 0 < weight <= 1.0

    def test_weight_increases_with_recent_access(self, temp_db):
        """Test that recent access boosts weight."""
        init_db(temp_db)

        bead = create_bead(content="Test", bead_type="learning")

        weight_before = calculate_weight(bead)

        # Log access
        log_access(bead.id)

        # Re-fetch to get updated last_accessed
        from enki.beads import get_bead
        bead_updated = get_bead(bead.id)

        weight_after = calculate_weight(bead_updated)

        # Weight should be same or higher after access
        assert weight_after >= weight_before


class TestUpdateAllWeights:
    """Tests for update_all_weights."""

    def test_updates_weights(self, temp_db):
        """Test that update_all_weights processes beads."""
        init_db(temp_db)

        create_bead(content="Bead 1", bead_type="learning")
        create_bead(content="Bead 2", bead_type="decision")
        create_bead(content="Bead 3", bead_type="solution")

        # Run update - new beads shouldn't change much
        updated = update_all_weights()

        # Just verify it runs without error
        assert isinstance(updated, int)

    def test_skips_superseded(self, temp_db):
        """Test that superseded beads are skipped."""
        init_db(temp_db)

        old = create_bead(content="Old", bead_type="learning")
        new = create_bead(content="New", bead_type="learning")
        supersede_bead(old.id, new.id)

        # This should complete without error
        updated = update_all_weights()

        # Verify the old bead still has weight 0
        db = get_db(temp_db)
        row = db.execute(
            "SELECT weight FROM beads WHERE id = ?",
            (old.id,),
        ).fetchone()

        assert row["weight"] == 0
