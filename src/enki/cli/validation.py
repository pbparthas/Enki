"""Validation commands: validate, spawn-validators, retry, validation-status."""

import json
import sys
from pathlib import Path


def cmd_validate(args):
    """Submit validation result for a task."""
    from ..orchestrator import record_validation_result

    project_path = Path(args.project) if args.project else None
    validator = args.validator or "Validator-Code"
    verdict_str = args.verdict.upper()
    passed = verdict_str == "PASS"
    feedback = args.feedback if not passed else None

    task = record_validation_result(
        task_id=args.task_id, validator=validator,
        passed=passed, feedback=feedback, project_path=project_path,
    )

    if passed:
        print(f"Validation PASSED: {args.task_id}")
        print(f"  Status: {task.status}")
    else:
        print(f"Validation FAILED: {args.task_id}")
        print(f"  Status: {task.status}")
        print(f"  Rejections: {task.rejection_count}/{task.max_rejections}")
        if task.status == "failed":
            print(f"  HITL required - max rejections exceeded")


def cmd_spawn_validators(args):
    """Spawn validators for a task."""
    from ..orchestrator import spawn_validators

    project_path = Path(args.project) if args.project else None
    spawn_calls = spawn_validators(args.task_id, project_path)
    if not spawn_calls:
        print(f"No validators configured for task {args.task_id}")
        return
    if args.json:
        print(json.dumps(spawn_calls, indent=2))
    else:
        print(f"Validators to spawn for {args.task_id}:")
        for call in spawn_calls:
            print(f"  - {call['validator']}: {call['params']['description']}")
        print()
        print("Use --json to get full spawn parameters")


def cmd_retry(args):
    """Get prompt to retry a rejected task."""
    from ..orchestrator import retry_rejected_task

    project_path = Path(args.project) if args.project else None
    try:
        params = retry_rejected_task(args.task_id, project_path)
    except ValueError as e:
        print(f"Error: {e}")
        return

    if args.json:
        print(json.dumps(params, indent=2))
    else:
        print(f"Retry parameters for {args.task_id}:")
        print(f"  Description: {params['description']}")
        print()
        print("Prompt:")
        print("-" * 40)
        print(params['prompt'])


def cmd_validation_status(args):
    """Show tasks awaiting validation or rejected."""
    from ..orchestrator import get_tasks_needing_validation, get_rejected_tasks

    project_path = Path(args.project) if args.project else None
    validating = get_tasks_needing_validation(project_path)
    rejected = get_rejected_tasks(project_path)

    if validating:
        print("Tasks awaiting validation:")
        for task in validating:
            print(f"  - {task.id} ({task.agent}): {task.description[:40]}...")
    else:
        print("No tasks awaiting validation.")

    print()

    if rejected:
        print("Rejected tasks (need retry):")
        for task in rejected:
            print(f"  - {task.id} ({task.agent}): rejection {task.rejection_count}/{task.max_rejections}")
            if task.validator_feedback:
                feedback_preview = task.validator_feedback[:60].replace('\n', ' ')
                print(f"    Feedback: {feedback_preview}...")
    else:
        print("No rejected tasks.")


def register(subparsers):
    """Register validation commands."""
    p = subparsers.add_parser("validate", help="Submit validation result")
    p.add_argument("task_id", help="Task ID being validated")
    p.add_argument("verdict", choices=["pass", "fail", "PASS", "FAIL"], help="Validation verdict")
    p.add_argument("--feedback", "-f", help="Feedback if failing (required for fail)")
    p.add_argument("--validator", "-v", default="Validator-Code", help="Validator name")
    p.add_argument("--project", "-p", help="Project path")
    p.set_defaults(func=cmd_validate)

    p = subparsers.add_parser("spawn-validators", help="Spawn validators for a task")
    p.add_argument("task_id", help="Task ID to validate")
    p.add_argument("--project", "-p", help="Project path")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_spawn_validators)

    p = subparsers.add_parser("retry", help="Retry a rejected task")
    p.add_argument("task_id", help="Rejected task ID")
    p.add_argument("--project", "-p", help="Project path")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_retry)

    p = subparsers.add_parser("validation-status", help="Show validation status")
    p.add_argument("--project", "-p", help="Project path")
    p.set_defaults(func=cmd_validation_status)
