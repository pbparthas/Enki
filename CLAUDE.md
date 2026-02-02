# Enki Development Guidelines

## What is Enki?

Enki is a second brain system for software engineering - persistent memory, PM capabilities, orchestration, and self-improvement.

## Key Principle: Start Simple

The spec is ambitious (13 parts, 8 phases). DO NOT try to build everything at once.

**Implementation Order:**
1. Phase 0: Migration scripts (if needed)
2. Phase 1: Memory foundation (beads, embeddings, search)
3. Phase 2: Basic enforcement (file/line counting, simple gates)
4. Get these working and tested
5. THEN add complexity (PM, orchestrator, Ereshkigal)

## Code Style

- Python 3.11+
- Type hints everywhere
- Pytest for testing
- SQLite for storage (wisdom.db)
- sentence-transformers for embeddings

## File Conventions

```
src/enki/
├── __init__.py
├── memory.py       # Bead storage, search
├── embeddings.py   # Vector operations
├── gates.py        # Enforcement logic
├── mcp_server.py   # MCP tools
└── ...

tests/
├── test_memory.py
├── test_embeddings.py
└── ...

scripts/hooks/
├── enki-session-start.sh
├── enki-pre-tool-use.sh
└── ...
```

## Testing

```bash
pytest tests/ -v
```

All new code must have tests. TDD preferred.

## The Two Personas

When working on Enki:
- **Enki** = The helpful advisor, stores memory, surfaces context
- **Ereshkigal** = The challenger, questions reasoning

Both are part of the same system. They learn from each other.

## Warnings

1. **Don't over-engineer** - The spec is a target, not a mandate for day 1
2. **Semantic analysis is expensive** - Start with simpler heuristics
3. **Claude challenging itself** - May be theater, evaluate if it actually works
4. **Migration can wait** - Get core working first
