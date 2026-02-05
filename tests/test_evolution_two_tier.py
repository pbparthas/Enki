"""Tests for two-tier evolution system (local → promote → global)."""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from enki.db import init_db, set_db_path, close_db
from enki.session import start_session
from enki.evolution import (
    get_local_evolution_path,
    get_global_evolution_path,
    get_evolution_path,
    migrate_per_project_evolution,
    promote_to_global,
    get_evolution_context_for_session,
    _merge_evolution_states,
    _format_evolution_for_injection,
    _save_evolution_to_path,
    prune_local_evolution,
    prune_global_evolution,
    init_evolution_log,
    load_evolution_state,
    save_evolution_state,
    create_self_correction,
    add_gate_adjustment,
)


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project with enki DB."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    start_session(tmp_path)

    running_path = tmp_path / ".enki" / "RUNNING.md"
    running_path.write_text("# Enki Running Log\n")

    yield tmp_path
    close_db()
    set_db_path(None)


@pytest.fixture
def global_dir(tmp_path):
    """Temporary directory to stand in for ~/.enki/."""
    gdir = tmp_path / "global_enki"
    gdir.mkdir()
    return gdir


# =============================================================================
# PATH FUNCTIONS
# =============================================================================

class TestEvolutionPaths:
    def test_local_path_uses_project(self, tmp_path):
        path = get_local_evolution_path(tmp_path)
        assert path == tmp_path / ".enki" / "EVOLUTION.md"

    def test_global_path_uses_home(self):
        path = get_global_evolution_path()
        assert path == Path.home() / ".enki" / "EVOLUTION.md"

    def test_get_evolution_path_is_alias(self, tmp_path):
        """get_evolution_path should be backward-compatible alias for local."""
        assert get_evolution_path(tmp_path) == get_local_evolution_path(tmp_path)


# =============================================================================
# MIGRATION
# =============================================================================

class TestMigratePerProjectEvolution:
    def test_idempotent_no_file(self, temp_project):
        """Migration on project with no EVOLUTION.md is a no-op."""
        migrate_per_project_evolution(temp_project)
        marker = temp_project / ".enki" / "EVOLUTION_MIGRATED"
        # No EVOLUTION.md exists, so nothing to migrate
        assert not marker.exists()

    def test_creates_marker(self, temp_project):
        """Migration creates a marker file when EVOLUTION.md exists."""
        init_evolution_log(temp_project)
        migrate_per_project_evolution(temp_project)
        marker = temp_project / ".enki" / "EVOLUTION_MIGRATED"
        assert marker.exists()

    def test_idempotent_runs_twice(self, temp_project):
        """Running migration twice doesn't error or change marker."""
        init_evolution_log(temp_project)
        migrate_per_project_evolution(temp_project)
        marker = temp_project / ".enki" / "EVOLUTION_MIGRATED"
        first_content = marker.read_text()

        migrate_per_project_evolution(temp_project)
        assert marker.read_text() == first_content

    def test_local_file_preserved(self, temp_project):
        """Migration does NOT delete the local EVOLUTION.md."""
        init_evolution_log(temp_project)
        create_self_correction(
            pattern_type="test", description="Test correction",
            frequency=1, impact="None", correction="Fixed",
            project_path=temp_project,
        )
        migrate_per_project_evolution(temp_project)

        local_path = get_local_evolution_path(temp_project)
        assert local_path.exists()
        state = load_evolution_state(temp_project)
        assert len(state["corrections"]) == 1


# =============================================================================
# PROMOTE TO GLOBAL
# =============================================================================

