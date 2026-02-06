"""Orchestration commands: orchestration, tasks, bugs, HITL, spawn, agents."""

import json
import sys
from pathlib import Path

from . import requires_db


def cmd_orchestration_status(args):
    """Show orchestration status."""
    from ..orchestrator import get_full_orchestration_status, get_next_action

    project_path = Path(args.project) if args.project else None
    status = get_full_orchestration_status(project_path)

    if not status["active"]:
        print("No active orchestration.")
        print("Run 'enki orchestrate <spec-name>' to start one.")
        return

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"Orchestration: {status['spec']} ({status['orchestration_id']})")
        print(f"Status: {status['status']}")
        print(f"Wave: {status['current_wave']}")
        print(f"Progress: {status['tasks']['completed']}/{status['tasks']['total']} tasks ({status['tasks']['progress']:.0%})")
        print()

        if status['hitl']['required']:
            print("⚠️  HUMAN INTERVENTION REQUIRED")
            print(f"   Reason: {status['hitl']['reason']}")
            print()

        if status['bugs']['open'] > 0:
            print(f"Bugs: {status['bugs']['open']} open ({status['bugs']['critical']} critical)")
            print()

        if status['tasks']['ready']:
            print(f"Ready tasks: {', '.join(status['tasks']['ready'])}")

        next_action = get_next_action(project_path)
        print()
        print(f"Next: {next_action['message']}")


