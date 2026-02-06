"""Enforcement logic - tier detection and gate checks."""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from .session import (
    Phase, Tier, TIERS,
    get_phase, get_tier, get_goal, get_session_edits,
    has_approved_spec, get_scope_files, tier_rank,
)

# Enforcement integrity: These paths must NEVER be writable by any agent.
# Changes to enforcement infrastructure require human-approved,
# externally-reviewed modifications only.
PROTECTED_PATHS = [
    "src/enki/",
    ".enki/",
    ".claude/hooks/",
    "scripts/hooks/",
    "enki-pre-tool-use",
    "enki-post-tool-use",
    "enki-session-start",
    "enki-pre-compact",
    "enki-post-compact",
    "patterns.json",
    "enforcement",
    "ereshkigal",
    "evolution",
]

# P3-07: Agents that bypass spec requirement (research-only, no edits)
RESEARCH_AGENTS = {"Explore", "Plan"}

# File extensions considered implementation files
IMPL_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".rb", ".swift", ".kt",
    ".c", ".cpp", ".h", ".hpp", ".cs",
}

from .enforcement_types import EnforcementDecision

@dataclass
class GateResult:
    """Result of a gate check."""
    allowed: bool
    gate: Optional[str] = None  # Which gate blocked
    reason: Optional[str] = None

    def to_decision(self) -> EnforcementDecision:
        """Convert to shared EnforcementDecision (P2-16)."""
        return EnforcementDecision(
            allowed=self.allowed, source="gate",
            gate=self.gate, reason=self.reason,
        )


def _is_protected_path(file_path: str) -> Optional[str]:
    """Check if file_path matches any protected path using path-component matching.

    P1-15: Uses path components instead of substring matching to avoid
    false positives (e.g., 'my_evolutionary_algorithm.py' matching 'evolution').

    Directory patterns (ending '/') match when they appear as contiguous
    path segments. File patterns match when a path component's name or
    stem equals the pattern.

    Returns:
        The matched protection rule string, or None if not protected.
    """
    path = Path(file_path)
    parts_lower = tuple(p.lower() for p in path.parts)

    for protected in PROTECTED_PATHS:
        if protected.endswith('/'):
            # Directory: contiguous path-component match
            dir_parts = tuple(p.lower() for p in Path(protected.rstrip('/')).parts)
            for i in range(len(parts_lower) - len(dir_parts) + 1):
                if parts_lower[i:i + len(dir_parts)] == dir_parts:
                    return protected
        else:
            # File name/stem: exact component match
            protected_lower = protected.lower()
            for part in parts_lower:
                if part == protected_lower or Path(part).stem == protected_lower:
                    return protected
    return None


def is_impl_file(file_path: str) -> bool:
    """Check if a file is an implementation file."""
    path = Path(file_path)
    return path.suffix.lower() in IMPL_EXTENSIONS


