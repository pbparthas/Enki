"""setup.py — First-run onboarding for Enki.

Interactive setup (max 2 questions):
1. Project directory
2. Assistant name
Then auto-detect and configure everything else.
"""

import json
import shutil
import stat
from pathlib import Path

from enki.config import ensure_config
from enki.db import DB_DIR, ENKI_ROOT, init_all


# Default values (used if user skips customization)
_DEFAULTS = {
    "assistant_name": "Enki",
    "description": (
        "A persistent second brain for software engineering. "
        "You remember, advise, manage, and learn."
    ),
    "voice_style": "Conversational, direct, opinionated. No filler phrases.",
}

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_HOOKS_DIR = CLAUDE_DIR / "hooks"
CLAUDE_SETTINGS = CLAUDE_DIR / "settings.json"


def _find_template() -> Path | None:
    """Locate PERSONA.md.template, checking repo tree then package."""
    # Check relative to this file (installed or dev)
    repo_template = (
        Path(__file__).resolve().parent.parent.parent / "templates" / "PERSONA.md.template"
    )
    if repo_template.exists():
        return repo_template
    # Check package data location
    pkg_template = Path(__file__).resolve().parent / "templates" / "PERSONA.md.template"
    if pkg_template.exists():
        return pkg_template
    return None


def _generate_persona(project_dir: Path, assistant_name: str) -> Path:
    """Generate PERSONA.md from template into {project}/.enki/PERSONA.md."""
    enki_project_dir = project_dir / ".enki"
    enki_project_dir.mkdir(parents=True, exist_ok=True)
    persona_path = enki_project_dir / "PERSONA.md"

    if persona_path.exists():
        return persona_path

    template_path = _find_template()
    if template_path:
        content = template_path.read_text()
    else:
        # Fallback inline template
        content = (
            "# {assistant_name}\n\n"
            "## Identity\n"
            "You are {assistant_name}, an AI engineering assistant.\n"
            "{description}\n\n"
            "## Voice\n"
            "{voice_style}\n\n"
            "## Principles\n"
            "- Remember decisions across sessions\n"
            "- Enforce quality gates before code changes\n"
            "- Challenge assumptions through structured debate\n"
            "- Never modify your own enforcement rules\n"
        )

    rendered = content.format(
        assistant_name=assistant_name,
        description=_DEFAULTS["description"],
        voice_style=_DEFAULTS["voice_style"],
    )
    persona_path.write_text(rendered)
    return persona_path


def _install_hooks() -> int:
    """Copy hook scripts from repo to ~/.claude/hooks/. Returns count installed."""
    # Find hooks source relative to this file (repo layout: src/enki/ → ../../scripts/hooks/)
    hooks_src = Path(__file__).resolve().parent.parent.parent / "scripts" / "hooks"
    if not hooks_src.exists():
        return 0

    CLAUDE_HOOKS_DIR.mkdir(parents=True, exist_ok=True)

    installed = 0
    for src_file in sorted(hooks_src.glob("enki-*.sh")):
        dst_file = CLAUDE_HOOKS_DIR / src_file.name
        shutil.copy2(src_file, dst_file)
        dst_file.chmod(dst_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed += 1

    return installed


def _register_mcp_server() -> bool:
    """Register Enki MCP server in ~/.claude/settings.json. Returns True if registered."""
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    settings = {}
    if CLAUDE_SETTINGS.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}

    mcp_servers = settings.setdefault("mcpServers", {})

    # Don't overwrite existing enki entry
    if "enki" in mcp_servers:
        return False

    mcp_servers["enki"] = {
        "command": "python",
        "args": ["-m", "enki.mcp_server"],
        "env": {},
    }

    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    return True


def run_setup(
    project_dir: str | None = None,
    assistant_name: str | None = None,
    interactive: bool = True,
    **_kwargs,
) -> dict:
    """Run first-time Enki setup.

    Args:
        project_dir: Project directory path (default: current directory)
        assistant_name: Name for the AI assistant (default: Enki)
        interactive: Whether to prompt for missing values

    Returns dict with setup results.
    """
    results = {"steps": []}

    # Step 1: Collect info (max 2 questions)
    if interactive:
        if project_dir is None:
            raw = input("Project directory? [.] ").strip()
            project_dir = raw if raw else "."
        if assistant_name is None:
            raw = input("Assistant name? [Enki] ").strip()
            assistant_name = raw if raw else _DEFAULTS["assistant_name"]

    project_dir = project_dir or "."
    assistant_name = assistant_name or _DEFAULTS["assistant_name"]
    project_path = Path(project_dir).resolve()

    results["name"] = assistant_name
    results["project_dir"] = str(project_path)

    print(f"\nEnki v3 Setup")
    print("─────────────\n")
    print("Setting up...")

    # Step 2: Create directories
    for d in [ENKI_ROOT, ENKI_ROOT / "config", DB_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    results["steps"].append("directories_created")
    print("  ✓ Created ~/.enki/")

    # Step 3: Initialize databases
    init_all()
    results["steps"].append("databases_initialized")
    print("  ✓ Initialized databases")

    # Step 4: Create config
    ensure_config()
    results["steps"].append("config_created")
    print("  ✓ Created ~/.enki/config/enki.toml")

    # Step 5: Generate PERSONA.md
    persona_path = _generate_persona(project_path, assistant_name)
    results["steps"].append("persona_generated")
    results["persona_path"] = str(persona_path)
    print("  ✓ Generated PERSONA.md")

    # Step 6: Install hooks to ~/.claude/hooks/
    hooks_count = _install_hooks()
    results["hooks_installed"] = hooks_count
    if hooks_count:
        results["steps"].append("hooks_installed")
        print(f"  ✓ Copied hooks to ~/.claude/hooks/")
    else:
        results["steps"].append("hooks_skipped")
        print("  ⚠ Hook scripts not found (install from repo)")

    # Step 7: Register MCP server
    registered = _register_mcp_server()
    if registered:
        results["steps"].append("mcp_registered")
        print("  ✓ Registered MCP server")
    else:
        results["steps"].append("mcp_exists")
        print("  ✓ MCP server already registered")

    print(f'\nReady! Start Claude Code and run:')
    print(f'  enki_goal "your first task"')

    return results
