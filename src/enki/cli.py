"""Command-line interface for Enki."""

import argparse
import sys
import json

from .db import init_db, get_db, DB_PATH, ENKI_DIR
from .beads import create_bead, get_bead, star_bead, get_recent_beads
from .search import search
from .retention import maintain_wisdom


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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
