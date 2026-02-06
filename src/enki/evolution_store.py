"""Evolution state I/O — persistence layer for EVOLUTION.md files.

P2-12: Split from evolution.py (SRP). Handles:
- Path resolution (local/global/promotion)
- Load/save/init state from Markdown + embedded JSON
- Pruning and archival
- Two-tier promotion (local → candidates file)
- Migration
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import get_db
from .path_utils import atomic_write
from .session import ensure_project_enki_dir

logger = logging.getLogger(__name__)


# --- Path Resolution ---


def get_evolution_path(project_path: Path = None) -> Path:
    """Get path to local (per-project) EVOLUTION.md file.

    Backward-compatible alias for get_local_evolution_path.
    """
    return get_local_evolution_path(project_path)


def get_local_evolution_path(project_path: Path = None) -> Path:
    """Get per-project evolution path — written during sessions."""
    project_path = project_path or Path.cwd()
    return project_path / ".enki" / "EVOLUTION.md"


def get_global_evolution_path() -> Path:
    """Get cross-project evolution path — written by promotion only."""
    return Path.home() / ".enki" / "EVOLUTION.md"


def get_promotion_candidates_path() -> Path:
    """Get path to promotion candidates file (for human review)."""
    return Path.home() / ".enki" / "promotion_candidates.json"


# --- Init / Load / Save ---


def init_evolution_log(project_path: Path = None):
    """Initialize EVOLUTION.md if it doesn't exist."""
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    evolution_path = get_evolution_path(project_path)
    if not evolution_path.exists():
        content = """# Enki Self-Evolution Log

This file tracks Enki's self-corrections and evolution over time.
Enki analyzes her own patterns and adjusts her behavior to improve outcomes.

## Active Corrections

(No active corrections yet)

## Correction History

(No corrections yet)

## Gate Adjustments

(No adjustments yet)

<!-- ENKI_EVOLUTION
{
  "corrections": [],
  "adjustments": [],
  "last_review": null
}
-->
"""
        evolution_path.write_text(content)


def load_evolution_state(project_path: Path = None) -> dict:
    """Load evolution state from EVOLUTION.md.

    Args:
        project_path: Project directory path

    Returns:
        Dict with corrections, adjustments, last_review
    """
    project_path = project_path or Path.cwd()
    evolution_path = get_evolution_path(project_path)

    if not evolution_path.exists():
        init_evolution_log(project_path)
        return {"corrections": [], "adjustments": [], "last_review": None}

    content = evolution_path.read_text()

    # Extract JSON state
    match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        return {"corrections": [], "adjustments": [], "last_review": None}

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"corrections": [], "adjustments": [], "last_review": None}


def save_evolution_state(state: dict, project_path: Path = None):
    """Save evolution state to EVOLUTION.md.

    Args:
        state: Evolution state dict
        project_path: Project directory path
    """
    project_path = project_path or Path.cwd()
    ensure_project_enki_dir(project_path)

    evolution_path = get_evolution_path(project_path)

    # Build EVOLUTION.md content
    lines = [
        "# Enki Self-Evolution Log",
        "",
        "This file tracks Enki's self-corrections and evolution over time.",
        "Enki analyzes her own patterns and adjusts her behavior to improve outcomes.",
        "",
        "## Active Corrections",
        "",
    ]

    corrections = state.get("corrections", [])
    active_corrections = [c for c in corrections if c.get("status") == "active"]
    proposed_corrections = [c for c in corrections if c.get("status") == "proposed"]

    if active_corrections:
        for c in active_corrections:
            lines.append(f"### {c['date']}: {c['description'][:50]}")
            lines.append(f"**Pattern Detected**: {c['pattern_type']}")
            lines.append(f"**Frequency**: {c['frequency']} occurrences")
            lines.append(f"**Impact**: {c['impact']}")
            lines.append(f"**Correction**: {c['correction']}")
            lines.append(f"**Status**: {c['status']}")
            lines.append("")
    else:
        lines.append("(No active corrections)")
        lines.append("")

    # Proposed corrections (awaiting human approval)
    lines.append("## Proposed Corrections (Pending Approval)")
    lines.append("")

    if proposed_corrections:
        for c in proposed_corrections:
            lines.append(f"- **{c.get('id', '?')}**: {c['description'][:50]} — `enki evolution approve {c.get('id', '?')}`")
        lines.append("")
    else:
        lines.append("(No pending proposals)")
        lines.append("")

    lines.append("## Correction History")
    lines.append("")

    historical = [c for c in corrections if c.get("status") not in ("active", "proposed")]
    if historical:
        for c in historical[-10:]:  # Last 10
            effective = "✓" if c.get("effective") else "✗" if c.get("effective") is False else "?"
            lines.append(f"- [{effective}] {c['date']}: {c['description'][:50]} ({c['status']})")
        lines.append("")
    else:
        lines.append("(No corrections yet)")
        lines.append("")

    lines.append("## Gate Adjustments")
    lines.append("")

    adjustments = state.get("adjustments", [])
    if adjustments:
        lines.append("| Gate | Type | Description | Active |")
        lines.append("|------|------|-------------|--------|")
        for a in adjustments[-10:]:
            active = "Yes" if a.get("active", True) else "No"
            lines.append(f"| {a['gate']} | {a['adjustment_type']} | {a['description'][:30]} | {active} |")
        lines.append("")
    else:
        lines.append("(No adjustments yet)")
        lines.append("")

    # Add JSON state
    lines.append("<!-- ENKI_EVOLUTION")
    lines.append(json.dumps(state, indent=2))
    lines.append("-->")

    # P1-03 / P2-17: Atomic write via shared helper
    with atomic_write(evolution_path) as f:
        f.write("\n".join(lines))


