"""Tests for search module."""

import pytest

from enki.db import init_db
from enki.beads import create_bead, supersede_bead
from enki.search import search, search_similar


class TestSearch:
    """Tests for hybrid search."""

    def test_finds_by_keyword(self, temp_db):
        """Test finding beads by keyword match."""
        init_db(temp_db)

        create_bead(
            content="Use JWT tokens for authentication",
            bead_type="decision",
        )
        create_bead(
            content="Database migration strategy",
            bead_type="learning",
        )

        results = search("JWT authentication", log_accesses=False)

        assert len(results) >= 1
        assert any("JWT" in r.bead.content for r in results)

    def test_finds_by_semantic_similarity(self, temp_db):
        """Test finding beads by semantic similarity."""
        init_db(temp_db)

        create_bead(
            content="Handle errors gracefully with try-catch blocks",
            bead_type="solution",
        )

        # Search with semantically similar but different words
        results = search("exception handling patterns", log_accesses=False)

        # Should find the error handling bead via semantic similarity
        assert len(results) >= 1

    def test_excludes_superseded(self, temp_db):
        """Test that superseded beads are excluded."""
        init_db(temp_db)

        old = create_bead(
            content="Old way to handle authentication",
            bead_type="learning",
        )
        new = create_bead(
            content="New improved authentication method",
            bead_type="learning",
        )
        supersede_bead(old.id, new.id)

        results = search("authentication", log_accesses=False)

        ids = [r.bead.id for r in results]
        assert old.id not in ids

    def test_filters_by_project(self, temp_db):
        """Test project filtering."""
        init_db(temp_db)

        create_bead(
            content="Project A specific knowledge",
            bead_type="learning",
            project="project-a",
        )
        create_bead(
            content="Project B specific knowledge",
            bead_type="learning",
            project="project-b",
        )
        create_bead(
            content="Global knowledge applicable everywhere",
            bead_type="learning",
            project=None,  # Global
        )

        # Search for project-a should include project-a and global
        results = search("knowledge", project="project-a", log_accesses=False)

        projects = [r.bead.project for r in results]
        assert "project-a" in projects or None in projects
        assert "project-b" not in projects

    def test_filters_by_type(self, temp_db):
        """Test type filtering."""
        init_db(temp_db)

        create_bead(content="A decision about X", bead_type="decision")
        create_bead(content="A solution for Y", bead_type="solution")
        create_bead(content="A learning about Z", bead_type="learning")

        results = search("about", bead_type="decision", log_accesses=False)

        types = [r.bead.type for r in results]
        assert all(t == "decision" for t in types)

    def test_respects_limit(self, temp_db):
        """Test result limiting."""
        init_db(temp_db)

        for i in range(10):
            create_bead(content=f"Test bead number {i}", bead_type="learning")

        results = search("test bead", limit=3, log_accesses=False)

        assert len(results) <= 3

    def test_returns_sources(self, temp_db):
        """Test that results include source information."""
        init_db(temp_db)

        create_bead(
            content="Authentication with JWT tokens",
            bead_type="decision",
        )

        results = search("JWT tokens", log_accesses=False)

        assert len(results) >= 1
        assert results[0].sources  # Should have at least one source
        assert all(s in ["keyword", "semantic"] for s in results[0].sources)

    def test_empty_query_returns_empty(self, temp_db):
        """Test that empty query returns no results."""
        init_db(temp_db)

        create_bead(content="Some content", bead_type="learning")

        results = search("", log_accesses=False)
        assert len(results) == 0


class TestSearchSimilar:
    """Tests for search_similar."""

    def test_finds_similar_beads(self, temp_db):
        """Test finding similar beads."""
        init_db(temp_db)

        bead1 = create_bead(
            content="Error handling with try-catch in Python",
            bead_type="solution",
        )
        create_bead(
            content="Exception handling patterns in JavaScript",
            bead_type="solution",
        )
        create_bead(
            content="Database connection pooling strategies",
            bead_type="solution",
        )

        similar = search_similar(bead1.id, limit=5)

        # Should find the JavaScript one as similar
        assert len(similar) >= 1

    def test_excludes_source_bead(self, temp_db):
        """Test that source bead is excluded from results."""
        init_db(temp_db)

        bead = create_bead(content="Test content", bead_type="learning")
        create_bead(content="Similar test content", bead_type="learning")

        similar = search_similar(bead.id, limit=5)

        ids = [r.bead.id for r in similar]
        assert bead.id not in ids

    def test_returns_empty_for_missing_bead(self, temp_db):
        """Test that missing bead returns empty results."""
        init_db(temp_db)

        similar = search_similar("non-existent-id")
        assert len(similar) == 0
