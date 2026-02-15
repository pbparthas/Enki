"""config.py — Configuration loading from enki.toml."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

ENKI_ROOT = Path.home() / ".enki"
CONFIG_PATH = ENKI_ROOT / "config" / "enki.toml"

_DEFAULTS = {
    "general": {
        "version": "3.0",
    },
    "memory": {
        "fts5_min_score": 0.3,
        "session_summary_max_tokens": {
            "minimal": 1500,
            "standard": 4000,
            "full": 8000,
        },
        "decay_thresholds": {
            "d90": 0.5,
            "d180": 0.2,
            "d365": 0.1,
        },
        "max_final_summaries_per_project": 5,
    },
    "gates": {
        "max_parallel_tasks": 2,
        "nudge_tool_call_threshold": 30,
    },
    "gemini": {
        "review_cadence": "quarterly",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursing into nested dicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config() -> dict:
    """Load config from enki.toml, merged with defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user_config = tomllib.load(f)
        return _deep_merge(_DEFAULTS, user_config)
    return _DEFAULTS.copy()


def ensure_config():
    """Create default enki.toml if it doesn't exist."""
    if CONFIG_PATH.exists():
        return

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        '[general]\nversion = "3.0"\n\n'
        "[memory]\n"
        "fts5_min_score = 0.3\n\n"
        "[memory.session_summary_max_tokens]\n"
        "minimal = 1500\n"
        "standard = 4000\n"
        "full = 8000\n\n"
        "[memory.decay_thresholds]\n"
        "d90 = 0.5\n"
        "d180 = 0.2\n"
        "d365 = 0.1\n\n"
        "[gates]\n"
        "max_parallel_tasks = 2\n"
        "nudge_tool_call_threshold = 30\n\n"
        "[gemini]\n"
        'review_cadence = "quarterly"\n'
    )
