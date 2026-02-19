"""devops.py — DevOps agent: CI, deploy, verify per user configuration.

Reads deploy config from .enki/deploy.yaml.
Spawned at complete phase (qualify/deploy/verify) or spec phase (CI setup).
"""

from pathlib import Path

from enki.config import get_config


# Default deploy config when no deploy.yaml exists
DEFAULT_DEPLOY_CONFIG = {
    "method": "git_push",
    "pipeline": None,
    "environments": {
        "staging": {
            "url": None,
            "health_check": "/health",
            "run_smoke_tests": False,
        },
    },
    "pre_deploy": None,
    "post_deploy": None,
    "rollback_method": "git_revert",
}


def read_deploy_config(project_path: str | None = None) -> dict:
    """Read deploy config from .enki/deploy.yaml or return defaults.

    Checks project-local .enki/deploy.yaml first, then global.
    """
    search_paths = []
    if project_path:
        search_paths.append(Path(project_path) / ".enki" / "deploy.yaml")

    from enki.db import ENKI_ROOT
    search_paths.append(ENKI_ROOT / "config" / "deploy.yaml")

    for path in search_paths:
        if path.exists():
            try:
                import yaml  # type: ignore[import-untyped]
                with open(path) as f:
                    config = yaml.safe_load(f) or {}
                # Merge with defaults
                merged = dict(DEFAULT_DEPLOY_CONFIG)
                merged.update(config)
                return merged
            except ImportError:
                # yaml not available, return defaults
                return dict(DEFAULT_DEPLOY_CONFIG)
            except Exception:
                return dict(DEFAULT_DEPLOY_CONFIG)

    return dict(DEFAULT_DEPLOY_CONFIG)


def run_ci(project_path: str, config: dict | None = None) -> dict:
    """Execute CI checks (lint, test, build).

    Returns structured result. Does NOT execute commands —
    returns what SHOULD be executed (agent prompt material).
    """
    if not config:
        config = read_deploy_config(project_path)

    steps = []

    # Pre-deploy checks
    if config.get("pre_deploy"):
        steps.append({
            "step": "pre_deploy",
            "command": config["pre_deploy"],
            "required": True,
        })

    # Standard CI steps
    steps.append({
        "step": "lint",
        "command": _detect_lint_command(project_path),
        "required": True,
    })
    steps.append({
        "step": "test",
        "command": _detect_test_command(project_path),
        "required": True,
    })
    steps.append({
        "step": "build",
        "command": _detect_build_command(project_path),
        "required": True,
    })

    return {
        "project": project_path,
        "steps": steps,
        "pipeline": config.get("pipeline"),
    }


def deploy_plan(
    project_path: str,
    environment: str = "staging",
    config: dict | None = None,
) -> dict:
    """Generate deployment plan (what to execute).

    Does NOT execute — returns structured plan for agent.
    """
    if not config:
        config = read_deploy_config(project_path)

    env_config = config.get("environments", {}).get(environment, {})

    plan = {
        "environment": environment,
        "method": config.get("method", "git_push"),
        "rollback_method": config.get("rollback_method", "git_revert"),
        "steps": [],
    }

    # Pre-deploy
    if config.get("pre_deploy"):
        plan["steps"].append({
            "step": "pre_deploy",
            "command": config["pre_deploy"],
        })

    # Deploy step depends on method
    method = config.get("method", "git_push")
    if method == "git_push":
        plan["steps"].append({
            "step": "deploy",
            "command": f"git push origin main",
        })
    elif method == "docker_ecr":
        plan["steps"].append({
            "step": "deploy",
            "command": "docker build && docker push",
        })
    else:
        plan["steps"].append({
            "step": "deploy",
            "command": f"# Custom: {method}",
        })

    # Health check
    if env_config.get("health_check"):
        url = env_config.get("url", "http://localhost")
        plan["steps"].append({
            "step": "health_check",
            "url": f"{url}{env_config['health_check']}",
        })

    # Post-deploy
    if config.get("post_deploy"):
        plan["steps"].append({
            "step": "post_deploy",
            "command": config["post_deploy"],
        })

    return plan


def verify_plan(project_path: str, environment: str = "staging") -> dict:
    """Generate verification plan for post-deploy checks."""
    config = read_deploy_config(project_path)
    env_config = config.get("environments", {}).get(environment, {})

    checks = []

    if env_config.get("health_check"):
        url = env_config.get("url", "http://localhost")
        checks.append({
            "check": "health",
            "url": f"{url}{env_config['health_check']}",
        })

    if env_config.get("run_smoke_tests"):
        checks.append({
            "check": "smoke_tests",
            "command": config.get("post_deploy", "echo 'no smoke tests configured'"),
        })

    return {
        "environment": environment,
        "checks": checks,
        "rollback_available": True,
        "rollback_method": config.get("rollback_method", "git_revert"),
    }


def rollback_plan(project_path: str, environment: str = "staging") -> dict:
    """Generate rollback plan."""
    config = read_deploy_config(project_path)
    method = config.get("rollback_method", "git_revert")

    return {
        "environment": environment,
        "method": method,
        "steps": [{"step": "rollback", "command": f"# Rollback via {method}"}],
    }


# ── Private helpers ──


def _detect_lint_command(project_path: str) -> str:
    """Detect lint command from project files."""
    p = Path(project_path)
    if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
        return "ruff check ."
    if (p / "package.json").exists():
        return "npm run lint"
    if (p / "go.mod").exists():
        return "golangci-lint run"
    return "echo 'no linter detected'"


def _detect_test_command(project_path: str) -> str:
    """Detect test command from project files."""
    p = Path(project_path)
    if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
        return "pytest tests/ -v"
    if (p / "package.json").exists():
        return "npm test"
    if (p / "go.mod").exists():
        return "go test ./..."
    return "echo 'no test framework detected'"


def _detect_build_command(project_path: str) -> str:
    """Detect build command from project files."""
    p = Path(project_path)
    if (p / "package.json").exists():
        return "npm run build"
    if (p / "Cargo.toml").exists():
        return "cargo build"
    if (p / "go.mod").exists():
        return "go build ./..."
    return "echo 'no build step needed'"