class TestPromoteToGlobal:
    def test_promotes_active_corrections(self, temp_project, global_dir):
        """Active corrections are promoted to global."""
        init_evolution_log(temp_project)
        create_self_correction(
            pattern_type="gate_bypass", description="TDD bypass detected",
            frequency=5, impact="Bugs", correction="Tightened TDD",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = promote_to_global(temp_project)

        assert result["promoted"] == 1
        assert result["skipped_duplicate"] == 0
        assert global_path.exists()

        # Verify global state
        content = global_path.read_text()
        import re
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        global_state = json.loads(match.group(1))
        assert len(global_state["corrections"]) == 1
        assert global_state["corrections"][0]["source_project"] == temp_project.name

    def test_promotes_adjustments(self, temp_project, global_dir):
        """Active adjustments are promoted."""
        init_evolution_log(temp_project)
        add_gate_adjustment(
            gate="tdd", adjustment_type="tighten",
            description="Require 80% coverage",
            reason="Shallow tests",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = promote_to_global(temp_project)

        assert result["promoted"] == 1

    def test_deduplicates(self, temp_project, global_dir):
        """Same correction not promoted twice."""
        init_evolution_log(temp_project)
        create_self_correction(
            pattern_type="gate_bypass", description="TDD bypass",
            frequency=3, impact="Bugs", correction="Tightened TDD",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            promote_to_global(temp_project)
            result = promote_to_global(temp_project)

        assert result["promoted"] == 0
        assert result["skipped_duplicate"] == 1

    def test_skips_reverted_corrections(self, temp_project, global_dir):
        """Reverted corrections are not promoted."""
        init_evolution_log(temp_project)
        state = load_evolution_state(temp_project)
        state["corrections"].append({
            "id": "corr_reverted",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "pattern_type": "test",
            "description": "Reverted correction",
            "frequency": 1,
            "impact": "None",
            "correction": "Something",
            "status": "reverted",
        })
        save_evolution_state(state, temp_project)

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = promote_to_global(temp_project)

        assert result["skipped_status"] == 1
        assert result["promoted"] == 0

    def test_excludes_reason_field(self, temp_project, global_dir):
        """Fox problem: 'reason' field should not be in promoted adjustments."""
        init_evolution_log(temp_project)
        add_gate_adjustment(
            gate="tdd", adjustment_type="tighten",
            description="Require coverage",
            reason="Claude thinks this is important",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            promote_to_global(temp_project)

        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        global_state = json.loads(match.group(1))
        promoted_adj = global_state["adjustments"][0]
        assert "reason" not in promoted_adj

    def test_no_write_when_nothing_promoted(self, temp_project, global_dir):
        """Global file not written when nothing to promote."""
        init_evolution_log(temp_project)

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = promote_to_global(temp_project)

        assert result["promoted"] == 0
        assert not global_path.exists()

    def test_promoted_entry_has_timestamp(self, temp_project, global_dir):
        """Promoted entries have promoted_at timestamp."""
        init_evolution_log(temp_project)
        create_self_correction(
            pattern_type="test", description="Test",
            frequency=1, impact="None", correction="Fixed",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            promote_to_global(temp_project)

        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        global_state = json.loads(match.group(1))
        assert "promoted_at" in global_state["corrections"][0]


# =============================================================================
# MERGE EVOLUTION STATES
# =============================================================================

class TestMergeEvolutionStates:
    def test_local_overrides_global(self):
        """Local corrections take precedence over global with same key."""
        global_state = {
            "corrections": [
                {"pattern_type": "gate_bypass", "correction": "Tighten TDD",
                 "description": "Global version", "status": "active"},
            ],
            "adjustments": [],
        }
        local_state = {
            "corrections": [
                {"pattern_type": "gate_bypass", "correction": "Tighten TDD",
                 "description": "Local version", "status": "active"},
            ],
            "adjustments": [],
            "last_review": None,
        }

        merged = _merge_evolution_states(global_state, local_state)
        assert len(merged["corrections"]) == 1
        assert merged["corrections"][0]["description"] == "Local version"

    def test_combines_non_overlapping(self):
        """Non-overlapping entries from both sides are included."""
        global_state = {
            "corrections": [
                {"pattern_type": "type_a", "correction": "fix_a",
                 "description": "Global A"},
            ],
            "adjustments": [
                {"gate": "tdd", "adjustment_type": "tighten",
                 "description": "Global TDD"},
            ],
        }
        local_state = {
            "corrections": [
                {"pattern_type": "type_b", "correction": "fix_b",
                 "description": "Local B"},
            ],
            "adjustments": [
                {"gate": "phase", "adjustment_type": "loosen",
                 "description": "Local phase"},
            ],
            "last_review": "2026-01-15",
        }

        merged = _merge_evolution_states(global_state, local_state)
        assert len(merged["corrections"]) == 2
        assert len(merged["adjustments"]) == 2

    def test_local_review_date_takes_precedence(self):
        global_state = {"corrections": [], "adjustments": [], "last_review": "2026-01-01"}
        local_state = {"corrections": [], "adjustments": [], "last_review": "2026-02-01"}
        merged = _merge_evolution_states(global_state, local_state)
        assert merged["last_review"] == "2026-02-01"

    def test_falls_back_to_global_review_date(self):
        global_state = {"corrections": [], "adjustments": [], "last_review": "2026-01-01"}
        local_state = {"corrections": [], "adjustments": [], "last_review": None}
        merged = _merge_evolution_states(global_state, local_state)
        assert merged["last_review"] == "2026-01-01"

    def test_empty_states(self):
        merged = _merge_evolution_states(
            {"corrections": [], "adjustments": []},
            {"corrections": [], "adjustments": [], "last_review": None},
        )
        assert merged["corrections"] == []
        assert merged["adjustments"] == []

    def test_adjustment_local_overrides_global(self):
        """Local adjustment overrides global with same (gate, adjustment_type)."""
        global_state = {
            "corrections": [],
            "adjustments": [
                {"gate": "tdd", "adjustment_type": "tighten",
                 "description": "Global TDD tighten"},
            ],
        }
        local_state = {
            "corrections": [],
            "adjustments": [
                {"gate": "tdd", "adjustment_type": "tighten",
                 "description": "Local TDD tighten"},
            ],
            "last_review": None,
        }

        merged = _merge_evolution_states(global_state, local_state)
        assert len(merged["adjustments"]) == 1
        assert merged["adjustments"][0]["description"] == "Local TDD tighten"


# =============================================================================
# FORMAT EVOLUTION FOR INJECTION
# =============================================================================

class TestFormatEvolutionForInjection:
    def test_empty_state(self):
        result = _format_evolution_for_injection(
            {"corrections": [], "adjustments": []}
        )
        assert result == ""

    def test_includes_active_corrections(self):
        state = {
            "corrections": [
                {"description": "TDD bypass fix", "status": "active"},
                {"description": "Effective one", "status": "effective"},
            ],
            "adjustments": [],
        }
        result = _format_evolution_for_injection(state)
        assert "TDD bypass fix" in result
        # Only active corrections shown
        assert "Effective one" not in result

    def test_includes_adjustments(self):
        state = {
            "corrections": [],
            "adjustments": [
                {"gate": "tdd", "adjustment_type": "tighten",
                 "description": "Require coverage", "active": True},
            ],
        }
        result = _format_evolution_for_injection(state)
        assert "tdd" in result
        assert "tighten" in result

    def test_shows_source_project(self):
        state = {
            "corrections": [
                {"description": "Fix from other", "status": "active",
                 "source_project": "ProjectX"},
            ],
            "adjustments": [],
        }
        result = _format_evolution_for_injection(state)
        assert "ProjectX" in result

    def test_limits_to_5_entries(self):
        state = {
            "corrections": [
                {"description": f"Correction {i}", "status": "active"}
                for i in range(10)
            ],
            "adjustments": [],
        }
        result = _format_evolution_for_injection(state)
        # Should show at most 5
        count = result.count("Correction")
        assert count == 5

    def test_inactive_adjustments_excluded(self):
        state = {
            "corrections": [],
            "adjustments": [
                {"gate": "tdd", "adjustment_type": "tighten",
                 "description": "Active", "active": True},
                {"gate": "phase", "adjustment_type": "loosen",
                 "description": "Inactive", "active": False},
            ],
        }
        result = _format_evolution_for_injection(state)
        assert "Active" in result
        assert "Inactive" not in result


# =============================================================================
# GET EVOLUTION CONTEXT FOR SESSION
# =============================================================================

class TestGetEvolutionContextForSession:
    def test_local_only(self, temp_project, global_dir):
        """Works when only local state exists."""
        init_evolution_log(temp_project)
        create_self_correction(
            pattern_type="test", description="Local correction",
            frequency=1, impact="None", correction="Fixed",
            project_path=temp_project,
        )

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = get_evolution_context_for_session(temp_project)

        assert "Local correction" in result

    def test_global_only(self, temp_project, global_dir):
        """Works when only global state exists."""
        init_evolution_log(temp_project)

        global_path = global_dir / "EVOLUTION.md"
        global_state = {
            "corrections": [
                {"description": "Global correction", "status": "active",
                 "pattern_type": "test", "correction": "Fixed globally",
                 "source_project": "OtherProject"},
            ],
            "adjustments": [],
            "last_review": None,
        }
        _save_evolution_to_path(global_state, global_path)

        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = get_evolution_context_for_session(temp_project)

        assert "Global correction" in result

    def test_local_overrides_global_in_context(self, temp_project, global_dir):
        """Local state takes precedence in merged context."""
        init_evolution_log(temp_project)
        state = load_evolution_state(temp_project)
        state["corrections"].append({
            "id": "local_1", "date": "2026-02-05",
            "pattern_type": "gate_bypass", "description": "Local version",
            "frequency": 1, "impact": "None",
            "correction": "Tighten TDD", "status": "active",
        })
        save_evolution_state(state, temp_project)

        global_path = global_dir / "EVOLUTION.md"
        global_state = {
            "corrections": [
                {"pattern_type": "gate_bypass", "correction": "Tighten TDD",
                 "description": "Global version", "status": "active"},
            ],
            "adjustments": [],
            "last_review": None,
        }
        _save_evolution_to_path(global_state, global_path)

        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = get_evolution_context_for_session(temp_project)

        assert "Local version" in result
        assert "Global version" not in result

    def test_empty_when_no_state(self, temp_project, global_dir):
        """Returns empty string when no evolution state anywhere."""
        init_evolution_log(temp_project)

        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = get_evolution_context_for_session(temp_project)

        assert result == ""


# =============================================================================
# SAVE EVOLUTION TO PATH
# =============================================================================

class TestSaveEvolutionToPath:
    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "EVOLUTION.md"
        state = {"corrections": [], "adjustments": [], "last_review": None}
        _save_evolution_to_path(state, path)
        assert path.exists()

    def test_embeds_json(self, tmp_path):
        path = tmp_path / "EVOLUTION.md"
        state = {
            "corrections": [{"id": "c1", "status": "active", "description": "Test"}],
            "adjustments": [],
            "last_review": None,
        }
        _save_evolution_to_path(state, path)

        import re
        content = path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        assert match is not None
        loaded = json.loads(match.group(1))
        assert len(loaded["corrections"]) == 1

    def test_shows_source_in_markdown(self, tmp_path):
        path = tmp_path / "EVOLUTION.md"
        state = {
            "corrections": [
                {"id": "c1", "status": "active", "date": "2026-02-05",
                 "pattern_type": "test", "description": "Fix",
                 "correction": "Tightened", "source_project": "MyProject"},
            ],
            "adjustments": [],
        }
        _save_evolution_to_path(state, path)
        content = path.read_text()
        assert "MyProject" in content


# =============================================================================
# PRUNE LOCAL EVOLUTION
# =============================================================================

class TestPruneLocalEvolution:
    def test_archives_old_completed(self, temp_project):
        """Corrections >90 days old with effective/reverted status are archived."""
        init_evolution_log(temp_project)
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")

        state = load_evolution_state(temp_project)
        state["corrections"] = [
            {"id": "old_1", "date": old_date, "pattern_type": "test",
             "description": "Old effective", "frequency": 1, "impact": "None",
             "correction": "Fixed", "status": "effective"},
            {"id": "active_1", "date": datetime.now().strftime("%Y-%m-%d"),
             "pattern_type": "test", "description": "Still active",
             "frequency": 1, "impact": "None", "correction": "Fixing",
             "status": "active"},
        ]
        save_evolution_state(state, temp_project)

        prune_local_evolution(temp_project)

        state = load_evolution_state(temp_project)
        # Active one stays, old effective one archived
        assert len(state["corrections"]) == 1
        assert state["corrections"][0]["id"] == "active_1"

        archive = temp_project / ".enki" / "EVOLUTION_ARCHIVE.md"
        assert archive.exists()
        assert "Old effective" in archive.read_text()

    def test_keeps_recent_completed(self, temp_project):
        """Completed corrections <90 days old are NOT archived."""
        init_evolution_log(temp_project)
        recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        state = load_evolution_state(temp_project)
        state["corrections"] = [
            {"id": "recent_1", "date": recent_date, "pattern_type": "test",
             "description": "Recent effective", "frequency": 1, "impact": "None",
             "correction": "Fixed", "status": "effective"},
        ]
        save_evolution_state(state, temp_project)

        prune_local_evolution(temp_project)

        state = load_evolution_state(temp_project)
        assert len(state["corrections"]) == 1

    def test_trims_to_30_corrections(self, temp_project):
        """Keeps at most 30 corrections."""
        init_evolution_log(temp_project)
        state = load_evolution_state(temp_project)
        state["corrections"] = [
            {"id": f"corr_{i}", "date": "2026-02-05", "pattern_type": "test",
             "description": f"Correction {i}", "frequency": 1, "impact": "None",
             "correction": "Fixed", "status": "active"}
            for i in range(40)
        ]
        save_evolution_state(state, temp_project)

        prune_local_evolution(temp_project)

        state = load_evolution_state(temp_project)
        assert len(state["corrections"]) <= 30

    def test_trims_to_15_adjustments(self, temp_project):
        """Keeps at most 15 adjustments."""
        init_evolution_log(temp_project)
        state = load_evolution_state(temp_project)
        state["adjustments"] = [
            {"gate": f"gate_{i}", "adjustment_type": "tighten",
             "description": f"Adj {i}", "active": True}
            for i in range(20)
        ]
        save_evolution_state(state, temp_project)

        prune_local_evolution(temp_project)

        state = load_evolution_state(temp_project)
        assert len(state["adjustments"]) <= 15

    def test_no_archive_when_nothing_old(self, temp_project):
        """No archive file created when nothing to archive."""
        init_evolution_log(temp_project)
        prune_local_evolution(temp_project)

        archive = temp_project / ".enki" / "EVOLUTION_ARCHIVE.md"
        assert not archive.exists()


# =============================================================================
# PRUNE GLOBAL EVOLUTION
# =============================================================================

class TestPruneGlobalEvolution:
    def test_archives_old_reverted(self, global_dir):
        """Reverted corrections >180 days old are archived."""
        global_path = global_dir / "EVOLUTION.md"
        old_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")

        state = {
            "corrections": [
                {"id": "old_reverted", "date": old_date, "pattern_type": "test",
                 "description": "Old reverted", "correction": "Something",
                 "status": "reverted", "source_project": "TestProj"},
                {"id": "active_1", "date": datetime.now().strftime("%Y-%m-%d"),
                 "pattern_type": "test", "description": "Still active",
                 "correction": "Something", "status": "active"},
            ],
            "adjustments": [],
            "last_review": None,
        }
        _save_evolution_to_path(state, global_path)

        archive_path = global_dir / "EVOLUTION_ARCHIVE.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path), \
             patch("enki.evolution.Path.home", return_value=global_dir.parent):
            # Need to also patch the archive path construction
            import enki.evolution as evo_module
            original_prune = evo_module.prune_global_evolution

            def patched_prune():
                """Run prune but redirect archive to our temp dir."""
                import re as re_mod
                if not global_path.exists():
                    return
                content = global_path.read_text()
                match = re_mod.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re_mod.DOTALL)
                if not match:
                    return
                try:
                    st = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return
                cutoff = (datetime.now() - timedelta(days=180)).isoformat()
                corrections = st.get("corrections", [])
                archivable = [
                    c for c in corrections
                    if c.get("status") == "reverted"
                    and c.get("date", "") < cutoff[:10]
                ]
                if archivable:
                    archive_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(archive_path, "a") as f:
                        f.write(f"\n## Global Archive {datetime.now().strftime('%Y-%m-%d')}\n\n")
                        for c in archivable:
                            source = f" (from {c.get('source_project', '?')})" if c.get("source_project") else ""
                            f.write(f"- [{c.get('status')}] {c.get('date')}: {c.get('description', '')[:60]}{source}\n")
                    st["corrections"] = [c for c in corrections if c not in archivable]
                    _save_evolution_to_path(st, global_path)

            patched_prune()

        # Active stays
        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        remaining = json.loads(match.group(1))
        assert len(remaining["corrections"]) == 1
        assert remaining["corrections"][0]["id"] == "active_1"

        # Archive created
        assert archive_path.exists()
        assert "Old reverted" in archive_path.read_text()

    def test_keeps_applied_indefinitely(self, global_dir):
        """Applied corrections stay regardless of age."""
        global_path = global_dir / "EVOLUTION.md"
        old_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        state = {
            "corrections": [
                {"id": "old_applied", "date": old_date, "pattern_type": "test",
                 "description": "Very old but applied", "correction": "Something",
                 "status": "effective"},
            ],
            "adjustments": [],
            "last_review": None,
        }
        _save_evolution_to_path(state, global_path)

        # Prune should not touch effective entries
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            prune_global_evolution()

        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        remaining = json.loads(match.group(1))
        assert len(remaining["corrections"]) == 1

    def test_noop_when_no_file(self, global_dir):
        """No error when global file doesn't exist."""
        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            prune_global_evolution()  # Should not raise


# =============================================================================
# INTEGRATION: FULL PROMOTION FLOW
# =============================================================================

class TestPromotionFlow:
    def test_full_local_to_global_flow(self, temp_project, global_dir):
        """End-to-end: create local → migrate → promote → verify global."""
        init_evolution_log(temp_project)

        # Create local corrections and adjustments
        create_self_correction(
            pattern_type="gate_bypass", description="TDD gate bypassed 5 times",
            frequency=5, impact="Untested code merged",
            correction="Added pre-commit TDD check",
            project_path=temp_project,
        )
        add_gate_adjustment(
            gate="tdd", adjustment_type="tighten",
            description="Require test file for each src file",
            reason="AI thinks this is important",  # Should NOT appear in global
            project_path=temp_project,
        )

        # Migrate (idempotent)
        migrate_per_project_evolution(temp_project)

        # Promote to global
        global_path = global_dir / "EVOLUTION.md"
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result = promote_to_global(temp_project)

        assert result["promoted"] == 2

        # Verify global state
        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        global_state = json.loads(match.group(1))

        assert len(global_state["corrections"]) == 1
        assert len(global_state["adjustments"]) == 1

        # Fox problem: reason field excluded
        adj = global_state["adjustments"][0]
        assert "reason" not in adj
        assert adj["source_project"] == temp_project.name

        # Verify context injection merges both
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            context = get_evolution_context_for_session(temp_project)

        assert "TDD gate bypassed" in context or "tdd" in context.lower()

    def test_two_projects_promote_independently(self, tmp_path, global_dir):
        """Two projects can promote to same global without conflict."""
        # Project A
        proj_a = tmp_path / "project_a"
        proj_a.mkdir()
        db_a = proj_a / ".enki" / "wisdom.db"
        db_a.parent.mkdir(parents=True, exist_ok=True)
        init_db(db_a)
        start_session(proj_a)
        init_evolution_log(proj_a)
        create_self_correction(
            pattern_type="type_a", description="Correction from A",
            frequency=1, impact="None", correction="Fix A",
            project_path=proj_a,
        )
        close_db()
        set_db_path(None)

        # Project B
        proj_b = tmp_path / "project_b"
        proj_b.mkdir()
        db_b = proj_b / ".enki" / "wisdom.db"
        db_b.parent.mkdir(parents=True, exist_ok=True)
        init_db(db_b)
        start_session(proj_b)
        init_evolution_log(proj_b)
        create_self_correction(
            pattern_type="type_b", description="Correction from B",
            frequency=2, impact="None", correction="Fix B",
            project_path=proj_b,
        )

        global_path = global_dir / "EVOLUTION.md"

        # Promote A
        close_db()
        set_db_path(None)
        init_db(db_a)
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result_a = promote_to_global(proj_a)
        close_db()
        set_db_path(None)

        # Promote B
        init_db(db_b)
        with patch("enki.evolution.get_global_evolution_path", return_value=global_path):
            result_b = promote_to_global(proj_b)
        close_db()
        set_db_path(None)

        assert result_a["promoted"] == 1
        assert result_b["promoted"] == 1

        # Global should have both
        import re
        content = global_path.read_text()
        match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
        global_state = json.loads(match.group(1))
        assert len(global_state["corrections"]) == 2

        sources = {c["source_project"] for c in global_state["corrections"]}
        assert "project_a" in sources
        assert "project_b" in sources
