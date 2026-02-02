"""Enforcement logic - tier detection and gate checks."""

import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .session import (
    Phase, Tier, TIERS,
    get_phase, get_tier, get_goal, get_session_edits,
    has_approved_spec, get_scope_files, tier_rank,
)

# File extensions considered implementation files
IMPL_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".rb", ".swift", ".kt",
    ".c", ".cpp", ".h", ".hpp", ".cs",
}

@dataclass
class GateResult:
    """Result of a gate check."""
    allowed: bool
    gate: Optional[str] = None  # Which gate blocked
    reason: Optional[str] = None


def is_impl_file(file_path: str) -> bool:
    """Check if a file is an implementation file."""
    path = Path(file_path)
    return path.suffix.lower() in IMPL_EXTENSIONS


def is_test_file(file_path: str) -> bool:
    """Check if a file is a test file."""
    name = Path(file_path).name.lower()
    return any([
        name.startswith("test_"),
        name.endswith("_test.py"),
        name.endswith(".test.ts"),
        name.endswith(".test.js"),
        name.endswith(".spec.ts"),
        name.endswith(".spec.js"),
        "tests/" in file_path.lower(),
        "test/" in file_path.lower(),
        "__tests__/" in file_path.lower(),
    ])


def is_enki_file(file_path: str) -> bool:
    """Check if a file is in .enki directory."""
    return ".enki/" in file_path or file_path.startswith(".enki/")


def count_lines_changed(file_path: str, project_path: Optional[Path] = None) -> int:
    """Count lines changed in a file using git diff."""
    cwd = project_path or Path.cwd()

    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", file_path],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )

        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                added = int(parts[0]) if parts[0] != "-" else 0
                deleted = int(parts[1]) if parts[1] != "-" else 0
                return added + deleted
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
        pass

    return 0


