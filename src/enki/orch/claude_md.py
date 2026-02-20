"""claude_md.py — CLAUDE.md generation from specs or codebase profile.

v4: Extended with preferences, tech stack, compaction behavior,
    enki_restore fallback, and User Instructions (sacred section).

Auto-generates CLAUDE.md from:
- Implementation Spec (greenfield/mid-design)
- Codebase Profile (brownfield)
- PM + customer + DBA input (full tier)
- wisdom.db preferences
- Confirmed tech stack
- Researcher conventions

Structure: WHY / WHAT / HOW / CONVENTIONS / CONSTRAINTS / COMPACTION / USER INSTRUCTIONS
"""

import re

# Project type templates for Minimal tier auto-generation
PROJECT_TYPE_REGISTRY = {
    "python_flask": {
        "build": "pip install -r requirements.txt",
        "test": "pytest tests/ -v",
        "run": "flask run",
        "lint": "ruff check .",
    },
    "python_fastapi": {
        "build": "pip install -r requirements.txt",
        "test": "pytest tests/ -v",
        "run": "uvicorn main:app --reload",
        "lint": "ruff check .",
    },
    "typescript_node": {
        "build": "npm install && npm run build",
        "test": "npm test",
        "run": "npm start",
        "lint": "npm run lint",
    },
    "react_typescript": {
        "build": "npm install && npm run build",
        "test": "npm test",
        "run": "npm run dev",
        "lint": "npm run lint",
    },
    "go_http": {
        "build": "go build ./...",
        "test": "go test ./...",
        "run": "go run main.go",
        "lint": "golangci-lint run",
    },
    "rust_actix": {
        "build": "cargo build",
        "test": "cargo test",
        "run": "cargo run",
        "lint": "cargo clippy",
    },
}


def generate_claude_md(
    project_type: str,
    project_name: str = "Project",
    impl_spec: str | None = None,
    codebase_profile: dict | None = None,
    pm_input: dict | None = None,
    customer_input: str | None = None,
) -> str:
    """Generate CLAUDE.md from available inputs.

    Args:
        project_type: greenfield, brownfield, or mid_design
        project_name: Name of the project
        impl_spec: Implementation spec text
        codebase_profile: Researcher's codebase profile dict
        pm_input: PM's WHY section input
        customer_input: Custom instructions from customer
    """
    sections = []

    # Header
    sections.append(f"# Project: {project_name}\n")

    # WHY section
    why = _build_why_section(pm_input)
    sections.append(why)

    # WHAT section
    if project_type == "brownfield" and codebase_profile:
        what = _build_what_from_profile(codebase_profile)
    elif impl_spec:
        what = _build_what_from_spec(impl_spec)
    else:
        what = "## WHAT\n\n- Tech stack: TBD\n- Structure: TBD\n"
    sections.append(what)

    # HOW section
    if project_type == "brownfield" and codebase_profile:
        how = _build_how_from_profile(codebase_profile)
    elif impl_spec:
        how = _build_how_from_spec(impl_spec)
    else:
        how = "## HOW\n\n- Build: TBD\n- Test: TBD\n- Run: TBD\n"
    sections.append(how)

    # CONVENTIONS
    if codebase_profile and "conventions" in codebase_profile:
        conv = _build_conventions_from_profile(codebase_profile["conventions"])
    else:
        conv = "## CONVENTIONS\n\n- Follow existing patterns\n"
    sections.append(conv)

    # CONSTRAINTS
    constraints = "## CONSTRAINTS\n\n- Do not modify protected infrastructure files\n"
    if customer_input:
        constraints += f"\n### Customer Instructions\n\n{customer_input}\n"
    sections.append(constraints)

    return "\n".join(sections)


def apply_tier_template(tier: str, project_type_key: str | None = None) -> str:
    """Generate minimal CLAUDE.md from project type registry.

    Used for Minimal tier auto-generation.
    """
    if not project_type_key or project_type_key not in PROJECT_TYPE_REGISTRY:
        return "# Project\n\n## HOW\n\n- Build: TBD\n- Test: TBD\n"

    tmpl = PROJECT_TYPE_REGISTRY[project_type_key]
    return (
        f"# Project\n\n"
        f"## HOW\n\n"
        f"- Build: `{tmpl['build']}`\n"
        f"- Test: `{tmpl['test']}`\n"
        f"- Run: `{tmpl['run']}`\n"
        f"- Lint: `{tmpl['lint']}`\n"
    )


