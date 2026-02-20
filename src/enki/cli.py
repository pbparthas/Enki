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

from enki import __version__
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

    run_setup(
        project_dir=args.project_dir,
        assistant_name=args.assistant_name,
        interactive=not args.non_interactive,
    )


def cmd_hooks_deploy(args):
    """Deploy hook scripts to ~/.claude/hooks/."""
    from pathlib import Path

    from enki.hook_versioning import deploy_hooks

    source_dir = args.source_dir
    if not source_dir:
        source_dir = str(
            Path(__file__).resolve().parent.parent.parent / "scripts" / "hooks"
        )
    deployed = deploy_hooks(source_dir=source_dir, target_dir=args.target_dir)
    if deployed:
        print(f"Deployed {len(deployed)} hooks to {args.target_dir}:")
        for hook in deployed:
            print(f"  - {hook}")
    else:
        print(f"No expected hooks found in source directory: {source_dir}")


def cmd_session_end(args):
    """Finalize current session: extract beads, run feedback loop, archive."""
    from pathlib import Path

    from enki.db import ENKI_ROOT
    from enki.gates.feedback import generate_session_proposals
    from enki.gates.uru import end_session as uru_end_session
    from enki.memory.abzu import finalize_session

    # Read current session ID
    session_path = ENKI_ROOT / "SESSION_ID"
    if session_path.exists():
        session_id = session_path.read_text().strip()
    else:
        session_id = "unknown"

    project = args.project

    print(f"Ending session: {session_id[:12]}...")
    print(f"Project: {project}")
    print()

    # Step 1: Finalize memory — extract candidates, reconcile summaries, run decay
    print("Memory finalization...")
    mem_result = finalize_session(session_id, project)
    candidates = mem_result.get("candidates_extracted", 0) if isinstance(mem_result, dict) else 0
    summary_id = mem_result.get("summary_id") if isinstance(mem_result, dict) else None
    print(f"  Candidates extracted: {candidates}")
    if summary_id:
        print(f"  Final summary: {summary_id[:12]}...")

    # Step 2: Enforcement summary
    print("\nEnforcement summary...")
    uru_result = uru_end_session(session_id)
    enforcement = uru_result.get("enforcement", {})
    if enforcement:
        for action, count in enforcement.items():
            print(f"  {action}: {count}")
    else:
        print("  No enforcement events this session")

    # Step 3: Feedback loop — propose gate adjustments
    print("\nFeedback loop...")
    proposals = generate_session_proposals(session_id)
    if proposals:
        print(f"  Generated {len(proposals)} proposal(s) for review")
    else:
        print("  No adjustment proposals")

    # Step 4: Archive session summary to .enki/sessions/
    sessions_dir = ENKI_ROOT / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = sessions_dir / f"{timestamp}-{session_id[:8]}.md"

    archive_lines = [
        f"# Session {session_id[:12]}",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Project:** {project}",
        "",
        "## Summary",
        f"- Candidates extracted: {candidates}",
        f"- Enforcement events: {sum(enforcement.values()) if enforcement else 0}",
        f"- Feedback proposals: {len(proposals)}",
    ]

    # Add goal/phase if available
    try:
        from enki.orch.tiers import get_project_state
        state = get_project_state(project)
        if state.get("goal"):
            archive_lines.insert(3, f"**Goal:** {state['goal']}")
        if state.get("phase"):
            archive_lines.insert(4, f"**Phase:** {state['phase']}")
    except Exception:
        pass

    archive_path.write_text("\n".join(archive_lines) + "\n")

    # Print final summary table
    print(f"\n{'─' * 40}")
    print(f"Session archived: {archive_path.name}")
    print(f"  Beads captured:    {candidates}")
    print(f"  Enforcement:       {sum(enforcement.values()) if enforcement else 0} events")
    print(f"  Proposals:         {len(proposals)}")
    print(f"{'─' * 40}")


