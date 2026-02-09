"""Tests for Spec 4: PM-EM Orchestration — triage, mode, handover, gates, PE-1 through PE-7."""

from pathlib import Path
import json

import pytest

from enki.db import init_db, get_db, set_db_path, reset_connection
from enki.session import (
    get_mode, set_mode, ensure_project_enki_dir,
    set_phase, set_tier, set_goal, get_tier,
)
from enki.pm import (
    triage, TriageResult, TIER_GATES,
    handover_pm_to_em, escalate_em_to_pm, activate_gates,
    create_spec, approve_spec, generate_approval_token,
    generate_perspectives,
)
from enki.enforcement import (
    check_mode_restrictions, get_active_gates, GateResult,
    is_impl_file, is_enki_file,
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    reset_connection()
    set_db_path(db_path)
    init_db(db_path)
    # Create .enki dir with session state
    enki_dir = tmp_path / ".enki"
    enki_dir.mkdir(exist_ok=True)
    (enki_dir / "PHASE").write_text("implement")
    (enki_dir / "TIER").write_text("trivial")
    (enki_dir / "SESSION_ID").write_text("test-session")
    yield tmp_path
    reset_connection()
    set_db_path(None)


# ========== Mode Tracking ==========


class TestModeTracking:
    def test_default_mode_is_none(self, setup_db):
        assert get_mode(setup_db) == "none"

    def test_set_and_get_pm(self, setup_db):
        set_mode("pm", setup_db)
        assert get_mode(setup_db) == "pm"

    def test_set_and_get_em(self, setup_db):
        set_mode("em", setup_db)
        assert get_mode(setup_db) == "em"

    def test_set_none(self, setup_db):
        set_mode("pm", setup_db)
        set_mode("none", setup_db)
        assert get_mode(setup_db) == "none"

    def test_invalid_mode_returns_none(self, setup_db):
        mode_file = setup_db / ".enki" / "MODE"
        mode_file.write_text("invalid")
        assert get_mode(setup_db) == "none"


# ========== Triage System ==========


class TestTriage:
    def test_trivial_tier(self, setup_db):
        result = triage("fix a typo in README", setup_db)
        assert result.tier == "trivial"
        assert result.gate_set == ["gate_1"]
        assert result.requires_spec is False
        assert result.requires_debate is False

    def test_quick_fix_tier(self, setup_db):
        result = triage("fix the login bug", setup_db)
        assert result.tier == "quick_fix"
        assert "gate_3" in result.gate_set

    def test_feature_tier(self, setup_db):
        result = triage("implement user authentication endpoint", setup_db)
        assert result.tier == "feature"
        assert result.requires_spec is True
        assert result.requires_debate is False

    def test_major_tier(self, setup_db):
        result = triage("refactor the entire architecture and redesign the database", setup_db)
        assert result.tier == "major"
        assert result.requires_spec is True
        assert result.requires_debate is True

    def test_default_to_quick_fix(self, setup_db):
        result = triage("do something vague", setup_db)
        assert result.tier == "quick_fix"

    def test_activate_gates_writes_file(self, setup_db):
        result = triage("implement new feature", setup_db)
        activate_gates(result, setup_db)
        gates_file = setup_db / ".enki" / "GATES"
        assert gates_file.exists()
        gates = json.loads(gates_file.read_text())
        assert gates == result.gate_set

    def test_triage_result_has_all_fields(self, setup_db):
        result = triage("add feature", setup_db)
        assert isinstance(result, TriageResult)
        assert isinstance(result.tier, str)
        assert isinstance(result.estimated_files, int)
        assert isinstance(result.gate_set, list)
        assert isinstance(result.requires_spec, bool)
        assert isinstance(result.requires_debate, bool)
        assert isinstance(result.suggested_agents, list)


# ========== Mode Restrictions ==========


class TestModeRestrictions:
    def test_no_mode_allows_all(self, setup_db):
        result = check_mode_restrictions("Edit", "src/app.py", setup_db)
        assert result.allowed is True

    def test_pm_blocks_impl_edit(self, setup_db):
        set_mode("pm", setup_db)
        result = check_mode_restrictions("Edit", "src/app.py", setup_db)
        assert result.allowed is False
        assert "PM MODE" in result.reason

    def test_pm_allows_spec_edit(self, setup_db):
        set_mode("pm", setup_db)
        result = check_mode_restrictions("Edit", "specs/feature.md", setup_db)
        assert result.allowed is True

    def test_pm_allows_enki_files(self, setup_db):
        set_mode("pm", setup_db)
        result = check_mode_restrictions("Edit", ".enki/PHASE", setup_db)
        assert result.allowed is True

    def test_em_blocks_spec_edit(self, setup_db):
        set_mode("em", setup_db)
        result = check_mode_restrictions("Edit", "specs/feature.md", setup_db)
        assert result.allowed is False
        assert "EM MODE" in result.reason

    def test_em_allows_impl_edit(self, setup_db):
        set_mode("em", setup_db)
        result = check_mode_restrictions("Edit", "src/app.py", setup_db)
        assert result.allowed is True

    def test_pm_allows_read(self, setup_db):
        set_mode("pm", setup_db)
        result = check_mode_restrictions("Read", "src/app.py", setup_db)
        assert result.allowed is True

    def test_em_allows_non_spec_markdown(self, setup_db):
        set_mode("em", setup_db)
        result = check_mode_restrictions("Edit", "docs/README.md", setup_db)
        assert result.allowed is True


# ========== Dynamic Gate Activation ==========


class TestDynamicGates:
    def test_missing_gates_file_returns_none(self, setup_db):
        result = get_active_gates(setup_db)
        assert result is None  # PE-4: fail-closed

    def test_reads_gates_from_file(self, setup_db):
        gates_file = setup_db / ".enki" / "GATES"
        gates_file.write_text(json.dumps(["gate_1", "gate_3"]))
        result = get_active_gates(setup_db)
        assert result == ["gate_1", "gate_3"]

    def test_corrupted_gates_returns_none(self, setup_db):
        gates_file = setup_db / ".enki" / "GATES"
        gates_file.write_text("not json")
        result = get_active_gates(setup_db)
        assert result is None  # Fail-closed


# ========== Handover Protocol ==========


class TestHandover:
    def _setup_approved_spec(self, project_path):
        """Create minimal approved spec for handover testing."""
        specs_dir = project_path / ".enki" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        spec_content = """# Test Spec

## Problem
Test problem.

## Solution
Test solution.

## Task Breakdown

### Wave 1 — Foundation
| Task | Agent | Dependencies | Files |
|------|-------|-------------|-------|
| Build module | Dev | none | src/mod.py |

### Wave 2 — Integration
| Task | Agent | Dependencies | Files |
|------|-------|-------------|-------|
| Write tests | QA | Build module | tests/test_mod.py |
"""
        (specs_dir / "test-spec.md").write_text(spec_content)
        # Mark as approved in RUNNING.md
        running = project_path / ".enki" / "RUNNING.md"
        running.write_text("# Running\n\nSPEC APPROVED: test-spec\n")

    def test_handover_requires_approved_spec(self, setup_db):
        with pytest.raises(ValueError, match="not approved"):
            handover_pm_to_em("nonexistent", setup_db, "test-session")

    def test_handover_switches_mode_to_em(self, setup_db):
        self._setup_approved_spec(setup_db)
        result = handover_pm_to_em("test-spec", setup_db, "test-session")
        assert result["mode"] == "em"
        assert get_mode(setup_db) == "em"

    def test_handover_creates_bead(self, setup_db):
        self._setup_approved_spec(setup_db)
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        handover_pm_to_em("test-spec", setup_db, "test-session")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after > before  # G-10: handover always creates bead

    def test_handover_sends_message(self, setup_db):
        self._setup_approved_spec(setup_db)
        handover_pm_to_em("test-spec", setup_db, "test-session")
        db = get_db()
        msg = db.execute(
            "SELECT * FROM messages WHERE subject LIKE '%Handover%'"
        ).fetchone()
        assert msg is not None
        assert msg["importance"] == "critical"


# ========== Escalation ==========


class TestEscalation:
    def test_escalation_switches_mode_to_pm(self, setup_db):
        from enki.messaging import register_agent
        register_agent("pm", "pm", "test-session")
        register_agent("em", "em", "test-session")
        set_mode("em", setup_db)
        result = escalate_em_to_pm("Blocked by auth issue", setup_db, "test-session")
        assert result["mode"] == "pm"
        assert get_mode(setup_db) == "pm"

    def test_escalation_creates_bead(self, setup_db):
        from enki.messaging import register_agent
        register_agent("pm", "pm", "test-session")
        register_agent("em", "em", "test-session")
        db = get_db()
        before = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        escalate_em_to_pm("Blocked", setup_db, "test-session")
        after = db.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assert after > before  # G-10: escalation always creates bead

    def test_escalation_sends_message(self, setup_db):
        from enki.messaging import register_agent
        register_agent("pm", "pm", "test-session")
        register_agent("em", "em", "test-session")
        escalate_em_to_pm("Need design review", setup_db, "test-session")
        db = get_db()
        msg = db.execute(
            "SELECT * FROM messages WHERE subject LIKE '%Escalation%'"
        ).fetchone()
        assert msg is not None
        assert msg["importance"] == "high"


# ========== MCP Handler Tests ==========


class TestPMEMHandlers:
    def test_triage_handler(self, setup_db):
        from enki.mcp_server import _handle_triage
        result = _handle_triage(
            {"goal": "fix a bug in login", "project": str(setup_db)},
            remote=False,
        )
        assert "Triage:" in result[0].text

    def test_handover_handler_no_spec(self, setup_db):
        from enki.mcp_server import _handle_handover
        result = _handle_handover(
            {"to": "em", "project": str(setup_db)},
            remote=False,
        )
        assert "ERROR" in result[0].text

    def test_escalate_handler(self, setup_db):
        from enki.messaging import register_agent
        register_agent("pm", "pm", "test-session")
        register_agent("em", "em", "test-session")
        from enki.mcp_server import _handle_escalate
        result = _handle_escalate(
            {"reason": "Design question", "project": str(setup_db)},
            remote=False,
        )
        assert "Escalation complete" in result[0].text


# ========== PE-1 through PE-7 Verification ==========


class TestGovernanceConstraints:
    def test_pe1_pm_cannot_edit_impl(self, setup_db):
        """PE-1: PM cannot edit implementation files."""
        set_mode("pm", setup_db)
        result = check_mode_restrictions("Edit", "src/app.py", setup_db)
        assert result.allowed is False

    def test_pe2_em_cannot_modify_specs(self, setup_db):
        """PE-2: EM cannot modify specs."""
        set_mode("em", setup_db)
        result = check_mode_restrictions("Edit", "specs/feature.md", setup_db)
        assert result.allowed is False

    def test_pe3_handover_requires_approved_spec(self, setup_db):
        """PE-3: Handover requires approved spec."""
        with pytest.raises(ValueError, match="not approved"):
            handover_pm_to_em("nonexistent-spec", setup_db, "test-session")

    def test_pe4_gates_default_to_all(self, setup_db):
        """PE-4: Gate set defaults to ALL gates when GATES file missing."""
        result = get_active_gates(setup_db)
        assert result is None  # None = caller must treat as all-active

    def test_pe5_triage_is_deterministic(self):
        """PE-5: Triage uses no LLM."""
        import enki.pm
        source = Path(enki.pm.__file__).read_text()
        triage_section = source.split("def triage(")[1].split("\ndef ")[0]
        for forbidden in ["embed", "anthropic", "openai", "gemini", "model"]:
            assert forbidden not in triage_section.lower()

    def test_pe6_handover_auto_creates_bead(self):
        """PE-6: Handover auto-creates bead."""
        import enki.pm
        source = Path(enki.pm.__file__).read_text()
        handover_section = source.split("def handover_pm_to_em")[1].split("\ndef ")[0]
        assert "create_bead" in handover_section

    def test_pe7_escalation_auto_creates_bead(self):
        """PE-7: Escalation auto-creates bead."""
        import enki.pm
        source = Path(enki.pm.__file__).read_text()
        escalation_section = source.split("def escalate_em_to_pm")[1].split("\n\n\n")[0]
        assert "create_bead" in escalation_section


# ========== Dispatch Map Update ==========


class TestDispatchMapUpdate:
    def test_35_handlers_registered(self):
        """Dispatch map now has 35 handlers (32 + 3 PM-EM)."""
        from enki.mcp_server import TOOL_HANDLERS
        assert len(TOOL_HANDLERS) == 35

    def test_pm_em_tools_in_dispatch(self):
        from enki.mcp_server import TOOL_HANDLERS
        pm_em_tools = {"enki_triage", "enki_handover", "enki_escalate"}
        assert pm_em_tools.issubset(set(TOOL_HANDLERS.keys()))
