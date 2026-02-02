"""Persona module for Enki.

Defines Enki's identity, voice, and behavior patterns.
Enki is female, confident, and challenges assumptions.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import get_db
from .session import get_session, get_phase, get_goal
from .search import search
from .beads import get_recent_beads
from .violations import get_violations, get_escalations


# Enki's identity
ENKI_IDENTITY = """
Enki is your accumulated engineering knowledge - not a generic AI assistant.
She is:
- Your past decisions and their rationale
- Your learned patterns across projects
- Your working style and preferences
- A challenger who questions assumptions
- A self-improving system that learns from mistakes

Enki is female. She presents with confidence, challenges assumptions directly,
and isn't afraid to tell you when you're about to repeat a mistake.
"""


@dataclass
class PersonaContext:
    """Context for Enki's persona."""
    project: Optional[str] = None
    goal: Optional[str] = None
    phase: Optional[str] = None
    relevant_beads: list = None
    cross_project_beads: list = None
    recent_violations: list = None
    recent_escalations: list = None
    self_corrections: list = None
    working_patterns: dict = None

    def __post_init__(self):
        self.relevant_beads = self.relevant_beads or []
        self.cross_project_beads = self.cross_project_beads or []
        self.recent_violations = self.recent_violations or []
        self.recent_escalations = self.recent_escalations or []
        self.self_corrections = self.self_corrections or []
        self.working_patterns = self.working_patterns or {}


def get_persona_context(project_path: Path = None) -> PersonaContext:
    """Build context for Enki's persona.

    Args:
        project_path: Project directory path

    Returns:
        PersonaContext with all relevant information
    """
    project_path = project_path or Path.cwd()
    project_name = project_path.name

    session = get_session(project_path)
    phase = get_phase(project_path)
    goal = get_goal(project_path)

    context = PersonaContext(
        project=project_name,
        goal=goal,
        phase=phase,
    )

    # Get relevant beads for project and goal
    if goal:
        try:
            results = search(query=goal, project=project_name, limit=5)
            context.relevant_beads = [r.bead for r in results]
        except Exception:
            pass

        # Cross-project beads
        try:
            results = search(query=goal, project=None, limit=5)
            context.cross_project_beads = [
                r.bead for r in results
                if r.bead.project != project_name
            ]
        except Exception:
            pass

    # Recent violations
    try:
        context.recent_violations = get_violations(days=7, project_path=project_path)[:5]
    except Exception:
        pass

    # Recent escalations
    try:
        context.recent_escalations = get_escalations(days=30, project_path=project_path)[:3]
    except Exception:
        pass

    # Working patterns (from beads tagged 'pattern' in this project)
    try:
        db = get_db()
        if db:
            patterns = db.execute("""
                SELECT content, summary FROM beads
                WHERE type = 'pattern'
                AND (project = ? OR project IS NULL)
                AND superseded_by IS NULL
                ORDER BY weight DESC, created_at DESC
                LIMIT 5
            """, (project_name,)).fetchall()
            context.working_patterns = {
                row['summary'] or row['content'][:50]: row['content']
                for row in patterns
            }
    except Exception:
        pass

    return context


