"""Adaptive context loading for optimized token usage.

Load different amounts of context based on session needs:
- MINIMAL: Quick tasks, bug fixes
- STANDARD: Normal development
- FULL: Complex features, debugging
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
import json

logger = logging.getLogger(__name__)


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
        except Exception as e:
            logger.warning("Non-fatal error in context (bead loading): %s", e)
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


# ============================================================
# CONTEXT.md Generation (Enki v2)
# ============================================================
# Single injection point for session start context.
# Always produces output. NEVER returns empty string.
# Capped at 3KB.

MAX_BYTES = 3072


def generate_context_md(project_path: Path) -> str:
    """Generate CONTEXT.md from beads. Always produces output.

    Returns non-empty string always. If no beads exist,
    returns skeleton with empty sections. NEVER returns "".
    """
    from .session import get_phase, get_tier, get_goal

    sections = []

    # Section 1: Current State
    sections.append(_generate_current_state(project_path))

    # Section 2: Recent Decisions (kind='decision', last 7 days)
    sections.append(_generate_decisions_section(project_path))

    # Section 3: Working Style (type='style' or kind='preference')
    sections.append(_generate_style_section(project_path))

    # Section 4: Active Preferences (kind='preference', not archived)
    # Populated by Proactive spec (Spec 2) — skeleton here
    sections.append(_generate_preferences_section())

    # Section 5: Open Questions
    sections.append(_generate_questions_section(project_path))

    # Section 6: Last Session
    sections.append(_generate_last_session(project_path))

    result = "\n\n".join(sections)

    # Enforce 3KB cap — truncate sections in priority order
    if len(result.encode("utf-8")) > MAX_BYTES:
        result = _truncate_context(sections)

    return result


def _generate_current_state(project_path: Path) -> str:
    """Generate Current State section."""
    from .session import get_phase, get_tier, get_goal

    phase = get_phase(project_path) or "intake"
    tier = get_tier(project_path) or "trivial"
    goal = get_goal(project_path) or "(not set)"

    project_name = project_path.name if project_path else "unknown"

    # Last session summary
    last_summary = _get_last_session_summary(project_path)

    lines = [
        "## Current State",
        f"Phase: {phase} | Tier: {tier} | Goal: {goal}",
        f"Project: {project_name}",
    ]
    if last_summary:
        lines.append(f"Last session: {last_summary}")

    return "\n".join(lines)


def _generate_decisions_section(project_path: Path) -> str:
    """Generate Recent Decisions section (last 7 days)."""
    header = "## Recent Decisions (last 7 days)"
    try:
        from .search import search
        results = search(
            query="decision",
            project=None,  # Cross-project
            bead_type="decision",
            limit=5,
            log_accesses=False,
        )
        if results:
            items = []
            for r in results[:5]:
                proj = r.bead.project or "global"
                date = str(r.bead.created_at)[:10] if r.bead.created_at else "?"
                summary = r.bead.summary or r.bead.content[:100]
                items.append(f"- [DECISION] {summary} ({proj}, {date})")
            return header + "\n" + "\n".join(items)
    except Exception as e:
        logger.warning("Context generation (decisions): %s", e)
    return header + "\n(none found)"


def _generate_style_section(project_path: Path) -> str:
    """Generate Working Style section."""
    header = "## Working Style"
    try:
        from .search import search
        results = search(
            query="style preference approach",
            project=None,
            limit=5,
            log_accesses=False,
        )
        style_beads = [
            r for r in results
            if r.bead.type in ("style", "approach")
            or getattr(r.bead, "kind", "fact") == "preference"
        ]
        if style_beads:
            items = []
            for r in style_beads[:5]:
                label = r.bead.type.upper()
                summary = r.bead.summary or r.bead.content[:100]
                items.append(f"- [{label}] {summary}")
            return header + "\n" + "\n".join(items)
    except Exception as e:
        logger.warning("Context generation (style): %s", e)
    return header + "\n(none found)"


def _generate_preferences_section() -> str:
    """Generate Active Preferences section.

    Section is ALWAYS present, even if empty (shows '(none yet)').
    Content is populated when Proactive spec (Spec 2) adds get_active_preferences().
    """
    header = "## Active Preferences (apply to all work)"
    try:
        from .retention import get_active_preferences
        prefs = get_active_preferences(limit=3)
        if prefs:
            items = []
            for p in prefs:
                proj = f"({p.project})" if p.project else "(global)"
                summary = p.summary or p.content[:100]
                items.append(f"- {proj} {summary}")
            return header + "\n" + "\n".join(items)
    except (ImportError, AttributeError):
        pass  # Proactive spec not yet implemented
    except Exception as e:
        logger.warning("Context generation (preferences): %s", e)
    return header + "\n(none yet)"


def _generate_questions_section(project_path: Path) -> str:
    """Generate Open Questions section."""
    header = "## Open Questions"
    try:
        from .search import search
        results = search(
            query="open question unresolved",
            project=str(project_path) if project_path else None,
            limit=3,
            log_accesses=False,
        )
        if results:
            items = []
            for r in results[:3]:
                summary = r.bead.summary or r.bead.content[:100]
                items.append(f"- {summary}")
            return header + "\n" + "\n".join(items)
    except Exception as e:
        logger.warning("Context generation (questions): %s", e)
    return header + "\n(none)"


def _generate_last_session(project_path: Path) -> str:
    """Generate Last Session summary section."""
    header = "## Last Session"
    summary = _get_last_session_detail(project_path)
    if summary:
        return header + "\n" + summary
    return header + "\n(no previous session)"


def _get_last_session_summary(project_path: Path) -> Optional[str]:
    """Get one-line summary of last session from archives."""
    if not project_path:
        return None
    sessions_dir = project_path / ".enki" / "sessions"
    if not sessions_dir.exists():
        return None
    archives = sorted(sessions_dir.glob("*.md"), reverse=True)
    if not archives:
        return None
    try:
        content = archives[0].read_text()
        # Extract first non-empty, non-header line
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("---"):
                return line[:100]
    except OSError:
        pass
    return None


def _get_last_session_detail(project_path: Path) -> Optional[str]:
    """Get 2-3 sentence summary of last session."""
    if not project_path:
        return None
    sessions_dir = project_path / ".enki" / "sessions"
    if not sessions_dir.exists():
        return None
    archives = sorted(sessions_dir.glob("*.md"), reverse=True)
    if not archives:
        return None
    try:
        content = archives[0].read_text()
        # Extract first paragraph after any header
        lines = []
        in_content = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                in_content = True
                continue
            if in_content and stripped:
                lines.append(stripped)
                if len(lines) >= 3:
                    break
            elif in_content and not stripped and lines:
                break
        if lines:
            return " ".join(lines)[:300]
    except OSError:
        pass
    return None


def _truncate_context(sections: list[str]) -> str:
    """Truncate CONTEXT.md to fit within MAX_BYTES.

    Priority (truncate first → last):
    1. Open Questions (least critical)
    2. Recent Decisions (keep top 3 instead of 5)
    3. Working Style (keep top 3 instead of 5)
    4. Active Preferences — NEVER truncated
    5. Current State — NEVER truncated
    """
    # Try removing Open Questions content first
    result = "\n\n".join(sections)
    if len(result.encode("utf-8")) <= MAX_BYTES:
        return result

    # Truncate Open Questions to just header
    if len(sections) > 4:
        sections[4] = "## Open Questions\n(truncated)"
    result = "\n\n".join(sections)
    if len(result.encode("utf-8")) <= MAX_BYTES:
        return result

    # Truncate Decisions to 3 items
    if len(sections) > 1:
        lines = sections[1].split("\n")
        sections[1] = "\n".join(lines[:4])  # header + 3 items
    result = "\n\n".join(sections)
    if len(result.encode("utf-8")) <= MAX_BYTES:
        return result

    # Truncate Style to 3 items
    if len(sections) > 2:
        lines = sections[2].split("\n")
        sections[2] = "\n".join(lines[:4])  # header + 3 items
    result = "\n\n".join(sections)
    if len(result.encode("utf-8")) <= MAX_BYTES:
        return result

    # Last resort: truncate Last Session
    if len(sections) > 5:
        sections[5] = "## Last Session\n(truncated)"

    result = "\n\n".join(sections)
    # Hard truncate if still over
    encoded = result.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        result = encoded[:MAX_BYTES].decode("utf-8", errors="ignore")

    return result