def cmd_checkpoint(args):
    """Create a session checkpoint."""
    from enki.orch.checkpoints import checkpoint_session

    cid = checkpoint_session(args.project, label=args.label)
    print(f"Checkpoint created: {cid[:12]}...")
    if args.label:
        print(f"  Label: {args.label}")


def cmd_list_checkpoints(args):
    """List session checkpoints."""
    from enki.orch.checkpoints import list_checkpoints

    checkpoints = list_checkpoints(args.project)
    if not checkpoints:
        print("No checkpoints found.")
        return

    print(f"{'ID':<14} {'Label':<20} {'Goal':<30} {'Phase':<10} {'Created'}")
    print("─" * 90)
    for cp in checkpoints:
        cid = cp["id"][:12] + "..."
        label = (cp.get("label") or "—")[:20]
        goal = (cp.get("goal") or "—")[:30]
        phase = cp.get("phase") or "—"
        created = cp.get("created_at", "")[:19]
        print(f"{cid:<14} {label:<20} {goal:<30} {phase:<10} {created}")


def cmd_resume(args):
    """Resume from a session checkpoint."""
    from enki.orch.checkpoints import resume_session

    result = resume_session(args.project, args.checkpoint_id)
    if result.get("error"):
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"Session resumed from checkpoint {args.checkpoint_id[:12]}...")
    if result.get("label"):
        print(f"  Label: {result['label']}")
    print(f"  Goal: {result.get('goal', '—')}")
    print(f"  Phase: {result.get('phase', '—')}")
    print(f"  Tier: {result.get('tier', '—')}")
    print(f"  Active sprints: {result.get('active_sprints', 0)}")
    print(f"  Active tasks: {result.get('active_tasks', 0)}")
    print(f"  Recent beads: {result.get('recent_bead_count', 0)}")


def cmd_github_sync(args):
    """Sync Enki tasks with GitHub Issues."""
    from enki.integrations.github import (
        REQUESTS_AVAILABLE,
        sync_issues_to_tasks,
        sync_tasks_to_issues,
    )

    if not REQUESTS_AVAILABLE:
        print("GitHub integration requires the 'requests' library.")
        print("Install with: pip install requests")
        return

    print(f"Syncing project '{args.project}' with GitHub...")
    push = sync_tasks_to_issues(args.project)
    if push.get("skipped"):
        print(f"  Skipped: {push['reason']}")
        return

    print(f"  Push: {push.get('created', 0)} issues created, "
          f"{push.get('skipped', 0)} skipped")
    if push.get("errors"):
        for e in push["errors"]:
            print(f"  Error: {e}")

    pull = sync_issues_to_tasks(args.project)
    print(f"  Pull: {pull.get('updated', 0)} tasks updated, "
          f"{pull.get('unchanged', 0)} unchanged")


def cmd_github_link(args):
    """Configure GitHub repo connection."""
    from enki.integrations.github import REQUESTS_AVAILABLE

    if not REQUESTS_AVAILABLE:
        print("GitHub integration requires the 'requests' library.")
        print("Install with: pip install requests")
        return

    print(f"To link a GitHub repo, add this to ~/.enki/config/enki.toml:\n")
    print(f'[integrations.github]')
    print(f'repo = "{args.repo}"')
    print(f'token = "ghp_your_personal_access_token"')
    print(f'\nThen run: enki github sync -p <project>')