@requires_db
def cmd_orchestrate_start(args):
    """Start orchestration from an approved spec."""
    from ..pm import decompose_spec
    from ..orchestrator import start_orchestration, get_next_action

    project_path = Path(args.project) if args.project else None
    try:
        graph = decompose_spec(args.name, project_path)
        orch = start_orchestration(args.name, graph, project_path)
        print(f"Orchestration started: {orch.id}")
        print(f"Spec: {args.name}")
        print(f"Tasks: {len(graph.tasks)}")
        print()
        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_start(args):
    """Start a task."""
    from ..orchestrator import start_task, AGENTS

    project_path = Path(args.project) if args.project else None
    try:
        task = start_task(args.task_id, project_path)
        print(f"Task started: {task.id}")
        print(f"Agent: {task.agent}")
        print(f"Description: {task.description}")
        if task.agent in AGENTS:
            agent_info = AGENTS[task.agent]
            print(f"Role: {agent_info['role']}")
            print(f"Tools: {', '.join(agent_info['tools'])}")
        if task.files_in_scope:
            print(f"Files: {', '.join(task.files_in_scope)}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_complete(args):
    """Complete a task."""
    from ..orchestrator import complete_task, get_next_action

    project_path = Path(args.project) if args.project else None
    try:
        task = complete_task(args.task_id, args.output, project_path)
        print(f"Task completed: {task.id}")
        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_task_fail(args):
    """Mark a task as failed."""
    from ..orchestrator import fail_task

    project_path = Path(args.project) if args.project else None
    try:
        task = fail_task(args.task_id, args.reason, project_path)
        if task.status == "failed":
            print(f"Task failed (max attempts reached): {task.id}")
            print("HITL escalation triggered.")
        else:
            print(f"Task will retry: {task.id} (attempt {task.attempts}/{task.max_attempts})")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_file(args):
    """File a new bug."""
    from ..orchestrator import file_bug

    project_path = Path(args.project) if args.project else None
    try:
        bug = file_bug(
            title=args.title, description=args.description or args.title,
            found_by=args.found_by, severity=args.severity,
            related_task=args.task, project_path=project_path,
        )
        print(f"Bug filed: {bug.id}")
        print(f"Title: {bug.title}")
        print(f"Severity: {bug.severity}")
        print(f"Assigned to: {bug.assigned_to}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_list(args):
    """List bugs."""
    from ..orchestrator import get_open_bugs

    project_path = Path(args.project) if args.project else None
    bugs = get_open_bugs(project_path)
    if not bugs:
        print("No open bugs.")
        return
    print("Open Bugs:")
    for bug in bugs:
        severity_marker = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(bug.severity, "⚪")
        print(f"  {severity_marker} {bug.id}: {bug.title}")
        print(f"     Status: {bug.status} | Assigned: {bug.assigned_to} | Cycle: {bug.cycle}/{bug.max_cycles}")


def cmd_bug_close(args):
    """Close a bug."""
    from ..orchestrator import close_bug

    project_path = Path(args.project) if args.project else None
    try:
        bug = close_bug(args.bug_id, args.resolution, project_path)
        print(f"Bug closed: {bug.id} ({bug.resolution})")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_bug_reopen(args):
    """Reopen a bug (verification failed)."""
    from ..orchestrator import reopen_bug

    project_path = Path(args.project) if args.project else None
    try:
        bug = reopen_bug(args.bug_id, project_path)
        if bug.status == "hitl":
            print(f"Bug escalated to HITL: {bug.id}")
            print(f"Max cycles ({bug.max_cycles}) exceeded.")
        else:
            print(f"Bug reopened: {bug.id} (cycle {bug.cycle}/{bug.max_cycles})")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_hitl_status(args):
    """Check HITL status."""
    from ..orchestrator import check_hitl_required

    project_path = Path(args.project) if args.project else None
    required, reason = check_hitl_required(project_path)
    if required:
        print("⚠️  HUMAN INTERVENTION REQUIRED")
        print(f"Reason: {reason}")
        print()
        print("Resolve with: enki hitl resolve --reason 'resolution details'")
    else:
        print("No HITL required.")


def cmd_hitl_resolve(args):
    """Resolve HITL escalation."""
    from ..orchestrator import resolve_hitl, get_next_action

    project_path = Path(args.project) if args.project else None
    try:
        resolve_hitl(args.reason, project_path)
        print(f"HITL resolved: {args.reason}")
        next_action = get_next_action(project_path)
        print(f"Next: {next_action['message']}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_next(args):
    """Show the next recommended action."""
    from ..orchestrator import get_next_action

    project_path = Path(args.project) if args.project else None
    action = get_next_action(project_path)
    if args.json:
        print(json.dumps(action, indent=2))
    else:
        print(f"Action: {action['action']}")
        print(f"Message: {action['message']}")
        if action['action'] == 'run_task':
            print(f"Task: {action['task_id']}")
            print(f"Agent: {action['agent']}")
        elif action['action'] == 'fix_bug':
            print(f"Bug: {action['bug_id']}")


def cmd_spawn(args):
    """Spawn an agent for a task."""
    from ..orchestrator import spawn_agent_for_task

    project_path = Path(args.project) if args.project else None
    try:
        spawn_params = spawn_agent_for_task(args.task_id, project_path)
        if args.json:
            print(json.dumps(spawn_params, indent=2))
        else:
            print(f"Task tool call for: {args.task_id}")
            print()
            print("=== Task Tool Parameters ===")
            print(f"description: \"{spawn_params['description']}\"")
            print(f"subagent_type: \"{spawn_params['subagent_type']}\"")
            print()
            print("prompt:")
            print("-" * 40)
            print(spawn_params['prompt'])
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_spawn_parallel(args):
    """Get spawn calls for all ready tasks."""
    from ..orchestrator import get_parallel_spawn_calls

    project_path = Path(args.project) if args.project else None
    spawn_calls = get_parallel_spawn_calls(project_path)
    if not spawn_calls:
        print("No tasks ready for parallel execution.")
        return
    if args.json:
        print(json.dumps(spawn_calls, indent=2))
    else:
        print(f"Found {len(spawn_calls)} tasks ready for parallel execution:")
        print()
        for call in spawn_calls:
            print(f"=== Task: {call['task_id']} ===")
            print(f"description: \"{call['params']['description']}\"")
            print(f"subagent_type: \"{call['params']['subagent_type']}\"")
            print()


def cmd_spawn_next(args):
    """Spawn the next recommended task."""
    from ..orchestrator import get_next_action, spawn_agent_for_task

    project_path = Path(args.project) if args.project else None
    action = get_next_action(project_path)
    if action['action'] != 'run_task':
        print(f"No task to spawn. Next action: {action['action']}")
        print(f"Message: {action['message']}")
        return

    task_id = action['task_id']
    spawn_params = spawn_agent_for_task(task_id, project_path)
    if args.json:
        print(json.dumps({"task_id": task_id, "params": spawn_params}, indent=2))
    else:
        print(f"Spawning next task: {task_id} ({action['agent']})")
        print()
        print("=== Task Tool Parameters ===")
        print(f"description: \"{spawn_params['description']}\"")
        print(f"subagent_type: \"{spawn_params['subagent_type']}\"")
        print()
        print("prompt:")
        print("-" * 40)
        print(spawn_params['prompt'])


def cmd_agents(args):
    """List available agents."""
    from ..orchestrator import AGENTS

    print("Available Agents:")
    print()
    for name, info in AGENTS.items():
        tier_marker = {"CRITICAL": "★", "STANDARD": "◆", "CONDITIONAL": "○"}.get(info['tier'], " ")
        print(f"  {tier_marker} {name}")
        print(f"    Role: {info['role']}")
        print(f"    Tier: {info['tier']}")
        print(f"    Tools: {', '.join(info['tools'])}")
        if 'skill' in info:
            print(f"    Skill: {info['skill']}")
        print()


def register(subparsers):
    """Register orchestration, task, bug, HITL, spawn, and agent commands."""
    # orchestration status
    p = subparsers.add_parser("orchestration", help="Show orchestration status")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_orchestration_status)

    # orchestrate (start)
    p = subparsers.add_parser("orchestrate", help="Start orchestration")
    p.add_argument("name", help="Spec name to orchestrate")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_orchestrate_start)

    # task subcommands
    task_parser = subparsers.add_parser("task", help="Task management")
    sub = task_parser.add_subparsers(dest="task_command")

    p = sub.add_parser("start", help="Start a task")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_task_start)

    p = sub.add_parser("complete", help="Complete a task")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("-o", "--output", help="Task output")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_task_complete)

    p = sub.add_parser("fail", help="Mark task as failed")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("-r", "--reason", help="Failure reason")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_task_fail)

    # bug subcommands
    bug_parser = subparsers.add_parser("bug", help="Bug management")
    sub = bug_parser.add_subparsers(dest="bug_command")

    p = sub.add_parser("file", help="File a new bug")
    p.add_argument("title", help="Bug title")
    p.add_argument("-d", "--description", help="Bug description")
    p.add_argument("-f", "--found-by", default="QA", help="Agent that found it")
    p.add_argument("-s", "--severity", choices=["critical", "high", "medium", "low"], default="medium")
    p.add_argument("-t", "--task", help="Related task ID")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_bug_file)

    p = sub.add_parser("list", help="List bugs")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_bug_list)

    p = sub.add_parser("close", help="Close a bug")
    p.add_argument("bug_id", help="Bug ID")
    p.add_argument("-r", "--resolution", choices=["fixed", "wontfix"], default="fixed")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_bug_close)

    p = sub.add_parser("reopen", help="Reopen a bug")
    p.add_argument("bug_id", help="Bug ID")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_bug_reopen)

    # HITL subcommands
    hitl_parser = subparsers.add_parser("hitl", help="HITL management")
    sub = hitl_parser.add_subparsers(dest="hitl_command")

    p = sub.add_parser("status", help="Check HITL status")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_hitl_status)

    p = sub.add_parser("resolve", help="Resolve HITL")
    p.add_argument("-r", "--reason", required=True, help="Resolution reason")
    p.add_argument("-p", "--project", help="Project path")
    p.set_defaults(func=cmd_hitl_resolve)

    # next
    p = subparsers.add_parser("next", help="Show next recommended action")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_next)

    # agents
    p = subparsers.add_parser("agents", help="List available agents")
    p.set_defaults(func=cmd_agents)

    # spawn
    p = subparsers.add_parser("spawn", help="Spawn agent for a task")
    p.add_argument("task_id", nargs="?", help="Task ID to spawn")
    p.add_argument("-p", "--project", help="Project path")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--parallel", action="store_true", help="Get all ready tasks for parallel spawn")
    p.add_argument("--next", action="store_true", help="Spawn the next recommended task")
    p.set_defaults(func=lambda args:
        cmd_spawn_parallel(args) if args.parallel else
        cmd_spawn_next(args) if getattr(args, 'next', False) else
        cmd_spawn(args) if args.task_id else
        print("Usage: enki spawn <task_id> or enki spawn --next or enki spawn --parallel"))
