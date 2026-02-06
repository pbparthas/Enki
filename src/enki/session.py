"""Session state management."""

import uuid
from pathlib import Path
from typing import Optional, Literal
from dataclasses import dataclass
from datetime import datetime

from .path_utils import atomic_write

Phase = Literal["intake", "debate", "plan", "implement", "review", "test", "ship"]
Tier = Literal["trivial", "quick_fix", "feature", "major"]

PHASES: list[Phase] = ["intake", "debate", "plan", "implement", "review", "test", "ship"]
TIERS: list[Tier] = ["trivial", "quick_fix", "feature", "major"]


@dataclass
class SessionState:
    """Current session state."""
    session_id: str
    project_path: Path
    phase: Phase
    tier: Tier
    goal: Optional[str]
    edits: list[str]  # Files edited this session


def get_project_enki_dir(project_path: Optional[Path] = None) -> Path:
    """Get the .enki directory for a project."""
    path = project_path or Path.cwd()
    return path / ".enki"


def ensure_project_enki_dir(project_path: Optional[Path] = None) -> Path:
    """Ensure .enki directory exists for project."""
    enki_dir = get_project_enki_dir(project_path)
    enki_dir.mkdir(parents=True, exist_ok=True)
    return enki_dir


def start_session(
    project_path: Optional[Path] = None,
    goal: Optional[str] = None,
) -> SessionState:
    """Start a new session.

    Creates/resets session state files in project's .enki directory.
    """
    enki_dir = ensure_project_enki_dir(project_path)
    session_id = str(uuid.uuid4())

    # Initialize state files — P2-17: atomic writes to prevent corruption
    with atomic_write(enki_dir / "SESSION_ID") as f:
        f.write(session_id)
    with atomic_write(enki_dir / "PHASE") as f:
        f.write("intake")
    with atomic_write(enki_dir / "TIER") as f:
        f.write("trivial")
    with atomic_write(enki_dir / ".session_edits") as f:
        f.write("")

    if goal:
        with atomic_write(enki_dir / "GOAL") as f:
            f.write(goal)
    elif (enki_dir / "GOAL").exists():
        (enki_dir / "GOAL").unlink()

    return SessionState(
        session_id=session_id,
        project_path=project_path or Path.cwd(),
        phase="intake",
        tier="trivial",
        goal=goal,
        edits=[],
    )


def get_session(project_path: Optional[Path] = None) -> Optional[SessionState]:
    """Get current session state."""
    enki_dir = get_project_enki_dir(project_path)

    if not enki_dir.exists():
        return None

    session_id_file = enki_dir / "SESSION_ID"
    if not session_id_file.exists():
        return None

    session_id = session_id_file.read_text().strip()
    phase = get_phase(project_path)
    tier = get_tier(project_path)
    goal = get_goal(project_path)
    edits = get_session_edits(project_path)

    return SessionState(
        session_id=session_id,
        project_path=project_path or Path.cwd(),
        phase=phase,
        tier=tier,
        goal=goal,
        edits=edits,
    )


def get_phase(project_path: Optional[Path] = None) -> Phase:
    """Get current phase."""
    enki_dir = get_project_enki_dir(project_path)
    phase_file = enki_dir / "PHASE"

    if phase_file.exists():
        phase = phase_file.read_text().strip().lower()
        if phase in PHASES:
            return phase  # type: ignore

    return "intake"


def set_phase(phase: Phase, project_path: Optional[Path] = None) -> None:
    """Set current phase."""
    if phase not in PHASES:
        raise ValueError(f"Invalid phase: {phase}. Must be one of {PHASES}")

    enki_dir = ensure_project_enki_dir(project_path)
    with atomic_write(enki_dir / "PHASE") as f:
        f.write(phase)


def get_tier(project_path: Optional[Path] = None) -> Tier:
    """Get current tier."""
    enki_dir = get_project_enki_dir(project_path)
    tier_file = enki_dir / "TIER"

    if tier_file.exists():
        tier = tier_file.read_text().strip().lower()
        if tier in TIERS:
            return tier  # type: ignore

    return "trivial"


def set_tier(tier: Tier, project_path: Optional[Path] = None) -> None:
    """Set current tier."""
    if tier not in TIERS:
        raise ValueError(f"Invalid tier: {tier}. Must be one of {TIERS}")

    enki_dir = ensure_project_enki_dir(project_path)
    with atomic_write(enki_dir / "TIER") as f:
        f.write(tier)


def get_goal(project_path: Optional[Path] = None) -> Optional[str]:
    """Get session goal."""
    enki_dir = get_project_enki_dir(project_path)
    goal_file = enki_dir / "GOAL"

    if goal_file.exists():
        return goal_file.read_text().strip()

    return None


def set_goal(goal: str, project_path: Optional[Path] = None) -> None:
    """Set session goal."""
    enki_dir = ensure_project_enki_dir(project_path)
    with atomic_write(enki_dir / "GOAL") as f:
        f.write(goal)


def get_session_edits(project_path: Optional[Path] = None) -> list[str]:
    """Get list of files edited this session."""
    enki_dir = get_project_enki_dir(project_path)
    edits_file = enki_dir / ".session_edits"

    if edits_file.exists():
        content = edits_file.read_text().strip()
        if content:
            return [line.strip() for line in content.split("\n") if line.strip()]

    return []


def add_session_edit(file_path: str, project_path: Optional[Path] = None) -> list[str]:
    """Add a file to session edits."""
    enki_dir = ensure_project_enki_dir(project_path)
    edits_file = enki_dir / ".session_edits"

    edits = get_session_edits(project_path)

    # Only add if not already tracked
    if file_path not in edits:
        edits.append(file_path)
        with atomic_write(edits_file) as f:
            f.write("\n".join(edits))

    return edits


def get_session_id(project_path: Optional[Path] = None) -> Optional[str]:
    """Get current session ID."""
    enki_dir = get_project_enki_dir(project_path)
    session_id_file = enki_dir / "SESSION_ID"

    if session_id_file.exists():
        return session_id_file.read_text().strip()

    return None


def has_approved_spec(project_path: Optional[Path] = None) -> bool:
    """Check if there's an approved spec."""
    enki_dir = get_project_enki_dir(project_path)
    running_file = enki_dir / "RUNNING.md"

    if running_file.exists():
        content = running_file.read_text()
        return "SPEC APPROVED:" in content

    return False


def get_scope_files(project_path: Optional[Path] = None) -> list[str]:
    """Get files in scope for current orchestration."""
    enki_dir = get_project_enki_dir(project_path)
    scope_file = enki_dir / "SCOPE"

    if scope_file.exists():
        content = scope_file.read_text().strip()
        if content:
            return [line.strip() for line in content.split("\n") if line.strip()]

    return []


def set_scope_files(files: list[str], project_path: Optional[Path] = None) -> None:
    """Set files in scope for orchestration."""
    enki_dir = ensure_project_enki_dir(project_path)
    with atomic_write(enki_dir / "SCOPE") as f:
        f.write("\n".join(files))


def tier_rank(tier: Tier) -> int:
    """Get numeric rank of tier for comparison."""
    return TIERS.index(tier)


def tier_escalated(old_tier: Tier, new_tier: Tier) -> bool:
    """Check if tier has escalated."""
    return tier_rank(new_tier) > tier_rank(old_tier)
