"""researcher.py — Codebase Profile protocol.

Read-only codebase investigation. Produces structured JSON profile.
Runs BEFORE Architect plans (brownfield). Non-negotiable.

Consumers: PM, Architect, Dev, QA, DevOps, Reviewer, User Profile.
"""

import os
from pathlib import Path


# File extensions by language
_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".swift": "swift",
}

# Framework detection patterns (file → framework)
_FRAMEWORK_SIGNALS = {
    "requirements.txt": "python",
    "setup.py": "python",
    "pyproject.toml": "python",
    "package.json": "node",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java_maven",
    "build.gradle": "java_gradle",
    "Gemfile": "ruby",
    "composer.json": "php",
}

# Test framework detection
_TEST_SIGNALS = {
    "pytest.ini": "pytest",
    "conftest.py": "pytest",
    "jest.config.js": "jest",
    "jest.config.ts": "jest",
    "vitest.config.ts": "vitest",
    ".mocharc.yml": "mocha",
    "karma.conf.js": "karma",
}

# CI detection
_CI_SIGNALS = {
    ".github/workflows": "github_actions",
    ".gitlab-ci.yml": "gitlab_ci",
    "Jenkinsfile": "jenkins",
    ".circleci": "circleci",
    ".travis.yml": "travis",
}


def get_codebase_profile_schema() -> dict:
    """Return expected JSON structure for a codebase profile."""
    return {
        "profile_version": 1,
        "project": {
            "name": "",
            "primary_language": "",
            "languages": [],
            "frameworks": [],
            "package_managers": [],
            "monorepo": False,
        },
        "structure": {
            "source_dirs": [],
            "test_dirs": [],
            "config_dir": "",
            "ci_config": "",
            "docker": False,
        },
        "conventions": {
            "naming": "",
            "import_style": "",
            "error_handling": "",
            "linter": "",
            "formatter": "",
            "test_framework": "",
            "test_pattern": "",
        },
        "architecture": {
            "pattern": "",
            "entry_point": "",
            "key_modules": [],
            "data_flow": "",
            "external_deps": [],
        },
        "testing": {
            "framework": "",
            "total_tests": 0,
            "test_dirs": [],
            "e2e_exists": False,
        },
        "ci_cd": {
            "provider": "",
            "pipelines": [],
            "deploy_method": "",
            "environments": [],
        },
        "claude_md_exists": False,
    }


def analyze_codebase(repo_path: str) -> dict:
    """Produce Codebase Profile from existing codebase.

    Read-only: never modifies files.
    Returns structured JSON profile.
    """
    root = Path(repo_path)
    if not root.exists():
        return {"error": f"Path does not exist: {repo_path}"}

    profile = get_codebase_profile_schema()
    profile["project"]["name"] = root.name

    # Detect languages
    lang_counts = _count_languages(root)
    if lang_counts:
        profile["project"]["languages"] = list(lang_counts.keys())
        profile["project"]["primary_language"] = max(
            lang_counts, key=lang_counts.get
        )

    # Detect frameworks / package managers
    for signal_file, framework in _FRAMEWORK_SIGNALS.items():
        if (root / signal_file).exists():
            profile["project"]["frameworks"].append(framework)
            if framework in ("python", "node"):
                profile["project"]["package_managers"].append(
                    "pip" if framework == "python" else "npm"
                )

    # Detect structure
    profile["structure"] = _detect_structure(root)

    # Detect testing
    profile["testing"] = _detect_testing(root)

    # Detect CI/CD
    profile["ci_cd"] = _detect_ci_cd(root)

    # Docker
    profile["structure"]["docker"] = (
        (root / "Dockerfile").exists()
        or (root / "docker-compose.yml").exists()
    )

    # CLAUDE.md
    profile["claude_md_exists"] = (root / "CLAUDE.md").exists()

    # Detect conventions (basic heuristics)
    profile["conventions"] = _detect_conventions(root, profile)

    return profile


def scope_to_request(
    profile: dict,
    customer_request: str,
) -> dict:
    """Scope profile to customer request for relevance.

    Returns profile with added `relevant_to_request` section.
    """
    request_lower = customer_request.lower()
    relevant = {
        "files_likely_touched": [],
        "existing_patterns_to_follow": "",
        "risks": "",
    }

    # Simple keyword matching against key modules
    for module in profile.get("architecture", {}).get("key_modules", []):
        purpose = module.get("purpose", "").lower()
        path = module.get("path", "")
        if any(word in purpose for word in request_lower.split()):
            relevant["files_likely_touched"].append(path)

    profile["relevant_to_request"] = relevant
    return profile


