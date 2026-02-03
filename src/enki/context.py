"""Adaptive context loading for optimized token usage.

Load different amounts of context based on session needs:
- MINIMAL: Quick tasks, bug fixes
- STANDARD: Normal development
- FULL: Complex features, debugging
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
import json


class ContextTier(Enum):
    """Context loading tiers."""
    MINIMAL = "minimal"    # PHASE, current task only
    STANDARD = "standard"  # + SPEC, recent tasks, blackboard
    FULL = "full"          # + All beads, full history
    AUTO = "auto"          # Detect based on complexity


@dataclass
class LoadedContext:
    """What was loaded into context."""
    tier: ContextTier
    phase: Optional[str] = None
    goal: Optional[str] = None
    spec: Optional[str] = None
    task_graph: Optional[dict] = None
    blackboard: Optional[dict] = None
    beads: list[dict] = field(default_factory=list)
    token_estimate: int = 0


# Approximate tokens per character
CHARS_PER_TOKEN = 4


def get_context_config(project_path: Path = None) -> dict:
    """Get context configuration from .enki/config.json.

    Args:
        project_path: Project directory

    Returns:
        Configuration dict with defaults
    """
    project_path = project_path or Path.cwd()
    config_file = project_path / ".enki" / "config.json"

    defaults = {
        "context_tier": "auto",
        "context_max_tokens": 50000,
        "context_include_beads": True,
        "context_bead_limit": 10,
    }

    if config_file.exists():
        try:
            with open(config_file) as f:
                config = json.load(f)
                defaults.update(config)
        except (json.JSONDecodeError, IOError):
            pass

    return defaults


def save_context_config(config: dict, project_path: Path = None) -> None:
    """Save context configuration to .enki/config.json.

    Args:
        config: Configuration to save
        project_path: Project directory
    """
    project_path = project_path or Path.cwd()
    enki_dir = project_path / ".enki"
    enki_dir.mkdir(exist_ok=True)

    config_file = enki_dir / "config.json"

    # Merge with existing config
    existing = {}
    if config_file.exists():
        try:
            with open(config_file) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    existing.update(config)

    with open(config_file, "w") as f:
        json.dump(existing, f, indent=2)


def detect_tier(project_path: Path = None) -> ContextTier:
    """Auto-detect appropriate context tier.

    Looks at:
    - Current phase
    - Number of files in scope
    - Complexity of current task (task count)
    - Session duration

    Args:
        project_path: Project directory

    Returns:
        Detected ContextTier
    """
    project_path = project_path or Path.cwd()
    enki_dir = project_path / ".enki"

    if not enki_dir.exists():
        return ContextTier.MINIMAL

    # Check current phase
    phase_file = enki_dir / "PHASE"
    phase = "intake"
    if phase_file.exists():
        phase = phase_file.read_text().strip()

    # Research/intake phases need minimal context
    if phase in ("intake", "research"):
        return ContextTier.MINIMAL

    # Check if we have a spec (debate/plan phase needs standard)
    if phase in ("debate", "plan", "spec"):
        return ContextTier.STANDARD

    # Implementation phase: check task complexity
    if phase in ("implement", "review", "test"):
        state_file = enki_dir / "STATE.md"
        if state_file.exists():
            state = state_file.read_text()
            # Count tasks
            task_count = state.count("- [ ]") + state.count("- [x]")
            if task_count > 10:
                return ContextTier.FULL
            elif task_count > 3:
                return ContextTier.STANDARD

        # Check scope size
        scope_file = enki_dir / "SCOPE"
        if scope_file.exists():
            scope_content = scope_file.read_text()
            file_count = len([l for l in scope_content.split("\n") if l.strip()])
            if file_count > 10:
                return ContextTier.FULL

        return ContextTier.STANDARD

    return ContextTier.STANDARD


def load_context(
    tier: ContextTier = ContextTier.AUTO,
    project_path: Path = None,
    max_tokens: int = 50000,
) -> LoadedContext:
    """Load context based on tier.

    Args:
        tier: Context tier to load
        project_path: Project directory
        max_tokens: Maximum tokens to use for context

    Returns:
        LoadedContext with loaded information
    """
    project_path = project_path or Path.cwd()
    enki_dir = project_path / ".enki"

    if tier == ContextTier.AUTO:
        tier = detect_tier(project_path)

    context = LoadedContext(
        tier=tier,
        phase=None,
        goal=None,
        spec=None,
        task_graph=None,
        blackboard=None,
        beads=[],
        token_estimate=0,
    )

    # Always load phase (MINIMAL tier)
    phase_file = enki_dir / "PHASE"
    if phase_file.exists():
        context.phase = phase_file.read_text().strip()
        context.token_estimate += 50

    # Always load goal if present
    goal_file = enki_dir / "GOAL"
    if goal_file.exists():
        context.goal = goal_file.read_text().strip()
        context.token_estimate += len(context.goal) // CHARS_PER_TOKEN

    if tier == ContextTier.MINIMAL:
        return context

    # STANDARD: Add spec and current tasks
    spec_dir = enki_dir / "specs"
    if spec_dir.exists():
        # Load most recent approved spec
        specs = sorted(spec_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for spec_file in specs:
            content = spec_file.read_text()
            token_cost = len(content) // CHARS_PER_TOKEN
            if context.token_estimate + token_cost > max_tokens:
                # Truncate if too large
                max_chars = (max_tokens - context.token_estimate) * CHARS_PER_TOKEN
                content = content[:max_chars] + "\n\n[Truncated for context limit]"
                token_cost = len(content) // CHARS_PER_TOKEN

            context.spec = content
            context.token_estimate += token_cost
            break

    # Load task graph state
    state_file = enki_dir / "STATE.md"
    if state_file.exists():
        state = state_file.read_text()
        token_cost = len(state) // CHARS_PER_TOKEN
        if context.token_estimate + token_cost < max_tokens:
            context.task_graph = {"raw": state[:4000]}
            context.token_estimate += min(token_cost, 1000)

    if tier == ContextTier.STANDARD:
        return context

    # FULL: Add beads and full history
    config = get_context_config(project_path)

    if config.get("context_include_beads", True):
        try:
            from .search import search

            # Search for relevant beads using goal or spec content
            query = context.goal or (context.spec[:500] if context.spec else "")
            if query:
                results = search(
                    query=query,
                    limit=config.get("context_bead_limit", 10),
                )

                for result in results:
                    bead_tokens = len(result.bead.content) // CHARS_PER_TOKEN
                    if context.token_estimate + bead_tokens > max_tokens:
                        break

                    context.beads.append({
                        "id": result.bead.id,
                        "type": result.bead.type,
                        "content": result.bead.content,
                        "summary": result.bead.summary,
                        "score": result.score,
                    })
                    context.token_estimate += bead_tokens
        except Exception:
            pass  # Beads are optional

    return context


def format_context_for_injection(context: LoadedContext) -> str:
    """Format loaded context for injection into conversation.

    Args:
        context: LoadedContext to format

    Returns:
        Formatted markdown string
    """
    parts = [
        "# Enki Context",
        "",
        f"**Tier**: {context.tier.value}",
        f"**Tokens**: ~{context.token_estimate:,}",
        "",
    ]

    if context.phase:
        parts.extend([
            f"## Phase: {context.phase.upper()}",
            "",
        ])

    if context.goal:
        parts.extend([
            f"## Goal",
            "",
            context.goal,
            "",
        ])

    if context.spec:
        parts.extend([
            "## Current Spec",
            "",
            context.spec[:2000] if len(context.spec) > 2000 else context.spec,
            "",
        ])

    if context.task_graph:
        raw = context.task_graph.get("raw", "")
        parts.extend([
            "## Task State",
            "",
            raw[:1000] if len(raw) > 1000 else raw,
            "",
        ])

    if context.beads:
        parts.extend([
            "## Relevant Knowledge",
            "",
        ])
        for bead in context.beads[:5]:  # Top 5
            parts.append(f"### [{bead['type']}] (score: {bead['score']:.2f})")
            content = bead['content']
            if len(content) > 500:
                content = content[:500] + "..."
            parts.append(content)
            parts.append("")

    return "\n".join(parts)


def preview_context(
    tier: ContextTier = ContextTier.AUTO,
    project_path: Path = None,
) -> str:
    """Preview what would be loaded without actually loading full content.

    Args:
        tier: Context tier to preview
        project_path: Project directory

    Returns:
        Formatted preview string
    """
    project_path = project_path or Path.cwd()
    enki_dir = project_path / ".enki"

    if tier == ContextTier.AUTO:
        detected = detect_tier(project_path)
        tier_display = f"AUTO -> {detected.value}"
    else:
        tier_display = tier.value
        detected = tier

    parts = [
        "# Context Preview",
        "",
        f"**Tier**: {tier_display}",
        "",
        "## What Would Be Loaded",
        "",
    ]

    # Phase (always)
    phase_file = enki_dir / "PHASE"
    if phase_file.exists():
        phase = phase_file.read_text().strip()
        parts.append(f"- Phase: {phase}")
    else:
        parts.append("- Phase: (not set)")

    # Goal (always)
    goal_file = enki_dir / "GOAL"
    if goal_file.exists():
        goal = goal_file.read_text().strip()
        parts.append(f"- Goal: {goal[:50]}...")
    else:
        parts.append("- Goal: (not set)")

    if detected in (ContextTier.STANDARD, ContextTier.FULL):
        # Spec
        spec_dir = enki_dir / "specs"
        if spec_dir.exists():
            specs = list(spec_dir.glob("*.md"))
            if specs:
                latest = max(specs, key=lambda p: p.stat().st_mtime)
                size = latest.stat().st_size
                parts.append(f"- Spec: {latest.name} ({size:,} bytes)")
            else:
                parts.append("- Spec: (none)")
        else:
            parts.append("- Spec: (none)")

        # Tasks
        state_file = enki_dir / "STATE.md"
        if state_file.exists():
            state = state_file.read_text()
            task_count = state.count("- [ ]") + state.count("- [x]")
            parts.append(f"- Tasks: {task_count} in graph")
        else:
            parts.append("- Tasks: (no orchestration)")

    if detected == ContextTier.FULL:
        config = get_context_config(project_path)
        bead_limit = config.get("context_bead_limit", 10)
        parts.append(f"- Beads: up to {bead_limit} relevant beads")

    parts.append("")
    parts.append("## Tier Descriptions")
    parts.append("")
    parts.append("- **minimal**: Phase + goal only (quick tasks)")
    parts.append("- **standard**: + spec + task graph (normal development)")
    parts.append("- **full**: + relevant beads (complex debugging)")

    return "\n".join(parts)


def set_default_tier(tier: ContextTier, project_path: Path = None) -> None:
    """Set default context tier for project.

    Args:
        tier: Tier to set as default
        project_path: Project directory
    """
    save_context_config(
        {"context_tier": tier.value},
        project_path,
    )