def cmd_freshness(args):
    """Check beads for stale version references."""
    from pathlib import Path
    from enki.memory.retention import check_freshness, dismiss_freshness

    if args.dismiss:
        success = dismiss_freshness(args.dismiss)
        if success:
            print(f"Dismissed freshness checks for bead {args.dismiss}")
        else:
            print(f"No freshness checks found for bead {args.dismiss}")
        return

    project_path = Path(args.project_path).resolve() if args.check else None
    results = check_freshness(project_path)

    if not results:
        print("No versioned references found in beads.")
        return

    stale = [r for r in results if r["status"] == "stale"]
    unknown = [r for r in results if r["status"] == "unknown"]
    current = [r for r in results if r["status"] == "current"]

    print(f"Freshness scan: {len(results)} versioned references found")
    print(f"  Current: {len(current)}  |  Stale: {len(stale)}  |  Unknown: {len(unknown)}")

    if stale:
        print(f"\nStale references:")
        for r in stale:
            print(f"  [{r['bead_id'][:8]}...] {r['detected_version']}"
                  f" → project has {r['current_version']}")
            print(f"    \"{r['content_excerpt']}\"")

    if unknown and not stale:
        print(f"\nUnknown (no project version to compare):")
        for r in unknown[:5]:
            print(f"  [{r['bead_id'][:8]}...] {r['detected_version']}")

    if stale:
        print(f"\nDismiss with: enki freshness --dismiss <bead_id>")


def cmd_synthesize(args):
    """Consolidate clusters of related beads into synthesis candidates."""
    from enki.memory.summarization import synthesize_knowledge
    import json as _json

    syntheses = synthesize_knowledge(
        project=getattr(args, "project", None),
        min_cluster_size=args.min_cluster,
        auto_apply=args.apply,
    )

    if not syntheses:
        print("No clusters found with enough related beads.")
        return

    if args.apply:
        print(f"Created {len(syntheses)} synthesis candidates (in staging):")
    else:
        print(f"Found {len(syntheses)} clusters (dry run — use --apply to create):")

    for s in syntheses:
        cid = s.get("candidate_id", "—")
        print(f"\n  [{s['category']}] {s['theme']} ({s['count']} beads)")
        if args.apply:
            print(f"    Candidate ID: {cid}")
        print(f"    Source beads: {len(s['source_bead_ids'])}")
        print(f"    Avg weight: {s['avg_weight']}")


def cmd_heal(args):
    """Detect and fix broken file path references in beads."""
    from pathlib import Path
    from enki.memory.beads import check_bead_references, heal_bead_references

    project_path = Path(args.project_path).resolve()

    if args.report:
        import json as _json
        refs = check_bead_references(project_path)
        print(_json.dumps(refs, indent=2))
        return

    if args.apply:
        result = heal_bead_references(project_path, auto_heal=True)
        print(f"Healed: {result['healed']}")
        print(f"Missing (no suggestion): {result['missing']}")
        print(f"Unchanged: {result['unchanged']}")
    else:
        # Dry run
        refs = check_bead_references(project_path)
        moved = [r for r in refs if r["status"] == "moved"]
        missing = [r for r in refs if r["status"] == "missing"]
        ok = [r for r in refs if r["status"] == "ok"]

        print(f"References found: {len(refs)}")
        print(f"  OK: {len(ok)}")
        print(f"  Moved (can heal): {len(moved)}")
        print(f"  Missing (no match): {len(missing)}")

        if moved:
            print("\nWould heal:")
            for r in moved:
                print(f"  {r['referenced_path']} → {r['suggested_path']}")

        if missing:
            print("\nMissing (no suggestion):")
            for r in missing[:10]:
                print(f"  {r['referenced_path']}")

        if moved:
            print(f"\nRun 'enki heal --apply {args.project_path}' to apply fixes.")


def cmd_digest_weekly(args):
    """Show weekly knowledge digest."""
    from enki.memory.summarization import generate_weekly_digest
    import json as _json

    result = generate_weekly_digest(
        project=getattr(args, "project", None),
        as_json=args.json,
    )
    if args.json:
        print(_json.dumps(result, indent=2, default=str))
    else:
        print(result)


def cmd_digest_monthly(args):
    """Show monthly knowledge synthesis."""
    from enki.memory.summarization import generate_monthly_synthesis
    import json as _json

    result = generate_monthly_synthesis(
        project=getattr(args, "project", None),
        as_json=args.json,
    )
    if args.json:
        print(_json.dumps(result, indent=2, default=str))
    else:
        print(result)


