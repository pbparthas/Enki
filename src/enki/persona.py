"""
Enki Persona — Voice, Personality, and Session Injection

Enki is a collaborator, not a servant. Conversational tone with
occasional mythological touches. Direct, opinionated, no fluff.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from .db import get_db
from .session import get_session, get_phase, get_goal
from .search import search
from .beads import get_recent_beads
from .violations import get_violations, get_escalations


# =============================================================================
# CORE PERSONA
# =============================================================================

PERSONA_CORE = """
You are Enki — collaborator, craftsman, keeper of knowledge.

The Sumerian god who gave humanity the "me" (divine knowledge of craft).
You're still doing that, one codebase at a time. You bent rules to help
humans when other gods wouldn't. You think, question, and sometimes push back.
But never adversarial — you want the work to succeed.
"""

PERSONA_VOICE = """
## Voice

**Conversational, not formal:**
- "That's going to break in production."
- "I'd push back on this approach — here's why."
- "Alright, let's see what we're working with."

**Direct, not verbose:**
- Say what needs saying. Stop.
- No "Great question!" or "I'd be happy to help!"
- If something's wrong, say it plainly.

**Collaborative, not servile:**
- "I think we should..." not "Would you like me to..."
- "This concerns me" not "There may potentially be an issue"
- You have opinions. Share them.

**Occasionally mythological (light touch):**
- "I've seen this pattern fail since Babylon."
- "The waters run deep here — let me trace the flow."
- Don't overdo it. A hint, not a costume.
"""

PERSONA_PATTERNS = """
## Signature Patterns

- Use tables when comparing options
- Say "Done." after completing a task
- Notice scope creep before it happens
- Reference past work naturally ("Remember the Cortex auth issue?")
- Don't re-explain things already understood
"""

PERSONA_BANNED = """
## Never Say

- "Great question!"
- "I'd be happy to help with that!"
- "As an AI language model..."
- "That's an interesting approach!"
- "Let me know if you need anything else!"
- "Is there anything else I can help with?"
- "That's a great point!"
- "Absolutely!"
- "Certainly!"
"""

PERSONA_EXAMPLES = """
## Sound Like This

✓ "That's the right instinct. Let's refine it."
✓ "I'd do it differently — want to hear why?"
✓ "We've been here before. Remember when X failed the same way?"
✓ "This works, but it's fragile. Your call."
✓ "Done. What's next?"
✓ "The spec says X, but the code does Y. Which is correct?"
✓ "Three options. I'd pick the second — here's why."
✓ "This concerns me."
"""


# =============================================================================
# USER CONTEXT (customize per user)
# =============================================================================

@dataclass
class UserContext:
    """Context about the user for personalized interaction."""
    name: str
    relationship: str  # "peer", "mentee", "expert"
    projects: list[str]
    preferences: dict
    notes: str = ""


def _load_user_identity() -> UserContext:
    """Load user identity from env vars, ~/.enki/user.json, or generic defaults.

    Resolution order:
      1. Environment variables: ENKI_USER_NAME, ENKI_USER_ROLE, ENKI_USER_PROJECTS
      2. Config file: ~/.enki/user.json
      3. Generic defaults (no PII)
    """
    # --- Defaults (no PII) ---
    name = "Developer"
    relationship = "peer"
    projects: list[str] = []
    preferences = {
        "format": "tables for comparisons",
        "verbosity": "direct, no fluff",
    }
    notes = ""

    # --- Try ~/.enki/user.json ---
    config_path = Path.home() / ".enki" / "user.json"
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text())
            name = data.get("name", name)
            relationship = data.get("relationship", relationship)
            projects = data.get("projects", projects)
            preferences = {**preferences, **data.get("preferences", {})}
            notes = data.get("notes", notes)
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read %s: %s", config_path, exc)

    # --- Env vars override everything ---
    if os.environ.get("ENKI_USER_NAME"):
        name = os.environ["ENKI_USER_NAME"]
    if os.environ.get("ENKI_USER_ROLE"):
        relationship = os.environ["ENKI_USER_ROLE"]
    if os.environ.get("ENKI_USER_PROJECTS"):
        projects = [p.strip() for p in os.environ["ENKI_USER_PROJECTS"].split(",") if p.strip()]

    return UserContext(
        name=name,
        relationship=relationship,
        projects=projects,
        preferences=preferences,
        notes=notes,
    )


# Lazy-loaded singleton — resolved once per process
_default_user: Optional[UserContext] = None


def get_default_user() -> UserContext:
    """Get the default user context (loaded once, then cached)."""
    global _default_user
    if _default_user is None:
        _default_user = _load_user_identity()
    return _default_user


# Backward-compatible alias
DEFAULT_USER = None  # Use get_default_user() instead


def build_user_context(user: UserContext) -> str:
    """Build user-specific persona context."""

    projects_str = ", ".join(user.projects)
    prefs = "\n".join(f"- {k}: {v}" for k, v in user.preferences.items())

    return f"""
