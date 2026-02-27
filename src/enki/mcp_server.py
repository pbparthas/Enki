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
            description="Store knowledge (decision, learning, pattern, fix, preference). Preferences → permanent. Others → staging for review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The knowledge to remember"},
                    "category": {
                        "type": "string",
                        "enum": ["decision", "learning", "pattern", "fix", "preference", "code_knowledge"],
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
            description="Search for relevant knowledge across permanent and staged stores",
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
            inputSchema={"type": "object", "properties": {}},
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
            description="Set active goal. Satisfies Gate 1. Auto-detects tier.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What we're building"},
                    "project": {"type": "string", "description": "Optional project ID", "default": "."},
                    "spec_path": {"type": "string", "description": "Optional authored spec path"},
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="enki_phase",
            description="Advance phase sequentially or return phase status.",
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
                    "project": {"type": "string", "default": "."},
                },
                "required": ["action"],
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
                    "project": {"type": "string", "default": "."},
                },
                "required": ["role", "task_id"],
            },
        ),
        Tool(
            name="enki_wave",
            description="Run next ready wave; always spawns both Dev and QA for each task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "project": {"type": "string", "default": "."},
                },
                "required": ["goal_id"],
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
            name="enki_triage",
            description="Auto-detect tier from description (minimal/standard/full)",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Task description to triage"},
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="enki_quick",
            description="Fast-path for Minimal tier. Combines goal + triage + phase in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What to fix/change"},
                    "project": {"type": "string", "default": "."},
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="enki_orchestrate",
            description="Begin execution — EM starts spawning tasks",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "."},
                },
            },
        ),
        Tool(
            name="enki_decompose",
            description="Break spec into task DAG",
            inputSchema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "files": {"type": "array", "items": {"type": "string"}},
                                "dependencies": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name"],
                        },
                        "description": "Task definitions",
                    },
                    "project": {"type": "string", "default": "."},
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="enki_bug",
            description="File or manage bugs",
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
    result = enki_status()
    return json.dumps(result, indent=2)


def _handle_restore(args: dict) -> str:
    from .mcp.memory_tools import enki_restore
    result = enki_restore(project=args.get("project"))
    return json.dumps(result, indent=2)


def _handle_goal(args: dict) -> str:
    from .mcp.orch_tools import enki_goal
    result = enki_goal(
        args["description"],
        args.get("project", "."),
        args.get("spec_path"),
    )
    return json.dumps(result, indent=2)


def _handle_phase(args: dict) -> str:
    from .mcp.orch_tools import enki_phase
    result = enki_phase(
        args["action"],
        args.get("to"),
        args.get("project", "."),
    )
    return json.dumps(result, indent=2)


def _handle_spawn(args: dict) -> str:
    from .mcp.orch_tools import enki_spawn
    result = enki_spawn(
        role=args["role"],
        task_id=args["task_id"],
        context=args.get("context"),
        project=args.get("project", "."),
    )
    return json.dumps(result, indent=2)


def _handle_wave(args: dict) -> str:
    from .mcp.orch_tools import enki_wave
    result = enki_wave(
        goal_id=args["goal_id"],
        project=args.get("project", "."),
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


def _handle_triage(args: dict) -> str:
    from .mcp.orch_tools import enki_triage
    result = enki_triage(args["description"])
    return json.dumps(result, indent=2)


def _handle_quick(args: dict) -> str:
    from .mcp.orch_tools import enki_quick
    result = enki_quick(args["description"], args.get("project", "."))
    return json.dumps(result, indent=2)


def _handle_orchestrate(args: dict) -> str:
    from .mcp.orch_tools import enki_orchestrate
    result = enki_orchestrate(args.get("project", "."))
    return json.dumps(result, indent=2)


def _handle_decompose(args: dict) -> str:
    from .mcp.orch_tools import enki_decompose
    result = enki_decompose(args["tasks"], args.get("project", "."))
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
    "enki_spawn": _handle_spawn,
    "enki_wave": _handle_wave,
    "enki_complete": _handle_complete,
    "enki_wrap": _handle_wrap,
    "enki_triage": _handle_triage,
    "enki_quick": _handle_quick,
    "enki_orchestrate": _handle_orchestrate,
    "enki_decompose": _handle_decompose,
    "enki_bug": _handle_bug,
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
