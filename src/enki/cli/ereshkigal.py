"""Ereshkigal pattern interceptor commands."""

import json
import sys
from pathlib import Path

from . import requires_db


def cmd_ereshkigal_init(args):
    """Initialize patterns.json with default patterns."""
    from ..ereshkigal import init_patterns
    path = init_patterns()
    print(f"Patterns initialized at: {path}")


def cmd_ereshkigal_test(args):
    """Test if reasoning would be blocked."""
    from ..ereshkigal import would_block

    result = would_block(args.reasoning)
    if result:
        category, pattern = result
        print(f"WOULD BLOCK")
        print(f"  Category: {category}")
        print(f"  Pattern: {pattern}")
        sys.exit(1)
    else:
        print("WOULD ALLOW")


@requires_db
def cmd_ereshkigal_intercept(args):
    """Run interception on reasoning."""
    from ..ereshkigal import intercept

    result = intercept(
        tool=args.tool, reasoning=args.reasoning,
        session_id=args.session, phase=args.phase,
    )
    if args.json:
        print(json.dumps({
            "allowed": result.allowed, "category": result.category,
            "pattern": result.pattern, "interception_id": result.interception_id,
            "message": result.message,
        }))
    else:
        if result.allowed:
            print("ALLOWED")
            print(f"Logged: {result.interception_id[:8] if result.interception_id else 'N/A'}")
        else:
            print(result.message)
            sys.exit(1)


@requires_db
def cmd_ereshkigal_stats(args):
    """Show interception statistics."""
    from ..ereshkigal import get_interception_stats

    stats = get_interception_stats(days=args.days)
    print(f"Ereshkigal Statistics (last {args.days} days)")
    print("=" * 40)
    print(f"Total attempts: {stats['total']}")
    print(f"Blocked: {stats['blocked']}")
    print(f"Allowed: {stats['allowed']}")
    if stats['total'] > 0:
        print(f"Block rate: {stats['blocked'] / stats['total'] * 100:.1f}%")
    print()

    if stats['by_category']:
        print("By category:")
        for cat, count in stats['by_category'].items():
            print(f"  {cat}: {count}")
        print()

    if stats['by_pattern']:
        print("Top patterns:")
        for pattern, count in list(stats['by_pattern'].items())[:5]:
            print(f"  {pattern}: {count}")
        print()

    if stats['false_positives'] > 0 or stats['legitimate_blocks'] > 0:
        total_evaluated = stats['false_positives'] + stats['legitimate_blocks']
        accuracy = stats['legitimate_blocks'] / total_evaluated * 100 if total_evaluated > 0 else 0
        print(f"False positives: {stats['false_positives']}")
        print(f"Pattern accuracy: {accuracy:.1f}%")


@requires_db
def cmd_ereshkigal_report(args):
    """Generate weekly report."""
    from ..ereshkigal import generate_weekly_report

    report = generate_weekly_report(days=args.days)
    if args.output:
        Path(args.output).write_text(report)
        print(f"Report written to: {args.output}")
    else:
        print(report)


def cmd_ereshkigal_patterns(args):
    """List current patterns."""
    from ..ereshkigal import load_patterns, get_pattern_categories

    patterns = load_patterns()
    if args.json:
        print(json.dumps(patterns, indent=2))
    else:
        print("Ereshkigal Patterns")
        print("=" * 40)
        print(f"Version: {patterns.get('version', 'unknown')}")
        print(f"Updated: {patterns.get('updated_at', 'unknown')}")
        print(f"Updated by: {patterns.get('updated_by', 'unknown')}")
        print()
        for category in get_pattern_categories():
            pattern_list = patterns.get(category, [])
            print(f"{category} ({len(pattern_list)} patterns):")
            for p in pattern_list:
                print(f"  - {p}")
            print()


def cmd_ereshkigal_add(args):
    """Add a pattern."""
    from ..ereshkigal import add_pattern
    add_pattern(args.pattern, args.category)
    print(f"Added pattern to {args.category}: {args.pattern}")


def cmd_ereshkigal_remove(args):
    """Remove a pattern."""
    from ..ereshkigal import remove_pattern

    if remove_pattern(args.pattern, args.category):
        print(f"Removed pattern from {args.category}: {args.pattern}")
    else:
        print(f"Pattern not found in {args.category}")
        sys.exit(1)