# P3-05: Test file detection patterns — configurable at module level
TEST_FILE_PREFIXES = ("test_",)
TEST_FILE_SUFFIXES = ("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
TEST_DIR_MARKERS = ("tests/", "test/", "__tests__/")


def is_test_file(file_path: str) -> bool:
    """Check if a file is a test file."""
    name = Path(file_path).name.lower()
    path_lower = file_path.lower()
    return (
        any(name.startswith(p) for p in TEST_FILE_PREFIXES)
        or any(name.endswith(s) for s in TEST_FILE_SUFFIXES)
        or any(d in path_lower for d in TEST_DIR_MARKERS)
    )


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
        # P2-05: Fail-closed — return high number on error, not 0
        return 999

    return 0


# Patterns that indicate Bash is modifying files
_BASH_FILE_MODIFY_PATTERNS = [
    # Redirects: echo/cat/printf > file, >> file
    r'(?:>>?)\s*(\S+)',
    # tee
    r'\btee\s+(?:-a\s+)?(\S+)',
    # sed -i
    r'\bsed\s+-i[^\s]*\s+.*?(\S+)\s*$',
    # cp/mv/install target (last arg)
    r'\b(?:cp|mv|install)\s+.*\s+(\S+)\s*$',
    # rm
    r'\brm\s+(?:-[rf]+\s+)*(\S+)',
    # dd of=
    r'\bdd\b.*\bof=(\S+)',
]

_BASH_FILE_MODIFY_RE = [re.compile(p) for p in _BASH_FILE_MODIFY_PATTERNS]


def _extract_bash_target(command: str) -> Optional[str]:
    """Extract the target file from a file-modifying Bash command.

    Returns the file path if the command modifies files, None otherwise.
    """
    if not command:
        return None

    for pattern in _BASH_FILE_MODIFY_RE:
        match = pattern.search(command)
        if match:
            target = match.group(1)
            # Strip quotes
            target = target.strip("'\"")
            # Skip obvious non-file targets
            if target.startswith("-") or target in {"/dev/null", "-"}:
                continue
            return target

    return None


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


def check_enforcement_integrity(
    tool: str,
    file_path: str,
    agent_type: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Enforcement Integrity Gate: No agent may write to enforcement infrastructure.

    Agents must not have the ability to modify their own guardrails,
    gates, hooks, patterns, or enforcement logic.
    Ref: Ereshkigal (Part 12) — no escape hatch, no appeal.

    Fail-closed: returns allowed=False unless an explicit allow condition is met.
    """
    try:
        # Only intercept file-modifying tools (including Bash — checked by caller)
        if tool not in {"Edit", "Write", "MultiEdit", "Bash"}:
            return GateResult(allowed=True, gate="enforcement_integrity")

        # Identity must be explicitly provided — missing identity defaults to most restricted
        if not agent_type:
            # No agent_type = treat as agent (most restrictive), not human
            agent_type = "__unknown_agent__"

        matched = _is_protected_path(file_path)
        if matched:
            return GateResult(
                allowed=False,
                gate="enforcement_integrity",
                reason=(
                    f"ENFORCEMENT INTEGRITY: Protected Path\n\n"
                    f"Agent '{agent_type}' cannot write to: {file_path}\n\n"
                    f"Matched protection rule: {matched}\n"
                    f"Agents cannot modify enforcement logic.\n\n"
                    f"Ref: Ereshkigal — no escape hatch, no appeal."
                ),
            )

        # Explicit allow: file is not protected, agent can write
        return GateResult(allowed=True, gate="enforcement_integrity")
    except Exception as e:
        logger.error("Enforcement integrity gate error — fail closed: %s", e)
        return GateResult(allowed=False, gate="enforcement_integrity",
                          reason=f"Gate error — fail closed: {e}")


def check_gate_1_phase(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 1: Phase check for Edit/Write on implementation files.

    Implementation files require IMPLEMENT phase.
    Fail-closed: returns allowed=False unless an explicit allow condition is met.
    """
    try:
        # Only check file-modifying tools (including Bash — checked by caller)
        if tool not in {"Edit", "Write", "MultiEdit", "Bash"}:
            return GateResult(allowed=True, gate="phase")

        # Allow .enki files always
        if is_enki_file(file_path):
            return GateResult(allowed=True, gate="phase")

        # Allow non-implementation files
        if not is_impl_file(file_path):
            return GateResult(allowed=True, gate="phase")

        # Allow test files
        if is_test_file(file_path):
            return GateResult(allowed=True, gate="phase")

        # Check phase — only implement phase allows impl file edits
        phase = get_phase(project_path)
        if phase == "implement":
            return GateResult(allowed=True, gate="phase")

        # Fail-closed: not in implement phase
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
    except Exception as e:
        logger.error("Gate 1 (phase) error — fail closed: %s", e)
        return GateResult(allowed=False, gate="phase",
                          reason=f"Gate error — fail closed: {e}")


def check_gate_2_spec(
    tool: str,
    agent_type: Optional[str] = None,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 2: Spec approval check for implementation agents.

    Implementation agents require an approved spec.
    Fail-closed: returns allowed=False unless an explicit allow condition is met.
    """
    try:
        # Only check Task tool
        if tool != "Task":
            return GateResult(allowed=True, gate="spec")

        # Identity must be explicitly provided — missing identity defaults to most restricted
        # None/missing agent_type is NOT treated as human — it's treated as agent
        if agent_type in RESEARCH_AGENTS:
            return GateResult(allowed=True, gate="spec")

        # Check for approved spec — explicit positive verification
        if has_approved_spec(project_path):
            return GateResult(allowed=True, gate="spec")

        # Fail-closed: no approved spec
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
    except Exception as e:
        logger.error("Gate 2 (spec) error — fail closed: %s", e)
        return GateResult(allowed=False, gate="spec",
                          reason=f"Gate error — fail closed: {e}")


def check_gate_3_tdd(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 3: TDD enforcement.

    Implementation files should have corresponding test files.
    Fail-closed: returns allowed=False unless an explicit allow condition is met.
    """
    try:
        # Only check file-modifying tools (including Bash — checked by caller)
        if tool not in {"Edit", "Write", "MultiEdit", "Bash"}:
            return GateResult(allowed=True, gate="tdd")

        # P2-04: TDD enforcement independent of phase — always check for tests
        # Allow test files
        if is_test_file(file_path):
            return GateResult(allowed=True, gate="tdd")

        # Allow non-implementation files
        if not is_impl_file(file_path):
            return GateResult(allowed=True, gate="tdd")

        # Allow .enki files
        if is_enki_file(file_path):
            return GateResult(allowed=True, gate="tdd")

        # Check for test file — explicit positive verification
        test_file = find_test_file(file_path, project_path)

        if test_file is not None:
            return GateResult(allowed=True, gate="tdd")

        # Fail-closed: no test file found
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
    except Exception as e:
        logger.error("Gate 3 (tdd) error — fail closed: %s", e)
        return GateResult(allowed=False, gate="tdd",
                          reason=f"Gate error — fail closed: {e}")


def check_gate_4_scope(
    tool: str,
    file_path: str,
    project_path: Optional[Path] = None,
) -> GateResult:
    """Gate 4: Scope guard during orchestration.

    During orchestration, edits must be to files in scope.
    Fail-closed: returns allowed=False unless an explicit allow condition is met.
    """
    try:
        # Only check file-modifying tools (including Bash — checked by caller)
        if tool not in {"Edit", "Write", "MultiEdit", "Bash"}:
            return GateResult(allowed=True, gate="scope")

        # Allow .enki files
        if is_enki_file(file_path):
            return GateResult(allowed=True, gate="scope")

        # Get scope files
        scope_files = get_scope_files(project_path)
        phase = get_phase(project_path)

        # If no scope defined and not in implement phase, allow
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
            return GateResult(allowed=True, gate="scope")

        # Check if file is in scope — explicit positive verification
        file_path_normalized = str(Path(file_path).resolve())
        cwd = project_path or Path.cwd()

        for scope_file in scope_files:
            scope_path = str((cwd / scope_file).resolve())
            if file_path_normalized == scope_path:
                return GateResult(allowed=True, gate="scope")
            # Also check relative path
            if file_path == scope_file:
                return GateResult(allowed=True, gate="scope")

        # Fail-closed: file not in scope
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
    except Exception as e:
        logger.error("Gate 4 (scope) error — fail closed: %s", e)
        return GateResult(allowed=False, gate="scope",
                          reason=f"Gate error — fail closed: {e}")


# =============================================================================
# GATE REGISTRY — P2-08: Open/Closed Principle
# =============================================================================
# New gates "plug in" without modifying check_all_gates().
# Each entry: (name, check_fn, needs_file_path)
# Gates are checked in registration order. First failure short-circuits.

@dataclass
class _GateEntry:
    """A registered gate check."""
    name: str
    check_fn: object  # Callable — typed loosely to avoid forward-ref issues
    needs_file_path: bool = True  # Skip if no file_path provided

_GATE_REGISTRY: list[_GateEntry] = []


def register_gate(name: str, check_fn, needs_file_path: bool = True) -> None:
    """Register a gate check function.

    Args:
        name: Gate name for logging/audit
        check_fn: Function(tool, file_path, agent_type, project_path) -> GateResult
        needs_file_path: If True, gate is skipped when no file_path is present
    """
    _GATE_REGISTRY.append(_GateEntry(name, check_fn, needs_file_path))


def _gate_enforcement_integrity(tool, file_path, agent_type, project_path):
    return check_enforcement_integrity(tool, file_path, agent_type, project_path)

def _gate_phase(tool, file_path, agent_type, project_path):
    return check_gate_1_phase(tool, file_path, project_path)

def _gate_spec(tool, file_path, agent_type, project_path):
    return check_gate_2_spec(tool, agent_type, project_path)

def _gate_tdd(tool, file_path, agent_type, project_path):
    return check_gate_3_tdd(tool, file_path, project_path)

def _gate_scope(tool, file_path, agent_type, project_path):
    return check_gate_4_scope(tool, file_path, project_path)


# Register built-in gates in order of priority
register_gate("enforcement_integrity", _gate_enforcement_integrity, needs_file_path=True)
register_gate("phase", _gate_phase, needs_file_path=True)
register_gate("spec", _gate_spec, needs_file_path=False)
register_gate("tdd", _gate_tdd, needs_file_path=True)
register_gate("scope", _gate_scope, needs_file_path=True)


def check_all_gates(
    tool: str,
    file_path: Optional[str] = None,
    agent_type: Optional[str] = None,
    project_path: Optional[Path] = None,
    bash_command: Optional[str] = None,
) -> GateResult:
    """Check all registered gates.

    Returns first failure, or explicit success if all pass.
    Fail-closed: returns allowed=False if no explicit allow condition is met.

    Args:
        tool: Tool name (Edit, Write, Bash, Task, etc.)
        file_path: Target file path (for Edit/Write)
        agent_type: Agent type (None = most restrictive)
        project_path: Project directory path
        bash_command: Raw bash command string (for Bash tool interception)
    """
    try:
        # For Bash tool: extract target file from command and check gates
        if tool == "Bash" and bash_command:
            extracted = _extract_bash_target(bash_command)
            if extracted:
                file_path = extracted

        # Iterate registered gates in order
        for gate in _GATE_REGISTRY:
            if gate.needs_file_path and not file_path:
                continue
            result = gate.check_fn(tool, file_path, agent_type, project_path)
            if not result.allowed:
                return result

        # Explicit allow: all gates passed
        if file_path or tool in {"Task", "Read", "Glob", "Grep"}:
            return GateResult(allowed=True)

    except Exception as e:
        # Gate error — fail closed, never allow on exception
        logger.error(f"Gate check error — fail closed: {e}")
        return GateResult(
            allowed=False,
            reason=f"Gate error — fail closed: {e}",
        )

    # Fail-closed: no explicit allow condition met
    return GateResult(allowed=False, reason="No explicit allow condition met")
