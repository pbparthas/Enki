"""build_step.py — Build artifact and verification DAG step (Item 4.3).

New DAG step: after Dev completion, before QA test execution.
DevOps agent runs build commands, verifies artifacts.
Mobile projects: APK produced → HITL handoff.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Implementation Spec template section for Build & Verification
BUILD_SECTION_TEMPLATE = """
### Build & Verification

**Build commands:**
{build_commands}

**Expected artifacts:**
{artifacts}

**Verification steps:**
{verification_steps}

**Build position in DAG:** After Dev completion, before QA test execution.
"""


def create_build_task(
    project: str,
    sprint_id: str,
    build_commands: list[str],
    expected_artifacts: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> dict:
    """Create a build verification task in the DAG.

    Positioned after Dev tasks, before QA tasks.

    Args:
        project: Project ID.
        sprint_id: Sprint ID.
        build_commands: Commands to execute the build.
        expected_artifacts: Files/dirs that should exist after build.
        depends_on: Task IDs this build depends on (typically Dev tasks).

    Returns task definition dict for DAG insertion.
    """
    return {
        "name": "Build & Verify",
        "work_type": "build",
        "agent": "devops",
        "sprint_id": sprint_id,
        "build_commands": build_commands,
        "expected_artifacts": expected_artifacts or [],
        "dependencies": depends_on or [],
        "verification_commands": build_commands,
    }


def detect_build_needed(spec_text: str, files: list[str] | None = None) -> bool:
    """Heuristic: does this project need a build step?

    True if project has build config, compiled languages, or bundling.
    """
    text = spec_text.lower()

    build_indicators = [
        "build", "compile", "bundle", "webpack", "vite", "rollup",
        "docker", "dockerfile", "cargo build", "go build", "npm run build",
        "gradle", "maven", "cmake", "makefile", "apk", "artifact",
    ]

    if any(ind in text for ind in build_indicators):
        return True

    if files:
        build_files = {
            "Dockerfile", "docker-compose.yml", "Makefile",
            "webpack.config.js", "vite.config.ts", "vite.config.js",
            "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "CMakeLists.txt",
        }
        for f in files:
            if any(bf in f for bf in build_files):
                return True

    return False


def detect_mobile_project(spec_text: str, tech_stack: dict | None = None) -> bool:
    """Detect if this is a mobile project requiring APK/IPA handoff."""
    text = spec_text.lower()
    mobile_indicators = [
        "android", "ios", "react native", "flutter", "expo",
        "apk", "ipa", "mobile app", "kotlin multiplatform",
    ]
    if any(ind in text for ind in mobile_indicators):
        return True

    if tech_stack:
        frameworks = tech_stack.get("frameworks", [])
        for fw in frameworks:
            if fw.lower() in ("react-native", "flutter", "expo"):
                return True

    return False


def format_build_section(
    build_commands: list[str],
    artifacts: list[str] | None = None,
    verification_steps: list[str] | None = None,
) -> str:
    """Format a Build & Verification section for the Implementation Spec."""
    cmds = "\n".join(f"- `{c}`" for c in build_commands) if build_commands else "- TBD"
    arts = "\n".join(f"- `{a}`" for a in (artifacts or [])) or "- None specified"
    steps = "\n".join(f"- {s}" for s in (verification_steps or [])) or "- Run build commands and verify exit code 0"

    return BUILD_SECTION_TEMPLATE.format(
        build_commands=cmds,
        artifacts=arts,
        verification_steps=steps,
    )
