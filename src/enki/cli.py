"""Command-line interface for Enki."""

import argparse
import sys
import json

from pathlib import Path

from .db import init_db, get_db, DB_PATH, ENKI_DIR
from .beads import create_bead, get_bead, star_bead, get_recent_beads
from .search import search
from .retention import maintain_wisdom
from .session import (
    start_session, get_session, get_phase, set_phase, get_tier, set_tier,
    get_goal, set_goal, add_session_edit, get_session_edits,
)
from .enforcement import check_all_gates, detect_tier
from .violations import get_violation_stats


def cmd_init(args):
    """Initialize Enki database."""
    init_db()
    print(f"Initialized Enki at {ENKI_DIR}")
    print(f"Database: {DB_PATH}")


def cmd_remember(args):
    """Store a new bead."""
    init_db()

    tags = args.tags.split(",") if args.tags else None

    bead = create_bead(
        content=args.content,
        bead_type=args.type,
        summary=args.summary,
        project=args.project,
        context=args.context,
        tags=tags,
        starred=args.starred,
    )

    print(f"Remembered [{bead.type}] {bead.id}")
    if args.verbose:
        print(f"Content: {bead.content[:200]}{'...' if len(bead.content) > 200 else ''}")


def cmd_recall(args):
    """Search for beads."""
    init_db()

    results = search(
        query=args.query,
        project=args.project,
        bead_type=args.type,
        limit=args.limit,
    )

    if not results:
        print("No results found.")
        return

    for i, result in enumerate(results, 1):
        bead = result.bead
        sources = "+".join(result.sources)
        starred = "*" if bead.starred else ""

        print(f"{i}. [{bead.type}]{starred} (score: {result.score:.2f}, {sources})")
        print(f"   {bead.summary or bead.content[:100]}{'...' if len(bead.content) > 100 else ''}")
        print(f"   ID: {bead.id}")
        print()


def cmd_status(args):
    """Show memory status."""
    init_db()
    db = get_db()

    total = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()["count"]
    active = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE superseded_by IS NULL"
    ).fetchone()["count"]
    starred = db.execute(
        "SELECT COUNT(*) as count FROM beads WHERE starred = 1"
    ).fetchone()["count"]

    by_type = db.execute(
        "SELECT type, COUNT(*) as count FROM beads WHERE superseded_by IS NULL GROUP BY type"
    ).fetchall()

    print("Enki Memory Status")
    print("=" * 40)
    print(f"Database: {DB_PATH}")
    print(f"Total beads: {total}")
    print(f"Active beads: {active}")
    print(f"Starred beads: {starred}")
    print()
    print("By type:")
    for row in by_type:
        print(f"  {row['type']}: {row['count']}")


def cmd_recent(args):
    """Show recent beads."""
    init_db()

    beads = get_recent_beads(limit=args.limit, project=args.project)

    if not beads:
        print("No beads found.")
        return

    for i, bead in enumerate(beads, 1):
        starred = "*" if bead.starred else ""
        print(f"{i}. [{bead.type}]{starred} {bead.id[:8]}...")
        print(f"   {bead.summary or bead.content[:100]}{'...' if len(bead.content) > 100 else ''}")
        print(f"   Created: {bead.created_at}")
        print()


def cmd_star(args):
    """Star a bead."""
    init_db()

    bead = star_bead(args.bead_id)
    if bead:
        print(f"Starred bead {bead.id}")
    else:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)


def cmd_get(args):
    """Get a specific bead."""
    init_db()

    bead = get_bead(args.bead_id)
    if not bead:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "id": bead.id,
            "type": bead.type,
            "content": bead.content,
            "summary": bead.summary,
            "project": bead.project,
            "starred": bead.starred,
            "tags": bead.tags,
            "weight": bead.weight,
            "created_at": str(bead.created_at),
            "last_accessed": str(bead.last_accessed) if bead.last_accessed else None,
        }, indent=2))
    else:
        print(f"ID: {bead.id}")
        print(f"Type: {bead.type}")
        print(f"Starred: {bead.starred}")
        print(f"Weight: {bead.weight:.2f}")
        print(f"Project: {bead.project or '(global)'}")
        print(f"Tags: {', '.join(bead.tags) if bead.tags else '(none)'}")
        print(f"Created: {bead.created_at}")
        print(f"Last accessed: {bead.last_accessed or '(never)'}")
        print()
        print("Content:")
        print("-" * 40)
        print(bead.content)
        if bead.summary:
            print()
            print("Summary:")
            print("-" * 40)
            print(bead.summary)


