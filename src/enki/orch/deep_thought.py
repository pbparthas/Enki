"""Deep Thought — task complexity scoring and model routing.

Transparent middleware in enki_spawn.
"""

from typing import Any

MODEL_OPUS = "claude-opus-4-6"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

ROLE_MODEL_OVERRIDE: dict[str, str] = {
    "architect": MODEL_OPUS,
    "igi": MODEL_OPUS,
    "security-auditor": MODEL_OPUS,
    "pm": MODEL_SONNET,
    "cto": MODEL_SONNET,
    "devils_advocate": MODEL_SONNET,
    "tech_feasibility": MODEL_SONNET,
    "historical_context": MODEL_SONNET,
}

HIGH_COMPLEXITY_SIGNALS = {
    "architecture", "security", "auth", "authentication",
    "payment", "algorithm", "performance", "migration",
    "refactor", "redesign", "breaking", "protocol",
    "encryption", "concurrent", "distributed", "race condition",
    "transaction", "rollback", "consensus", "sharding",
}

MEDIUM_COMPLEXITY_SIGNALS = {
    "integration", "api", "database", "schema", "index",
    "cache", "queue", "event", "webhook", "retry",
    "validation", "serialization", "parsing",
}


def compute_task_complexity(
    task: dict[str, Any],
    role: str,
    graph_context: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Score task complexity and recommend a model."""
    role_key = (role or "").strip().lower()
    if role_key in ROLE_MODEL_OVERRIDE:
        return (0, ROLE_MODEL_OVERRIDE[role_key])

    score = 0
    file_count = len(task.get("files") or task.get("assigned_files") or [])
    score += min(file_count * 2, 10)

    dep_count = len(task.get("dependencies") or [])
    score += min(dep_count, 5)

    text = ((task.get("description") or "") + " " + (task.get("name") or "")).lower()
    high_matches = sum(1 for s in HIGH_COMPLEXITY_SIGNALS if s in text)
    medium_matches = sum(1 for s in MEDIUM_COMPLEXITY_SIGNALS if s in text)
    score += high_matches * 4
    score += medium_matches * 2

    if graph_context:
        max_blast = float(graph_context.get("max_blast_score", 0) or 0)
        score += int(max_blast * 15)

    criteria_count = len(task.get("acceptance_criteria") or [])
    score += min(criteria_count, 5)

    if score >= 18:
        model = MODEL_OPUS
    elif score >= 8:
        model = MODEL_SONNET
    else:
        model = MODEL_HAIKU

    return (score, model)


def select_model(
    role: str,
    task: dict[str, Any],
    graph_context: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Public API for model selection."""
    return compute_task_complexity(task, role, graph_context)