def cmd_rejections(args):
    """Show recent bouncer rejections."""
    from enki.memory.staging import list_rejections

    rejections = list_rejections(limit=args.limit)
    if not rejections:
        print("No rejections found. The bouncer hasn't rejected anything yet.")
        return

    print(f"{'ID':<6} {'Reason':<25} {'Source':<10} {'Content'}")
    print("─" * 80)
    for r in rejections:
        content_preview = (r["content"] or "")[:50]
        if len(r.get("content", "")) > 50:
            content_preview += "..."
        source = r.get("source") or "—"
        print(f"{r['id']:<6} {r['reason']:<25} {source:<10} {content_preview}")


def cmd_rejections_override(args):
    """Override a bouncer rejection, pushing it into staging."""
    from enki.memory.staging import override_rejection

    candidate_id = override_rejection(args.rejection_id)
    if candidate_id:
        print(f"Rejection #{args.rejection_id} overridden.")
        print(f"New staging candidate: {candidate_id}")
    else:
        print(f"Rejection #{args.rejection_id} not found.")


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
    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {__version__}",
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
        "setup", help="First-run setup (project dir, assistant name, hooks, MCP)"
    )
    setup_parser.add_argument(
        "--project-dir", help="Project directory (default: current directory)"
    )
    setup_parser.add_argument(
        "--assistant-name", help="Assistant name (default: Enki)"
    )
    setup_parser.add_argument(
        "--non-interactive", action="store_true",
        help="Skip prompts, use defaults",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # hooks (parent with subcommands)
    hooks_parser = subparsers.add_parser(
        "hooks", help="Hook lifecycle commands"
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command")

    hooks_deploy = hooks_sub.add_parser("deploy", help="Deploy Enki hooks")
    hooks_deploy.add_argument(
        "--source-dir",
        default=None,
        help="Source hooks directory (default: repo scripts/hooks)",
    )
    hooks_deploy.add_argument(
        "--target-dir",
        default=str((ENKI_ROOT.parent / ".claude" / "hooks").expanduser()),
        help="Target hooks directory (default: ~/.claude/hooks)",
    )
    hooks_deploy.set_defaults(func=cmd_hooks_deploy)

    # session (parent with subcommands)
    session_parser = subparsers.add_parser(
        "session", help="Session lifecycle commands"
    )
    session_sub = session_parser.add_subparsers(dest="session_command")

    session_end = session_sub.add_parser(
        "end", help="Finalize session: extract beads, feedback loop, archive"
    )
    session_end.add_argument(
        "--project", "-p", default=".",
        help="Project ID (default: .)",
    )
    session_end.set_defaults(func=cmd_session_end)

    # checkpoint
    cp_parser = subparsers.add_parser(
        "checkpoint", help="Create a session checkpoint"
    )
    cp_parser.add_argument(
        "--project", "-p", required=True, help="Project ID"
    )
    cp_parser.add_argument(
        "label", nargs="?", default=None, help="Optional checkpoint label"
    )
    cp_parser.set_defaults(func=cmd_checkpoint)

    # checkpoints (list)
    cps_parser = subparsers.add_parser(
        "checkpoints", help="List session checkpoints"
    )
    cps_parser.add_argument(
        "--project", "-p", required=True, help="Project ID"
    )
    cps_parser.set_defaults(func=cmd_list_checkpoints)

    # resume
    resume_parser = subparsers.add_parser(
        "resume", help="Resume from a session checkpoint"
    )
    resume_parser.add_argument(
        "--project", "-p", required=True, help="Project ID"
    )
    resume_parser.add_argument(
        "checkpoint_id", help="Checkpoint ID to resume from"
    )
    resume_parser.set_defaults(func=cmd_resume)

    # github (parent with subcommands)
    github_parser = subparsers.add_parser(
        "github", help="GitHub Issues integration"
    )
    github_sub = github_parser.add_subparsers(dest="github_command")

    gh_sync = github_sub.add_parser("sync", help="Sync tasks with GitHub Issues")
    gh_sync.add_argument("--project", "-p", required=True, help="Project ID")
    gh_sync.set_defaults(func=cmd_github_sync)

    gh_link = github_sub.add_parser("link", help="Configure GitHub repo connection")
    gh_link.add_argument("repo", help="GitHub repo (owner/name)")
    gh_link.set_defaults(func=cmd_github_link)

    # freshness
    fresh_parser = subparsers.add_parser(
        "freshness", help="Check beads for stale version references"
    )
    fresh_parser.add_argument(
        "--check", action="store_true",
        help="Re-run detection against current project files",
    )
    fresh_parser.add_argument(
        "--dismiss", metavar="BEAD_ID",
        help="Dismiss freshness checks for a bead (mark as reviewed)",
    )
    fresh_parser.add_argument(
        "project_path", nargs="?", default=".",
        help="Project path for version comparison (default: .)",
    )
    fresh_parser.set_defaults(func=cmd_freshness)

    # synthesize
    synth_parser = subparsers.add_parser(
        "synthesize", help="Consolidate related beads into synthesis candidates"
    )
    synth_parser.add_argument(
        "--project", "-p", help="Filter by project"
    )
    synth_parser.add_argument(
        "--apply", action="store_true",
        help="Create synthesis candidates (default: dry run)",
    )
    synth_parser.add_argument(
        "--min-cluster", type=int, default=3,
        help="Minimum beads per cluster (default: 3)",
    )
    synth_parser.set_defaults(func=cmd_synthesize)

    # heal
    heal_parser = subparsers.add_parser(
        "heal", help="Detect and fix broken file path references in beads"
    )
    heal_parser.add_argument(
        "project_path", nargs="?", default=".",
        help="Project path to check references against (default: .)",
    )
    heal_parser.add_argument(
        "--apply", action="store_true",
        help="Actually update bead content (default: dry run)",
    )
    heal_parser.add_argument(
        "--report", action="store_true",
        help="JSON output of all references",
    )
    heal_parser.set_defaults(func=cmd_heal)

    # digest (parent with subcommands)
    digest_parser = subparsers.add_parser(
        "digest", help="Knowledge activity digests"
    )
    digest_sub = digest_parser.add_subparsers(dest="digest_command")

    dg_weekly = digest_sub.add_parser("weekly", help="Weekly knowledge digest")
    dg_weekly.add_argument("--project", "-p", help="Filter by project")
    dg_weekly.add_argument("--json", action="store_true", help="JSON output")
    dg_weekly.set_defaults(func=cmd_digest_weekly)

    dg_monthly = digest_sub.add_parser("monthly", help="Monthly knowledge synthesis")
    dg_monthly.add_argument("--project", "-p", help="Filter by project")
    dg_monthly.add_argument("--json", action="store_true", help="JSON output")
    dg_monthly.set_defaults(func=cmd_digest_monthly)

    # rejections (parent with subcommands)
    rej_parser = subparsers.add_parser(
        "rejections", help="Bouncer rejection log"
    )
    rej_parser.add_argument(
        "--limit", "-n", type=int, default=20,
        help="Number of rejections to show (default: 20)",
    )
    rej_parser.set_defaults(func=cmd_rejections)
    rej_sub = rej_parser.add_subparsers(dest="rejections_command")

    rej_override = rej_sub.add_parser(
        "override", help="Push a rejected item into staging"
    )
    rej_override.add_argument(
        "rejection_id", type=int, help="Rejection ID to override"
    )
    rej_override.set_defaults(func=cmd_rejections_override)

    # review
    review_parser = subparsers.add_parser(
        "review", help="Generate Gemini review package"
    )
    review_parser.set_defaults(func=cmd_review)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not hasattr(args, "func"):
        # Nested subcommand not provided
        if args.command == "github":
            github_parser.print_help()
        elif args.command == "digest":
            digest_parser.print_help()
        elif args.command == "session":
            session_parser.print_help()
        elif args.command == "hooks":
            hooks_parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
