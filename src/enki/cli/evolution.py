"""Evolution commands: summary, patterns, triggers, review, explain, ask, status."""

from pathlib import Path

from . import requires_db


def cmd_evolution_summary(args):
    """Show evolution summary."""
    from ..evolution import get_evolution_summary

    project_path = Path(args.project) if args.project else None
    print(get_evolution_summary(project_path))


def cmd_evolution_patterns(args):
    """Show violation patterns."""
    from ..evolution import analyze_violation_patterns

    project_path = Path(args.project) if args.project else None
    patterns = analyze_violation_patterns(days=args.days, project_path=project_path)
    if not patterns:
        print("No violation patterns found.")
        return
    print(f"Violation Patterns (last {args.days} days)")
    print("=" * 40)
    for p in patterns:
        print(f"\nGate: {p['gate']} ({p['total']} violations)")
        for reason in p.get('reasons', [])[:3]:
            print(f"  - {reason['reason'][:50]}: {reason['count']}x")


def cmd_evolution_triggers(args):
    """Check for self-correction triggers."""
    from ..evolution import check_correction_triggers

    project_path = Path(args.project) if args.project else None
    triggers = check_correction_triggers(project_path)
    if not triggers:
        print("No correction triggers detected.")
        return
    print("Self-Correction Triggers")
    print("=" * 40)
    for t in triggers:
        print(f"\n{t['trigger'].upper()}")
        print(f"  {t['suggestion']}")


@requires_db
def cmd_evolution_review(args):
    """Run weekly self-review."""
    from ..evolution import run_weekly_self_review

    project_path = Path(args.project) if args.project else None
    print("Running Enki self-review...")
    print()
    report = run_weekly_self_review(project_path)
    print(f"Review Date: {report['date']}")
    print()

    if report['violation_patterns']:
        print(f"Violation Patterns: {len(report['violation_patterns'])}")
        for p in report['violation_patterns'][:3]:
            print(f"  - {p['gate']}: {p['total']} violations")

    if report['escalation_patterns']:
        print(f"\nEscalation Patterns: {len(report['escalation_patterns'])}")
        for p in report['escalation_patterns'][:3]:
            print(f"  - '{p['goal_pattern'][:30]}': {p['count']} escalations")

    if report['corrections_made']:
        print(f"\nCorrections Made: {len(report['corrections_made'])}")
        for c in report['corrections_made']:
            print(f"  - {c['correction']}")

    if report['recommendations']:
        print(f"\nRecommendations: {len(report['recommendations'])}")
        for r in report['recommendations']:
            print(f"  - {r['description']}")

    if not any([report['violation_patterns'], report['escalation_patterns'],
                report['corrections_made'], report['recommendations']]):
        print("No significant patterns detected. Keep up the good work!")


def cmd_evolution_explain(args):
    """Explain a blocking decision."""
    from ..evolution import explain_block

    project_path = Path(args.project) if args.project else None
    print(explain_block(args.gate, args.reason or "", project_path))


def cmd_evolution_ask(args):
    """Ask Enki about her behavior."""
    from ..evolution import get_self_awareness_response

    project_path = Path(args.project) if args.project else None
    print(get_self_awareness_response(args.question, project_path))


def cmd_evolution_status(args):
    """Check if review is due."""
    from ..evolution import get_last_review_date, is_review_due

    project_path = Path(args.project) if args.project else None
    last_review = get_last_review_date(project_path)
    due = is_review_due(project_path)
    print(f"Last Review: {last_review or 'Never'}")
    print(f"Review Due: {'Yes' if due else 'No'}")
    if due:
        print("\nRun 'enki evolution review' to perform self-review.")


def register(subparsers):
    """Register evolution commands."""
    evolution_parser = subparsers.add_parser("evolution", help="Self-evolution management")
    sub = evolution_parser.add_subparsers(dest="evolution_command")

    p = sub.add_parser("summary", help="Show evolution summary")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_summary)

    p = sub.add_parser("patterns", help="Show violation patterns")
    p.add_argument("-d", "--days", type=int, default=7, help="Days to look back")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_patterns)

    p = sub.add_parser("triggers", help="Check correction triggers")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_triggers)

    p = sub.add_parser("review", help="Run weekly self-review")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_review)

    p = sub.add_parser("explain", help="Explain a blocking decision")
    p.add_argument("gate", help="Gate that blocked")
    p.add_argument("-r", "--reason", help="Block reason")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_explain)

    p = sub.add_parser("ask", help="Ask about Enki's behavior")
    p.add_argument("question", help="Your question")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_ask)

    p = sub.add_parser("status", help="Check if review is due")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_evolution_status)
