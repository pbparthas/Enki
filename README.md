# Enki — Persistent Second Brain for Software Engineering

Enki remembers your decisions across sessions, enforces quality gates
before code changes, and orchestrates multi-agent workflows.

## Quick Start

    pip install enki-ai
    enki setup

Then in Claude Code:

    enki_goal "implement JWT authentication"
    enki_recall "how did we handle auth before?"

## What It Does

**Remembers** — Decisions, solutions, and learnings persist in local
SQLite databases. They survive session resets, compaction, and restarts.

**Enforces** — Gates block code changes until you've set a goal, approved
a spec, and followed TDD. Enforcement is structural, not instructional.
Missing a step doesn't produce a warning — it blocks the tool call.

**Orchestrates** — Decomposes specs into tasks, spawns specialized agents
(Dev, QA, Validator, Reviewer), manages workflow with blind validation
where agents cannot see each other's work.

## Requirements

- Python 3.11+
- Claude Code CLI
- Optional: Gemini CLI (for external code review)

## Architecture

| Pillar | Name | What |
|--------|------|------|
| Memory | Abzu | Knowledge units (beads), search, decay, staging, external review |
| Gates | Uru | File protection, workflow gates, fail-closed enforcement |
| Orchestration | EM | Agent communication, DAG execution, blind validation |

## MCP Tools

| Tool | What |
|------|------|
| enki_remember | Store a decision, solution, or learning |
| enki_recall | Search your knowledge base |
| enki_star | Pin important knowledge (exempt from decay) |
| enki_status | Memory and session health check |
| enki_goal | Set session goal (required before coding) |
| enki_phase | Get/set workflow phase |
| enki_triage | Auto-detect project complexity tier |
| enki_quick | Skip ceremony for small changes |
| enki_orchestrate | Start multi-agent execution |
| enki_decompose | Break spec into tasks |
| enki_bug | File and track bugs |

## CLI

    enki setup              # First-time setup
    enki status             # Current state
    enki recall "query"     # Search knowledge
    enki approve            # Approve spec (human-only, never AI)
    enki digest weekly      # What you learned this week

## Configuration

~/.enki/config/enki.toml — created by enki setup.

## License

Apache 2.0