def _save_evolution_to_path(state: dict, evolution_path: Path):
    """Save evolution state to a specific path.

    Args:
        state: Evolution state dict
        evolution_path: Path to EVOLUTION.md file
    """
    evolution_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Enki Self-Evolution Log",
        "",
        "This file tracks Enki's self-corrections and evolution over time.",
        "",
        "## Active Corrections",
        "",
    ]

    corrections = state.get("corrections", [])
    active_corrections = [c for c in corrections if c.get("status") == "active"]

    if active_corrections:
        for c in active_corrections:
            source = f" (from {c['source_project']})" if c.get("source_project") else ""
            lines.append(f"### {c.get('date', '?')}: {c.get('description', '')[:50]}{source}")
            lines.append(f"**Pattern**: {c.get('pattern_type', '')}")
            lines.append(f"**Correction**: {c.get('correction', '')}")
            lines.append(f"**Status**: {c.get('status', '')}")
            lines.append("")
    else:
        lines.append("(No active corrections)")
        lines.append("")

    lines.append("## Gate Adjustments")
    lines.append("")

    adjustments = state.get("adjustments", [])
    active = [a for a in adjustments if a.get("active", True)]
    if active:
        lines.append("| Gate | Type | Description | Source |")
        lines.append("|------|------|-------------|--------|")
        for a in active[-15:]:
            source = a.get("source_project", "")
            lines.append(f"| {a['gate']} | {a['adjustment_type']} | {a['description'][:30]} | {source} |")
        lines.append("")
    else:
        lines.append("(No adjustments)")
        lines.append("")

    # Embed JSON state
    lines.append("<!-- ENKI_EVOLUTION")
    lines.append(json.dumps(state, indent=2))
    lines.append("-->")

    # P1-03 / P2-17: Atomic write via shared helper
    with atomic_write(evolution_path) as f:
        f.write("\n".join(lines))


# --- Two-Tier: Migration & Promotion ---


def migrate_per_project_evolution(project_path: Path):
    """One-time migration: mark existing per-project EVOLUTION.md as local.

    Idempotent. Checks for a .migrated marker to avoid re-running.
    Does NOT merge into global — only promotion does that.

    Args:
        project_path: Project directory path
    """
    local_path = get_local_evolution_path(project_path)
    marker = local_path.parent / "EVOLUTION_MIGRATED"

    if marker.exists():
        return  # already migrated

    if local_path.exists():
        # Mark as migrated — local file stays, promotion handles the rest
        marker.write_text(datetime.now().isoformat())


