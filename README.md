# Enki - Second Brain for Software Engineering

> *"Enki, goddess of wisdom, water, and creation. The cunning problem-solver who gave humanity the arts of civilization."*

Enki is a persistent second brain that:
- **Remembers** - Decisions, solutions, learnings across sessions/projects
- **Advises** - Challenges assumptions, suggests improvements
- **Manages** - Decomposes work, orchestrates agents, enforces TDD
- **Learns** - Gets smarter from cross-project patterns
- **Evolves** - Self-corrects based on her own violations

## Core Components

| Component | Purpose |
|-----------|---------|
| **Enki** | Memory, helper, friendly advisor (she/her) |
| **Ereshkigal** | The Challenger - questions reasoning, demands justification |
| **Together** | Symbiotic learning - they improve each other |

## Project Structure

```
Enki/
├── src/enki/           # Core implementation
├── scripts/hooks/      # Claude Code hooks
├── tests/              # Test suite
├── docs/               # Documentation
│   └── SPEC.md         # Full specification
└── .enki/              # Enki's own memory
```

## Quick Start

```bash
# Install
pip install -e .

# Run MCP server
python -m src.enki.mcp_server

# Copy hooks to Claude Code
cp scripts/hooks/*.sh ~/.claude/hooks/
```

## Documentation

- [Full Specification](docs/SPEC.md) - Complete design document

## Status

**Phase**: Design Complete, Implementation Not Started

See [SPEC.md](docs/SPEC.md) for:
- Memory system (beads, embeddings, retention)
- PM system (debate, plan, approve)
- Orchestrator (TDD, agents, bug loops)
- Enforcement (tiers, gates)
- Ereshkigal (semantic challenge system)
- Symbiotic learning
- Migration from Odin/Freyja
