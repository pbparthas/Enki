"""PM commands: debate, plan, approve, specs, spec show, decompose."""

import sys
from pathlib import Path

from . import requires_db


@requires_db
def cmd_debate(args):
    """Start debate phase - generate perspectives."""
    from ..session import set_phase
    from ..pm import generate_perspectives

    project_path = Path(args.project) if args.project else None
    set_phase("debate", project_path)
    perspectives_path = generate_perspectives(
        goal=args.goal, context=args.context, project_path=project_path,
    )
    print(f"Debate started for: {args.goal}")
    print(f"Perspectives template created: {perspectives_path}")
    print()
    print("Fill in ALL perspectives before running 'enki plan':")
    print("  - PM Perspective")
    print("  - CTO Perspective")
    print("  - Architect Perspective")
    print("  - DBA Perspective")
    print("  - Security Perspective")
    print("  - Devil's Advocate")
    print()
    print("Check status with: enki debate --check")


def cmd_debate_check(args):
    """Check if debate perspectives are complete."""
    from ..pm import check_perspectives_complete

    project_path = Path(args.project) if args.project else None
    is_complete, missing = check_perspectives_complete(project_path)
    if is_complete:
        print("All perspectives complete. Ready for 'enki plan'.")
    else:
        print("Perspectives incomplete. Missing:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


@requires_db
def cmd_plan(args):
    """Create a spec from debate."""
    from ..pm import create_spec

    project_path = Path(args.project) if args.project else None
    try:
        spec_path = create_spec(
            name=args.name, problem=args.problem,
            solution=args.solution, project_path=project_path,
        )
        print(f"Spec created: {spec_path}")
        print()
        print("Edit the spec to fill in details, then run:")
        print(f"  enki approve {args.name}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


@requires_db
def cmd_approve(args):
    """Approve a spec. Atomic HITL flow: generate token + approve + consume."""
    from ..pm import approve_spec, generate_approval_token

    project_path = Path(args.project) if args.project else None
    try:
        # Gate 6: Atomic token flow — CLI is human-invoked, so generate + consume here
        token = generate_approval_token(project_path)
        approve_spec(args.name, project_path, approval_token=token)
        print(f"Spec approved: {args.name}")
        print("Phase transitioned to: implement")
        print()
        print("Gate 2 (Spec Approval) is now satisfied.")
        print("You can now spawn implementation agents.")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_specs(args):
    """List all specs."""
    from ..pm import list_specs

    project_path = Path(args.project) if args.project else None
    specs = list_specs(project_path)
    if not specs:
        print("No specs found.")
        return
    print("Specs:")
    for spec in specs:
        status = "[APPROVED]" if spec["approved"] else "[pending]"
        print(f"  {status} {spec['name']}")
        if args.verbose:
            print(f"         {spec['path']}")


def cmd_spec_show(args):
    """Show a spec."""
    from ..pm import get_spec

    project_path = Path(args.project) if args.project else None
    content = get_spec(args.name, project_path)
    if content:
        print(content)
    else:
        print(f"Spec not found: {args.name}")
        sys.exit(1)


@requires_db
def cmd_decompose(args):
    """Decompose spec into task graph."""
    from ..pm import decompose_spec, save_task_graph, is_spec_approved

    project_path = Path(args.project) if args.project else None
    try:
        if not is_spec_approved(args.name, project_path):
            print(f"Spec not approved: {args.name}")
            print("Run 'enki approve {name}' first.")
            sys.exit(1)

        graph = decompose_spec(args.name, project_path)
        save_task_graph(graph, project_path)

        print(f"Task graph created for: {args.name}")
        print()
        waves = graph.get_waves()
        for i, wave in enumerate(waves, 1):
            print(f"Wave {i}:")
            for task in wave:
                deps = ", ".join(task.dependencies) if task.dependencies else "none"
                print(f"  - {task.id}: {task.description} ({task.agent})")
                print(f"    Dependencies: {deps}")
                if task.files_in_scope:
                    print(f"    Files: {', '.join(task.files_in_scope)}")
            print()
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def register(subparsers):
    """Register PM commands."""
    p = subparsers.add_parser("debate", help="Start debate phase")
    p.add_argument("goal", nargs="?", help="Feature/change to debate")
    p.add_argument("-c", "--context", help="Additional context")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--check", action="store_true", help="Check if perspectives complete")
    p.set_defaults(func=lambda args: cmd_debate_check(args) if args.check else cmd_debate(args))

    p = subparsers.add_parser("plan", help="Create spec from debate")
    p.add_argument("name", help="Spec name")
    p.add_argument("--problem", help="Problem statement")
    p.add_argument("--solution", help="Proposed solution")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_plan)

    p = subparsers.add_parser("approve", help="Approve a spec")
    p.add_argument("name", help="Spec name to approve")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_approve)

    p = subparsers.add_parser("specs", help="List specs")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("-v", "--verbose", action="store_true", help="Show paths")
    p.set_defaults(func=cmd_specs)

    p = subparsers.add_parser("spec", help="Show a spec")
    p.add_argument("name", help="Spec name")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_spec_show)

    p = subparsers.add_parser("decompose", help="Decompose spec into tasks")
    p.add_argument("name", help="Spec name")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_decompose)