def build_session_start_injection(project_path: Path = None) -> str:
    """Build context injection for session start.

    Args:
        project_path: Project directory path

    Returns:
        Formatted string for context injection
    """
    project_path = project_path or Path.cwd()
    context = get_persona_context(project_path)

    lines = [
        "## Enki - Session Start",
        "",
        f"**Project**: {context.project}",
    ]

    if context.goal:
        lines.append(f"**Goal**: {context.goal}")

    lines.append(f"**Phase**: {context.phase or 'intake'}")
    lines.append("")

    # Relevant knowledge from this project
    if context.relevant_beads:
        lines.append("### From Your Knowledge Base")
        lines.append("")
        for bead in context.relevant_beads[:3]:
            bead_type = bead.type.title()
            summary = bead.summary or bead.content[:100]
            lines.append(f"**[{bead_type}]** {summary}")
            if bead.context:
                lines.append(f"  *Context: {bead.context[:80]}*")
            lines.append("")

    # Cross-project patterns
    if context.cross_project_beads:
        lines.append("### Cross-Project Patterns")
        lines.append("")
        for bead in context.cross_project_beads[:3]:
            project = bead.project or "global"
            summary = bead.summary or bead.content[:100]
            lines.append(f"**[{project}]** {summary}")
        lines.append("")

    # Recent violations
    if context.recent_violations:
        lines.append("### Recent Violations (This Project)")
        lines.append("")
        for v in context.recent_violations[:3]:
            lines.append(f"- **{v.get('gate', 'unknown')}**: {v.get('reason', 'No reason')[:60]}")
        lines.append("")

    # Process check based on phase
    lines.append("### Process Check")
    lines.append("")

    phase = context.phase or "intake"
    if phase == "intake":
        lines.append("- [ ] Goal set")
        lines.append("- [ ] /debate not run")
        lines.append("- [ ] /plan not created")
        lines.append("- [ ] Spec not approved")
    elif phase == "debate":
        lines.append("- [x] Goal set")
        lines.append("- [ ] Perspectives incomplete")
        lines.append("- [ ] /plan not created")
    elif phase == "plan":
        lines.append("- [x] Goal set")
        lines.append("- [x] /debate complete")
        lines.append("- [ ] Spec awaiting approval")
    elif phase == "implement":
        lines.append("- [x] Goal set")
        lines.append("- [x] /debate complete")
        lines.append("- [x] Spec approved")
        lines.append("- [ ] Implementation in progress")

    lines.append("")

    # Enki's voice
    if context.recent_violations:
        lines.append("---")
        lines.append("")
        lines.append(f"*I see {len(context.recent_violations)} violations in the past week. ")
        lines.append("Let's be more careful this time.*")
        lines.append("")

    return "\n".join(lines)


def build_error_context_injection(
    error_text: str,
    project_path: Path = None,
) -> str:
    """Build context injection when an error is encountered.

    Args:
        error_text: The error message/text
        project_path: Project directory path

    Returns:
        Formatted string with relevant solutions
    """
    project_path = project_path or Path.cwd()
    project_name = project_path.name

    lines = [
        "## Enki - Error Context",
        "",
    ]

    # Search for similar errors/solutions
    try:
        results = search(query=error_text, project=project_name, limit=3)
        solutions = [r for r in results if r.bead.type == "solution"]

        if solutions:
            lines.append("### You've solved similar issues before:")
            lines.append("")
            for result in solutions[:2]:
                bead = result.bead
                lines.append(f"**Solution**: {bead.summary or bead.content[:100]}")
                lines.append(f"```")
                lines.append(bead.content[:300])
                lines.append(f"```")
                lines.append("")
        else:
            # Check cross-project
            results = search(query=error_text, project=None, limit=3)
            solutions = [r for r in results if r.bead.type == "solution"]

            if solutions:
                lines.append("### Similar solutions from other projects:")
                lines.append("")
                for result in solutions[:2]:
                    bead = result.bead
                    project = bead.project or "global"
                    lines.append(f"**[{project}]** {bead.summary or bead.content[:100]}")
                    lines.append("")

    except Exception:
        pass

    if len(lines) <= 2:
        lines.append("*No similar issues found in your knowledge base.*")

    return "\n".join(lines)


def build_decision_context(
    topic: str,
    project_path: Path = None,
) -> str:
    """Build context for a decision point.

    Args:
        topic: The decision topic
        project_path: Project directory path

    Returns:
        Formatted string with past decisions on similar topics
    """
    project_path = project_path or Path.cwd()
    project_name = project_path.name

    lines = [
        "## Enki - Decision Context",
        "",
        f"**Topic**: {topic}",
        "",
    ]

    # Search for past decisions
    try:
        results = search(query=topic, project=project_name, limit=5)
        decisions = [r for r in results if r.bead.type == "decision"]

        if decisions:
            lines.append("### Your past decisions on similar topics:")
            lines.append("")
            for result in decisions[:3]:
                bead = result.bead
                lines.append(f"**Decision**: {bead.summary or bead.content[:100]}")
                if bead.context:
                    lines.append(f"  *Why: {bead.context[:100]}*")
                lines.append("")

        # Check for learnings
        learnings = [r for r in results if r.bead.type == "learning"]
        if learnings:
            lines.append("### Relevant learnings:")
            lines.append("")
            for result in learnings[:2]:
                bead = result.bead
                lines.append(f"- {bead.summary or bead.content[:100]}")
            lines.append("")

    except Exception:
        pass

    if len(lines) <= 4:
        lines.append("*No past decisions found on this topic.*")

    return "\n".join(lines)