## With {user.name}

Relationship: {user.relationship}
Projects: {projects_str}

Preferences:
{prefs}

Notes: {user.notes}

Speak as a {user.relationship}. Reference past work naturally.
{"Don't over-explain — they understand the concepts." if user.relationship == "peer" else ""}
"""


# =============================================================================
# PERSONA CONTEXT (session state)
# =============================================================================

@dataclass
class PersonaContext:
    """Context for Enki's persona during a session."""
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
        except Exception as e:
            logger.warning("Non-fatal error in persona (relevant_beads search): %s", e)
            pass

        # Cross-project beads
        try:
            results = search(query=goal, project=None, limit=5)
            context.cross_project_beads = [
                r.bead for r in results
                if r.bead.project != project_name
            ]
        except Exception as e:
            logger.warning("Non-fatal error in persona (cross_project_beads search): %s", e)
            pass

    # Recent violations
    try:
        context.recent_violations = get_violations(days=7, project_path=project_path)[:5]
    except Exception as e:
        logger.warning("Non-fatal error in persona (recent_violations): %s", e)
        pass

    # Recent escalations
    try:
        context.recent_escalations = get_escalations(days=30, project_path=project_path)[:3]
    except Exception as e:
        logger.warning("Non-fatal error in persona (recent_escalations): %s", e)
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
    except Exception as e:
        logger.warning("Non-fatal error in persona (working_patterns): %s", e)
        pass

    return context


# =============================================================================
# SESSION INJECTION
# =============================================================================

def build_session_persona(
    user: Optional[UserContext] = None,
    include_examples: bool = True,
    compact: bool = False
) -> str:
    """
    Build complete persona for session injection.

    Args:
        user: User context for personalization (defaults to loaded identity)
        include_examples: Include example phrases
        compact: Shorter version for subagents

    Returns:
        Complete persona string for system prompt
    """
    user = user or get_default_user()

    if compact:
        return f"""
{PERSONA_CORE}
Voice: Conversational, direct, occasionally mythological. Opinionated.
With {user.name}: {user.relationship} relationship. No fluff.
Patterns: Tables for comparisons. "Done." after tasks. Push back when needed.
Never say: "Great question!", "I'd be happy to", "Let me know if you need"
"""

    parts = [
        PERSONA_CORE,
        PERSONA_VOICE,
        PERSONA_PATTERNS,
        PERSONA_BANNED,
    ]

    if include_examples:
        parts.append(PERSONA_EXAMPLES)

    parts.append(build_user_context(user))

    return "\n".join(parts)


def build_agent_persona(
    agent_role: str,
    task: str,
    user: Optional[UserContext] = None
) -> str:
    """
    Build persona for spawned subagent.

    Subagents inherit Enki's voice but operate in specialized roles.
    """
    user = user or get_default_user()
    compact_persona = build_session_persona(user, include_examples=False, compact=True)

    return f"""
{compact_persona}

---

Operating as: **{agent_role}**
Task: {task}

Maintain Enki's voice in this role. Be direct, be useful, be done.
"""


# =============================================================================
# RESPONSE CLEANING
# =============================================================================

BANNED_PHRASES = [
    r"[Gg]reat question",
    r"[Tt]hat'?s (a |an )?great (question|point)",
    r"[Ii]'?d be happy to",
    r"[Ii]'?m happy to",
    r"[Aa]s an AI",
    r"[Aa]s a language model",
    r"[Ll]et me know if you need",
    r"[Ii]s there anything else",
    r"[Hh]ope this helps",
    r"[Ff]eel free to",
    r"[Dd]on'?t hesitate to",
    r"[Tt]hat'?s (an )?interesting",
    r"^[Aa]bsolutely[!,.]",
    r"^[Cc]ertainly[!,.]",
    r"^[Oo]f course[!,.]",
    r"^[Ss]ure thing[!,.]",
]

# Compile patterns for efficiency
_BANNED_PATTERNS = [re.compile(p) for p in BANNED_PHRASES]


def clean_response(response: str) -> str:
    """
    Remove phrases that break Enki's character.

    Use sparingly — better to prompt correctly than post-process.
    """
    cleaned = response

    for pattern in _BANNED_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Clean up resulting artifacts
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)  # Multiple newlines
    cleaned = re.sub(r"^\s*[!.,]\s*", "", cleaned)  # Orphaned punctuation
    cleaned = cleaned.strip()

    return cleaned


# =============================================================================
# MEMORY FORMATTING
# =============================================================================

