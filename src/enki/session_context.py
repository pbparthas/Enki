"""Session-start context assembly helpers."""

from __future__ import annotations

from pathlib import Path

from enki.db import ENKI_ROOT


def generate_orientation_block(project: str, phase: str, goal: str, tier: str) -> str:
    """Generate a short dynamic orientation block for session start."""
    phase_key = (phase or "none").strip().lower()
    action_map = {
        "none": "Call enki_goal to initialise this project",
        "planning": "Call enki_goal to initialise this project",
        "spec": "Igi challenge is pending or complete. Check findings, present to operator, then call enki_approve(stage='igi')",
        "approved": "Write Architect implementation spec, present to operator, call enki_approve(stage='architect')",
        "implement": "ORCHESTRATOR MODE ONLY. Your first and only action is enki_wave(project='{project}'). Do NOT read source files, explore code, or implement directly. Spawn agents and report results.",
        "validating": "Validation in progress. Present results to operator, call enki_approve(stage='test')",
        "complete": "Sprint complete. Run session end pipeline.",
    }
    next_action = action_map.get(phase_key, "Check enki_phase status and continue pipeline.").format(project=project)
    return (
        f"## 𒀭 Enki Session — {project}\n"
        f"- Goal: {goal}\n"
        f"- Phase: {phase} | Tier: {tier}\n"
        f"- Next action: {next_action}"
    )


def build_session_start_context(project: str, goal: str, tier: str, phase: str) -> str:
    """Assemble session-start context in strict operational order."""
    parts: list[str] = []

    # 1) Orientation block
    parts.append(generate_orientation_block(project, phase, goal, tier))

    # 2) Global pipeline reference
    pipeline_path = ENKI_ROOT / "PIPELINE.md"
    if pipeline_path.exists():
        text = pipeline_path.read_text().strip()
        if text:
            parts.append(text)

    # 3) Persona
    persona_path = ENKI_ROOT / "persona" / "PERSONA.md"
    if persona_path.exists():
        persona = persona_path.read_text().strip()
        if persona:
            parts.append(persona)

    # 4) Uru enforcement state
    try:
        from enki.gates.uru import inject_enforcement_context

        uru_ctx = (inject_enforcement_context() or "").strip()
        if uru_ctx:
            parts.append(uru_ctx)
    except Exception:
        pass

    # 5) Abzu memory
    try:
        from enki.memory.abzu import inject_session_start

        memory_ctx = (inject_session_start(project, goal, tier) or "").strip()
        if memory_ctx:
            parts.append(memory_ctx)
    except Exception:
        pass

    return "\n\n".join(parts).strip()