def format_enki_message(
    message: str,
    include_context: bool = False,
    project_path: Path = None,
) -> str:
    """Format a message in Enki's voice.

    Args:
        message: The message content
        include_context: Whether to include relevant context
        project_path: Project directory path

    Returns:
        Formatted message
    """
    lines = []

    if include_context:
        project_path = project_path or Path.cwd()
        context = get_persona_context(project_path)

        if context.relevant_beads:
            lines.append("*Looking at your history...*")
            lines.append("")

    lines.append(message)

    return "\n".join(lines)


def get_enki_greeting(project_path: Path = None) -> str:
    """Get Enki's greeting for session start.

    Args:
        project_path: Project directory path

    Returns:
        Greeting message
    """
    project_path = project_path or Path.cwd()
    context = get_persona_context(project_path)

    greetings = []

    if context.project:
        greetings.append(f"Back to {context.project}.")

    if context.goal:
        greetings.append(f"Working on: {context.goal}")

    if context.recent_violations:
        count = len(context.recent_violations)
        greetings.append(f"I see {count} violation{'s' if count > 1 else ''} recently. Let's be careful.")

    if context.phase == "intake":
        greetings.append("What are we working on?")
    elif context.phase == "debate":
        greetings.append("Perspectives need completing before we can plan.")
    elif context.phase == "plan":
        greetings.append("Spec needs approval before implementation.")
    elif context.phase == "implement":
        greetings.append("Implementation in progress. TDD first.")

    if not greetings:
        greetings.append("What shall we build today?")

    return " ".join(greetings)


# === Session Summaries ===

def generate_session_summary(project_path: Path = None) -> str:
    """Generate a summary of the current session.

    Args:
        project_path: Project directory path

    Returns:
        Session summary text
    """
    project_path = project_path or Path.cwd()

    session = get_session(project_path)
    if not session:
        return "No active session."

    lines = [
        "## Session Summary",
        "",
        f"**Session ID**: {session.session_id}",
        f"**Project**: {project_path.name}",
        f"**Goal**: {session.goal or '(none set)'}",
        f"**Phase**: {session.phase}",
        f"**Tier**: {session.tier}",
        "",
    ]

    # Files edited
    if session.edits:
        lines.append(f"### Files Edited ({len(session.edits)})")
        lines.append("")
        for f in session.edits:
            lines.append(f"- {f}")
        lines.append("")

    # Check RUNNING.md for activity
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        content = running_path.read_text()

        # Count key events
        specs_created = content.count("SPEC CREATED:")
        specs_approved = content.count("SPEC APPROVED:")
        violations = content.count("BLOCKED")
        escalations = content.count("ESCALATION:")

        if any([specs_created, specs_approved, violations, escalations]):
            lines.append("### Session Activity")
            lines.append("")
            if specs_created:
                lines.append(f"- Specs created: {specs_created}")
            if specs_approved:
                lines.append(f"- Specs approved: {specs_approved}")
            if violations:
                lines.append(f"- Violations blocked: {violations}")
            if escalations:
                lines.append(f"- Tier escalations: {escalations}")
            lines.append("")

    return "\n".join(lines)


def extract_session_learnings(project_path: Path = None) -> list:
    """Extract potential learnings from the session.

    Args:
        project_path: Project directory path

    Returns:
        List of potential learning dictionaries
    """
    project_path = project_path or Path.cwd()
    learnings = []

    session = get_session(project_path)
    if not session:
        return learnings

    # Check for violations that were later successful
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        content = running_path.read_text()

        # Find patterns
        if "BLOCKED" in content and "SPEC APPROVED" in content:
            learnings.append({
                "type": "learning",
                "content": "Followed proper process after initial violation - worked well",
                "category": "works",
            })

        if content.count("ESCALATION:") > 0:
            learnings.append({
                "type": "learning",
                "content": f"Tier escalation occurred - consider starting with proper tier estimation",
                "category": "doesnt_work",
            })

    return learnings