@requires_db
def cmd_ereshkigal_mark_fp(args):
    """Mark interception as false positive."""
    from ..ereshkigal import mark_false_positive

    if mark_false_positive(args.interception_id, args.note):
        print(f"Marked as false positive: {args.interception_id}")
    else:
        print(f"Interception not found: {args.interception_id}")
        sys.exit(1)


@requires_db
def cmd_ereshkigal_mark_legit(args):
    """Mark interception as legitimate block."""
    from ..ereshkigal import mark_legitimate

    if mark_legitimate(args.interception_id, args.note):
        print(f"Marked as legitimate: {args.interception_id}")
    else:
        print(f"Interception not found: {args.interception_id}")
        sys.exit(1)


@requires_db
def cmd_ereshkigal_recent(args):
    """Show recent interceptions."""
    from ..ereshkigal import get_recent_interceptions

    interceptions = get_recent_interceptions(result=args.result, limit=args.limit)
    if not interceptions:
        print("No interceptions found.")
        return

    for i in interceptions:
        status = "BLOCKED" if i['result'] == 'blocked' else "allowed"
        reasoning = i.get('reasoning', '')[:50]
        timestamp = i.get('timestamp', 'unknown')
        print(f"[{status}] {i['id'][:8]}")
        print(f"  Tool: {i.get('tool', 'unknown')}")
        print(f"  Reasoning: \"{reasoning}...\"")
        if i['result'] == 'blocked':
            print(f"  Pattern: {i.get('pattern', 'unknown')}")
        print(f"  Time: {timestamp}")
        if i.get('was_legitimate') is not None:
            legit = "Yes" if i['was_legitimate'] else "No (false positive)"
            print(f"  Legitimate: {legit}")
        print()


def register(subparsers):
    """Register ereshkigal commands."""
    ereshkigal_parser = subparsers.add_parser("ereshkigal", help="Pattern interceptor (Ereshkigal)")
    sub = ereshkigal_parser.add_subparsers(dest="ereshkigal_command")

    p = sub.add_parser("init", help="Initialize patterns.json")
    p.set_defaults(func=cmd_ereshkigal_init)

    p = sub.add_parser("test", help="Test if reasoning would be blocked")
    p.add_argument("reasoning", help="Reasoning text to test")
    p.set_defaults(func=cmd_ereshkigal_test)

    p = sub.add_parser("intercept", help="Run interception")
    p.add_argument("--tool", required=True, help="Tool being used")
    p.add_argument("--reasoning", required=True, help="Claude's reasoning")
    p.add_argument("--session", help="Session ID")
    p.add_argument("--phase", help="Current phase")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_ereshkigal_intercept)

    p = sub.add_parser("stats", help="Show interception statistics")
    p.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    p.set_defaults(func=cmd_ereshkigal_stats)

    p = sub.add_parser("report", help="Generate weekly report")
    p.add_argument("-d", "--days", type=int, default=7, help="Days to include")
    p.add_argument("-o", "--output", help="Output file path")
    p.set_defaults(func=cmd_ereshkigal_report)

    p = sub.add_parser("patterns", help="List current patterns")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_ereshkigal_patterns)

    p = sub.add_parser("add", help="Add a pattern")
    p.add_argument("pattern", help="Regex pattern to add")
    p.add_argument("-c", "--category", required=True,
        choices=["skip_patterns", "minimize_patterns", "urgency_patterns", "certainty_patterns"],
        help="Pattern category")
    p.set_defaults(func=cmd_ereshkigal_add)

    p = sub.add_parser("remove", help="Remove a pattern")
    p.add_argument("pattern", help="Pattern to remove")
    p.add_argument("-c", "--category", required=True, help="Pattern category")
    p.set_defaults(func=cmd_ereshkigal_remove)

    p = sub.add_parser("mark-fp", help="Mark as false positive")
    p.add_argument("interception_id", help="Interception ID")
    p.add_argument("-n", "--note", help="Note explaining why")
    p.set_defaults(func=cmd_ereshkigal_mark_fp)

    p = sub.add_parser("mark-legit", help="Mark as legitimate block")
    p.add_argument("interception_id", help="Interception ID")
    p.add_argument("-n", "--note", help="Outcome note")
    p.set_defaults(func=cmd_ereshkigal_mark_legit)

    p = sub.add_parser("recent", help="Show recent interceptions")
    p.add_argument("-r", "--result", choices=["allowed", "blocked"], help="Filter by result")
    p.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    p.set_defaults(func=cmd_ereshkigal_recent)