def validate_claude_md(content: str) -> dict:
    """Validate CLAUDE.md best practices.

    Returns {"valid": bool, "issues": list[str]}.
    """
    issues = []
    lines = content.split("\n")

    # Under 300 lines
    if len(lines) > 300:
        issues.append(f"Too long: {len(lines)} lines (max 300)")

    # Has key sections
    content_lower = content.lower()
    for section in ["## why", "## what", "## how"]:
        if section not in content_lower:
            issues.append(f"Missing section: {section.upper()}")

    # No secrets patterns
    secret_patterns = [
        r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]+['\"]",
    ]
    for pat in secret_patterns:
        if re.search(pat, content, re.IGNORECASE):
            issues.append("Possible secret detected — remove credentials")

    # Commands should be copy-pasteable (in backticks)
    command_lines = [l for l in lines if l.strip().startswith("- ") and ":" in l]
    for cmd_line in command_lines:
        if "`" not in cmd_line and any(
            kw in cmd_line.lower()
            for kw in ["build", "test", "run", "lint", "deploy"]
        ):
            issues.append(f"Command not in backticks: {cmd_line.strip()[:60]}")

    return {"valid": len(issues) == 0, "issues": issues}


def get_project_type_registry() -> dict:
    """Return registry of project type templates."""
    return dict(PROJECT_TYPE_REGISTRY)


# ── Private builders ──


def _build_why_section(pm_input: dict | None) -> str:
    """Build WHY section from PM input."""
    if not pm_input:
        return "## WHY\n\n- Purpose: TBD\n"

    lines = ["## WHY\n"]
    if pm_input.get("outcome"):
        lines.append(f"- Purpose: {pm_input['outcome']}")
    if pm_input.get("audience"):
        lines.append(f"- Audience: {pm_input['audience']}")
    if pm_input.get("constraints"):
        lines.append(f"- Constraints: {pm_input['constraints']}")
    return "\n".join(lines) + "\n"


def _build_what_from_profile(profile: dict) -> str:
    """Build WHAT section from codebase profile."""
    lines = ["## WHAT\n"]

    proj = profile.get("project", {})
    if proj.get("primary_language"):
        lines.append(f"- Primary language: {proj['primary_language']}")
    if proj.get("frameworks"):
        lines.append(f"- Frameworks: {', '.join(proj['frameworks'])}")

    arch = profile.get("architecture", {})
    if arch.get("pattern"):
        lines.append(f"- Architecture: {arch['pattern']}")
    if arch.get("entry_point"):
        lines.append(f"- Entry point: `{arch['entry_point']}`")

    structure = profile.get("structure", {})
    if structure.get("source_dirs"):
        lines.append(f"- Source: {', '.join(structure['source_dirs'])}")

    return "\n".join(lines) + "\n"


def _build_what_from_spec(spec: str) -> str:
    """Build WHAT section from implementation spec text."""
    lines = ["## WHAT\n"]
    # Extract tech stack mentions
    if "python" in spec.lower():
        lines.append("- Language: Python")
    if "typescript" in spec.lower():
        lines.append("- Language: TypeScript")
    lines.append(f"- See Implementation Spec for full details")
    return "\n".join(lines) + "\n"


def _build_how_from_profile(profile: dict) -> str:
    """Build HOW section from codebase profile."""
    lines = ["## HOW\n"]

    testing = profile.get("testing", {})
    ci = profile.get("ci_cd", {})

    if testing.get("framework"):
        lines.append(f"- Test: `{testing['framework']}`")
    if ci.get("deploy_method"):
        lines.append(f"- Deploy: {ci['deploy_method']}")

    return "\n".join(lines) + "\n"


def _build_how_from_spec(spec: str) -> str:
    """Build HOW section from implementation spec."""
    return "## HOW\n\n- See Implementation Spec for commands\n"


def _build_conventions_from_profile(conventions: dict) -> str:
    """Build CONVENTIONS section from codebase profile conventions."""
    lines = ["## CONVENTIONS\n"]
    for key, value in conventions.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


# ── v4 Generation ──


# Compaction behavior instruction — always included
COMPACTION_INSTRUCTION = """## COMPACTION BEHAVIOR

When your context is compacted, preserve these items in your summary:
1. Current goal and phase
2. Active sprint and task status
3. Files you've created or modified this session
4. Key decisions made (with rationale)
5. What you were about to do next

If after compaction you lose working context, call `enki_restore` to recover:
- Session state (goal, phase, tier)
- Recent knowledge relevant to the current project
- Enforcement state (gates)

Do NOT ask the user to repeat information that enki_restore can provide.
"""

# User Instructions header — sacred section, Enki never modifies
USER_INSTRUCTIONS_HEADER = """## USER INSTRUCTIONS

> **Sacred section** — Enki never modifies content below this line.
> Add your own instructions, preferences, and rules here.

"""


