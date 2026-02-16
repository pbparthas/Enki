"""skills.py — Optional integrations with external tools.

Guards for optional dependencies that may not be installed.
All code paths that call these tools must check the availability flag first.
"""

import logging

logger = logging.getLogger(__name__)

# ── Prism (enhanced code review) ──

try:
    from prism import analyze  # type: ignore[import-untyped]
    PRISM_AVAILABLE = True
except ImportError:
    analyze = None  # type: ignore[assignment]
    PRISM_AVAILABLE = False
    logger.info("Prism not installed — enhanced code review disabled")


def review_with_prism(files: list[str], project_path: str = ".") -> dict:
    """Run Prism code review on the given files.

    Returns review results dict, or a skip notice if Prism is not installed.
    """
    if not PRISM_AVAILABLE:
        return {"skipped": True, "reason": "Prism not installed"}

    results = analyze(  # type: ignore[misc]
        files=files,
        project_path=project_path,
        mode="static",
    )
    return {
        "skipped": False,
        "files_reviewed": len(files),
        "issues": results.get("issues", []) if isinstance(results, dict) else [],
        "summary": results.get("summary", "") if isinstance(results, dict) else str(results),
    }
