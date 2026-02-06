"""Gate enforcement commands: check, stats."""

import json
import sys
from pathlib import Path

from . import requires_db


@requires_db
def cmd_gate_check(args):
    """Check if a tool use is allowed."""
    from ..enforcement import check_all_gates

    project_path = Path(args.project) if args.project else None
    result = check_all_gates(
        tool=args.tool,
        file_path=args.file if args.file else None,
        agent_type=args.agent if args.agent else None,
        project_path=project_path,
    )

    if args.json:
        if result.allowed:
            print(json.dumps({"decision": "allow"}))
        else:
            print(json.dumps({"decision": "block", "reason": result.reason}))
    else:
        if result.allowed:
            print("ALLOWED")
        else:
            print(f"BLOCKED by gate: {result.gate}")
            print(result.reason)
            sys.exit(1)


@requires_db
def cmd_gate_stats(args):
    """Show gate violation statistics."""
    from ..violations import get_violation_stats

    stats = get_violation_stats(days=args.days)
    print(f"Violation Statistics (last {args.days} days)")
    print("=" * 40)
    print(f"Total violations: {stats['total_violations']}")
    print(f"Tier escalations: {stats['escalations']}")
    print()
    print("By gate:")
    for gate, count in stats['by_gate'].items():
        print(f"  {gate}: {count}")


def register(subparsers):
    """Register gate commands."""
    gate_parser = subparsers.add_parser("gate", help="Gate enforcement")
    sub = gate_parser.add_subparsers(dest="gate_command")

    p = sub.add_parser("check", help="Check if tool use is allowed")
    p.add_argument("--tool", required=True, help="Tool name")
    p.add_argument("--file", help="File path")
    p.add_argument("--agent", help="Agent type")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_gate_check)

    p = sub.add_parser("stats", help="Show violation statistics")
    p.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    p.set_defaults(func=cmd_gate_stats)
