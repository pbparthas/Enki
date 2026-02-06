"""Report commands: weekly, evasions, prompt, checklist, complete, status."""

import json
from pathlib import Path

from . import requires_db


@requires_db
def cmd_report_weekly(args):
    """Generate weekly Ereshkigal report."""
    from ..ereshkigal import generate_weekly_report, get_report_summary

    report = generate_weekly_report(days=args.days)
    if args.output:
        Path(args.output).write_text(report)
        print(f"Report written to: {args.output}")
    elif args.summary:
        print(get_report_summary())
    else:
        print(report)


@requires_db
def cmd_report_evasions(args):
    """Show evasions that should have been blocked."""
    from ..ereshkigal import find_evasions_with_bugs

    evasions = find_evasions_with_bugs(days=args.days)
    if not evasions:
        print("No evasions found (no allowed attempts correlated with later issues).")
        return
    print(f"Evasions (last {args.days} days)")
    print("=" * 50)
    print()
    for e in evasions:
        reasoning = e.get("reasoning", "")[:80]
        print(f"ID: {e['interception_id'][:8]}")
        print(f"Tool: {e.get('tool', 'unknown')}")
        print(f"Reasoning: \"{reasoning}...\"")
        print(f"Note: {e.get('correlation', 'Issues followed')}")
        print()


@requires_db
def cmd_report_prompt(args):
    """Generate prompt for fresh Claude analysis."""
    from ..ereshkigal import generate_fresh_claude_prompt

    prompt = generate_fresh_claude_prompt(days=args.days)
    if args.output:
        Path(args.output).write_text(prompt)
        print(f"Prompt written to: {args.output}")
    else:
        print(prompt)


@requires_db
def cmd_report_checklist(args):
    """Generate human review checklist."""
    from ..ereshkigal import generate_review_checklist

    output_path = Path(args.output) if args.output else None
    checklist = generate_review_checklist(output_path)
    if output_path:
        print(f"Checklist written to: {output_path}")
    else:
        print(checklist)


def cmd_report_complete(args):
    """Mark review as complete."""
    from ..ereshkigal import complete_review
    complete_review()
    print("Review marked as complete.")
    print(f"Next review due in 7 days.")


@requires_db
def cmd_report_status(args):
    """Check if review is due."""
    from ..ereshkigal import (
        get_last_review_date as ereshkigal_last_review,
        is_review_overdue as ereshkigal_overdue,
        get_interception_stats, find_evasions_with_bugs,
        get_review_reminder,
    )

    if args.json:
        from datetime import datetime
        last_review = ereshkigal_last_review()
        is_overdue = ereshkigal_overdue()
        days_since = 0
        if last_review:
            delta = datetime.now() - datetime.fromisoformat(last_review)
            days_since = delta.days
        else:
            days_since = 999
        stats = get_interception_stats(days=7)
        print(json.dumps({
            "is_overdue": is_overdue,
            "days_since_review": days_since,
            "blocked_count": stats.get("blocked", 0),
            "evasion_count": len(find_evasions_with_bugs(days=7)),
            "false_positive_count": stats.get("false_positives", 0),
        }))
    else:
        reminder = get_review_reminder()
        if reminder:
            print(reminder)
        else:
            print("No review due. Pattern enforcement is up to date.")


def register(subparsers):
    """Register report commands."""
    report_parser = subparsers.add_parser("report", help="Pattern evolution reports")
    sub = report_parser.add_subparsers(dest="report_command")

    p = sub.add_parser("weekly", help="Generate weekly report")
    p.add_argument("-d", "--days", type=int, default=7, help="Days to include")
    p.add_argument("-o", "--output", help="Output file path")
    p.add_argument("--summary", action="store_true", help="One-line summary only")
    p.set_defaults(func=cmd_report_weekly)

    p = sub.add_parser("evasions", help="Show evasions that caused issues")
    p.add_argument("-d", "--days", type=int, default=30, help="Days to look back")
    p.set_defaults(func=cmd_report_evasions)

    p = sub.add_parser("prompt", help="Generate fresh Claude analysis prompt")
    p.add_argument("-d", "--days", type=int, default=7, help="Days of data to include")
    p.add_argument("-o", "--output", help="Output file path")
    p.set_defaults(func=cmd_report_prompt)

    p = sub.add_parser("checklist", help="Generate review checklist")
    p.add_argument("-o", "--output", help="Output file path")
    p.set_defaults(func=cmd_report_checklist)

    p = sub.add_parser("complete", help="Mark review as complete")
    p.set_defaults(func=cmd_report_complete)

    p = sub.add_parser("status", help="Check if review is due")
    p.add_argument("--json", action="store_true", help="JSON output for hooks")
    p.set_defaults(func=cmd_report_status)
