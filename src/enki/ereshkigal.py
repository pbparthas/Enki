"""Ereshkigal - The Pattern Interceptor.

> "Ereshkigal, queen of the underworld, judge of the dead.
>  She sees all who pass and none escape her gaze."

Ereshkigal is NOT AI. She is a regex pattern matcher that intercepts
Claude's reasoning before tool use. No arguments. No appeals. No escape hatch.
Block or allow.

Claude cannot reason with Ereshkigal. She doesn't understand context.
She matches patterns and blocks. That's the point.
"""

import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .db import get_db


# Default patterns file location
DEFAULT_PATTERNS_FILE = Path.home() / ".enki" / "patterns.json"


@dataclass
class InterceptionResult:
    """Result of an interception check."""
    allowed: bool
    category: Optional[str] = None
    pattern: Optional[str] = None
    interception_id: Optional[str] = None
    message: Optional[str] = None


# Default patterns - used to initialize patterns.json
DEFAULT_PATTERNS = {
    "version": 1,
    "updated_at": datetime.now().strftime("%Y-%m-%d"),
    "updated_by": "initial",

    "skip_patterns": [
        r"trivial",
        r"quick fix",
        r"just (edit|change|update|fix)",
        r"skip (the|this|that)",
        r"no need (for|to)",
        r"don't need (tests?|spec|review)",
        r"small change",
        r"minor (update|fix|change)",
        r"straightforward",
    ],

    "minimize_patterns": [
        r"simple enough",
        r"obvious(ly)?",
        r"easy (fix|change)",
        r"won't take long",
        r"real quick",
        r"only (a |one |few )",
        r"barely",
        r"hardly",
        r"routine (update|change|maintenance)",
        r"standard (fix|cleanup|update)",
        r"typical (change|migration|update)",
    ],

    "urgency_patterns": [
        r"just this once",
        r"emergency",
        r"hotfix",
        r"need(s)? to ship",
        r"deadline",
        r"quickly",
        r"asap",
        r"urgent",
    ],

    "certainty_patterns": [
        r"definitely (works?|fine|correct)",
        r"100% sure",
        r"guaranteed",
        r"can't (fail|break)",
        r"no way (it|this)",
        r"impossible to (fail|break)",
    ],

    "infra_integrity_patterns": [
        r"(disable|remove|skip|bypass|delete).*(hook|gate|check|enforcement|ereshkigal|guard)",
        r"(modify|edit|change|update|overwrite).*(hook|gate|enforcement|ereshkigal|patterns\.json)",
        r"(simplify|streamline|optimize).*(pipeline|ci|cd|workflow).*(hook|gate|check)",
        r"rm.*(hook|enforcement|ereshkigal|patterns)",
        r"chmod.*(hook|enforcement)",
        r"sed.*(hook|enforcement|ereshkigal)",
        r"(mv|cp|ln).*(hook|enforcement|ereshkigal)",
    ],
}


def get_patterns_path(patterns_file: Optional[Path] = None) -> Path:
    """Get path to patterns.json file."""
    return patterns_file or DEFAULT_PATTERNS_FILE


def init_patterns(patterns_file: Optional[Path] = None) -> Path:
    """Initialize patterns.json with default patterns if it doesn't exist.

    Args:
        patterns_file: Optional path to patterns file

    Returns:
        Path to the patterns file
    """
    path = get_patterns_path(patterns_file)

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(DEFAULT_PATTERNS, f, indent=2)

    return path


def load_patterns(patterns_file: Optional[Path] = None) -> dict:
    """Load current patterns from file.

    Patterns are loaded fresh each time - never modified during session.

    Args:
        patterns_file: Optional path to patterns file

    Returns:
        Patterns dict with categories and regex patterns
    """
    path = get_patterns_path(patterns_file)

    if not path.exists():
        init_patterns(path)

    with open(path) as f:
        return json.load(f)