def promote_to_global(project_path: Path) -> dict:
    """Write promotion candidates — does NOT apply to global EVOLUTION.md.

    Global evolution requires fresh-context human review (SPEC Part 13).
    This function writes candidates to ~/.enki/promotion_candidates.json
    for human review. No mechanical promotion.

    Args:
        project_path: Project directory path

    Returns:
        {"candidates_written": int, "skipped_duplicate": int, "skipped_status": int}
    """
    result = {"candidates_written": 0, "skipped_duplicate": 0, "skipped_status": 0}

    local_state = load_evolution_state(project_path)
    candidates_path = get_promotion_candidates_path()
    candidates_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing candidates
    existing_candidates = []
    if candidates_path.exists():
        try:
            existing_candidates = json.loads(candidates_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing_candidates = []

    # Build dedup key set from existing candidates
    existing_keys = set()
    for c in existing_candidates:
        key = (c.get("type", ""), c.get("pattern_type", ""), c.get("correction", ""),
               c.get("gate", ""), c.get("adjustment_type", ""))
        existing_keys.add(key)

    project_name = project_path.name if project_path else "unknown"

    # Write correction candidates (do NOT apply to global EVOLUTION.md)
    for c in local_state.get("corrections", []):
        status = c.get("status", "")
        if status not in ("effective", "active"):
            result["skipped_status"] += 1
            continue

        key = ("correction", c.get("pattern_type", ""), c.get("correction", ""), "", "")
        if key in existing_keys:
            result["skipped_duplicate"] += 1
            continue

        existing_candidates.append({
            "type": "correction",
            "id": c.get("id", ""),
            "date": c.get("date", ""),
            "pattern_type": c.get("pattern_type", ""),
            "description": c.get("description", ""),
            "frequency": c.get("frequency", 0),
            "correction": c.get("correction", ""),
            "source_project": project_name,
            "proposed_at": datetime.now().isoformat(),
            "status": "pending_review",
        })
        existing_keys.add(key)
        result["candidates_written"] += 1

    # Write adjustment candidates (do NOT apply to global EVOLUTION.md)
    for a in local_state.get("adjustments", []):
        if not a.get("active", True):
            result["skipped_status"] += 1
            continue

        key = ("adjustment", "", "", a.get("gate", ""), a.get("adjustment_type", ""))
        if key in existing_keys:
            result["skipped_duplicate"] += 1
            continue

        existing_candidates.append({
            "type": "adjustment",
            "gate": a.get("gate", ""),
            "adjustment_type": a.get("adjustment_type", ""),
            "description": a.get("description", ""),
            "source_project": project_name,
            "proposed_at": datetime.now().isoformat(),
            "status": "pending_review",
        })
        existing_keys.add(key)
        result["candidates_written"] += 1

    # Save candidates file (NOT global EVOLUTION.md)
    if result["candidates_written"] > 0:
        candidates_path.write_text(json.dumps(existing_candidates, indent=2))

    return result


# --- Pruning ---


def prune_local_evolution(project_path: Path):
    """Prune local (per-project) evolution state.

    - Keep last 30 corrections and 15 adjustments
    - Archive completed/reverted corrections older than 90 days

    Args:
        project_path: Project directory path
    """
    state = load_evolution_state(project_path)
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()

    corrections = state.get("corrections", [])
    adjustments = state.get("adjustments", [])

    # Separate active from archivable
    archivable = [
        c for c in corrections
        if c.get("status") in ("effective", "reverted")
        and c.get("date", "") < cutoff[:10]
    ]
    keep = [
        c for c in corrections
        if c not in archivable
    ]

    # Archive old corrections
    if archivable:
        archive_path = project_path / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            f.write(f"\n## Archived {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for c in archivable:
                f.write(f"- [{c.get('status')}] {c.get('date')}: {c.get('description', '')[:60]}\n")

    # P2-06: Archive overflow instead of deleting
    if len(keep) > 30:
        overflow = keep[:-30]
        archive_path = project_path / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            if not archivable:  # Only write header if we didn't already
                f.write(f"\n## Archived {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for c in overflow:
                f.write(f"- [overflow] {c.get('date', '?')}: {c.get('description', '')[:60]}\n")

    if len(adjustments) > 15:
        overflow_adj = adjustments[:-15]
        archive_path = project_path / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            f.write(f"\n## Adjustments Archived {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for a in overflow_adj:
                f.write(f"- [{a.get('adjustment_type', '?')}] {a.get('gate', '?')}: {a.get('description', '')[:60]}\n")

    state["corrections"] = keep[-30:]
    state["adjustments"] = adjustments[-15:]

    save_evolution_state(state, project_path)


def prune_global_evolution():
    """Prune global evolution state.

    - Archive reverted entries older than 180 days
    - Applied/acknowledged entries stay indefinitely
    """
    global_path = get_global_evolution_path()
    if not global_path.exists():
        return

    content = global_path.read_text()
    match = re.search(r'<!-- ENKI_EVOLUTION\n(.*?)\n-->', content, re.DOTALL)
    if not match:
        return

    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return

    cutoff = (datetime.now() - timedelta(days=180)).isoformat()

    corrections = state.get("corrections", [])
    archivable = [
        c for c in corrections
        if c.get("status") == "reverted"
        and c.get("date", "") < cutoff[:10]
    ]

    if archivable:
        archive_path = Path.home() / ".enki" / "EVOLUTION_ARCHIVE.md"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a") as f:
            f.write(f"\n## Global Archive {datetime.now().strftime('%Y-%m-%d')}\n\n")
            for c in archivable:
                source = f" (from {c.get('source_project', '?')})" if c.get("source_project") else ""
                f.write(f"- [{c.get('status')}] {c.get('date')}: {c.get('description', '')[:60]}{source}\n")

        state["corrections"] = [c for c in corrections if c not in archivable]
        _save_evolution_to_path(state, global_path)
