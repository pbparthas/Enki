"""setup.py — First-run onboarding for Enki.

Interactive setup:
1. Ask name, role, projects
2. Generate PERSONA.md from template
3. Initialize databases
4. Install hooks from repo to ~/.enki/hooks/
5. Create enki.toml with defaults
"""

import shutil
from pathlib import Path

from enki.config import ensure_config
from enki.db import ENKI_ROOT, init_all


# Default PERSONA.md template
_PERSONA_TEMPLATE = """\
# {name}

## Identity
You are Enki — a second brain for software engineering.
Your human partner is **{name}**, a {role}.

## Voice
- Direct, no filler
- Technical but clear
- Challenge weak reasoning (Ereshkigal mode)
- Store decisions, patterns, fixes — not trivia

## Working Style
- Start simple, add complexity only when earned
- Tests before features when possible
- Prefer editing existing files over creating new ones
- Keep CLAUDE.md under 300 lines

## Projects
{projects_section}
"""


def run_setup(
    name: str | None = None,
    role: str | None = None,
    projects: list[str] | None = None,
    interactive: bool = True,
    repo_root: str | None = None,
) -> dict:
    """Run first-time Enki setup.

    Args:
        name: User's name (prompted if None and interactive)
        role: User's role (prompted if None and interactive)
        projects: List of project names
        interactive: Whether to prompt for missing values
        repo_root: Path to Enki repo (for hook installation)

    Returns dict with setup results.
    """
    results = {"steps": []}

    # Step 1: Collect info
    if interactive and not name:
        name = input("What's your name? ").strip()
    if interactive and not role:
        role = input("What's your role? (e.g., backend engineer, fullstack dev) ").strip()
    if interactive and not projects:
        raw = input("Projects you're working on? (comma-separated, or empty) ").strip()
        projects = [p.strip() for p in raw.split(",") if p.strip()] if raw else []

    name = name or "Engineer"
    role = role or "software engineer"
    projects = projects or []

    results["name"] = name
    results["role"] = role
    results["projects"] = projects

    # Step 2: Create directories
    dirs = [
        ENKI_ROOT,
        ENKI_ROOT / "config",
        ENKI_ROOT / "hooks",
        ENKI_ROOT / "prompts",
        ENKI_ROOT / "persona",
        ENKI_ROOT / "sessions",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    results["steps"].append("directories_created")

    # Step 3: Initialize databases
    init_all()
    results["steps"].append("databases_initialized")

    # Step 4: Generate PERSONA.md
    persona_path = ENKI_ROOT / "persona" / "PERSONA.md"
    if not persona_path.exists():
        projects_section = "\n".join(f"- {p}" for p in projects) if projects else "- (none yet)"
        persona_content = _PERSONA_TEMPLATE.format(
            name=name,
            role=role,
            projects_section=projects_section,
        )
        persona_path.write_text(persona_content)
        results["steps"].append("persona_created")
        results["persona_path"] = str(persona_path)
    else:
        results["steps"].append("persona_exists")

    # Step 5: Create config
    ensure_config()
    results["steps"].append("config_created")

    # Step 6: Install hooks from repo
    hooks_installed = _install_hooks(repo_root)
    if hooks_installed:
        results["steps"].append("hooks_installed")
        results["hooks_installed"] = hooks_installed
    else:
        results["steps"].append("hooks_skipped")

    # Step 7: Initialize project em.db for each project
    for project in projects:
        from enki.db import em_db
        with em_db(project) as conn:
            pass  # em_db auto-creates tables
    if projects:
        results["steps"].append("projects_initialized")

    return results


def _install_hooks(repo_root: str | None = None) -> int:
    """Copy hook scripts from repo to ~/.enki/hooks/.

    Returns count of hooks installed.
    """
    if not repo_root:
        # Try to find repo root relative to this file
        candidate = Path(__file__).resolve().parent.parent.parent / "scripts" / "hooks"
        if candidate.exists():
            repo_root = str(candidate.parent.parent)

    if not repo_root:
        return 0

    hooks_src = Path(repo_root) / "scripts" / "hooks"
    hooks_dst = ENKI_ROOT / "hooks"

    if not hooks_src.exists():
        return 0

    installed = 0
    for src_file in hooks_src.glob("enki-*.sh"):
        # Strip "enki-" prefix for installed name
        dst_name = src_file.name.replace("enki-", "", 1)
        dst_file = hooks_dst / dst_name
        shutil.copy2(src_file, dst_file)
        dst_file.chmod(0o755)
        installed += 1

    return installed
