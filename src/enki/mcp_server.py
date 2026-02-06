"""MCP server exposing Enki tools.

P2-02: Refactored from 680-line if/elif chain into dispatch map + per-tool handlers.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .db import init_db, get_db
from .beads import create_bead, get_bead, star_bead, unstar_bead, supersede_bead, BeadType
from .search import search
from .retention import maintain_wisdom

# Import client for remote mode
from .client import (
    is_remote_mode,
    remote_remember,
    remote_recall,
    remote_star,
    remote_supersede,
    remote_status,
    remote_goal,
    remote_phase,
    startup_sync,
    force_sync,
    client_get_sync_status,
)
from .offline import (
    ConnectionState,
    get_connection_state,
    is_offline,
    get_queue_size,
    get_cache_count,
)
from .session import (
    get_session, get_phase, set_phase, get_goal, set_goal,
    start_session,
)
from .pm import (
    generate_perspectives, check_perspectives_complete,
    create_spec, approve_spec, is_spec_approved, list_specs,
    decompose_spec, save_task_graph, get_orchestration_status,
)
from .orchestrator import (
    start_orchestration, load_orchestration,
    start_task, complete_task, fail_task,
    file_bug, close_bug, get_open_bugs,
    get_full_orchestration_status, get_next_action,
    # Validation functions
    submit_for_validation,
    spawn_validators,
    record_validation_result,
    retry_rejected_task,
    get_tasks_needing_validation,
    get_rejected_tasks,
    get_validators_for_task,
)
from .worktree import (
    create_worktree,
    list_worktrees,
    remove_worktree,
    merge_worktree,
    get_worktree_state,
)
from .simplifier import (
    run_simplification,
    get_modified_files,
)
from .path_utils import validate_project_path

logger = logging.getLogger(__name__)

# Initialize server
server = Server("enki")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Enki tools."""
    return [
        Tool(
            name="enki_remember",
            description="Store a new piece of knowledge (decision, solution, learning, violation, or pattern)",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The knowledge to remember",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["decision", "solution", "learning", "violation", "pattern"],
                        "description": "Type of knowledge",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Optional short summary",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project identifier",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context when learned",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for categorization",
                    },
                    "starred": {
                        "type": "boolean",
                        "description": "Star this bead (never decay)",
                        "default": False,
                    },
                },
                "required": ["content", "type"],
            },
        ),
        Tool(
            name="enki_recall",
            description="Search for relevant knowledge",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project filter",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["decision", "solution", "learning", "violation", "pattern"],
                        "description": "Optional type filter",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="enki_forget",
            description="Mark a bead as superseded by another",
            inputSchema={
                "type": "object",
                "properties": {
                    "old_id": {
                        "type": "string",
                        "description": "ID of the bead being superseded",
                    },
                    "new_id": {
                        "type": "string",
                        "description": "ID of the bead that supersedes it",
                    },
                },
                "required": ["old_id", "new_id"],
            },
        ),
        Tool(
            name="enki_star",
            description="Star or unstar a bead (starred beads never decay)",
            inputSchema={
                "type": "object",
                "properties": {
                    "bead_id": {
                        "type": "string",
                        "description": "ID of the bead",
                    },
                    "starred": {
                        "type": "boolean",
                        "description": "True to star, False to unstar",
                        "default": True,
                    },
                },
                "required": ["bead_id"],
            },
        ),
        Tool(
            name="enki_status",
            description="Get memory statistics and current session status",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
            },
        ),
        # Session tools
        Tool(
            name="enki_goal",
            description="Set the session goal (satisfies Gate 1)",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What we're working on this session",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["goal"],
            },
        ),
        Tool(
            name="enki_phase",
            description="Get or set the current session phase",
            inputSchema={
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["intake", "debate", "plan", "implement", "review", "test", "ship"],
                        "description": "Phase to set (omit to just get current)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
            },
        ),
        # PM tools
        Tool(
            name="enki_debate",
            description="Start debate phase - generate multi-perspective analysis",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Feature/change to debate",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["goal"],
            },
        ),
        Tool(
            name="enki_plan",
            description="Create a spec from debate",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Spec name (slug format)",
                    },
                    "problem": {
                        "type": "string",
                        "description": "Problem statement",
                    },
                    "solution": {
                        "type": "string",
                        "description": "Proposed solution",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="enki_approve",
            description="Approve a spec (satisfies Gate 2)",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Spec name to approve",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="enki_decompose",
            description="Break spec into tasks with dependencies",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Spec name to decompose",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["name"],
            },
        ),
        # Orchestrator tools
        Tool(
            name="enki_orchestrate",
            description="Start orchestration from approved spec",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Spec name to orchestrate",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="enki_task",
            description="Manage orchestration tasks",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "complete", "fail"],
                        "description": "Action to perform",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID",
                    },
                    "output": {
                        "type": "string",
                        "description": "Task output (for complete action)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Failure reason (for fail action)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["action", "task_id"],
            },
        ),
        Tool(
            name="enki_bug",
            description="File or manage bugs",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["file", "close", "list"],
                        "description": "Action to perform",
                    },
                    "title": {
                        "type": "string",
                        "description": "Bug title (for file action)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Bug description",
                    },
                    "bug_id": {
                        "type": "string",
                        "description": "Bug ID (for close action)",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Bug severity",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["action"],
            },
        ),
        # Utility tools
        Tool(
            name="enki_log",
            description="Log to RUNNING.md",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to log",
                    },
                    "entry_type": {
                        "type": "string",
                        "enum": ["NOTE", "DECISION", "FILE", "CMD", "WARNING"],
                        "description": "Type of log entry",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="enki_maintain",
            description="Run maintenance (decay weights, archive old beads)",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Validation tools
        Tool(
            name="enki_submit_for_validation",
            description="Submit a task for validation (call when worker completes work)",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID",
                    },
                    "output": {
                        "type": "string",
                        "description": "Summary of work done",
                    },
                },
                "required": ["task_id", "output"],
            },
        ),
        Tool(
            name="enki_spawn_validators",
            description="Spawn validator agents for a task awaiting validation",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to validate",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="enki_record_validation",
            description="Record a validator's verdict (PASS or FAIL)",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The original task ID (not the validation task)",
                    },
                    "validator": {
                        "type": "string",
                        "description": "Which validator (Validator-Tests, Validator-Code)",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["PASS", "FAIL"],
                        "description": "Validation verdict",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "Required if verdict is FAIL - specific issues found",
                    },
                },
                "required": ["task_id", "validator", "verdict"],
            },
        ),
        Tool(
            name="enki_retry_rejected_task",
            description="Get the prompt to retry a rejected task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The rejected task ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="enki_validation_status",
            description="Get status of tasks awaiting validation or rejected",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Worktree tools
        Tool(
            name="enki_worktree_create",
            description="Create isolated git worktree for a task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID for the worktree",
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Branch to create from (default: main)",
                        "default": "main",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="enki_worktree_list",
            description="List all worktrees for the project",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="enki_worktree_merge",
            description="Merge worktree branch back and optionally remove",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID",
                    },
                    "target_branch": {
                        "type": "string",
                        "description": "Branch to merge into (default: main)",
                        "default": "main",
                    },
                    "delete_after": {
                        "type": "boolean",
                        "description": "Remove worktree after merge",
                        "default": True,
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="enki_worktree_remove",
            description="Remove a worktree",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force removal even with uncommitted changes",
                        "default": False,
                    },
                },
                "required": ["task_id"],
            },
        ),
        # Reflector tool
        Tool(
            name="enki_reflect",
            description="Close the feedback loop — reflect on session and store learnings",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Project directory path (default: current)",
                    },
                },
            },
        ),
        # Feedback loop tool
        Tool(
            name="enki_feedback_loop",
            description="Manage enforcement feedback proposals (run, status, apply, reject, revert, acknowledge)",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["run", "status", "apply", "reject", "revert", "acknowledge"],
                        "description": "Action to perform",
                    },
                    "proposal_id": {
                        "type": "string",
                        "description": "Proposal ID (required for apply/reject/revert/acknowledge)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project path",
                    },
                },
                "required": ["action"],
            },
        ),
        # Simplifier tool
        Tool(
            name="enki_simplify",
            description="Run code simplification on files to reduce AI-generated bloat",
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific files to simplify (omit for all modified)",
                    },
                    "all_modified": {
                        "type": "boolean",
                        "description": "Simplify all modified files from git",
                        "default": False,
                    },
                },
            },
        ),
    ]


