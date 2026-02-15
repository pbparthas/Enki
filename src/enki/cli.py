"""cli.py — CLI entrypoint for human-only commands.

Commands that CC cannot call — they write directly to DBs
using the human's process, not CC's process.

Combined with Layer 0.5 (CC can't sqlite3), CC cannot forge approval.

Usage:
    python -m enki.cli approve --project myproject
    python -m enki.cli approve --project myproject --spec product
    python -m enki.cli status --project myproject
    python -m enki.cli migrate
    python -m enki.cli init
"""

import argparse
import sys

from enki.db import ENKI_ROOT, init_all


def cmd_approve(args):
    """Human approval of spec. Writes directly to em.db."""
    from enki.orch.pm import approve_spec

    result = approve_spec(args.project, args.spec)
    if result.get("approved"):
        print(f"Approved {args.spec} spec for project: {args.project}")
    else:
        print(f"Approval failed: {result}")


def cmd_status(args):
    """Show project status."""
    from enki.orch.tiers import get_project_state
    from enki.orch.status import generate_status_update

    state = get_project_state(args.project)
    print(f"Project: {args.project}")
    print(f"Goal: {state.get('goal', 'None')}")
    print(f"Tier: {state.get('tier', 'None')}")
    print(f"Phase: {state.get('phase', 'None')}")
    print()
    print(generate_status_update(args.project))


def cmd_init(args):
    """Initialize all Enki databases."""
    init_all()
    print(f"Initialized Enki databases at {ENKI_ROOT}")


def cmd_migrate(args):
    """Run v1/v2 → v3 migration."""
    try:
        from enki.scripts.migrate_v1 import run_migration
        run_migration()
    except ImportError:
        print("Migration script not found. Run from project root.")
        sys.exit(1)


def cmd_setup(args):
    """First-run onboarding."""
    from enki.setup import run_setup

    result = run_setup(
        name=args.name,
        role=args.role,
        interactive=not args.name,  # interactive if no name given
    )
    print(f"Enki setup complete for {result['name']}")
    print(f"  Steps: {', '.join(result['steps'])}")
    if result.get("persona_path"):
        print(f"  Persona: {result['persona_path']}")
    hooks = result.get("hooks_installed", 0)
    if hooks:
        print(f"  Hooks: {hooks} installed to ~/.enki/hooks/")
    print(f"\nRun 'python -m enki.cli init' anytime to re-initialize databases.")


def cmd_review(args):
    """Generate Gemini review package."""
    from enki.memory.gemini import generate_review_package

    package = generate_review_package()
    print(f"Review package generated:")
    print(f"  Candidates: {package.get('candidate_count', 0)}")
    print(f"  Proposals: {package.get('proposal_count', 0)}")
    print(f"\nPackage content written to stdout. Pipe to Gemini for review.")
    print()
    print(package.get("markdown", ""))


def main():
    parser = argparse.ArgumentParser(
        prog="enki",
        description="Enki v3 CLI — human-only commands",
    )
    subparsers = parser.add_subparsers(dest="command")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a spec")
    approve_parser.add_argument(
        "--project", "-p", required=True, help="Project ID"
    )
    approve_parser.add_argument(
        "--spec", "-s", default="implementation",
        help="Spec type (default: implementation)",
    )
    approve_parser.set_defaults(func=cmd_approve)

    # status
    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--project", "-p", required=True, help="Project ID"
    )
    status_parser.set_defaults(func=cmd_status)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize databases")
    init_parser.set_defaults(func=cmd_init)

    # migrate
    migrate_parser = subparsers.add_parser(
        "migrate", help="Migrate v1/v2 beads to v3"
    )
    migrate_parser.set_defaults(func=cmd_migrate)

    # setup
    setup_parser = subparsers.add_parser(
        "setup", help="First-run onboarding (name, role, persona, hooks)"
    )
    setup_parser.add_argument("--name", "-n", help="Your name")
    setup_parser.add_argument("--role", "-r", help="Your role (e.g., backend engineer)")
    setup_parser.set_defaults(func=cmd_setup)

    # review
    review_parser = subparsers.add_parser(
        "review", help="Generate Gemini review package"
    )
    review_parser.set_defaults(func=cmd_review)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
