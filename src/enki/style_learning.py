"""Working Style Learning - Auto-detect user preferences from session patterns.

Analyzes session history to learn:
- Coding patterns (naming, structure, comments)
- Tool preferences (editors, test frameworks, linters)
- Decision patterns (what choices are made repeatedly)
- Time patterns (when most productive)
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .db import get_db, init_db
from .beads import create_bead, BeadType


@dataclass
class StylePattern:
    """A detected working style pattern."""
    category: str  # naming, testing, structure, timing, tools
    pattern: str
    confidence: float  # 0.0 to 1.0
    evidence_count: int
    examples: list[str]


def analyze_session_patterns(
    days: int = 30,
    project: Optional[str] = None,
) -> list[StylePattern]:
    """Analyze session history to detect working style patterns.

    Args:
        days: Number of days to analyze
        project: Optional project filter

    Returns:
        List of detected patterns with confidence scores
    """
    init_db()
    db = get_db()

    patterns = []

    # Analyze file editing patterns
    file_patterns = _analyze_file_patterns(db, days, project)
    patterns.extend(file_patterns)

    # Analyze timing patterns
    timing_patterns = _analyze_timing_patterns(db, days, project)
    patterns.extend(timing_patterns)

    # Analyze tool usage patterns
    tool_patterns = _analyze_tool_patterns(db, days, project)
    patterns.extend(tool_patterns)

    # Analyze decision patterns from beads
    decision_patterns = _analyze_decision_patterns(db, days, project)
    patterns.extend(decision_patterns)

    return patterns


def _analyze_file_patterns(
    db,
    days: int,
    project: Optional[str],
) -> list[StylePattern]:
    """Analyze file editing patterns."""
    patterns = []

    # Get violation data which includes file paths
    query = """
        SELECT file_path, tool, COUNT(*) as count
        FROM violations
        WHERE timestamp > datetime('now', ?)
        AND file_path IS NOT NULL
    """
    params = [f"-{days} days"]

    if project:
        query += " AND file_path LIKE ?"
        params.append(f"%{project}%")

    query += " GROUP BY file_path, tool ORDER BY count DESC LIMIT 100"

    rows = db.execute(query, params).fetchall()

    # Analyze file extensions
    extensions = Counter()
    for row in rows:
        if row["file_path"]:
            ext = Path(row["file_path"]).suffix
            if ext:
                extensions[ext] += row["count"]

    # Top file types
    if extensions:
        top_ext = extensions.most_common(3)
        if top_ext:
            total = sum(extensions.values())
            for ext, count in top_ext:
                confidence = count / total
                if confidence > 0.1:  # At least 10% of edits
                    patterns.append(StylePattern(
                        category="file_types",
                        pattern=f"Frequently edits {ext} files",
                        confidence=confidence,
                        evidence_count=count,
                        examples=[ext],
                    ))

    # Analyze directory patterns
    directories = Counter()
    for row in rows:
        if row["file_path"]:
            parts = Path(row["file_path"]).parts
            if len(parts) > 1:
                # Get first meaningful directory
                for part in parts:
                    if part not in (".", "..", "src", "lib"):
                        directories[part] += row["count"]
                        break

    if directories:
        top_dirs = directories.most_common(3)
        total = sum(directories.values())
        for dir_name, count in top_dirs:
            confidence = count / total
            if confidence > 0.15:
                patterns.append(StylePattern(
                    category="structure",
                    pattern=f"Works frequently in {dir_name}/ directory",
                    confidence=confidence,
                    evidence_count=count,
                    examples=[dir_name],
                ))

    return patterns


def _analyze_timing_patterns(
    db,
    days: int,
    project: Optional[str],
) -> list[StylePattern]:
    """Analyze when user is most active."""
    patterns = []

    # Get session timestamps
    query = """
        SELECT started_at
        FROM sessions
        WHERE started_at > datetime('now', ?)
    """
    params = [f"-{days} days"]

    if project:
        query += " AND project_id = ?"
        params.append(project)

    rows = db.execute(query, params).fetchall()

    if not rows:
        return patterns

    # Analyze hours
    hours = Counter()
    for row in rows:
        if row["started_at"]:
            try:
                dt = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
                hours[dt.hour] += 1
            except:
                pass

    if hours:
        total = sum(hours.values())

        # Morning (6-12)
        morning = sum(hours.get(h, 0) for h in range(6, 12))
        # Afternoon (12-18)
        afternoon = sum(hours.get(h, 0) for h in range(12, 18))
        # Evening (18-24)
        evening = sum(hours.get(h, 0) for h in range(18, 24))
        # Night (0-6)
        night = sum(hours.get(h, 0) for h in range(0, 6))

        time_slots = [
            ("morning (6am-12pm)", morning),
            ("afternoon (12pm-6pm)", afternoon),
            ("evening (6pm-midnight)", evening),
            ("night (midnight-6am)", night),
        ]

        # Find dominant time
        max_slot = max(time_slots, key=lambda x: x[1])
        if max_slot[1] > 0:
            confidence = max_slot[1] / total
            if confidence > 0.4:  # At least 40% in this slot
                patterns.append(StylePattern(
                    category="timing",
                    pattern=f"Most active during {max_slot[0]}",
                    confidence=confidence,
                    evidence_count=max_slot[1],
                    examples=[f"{max_slot[1]} sessions"],
                ))

    return patterns


def _analyze_tool_patterns(
    db,
    days: int,
    project: Optional[str],
) -> list[StylePattern]:
    """Analyze tool usage patterns."""
    patterns = []

    # Analyze interceptions to see what tools are used
    query = """
        SELECT tool, COUNT(*) as count
        FROM interceptions
        WHERE timestamp > datetime('now', ?)
        AND result = 'allowed'
    """
    params = [f"-{days} days"]

    query += " GROUP BY tool ORDER BY count DESC"

    rows = db.execute(query, params).fetchall()

    if rows:
        total = sum(row["count"] for row in rows)
        for row in rows:
            confidence = row["count"] / total
            if confidence > 0.2:  # At least 20% of tool usage
                patterns.append(StylePattern(
                    category="tools",
                    pattern=f"Frequently uses {row['tool']} tool",
                    confidence=confidence,
                    evidence_count=row["count"],
                    examples=[row["tool"]],
                ))

    return patterns


def _analyze_decision_patterns(
    db,
    days: int,
    project: Optional[str],
) -> list[StylePattern]:
    """Analyze decision patterns from beads."""
    patterns = []

    # Get decision beads
    query = """
        SELECT content, tags
        FROM beads
        WHERE type = 'decision'
        AND created_at > datetime('now', ?)
        AND superseded_by IS NULL
    """
    params = [f"-{days} days"]

    if project:
        query += " AND project = ?"
        params.append(project)

    rows = db.execute(query, params).fetchall()

    if not rows:
        return patterns

    # Look for common themes in decisions
    themes = Counter()
    theme_examples = defaultdict(list)

    # Common decision keywords
    keywords = {
        "testing": ["test", "tdd", "coverage", "unit", "integration"],
        "architecture": ["pattern", "solid", "dependency", "injection", "module"],
        "performance": ["cache", "optimize", "performance", "speed", "memory"],
        "security": ["auth", "security", "token", "encrypt", "permission"],
        "database": ["database", "sql", "query", "migration", "schema"],
    }

    for row in rows:
        content = (row["content"] or "").lower()
        for theme, words in keywords.items():
            if any(word in content for word in words):
                themes[theme] += 1
                if len(theme_examples[theme]) < 3:
                    theme_examples[theme].append(row["content"][:100])

    if themes:
        total = sum(themes.values())
        for theme, count in themes.most_common(3):
            if count >= 2:  # At least 2 decisions in this area
                confidence = min(count / total, 0.9)
                patterns.append(StylePattern(
                    category="decisions",
                    pattern=f"Frequently makes {theme}-related decisions",
                    confidence=confidence,
                    evidence_count=count,
                    examples=theme_examples[theme],
                ))

    return patterns


def learn_from_session(
    session_id: str,
    project_path: Optional[Path] = None,
) -> list[str]:
    """Extract learnings from a completed session.

    Called on session end to capture what was learned.

    Returns:
        List of bead IDs created for learnings
    """
    init_db()
    db = get_db()

    created_beads = []
    project = str(project_path) if project_path else None

    # Get session edits
    if project_path:
        edits_file = project_path / ".enki" / ".session_edits"
        if edits_file.exists():
            edits = edits_file.read_text().strip().split("\n")
            edits = [e for e in edits if e]

            if len(edits) >= 3:
                # Significant session - extract patterns
                extensions = Counter(Path(e).suffix for e in edits if Path(e).suffix)
                if extensions:
                    top_ext = extensions.most_common(1)[0]
                    if top_ext[1] >= 3:
                        bead = create_bead(
                            content=f"Session focused on {top_ext[0]} files ({top_ext[1]} edited)",
                            bead_type="pattern",
                            summary=f"Heavy {top_ext[0]} editing session",
                            project=project,
                            context=f"Extracted from session {session_id}",
                        )
                        created_beads.append(bead.id)

    # Check for violations that were later resolved
    violations = db.execute("""
        SELECT gate, reason, COUNT(*) as count
        FROM violations
        WHERE session_id = ?
        GROUP BY gate, reason
        HAVING count >= 2
    """, (session_id,)).fetchall()

    for v in violations:
        # Repeated violations suggest a learning opportunity
        bead = create_bead(
            content=f"Repeatedly hit {v['gate']} gate: {v['reason']}",
            bead_type="learning",
            summary=f"Gate {v['gate']} friction point",
            project=project,
            context=f"Extracted from session {session_id}",
            tags=["auto-extracted", "friction"],
        )
        created_beads.append(bead.id)

    return created_beads


def save_style_patterns(
    patterns: list[StylePattern],
    project: Optional[str] = None,
) -> list[str]:
    """Save detected patterns as beads.

    Returns:
        List of created bead IDs
    """
    created_beads = []

    for pattern in patterns:
        if pattern.confidence >= 0.3:  # Only save patterns with decent confidence
            content = f"{pattern.pattern}\n\nConfidence: {pattern.confidence:.0%}\nEvidence: {pattern.evidence_count} occurrences"
            if pattern.examples:
                content += f"\nExamples: {', '.join(pattern.examples[:3])}"

            bead = create_bead(
                content=content,
                bead_type="pattern",
                summary=pattern.pattern,
                project=project,
                context=f"Auto-detected working style pattern ({pattern.category})",
                tags=["auto-detected", "working-style", pattern.category],
            )
            created_beads.append(bead.id)

    return created_beads


def get_style_summary(
    project: Optional[str] = None,
    days: int = 30,
) -> str:
    """Get a summary of detected working style patterns.

    Returns:
        Formatted string describing the user's working style
    """
    patterns = analyze_session_patterns(days=days, project=project)

    if not patterns:
        return "Not enough data to detect working style patterns yet."

    lines = ["## Your Working Style\n"]

    # Group by category
    by_category = defaultdict(list)
    for p in patterns:
        by_category[p.category].append(p)

    category_names = {
        "file_types": "File Preferences",
        "structure": "Project Structure",
        "timing": "Work Schedule",
        "tools": "Tool Usage",
        "decisions": "Decision Patterns",
    }

    for category, category_patterns in by_category.items():
        if category_patterns:
            lines.append(f"### {category_names.get(category, category.title())}")
            for p in sorted(category_patterns, key=lambda x: -x.confidence):
                lines.append(f"- {p.pattern} ({p.confidence:.0%} confidence)")
            lines.append("")

    return "\n".join(lines)
