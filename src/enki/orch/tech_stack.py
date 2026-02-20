"""tech_stack.py — Tech stack discovery, confirmation, and constraint enforcement.

Brownfield: Researcher scans codebase → Discovered Tech Stack → HITL confirms.
Greenfield: Architect proposes → HITL approves → stored.

Stored in projects.tech_stack (JSON field in wisdom.db).
Architect receives tech stack as input constraint, not a decision to make.
Deviations from confirmed stack are blockers requiring negotiation.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Common framework/library detection patterns
_DETECTION_PATTERNS = {
    "python": {
        "indicators": ["*.py", "setup.py", "pyproject.toml", "requirements.txt", "Pipfile"],
        "frameworks": {
            "flask": ["flask", "Flask"],
            "django": ["django", "Django", "manage.py"],
            "fastapi": ["fastapi", "FastAPI"],
            "pytest": ["pytest", "conftest.py"],
        },
    },
    "javascript": {
        "indicators": ["package.json", "*.js", "*.mjs"],
        "frameworks": {
            "react": ["react", "React", "jsx", "tsx"],
            "vue": ["vue", "Vue", ".vue"],
            "express": ["express", "Express"],
            "next": ["next", "Next", "next.config"],
        },
    },
    "typescript": {
        "indicators": ["tsconfig.json", "*.ts", "*.tsx"],
        "frameworks": {},
    },
    "go": {
        "indicators": ["go.mod", "go.sum", "*.go"],
        "frameworks": {},
    },
    "rust": {
        "indicators": ["Cargo.toml", "*.rs"],
        "frameworks": {},
    },
    "java": {
        "indicators": ["pom.xml", "build.gradle", "*.java"],
        "frameworks": {
            "spring": ["springframework", "SpringBoot"],
        },
    },
}


def scan_tech_stack(project_path: str) -> dict:
    """Scan a codebase to discover tech stack.

    Returns structured tech stack dict suitable for storage.
    """
    path = Path(project_path)
    if not path.exists():
        return {"error": "Path does not exist", "languages": [], "frameworks": []}

    languages = []
    frameworks = []
    build_tools = []
    detected_files = []

    # Scan for language indicators
    for lang, config in _DETECTION_PATTERNS.items():
        for indicator in config["indicators"]:
            if indicator.startswith("*"):
                # Glob pattern
                matches = list(path.glob(indicator)) + list(path.glob(f"**/{indicator}"))
                if matches:
                    if lang not in languages:
                        languages.append(lang)
                    break
            else:
                # Exact file
                if (path / indicator).exists():
                    if lang not in languages:
                        languages.append(lang)
                    detected_files.append(indicator)
                    break

    # Scan for framework indicators (check common files)
    _scan_frameworks(path, languages, frameworks)

    # Detect build tools
    _detect_build_tools(path, build_tools, detected_files)

    return {
        "languages": languages,
        "frameworks": frameworks,
        "build_tools": build_tools,
        "detected_files": detected_files,
        "primary_language": languages[0] if languages else None,
    }


def store_tech_stack(project: str, tech_stack: dict) -> bool:
    """Store confirmed tech stack in projects table.

    Returns True if stored successfully.
    """
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        stack_json = json.dumps(tech_stack)
        cursor = conn.execute(
            "UPDATE projects SET tech_stack = ? WHERE name = ?",
            (stack_json, project),
        )
        if cursor.rowcount == 0:
            # Project doesn't exist yet, create it
            conn.execute(
                "INSERT INTO projects (name, tech_stack) VALUES (?, ?)",
                (project, stack_json),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def get_tech_stack(project: str) -> Optional[dict]:
    """Retrieve confirmed tech stack for a project.

    Returns None if no tech stack stored.
    """
    from enki.db import get_wisdom_db

    conn = get_wisdom_db()
    try:
        row = conn.execute(
            "SELECT tech_stack FROM projects WHERE name = ?",
            (project,),
        ).fetchone()
        if row and row["tech_stack"]:
            return json.loads(row["tech_stack"])
        return None
    finally:
        conn.close()


def check_deviation(
    proposed_tech: list[str],
    confirmed_stack: dict,
) -> list[dict]:
    """Check if proposed technologies deviate from confirmed stack.

    Returns list of deviations that require negotiation.
    """
    if not confirmed_stack:
        return []

    confirmed_langs = set(confirmed_stack.get("languages", []))
    confirmed_frameworks = set(confirmed_stack.get("frameworks", []))
    all_confirmed = confirmed_langs | confirmed_frameworks

    deviations = []
    for tech in proposed_tech:
        tech_lower = tech.lower()
        if not any(tech_lower in c.lower() for c in all_confirmed):
            deviations.append({
                "proposed": tech,
                "status": "deviation",
                "message": f"'{tech}' not in confirmed stack. Requires negotiation.",
            })

    return deviations


def _scan_frameworks(path: Path, languages: list, frameworks: list):
    """Scan for framework indicators in common config files."""
    # Check package.json for JS frameworks
    pkg_json = path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            deps = {}
            deps.update(pkg.get("dependencies", {}))
            deps.update(pkg.get("devDependencies", {}))
            for fw_name in ("react", "vue", "angular", "express", "next",
                            "svelte", "nuxt", "fastify"):
                if fw_name in deps:
                    frameworks.append(fw_name)
        except (json.JSONDecodeError, OSError):
            pass

    # Check pyproject.toml / setup.cfg for Python frameworks
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            for fw_name in ("flask", "django", "fastapi", "starlette", "pytest"):
                if fw_name in content.lower():
                    frameworks.append(fw_name)
        except OSError:
            pass

    # Check requirements.txt
    reqs = path / "requirements.txt"
    if reqs.exists():
        try:
            content = reqs.read_text().lower()
            for fw_name in ("flask", "django", "fastapi", "pytest"):
                if fw_name in content:
                    if fw_name not in frameworks:
                        frameworks.append(fw_name)
        except OSError:
            pass


def _detect_build_tools(path: Path, build_tools: list, detected_files: list):
    """Detect build tools from config files."""
    tool_map = {
        "Makefile": "make",
        "Dockerfile": "docker",
        "docker-compose.yml": "docker-compose",
        "docker-compose.yaml": "docker-compose",
        ".github/workflows": "github-actions",
        "Jenkinsfile": "jenkins",
        "webpack.config.js": "webpack",
        "vite.config.ts": "vite",
        "vite.config.js": "vite",
        "rollup.config.js": "rollup",
    }
    for file_name, tool_name in tool_map.items():
        if (path / file_name).exists():
            build_tools.append(tool_name)
            detected_files.append(file_name)
