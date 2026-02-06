"""Memory commands: init, remember, recall, status, recent, star, get, maintain."""

import json
import sys

from . import requires_db


@requires_db
def cmd_init(args):
    """Initialize Enki database."""
    from ..db import DB_PATH, ENKI_DIR
    print(f"Initialized Enki at {ENKI_DIR}")
    print(f"Database: {DB_PATH}")


@requires_db
def cmd_remember(args):
    """Store a new bead."""
    from ..beads import create_bead

    tags = args.tags.split(",") if args.tags else None
    bead = create_bead(
        content=args.content, bead_type=args.type, summary=args.summary,
        project=args.project, context=args.context, tags=tags, starred=args.starred,
    )
    print(f"Remembered [{bead.type}] {bead.id}")
    if args.verbose:
        print(f"Content: {bead.content[:200]}{'...' if len(bead.content) > 200 else ''}")


@requires_db
def cmd_recall(args):
    """Search for beads."""
    from ..search import search

    results = search(query=args.query, project=args.project, bead_type=args.type, limit=args.limit)
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


@requires_db
def cmd_status(args):
    """Show memory status (P2-11: uses get_bead_stats instead of raw SQL)."""
    from ..db import DB_PATH
    from ..beads import get_bead_stats

    stats = get_bead_stats()
    print("Enki Memory Status")
    print("=" * 40)
    print(f"Database: {DB_PATH}")
    print(f"Total beads: {stats['total']}")
    print(f"Active beads: {stats['active']}")
    print(f"Starred beads: {stats['starred']}")
    print()
    print("By type:")
    for bead_type, count in stats['by_type'].items():
        print(f"  {bead_type}: {count}")


@requires_db
def cmd_recent(args):
    """Show recent beads."""
    from ..beads import get_recent_beads

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


@requires_db
def cmd_star(args):
    """Star a bead."""
    from ..beads import star_bead

    bead = star_bead(args.bead_id)
    if bead:
        print(f"Starred bead {bead.id}")
    else:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)


@requires_db
def cmd_get(args):
    """Get a specific bead."""
    from ..beads import get_bead

    bead = get_bead(args.bead_id)
    if not bead:
        print(f"Bead {args.bead_id} not found")
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "id": bead.id, "type": bead.type, "content": bead.content,
            "summary": bead.summary, "project": bead.project, "starred": bead.starred,
            "tags": bead.tags, "weight": bead.weight, "created_at": str(bead.created_at),
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


@requires_db
def cmd_maintain(args):
    """Run maintenance tasks."""
    from ..retention import maintain_wisdom

    results = maintain_wisdom()
    print("Maintenance complete:")
    print(f"  Weights updated: {results['weights_updated']}")
    print(f"  Beads archived: {results['archived']}")
    print(f"  Superseded purged: {results['purged']}")


def register(subparsers):
    """Register memory commands."""
    p = subparsers.add_parser("init", help="Initialize Enki database")
    p.set_defaults(func=cmd_init)

    p = subparsers.add_parser("remember", help="Store a new bead")
    p.add_argument("content", help="Content to remember")
    p.add_argument("-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"],
        default="learning", help="Type of knowledge")
    p.add_argument("-s", "--summary", help="Short summary")
    p.add_argument("-p", "--project", help="Project identifier")
    p.add_argument("-c", "--context", help="Context when learned")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--starred", action="store_true", help="Star this bead")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p.set_defaults(func=cmd_remember)

    p = subparsers.add_parser("recall", help="Search for beads")
    p.add_argument("query", help="Search query")
    p.add_argument("-p", "--project", help="Project filter")
    p.add_argument("-t", "--type",
        choices=["decision", "solution", "learning", "violation", "pattern"], help="Type filter")
    p.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    p.set_defaults(func=cmd_recall)

    p = subparsers.add_parser("status", help="Show memory status")
    p.set_defaults(func=cmd_status)

    p = subparsers.add_parser("recent", help="Show recent beads")
    p.add_argument("-l", "--limit", type=int, default=10, help="Max results")
    p.add_argument("-p", "--project", help="Project filter")
    p.set_defaults(func=cmd_recent)

    p = subparsers.add_parser("star", help="Star a bead")
    p.add_argument("bead_id", help="Bead ID to star")
    p.set_defaults(func=cmd_star)

    p = subparsers.add_parser("get", help="Get a specific bead")
    p.add_argument("bead_id", help="Bead ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get)

    p = subparsers.add_parser("maintain", help="Run maintenance tasks")
    p.set_defaults(func=cmd_maintain)