def cmd_maintain(args):
    """Run maintenance tasks."""
    init_db()

    results = maintain_wisdom()
    print("Maintenance complete:")
    print(f"  Weights updated: {results['weights_updated']}")
    print(f"  Beads archived: {results['archived']}")
    print(f"  Superseded purged: {results['purged']}")


# === Session Commands ===

def cmd_session_start(args):
    """Start a new session."""
    init_db()
    project_path = Path(args.project) if args.project else None

    session = start_session(project_path, args.goal)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id,
            "phase": session.phase,
            "tier": session.tier,
            "goal": session.goal,
        }))
    else:
        print(f"Session started: {session.session_id}")
        print(f"Phase: {session.phase}")
        print(f"Tier: {session.tier}")
        if session.goal:
            print(f"Goal: {session.goal}")


def cmd_session_status(args):
    """Show session status."""
    project_path = Path(args.project) if args.project else None

    session = get_session(project_path)
    if not session:
        print("No active session")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "session_id": session.session_id,
            "phase": session.phase,
            "tier": session.tier,
            "goal": session.goal,
            "edits": session.edits,
        }))
    else:
        print(f"Session: {session.session_id}")
        print(f"Phase: {session.phase}")
        print(f"Tier: {session.tier}")
        print(f"Goal: {session.goal or '(none)'}")
        print(f"Files edited: {len(session.edits)}")
        if session.edits:
            print("Recent edits:")
            for f in session.edits[-5:]:
                print(f"  - {f}")