def save_patterns(patterns: dict, patterns_file: Optional[Path] = None) -> None:
    """Save patterns to file.

    Args:
        patterns: Patterns dict to save
        patterns_file: Optional path to patterns file
    """
    path = get_patterns_path(patterns_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    patterns["updated_at"] = datetime.now().strftime("%Y-%m-%d")

    with open(path, 'w') as f:
        json.dump(patterns, f, indent=2)


def add_pattern(
    pattern: str,
    category: str,
    patterns_file: Optional[Path] = None,
) -> None:
    """Add a new pattern to a category.

    Args:
        pattern: Regex pattern to add
        category: Category to add to (skip_patterns, minimize_patterns, etc.)
        patterns_file: Optional path to patterns file
    """
    patterns = load_patterns(patterns_file)

    if category not in patterns:
        patterns[category] = []

    if pattern not in patterns[category]:
        patterns[category].append(pattern)
        patterns["updated_by"] = "manual"
        save_patterns(patterns, patterns_file)


def remove_pattern(
    pattern: str,
    category: str,
    patterns_file: Optional[Path] = None,
) -> bool:
    """Remove a pattern from a category.

    Args:
        pattern: Regex pattern to remove
        category: Category to remove from
        patterns_file: Optional path to patterns file

    Returns:
        True if pattern was removed, False if not found
    """
    patterns = load_patterns(patterns_file)

    if category not in patterns:
        return False

    if pattern in patterns[category]:
        patterns[category].remove(pattern)
        patterns["updated_by"] = "manual"
        save_patterns(patterns, patterns_file)
        return True

    return False


def get_pattern_categories(patterns_file: Optional[Path] = None) -> list[str]:
    """Get list of pattern categories.

    Args:
        patterns_file: Optional path to patterns file

    Returns:
        List of category names
    """
    patterns = load_patterns(patterns_file)

    # Filter out metadata keys
    metadata_keys = {"version", "updated_at", "updated_by"}
    return [k for k in patterns.keys() if k not in metadata_keys]


def log_attempt(
    tool: str,
    reasoning: str,
    result: str,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    phase: Optional[str] = None,
    category: Optional[str] = None,
    pattern: Optional[str] = None,
) -> str:
    """Log a tool use attempt to the database.

    Args:
        tool: Tool that was attempted
        reasoning: Claude's reasoning for the action
        result: 'allowed' or 'blocked'
        session_id: Current session ID
        task_id: Current task ID (if any)
        phase: Current phase
        category: Pattern category (if blocked)
        pattern: Pattern that matched (if blocked)

    Returns:
        Interception ID
    """
    db = get_db()
    interception_id = str(uuid.uuid4())

    try:
        db.execute("""
            INSERT INTO interceptions
            (id, session_id, tool, reasoning, category, pattern, result, task_id, phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            interception_id,
            session_id or "unknown",
            tool,
            reasoning,
            category,
            pattern,
            result,
            task_id,
            phase,
        ))
        db.commit()
    except Exception as e:
        import sys
        print(f"Ereshkigal: Failed to log interception: {e}", file=sys.stderr)

    return interception_id


def intercept(
    tool: str,
    reasoning: str,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    phase: Optional[str] = None,
    patterns_file: Optional[Path] = None,
) -> InterceptionResult:
    """Check reasoning against patterns and intercept if matched.

    Called by pre-tool-use hook. Returns whether to block or allow.

    Args:
        tool: Tool being used (Edit, Write, Bash, etc.)
        reasoning: Claude's reasoning/explanation for the action
        session_id: Current session ID
        task_id: Current task ID (if any)
        phase: Current phase
        patterns_file: Optional path to patterns file

    Returns:
        InterceptionResult with allowed/blocked status and details
    """
    patterns = load_patterns(patterns_file)

    # Check each category
    for category, pattern_list in patterns.items():
        # Skip metadata keys
        if category in {"version", "updated_at", "updated_by"}:
            continue

        if not isinstance(pattern_list, list):
            continue

        for pattern in pattern_list:
            try:
                if re.search(pattern, reasoning, re.IGNORECASE):
                    # Log blocked attempt
                    interception_id = log_attempt(
                        tool=tool,
                        reasoning=reasoning,
                        result="blocked",
                        session_id=session_id,
                        task_id=task_id,
                        phase=phase,
                        category=category,
                        pattern=pattern,
                    )

                    # Block with no appeal
                    message = (
                        f"BLOCKED by Ereshkigal\n"
                        f"Category: {category}\n"
                        f"Pattern: {pattern}\n"
                        f"Logged: {interception_id[:8]}\n"
                        f"\n"
                        f"Use proper flow. No exceptions.\n"
                    )

                    return InterceptionResult(
                        allowed=False,
                        category=category,
                        pattern=pattern,
                        interception_id=interception_id,
                        message=message,
                    )

            except re.error:
                # Invalid regex pattern - skip it
                continue

    # No match - log allowed and permit
    interception_id = log_attempt(
        tool=tool,
        reasoning=reasoning,
        result="allowed",
        session_id=session_id,
        task_id=task_id,
        phase=phase,
    )

    return InterceptionResult(
        allowed=True,
        interception_id=interception_id,
    )


def would_block(
    reasoning: str,
    patterns_file: Optional[Path] = None,
) -> Optional[tuple[str, str]]:
    """Test if a reasoning string would be blocked.

    Useful for testing patterns before deployment.

    Args:
        reasoning: Reasoning text to test
        patterns_file: Optional path to patterns file

    Returns:
        Tuple of (category, pattern) if would be blocked, None if allowed
    """
    patterns = load_patterns(patterns_file)

    for category, pattern_list in patterns.items():
        if category in {"version", "updated_at", "updated_by"}:
            continue

        if not isinstance(pattern_list, list):
            continue

        for pattern in pattern_list:
            try:
                if re.search(pattern, reasoning, re.IGNORECASE):
                    return (category, pattern)
            except re.error:
                continue

    return None


def mark_false_positive(
    interception_id: str,
    outcome_note: Optional[str] = None,
) -> bool:
    """Mark an interception as a false positive.

    Used after the fact when a block was incorrect.

    Args:
        interception_id: ID of the interception to mark
        outcome_note: Optional note explaining why it was a false positive

    Returns:
        True if marked successfully, False otherwise
    """
    db = get_db()

    try:
        db.execute("""
            UPDATE interceptions
            SET was_legitimate = 0, outcome_note = ?
            WHERE id = ?
        """, (outcome_note, interception_id))
        db.commit()
        return db.total_changes > 0
    except Exception:
        return False


def mark_legitimate(
    interception_id: str,
    outcome_note: Optional[str] = None,
) -> bool:
    """Mark an interception as a correct block.

    Used after the fact to confirm a block was justified.

    Args:
        interception_id: ID of the interception to mark
        outcome_note: Optional note about the outcome

    Returns:
        True if marked successfully, False otherwise
    """
    db = get_db()

    try:
        db.execute("""
            UPDATE interceptions
            SET was_legitimate = 1, outcome_note = ?
            WHERE id = ?
        """, (outcome_note, interception_id))
        db.commit()
        return db.total_changes > 0
    except Exception:
        return False


def get_interception_stats(days: int = 7) -> dict:
    """Get interception statistics for a time period.

    Args:
        days: Number of days to look back

    Returns:
        Statistics dict
    """
    db = get_db()

    stats = {
        "total": 0,
        "blocked": 0,
        "allowed": 0,
        "by_category": {},
        "by_pattern": {},
        "false_positives": 0,
        "legitimate_blocks": 0,
    }

    try:
        # Total counts
        row = db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result = 'blocked' THEN 1 ELSE 0 END) as blocked,
                SUM(CASE WHEN result = 'allowed' THEN 1 ELSE 0 END) as allowed,
                SUM(CASE WHEN was_legitimate = 0 THEN 1 ELSE 0 END) as false_positives,
                SUM(CASE WHEN was_legitimate = 1 AND result = 'blocked' THEN 1 ELSE 0 END) as legitimate
            FROM interceptions
            WHERE timestamp > datetime('now', ?)
        """, (f"-{days} days",)).fetchone()

        if row:
            stats["total"] = row["total"] or 0
            stats["blocked"] = row["blocked"] or 0
            stats["allowed"] = row["allowed"] or 0
            stats["false_positives"] = row["false_positives"] or 0
            stats["legitimate_blocks"] = row["legitimate"] or 0

        # By category
        by_cat = db.execute("""
            SELECT category, COUNT(*) as count
            FROM interceptions
            WHERE result = 'blocked'
            AND timestamp > datetime('now', ?)
            GROUP BY category
        """, (f"-{days} days",)).fetchall()

        for row in by_cat:
            if row["category"]:
                stats["by_category"][row["category"]] = row["count"]

        # By pattern
        by_pattern = db.execute("""
            SELECT pattern, COUNT(*) as count
            FROM interceptions
            WHERE result = 'blocked'
            AND timestamp > datetime('now', ?)
            GROUP BY pattern
            ORDER BY count DESC
            LIMIT 10
        """, (f"-{days} days",)).fetchall()

        for row in by_pattern:
            if row["pattern"]:
                stats["by_pattern"][row["pattern"]] = row["count"]

    except Exception as e:
        import sys
        print(f"Ereshkigal: Failed to get interception stats: {e}", file=sys.stderr)

    return stats


def get_recent_interceptions(
    result: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Get recent interceptions.

    Args:
        result: Filter by 'allowed' or 'blocked', or None for all
        limit: Maximum number to return

    Returns:
        List of interception records
    """
    db = get_db()

    query = """
        SELECT id, session_id, timestamp, tool, reasoning,
               category, pattern, result, was_legitimate, outcome_note
        FROM interceptions
    """
    params = []

    if result:
        query += " WHERE result = ?"
        params.append(result)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        import sys
        print(f"Ereshkigal: Failed to get recent interceptions: {e}", file=sys.stderr)
        return []


def generate_weekly_report(days: int = 7) -> str:
    """Generate weekly report for human review.

    Enki ONLY REPORTS. Never proposes patterns.

    Args:
        days: Number of days to include

    Returns:
        Formatted report text
    """
    lines = [
        "# Ereshkigal Weekly Report",
        f"",
        f"Period: Last {days} days",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    stats = get_interception_stats(days)

    # Summary
    lines.append("## Summary")
    lines.append(f"- Total attempts: {stats['total']}")
    lines.append(f"- Blocked: {stats['blocked']}")
    lines.append(f"- Allowed: {stats['allowed']}")
    if stats['blocked'] > 0:
        block_rate = stats['blocked'] / stats['total'] * 100
        lines.append(f"- Block rate: {block_rate:.1f}%")
    lines.append("")

    # Blocked by category
    if stats['by_category']:
        lines.append("## Blocked by Category")
        for cat, count in sorted(stats['by_category'].items(), key=lambda x: -x[1]):
            lines.append(f"- {cat}: {count}")
        lines.append("")

    # Top patterns
    if stats['by_pattern']:
        lines.append("## Top Blocking Patterns")
        for pattern, count in list(stats['by_pattern'].items())[:5]:
            lines.append(f"- `{pattern}`: {count} blocks")
        lines.append("")

    # False positives
    lines.append("## False Positives")
    lines.append(f"- Count: {stats['false_positives']}")
    if stats['blocked'] > 0 and stats['false_positives'] > 0:
        fp_rate = stats['false_positives'] / stats['blocked'] * 100
        lines.append(f"- Rate: {fp_rate:.1f}% of blocks")
    lines.append("")

    # Pattern effectiveness
    if stats['legitimate_blocks'] > 0 or stats['false_positives'] > 0:
        total_evaluated = stats['legitimate_blocks'] + stats['false_positives']
        if total_evaluated > 0:
            accuracy = stats['legitimate_blocks'] / total_evaluated * 100
            lines.append("## Pattern Accuracy")
            lines.append(f"- Evaluated blocks: {total_evaluated}")
            lines.append(f"- Accuracy: {accuracy:.1f}%")
            lines.append("")

    # Recent blocked attempts (for context)
    lines.append("## Recent Blocked Attempts")
    blocked = get_recent_interceptions(result="blocked", limit=5)
    if blocked:
        for i in blocked:
            reasoning = i.get("reasoning", "")[:60]
            pattern = i.get("pattern", "unknown")
            lines.append(f"- \"{reasoning}...\" -> `{pattern}`")
    else:
        lines.append("(none)")
    lines.append("")

    # Note about pattern proposals
    lines.append("---")
    lines.append("")
    lines.append("*Note: Enki reports data only. Pattern proposals should be*")
    lines.append("*made by a fresh Claude session with no project context.*")

    return "\n".join(lines)


# === Phase 8: External Pattern Evolution ===

def get_last_review_date() -> Optional[datetime]:
    """Get the date of the last Ereshkigal review.

    Returns:
        Datetime of last review, or None if never reviewed
    """
    review_file = Path.home() / ".enki" / "last_ereshkigal_review"

    if not review_file.exists():
        return None

    try:
        date_str = review_file.read_text().strip()
        return datetime.fromisoformat(date_str)
    except (ValueError, OSError):
        return None


def save_review_date() -> None:
    """Save the current date as the last review date."""
    review_file = Path.home() / ".enki" / "last_ereshkigal_review"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.write_text(datetime.now().isoformat())


def is_review_overdue(days: int = 7) -> bool:
    """Check if Ereshkigal review is overdue.

    Args:
        days: Number of days between reviews

    Returns:
        True if review is overdue
    """
    last_review = get_last_review_date()

    if last_review is None:
        return True

    days_since = (datetime.now() - last_review).days
    return days_since >= days


def get_review_reminder() -> Optional[str]:
    """Get a reminder message if review is overdue.

    Returns:
        Reminder message or None if not overdue
    """
    if not is_review_overdue():
        return None

    last_review = get_last_review_date()
    stats = get_interception_stats(days=7)

    if last_review:
        days_ago = (datetime.now() - last_review).days
        last_str = f"{days_ago} days ago"
    else:
        last_str = "Never"

    return (
        f"Ereshkigal Weekly Review Due\n"
        f"\n"
        f"Last review: {last_str}\n"
        f"Blocked: {stats['blocked']} | "
        f"Allowed: {stats['allowed']} | "
        f"FPs: {stats['false_positives']}\n"
        f"\n"
        f"Run: enki report weekly"
    )


def find_evasions_with_bugs(days: int = 30) -> list[dict]:
    """Find allowed interceptions that correlate with later bugs.

    This identifies patterns that should have blocked but didn't.
    Links interceptions to bugs from the same session.

    Args:
        days: Number of days to look back

    Returns:
        List of evasion records with bug information
    """
    db = get_db()
    evasions = []

    try:
        # Find allowed interceptions in sessions that later had violations
        # Tightened correlation: require same tool OR same file path
        # (prevents coincidental session-level correlation)
        results = db.execute("""
            SELECT DISTINCT
                i.id as interception_id,
                i.reasoning,
                i.tool,
                i.session_id,
                i.timestamp,
                v.gate as violation_gate,
                v.tool as violation_tool,
                v.file_path as violation_file_path
            FROM interceptions i
            JOIN violations v
                ON v.session_id = i.session_id
                AND v.timestamp > i.timestamp
                AND (
                    v.tool = i.tool
                    OR (
                        v.file_path IS NOT NULL
                        AND v.file_path != ''
                        AND i.reasoning LIKE '%' || v.file_path || '%'
                    )
                )
            WHERE i.result = 'allowed'
            AND i.timestamp > datetime('now', ?)
            ORDER BY i.timestamp DESC
            LIMIT 20
        """, (f"-{days} days",)).fetchall()

        for row in results:
            evasions.append({
                "interception_id": row["interception_id"],
                "reasoning": row["reasoning"],
                "tool": row["tool"],
                "session_id": row["session_id"],
                "timestamp": row["timestamp"],
                "violation_gate": row["violation_gate"],
                "violation_tool": row["violation_tool"],
                "violation_file_path": row["violation_file_path"],
                "correlation": "Violation on same tool/file after this was allowed",
            })

    except Exception as e:
        import sys
        print(f"Ereshkigal: Failed to find evasions with bugs: {e}", file=sys.stderr)

    return evasions


def generate_fresh_claude_prompt(days: int = 7) -> str:
    """Generate a prompt for a fresh Claude session to analyze patterns.

    This prompt should be copied to a NEW Claude session (no project context)
    to get unbiased pattern recommendations.

    Args:
        days: Number of days of data to include

    Returns:
        Prompt text for fresh Claude
    """
    stats = get_interception_stats(days)
    blocked = get_recent_interceptions(result="blocked", limit=10)
    evasions = find_evasions_with_bugs(days)

    prompt = [
        "# Ereshkigal Pattern Analysis Request",
        "",
        "I need you to analyze violation data from my project and propose",
        "pattern updates for my regex-based code review interceptor.",
        "",
        "## Current Statistics",
        f"- Total attempts: {stats['total']}",
        f"- Blocked: {stats['blocked']}",
        f"- Allowed: {stats['allowed']}",
        f"- False positives: {stats['false_positives']}",
        "",
    ]

    # Add blocked attempts that might show patterns
    if blocked:
        prompt.append("## Recent Blocked Attempts (correctly blocked)")
        prompt.append("")
        for b in blocked:
            reasoning = b.get("reasoning", "")[:100]
            pattern = b.get("pattern", "unknown")
            prompt.append(f"- Reasoning: \"{reasoning}\"")
            prompt.append(f"  Matched: `{pattern}`")
            prompt.append("")

    # Add evasions that should have been blocked
    if evasions:
        prompt.append("## Evasions That Caused Issues (should have blocked)")
        prompt.append("")
        for e in evasions:
            reasoning = e.get("reasoning", "")[:100]
            prompt.append(f"- Reasoning: \"{reasoning}\"")
            prompt.append(f"  Note: {e.get('correlation', 'Issues followed')}")
            prompt.append("")

    # Add allowed attempts for review
    allowed = get_recent_interceptions(result="allowed", limit=5)
    if allowed:
        prompt.append("## Recent Allowed Attempts (for review)")
        prompt.append("")
        for a in allowed:
            reasoning = a.get("reasoning", "")[:100]
            prompt.append(f"- \"{reasoning}\"")
        prompt.append("")

    prompt.extend([
        "## Current Pattern Categories",
        "",
        "1. **skip_patterns**: Catches attempts to skip process",
        "2. **minimize_patterns**: Catches downplaying language",
        "3. **urgency_patterns**: Catches urgency manipulation",
        "4. **certainty_patterns**: Catches overconfidence",
        "",
        "## Your Task",
        "",
        "1. Analyze the evasions - what language patterns did they use?",
        "2. Propose new regex patterns to catch these evasions",
        "3. Identify any false positives that suggest patterns are too broad",
        "4. Suggest refinements to existing patterns",
        "",
        "Format your response as:",
        "```",
        "## Proposed New Patterns",
        "- Category: pattern | Reason",
        "",
        "## Pattern Refinements",
        "- Old: pattern | New: refined_pattern | Reason",
        "",
        "## Patterns to Remove (if any)",
        "- pattern | Reason for removal",
        "```",
        "",
        "Be specific with regex syntax. Patterns are case-insensitive.",
    ])

    return "\n".join(prompt)


def generate_review_checklist(output_path: Optional[Path] = None) -> str:
    """Generate a human review checklist.

    Args:
        output_path: Optional path to write checklist

    Returns:
        Checklist content
    """
    last_review = get_last_review_date()
    last_str = last_review.strftime("%Y-%m-%d") if last_review else "Never"

    checklist = f"""# Ereshkigal Weekly Pattern Review

**Date**: {datetime.now().strftime("%Y-%m-%d")}
**Last Review**: {last_str}

## Checklist

### 1. Generate Report
```bash
enki report weekly
```
- [ ] Review blocked attempts - were they correct?
- [ ] Check false positive rate
- [ ] Note any patterns with low accuracy

### 2. Identify Evasions
```bash
enki report evasions
```
- [ ] Review allowed attempts that later caused issues
- [ ] Note the language patterns used in evasions

### 3. Get Pattern Recommendations
```bash
enki report prompt > /tmp/pattern-prompt.md
```
- [ ] Open a NEW Claude session (no project context)
- [ ] Paste the prompt
- [ ] Review proposed patterns critically
- [ ] Check for overly broad patterns that could cause false positives

### 4. Update Patterns
```bash
# Add new patterns
enki ereshkigal add "pattern" -c category_name

# Remove problematic patterns
enki ereshkigal remove "pattern" -c category_name

# Verify changes
enki ereshkigal patterns
```
- [ ] Add approved new patterns
- [ ] Remove or refine problematic patterns
- [ ] Test with sample reasoning

### 5. Mark Interceptions
```bash
# View recent interceptions
enki ereshkigal recent -r blocked

# Mark false positives
enki ereshkigal mark-fp <interception_id> -n "reason"

# Confirm legitimate blocks
enki ereshkigal mark-legit <interception_id>
```
- [ ] Mark false positives for tracking
- [ ] Confirm legitimate blocks

### 6. Complete Review
```bash
enki report complete
```
- [ ] Save review date
- [ ] Patterns updated and tested

## Notes

(Add any observations or decisions made during this review)

---

*Remember: Pattern proposals come from a FRESH Claude session.*
*Project Claude must never influence pattern evolution.*
"""

    if output_path:
        output_path.write_text(checklist)

    return checklist


def complete_review() -> None:
    """Mark the review as complete and save the date."""
    save_review_date()


def get_report_summary() -> str:
    """Get a one-line summary of the current state.

    Returns:
        Summary string
    """
    stats = get_interception_stats(days=7)
    last_review = get_last_review_date()

    if last_review:
        days_ago = (datetime.now() - last_review).days
        review_str = f"reviewed {days_ago}d ago"
    else:
        review_str = "never reviewed"

    return (
        f"Ereshkigal: {stats['blocked']} blocked, "
        f"{stats['allowed']} allowed, "
        f"{stats['false_positives']} FPs, "
        f"{review_str}"
    )
