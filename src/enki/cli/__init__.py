"""Command-line interface for Enki.

P2-03: Split from 2806-line god module into category modules.
P2-11: Raw SQL eliminated — CLI delegates to service abstractions.
"""

import argparse
import json
import sys
from functools import wraps


def format_output(data, as_json: bool = False) -> str:
    """Format output as JSON or human-readable text (P3-11).

    Args:
        data: Dict or string to format
        as_json: If True, output JSON; otherwise return as-is

    Returns:
        Formatted string
    """
    if as_json:
        return json.dumps(data, indent=2, default=str)
    if isinstance(data, dict):
        return "\n".join(f"{k}: {v}" for k, v in data.items())
    return str(data)


def requires_db(func):
    """Decorator that initializes the database before running a CLI command."""
    @wraps(func)
    def wrapper(args):
        from ..db import init_db
        init_db()
        return func(args)
    return wrapper


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enki - Second brain for software engineering",
        prog="enki",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Import and register all command categories (lazy to avoid circular imports)
    from . import (
        memory, session, gates, pm, orchestration, validation,
        context, evolution, ereshkigal, reports, migration, extras,
    )

    memory.register(subparsers)
    session.register(subparsers)
    gates.register(subparsers)
    pm.register(subparsers)
    orchestration.register(subparsers)
    validation.register(subparsers)
    context.register(subparsers)
    evolution.register(subparsers)
    ereshkigal.register(subparsers)
    reports.register(subparsers)
    migration.register(subparsers)
    extras.register(subparsers)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