def cmd_session_set_phase(args):
    """Set session phase."""
    project_path = Path(args.project) if args.project else None

    try:
        set_phase(args.phase, project_path)
        print(f"Phase set to: {args.phase}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_session_set_goal(args):
    """Set session goal."""
    project_path = Path(args.project) if args.project else None

    set_goal(args.goal, project_path)
    print(f"Goal set: {args.goal}")


def cmd_session_track_edit(args):
    """Track a file edit."""
    project_path = Path(args.project) if args.project else None

    edits = add_session_edit(args.file, project_path)

    # Recalculate tier
    old_tier = get_tier(project_path)
    new_tier = detect_tier(project_path=project_path)

    if new_tier != old_tier:
        from .session import tier_escalated
        from .violations import log_escalation, log_escalation_to_file

        if tier_escalated(old_tier, new_tier):
            log_escalation(old_tier, new_tier, project_path)
            log_escalation_to_file(old_tier, new_tier, project_path)
            print(f"ESCALATION: {old_tier} -> {new_tier}")

        set_tier(new_tier, project_path)


# === Gate Commands ===

def cmd_gate_check(args):
    """Check if a tool use is allowed."""
    init_db()
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
            print(json.dumps({
                "decision": "block",
                "reason": result.reason,
            }))
    else:
        if result.allowed:
            print("ALLOWED")
        else:
            print(f"BLOCKED by gate: {result.gate}")
            print(result.reason)
            sys.exit(1)


def cmd_gate_stats(args):
    """Show gate violation statistics."""
    init_db()

    stats = get_violation_stats(days=args.days)

    print(f"Violation Statistics (last {args.days} days)")
    print("=" * 40)
    print(f"Total violations: {stats['total_violations']}")
    print(f"Override rate: {stats['override_rate']:.1%}")
    print(f"Tier escalations: {stats['escalations']}")
    print()
    print("By gate:")
    for gate, count in stats['by_gate'].items():
        print(f"  {gate}: {count}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enki - Second brain for software engineering",
        prog="enki",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize Enki database")
    init_parser.set_defaults(func=cmd_init)

    # remember
    remember_parser = subparsers.add_parser("remember", help="Store a new bead")
    remember_parser.add_argument("content", help="Content to remember")
    remember_parser.add_argument(
        "-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"],
        default="learning",
        help="Type of knowledge",
    )
    remember_parser.add_argument("-s", "--summary", help="Short summary")
    remember_parser.add_argument("-p", "--project", help="Project identifier")
    remember_parser.add_argument("-c", "--context", help="Context when learned")
    remember_parser.add_argument("--tags", help="Comma-separated tags")
    remember_parser.add_argument("--starred", action="store_true", help="Star this bead")
    remember_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    remember_parser.set_defaults(func=cmd_remember)

    # recall
    recall_parser = subparsers.add_parser("recall", help="Search for beads")
    recall_parser.add_argument("query", help="Search query")
    recall_parser.add_argument("-p", "--project", help="Project filter")
    recall_parser.add_argument(
        "-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"],
        help="Type filter",
    )
    recall_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    recall_parser.set_defaults(func=cmd_recall)

    # status
    status_parser = subparsers.add_parser("status", help="Show memory status")
    status_parser.set_defaults(func=cmd_status)

    # recent
    recent_parser = subparsers.add_parser("recent", help="Show recent beads")
    recent_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    recent_parser.add_argument("-p", "--project", help="Project filter")
    recent_parser.set_defaults(func=cmd_recent)

    # star
    star_parser = subparsers.add_parser("star", help="Star a bead")
    star_parser.add_argument("bead_id", help="Bead ID to star")
    star_parser.set_defaults(func=cmd_star)

    # get
    get_parser = subparsers.add_parser("get", help="Get a specific bead")
    get_parser.add_argument("bead_id", help="Bead ID")
    get_parser.add_argument("--json", action="store_true", help="Output as JSON")
    get_parser.set_defaults(func=cmd_get)

    # maintain
    maintain_parser = subparsers.add_parser("maintain", help="Run maintenance tasks")
    maintain_parser.set_defaults(func=cmd_maintain)

    # === Session Commands ===
    session_parser = subparsers.add_parser("session", help="Session management")
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    # session start
    session_start_parser = session_subparsers.add_parser("start", help="Start a new session")
    session_start_parser.add_argument("-g", "--goal", help="Session goal")
    session_start_parser.add_argument("-p", "--project", help="Project path")
    session_start_parser.add_argument("--json", action="store_true", help="JSON output")
    session_start_parser.set_defaults(func=cmd_session_start)

    # session status
    session_status_parser = session_subparsers.add_parser("status", help="Show session status")
    session_status_parser.add_argument("-p", "--project", help="Project path")
    session_status_parser.add_argument("--json", action="store_true", help="JSON output")
    session_status_parser.set_defaults(func=cmd_session_status)

    # session set-phase
    session_phase_parser = session_subparsers.add_parser("set-phase", help="Set session phase")
    session_phase_parser.add_argument("phase", choices=["intake", "debate", "plan", "implement", "review", "test", "ship"])
    session_phase_parser.add_argument("-p", "--project", help="Project path")
    session_phase_parser.set_defaults(func=cmd_session_set_phase)

    # session set-goal
    session_goal_parser = session_subparsers.add_parser("set-goal", help="Set session goal")
    session_goal_parser.add_argument("goal", help="Session goal")
    session_goal_parser.add_argument("-p", "--project", help="Project path")
    session_goal_parser.set_defaults(func=cmd_session_set_goal)

    # session track-edit
    session_edit_parser = session_subparsers.add_parser("track-edit", help="Track a file edit")
    session_edit_parser.add_argument("--file", required=True, help="File path")
    session_edit_parser.add_argument("-p", "--project", help="Project path")
    session_edit_parser.set_defaults(func=cmd_session_track_edit)

    # === Gate Commands ===
    gate_parser = subparsers.add_parser("gate", help="Gate enforcement")
    gate_subparsers = gate_parser.add_subparsers(dest="gate_command")

    # gate check
    gate_check_parser = gate_subparsers.add_parser("check", help="Check if tool use is allowed")
    gate_check_parser.add_argument("--tool", required=True, help="Tool name")
    gate_check_parser.add_argument("--file", help="File path")
    gate_check_parser.add_argument("--agent", help="Agent type")
    gate_check_parser.add_argument("-p", "--project", help="Project path")
    gate_check_parser.add_argument("--json", action="store_true", help="JSON output")
    gate_check_parser.set_defaults(func=cmd_gate_check)

    # gate stats
    gate_stats_parser = gate_subparsers.add_parser("stats", help="Show violation statistics")
    gate_stats_parser.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    gate_stats_parser.set_defaults(func=cmd_gate_stats)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
