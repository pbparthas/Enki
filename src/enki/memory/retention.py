"""retention.py — Decay scoring + maintenance + freshness checks.

Recall-based decay: recalled beads stay hot, unused beads fade.
Decay reduces search ranking but NEVER deletes.
Only Gemini can flag for deletion.

Thresholds (from config):
    Not recalled in 90 days: weight 0.5
    Not recalled in 180 days: weight 0.2
    Not recalled in 365 days: weight 0.1
    Starred or preference: always 1.0
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from enki.config import get_config
from enki.db import wisdom_db


def run_decay() -> dict:
    """Run decay pass on all notes in wisdom.db.

    Returns stats dict with counts of notes affected at each threshold.
    """
    config = get_config()
    thresholds = config["memory"]["decay_thresholds"]
    now = datetime.now()

    stats = {"unchanged": 0, "d30": 0, "d90": 0, "d180": 0, "d365": 0}

    with wisdom_db() as conn:
        beads = conn.execute(
            "SELECT id, last_accessed, starred, category, weight FROM notes"
        ).fetchall()

        for bead in beads:
            # Never decay starred beads, preferences, or protected categories
            if bead["starred"] or bead["category"] in ("preference", "enforcement", "gate", "pattern"):
                stats["unchanged"] += 1
                continue

            last = bead["last_accessed"]
            if not last:
                # Never accessed — apply maximum decay
                _set_weight(conn, bead["id"], thresholds["d365"])
                stats["d365"] += 1
                continue

            try:
                last_dt = datetime.fromisoformat(last)
            except (ValueError, TypeError):
                stats["unchanged"] += 1
                continue

            days_since = (now - last_dt).days

            if days_since >= 365:
                new_weight = thresholds["d365"]
                stats["d365"] += 1
            elif days_since >= 180:
                new_weight = thresholds["d180"]
                stats["d180"] += 1
            elif days_since >= 90:
                new_weight = thresholds["d90"]
                stats["d90"] += 1
            elif days_since >= 30:
                # 30-day threshold: still hot but starting to cool
                new_weight = 1.0
                stats["d30"] += 1
            else:
                new_weight = 1.0
                stats["unchanged"] += 1

            if abs(bead["weight"] - new_weight) > 0.01:
                _set_weight(conn, bead["id"], new_weight)

    return stats


def refresh_weight(bead_id: str) -> None:
    """Reset weight to 1.0 when a bead is recalled."""
    with wisdom_db() as conn:
        conn.execute(
            "UPDATE notes SET weight = 1.0, last_accessed = datetime('now') "
            "WHERE id = ?",
            (bead_id,),
        )


def get_decay_stats() -> dict:
    """Get current decay distribution stats."""
    with wisdom_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        hot = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE weight >= 0.9"
        ).fetchone()[0]
        warm = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE weight >= 0.4 AND weight < 0.9"
        ).fetchone()[0]
        cold = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE weight >= 0.1 AND weight < 0.4"
        ).fetchone()[0]
        frozen = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE weight < 0.1"
        ).fetchone()[0]
        starred = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE starred = 1"
        ).fetchone()[0]

    return {
        "total": total,
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "frozen": frozen,
        "starred": starred,
    }


def process_flagged_deletions() -> dict:
    """Delete notes tagged with gemini_flagged (Abzu Spec §9)."""
    stats = {"deleted": 0, "by_category": {}}

    with wisdom_db() as conn:
        flagged = conn.execute(
            "SELECT id, category, context_description FROM notes "
            "WHERE tags LIKE '%gemini_flagged%'"
        ).fetchall()

        for bead in flagged:
            category = bead["category"]
            # Never delete protected categories even if Gemini flags them
            if category in ("enforcement", "gate", "pattern"):
                continue
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
            conn.execute("DELETE FROM notes WHERE id = ?", (bead["id"],))
            stats["deleted"] += 1

    return stats


def calculate_weight(
    last_accessed: str | None,
    starred: bool,
    category: str,
) -> float:
    """Calculate decay weight for a bead (Abzu Spec §9).

    Thresholds:
    - Recalled in last 30 days: 1.0
    - Not recalled in 90 days: 0.5
    - Not recalled in 180 days: 0.2
    - Not recalled in 365 days: 0.1
    - Starred or preference: always 1.0
    """
    if starred or category in ("preference", "enforcement", "gate", "pattern"):
        return 1.0

    if not last_accessed:
        config = get_config()
        return config["memory"]["decay_thresholds"]["d365"]

    try:
        last_dt = datetime.fromisoformat(last_accessed)
    except (ValueError, TypeError):
        return 1.0

    config = get_config()
    thresholds = config["memory"]["decay_thresholds"]
    days_since = (datetime.now() - last_dt).days

    if days_since >= 365:
        return thresholds["d365"]
    elif days_since >= 180:
        return thresholds["d180"]
    elif days_since >= 90:
        return thresholds["d90"]
    else:
        return 1.0


# ── Freshness checks ──

# Regex patterns for version references in bead content
_VERSION_PATTERNS = [
    # "Node 18", "Python 3.11", "React 18.2", "PostgreSQL 15"
    re.compile(r'\b(Node|Python|React|Vue|Angular|Django|Flask|PostgreSQL|MySQL|Ruby|Go|Rust|Java|PHP|Swift|Kotlin)\s+(\d+(?:\.\d+)*)\b', re.IGNORECASE),
    # "v2.3.1", "version 4.18"
    re.compile(r'\b(?:v|version\s*)(\d+(?:\.\d+)+)\b', re.IGNORECASE),
    # "express@4.18.2", "lodash@4.17"
    re.compile(r'\b([a-z][a-z0-9_-]*)@(\d+(?:\.\d+)*)\b'),
    # "node:18-alpine", "python:3.11-slim"
    re.compile(r'\b([a-z][a-z0-9_-]*):(\d+(?:\.\d+)*)(?:-[a-z]+)?\b'),
]


def _extract_project_versions(project_path: Path | None) -> dict[str, str]:
    """Extract current versions from project files."""
    versions: dict[str, str] = {}
    if not project_path or not project_path.exists():
        return versions

    # package.json
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for name, ver in deps.items():
                # Strip ^ ~ >= etc.
                clean = re.sub(r'^[^0-9]*', '', str(ver))
                if clean:
                    versions[name.lower()] = clean
            # Node version from engines
            engines = data.get("engines", {})
            if "node" in engines:
                clean = re.sub(r'^[^0-9]*', '', str(engines["node"]))
                if clean:
                    versions["node"] = clean
        except (json.JSONDecodeError, OSError):
            pass

    # requirements.txt
    req_txt = project_path / "requirements.txt"
    if req_txt.exists():
        try:
            for line in req_txt.read_text().splitlines():
                line = line.strip()
                if "==" in line:
                    name, ver = line.split("==", 1)
                    versions[name.strip().lower()] = ver.strip()
        except OSError:
            pass

    # pyproject.toml — simple regex extraction
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            # Look for python version
            m = re.search(r'requires-python\s*=\s*"[><=]*(\d+\.\d+)', content)
            if m:
                versions["python"] = m.group(1)
        except OSError:
            pass

    # .node-version, .python-version, .tool-versions
    for vfile, key in [(".node-version", "node"), (".python-version", "python")]:
        p = project_path / vfile
        if p.exists():
            try:
                ver = p.read_text().strip()
                if ver:
                    versions[key] = ver
            except OSError:
                pass

    tool_versions = project_path / ".tool-versions"
    if tool_versions.exists():
        try:
            for line in tool_versions.read_text().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    versions[parts[0].lower()] = parts[1]
        except OSError:
            pass

    return versions


def check_freshness(project_path: Path | None = None) -> list[dict]:
    """Scan notes for versioned references and flag potentially stale ones.

    Cross-references against project files (package.json, requirements.txt, etc.)
    to detect outdated version references.

    Returns list of dicts with bead_id, detected_version, status, etc.
    """
    project_versions = _extract_project_versions(project_path)
    results = []

    with wisdom_db() as conn:
        beads = conn.execute(
            "SELECT id, content, category FROM notes"
        ).fetchall()

        # Load previous checks
        existing_checks = {}
        try:
            checks = conn.execute(
                "SELECT bead_id, detected_version, status, checked_at "
                "FROM freshness_checks"
            ).fetchall()
            for c in checks:
                existing_checks[(c["bead_id"], c["detected_version"])] = dict(c)
        except Exception:
            pass  # Table might not exist yet

    for bead in beads:
        bead = dict(bead)
        content = bead["content"]

        for pattern in _VERSION_PATTERNS:
            for match in pattern.finditer(content):
                groups = match.groups()
                if len(groups) == 2:
                    tool_name, version = groups[0], groups[1]
                else:
                    tool_name, version = "unknown", groups[0]

                detected = f"{tool_name} {version}"
                tool_key = tool_name.lower()

                # Check against project versions
                current_version = project_versions.get(tool_key)
                prev_check = existing_checks.get((bead["id"], detected))

                if prev_check and prev_check["status"] == "dismissed":
                    continue  # Already reviewed by user

                if current_version:
                    if current_version.startswith(version) or version.startswith(current_version):
                        status = "current"
                    else:
                        status = "stale"
                else:
                    status = "unknown"

                results.append({
                    "bead_id": bead["id"],
                    "content_excerpt": content[:80],
                    "detected_version": detected,
                    "current_version": current_version,
                    "last_checked": prev_check["checked_at"] if prev_check else None,
                    "status": status,
                })

    # Record checks
    with wisdom_db() as conn:
        for r in results:
            conn.execute(
                "INSERT OR REPLACE INTO freshness_checks "
                "(bead_id, detected_version, current_version, status) "
                "VALUES (?, ?, ?, ?)",
                (r["bead_id"], r["detected_version"],
                 r["current_version"], r["status"]),
            )

    return results


def dismiss_freshness(bead_id: str, detected_version: str | None = None) -> bool:
    """Mark a freshness check as dismissed (user reviewed it).

    If detected_version is None, dismiss all checks for the bead.
    Returns True if any rows were updated.
    """
    with wisdom_db() as conn:
        if detected_version:
            cursor = conn.execute(
                "UPDATE freshness_checks SET status = 'dismissed' "
                "WHERE bead_id = ? AND detected_version = ?",
                (bead_id, detected_version),
            )
        else:
            cursor = conn.execute(
                "UPDATE freshness_checks SET status = 'dismissed' "
                "WHERE bead_id = ?",
                (bead_id,),
            )
        return cursor.rowcount > 0


def _set_weight(conn, bead_id: str, weight: float) -> None:
    """Set weight for a bead."""
    conn.execute(
        "UPDATE notes SET weight = ? WHERE id = ?", (weight, bead_id)
    )
