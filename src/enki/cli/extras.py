"""Extra commands: style, onboarding, skills, summarization, worktree, simplify."""

import json
import sys
from pathlib import Path

from . import requires_db


# === Style Learning ===

@requires_db
def cmd_style_analyze(args):
    """Analyze working style patterns."""
    from ..style_learning import analyze_session_patterns

    patterns = analyze_session_patterns(days=args.days, project=args.project)
    if not patterns:
        print("Not enough data to detect working style patterns yet.")
        print(f"Try again after more sessions (analyzed last {args.days} days).")
        return
    print(f"Working Style Patterns (last {args.days} days)")
    print("=" * 50)
    for p in sorted(patterns, key=lambda x: -x.confidence):
        print(f"\n[{p.category}] {p.pattern}")
        print(f"  Confidence: {p.confidence:.0%}")
        print(f"  Evidence: {p.evidence_count} occurrences")
        if p.examples:
            print(f"  Examples: {', '.join(p.examples[:3])}")


@requires_db
def cmd_style_save(args):
    """Save detected patterns as beads."""
    from ..style_learning import analyze_session_patterns, save_style_patterns

    patterns = analyze_session_patterns(days=args.days, project=args.project)
    if not patterns:
        print("No patterns to save.")
        return
    bead_ids = save_style_patterns(patterns, project=args.project)
    print(f"Saved {len(bead_ids)} pattern(s) as beads:")
    for bid in bead_ids:
        print(f"  - {bid}")


@requires_db
def cmd_style_summary(args):
    """Show working style summary."""
    from ..style_learning import get_style_summary
    print(get_style_summary(project=args.project, days=args.days))


# === Onboarding ===

def cmd_onboard_preview(args):
    """Preview what onboarding would extract."""
    from ..onboarding import get_onboarding_preview
    project_path = Path(args.project) if args.project else Path.cwd()
    print(get_onboarding_preview(project_path))


def cmd_onboard_run(args):
    """Run project onboarding."""
    from ..onboarding import onboard_project

    project_path = Path(args.project) if args.project else Path.cwd()
    print(f"Onboarding project: {project_path.name}")
    print()
    extracted = onboard_project(project_path, dry_run=False)
    if not extracted:
        print("No knowledge found to extract from this project's documentation.")
        return

    by_type = {}
    for item in extracted:
        if item.bead_type not in by_type:
            by_type[item.bead_type] = []
        by_type[item.bead_type].append(item)

    saved_count = sum(1 for item in extracted if item.confidence >= 0.5)
    print(f"Extracted {len(extracted)} pieces of knowledge:")
    for bead_type, items in by_type.items():
        print(f"  {bead_type}: {len(items)}")
    print()
    print(f"Created {saved_count} beads (confidence >= 50%)")
    print()
    print("Run 'enki recall onboarded' to see extracted knowledge.")


def cmd_onboard_status(args):
    """Check onboarding status."""
    from ..onboarding import get_onboarding_status

    project_path = Path(args.project) if args.project else Path.cwd()
    status = get_onboarding_status(project_path)
    print(f"Project: {project_path.name}")
    print(f"Onboarded: {'Yes' if status['onboarded'] else 'No'}")
    if status['onboarded']:
        print(f"Beads created: {status['bead_count']}")
    print(f"Has .enki directory: {'Yes' if status['has_enki_dir'] else 'No'}")
    if status['available_docs']:
        print(f"Available docs: {', '.join(status['available_docs'])}")
    else:
        print("Available docs: None found")


# === Skills ===

def cmd_skills_list(args):
    """List available skills."""
    from ..skills import list_available_skills

    skills = list_available_skills()
    if args.json:
        print(json.dumps(skills, indent=2))
    else:
        print("Available Skills:")
        print("=" * 50)
        for skill in skills:
            print(f"\n/{skill['name']}")
            print(f"  Agent: {skill['agent']}")
            print(f"  Description: {skill['description']}")
            if skill['options']:
                print(f"  Options: {', '.join(skill['options'])}")


def cmd_skills_prompt(args):
    """Show skill invocation prompt."""
    from ..skills import SKILLS, get_skill_prompt

    if args.skill not in SKILLS:
        print(f"Unknown skill: {args.skill}")
        print(f"Available skills: {', '.join(SKILLS.keys())}")
        sys.exit(1)
    files = args.files.split(",") if args.files else None
    print(get_skill_prompt(skill_name=args.skill, target_files=files, context=args.context))


# === Summarization ===