def detect_tier(
    session_edits: Optional[list[str]] = None,
    goal: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> Tier:
    """Detect tier based on objective metrics.

    Args:
        session_edits: List of files edited (or fetch from session)
        goal: Session goal (or fetch from session)
        project_path: Project path

    Returns:
        Detected tier
    """
    if session_edits is None:
        session_edits = get_session_edits(project_path)

    if goal is None:
        goal = get_goal(project_path) or ""

    # Count files and lines
    files_edited = len(session_edits)
    lines_changed = sum(count_lines_changed(f, project_path) for f in session_edits)

    # Objective tier detection
    if files_edited >= 10:
        return "major"
    elif files_edited >= 3 or lines_changed >= 50:
        return "feature"
    elif files_edited >= 1:
        return "quick_fix"
    else:
        return "trivial"


def find_test_file(impl_file: str, project_path: Optional[Path] = None) -> Optional[str]:
    """Find the test file for an implementation file."""
    path = Path(impl_file)
    cwd = project_path or Path.cwd()

    # Common test file patterns
    patterns = [
        f"tests/test_{path.stem}{path.suffix}",
        f"tests/{path.stem}_test{path.suffix}",
        f"test/test_{path.stem}{path.suffix}",
        f"test/{path.stem}_test{path.suffix}",
        f"{path.parent}/tests/test_{path.stem}{path.suffix}",
        f"{path.parent}/test_{path.stem}{path.suffix}",
        f"__tests__/{path.stem}.test{path.suffix}",
    ]

    # For TypeScript/JavaScript
    if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
        patterns.extend([
            f"{path.parent}/{path.stem}.test{path.suffix}",
            f"{path.parent}/{path.stem}.spec{path.suffix}",
            f"__tests__/{path.stem}.test{path.suffix}",
        ])

    for pattern in patterns:
        test_path = cwd / pattern
        if test_path.exists():
            return str(test_path)

    return None


def check_gate_1_phase(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 1: Phase check for Edit/Write on implementation files.

    Implementation files require IMPLEMENT phase.
    """
    # Only check Edit/Write
    if tool not in {"Edit", "Write", "MultiEdit"}:
        return GateResult(allowed=True)

    # Allow .enki files always
    if is_enki_file(file_path):
        return GateResult(allowed=True)

    # Allow non-implementation files
    if not is_impl_file(file_path):
        return GateResult(allowed=True)

    # Allow test files
    if is_test_file(file_path):
        return GateResult(allowed=True)

    # Check phase
    phase = get_phase(project_path)
    if phase != "implement":
        return GateResult(
            allowed=False,
            gate="phase",
            reason=(
                f"GATE 1: Phase Violation\n\n"
                f"Cannot edit implementation files in '{phase}' phase.\n\n"
                f"Current phase: {phase}\n"
                f"Required phase: implement\n\n"
                f"Complete the current phase first."
            ),
        )

    return GateResult(allowed=True)


def check_gate_2_spec(
    tool: str,
    agent_type: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 2: Spec approval check for implementation agents.

    Implementation agents require an approved spec.
    """
    # Only check Task tool
    if tool != "Task":
        return GateResult(allowed=True)

    # Research agents allowed without spec
    research_agents = {"Explore", "Plan"}
    if agent_type in research_agents:
        return GateResult(allowed=True)

    # Check for approved spec
    if not has_approved_spec(project_path):
        return GateResult(
            allowed=False,
            gate="spec",
            reason=(
                "GATE 2: No Approved Spec\n\n"
                "Cannot spawn implementation agents without an approved spec.\n\n"
                "Steps:\n"
                "1. Run /debate to analyze from all perspectives\n"
                "2. Run /plan to create specification\n"
                "3. Get user approval\n"
                "4. Then spawn agents"
            ),
        )

    return GateResult(allowed=True)


def check_gate_3_tdd(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 3: TDD enforcement.

    Implementation files should have corresponding test files.
    """
    # Only check Edit/Write in implement phase
    if tool not in {"Edit", "Write", "MultiEdit"}:
        return GateResult(allowed=True)

    phase = get_phase(project_path)
    if phase != "implement":
        return GateResult(allowed=True)

    # Allow test files
    if is_test_file(file_path):
        return GateResult(allowed=True)

    # Allow non-implementation files
    if not is_impl_file(file_path):
        return GateResult(allowed=True)

    # Allow .enki files
    if is_enki_file(file_path):
        return GateResult(allowed=True)

    # Check for test file
    test_file = find_test_file(file_path, project_path)
    tier = get_tier(project_path)

    if test_file is None:
        # All tiers require tests
        return GateResult(
            allowed=False,
            gate="tdd",
            reason=(
                f"GATE 3: TDD Required\n\n"
                f"No test file found for: {file_path}\n\n"
                f"Tests are required before implementation.\n\n"
                f"Create a test file first, then implement."
            ),
        )

    return GateResult(allowed=True)


def check_gate_4_scope(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 4: Scope guard during orchestration.

    During orchestration, edits must be to files in scope.
    """
    # Only check Edit/Write
    if tool not in {"Edit", "Write", "MultiEdit"}:
        return GateResult(allowed=True)

    # Allow .enki files
    if is_enki_file(file_path):
        return GateResult(allowed=True)

    # Get scope files
    scope_files = get_scope_files(project_path)
    phase = get_phase(project_path)

    # If orchestrating without scope, block
    if not scope_files:
        if phase == "implement":
            return GateResult(
                allowed=False,
                gate="scope",
                reason=(
                    "GATE 4: No Scope Defined\n\n"
                    "Implementation phase requires defined scope.\n\n"
                    "Run /plan to define scope before implementing."
                ),
            )
        return GateResult(allowed=True)

    # Check if file is in scope
    # Normalize paths for comparison
    file_path_normalized = str(Path(file_path).resolve())
    cwd = project_path or Path.cwd()

    for scope_file in scope_files:
        scope_path = str((cwd / scope_file).resolve())
        if file_path_normalized == scope_path:
            return GateResult(allowed=True)
        # Also check relative path
        if file_path == scope_file:
            return GateResult(allowed=True)

    return GateResult(
        allowed=False,
        gate="scope",
        reason=(
            f"GATE 4: Out of Scope\n\n"
            f"File not in orchestration scope: {file_path}\n\n"
            f"Allowed files:\n"
            + "\n".join(f"  - {f}" for f in scope_files[:10])
            + ("\n  ..." if len(scope_files) > 10 else "")
        ),
    )


def check_all_gates(
    tool: str,
    file_path: Optional[str] = None,
    agent_type: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Check all applicable gates.

    Returns first failure, or success if all pass.
    """
    # Gate 1: Phase
    if file_path:
        result = check_gate_1_phase(tool, file_path, project_path)
        if not result.allowed:
            return result

    # Gate 2: Spec
    result = check_gate_2_spec(tool, agent_type, project_path)
    if not result.allowed:
        return result

    # Gate 3: TDD
    if file_path:
        result = check_gate_3_tdd(tool, file_path, project_path)
        if not result.allowed:
            return result

    # Gate 4: Scope
    if file_path:
        result = check_gate_4_scope(tool, file_path, project_path)
        if not result.allowed:
            return result

    return GateResult(allowed=True)
