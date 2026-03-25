"""MCP server exposing Enki tools.

v4: Updated memory tools for note model. enki_restore added.
v3: Rewired to use Abzu (memory), Uru (gates), and EM (orchestration).
"""

import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .db import init_all

logger = logging.getLogger(__name__)

server = Server("enki")


# =============================================================================
# Tool definitions
# =============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Enki tools."""
    return [
        # ── Memory (Abzu) ──
        Tool(
            name="enki_remember",
            description=(
                "Store a note in memory. When to call: after decisions, fixes, and notable findings. "
                "Categories: decision, solution, learning, violation, pattern, challenge. "
                "Use 'challenge' for Igi findings. Use 'decision' for architectural choices. "
                "Preference notes bypass staging and go directly to permanent memory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The knowledge to remember"},
                    "category": {
                        "type": "string",
                        "enum": ["decision", "learning", "pattern", "fix", "preference", "code_knowledge", "challenge"],
                        "description": "Category of knowledge",
                    },
                    "project": {"type": "string", "description": "Optional project ID"},
                    "summary": {"type": "string", "description": "Optional short summary"},
                    "tags": {"type": "string", "description": "Optional comma-separated tags"},
                },
                "required": ["content", "category"],
            },
        ),
        Tool(
            name="enki_recall",
            description=(
                "Search memory for relevant notes. When to call: at the START of every session "
                "before doing any work, and before architectural decisions to check prior solutions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "scope": {
                        "type": "string",
                        "enum": ["project", "global"],
                        "description": "Search scope (default: project)",
                        "default": "project",
                    },
                    "project": {"type": "string", "description": "Optional project filter"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="enki_star",
            description="Star a note — starred notes never decay",
            inputSchema={
                "type": "object",
                "properties": {
                    "bead_id": {"type": "string", "description": "Note or bead ID to star"},
                },
                "required": ["bead_id"],
            },
        ),
        Tool(
            name="enki_status",
            description="Get memory system health: note counts, staging depth, decay stats",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Optional project ID"},
                },
            },
        ),
        Tool(
            name="enki_restore",
            description="Recover session context after compaction. Returns persona + enforcement state + recent knowledge.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Optional project ID"},
                },
            },
        ),

        # ── Gates (Uru) ──
        # ── Orchestration (EM) ──
        Tool(
            name="enki_goal",
            description=(
                "Initialise or update a project. Call this at the start of every new project "
                "or when no goal is set. Creates full project infrastructure if missing "
                "(directory, em.db, all tables). Parameters: project (name string, not path), "
                "goal (what to build), tier (minimal/standard/full)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What we're building"},
                    "goal": {"type": "string", "description": "Alias for description"},
                    "project": {"type": "string", "description": "Optional project ID", "default": "default"},
                    "tier": {"type": "string", "enum": ["minimal", "standard", "full"]},
                    "spec_path": {"type": "string", "description": "Optional authored spec path"},
                },
                "required": [],
            },
        ),
        Tool(
            name="enki_phase",
            description=(
                "Check current phase status. When to call: for status checks only. "
                "Never call action='advance' directly; call enki_approve for HITL phase transitions. "
                "Parameters: action ('status'), project (name)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["advance", "status"],
                        "description": "Advance phase or return status",
                    },
                    "to": {
                        "type": "string",
                        "enum": ["planning", "spec", "approved", "implement", "validating", "complete"],
                        "description": "Target phase for advance",
                    },
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="enki_approve",
            description=(
                "Record HITL approval and advance phase. When to call: after EVERY operator approval; "
                "call this immediately because the gate will not advance without it. "
                "Stages: 'igi' after Igi challenge review, "
                "'spec' after product spec review, 'architect' after implementation spec review, "
                "'test' after test results review. Never skip this call even if approval is verbal."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                    "stage": {
                        "type": "string",
                        "enum": ["igi", "spec", "architect", "test", "spec-revision"],
                    },
                    "note": {"type": "string"},
                },
                "required": ["stage"],
            },
        ),
        Tool(
            name="enki_spawn",
            description="Spawn a single agent mechanically; persist full output to artifacts and return summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task_id": {"type": "string"},
                    "context": {"type": "object"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["role", "task_id"],
            },
        ),
        Tool(
            name="enki_report",
            description="Record agent completion after Task tool execution. Call after running an agent spawned by enki_spawn.",
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "task_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "status": {"type": "string", "enum": ["completed", "failed"], "default": "completed"},
                    "output": {"type": "object", "description": "Optional structured agent output incl. concerns"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["role", "task_id", "summary"],
            },
        ),
        Tool(
            name="enki_wave",
            description="Run next ready wave; always spawns both Dev and QA for each task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
                "required": [],
            },
        ),
        Tool(
            name="enki_decompose",
            description=(
                "Break an approved spec into a task DAG for sprint execution. "
                "Call after architect spec is HITL approved, before enki_wave. "
                "tasks: list of {name, files, dependencies} dicts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of {name, files, dependencies} task dicts",
                    },
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="enki_debate",
            description=(
                "Run multi-round spec debate before HITL approval. "
                "Call after PM writes docs/spec-draft.md and before enki_approve(stage='spec'). "
                "Runs 2 rounds: opening positions then rebuttals. "
                "PM reconciles into docs/spec-final.md + docs/debate-summary.md. "
                "Resumable - safe to call multiple times. Automatically detects brownfield "
                "and includes historical_context agent if Researcher Codebase Profile exists."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_debate_update",
            description=(
                "Record a debate agent's output progressively. "
                "Call after each debate agent Task completes. "
                "round: '1' for opening positions, '2' for rebuttals, 'reconciliation' for PM."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "round": {"type": "string", "enum": ["1", "2", "reconciliation"]},
                    "output": {"type": "object"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["role", "round", "output"],
            },
        ),
        Tool(
            name="enki_kickoff",
            description=(
                "Run pre-implementation kickoff. Call after enki_approve(stage='igi'). "
                "PM presents spec, Architect reviews technical feasibility, DBA/UI join conditionally. "
                "Handles resume on session restart — safe to call multiple times. "
                "Skips automatically for brownfield projects without a spec or projects already in implement phase."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_kickoff_update",
            description=(
                "Record a kickoff agent's output progressively. "
                "Call after each kickoff agent completes via Task tool. "
                "For PM output: automatically triggers DBA/UI spawning if needed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "Agent role that just completed"},
                    "output": {"type": "object", "description": "Agent's structured output"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["role", "output"],
            },
        ),
        Tool(
            name="enki_kickoff_complete",
            description=(
                "Evaluate all kickoff agent outputs, collect blockers, write final summary. "
                "Call after all kickoff agents have completed and been recorded via enki_kickoff_update."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_escalate",
            description=(
                "Escalate a blocked task to human (HITL). Call immediately when a task "
                "cannot proceed without human input. Never improvise around blockers - escalate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "reason": {"type": "string", "description": "Why this needs human attention"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["task_id", "reason"],
            },
        ),
        Tool(
            name="enki_mark_blocked",
            description="Mark a task as blocked with a reason. Use when a task cannot proceed due to unresolved dependency or missing input.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["task_id", "reason"],
            },
        ),
        Tool(
            name="enki_sprint_summary",
            description=(
                "Get full sprint summary including wave status, task counts, and completion state. "
                "Call at session start to orient on current sprint progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sprint_id": {"type": "string", "description": "Sprint ID e.g. sprint-1"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["sprint_id"],
            },
        ),
        Tool(
            name="enki_sprint_close",
            description=(
                "Run sprint close pipeline (test consolidation, full test run, InfoSec, sprint Reviewer) "
                "before advancing implement -> validating."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_diagram",
            description=(
                "Generate Mermaid diagrams from project state: dag, files, pipeline, or codebase."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["dag", "files", "pipeline", "codebase"],
                        "default": "dag",
                    },
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_status_update",
            description="Generate a human-readable project status update covering goal, phase, sprint, and wave progress.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_mail_inbox",
            description=(
                "Read unread messages in EM inbox. Call after each wave completes to read "
                "agent messages, concerns, and handoffs before starting next wave."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": "EM", "description": "Inbox owner"},
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_mail_thread",
            description="Read full message thread history by thread ID. Use to get complete context on an agent conversation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "project": {"type": "string", "default": "default"},
                },
                "required": ["thread_id"],
            },
        ),
        Tool(
            name="enki_next_actions",
            description=(
                "Get list of tasks ready to spawn in current wave without triggering execution. "
                "Use to inspect what's ready before calling enki_wave."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="enki_complete",
            description="Finalize a task only when validator/QA/wave preconditions are satisfied.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "project": {"type": "string", "default": "."},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="enki_wrap",
            description="Run session-end memory curation pipeline and return aggregate counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="enki_bug",
            description=(
                "File a bug. Returns human-readable ID in format PREFIX-### (for example TF-001). "
                "Severity: critical, high, medium, low. Call immediately when a bug is found; "
                "do not defer. The returned bug_id is referenceable in conversation and commits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["file", "close", "list"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                    "bug_id": {"type": "string"},
                    "project": {"type": "string", "default": "."},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="enki_register",
            description=(
                "Register a project path mapping in wisdom.db. "
                "Use this to repair/refresh CWD-based project resolution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "path": {"type": "string", "description": "Optional source path (defaults to CWD)"},
                },
            },
        ),
    ]


# =============================================================================
# Tool handlers
# =============================================================================


def _handle_remember(args: dict) -> str:
    from .mcp.memory_tools import enki_remember
    result = enki_remember(
        content=args["content"],
        category=args["category"],
        project=args.get("project"),
        summary=args.get("summary"),
        tags=args.get("tags"),
    )
    return json.dumps(result, indent=2)


def _handle_recall(args: dict) -> str:
    from .mcp.memory_tools import enki_recall
    results = enki_recall(
        query=args["query"],
        scope=args.get("scope", "project"),
        project=args.get("project"),
        limit=args.get("limit", 5),
    )
    if not results:
        return "No relevant knowledge found."
    lines = [f"Found {len(results)} results:\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r.get('category', '?')}] {r.get('summary') or r.get('content', '')[:150]}")
        note_id = r.get('note_id') or r.get('id', '?')
        lines.append(f"   ID: {note_id}\n")
    return "\n".join(lines)


def _handle_star(args: dict) -> str:
    from .mcp.memory_tools import enki_star
    result = enki_star(args["bead_id"])
    return json.dumps(result)


def _handle_status(args: dict) -> str:
    from .mcp.memory_tools import enki_status
    result = enki_status(project=args.get("project"))
    return json.dumps(result, indent=2)


def _handle_restore(args: dict) -> str:
    from .mcp.memory_tools import enki_restore
    result = enki_restore(project=args.get("project"))
    return json.dumps(result, indent=2)


def _handle_goal(args: dict) -> str:
    from .mcp.orch_tools import enki_goal
    description = args.get("description") or args.get("goal")
    result = enki_goal(
        description,
        args.get("project", "default"),
        args.get("spec_path"),
        args.get("goal"),
        args.get("tier"),
    )
    return json.dumps(result, indent=2)


def _handle_phase(args: dict) -> str:
    from .mcp.orch_tools import enki_phase
    result = enki_phase(
        args["action"],
        args.get("to"),
        args.get("project"),
    )
    return json.dumps(result, indent=2)


def _handle_approve(args: dict) -> str:
    from .mcp.orch_tools import enki_approve
    result = enki_approve(
        project=args.get("project"),
        stage=args["stage"],
        note=args.get("note"),
    )
    return json.dumps(result, indent=2)


def _handle_spawn(args: dict) -> str:
    from .mcp.orch_tools import enki_spawn
    result = enki_spawn(
        role=args["role"],
        task_id=args["task_id"],
        context=args.get("context"),
        project=args.get("project"),
    )
    return json.dumps(result, indent=2)


def _handle_report(args: dict) -> str:
    from .mcp.orch_tools import enki_report
    result = enki_report(
        role=args["role"],
        task_id=args["task_id"],
        summary=args["summary"],
        status=args.get("status", "completed"),
        output=args.get("output"),
        project=args.get("project"),
    )
    return json.dumps(result, indent=2)


def _handle_wave(args: dict) -> str:
    from .mcp.orch_tools import enki_wave
    result = enki_wave(
        project=args.get("project"),
    )
    return json.dumps(result, indent=2)


def _handle_complete(args: dict) -> str:
    from .mcp.orch_tools import enki_complete
    result = enki_complete(
        task_id=args["task_id"],
        project=args.get("project", "."),
    )
    return json.dumps(result, indent=2)


def _handle_decompose(args: dict) -> str:
    from .mcp.orch_tools import enki_decompose
    result = enki_decompose(
        tasks=args["tasks"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_debate(args: dict) -> str:
    from .mcp.orch_tools import enki_debate
    result = enki_debate(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_debate_update(args: dict) -> str:
    from .mcp.orch_tools import enki_debate_update
    result = enki_debate_update(
        role=args["role"],
        round=args["round"],
        output=args["output"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_kickoff(args: dict) -> str:
    from .mcp.orch_tools import enki_kickoff
    result = enki_kickoff(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_kickoff_update(args: dict) -> str:
    from .mcp.orch_tools import enki_kickoff_update
    result = enki_kickoff_update(
        role=args["role"],
        output=args["output"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_kickoff_complete(args: dict) -> str:
    from .mcp.orch_tools import enki_kickoff_complete
    result = enki_kickoff_complete(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_escalate(args: dict) -> str:
    from .mcp.orch_tools import enki_escalate
    result = enki_escalate(
        task_id=args["task_id"],
        reason=args["reason"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_mark_blocked(args: dict) -> str:
    from .mcp.orch_tools import enki_mark_blocked
    result = enki_mark_blocked(
        task_id=args["task_id"],
        reason=args["reason"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_sprint_summary(args: dict) -> str:
    from .mcp.orch_tools import enki_sprint_summary
    result = enki_sprint_summary(
        sprint_id=args["sprint_id"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_sprint_close(args: dict) -> str:
    from .mcp.orch_tools import enki_sprint_close
    result = enki_sprint_close(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_diagram(args: dict) -> str:
    from .mcp.orch_tools import enki_diagram
    result = enki_diagram(
        type=args.get("type", "dag"),
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_status_update(args: dict) -> str:
    from .mcp.orch_tools import enki_status_update
    result = enki_status_update(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_mail_inbox(args: dict) -> str:
    from .mcp.orch_tools import enki_mail_inbox
    result = enki_mail_inbox(
        agent=args.get("agent", "EM"),
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_mail_thread(args: dict) -> str:
    from .mcp.orch_tools import enki_mail_thread
    result = enki_mail_thread(
        thread_id=args["thread_id"],
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_next_actions(args: dict) -> str:
    from .mcp.orch_tools import enki_next_actions
    result = enki_next_actions(
        project=args.get("project", "default"),
    )
    return json.dumps(result, indent=2)


def _handle_wrap(args: dict) -> str:
    from .mcp.orch_tools import enki_wrap
    _ = args
    result = enki_wrap()
    return json.dumps(result, indent=2)


def _handle_bug(args: dict) -> str:
    from .mcp.orch_tools import enki_bug
    result = enki_bug(
        action=args["action"],
        title=args.get("title"),
        description=args.get("description"),
        severity=args.get("severity", "medium"),
        bug_id=args.get("bug_id"),
        project=args.get("project", "."),
    )
    return json.dumps(result, indent=2)


def _handle_register(args: dict) -> str:
    from .mcp.orch_tools import enki_register
    result = enki_register(
        project=args.get("project"),
        path=args.get("path"),
    )
    return json.dumps(result, indent=2)


# =============================================================================
# Dispatch map
# =============================================================================

TOOL_HANDLERS = {
    "enki_remember": _handle_remember,
    "enki_recall": _handle_recall,
    "enki_star": _handle_star,
    "enki_status": _handle_status,
    "enki_restore": _handle_restore,
    "enki_goal": _handle_goal,
    "enki_phase": _handle_phase,
    "enki_approve": _handle_approve,
    "enki_spawn": _handle_spawn,
    "enki_report": _handle_report,
    "enki_wave": _handle_wave,
    "enki_decompose": _handle_decompose,
    "enki_debate": _handle_debate,
    "enki_debate_update": _handle_debate_update,
    "enki_kickoff": _handle_kickoff,
    "enki_kickoff_update": _handle_kickoff_update,
    "enki_kickoff_complete": _handle_kickoff_complete,
    "enki_escalate": _handle_escalate,
    "enki_mark_blocked": _handle_mark_blocked,
    "enki_sprint_summary": _handle_sprint_summary,
    "enki_sprint_close": _handle_sprint_close,
    "enki_diagram": _handle_diagram,
    "enki_status_update": _handle_status_update,
    "enki_mail_inbox": _handle_mail_inbox,
    "enki_mail_thread": _handle_mail_thread,
    "enki_next_actions": _handle_next_actions,
    "enki_complete": _handle_complete,
    "enki_wrap": _handle_wrap,
    "enki_bug": _handle_bug,
    "enki_register": _handle_register,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls via dispatch map."""
    init_all()
    args = arguments or {}

    try:
        if name == "enki_decompose":
            from .mcp.orch_tools import enki_decompose
            result = enki_decompose(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_debate":
            from .mcp.orch_tools import enki_debate
            result = enki_debate(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_debate_update":
            from .mcp.orch_tools import enki_debate_update
            result = enki_debate_update(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_kickoff":
            from .mcp.orch_tools import enki_kickoff
            result = enki_kickoff(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_kickoff_update":
            from .mcp.orch_tools import enki_kickoff_update
            result = enki_kickoff_update(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_kickoff_complete":
            from .mcp.orch_tools import enki_kickoff_complete
            result = enki_kickoff_complete(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_escalate":
            from .mcp.orch_tools import enki_escalate
            result = enki_escalate(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_mark_blocked":
            from .mcp.orch_tools import enki_mark_blocked
            result = enki_mark_blocked(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_sprint_summary":
            from .mcp.orch_tools import enki_sprint_summary
            result = enki_sprint_summary(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_sprint_close":
            from .mcp.orch_tools import enki_sprint_close
            result = enki_sprint_close(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_diagram":
            from .mcp.orch_tools import enki_diagram
            result = enki_diagram(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_status_update":
            from .mcp.orch_tools import enki_status_update
            result = enki_status_update(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_mail_inbox":
            from .mcp.orch_tools import enki_mail_inbox
            result = enki_mail_inbox(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_mail_thread":
            from .mcp.orch_tools import enki_mail_thread
            result = enki_mail_thread(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        elif name == "enki_next_actions":
            from .mcp.orch_tools import enki_next_actions
            result = enki_next_actions(**args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.exception(f"Error in {name}")
        return [TextContent(type="text", text=f"Error: {e}")]

    handler = TOOL_HANDLERS.get(name)
    if handler:
        try:
            result = handler(args)
            return [TextContent(type="text", text=result)]
        except Exception as e:
            logger.exception(f"Error in {name}")
            return [TextContent(type="text", text=f"Error: {e}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# =============================================================================
# Sync helpers for testing and CLI usage
# =============================================================================


def get_tools() -> list[dict]:
    """Sync wrapper: return tool definitions as dicts."""
    import asyncio
    tools = asyncio.get_event_loop().run_until_complete(list_tools())
    return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]


def handle_tool(name: str, arguments: dict) -> dict | str:
    """Sync wrapper: call a tool handler directly, return parsed result."""
    init_all()
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}
    result_str = handler(arguments)
    try:
        return json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
