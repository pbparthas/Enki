"""MCP server exposing Enki v3 tools.

v3: Rewired to use Abzu (memory), Uru (gates), and EM (orchestration).
Down from 1591 lines / 35 tools to clean dispatch against v3 modules.
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
    """List available Enki v3 tools."""
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
                        "enum": ["decision", "learning", "pattern", "fix", "preference"],
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
            description="Star a bead — starred beads never decay",
            inputSchema={
                "type": "object",
                "properties": {
                    "bead_id": {"type": "string", "description": "Bead ID to star"},
                },
                "required": ["bead_id"],
            },
        ),
        Tool(
            name="enki_status",
            description="Get memory system health: bead counts, staging depth, decay stats",
            inputSchema={"type": "object", "properties": {}},
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
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="enki_phase",
            description="Set current phase (intake/debate/plan/implement/review/ship). Gate 3 satisfied at implement+.",
            inputSchema={
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["intake", "debate", "plan", "implement", "review", "ship"],
                        "description": "Phase to set",
                    },
                    "project": {"type": "string", "default": "."},
                },
                "required": ["phase"],
            },
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
            name="enki_approve",
            description="Human approval of spec. Satisfies Gate 2.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "default": "."},
                },
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
        lines.append(f"   ID: {r.get('id', '?')}\n")
    return "\n".join(lines)


def _handle_star(args: dict) -> str:
    from .mcp.memory_tools import enki_star
    result = enki_star(args["bead_id"])
    return json.dumps(result)


def _handle_status(args: dict) -> str:
    from .mcp.memory_tools import enki_status
    result = enki_status()
    return json.dumps(result, indent=2)


def _handle_goal(args: dict) -> str:
    from .mcp.orch_tools import enki_goal
    result = enki_goal(args["description"], args.get("project", "."))
    return json.dumps(result, indent=2)


def _handle_phase(args: dict) -> str:
    from .mcp.orch_tools import enki_phase
    result = enki_phase(args["phase"], args.get("project", "."))
    return json.dumps(result, indent=2)


def _handle_triage(args: dict) -> str:
    from .mcp.orch_tools import enki_triage
    result = enki_triage(args["description"])
    return json.dumps(result, indent=2)


def _handle_quick(args: dict) -> str:
    from .mcp.orch_tools import enki_quick
    result = enki_quick(args["description"], args.get("project", "."))
    return json.dumps(result, indent=2)


def _handle_approve(args: dict) -> str:
    from .mcp.orch_tools import enki_approve
    result = enki_approve(args.get("project", "."))
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
    "enki_goal": _handle_goal,
    "enki_phase": _handle_phase,
    "enki_triage": _handle_triage,
    "enki_quick": _handle_quick,
    "enki_approve": _handle_approve,
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
