# Enki v3 — Build Instructions

## What This Is

You are building Enki v3 from scratch. This is a complete rewrite — the existing codebase (~17.2K lines, 28 modules) is being retired. Do not reference, import from, or preserve any existing code. The new system is ~10,800 lines across ~43 files organized into 4 build phases.

## Documents (Read In This Order)

1. **enki-v3-design-decisions.md** — Read FIRST. Every architectural decision and why. Do not re-debate decided items.
2. **enki-v3-implementation-spec-v1.2.md** — Your PRIMARY build document. Directory structure, build order, code samples, interfaces, test strategy. Build from this.
3. **abzu-memory-spec-v1.2.md** — Pillar 1: Memory system (beads, sessions, staging, Gemini review interface)
4. **uru-gates-spec-v1.1.md** — Pillar 2: Gates system (Layer 0/0.5/1, nudges, hooks, feedback loop)
5. **em-orchestrator-spec-v1.4.md** — Pillar 3: Orchestration (agents, DAG, mail, PM workflow, onboarding, tiers)
6. **enki-v3-bridge-spec-v1.1.md** — Cross-pillar interfaces and hook orchestration
7. **enki-v3-ship-quality-spec-v1.2.md** — Test pyramid, CI pipeline, deployment, qualification, closure
8. **enki-v3-agent-prompts-v1.5.md** — Agent prompt files (you do NOT write these — they are Layer 0 protected. Just create the prompts/ directory and copy these files in verbatim)
9. **agent-prompt-spec-v1.0.md** — How prompts are assembled at runtime by agents.py
10. **PERSONA.md** — Identity file. Copy to ~/.enki/persona/PERSONA.md during setup.

## Build Phases (Strict Order)

```
Phase 0: Bootstrap
    schemas.py (all 4 DBs + user_profile) → DB initialization → CLAUDE.md

Phase 1: Uru (Gates) — ~910 lines
    Layer 0 → Layer 0.5 → Layer 1 → Nudges → All 6 hooks
    Layer 0 MUST protect: hooks/, prompts/, uru.py, uru.db, PERSONA.md, abzu.py
    TEST: Gates block correctly AND exempt paths pass correctly

Phase 2: Abzu (Memory) — ~2,300 lines
    beads.py → sessions.py → extraction.py → retention.py → staging.py → gemini.py → abzu.py
    MCP tools: enki_remember, enki_recall, enki_star, enki_status
    TEST: Bead CRUD, FTS5 search, session lifecycle, staging promotion

Phase 3: EM (Orchestration) — ~6,750 lines
    mail.py → task_graph.py → agents.py → tiers.py → pm.py → validation.py
    → bugs.py → parsing.py → bridge.py → status.py → yggdrasil.py
    → claude_md.py → devops.py → onboarding.py → researcher.py → orchestrator.py
    TEST: Mail CRUD, DAG construction, agent spawning, tier detection

Phase 4: Integration
    Hook wiring (hooks call Abzu + Uru together)
    End-to-end session lifecycle test
    Migration script: scripts/migrate_v1.py (one-time, reads old wisdom.db, maps categories, writes to abzu.db staging)
    Gemini review script: scripts/gemini_review.py (generates review package as markdown)
```

## Critical Rules

1. **No existing code reuse.** This is from scratch. The old codebase is reference for behavior understanding only.
2. **Implementation Spec is your blueprint.** Build order, file names, interfaces — follow it exactly. If you find a conflict between specs, Implementation Spec wins for structure; Product Specs win for behavior.
3. **Layer 0 files are sacred.** The hooks/ and prompts/ directories, uru.py, uru.db, PERSONA.md, and abzu.py core functions are Layer 0 protected. You will build the Layer 0 protection that prevents future edits to these files — including by yourself. This is intentional.
4. **Agent prompts are pre-written.** Copy the 15 files from enki-v3-agent-prompts-v1.5.md into ~/.enki/prompts/ verbatim. Do not modify their content. agents.py assembles them at runtime.
5. **SQLite conventions.** WAL mode and busy_timeout on every connection. See db.py in Implementation Spec.
6. **Test strategy.** You write tests FOR Enki (the framework). The Ship & Quality spec describes tests Enki writes for OTHER projects. Don't conflate them.
7. **Migration.** The old wisdom.db has 378 beads. The migration script maps categories (architectural_decision→decision, lesson_learned→learning, code_pattern→pattern, bug_fix→fix, *_preference→preference). Preferences go directly to new wisdom.db. Everything else goes to abzu.db staging for Gemini review. This script runs ONCE after v3 is built, then gets deleted.

## What Success Looks Like

- All 4 databases initialize cleanly (wisdom.db, abzu.db, uru.db, em.db)
- Layer 0 blocks writes to protected files (including its own source)
- Hooks fire at correct lifecycle points
- `enki_remember` routes preferences to wisdom.db, everything else to staging
- `enki_recall` searches both wisdom.db and staging, ranks appropriately
- Session summaries survive compaction and session boundaries
- EM can spawn agents, parse their JSON output, route mail
- Gates enforce phase workflow without blocking legitimate work
- Exempt paths work (documentation, config files pass through gates)
- Migration script converts old beads to new schema

## Start

Read the Design Decisions document first. Then read the Implementation Spec. Then start Phase 0.
