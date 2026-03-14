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
                        "enum": ["igi", "spec", "architect", "test"],
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
    "enki_complete": _handle_complete,
    "enki_wrap": _handle_wrap,
    "enki_bug": _handle_bug,
    "enki_register": _handle_register,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls via dispatch map."""
    init_all()

    handler = TOOL_HANDLERS.get(name)
    if handler:
        try:
            result = handler(arguments)
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
