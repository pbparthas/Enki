"""code_nudge.py — Duplicate code nudge for Dev and Reviewer (Item 4.6).

EM checks Abzu recall results before spawning Dev. If code knowledge
matches task, inject reuse hint. Reviewer catches if Dev ignored it.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def check_for_reusable_code(
    task_description: str,
    project: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Check Abzu for code knowledge matching the task.

    Returns list of matching code knowledge notes.
    """
    try:
        from enki.orch.recall import recall_for_dev
        result = recall_for_dev(task_description, project=project, limit=limit)
        return result.get("code_knowledge", [])
    except Exception as e:
        logger.debug("Code reuse check failed: %s", e)
        return []


def build_dev_nudge(matches: list[dict]) -> str | None:
    """Build a reuse nudge for Dev agent context injection.

    Returns None if no matches.
    """
    if not matches:
        return None

    lines = [
        "## CODE REUSE ADVISORY",
        "",
        "Existing code knowledge matches this task. "
        "**Evaluate for reuse before implementing new code.**",
        "",
    ]
    for m in matches[:5]:
        content = m.get("content", "")[:200]
        file_ref = m.get("file_ref", "")
        if file_ref:
            lines.append(f"- `{file_ref}`: {content}")
        else:
            lines.append(f"- {content}")

    return "\n".join(lines)


def build_reviewer_instruction(has_code_matches: bool) -> str | None:
    """Build a Reviewer instruction about duplicate code detection.

    Returns instruction string if code knowledge was injected into Dev context,
    None otherwise.
    """
    if not has_code_matches:
        return None

    return (
        "Code knowledge was injected into the Dev context for this task. "
        "Flag any new code that duplicates existing utilities identified "
        "in the code knowledge. If Dev created new implementations for "
        "functionality that already exists, note it as a concern."
    )
