"""MCP server exposing Enki tools."""

import json
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .db import init_db, get_db
from .beads import create_bead, get_bead, star_bead, unstar_bead, supersede_bead, BeadType
from .search import search
from .retention import maintain_wisdom

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
            description="Get memory statistics",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Optional project filter",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""

    # Ensure database is initialized
    init_db()

    if name == "enki_remember":
        bead = create_bead(
            content=arguments["content"],
            bead_type=arguments["type"],
            summary=arguments.get("summary"),
            project=arguments.get("project"),
            context=arguments.get("context"),
            tags=arguments.get("tags"),
            starred=arguments.get("starred", False),
        )
        return [TextContent(
            type="text",
            text=f"Remembered [{bead.type}] {bead.id}\n\n{bead.content[:200]}{'...' if len(bead.content) > 200 else ''}",
        )]

    elif name == "enki_recall":
        results = search(
            query=arguments["query"],
            project=arguments.get("project"),
            bead_type=arguments.get("type"),
            limit=arguments.get("limit", 10),
        )

        if not results:
            return [TextContent(type="text", text="No relevant knowledge found.")]

        lines = [f"Found {len(results)} results:\n"]
        for i, result in enumerate(results, 1):
            bead = result.bead
            sources = "+".join(result.sources)
            starred = "*" if bead.starred else ""
            lines.append(
                f"{i}. [{bead.type}]{starred} (score: {result.score:.2f}, via: {sources})\n"
                f"   {bead.summary or bead.content[:150]}{'...' if len(bead.content) > 150 else ''}\n"
                f"   ID: {bead.id}\n"
            )

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "enki_forget":
        bead = supersede_bead(arguments["old_id"], arguments["new_id"])
        if bead:
            return [TextContent(type="text", text=f"Marked {arguments['old_id']} as superseded by {arguments['new_id']}")]
        else:
            return [TextContent(type="text", text=f"Bead {arguments['old_id']} not found")]

    elif name == "enki_star":
        starred = arguments.get("starred", True)
        if starred:
            bead = star_bead(arguments["bead_id"])
            action = "Starred"
        else:
            bead = unstar_bead(arguments["bead_id"])
            action = "Unstarred"

        if bead:
            return [TextContent(type="text", text=f"{action} bead {bead.id}")]
        else:
            return [TextContent(type="text", text=f"Bead {arguments['bead_id']} not found")]

    elif name == "enki_status":
        db = get_db()
        project = arguments.get("project")

        # Get counts
        if project:
            total = db.execute(
                "SELECT COUNT(*) as count FROM beads WHERE project = ? OR project IS NULL",
                (project,),
            ).fetchone()["count"]
        else:
            total = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()["count"]

        active = db.execute(
            "SELECT COUNT(*) as count FROM beads WHERE superseded_by IS NULL"
        ).fetchone()["count"]

        starred = db.execute(
            "SELECT COUNT(*) as count FROM beads WHERE starred = 1"
        ).fetchone()["count"]

        by_type = db.execute(
            "SELECT type, COUNT(*) as count FROM beads WHERE superseded_by IS NULL GROUP BY type"
        ).fetchall()

        type_counts = {row["type"]: row["count"] for row in by_type}

        lines = [
            "Enki Memory Status",
            "=" * 40,
            f"Total beads: {total}",
            f"Active beads: {active}",
            f"Starred beads: {starred}",
            "",
            "By type:",
        ]
        for bead_type in ["decision", "solution", "learning", "violation", "pattern"]:
            count = type_counts.get(bead_type, 0)
            lines.append(f"  {bead_type}: {count}")

        return [TextContent(type="text", text="\n".join(lines))]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