def cmd_summarize_preview(args):
    """Preview what would be summarized."""
    from ..summarization import get_summarization_preview
    print(get_summarization_preview(project=args.project))


def cmd_summarize_run(args):
    """Run summarization."""
    from ..summarization import run_session_summarization

    print("Running session summarization...")
    print()
    results = run_session_summarization(project=args.project, dry_run=False, max_beads=args.max)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Candidates found: {results['candidates_found']}")
        print(f"Beads summarized: {results['summarized']}")
        print(f"Space saved: {results['space_saved_chars']:,} characters")
        if results['beads_processed']:
            print()
            print("Processed beads:")
            for p in results['beads_processed']:
                if 'error' in p:
                    print(f"  - {p['old_id']}: ERROR - {p['error']}")
                else:
                    print(f"  - {p['old_id']} -> {p['new_id']} (saved {p['saved']} chars)")


@requires_db
def cmd_summarize_stats(args):
    """Show summarization statistics."""
    from ..summarization import get_summarization_stats

    stats = get_summarization_stats()
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print("Summarization Statistics")
        print("=" * 40)
        print(f"Already summarized: {stats['summarized_count']} beads")
        print(f"Candidates for summarization: {stats['candidates_count']} beads")
        print(f"Potential space savings: {stats['potential_savings_chars']:,} characters")


# === Worktree ===

def cmd_worktree_create(args):
    """Create a worktree for a task."""
    from ..worktree import create_worktree

    project_path = Path(args.project) if args.project else None
    try:
        path = create_worktree(
            task_id=args.task_id, branch_name=args.branch,
            base_branch=args.base or "main", project_path=project_path,
        )
        if args.json:
            print(json.dumps({
                "path": str(path), "task_id": args.task_id,
                "branch": args.branch or f"enki/{args.task_id}",
            }))
        else:
            print(f"Worktree created: {path}")
            print(f"Branch: {args.branch or f'enki/{args.task_id}'}")
    except (ValueError, Exception) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_worktree_list(args):
    """List all worktrees."""
    from ..worktree import list_worktrees

    project_path = Path(args.project) if args.project else None
    trees = list_worktrees(project_path)
    if args.json:
        print(json.dumps([
            {"task_id": t.task_id, "path": str(t.path), "branch": t.branch}
            for t in trees
        ], indent=2))
    else:
        if not trees:
            print("No worktrees found.")
        else:
            print("Worktrees:")
            for t in trees:
                marker = " (main)" if not t.task_id else ""
                print(f"  - {t.path} [{t.branch}]{marker}")


def cmd_worktree_exec(args):
    """Execute a command in a worktree."""
    from ..worktree import exec_in_worktree

    project_path = Path(args.project) if args.project else None
    try:
        if args.no_wait:
            proc = exec_in_worktree(
                args.task_id, args.command, project_path=project_path, wait=False,
            )
            print(f"Started process with PID: {proc.pid}")
        else:
            result = exec_in_worktree(
                args.task_id, args.command, project_path=project_path, wait=True,
            )
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_worktree_merge(args):
    """Merge a worktree back to target branch."""
    from ..worktree import merge_worktree

    project_path = Path(args.project) if args.project else None
    success = merge_worktree(
        task_id=args.task_id, target_branch=args.into or "main",
        delete_after=not args.keep, project_path=project_path,
    )
    if success:
        print(f"Merged {args.task_id} into {args.into or 'main'}")
        if not args.keep:
            print("Worktree removed.")
    else:
        print(f"Error: Failed to merge {args.task_id}", file=sys.stderr)
        sys.exit(1)


def cmd_worktree_remove(args):
    """Remove a worktree."""
    from ..worktree import remove_worktree

    project_path = Path(args.project) if args.project else None
    success = remove_worktree(task_id=args.task_id, force=args.force, project_path=project_path)
    if success:
        print(f"Removed worktree: {args.task_id}")
    else:
        print(f"Error: Failed to remove worktree {args.task_id}", file=sys.stderr)
        sys.exit(1)


# === Simplifier ===

def cmd_simplify(args):
    """Run code simplification."""
    from ..simplifier import run_simplification

    project_path = Path(args.project) if args.project else None
    files = args.files if hasattr(args, 'files') and args.files else None
    all_modified = args.all_modified if hasattr(args, 'all_modified') else False

    params = run_simplification(files=files, all_modified=all_modified, project_path=project_path)
    if hasattr(args, 'json') and args.json:
        print(json.dumps(params, indent=2))
    else:
        print("Simplifier Agent Parameters")
        print("=" * 40)
        print(f"Description: {params['description']}")
        if params.get('files'):
            print(f"Files: {', '.join(params['files'])}")
        else:
            print("Files: (will detect modified files)")
        print()
        print("To spawn the Simplifier agent, use the Task tool with these parameters.")
        print("-" * 40)
        print()
        print("Prompt preview (first 500 chars):")
        print(params['prompt'][:500])
        if len(params['prompt']) > 500:
            print("...")