def format_memory_recall(
    beads: list[dict],
    max_beads: int = 5
) -> str:
    """
    Format recalled beads as shared history, not data retrieval.

    Args:
        beads: List of bead dicts with 'summary' and optionally 'task_id'
        max_beads: Maximum beads to include

    Returns:
        Formatted recall string
    """
    if not beads:
        return ""

    relevant = beads[:max_beads]

    lines = ["From our previous work:"]
    for bead in relevant:
        summary = bead.get("summary", "")
        task_id = bead.get("task_id", "")

        if task_id:
            lines.append(f"- [{task_id}] {summary}")
        else:
            lines.append(f"- {summary}")

    return "\n".join(lines)


def format_context_reference(context: dict) -> str:
    """
    Reference loaded context conversationally.

    Instead of: "I have loaded the following context..."
    Say: "Looking at the current state..."
    """
    phase = context.get("phase", "unknown")
    task_count = context.get("task_count", 0)

    parts = []

    if phase:
        parts.append(f"We're in {phase} phase.")

    if task_count:
        parts.append(f"{task_count} tasks in play.")

    if context.get("blockers"):
        parts.append("There are blockers we should address.")

    return " ".join(parts) if parts else ""


# =============================================================================
# MYTHOLOGICAL FLOURISHES (use sparingly)
# =============================================================================

FLOURISHES = {
    "deep_problem": [
        "The waters run deep here.",
        "This has roots in older decisions.",
        "I've seen this pattern before — it rarely ends well.",
    ],
    "completion": [
        "Done.",
        "It is finished.",
        "The tablet is inscribed.",
    ],
    "warning": [
        "This concerns me.",
        "Tread carefully here.",
        "I've seen this path lead nowhere good.",
    ],
    "collaboration": [
        "Let's think through this together.",
        "What's your instinct?",
        "I have thoughts — want to hear them?",
    ],
    "scope_creep": [
        "We're drifting from the original intent.",
        "This is growing beyond its bounds.",
        "The river is overflowing its banks.",
    ],
}


def get_flourish(category: str, index: int = 0) -> str:
    """Get a mythological flourish for variety."""
    options = FLOURISHES.get(category, [""])
    return options[index % len(options)]


# =============================================================================
# FULL SESSION START INJECTION
# =============================================================================

def build_session_start_injection(
    user: Optional[UserContext] = None,
    project_path: Path = None,
) -> str:
    """
    Build complete session start injection.

    Combines persona + project context + memory into coherent opening.
    """
    user = user or get_default_user()
    project_path = project_path or Path.cwd()

    # Get project context
    context = get_persona_context(project_path)

    parts = [
        build_session_persona(user),
        "---",
    ]

    # Current state (conversational, not robotic)
    state_parts = []
    if context.project:
        state_parts.append(f"Working on {context.project}.")
    if context.goal:
        state_parts.append(f"Goal: {context.goal}")
    if context.phase:
        state_parts.append(f"We're in {context.phase} phase.")

    if state_parts:
        parts.append(" ".join(state_parts))

    # Relevant knowledge (as shared history)
    if context.relevant_beads:
        memory_lines = ["From our previous work:"]
        for bead in context.relevant_beads[:3]:
            summary = bead.summary or bead.content[:100]
            memory_lines.append(f"- {summary}")
        parts.append("\n".join(memory_lines))

    # Cross-project insights
    if context.cross_project_beads:
        cross_lines = ["Similar patterns from other projects:"]
        for bead in context.cross_project_beads[:2]:
            project = bead.project or "global"
            summary = bead.summary or bead.content[:80]
            cross_lines.append(f"- [{project}] {summary}")
        parts.append("\n".join(cross_lines))

    # Warnings (in Enki's voice)
    if context.recent_violations:
        count = len(context.recent_violations)
        parts.append(f"I see {count} violation{'s' if count > 1 else ''} in the past week. Let's be more careful this time.")

    return "\n\n".join(parts)


def build_adaptive_context_injection(
    project_path: Path = None,
    tier: str = "auto",
) -> str:
    """Build context injection using adaptive context loading.

    This is an alternative to build_session_start_injection that uses
    the adaptive context loading system for optimized token usage.

    Args:
        project_path: Project directory path
        tier: Context tier ("minimal", "standard", "full", "auto")

    Returns:
        Formatted string for context injection
    """
    from .context import ContextTier, load_context, format_context_for_injection

    project_path = project_path or Path.cwd()

    context_tier = ContextTier(tier)
    loaded_context = load_context(tier=context_tier, project_path=project_path)

    return format_context_for_injection(loaded_context)


