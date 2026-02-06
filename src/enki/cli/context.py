"""Context and persona commands: context, greeting, summary, error-context, decision-context."""

import json
from pathlib import Path


def cmd_context(args):
    """Show Enki's context injection (legacy)."""
    from ..persona import build_session_start_injection

    project_path = Path(args.project) if args.project else None
    print(build_session_start_injection(project_path))


def cmd_context_load(args):
    """Load and display adaptive context."""
    from ..context import ContextTier, load_context, format_context_for_injection

    project_path = Path(args.project) if args.project else None
    tier_str = args.tier if hasattr(args, 'tier') and args.tier else "auto"
    tier = ContextTier(tier_str)
    context = load_context(tier=tier, project_path=project_path)

    if hasattr(args, 'json') and args.json:
        print(json.dumps({
            "tier": context.tier.value, "phase": context.phase,
            "goal": context.goal, "token_estimate": context.token_estimate,
            "beads_count": len(context.beads),
            "has_spec": context.spec is not None,
            "has_tasks": context.task_graph is not None,
        }, indent=2))
    else:
        print(format_context_for_injection(context))


def cmd_context_preview(args):
    """Preview what context would be loaded."""
    from ..context import ContextTier, preview_context

    project_path = Path(args.project) if args.project else None
    tier_str = args.tier if hasattr(args, 'tier') and args.tier else "auto"
    tier = ContextTier(tier_str)
    print(preview_context(tier=tier, project_path=project_path))


def cmd_context_set_default(args):
    """Set default context tier."""
    from ..context import ContextTier, set_default_tier

    project_path = Path(args.project) if args.project else None
    tier = ContextTier(args.tier)
    set_default_tier(tier, project_path)
    print(f"Default context tier set to: {args.tier}")


def cmd_greeting(args):
    """Show Enki's greeting."""
    from ..persona import get_enki_greeting

    project_path = Path(args.project) if args.project else None
    print(get_enki_greeting(project_path))


def cmd_summary(args):
    """Show session summary."""
    from ..persona import generate_session_summary

    project_path = Path(args.project) if args.project else None
    print(generate_session_summary(project_path))


def cmd_error_context(args):
    """Show context for an error."""
    from ..persona import build_error_context_injection

    project_path = Path(args.project) if args.project else None
    print(build_error_context_injection(args.error, project_path))


def cmd_decision_context(args):
    """Show context for a decision."""
    from ..persona import build_decision_context

    project_path = Path(args.project) if args.project else None
    print(build_decision_context(args.topic, project_path))


def register(subparsers):
    """Register context and persona commands."""
    # context (with subcommands)
    context_parser = subparsers.add_parser("context", help="Adaptive context management")
    context_subs = context_parser.add_subparsers(dest="context_command")

    p = context_subs.add_parser("load", help="Load and display context")
    p.add_argument("-t", "--tier",
        choices=["minimal", "standard", "full", "auto"], default="auto",
        help="Context tier (default: auto)")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_context_load)

    p = context_subs.add_parser("preview", help="Preview what would be loaded")
    p.add_argument("-t", "--tier",
        choices=["minimal", "standard", "full", "auto"], default="auto",
        help="Context tier (default: auto)")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_context_preview)

    p = context_subs.add_parser("set-default", help="Set default tier")
    p.add_argument("tier", choices=["minimal", "standard", "full", "auto"],
        help="Default tier to set")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_context_set_default)

    # Default: show legacy context
    context_parser.add_argument("-p", "--project", help="Project path")
    context_parser.set_defaults(func=cmd_context)

    # greeting
    p = subparsers.add_parser("greeting", help="Show Enki's greeting")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_greeting)

    # summary
    p = subparsers.add_parser("summary", help="Show session summary")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_summary)

    # error-context
    p = subparsers.add_parser("error-context", help="Show context for an error")
    p.add_argument("error", help="Error text")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_error_context)

    # decision-context
    p = subparsers.add_parser("decision-context", help="Show context for a decision")
    p.add_argument("topic", help="Decision topic")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_decision_context)
