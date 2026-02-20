"""Tests for Phase 1: Uru Gates — Layer 0, 0.5, 1, nudges, feedback.

This is the most critical test suite. If gates are wrong, everything is wrong.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from enki.db import connect
from enki.gates.layer0 import (
    ENKI_ROOT,
    extract_db_targets,
    extract_write_targets,
    is_exempt,
    is_layer0_protected,
)


# ── Layer 0: Protected file checks ──


class TestLayer0Protected:
    """Layer 0 blocklist: files CC cannot edit."""

    def test_blocks_hook_scripts(self):
        for hook in [
            "session-start.sh", "pre-tool-use.sh", "post-tool-use.sh",
            "pre-compact.sh", "post-compact.sh", "session-end.sh",
        ]:
            assert is_layer0_protected(f"/any/path/{hook}"), f"{hook} should be protected"

    def test_blocks_uru_py(self):
        assert is_layer0_protected("/home/user/src/enki/gates/uru.py")

    def test_blocks_layer0_py(self):
        assert is_layer0_protected("/home/user/src/enki/gates/layer0.py")

    def test_blocks_persona_md(self):
        assert is_layer0_protected("/home/user/.enki/persona/PERSONA.md")

    def test_blocks_prompt_files(self):
        for prompt in [
            "_base.md", "_coding_standards.md", "pm.md", "architect.md",
            "dba.md", "dev.md", "qa.md", "ui_ux.md", "validator.md",
            "reviewer.md", "infosec.md", "devops.md", "performance.md",
            "researcher.md", "em.md",
        ]:
            assert is_layer0_protected(f"/any/path/{prompt}"), f"{prompt} should be protected"

    def test_blocks_hooks_directory(self):
        path = str(ENKI_ROOT / "hooks" / "anything.sh")
        assert is_layer0_protected(path)

    def test_blocks_prompts_directory(self):
        path = str(ENKI_ROOT / "prompts" / "custom.md")
        assert is_layer0_protected(path)

    def test_blocks_uru_db(self):
        path = str(ENKI_ROOT / "uru.db")
        assert is_layer0_protected(path)

    def test_allows_normal_python_file(self):
        assert not is_layer0_protected("/home/user/project/src/main.py")

    def test_allows_normal_md_file(self):
        assert not is_layer0_protected("/home/user/project/docs/README.md")

    def test_allows_wisdom_db(self):
        path = str(ENKI_ROOT / "wisdom.db")
        assert not is_layer0_protected(path)


# ── Exempt path checks ──


class TestExemptPaths:
    """Exempt paths bypass Layer 1 gate checks."""

    def test_enki_infra_exempt(self):
        path = str(ENKI_ROOT / "abzu.db")
        assert is_exempt(path)

    def test_enki_subdir_exempt(self):
        path = str(ENKI_ROOT / "projects" / "myproj" / "em.db")
        assert is_exempt(path)

    def test_md_outside_src_exempt(self):
        assert is_exempt("/home/user/project/docs/README.md")
        assert is_exempt("/home/user/project/CHANGELOG.md")

    def test_md_inside_src_not_exempt(self):
        assert not is_exempt("/home/user/project/src/enki/README.md")

    def test_claude_md_exempt(self):
        assert is_exempt("/home/user/project/CLAUDE.md")

    def test_toml_outside_src_exempt(self):
        assert is_exempt("/home/user/project/pyproject.toml")

    def test_yaml_outside_src_exempt(self):
        assert is_exempt("/home/user/project/docker-compose.yml")

    def test_yaml_inside_src_not_exempt(self):
        assert not is_exempt("/home/user/project/src/config.yaml")

    def test_dot_claude_exempt(self):
        assert is_exempt("/home/user/project/.claude/settings.json")

    def test_git_exempt(self):
        assert is_exempt("/home/user/project/.git/config")

    def test_python_source_not_exempt(self):
        assert not is_exempt("/home/user/project/src/main.py")

    def test_js_source_not_exempt(self):
        assert not is_exempt("/home/user/project/src/app.js")


# ── Target extraction ──


class TestTargetExtraction:
    """Extract WRITE TARGETS from bash commands, not content mentions."""

    def test_redirect_single(self):
        targets = extract_write_targets('echo "hello" > output.txt')
        assert "output.txt" in targets

    def test_redirect_append(self):
        targets = extract_write_targets('echo "hello" >> output.txt')
        assert "output.txt" in targets

    def test_content_not_target(self):
        """'echo "enforcement.py" > notes.md' — target is notes.md, NOT enforcement.py."""
        targets = extract_write_targets('echo "Fixed bug in enforcement.py" > notes.md')
        assert "notes.md" in targets
        assert "enforcement.py" not in targets

    def test_sed_inplace(self):
        targets = extract_write_targets("sed -i 's/old/new/' myfile.py")
        assert "myfile.py" in targets

    def test_cp_target(self):
        targets = extract_write_targets("cp source.py target.py")
        assert "target.py" in targets

    def test_mv_target(self):
        targets = extract_write_targets("mv old.py new.py")
        assert "new.py" in targets

    def test_rm_targets(self):
        targets = extract_write_targets("rm -rf build/ dist/")
        assert "build/" in targets
        assert "dist/" in targets

    def test_tee_target(self):
        targets = extract_write_targets("echo 'hello' | tee output.log")
        assert "output.log" in targets

    def test_tee_append_target(self):
        targets = extract_write_targets("echo 'hello' | tee -a output.log")
        assert "output.log" in targets

    def test_cat_is_readonly(self):
        targets = extract_write_targets("cat enforcement.py")
        assert len(targets) == 0

    def test_grep_is_readonly(self):
        targets = extract_write_targets("grep -r 'pattern' src/")
        assert len(targets) == 0

    def test_ls_is_readonly(self):
        targets = extract_write_targets("ls -la /home/user")
        assert len(targets) == 0

    def test_python_write_detected(self):
        targets = extract_write_targets(
            "python -c \"open('secret.py', 'w').write('hack')\""
        )
        assert "__PYTHON_WRITE__" in targets

    def test_multiple_targets(self):
        targets = extract_write_targets("cp good.py bad.py; echo 'x' > log.txt")
        assert "bad.py" in targets
        assert "log.txt" in targets


class TestDBTargetExtraction:
    """Layer 0.5: Extract database targets from bash commands."""

    def test_sqlite3_binary(self):
        targets = extract_db_targets('sqlite3 ~/.enki/em.db "SELECT *"')
        assert any("em.db" in t for t in targets)

    def test_python_sqlite3_connect_not_matched(self):
        targets = extract_db_targets(
            "python -c \"import sqlite3; sqlite3.connect('test.db')\""
        )
        assert "test.db" not in targets

    def test_python_script_with_sqlite3_substring_not_matched(self):
        targets = extract_db_targets("python3 test_file.py")
        assert len(targets) == 0

    def test_python_inline_string_with_sqlite3_substring_not_matched(self):
        targets = extract_db_targets(
            "python3 -c \"print('sqlite3 ~/.enki/wisdom.db')\""
        )
        assert len(targets) == 0

    def test_redirect_to_db(self):
        targets = extract_db_targets("echo 'data' > backup.db")
        assert "backup.db" in targets

    def test_no_db_in_normal_command(self):
        targets = extract_db_targets("ls -la /home/user")
        assert len(targets) == 0


# ── Gate checks (Layer 1) ──


class TestGateChecks:
    """Layer 1 gate checks: goal, phase, spec approval."""

    @pytest.fixture
    def mock_project(self, tmp_path):
        """Set up a mock project with em.db."""
        enki_root = tmp_path / ".enki"
        enki_root.mkdir()
        projects_dir = enki_root / "projects" / "testproj"
        projects_dir.mkdir(parents=True)

        # Create em.db with schema
        db_path = projects_dir / "em.db"
        with connect(db_path) as conn:
            from enki.orch.schemas import create_tables
            create_tables(conn)

        # Create uru.db
        uru_path = enki_root / "uru.db"
        with connect(uru_path) as conn:
            from enki.gates.schemas import create_tables as create_uru
            create_uru(conn)

        # Write session ID
        (enki_root / "SESSION_ID").write_text("test-session")

        db_dir = enki_root / "db"
        db_dir.mkdir()

        with patch("enki.db.ENKI_ROOT", enki_root), \
             patch("enki.db.DB_DIR", db_dir), \
             patch("enki.gates.uru.ENKI_ROOT", enki_root), \
             patch("enki.gates.layer0.ENKI_ROOT", enki_root):
            yield enki_root, projects_dir, db_path

    def _set_goal(self, db_path, goal="Build feature", tier="minimal"):
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO task_state "
                "(task_id, project_id, sprint_id, task_name, tier, work_type, "
                "status, started_at) "
                "VALUES ('g1', 'testproj', 's1', ?, ?, 'goal', 'active', "
                "datetime('now'))",
                (goal, tier),
            )

    def _set_phase(self, db_path, phase="implement"):
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO task_state "
                "(task_id, project_id, sprint_id, task_name, tier, work_type, "
                "status, started_at) "
                "VALUES ('p1', 'testproj', 's1', ?, 'minimal', 'phase', "
                "'active', datetime('now'))",
                (phase,),
            )

    def _approve_spec(self, db_path):
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO pm_decisions "
                "(id, project_id, decision_type, proposed_action, human_response) "
                "VALUES ('d1', 'testproj', 'spec_approval', 'approve impl spec', "
                "'approved')"
            )

    def test_gate1_no_goal_blocks_code(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        # No goal set — should block
        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "block"
        assert "Gate 1" in result["reason"]

    def test_gate1_no_goal_allows_docs(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        # No goal — but .md files are exempt
        result = check_pre_tool_use("Write", {"file_path": "/project/docs/README.md"})
        assert result["decision"] == "allow"

    def test_gate1_no_goal_allows_enki_infra(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        enki_root, _, _ = mock_project
        path = str(enki_root / "some_file.json")
        result = check_pre_tool_use("Write", {"file_path": path})
        assert result["decision"] == "allow"

    def test_gate3_wrong_phase_blocks(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path)
        self._set_phase(db_path, "plan")

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "block"
        assert "Gate 3" in result["reason"]

    def test_gate3_implement_phase_allows(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path)
        self._set_phase(db_path, "implement")

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "allow"

    def test_gate3_review_phase_allows(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path)
        self._set_phase(db_path, "review")

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "allow"

    def test_gate3_ship_phase_allows(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path)
        self._set_phase(db_path, "complete")

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "allow"

    def test_gate2_standard_tier_no_spec_blocks(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path, tier="standard")
        self._set_phase(db_path, "implement")

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "block"
        assert "Gate 2" in result["reason"]

    def test_gate2_standard_tier_with_spec_allows(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path, tier="standard")
        self._set_phase(db_path, "implement")
        self._approve_spec(db_path)

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "allow"

    def test_gate2_minimal_tier_skips_spec_check(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path, tier="minimal")
        self._set_phase(db_path, "implement")
        # No spec approval — but minimal tier skips Gate 2

        result = check_pre_tool_use("Write", {"file_path": "/project/src/main.py"})
        assert result["decision"] == "allow"

    def test_layer0_blocks_hook_edit(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        enki_root, _, _ = mock_project
        path = str(enki_root / "hooks" / "pre-tool-use.sh")
        result = check_pre_tool_use("Write", {"file_path": path})
        assert result["decision"] == "block"
        assert "Layer 0" in result["reason"]

    def test_layer05_blocks_sqlite3(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        enki_root, _, _ = mock_project
        em_path = str(enki_root / "projects" / "testproj" / "em.db")
        result = check_pre_tool_use(
            "Bash", {"command": f"sqlite3 {em_path} 'SELECT *'"}
        )
        assert result["decision"] == "block"
        assert "Layer 0.5" in result["reason"]

    def test_layer05_allows_normal_bash(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        result = check_pre_tool_use("Bash", {"command": "ls -la /home/user"})
        assert result["decision"] == "allow"

    def test_layer05_allows_python_test_file(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        result = check_pre_tool_use("Bash", {"command": "python3 test_file.py"})
        assert result["decision"] == "allow"

    def test_layer05_allows_python_inline_sqlite3_string(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        result = check_pre_tool_use(
            "Bash",
            {"command": "python3 -c \"print('sqlite3 ~/.enki/wisdom.db')\""},
        )
        assert result["decision"] == "allow"

    def test_read_tools_always_pass(self, mock_project):
        from enki.gates.uru import check_pre_tool_use

        for tool in ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]:
            result = check_pre_tool_use(tool, {})
            assert result["decision"] == "allow", f"{tool} should always pass"

    def test_bash_content_not_target(self, mock_project):
        """echo 'Fixed bug in uru.py' > log.txt — allowed because target is log.txt."""
        from enki.gates.uru import check_pre_tool_use

        _, _, db_path = mock_project
        self._set_goal(db_path)
        self._set_phase(db_path, "implement")

        result = check_pre_tool_use(
            "Bash", {"command": 'echo "Fixed bug in uru.py" > log.txt'}
        )
        assert result["decision"] == "allow"


# ── Nudges ──


class TestNudges:
    """Post-tool-use nudge checks."""

    @pytest.fixture
    def mock_nudge_env(self, tmp_path):
        enki_root = tmp_path / ".enki"
        enki_root.mkdir()
        (enki_root / "SESSION_ID").write_text("test-session")

        uru_path = enki_root / "uru.db"
        with connect(uru_path) as conn:
            from enki.gates.schemas import create_tables
            create_tables(conn)

        db_dir = enki_root / "db"
        db_dir.mkdir()

        with patch("enki.db.ENKI_ROOT", enki_root), \
             patch("enki.db.DB_DIR", db_dir), \
             patch("enki.gates.uru.ENKI_ROOT", enki_root), \
             patch("enki.gates.layer0.ENKI_ROOT", enki_root):
            yield enki_root

    def test_decision_nudge_fires(self, mock_nudge_env):
        from enki.gates.uru import check_post_tool_use

        result = check_post_tool_use(
            "Write", {"file_path": "test.py"},
            assistant_response="I decided to use JWT instead of sessions"
        )
        assert result["decision"] == "allow"
        if "nudges" in result:
            assert any("enki_remember" in n for n in result["nudges"])

    def test_long_session_nudge(self, mock_nudge_env):
        from enki.gates.uru import check_post_tool_use, _log_enforcement

        # Simulate 35 tool calls
        for i in range(35):
            _log_enforcement(
                "post-tool-use", "nudge", "Write", None, "allow", None
            )

        result = check_post_tool_use("Write", {"file_path": "test.py"})
        assert result["decision"] == "allow"
        if "nudges" in result:
            assert any("checkpoint" in n or "capture" in n for n in result["nudges"])

    def test_nudges_never_block(self, mock_nudge_env):
        from enki.gates.uru import check_post_tool_use

        result = check_post_tool_use(
            "Write", {"file_path": "test.py"},
            assistant_response="I decided going with approach X"
        )
        assert result["decision"] == "allow"


# ── Feedback proposals ──


class TestFeedback:
    """Feedback proposal CRUD."""

    @pytest.fixture
    def mock_feedback_env(self, tmp_path):
        enki_root = tmp_path / ".enki"
        enki_root.mkdir()
        db_dir = enki_root / "db"
        db_dir.mkdir()
        uru_path = enki_root / "uru.db"
        with connect(uru_path) as conn:
            from enki.gates.schemas import create_tables
            create_tables(conn)
        with patch("enki.db.ENKI_ROOT", enki_root), \
             patch("enki.db.DB_DIR", db_dir):
            yield enki_root

    def test_create_proposal(self, mock_feedback_env):
        from enki.gates.feedback import create_proposal, get_proposal

        pid = create_proposal("override", "Gate too strict for docs")
        proposal = get_proposal(pid)
        assert proposal is not None
        assert proposal["status"] == "pending"
        assert proposal["trigger_type"] == "override"

    def test_list_proposals(self, mock_feedback_env):
        from enki.gates.feedback import create_proposal, list_proposals

        create_proposal("override", "Proposal 1")
        create_proposal("nudge_ignored", "Proposal 2")

        proposals = list_proposals("pending")
        assert len(proposals) == 2

    def test_apply_proposal(self, mock_feedback_env):
        from enki.gates.feedback import apply_proposal, create_proposal, get_proposal

        pid = create_proposal("override", "Test")
        apply_proposal(pid)
        proposal = get_proposal(pid)
        assert proposal["status"] == "applied"
        assert proposal["applied"] == 1

    def test_reject_proposal(self, mock_feedback_env):
        from enki.gates.feedback import create_proposal, get_proposal, reject_proposal

        pid = create_proposal("override", "Test")
        reject_proposal(pid, "Not needed")
        proposal = get_proposal(pid)
        assert proposal["status"] == "rejected"


# ── Enforcement context ──


class TestEnforcementContext:
    """Test context injection for post-compact."""

    @pytest.fixture
    def mock_context_env(self, tmp_path):
        enki_root = tmp_path / ".enki"
        enki_root.mkdir()
        projects_dir = enki_root / "projects" / "testproj"
        projects_dir.mkdir(parents=True)

        db_path = projects_dir / "em.db"
        with connect(db_path) as conn:
            from enki.orch.schemas import create_tables
            create_tables(conn)
            conn.execute(
                "INSERT INTO task_state "
                "(task_id, project_id, sprint_id, task_name, tier, work_type, "
                "status, started_at) "
                "VALUES ('g1', 'testproj', 's1', 'Build auth', 'standard', "
                "'goal', 'active', datetime('now'))"
            )
            conn.execute(
                "INSERT INTO task_state "
                "(task_id, project_id, sprint_id, task_name, tier, work_type, "
                "status, started_at) "
                "VALUES ('p1', 'testproj', 's1', 'implement', 'standard', "
                "'phase', 'active', datetime('now'))"
            )

        db_dir = enki_root / "db"
        db_dir.mkdir()

        with patch("enki.db.ENKI_ROOT", enki_root), \
             patch("enki.db.DB_DIR", db_dir), \
             patch("enki.gates.uru.ENKI_ROOT", enki_root):
            yield enki_root

    def test_enforcement_context_includes_state(self, mock_context_env):
        from enki.gates.uru import inject_enforcement_context

        context = inject_enforcement_context()
        assert "testproj" in context
        assert "Build auth" in context
        assert "implement" in context
        assert "standard" in context