def register(subparsers):
    """Register style, onboarding, skills, summarization, worktree, and simplify commands."""

    # === Style Learning ===
    style_parser = subparsers.add_parser("style", help="Working style learning")
    sub = style_parser.add_subparsers(dest="style_command")

    p = sub.add_parser("analyze", help="Analyze working style patterns")
    p.add_argument("-d", "--days", type=int, default=30, help="Days to analyze")
    p.add_argument("-p", "--project", help="Project filter")
    p.set_defaults(func=cmd_style_analyze)

    p = sub.add_parser("save", help="Save detected patterns as beads")
    p.add_argument("-d", "--days", type=int, default=30, help="Days to analyze")
    p.add_argument("-p", "--project", help="Project filter")
    p.set_defaults(func=cmd_style_save)

    p = sub.add_parser("summary", help="Show working style summary")
    p.add_argument("-d", "--days", type=int, default=30, help="Days to analyze")
    p.add_argument("-p", "--project", help="Project filter")
    p.set_defaults(func=cmd_style_summary)

    # === Onboarding ===
    p = subparsers.add_parser("onboard", help="Project onboarding")
    p.add_argument("-p", "--project", help="Project path (default: current directory)")
    p.add_argument("--confirm", action="store_true", help="Run onboarding (creates beads)")
    p.add_argument("--status", action="store_true", help="Check onboarding status")
    p.set_defaults(func=lambda args:
        cmd_onboard_status(args) if args.status else
        cmd_onboard_run(args) if args.confirm else
        cmd_onboard_preview(args))

    # === Skills ===
    skills_parser = subparsers.add_parser("skills", help="Prism skills integration")
    skills_parser.add_argument("--json", action="store_true", help="JSON output")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")

    p = skills_sub.add_parser("list", help="List available skills")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_skills_list)

    p = skills_sub.add_parser("prompt", help="Show skill invocation prompt")
    p.add_argument("skill", help="Skill name (e.g., review, security-review)")
    p.add_argument("-f", "--files", help="Comma-separated file paths")
    p.add_argument("-c", "--context", help="Additional context")
    p.set_defaults(func=cmd_skills_prompt)

    skills_parser.set_defaults(func=cmd_skills_list)

    # === Summarization ===
    p = subparsers.add_parser("summarize", help="Summarize verbose beads")
    p.add_argument("-p", "--project", help="Project filter")
    p.add_argument("--confirm", action="store_true", help="Actually run summarization")
    p.add_argument("--stats", action="store_true", help="Show summarization stats")
    p.add_argument("--max", type=int, default=10, help="Max beads to summarize")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=lambda args:
        cmd_summarize_stats(args) if args.stats else
        cmd_summarize_run(args) if args.confirm else
        cmd_summarize_preview(args))

    # === Worktree ===
    worktree_parser = subparsers.add_parser("worktree", help="Git worktree management")
    sub = worktree_parser.add_subparsers(dest="worktree_command")

    p = sub.add_parser("create", help="Create worktree for task")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("-b", "--branch", help="Branch name (default: enki/TASK_ID)")
    p.add_argument("--base", default="main", help="Base branch to branch from")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_worktree_create)

    p = sub.add_parser("list", help="List worktrees")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_worktree_list)

    p = sub.add_parser("exec", help="Execute command in worktree")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("--no-wait", action="store_true", help="Run async (don't wait)")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("command", nargs="+", help="Command to run")
    p.set_defaults(func=cmd_worktree_exec)

    p = sub.add_parser("merge", help="Merge worktree")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("--into", default="main", help="Target branch")
    p.add_argument("--keep", action="store_true", help="Keep worktree after merge")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_worktree_merge)

    p = sub.add_parser("remove", help="Remove worktree")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("--force", action="store_true", help="Force removal")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_worktree_remove)

    worktree_parser.set_defaults(func=cmd_worktree_list)

    # === Simplifier ===
    p = subparsers.add_parser("simplify", help="Run code simplification")
    p.add_argument("--files", nargs="+", help="Specific files to simplify")
    p.add_argument("--all-modified", action="store_true",
        help="Simplify all modified files (from git)")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_simplify)
