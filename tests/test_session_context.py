from unittest.mock import patch


def test_generate_orientation_block_actions():
    from enki.session_context import generate_orientation_block

    expected = {
        "none": "Call enki_goal to initialise this project",
        "planning": "Call enki_goal to initialise this project",
        "spec": "call enki_approve(stage='igi')",
        "approved": "call enki_approve(stage='architect')",
        "implement": "Spawn EM orchestration",
        "validating": "call enki_approve(stage='test')",
        "complete": "Run session end pipeline",
    }
    for phase, fragment in expected.items():
        block = generate_orientation_block("proj-x", phase, "Ship API", "standard")
        assert block.startswith("## 𒀭 Enki Session — proj-x")
        assert f"- Phase: {phase} | Tier: standard" in block
        assert fragment in block


def test_session_start_context_order_strict(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "persona").mkdir(parents=True)
    (enki_root / "PIPELINE.md").write_text("# Enki Pipeline — Operational Reference\npipeline text")
    (enki_root / "persona" / "PERSONA.md").write_text("persona text")

    with patch("enki.session_context.ENKI_ROOT", enki_root), \
         patch("enki.gates.uru.inject_enforcement_context", return_value="uru text"), \
         patch("enki.memory.abzu.inject_session_start", return_value="memory text"):
        from enki.session_context import build_session_start_context
        ctx = build_session_start_context("proj-x", "goal-x", "standard", "spec")

    orientation_idx = ctx.find("## 𒀭 Enki Session — proj-x")
    pipeline_idx = ctx.find("# Enki Pipeline — Operational Reference")
    persona_idx = ctx.find("persona text")
    uru_idx = ctx.find("uru text")
    memory_idx = ctx.find("memory text")

    assert -1 not in {orientation_idx, pipeline_idx, persona_idx, uru_idx, memory_idx}
    assert orientation_idx < pipeline_idx < persona_idx < uru_idx < memory_idx


def test_pipeline_missing_is_graceful(tmp_path):
    enki_root = tmp_path / ".enki"
    (enki_root / "persona").mkdir(parents=True)
    (enki_root / "persona" / "PERSONA.md").write_text("persona text")

    with patch("enki.session_context.ENKI_ROOT", enki_root), \
         patch("enki.gates.uru.inject_enforcement_context", return_value="uru text"), \
         patch("enki.memory.abzu.inject_session_start", return_value="memory text"):
        from enki.session_context import build_session_start_context
        ctx = build_session_start_context("proj-x", "goal-x", "standard", "implement")

    assert "## 𒀭 Enki Session — proj-x" in ctx
    assert "persona text" in ctx
    assert "uru text" in ctx
    assert "memory text" in ctx
    assert "# Enki Pipeline — Operational Reference" not in ctx


def test_tool_descriptions_include_when_and_call():
    from enki.mcp_server import get_tools

    targets = {
        "enki_goal",
        "enki_approve",
        "enki_phase",
        "enki_remember",
        "enki_recall",
        "enki_bug",
    }
    tool_map = {t["name"]: t for t in get_tools()}
    for name in targets:
        desc = tool_map[name]["description"].lower()
        assert "when" in desc
        assert "call" in desc
