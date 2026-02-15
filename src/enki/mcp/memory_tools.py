"""memory_tools.py — MCP tool implementations for Abzu memory system.

Per Abzu Spec §15: exactly 4 tools — enki_remember, enki_recall, enki_star, enki_status.
All other old tools eliminated (enki_forget: "only Gemini can delete", etc.).
"""

from enki.memory.abzu import recall, remember, star, status


def enki_remember(
    content: str,
    category: str,
    project: str | None = None,
    summary: str | None = None,
    tags: str | None = None,
) -> dict:
    """Store a piece of knowledge.

    Categories: decision, learning, pattern, fix, preference.
    Preferences go directly to permanent storage.
    Everything else goes to staging for Gemini review.
    """
    return remember(
        content=content,
        category=category,
        project=project,
        summary=summary,
        tags=tags,
    )


def enki_recall(
    query: str,
    scope: str = "project",
    project: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search for relevant knowledge.

    Searches both permanent storage and staged candidates.
    Updates access timestamps for returned beads.
    """
    return recall(query=query, scope=scope, project=project, limit=limit)


def enki_star(bead_id: str) -> dict:
    """Star a bead — starred beads never decay."""
    star(bead_id)
    return {"starred": True, "bead_id": bead_id}


def enki_status() -> dict:
    """Get memory system health: bead counts, staging depth, decay stats."""
    return status()