# ── Private helpers ──


def _count_languages(root: Path) -> dict[str, int]:
    """Count files by language, skipping hidden/vendor dirs."""
    counts: dict[str, int] = {}
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__",
                 "dist", "build", ".next", "target"}

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and vendor directories
        dirnames[:] = [
            d for d in dirnames
            if d not in skip_dirs and not d.startswith(".")
        ]
        for f in filenames:
            ext = Path(f).suffix
            if ext in _LANGUAGE_MAP:
                lang = _LANGUAGE_MAP[ext]
                counts[lang] = counts.get(lang, 0) + 1

    return counts


def _detect_structure(root: Path) -> dict:
    """Detect project structure."""
    structure = {
        "source_dirs": [],
        "test_dirs": [],
        "config_dir": "",
        "ci_config": "",
        "docker": False,
    }

    # Common source directories
    for src_dir in ["src", "lib", "app", "api", "server", "pkg", "cmd"]:
        if (root / src_dir).is_dir():
            structure["source_dirs"].append(f"{src_dir}/")

    # Common test directories
    for test_dir in ["tests", "test", "__tests__", "spec", "specs"]:
        if (root / test_dir).is_dir():
            structure["test_dirs"].append(f"{test_dir}/")

    # Config directory
    for config_dir in [".config", "config", ".enki"]:
        if (root / config_dir).is_dir():
            structure["config_dir"] = f"{config_dir}/"
            break

    # CI config
    for ci_path, provider in _CI_SIGNALS.items():
        if (root / ci_path).exists():
            structure["ci_config"] = ci_path
            break

    return structure


def _detect_testing(root: Path) -> dict:
    """Detect testing setup."""
    testing = {
        "framework": "",
        "total_tests": 0,
        "test_dirs": [],
        "e2e_exists": False,
    }

    for signal_file, framework in _TEST_SIGNALS.items():
        if (root / signal_file).exists():
            testing["framework"] = framework
            break

    # Find test directories
    for test_dir in ["tests", "test", "__tests__", "spec"]:
        if (root / test_dir).is_dir():
            testing["test_dirs"].append(f"{test_dir}/")

    # E2E detection
    for e2e_dir in ["e2e", "cypress", "playwright"]:
        if (root / e2e_dir).is_dir():
            testing["e2e_exists"] = True
            break

    return testing


def _detect_ci_cd(root: Path) -> dict:
    """Detect CI/CD setup."""
    ci_cd = {
        "provider": "",
        "pipelines": [],
        "deploy_method": "",
        "environments": [],
    }

    for ci_path, provider in _CI_SIGNALS.items():
        p = root / ci_path
        if p.exists():
            ci_cd["provider"] = provider
            if p.is_dir():
                ci_cd["pipelines"] = [
                    f.name for f in p.iterdir()
                    if f.suffix in (".yml", ".yaml")
                ]
            else:
                ci_cd["pipelines"] = [ci_path]
            break

    return ci_cd


def _detect_conventions(root: Path, profile: dict) -> dict:
    """Detect coding conventions (basic heuristics)."""
    conventions = {
        "naming": "",
        "import_style": "",
        "error_handling": "",
        "linter": "",
        "formatter": "",
        "test_framework": profile.get("testing", {}).get("framework", ""),
        "test_pattern": "",
    }

    # Linter detection
    linter_files = {
        ".eslintrc.js": "eslint",
        ".eslintrc.json": "eslint",
        ".eslintrc.yml": "eslint",
        "ruff.toml": "ruff",
        ".flake8": "flake8",
        ".pylintrc": "pylint",
    }
    for f, linter in linter_files.items():
        if (root / f).exists():
            conventions["linter"] = linter
            break

    # Check pyproject.toml for ruff
    pyproject = root / "pyproject.toml"
    if pyproject.exists() and not conventions["linter"]:
        try:
            content = pyproject.read_text()
            if "[tool.ruff]" in content:
                conventions["linter"] = "ruff"
        except Exception:
            pass

    # Formatter detection
    formatter_files = {
        ".prettierrc": "prettier",
        ".prettierrc.json": "prettier",
        "prettier.config.js": "prettier",
    }
    for f, fmt in formatter_files.items():
        if (root / f).exists():
            conventions["formatter"] = fmt
            break

    return conventions