def generate_claude_md_v4(
    project_name: str = "Project",
    project_type: str = "greenfield",
    impl_spec: str | None = None,
    codebase_profile: dict | None = None,
    pm_input: dict | None = None,
    customer_input: str | None = None,
    preferences: dict | None = None,
    tech_stack: dict | None = None,
    conventions: dict | None = None,
    user_instructions: str | None = None,
) -> str:
    """Generate CLAUDE.md with v4 sections.

    Assembles from:
    - wisdom.db preferences
    - Confirmed tech stack (projects.tech_stack)
    - Researcher conventions
    - Compaction behavior (always included)
    - enki_restore fallback (always included)
    - User Instructions (sacred — never modified by Enki)

    Args:
        project_name: Project display name.
        project_type: greenfield, brownfield, or mid_design.
        impl_spec: Implementation spec text.
        codebase_profile: Researcher's codebase profile.
        pm_input: PM's input (outcome, audience, constraints).
        customer_input: Custom instructions from customer.
        preferences: wisdom.db preferences dict.
        tech_stack: Confirmed tech stack dict.
        conventions: Researcher-discovered conventions.
        user_instructions: Existing user instructions to preserve.
    """
    sections = []

    # Header
    sections.append(f"# Project: {project_name}\n")

    # WHY section
    sections.append(_build_why_section(pm_input))

    # WHAT section — enhanced with tech stack
    what = _build_what_v4(project_type, codebase_profile, impl_spec, tech_stack)
    sections.append(what)

    # HOW section
    if project_type == "brownfield" and codebase_profile:
        sections.append(_build_how_from_profile(codebase_profile))
    elif impl_spec:
        sections.append(_build_how_from_spec(impl_spec))
    else:
        sections.append("## HOW\n\n- Build: TBD\n- Test: TBD\n- Run: TBD\n")

    # CONVENTIONS — from profile or preferences
    if conventions:
        sections.append(_build_conventions_from_profile(conventions))
    elif codebase_profile and "conventions" in codebase_profile:
        sections.append(_build_conventions_from_profile(codebase_profile["conventions"]))
    elif preferences:
        sections.append(_build_conventions_from_preferences(preferences))
    else:
        sections.append("## CONVENTIONS\n\n- Follow existing patterns\n")

    # CONSTRAINTS
    constraints = "## CONSTRAINTS\n\n- Do not modify protected infrastructure files\n"
    if customer_input:
        constraints += f"\n### Customer Instructions\n\n{customer_input}\n"
    sections.append(constraints)

    # COMPACTION — always included
    sections.append(COMPACTION_INSTRUCTION)

    # USER INSTRUCTIONS — sacred section
    if user_instructions:
        sections.append(f"## USER INSTRUCTIONS\n\n{user_instructions}\n")
    else:
        sections.append(USER_INSTRUCTIONS_HEADER)

    return "\n".join(sections)


def load_preferences_for_claude_md(project: str | None = None) -> dict:
    """Load relevant preferences from wisdom.db for CLAUDE.md generation.

    Returns dict of preference key-value pairs.
    """
    try:
        from enki.db import get_wisdom_db
        conn = get_wisdom_db()
        try:
            rows = conn.execute(
                "SELECT content, summary FROM notes "
                "WHERE category = 'preference' "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            prefs = {}
            for i, row in enumerate(rows):
                key = row["summary"] or f"pref_{i}"
                prefs[key] = row["content"][:200]
            return prefs
        finally:
            conn.close()
    except Exception:
        return {}


def preserve_user_instructions(existing_claude_md: str) -> str | None:
    """Extract User Instructions section from existing CLAUDE.md.

    This section is sacred — Enki never modifies it.
    Returns the content after the USER INSTRUCTIONS header, or None.
    """
    marker = "## USER INSTRUCTIONS"
    idx = existing_claude_md.find(marker)
    if idx < 0:
        return None

    # Get everything after the header line
    content_start = existing_claude_md.find("\n", idx)
    if content_start < 0:
        return None

    content = existing_claude_md[content_start + 1:].strip()
    # Strip the sacred section comment if present
    if content.startswith("> **Sacred section**"):
        lines = content.split("\n")
        # Skip comment lines
        while lines and lines[0].startswith(">"):
            lines.pop(0)
        content = "\n".join(lines).strip()

    return content if content else None


def _build_what_v4(
    project_type: str,
    codebase_profile: dict | None,
    impl_spec: str | None,
    tech_stack: dict | None,
) -> str:
    """Build WHAT section with v4 tech stack integration."""
    if project_type == "brownfield" and codebase_profile:
        base = _build_what_from_profile(codebase_profile)
    elif impl_spec:
        base = _build_what_from_spec(impl_spec)
    else:
        base = "## WHAT\n\n- Tech stack: TBD\n- Structure: TBD\n"

    # Append confirmed tech stack
    if tech_stack:
        import json
        lines = [base.rstrip()]
        if tech_stack.get("languages"):
            lines.append(f"- Languages: {', '.join(tech_stack['languages'])}")
        if tech_stack.get("frameworks"):
            lines.append(f"- Frameworks: {', '.join(tech_stack['frameworks'])}")
        if tech_stack.get("build_tools"):
            lines.append(f"- Build tools: {', '.join(tech_stack['build_tools'])}")
        return "\n".join(lines) + "\n"

    return base


def _build_conventions_from_preferences(preferences: dict) -> str:
    """Build CONVENTIONS section from wisdom.db preferences."""
    lines = ["## CONVENTIONS\n"]
    for key, value in preferences.items():
        lines.append(f"- {key}: {value}")
    if len(lines) == 1:
        lines.append("- Follow existing patterns")
    return "\n".join(lines) + "\n"
