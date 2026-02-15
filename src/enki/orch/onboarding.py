"""onboarding.py — Entry point detection, user profile, first-time flow.

Detect entry point (greenfield/mid-design/brownfield).
Manage user profile in wisdom.db.
First-time user: two questions max, everything else learned over time.
"""

from pathlib import Path

from enki.db import wisdom_db


# Source code indicators for brownfield detection
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".cs", ".cpp", ".c",
    ".swift", ".scala", ".clj",
}

# Design artifact indicators for mid-design detection
_ARTIFACT_TYPES = {
    "prd", "product spec", "wireframe", "api spec",
    "design doc", "architecture doc", "figma", "miro",
}


def detect_entry_point(signals: dict) -> str:
    """Auto-detect entry point based on signals.

    Args:
        signals: {
            "existing_repo": bool,
            "repo_path": str (optional),
            "design_artifacts": bool,
        }

    Returns: "greenfield" | "mid_design" | "brownfield"
    """
    if signals.get("existing_repo"):
        repo_path = signals.get("repo_path", "")
        if repo_path and _has_source_files(repo_path):
            return "brownfield"

    if signals.get("design_artifacts"):
        return "mid_design"

    return "greenfield"


def get_or_create_user_profile() -> dict:
    """Load user profile from wisdom.db.

    Returns dict of preferences. Creates table entry if first time.
    """
    with wisdom_db() as conn:
        rows = conn.execute(
            "SELECT key, value, source, confidence FROM user_profile"
        ).fetchall()

    if not rows:
        return {
            "user_id": "default",
            "first_time": True,
            "preferences": {},
        }

    prefs = {}
    for row in rows:
        prefs[row["key"]] = {
            "value": row["value"],
            "source": row["source"],
            "confidence": row["confidence"],
        }

    return {
        "user_id": "default",
        "first_time": False,
        "preferences": prefs,
    }


def update_user_profile(
    key: str,
    value: str,
    source: str = "explicit",
    confidence: float = 1.0,
    project: str | None = None,
) -> None:
    """Update or create user profile entry.

    Args:
        key: Preference key
        value: Preference value
        source: "explicit" | "inferred" | "codebase"
        confidence: 1.0 for explicit, 0.5 for inferred
        project: Which project this was learned from
    """
    with wisdom_db() as conn:
        # Check existing
        existing = conn.execute(
            "SELECT source, confidence FROM user_profile WHERE key = ?",
            (key,),
        ).fetchone()

        if existing:
            # Explicit beats inferred — don't downgrade
            if existing["source"] == "explicit" and source != "explicit":
                return
            conn.execute(
                "UPDATE user_profile SET value = ?, source = ?, "
                "confidence = ?, project_id = ?, "
                "updated_at = datetime('now') WHERE key = ?",
                (value, source, confidence, project, key),
            )
        else:
            conn.execute(
                "INSERT INTO user_profile "
                "(key, value, source, confidence, project_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, value, source, confidence, project),
            )


def get_user_preference(key: str) -> str | None:
    """Get a single user preference value."""
    with wisdom_db() as conn:
        row = conn.execute(
            "SELECT value FROM user_profile WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else None


def first_time_questions() -> list[dict]:
    """Return the two first-time user questions.

    These are returned as structured data for the MCP tool to present.
    """
    return [
        {
            "id": "project_type",
            "question": "Are we starting something new, or working on an existing codebase?",
            "options": ["new", "existing"],
            "maps_to": "default_project_type",
        },
        {
            "id": "update_frequency",
            "question": "How do you want updates? Every sprint, daily, or only when I need you?",
            "options": ["daily", "weekly", "on_demand"],
            "maps_to": "update_frequency",
        },
    ]


def process_first_time_answers(answers: dict) -> None:
    """Store first-time answers in user profile."""
    if "project_type" in answers:
        update_user_profile(
            "default_project_type",
            answers["project_type"],
            source="explicit",
        )
    if "update_frequency" in answers:
        update_user_profile(
            "update_frequency",
            answers["update_frequency"],
            source="explicit",
        )


# ── Private helpers ──


def _has_source_files(repo_path: str) -> bool:
    """Check if path contains source code files."""
    p = Path(repo_path)
    if not p.exists():
        return False

    # Check top-level and one level deep
    for child in p.iterdir():
        if child.suffix in _SOURCE_EXTENSIONS:
            return True
        if child.is_dir() and not child.name.startswith("."):
            for grandchild in child.iterdir():
                if grandchild.suffix in _SOURCE_EXTENSIONS:
                    return True
    return False