# =============================================================================
# P2-02: Per-tool handler functions
# =============================================================================
# Each handler: def _handle_X(args, remote) -> list[TextContent]

def _get_project_path(arguments: dict) -> Optional[Path]:
    """Extract project_path from arguments."""
    raw = arguments.get("project") or arguments.get("project_path")
    return Path(raw) if raw else None


def _status_label(result: dict) -> str:
    """Generate status label from store result."""
    if result.get("offline"):
        return " (queued - offline)"
    if result.get("fallback"):
        return " (local fallback)"
    return ""


def _handle_remember(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    result = store.remember(
        content=arguments["content"], bead_type=arguments["type"],
        summary=arguments.get("summary"), project=arguments.get("project"),
        context=arguments.get("context"), tags=arguments.get("tags"),
        starred=arguments.get("starred", False),
    )
    label = _status_label(result)
    content = arguments["content"]
    preview = f"{content[:200]}{'...' if len(content) > 200 else ''}"
    return [TextContent(type="text", text=f"Remembered [{result['type']}] {result['id']}{label}\n\n{preview}")]


def _handle_recall(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    results = store.recall(
        query=arguments["query"], project=arguments.get("project"),
        bead_type=arguments.get("type"), limit=arguments.get("limit", 10),
    )
    if not results:
        return [TextContent(type="text", text="No relevant knowledge found.")]

    is_cached = any(r.get("cached") for r in results)
    source_note = " (from local cache - offline)" if is_cached else ""
    lines = [f"Found {len(results)} results{source_note}:\n"]
    for i, r in enumerate(results, 1):
        starred_marker = "*" if r.get("starred") else ""
        cached_marker = " [cached]" if r.get("cached") else ""
        content = r.get("content", "")
        summary = r.get("summary") or content[:150]
        lines.append(
            f"{i}. [{r['type']}]{starred_marker}{cached_marker} (score: {r.get('score', 0):.2f}, via: {r.get('sources', '')})\n"
            f"   {summary}{'...' if len(content) > 150 else ''}\n"
            f"   ID: {r['id']}\n"
        )
    return [TextContent(type="text", text="\n".join(lines))]


def _handle_forget(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    result = store.supersede(arguments["old_id"], arguments["new_id"])
    if not result["found"]:
        return [TextContent(type="text", text=f"Bead {arguments['old_id']} not found")]
    label = _status_label(result)
    return [TextContent(type="text", text=f"Marked {arguments['old_id']} as superseded by {arguments['new_id']}{label}")]


def _handle_star(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    starred = arguments.get("starred", True)
    result = store.star(arguments["bead_id"], starred)
    action = "Starred" if starred else "Unstarred"
    if not result["found"]:
        return [TextContent(type="text", text=f"Bead {arguments['bead_id']} not found")]
    label = _status_label(result)
    return [TextContent(type="text", text=f"{action} bead {arguments['bead_id']}{label}")]


def _handle_status(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    status = store.get_status(arguments.get("project"))
    offline = status.get("offline", False)
    fallback = status.get("fallback", False)
    header = "Enki Status"
    if offline:
        header += " [OFFLINE]"
    elif fallback:
        header += " (local fallback)"
    lines = [
        header, "=" * 40,
        f"Phase: {status.get('phase', 'intake')}",
        f"Goal: {status.get('goal') or '(not set)'}",
        "", "Memory:",
        f"  Total beads: {status.get('total_beads', 0)}",
        f"  Active beads: {status.get('active_beads', 0)}",
        f"  Starred beads: {status.get('starred_beads', 0)}",
    ]
    if offline:
        lines.extend(["", "Offline Mode:",
            f"  Cached beads: {status.get('cached_beads', 0)}",
            f"  Pending sync: {status.get('pending_sync', 0)} operations",
        ])
    orch = status.get("orchestration")
    if orch and orch.get("active"):
        lines.extend(["", "Orchestration:",
            f"  Spec: {orch['spec']}",
            f"  Progress: {orch['tasks']['completed']}/{orch['tasks']['total']}",
        ])
    return [TextContent(type="text", text="\n".join(lines))]


def _handle_goal(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    result = store.set_goal(arguments["goal"], arguments.get("project"))
    label = _status_label(result)
    return [TextContent(type="text", text=f"Goal set: {arguments['goal']}{label}\n\nGate 1 (Goal Required) is now satisfied.")]


def _handle_phase(arguments: dict, remote: bool) -> list[TextContent]:
    from .store import get_store
    store = get_store(remote)
    phase_arg = arguments.get("phase")
    result = store.get_or_set_phase(phase_arg, arguments.get("project"))
    label = _status_label(result)
    if phase_arg:
        return [TextContent(type="text", text=f"Phase set to: {phase_arg}{label}")]
    return [TextContent(type="text", text=f"Current phase: {result['phase']}{label}")]


def _handle_debate(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    perspectives_path = generate_perspectives(
        goal=arguments["goal"], context=arguments.get("context"), project_path=project_path,
    )
    return [TextContent(type="text",
        text=f"Debate started for: {arguments['goal']}\n\n"
             f"Perspectives template created: {perspectives_path}\n\n"
             f"Fill in ALL perspectives before running enki_plan:\n"
             f"  - PM Perspective\n  - CTO Perspective\n  - Architect Perspective\n"
             f"  - DBA Perspective\n  - Security Perspective\n  - Devil's Advocate",
    )]


def _handle_plan(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    try:
        spec_path = create_spec(
            name=arguments["name"], problem=arguments.get("problem"),
            solution=arguments.get("solution"), project_path=project_path,
        )
        return [TextContent(type="text", text=f"Spec created: {spec_path}\n\nEdit the spec, then use enki_approve to approve it.")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_approve(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    try:
        approve_spec(arguments["name"], project_path)
        return [TextContent(type="text",
            text=f"Spec approved: {arguments['name']}\n\nGate 2 (Spec Approval) is now satisfied.\nYou can now spawn implementation agents.",
        )]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_decompose(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    try:
        if not is_spec_approved(arguments["name"], project_path):
            return [TextContent(type="text", text=f"Spec not approved: {arguments['name']}")]
        graph = decompose_spec(arguments["name"], project_path)
        save_task_graph(graph, project_path)
        waves = graph.get_waves()
        lines = [f"Task graph created for: {arguments['name']}\n"]
        for i, wave in enumerate(waves, 1):
            lines.append(f"Wave {i}:")
            for task in wave:
                lines.append(f"  - {task.id}: {task.description} ({task.agent})")
        return [TextContent(type="text", text="\n".join(lines))]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_orchestrate(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    try:
        graph = decompose_spec(arguments["name"], project_path)
        orch = start_orchestration(arguments["name"], graph, project_path)
        next_action = get_next_action(project_path)
        return [TextContent(type="text",
            text=f"Orchestration started: {orch.id}\nSpec: {arguments['name']}\n"
                 f"Tasks: {len(graph.tasks)}\n\nNext: {next_action['message']}",
        )]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_task(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    action = arguments["action"]
    task_id = arguments["task_id"]
    try:
        if action == "start":
            task = start_task(task_id, project_path)
            return [TextContent(type="text", text=f"Task started: {task.id}\nAgent: {task.agent}\nDescription: {task.description}")]
        elif action == "complete":
            task = complete_task(task_id, arguments.get("output"), project_path)
            next_action = get_next_action(project_path)
            return [TextContent(type="text", text=f"Task completed: {task.id}\n\nNext: {next_action['message']}")]
        elif action == "fail":
            task = fail_task(task_id, arguments.get("reason"), project_path)
            status = "failed (HITL required)" if task.status == "failed" else f"will retry (attempt {task.attempts}/{task.max_attempts})"
            return [TextContent(type="text", text=f"Task {status}: {task.id}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    return [TextContent(type="text", text=f"Unknown task action: {action}")]


def _handle_bug(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = _get_project_path(arguments)
    action = arguments["action"]
    try:
        if action == "file":
            bug = file_bug(
                title=arguments.get("title", "Bug"),
                description=arguments.get("description", ""),
                found_by=arguments.get("found_by", "enki"),
                severity=arguments.get("severity", "medium"),
                project_path=project_path,
            )
            return [TextContent(type="text", text=f"Bug filed: {bug.id}\nTitle: {bug.title}\nSeverity: {bug.severity}")]
        elif action == "close":
            bug = close_bug(arguments["bug_id"], "fixed", project_path)
            return [TextContent(type="text", text=f"Bug closed: {bug.id}")]
        elif action == "list":
            bugs = get_open_bugs(project_path)
            if not bugs:
                return [TextContent(type="text", text="No open bugs.")]
            lines = ["Open Bugs:"]
            for bug in bugs:
                lines.append(f"  {bug.id}: {bug.title} ({bug.severity})")
            return [TextContent(type="text", text="\n".join(lines))]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    return [TextContent(type="text", text=f"Unknown bug action: {action}")]


def _handle_log(arguments: dict, remote: bool) -> list[TextContent]:
    project_path = Path(arguments.get("project", "."))
    enki_dir = project_path / ".enki"
    enki_dir.mkdir(exist_ok=True)
    running_md = enki_dir / "RUNNING.md"
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"[{timestamp}] {arguments.get('entry_type', 'NOTE')}: {arguments['message']}\n"
    with open(running_md, "a") as f:
        f.write(entry)
    return [TextContent(type="text", text=f"Logged: {entry.strip()}")]


def _handle_maintain(arguments: dict, remote: bool) -> list[TextContent]:
    results = maintain_wisdom()
    return [TextContent(type="text",
        text=f"Maintenance complete:\n  Weights updated: {results['weights_updated']}\n"
             f"  Beads archived: {results['archived']}\n  Superseded purged: {results['purged']}",
    )]


def _handle_submit_for_validation(arguments: dict, remote: bool) -> list[TextContent]:
    try:
        task = submit_for_validation(arguments["task_id"], arguments["output"])
        validators = get_validators_for_task(task)
        return [TextContent(type="text", text=f"Task {arguments['task_id']} submitted for validation.\nValidators: {validators}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_spawn_validators(arguments: dict, remote: bool) -> list[TextContent]:
    try:
        spawn_calls = spawn_validators(arguments["task_id"])
        if not spawn_calls:
            return [TextContent(type="text", text=f"No validators configured for task {arguments['task_id']}")]
        return [TextContent(type="text", text=json.dumps(spawn_calls, indent=2))]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_record_validation(arguments: dict, remote: bool) -> list[TextContent]:
    passed = arguments["verdict"].upper() == "PASS"
    feedback = arguments.get("feedback")
    if not passed and not feedback:
        return [TextContent(type="text", text="Error: feedback is required when verdict is FAIL")]
    try:
        task = record_validation_result(
            task_id=arguments["task_id"], validator=arguments["validator"],
            passed=passed, feedback=feedback,
        )
        if task.status == "complete":
            return [TextContent(type="text", text=f"Task {arguments['task_id']} validated and completed")]
        elif task.status == "rejected":
            return [TextContent(type="text",
                text=f"Task {arguments['task_id']} rejected (rejection {task.rejection_count}/{task.max_rejections})\n"
                     f"Worker must fix issues and resubmit.\nUse enki_retry_rejected_task to get the retry prompt.",
            )]
        elif task.status == "failed":
            return [TextContent(type="text",
                text=f"Task {arguments['task_id']} failed - HITL required\nMax rejections exceeded. Human intervention needed.",
            )]
        return [TextContent(type="text", text=f"Task {arguments['task_id']} status: {task.status}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_retry_rejected_task(arguments: dict, remote: bool) -> list[TextContent]:
    try:
        params = retry_rejected_task(arguments["task_id"])
        return [TextContent(type="text", text=json.dumps(params, indent=2))]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_validation_status(arguments: dict, remote: bool) -> list[TextContent]:
    validating = get_tasks_needing_validation()
    rejected = get_rejected_tasks()
    lines = []
    if validating:
        lines.append("**Tasks Awaiting Validation:**")
        for task in validating:
            lines.append(f"- {task.id} ({task.agent})")
    else:
        lines.append("No tasks awaiting validation.")
    lines.append("")
    if rejected:
        lines.append("**Rejected Tasks (need retry):**")
        for task in rejected:
            lines.append(f"- {task.id}: {task.rejection_count}/{task.max_rejections} rejections")
    else:
        lines.append("No rejected tasks.")
    return [TextContent(type="text", text="\n".join(lines))]


def _handle_worktree_create(arguments: dict, remote: bool) -> list[TextContent]:
    try:
        path = create_worktree(task_id=arguments["task_id"], base_branch=arguments.get("base_branch", "main"))
        return [TextContent(type="text", text=f"Created worktree at {path}\nBranch: enki/{arguments['task_id']}")]
    except (ValueError, Exception) as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _handle_worktree_list(arguments: dict, remote: bool) -> list[TextContent]:
    trees = list_worktrees()
    if not trees:
        return [TextContent(type="text", text="No worktrees found.")]
    lines = ["Worktrees:"]
    for t in trees:
        marker = " (main)" if not t.task_id else ""
        lines.append(f"- {t.path} [{t.branch}]{marker}")
    return [TextContent(type="text", text="\n".join(lines))]


def _handle_worktree_merge(arguments: dict, remote: bool) -> list[TextContent]:
    success = merge_worktree(
        task_id=arguments["task_id"],
        target_branch=arguments.get("target_branch", "main"),
        delete_after=arguments.get("delete_after", True),
    )
    if success:
        msg = f"Merged {arguments['task_id']} into {arguments.get('target_branch', 'main')}"
        if arguments.get("delete_after", True):
            msg += "\nWorktree removed."
        return [TextContent(type="text", text=msg)]
    return [TextContent(type="text", text=f"Error: Failed to merge {arguments['task_id']}")]


def _handle_worktree_remove(arguments: dict, remote: bool) -> list[TextContent]:
    success = remove_worktree(task_id=arguments["task_id"], force=arguments.get("force", False))
    if success:
        return [TextContent(type="text", text=f"Removed worktree: {arguments['task_id']}")]
    return [TextContent(type="text", text=f"Error: Failed to remove worktree {arguments['task_id']}")]


def _handle_reflect(arguments: dict, remote: bool) -> list[TextContent]:
    from .reflector import close_feedback_loop
    project_path = Path(arguments.get("project_path", "."))
    try:
        report = close_feedback_loop(project_path)
        lines = [f"## Session Reflection: {report['session_id']}"]
        for r in report["reflections"]:
            icon = {"worked": "✓", "failed": "✗", "pattern": "⟳", "warning": "⚠"}.get(r["category"], "?")
            lines.append(f"- {icon} {r['description']}")
        lines.append(f"\n**Skills stored:** {report['skills_stored']} new, {report['skills_duplicate']} duplicates")
        return [TextContent(type="text", text="\n".join(lines))]
    except Exception as e:
        return [TextContent(type="text", text=f"Reflection error: {e}")]


def _handle_feedback_loop(arguments: dict, remote: bool) -> list[TextContent]:
    from .feedback_loop import (
        run_feedback_cycle, get_feedback_summary,
        apply_proposal, reject_proposal,
        revert_proposal, acknowledge_regression,
    )
    action = arguments["action"]
    proposal_id = arguments.get("proposal_id")
    project_path = _get_project_path(arguments)
    try:
        if action == "run":
            report = run_feedback_cycle(project_path)
            if report["proposals_stored"]:
                return [TextContent(type="text",
                    text=f"Feedback cycle complete.\nFP patterns analyzed: {report['fp_patterns_analyzed']}\n"
                         f"Evasion patterns analyzed: {report['evasion_patterns_analyzed']}\n"
                         f"Proposals generated: {report['proposals_generated']}\n"
                         f"Proposal IDs: {', '.join(report['proposals_stored'])}\n\nReview with `enki_feedback_loop status`.",
                )]
            return [TextContent(type="text",
                text=f"Feedback cycle complete. System is stable.\nFP patterns analyzed: {report['fp_patterns_analyzed']}\n"
                     f"Evasion patterns analyzed: {report['evasion_patterns_analyzed']}\nNo proposals needed.",
            )]
        elif action == "status":
            return [TextContent(type="text", text=get_feedback_summary())]
        elif action == "apply":
            if not proposal_id:
                return [TextContent(type="text", text="Error: proposal_id required for apply")]
            result = apply_proposal(proposal_id)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text=f"Proposal applied: {result['proposal_id']}\n{result['change_summary']}")]
        elif action == "reject":
            if not proposal_id:
                return [TextContent(type="text", text="Error: proposal_id required for reject")]
            result = reject_proposal(proposal_id)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text=f"Proposal rejected: {result['proposal_id']}")]
        elif action == "revert":
            if not proposal_id:
                return [TextContent(type="text", text="Error: proposal_id required for revert")]
            result = revert_proposal(proposal_id)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text=f"Proposal reverted: {result['proposal_id']}\nPrevious status: {result['previous_status']}")]
        elif action == "acknowledge":
            if not proposal_id:
                return [TextContent(type="text", text="Error: proposal_id required for acknowledge")]
            result = acknowledge_regression(proposal_id)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [TextContent(type="text", text=f"Regression acknowledged: {result['proposal_id']}")]
        return [TextContent(type="text", text=f"Unknown action: {action}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Feedback loop error: {e}")]


def _handle_simplify(arguments: dict, remote: bool) -> list[TextContent]:
    params = run_simplification(files=arguments.get("files"), all_modified=arguments.get("all_modified", False))
    return [TextContent(type="text",
        text=f"Simplifier Agent Parameters:\n\nDescription: {params['description']}\n"
             f"Files: {', '.join(params.get('files', [])) or '(will detect modified files)'}\n\n"
             f"To spawn the Simplifier agent, use the Task tool with these parameters:\n\n"
             f"```json\n{json.dumps({'description': params['description'], 'prompt': params['prompt'][:500] + '...', 'subagent_type': params['subagent_type']}, indent=2)}\n```",
    )]


# =============================================================================
# P2-02: Dispatch map — tool name → handler
# =============================================================================

TOOL_HANDLERS: dict[str, object] = {
    "enki_remember": _handle_remember,
    "enki_recall": _handle_recall,
    "enki_forget": _handle_forget,
    "enki_star": _handle_star,
    "enki_status": _handle_status,
    "enki_goal": _handle_goal,
    "enki_phase": _handle_phase,
    "enki_debate": _handle_debate,
    "enki_plan": _handle_plan,
    "enki_approve": _handle_approve,
    "enki_decompose": _handle_decompose,
    "enki_orchestrate": _handle_orchestrate,
    "enki_task": _handle_task,
    "enki_bug": _handle_bug,
    "enki_log": _handle_log,
    "enki_maintain": _handle_maintain,
    "enki_submit_for_validation": _handle_submit_for_validation,
    "enki_spawn_validators": _handle_spawn_validators,
    "enki_record_validation": _handle_record_validation,
    "enki_retry_rejected_task": _handle_retry_rejected_task,
    "enki_validation_status": _handle_validation_status,
    "enki_worktree_create": _handle_worktree_create,
    "enki_worktree_list": _handle_worktree_list,
    "enki_worktree_merge": _handle_worktree_merge,
    "enki_worktree_remove": _handle_worktree_remove,
    "enki_reflect": _handle_reflect,
    "enki_feedback_loop": _handle_feedback_loop,
    "enki_simplify": _handle_simplify,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls via dispatch map (P2-02)."""

    # P1-02: Validate project_path early — reject path traversal
    raw_project = arguments.get("project") or arguments.get("project_path")
    if raw_project:
        try:
            validated = validate_project_path(raw_project)
            if validated:
                arguments["project"] = str(validated)
                if "project_path" in arguments:
                    arguments["project_path"] = str(validated)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    # Always initialize local database — it's the fallback when remote is unreachable
    init_db()
    remote = is_remote_mode()

    handler = TOOL_HANDLERS.get(name)
    if handler:
        return handler(arguments, remote)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