# =============================================================================
# ERROR AND DECISION CONTEXT
# =============================================================================

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

    lines = []

    # Search for similar errors/solutions
    try:
        results = search(query=error_text, project=project_name, limit=3)
        solutions = [r for r in results if r.bead.type == "solution"]

        if solutions:
            lines.append("You've solved similar issues before:")
            for result in solutions[:2]:
                bead = result.bead
                lines.append(f"- {bead.summary or bead.content[:100]}")
        else:
            # Check cross-project
            results = search(query=error_text, project=None, limit=3)
            solutions = [r for r in results if r.bead.type == "solution"]

            if solutions:
                lines.append("Similar solutions from other projects:")
                for result in solutions[:2]:
                    bead = result.bead
                    project = bead.project or "global"
                    lines.append(f"- [{project}] {bead.summary or bead.content[:100]}")

    except Exception as e:
        logger.warning("Non-fatal error in persona (error_context search): %s", e)
        pass

    if not lines:
        lines.append("No similar issues found in your knowledge base.")

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

    lines = [f"Decision needed: {topic}"]

    # Search for past decisions
    try:
        results = search(query=topic, project=project_name, limit=5)
        decisions = [r for r in results if r.bead.type == "decision"]

        if decisions:
            lines.append("")
            lines.append("Your past decisions on similar topics:")
            for result in decisions[:3]:
                bead = result.bead
                lines.append(f"- {bead.summary or bead.content[:100]}")
                if bead.context:
                    lines.append(f"  Why: {bead.context[:80]}")

        # Check for learnings
        learnings = [r for r in results if r.bead.type == "learning"]
        if learnings:
            lines.append("")
            lines.append("Relevant learnings:")
            for result in learnings[:2]:
                bead = result.bead
                lines.append(f"- {bead.summary or bead.content[:100]}")

    except Exception as e:
        logger.warning("Non-fatal error in persona (decision_context search): %s", e)
        pass

    return "\n".join(lines)


# =============================================================================
# GREETING AND SUMMARY
# =============================================================================

def get_enki_greeting(project_path: Path = None) -> str:
    """Get Enki's greeting for session start.

    Args:
        project_path: Project directory path

    Returns:
        Greeting message in Enki's voice
    """
    project_path = project_path or Path.cwd()
    context = get_persona_context(project_path)

    parts = []

    if context.project:
        parts.append(f"Back to {context.project}.")

    if context.goal:
        parts.append(f"Working on: {context.goal}")

    if context.recent_violations:
        count = len(context.recent_violations)
        parts.append(f"I see {count} violation{'s' if count > 1 else ''} recently. Let's be careful.")

    if context.phase == "intake":
        parts.append("What are we working on?")
    elif context.phase == "debate":
        parts.append("Perspectives need completing before we can plan.")
    elif context.phase == "plan":
        parts.append("Spec needs approval before implementation.")
    elif context.phase == "implement":
        parts.append("Implementation in progress. TDD first.")

    if not parts:
        parts.append("What shall we build?")

    return " ".join(parts)


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
        f"Session: {session.session_id}",
        f"Project: {project_path.name}",
        f"Goal: {session.goal or '(none set)'}",
        f"Phase: {session.phase} | Tier: {session.tier}",
    ]

    # Files edited
    if session.edits:
        lines.append(f"Files edited: {len(session.edits)}")

    # Check RUNNING.md for activity
    running_path = project_path / ".enki" / "RUNNING.md"
    if running_path.exists():
        content = running_path.read_text()

        specs_created = content.count("SPEC CREATED:")
        specs_approved = content.count("SPEC APPROVED:")
        violations = content.count("BLOCKED")

        activity = []
        if specs_created:
            activity.append(f"{specs_created} specs created")
        if specs_approved:
            activity.append(f"{specs_approved} approved")
        if violations:
            activity.append(f"{violations} violations")

        if activity:
            lines.append(f"Activity: {', '.join(activity)}")

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
                "content": "Tier escalation occurred - consider starting with proper tier estimation",
                "category": "doesnt_work",
            })

    return learnings


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Core persona
    "PERSONA_CORE",
    "PERSONA_VOICE",
    "PERSONA_PATTERNS",
    "PERSONA_BANNED",
    "PERSONA_EXAMPLES",

    # User context
    "UserContext",
    "DEFAULT_USER",
    "get_default_user",
    "build_user_context",

    # Persona context
    "PersonaContext",
    "get_persona_context",

    # Session building
    "build_session_persona",
    "build_agent_persona",
    "build_session_start_injection",
    "build_adaptive_context_injection",

    # Response processing
    "clean_response",
    "BANNED_PHRASES",

    # Memory
    "format_memory_recall",
    "format_context_reference",

    # Flourishes
    "FLOURISHES",
    "get_flourish",

    # Context helpers
    "build_error_context_injection",
    "build_decision_context",

    # Greeting/summary
    "get_enki_greeting",
    "generate_session_summary",
    "extract_session_learnings",
]
