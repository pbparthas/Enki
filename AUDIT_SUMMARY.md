# Enki v3 Audit Summary

Date: 2026-02-15

This summary consolidates all issues and items working as expected from the audit of `src/enki/` against the listed specs (Implementation v1.2, Uru Gates v1.1, Abzu Memory v1.2, EM Orchestrator v1.4, Bridge v1.1, Ship Quality v1.2, Agent Prompt v1.0).

## Executive Summary

**High-risk enforcement gaps**
- Gate 2 (spec approval) can be bypassed because `enki_approve` is exposed via MCP tools and not restricted to human-only CLI.
- Layer 0 protected list is incomplete (missing `abzu.py`, `layer0.conf`), enabling edits to core enforcement/memory modules.
- Blind wall filtering is broken: `get_blind_wall_filter()` returns `sees/blocked` but `spawn_agent()` expects `exclude`, so no filtering occurs.
- Prompts directory is missing; agents silently fall back to default prompts, which violates prompt immutability and blind wall requirements.

**System-wide policy violations**
- Raw SQL is used throughout modules outside `db.py`, violating the stated “no raw SQL outside db.py” requirement.

**Major functionality gaps**
- Abzu session end flow does not perform heuristic extraction or candidate staging.
- Prompt version logging to em.db is missing.
- MCP tool surface is incomplete/misaligned with spec (missing listed tools; extra tools added; broken tool handlers).

## Issues (Grouped by Theme)

### Enforcement Integrity (Fox Problem)
- **Gate 2 bypass**: `enki_approve` exposed to CC via MCP (`src/enki/mcp/orch_tools.py`, `src/enki/mcp_server.py`).
- **Layer 0 blocklist incomplete**: missing `abzu.py` and `layer0.conf` (`src/enki/gates/layer0.py`).
- **Layer 0.5 gaps**: DB protection is heuristic; limited target extraction can fail open (`src/enki/gates/layer0.py`).
- **Blind wall broken**: `spawn_agent()` uses `exclude` but filter uses `blocked`, so no filtering (`src/enki/orch/orchestrator.py`, `src/enki/orch/agents.py`).
- **Prompts missing**: no `prompts/` directory in repo; prompt fallback is fail-open (`src/enki/orch/agents.py`, `src/enki/setup.py`).

### Spec Compliance Gaps
- **Abzu session lifecycle**: no extraction/staging on session end (`src/enki/memory/abzu.py`, `src/enki/memory/sessions.py`).
- **JSONL versioning**: declared but not enforced (`src/enki/memory/extraction.py`).
- **Prompt version logging**: missing (`src/enki/orch/agents.py`, `src/enki/orch/orchestrator.py`).
- **Bridge staging**: extracted beads aren’t staged into abzu.db (`src/enki/orch/bridge.py`).
- **Gemini response handling**: consolidate actions not handled in `process_review_response` (`src/enki/memory/gemini.py`).
- **Yggdrasil**: explicitly marked “v3 stub”; missing full spec features (`src/enki/orch/yggdrasil.py`).

### Tooling / MCP Surface
- **Missing tools in MCP server**: several spec-listed tools are defined but not exposed (e.g., `enki_spec`, `enki_intake`, `enki_status_update`, `enki_sprint_summary`, `enki_extract_beads`).
- **Extra tools not in spec**: `enki_log`, `enki_maintain`, and several extended memory tools.
- **Broken tool handlers**: `enki_mail_send` uses wrong `create_thread`/`send` signatures; `_handle_mail_inbox` reads `content` but schema uses `body`.

### Database Integrity
- **Raw SQL outside db.py**: pervasive in modules across gates, memory, and orchestration.
- **Migration script**: uses direct sqlite3 connections and calls `add_candidate(..., tags=...)` even though the function doesn’t accept `tags` (`src/enki/scripts/migrate_v1.py`).
- **FTS triggers**: `candidates_fts` lacks update trigger (`src/enki/memory/schemas.py`).

### Error Handling / Fail-Open Risks
- Gate modules swallow exceptions and return allow-like defaults (`src/enki/gates/uru.py`).
- Parsing failure retry/escalation is not wired into orchestrator flow (`src/enki/orch/orchestrator.py`, `src/enki/orch/parsing.py`, `src/enki/orch/validation.py`).
- Prompt loading failures are silent fallbacks (`src/enki/orch/agents.py`).

## Things Working as Expected

### Core DB Layer
- `src/enki/db.py` correctly configures WAL, busy_timeout, and foreign_keys, and centralizes connection handling.

### Schemas
- Uru schema tables match spec (`src/enki/gates/schemas.py`).
- EM schema tables match spec (`src/enki/orch/schemas.py`).
- Abzu wisdom/abzu DB schema is largely aligned with spec (`src/enki/memory/schemas.py`), aside from missing update trigger for candidates.

### Memory (Partial Compliance)
- Bead CRUD, FTS5 search, scoring/boosting implemented (`src/enki/memory/beads.py`).
- Staging and promotion paths exist (`src/enki/memory/staging.py`).
- Decay/retention logic implemented (`src/enki/memory/retention.py`).
- Gemini review package generation and validation implemented (`src/enki/memory/gemini.py`, `src/enki/scripts/gemini_review.py`).

### Orchestration (Partial Compliance)
- Mail CRUD and thread hierarchy implemented (`src/enki/orch/mail.py`).
- DAG/task graph core implemented (`src/enki/orch/task_graph.py`).
- PM intake, debate, decision tracking implemented (`src/enki/orch/pm.py`).
- Researcher Codebase Profile implemented (`src/enki/orch/researcher.py`).
- Tiering logic implemented (`src/enki/orch/tiers.py`).

## Highest Priority Fixes (Recommended Order)
1. Remove `enki_approve` from MCP surface; keep approval human-only via CLI.
2. Fix Layer 0 protected list (`abzu.py`, `layer0.conf`) and ensure prompt files are installed/read-only.
3. Fix blind wall filtering in `spawn_agent()`; align filter keys and enforce exclusions.
4. Implement session-end extraction and staging in Abzu.
5. Wire parse retries and HITL escalation into orchestrator flow.
6. Align MCP server tools with spec; fix broken handlers.
7. Address raw SQL policy (either allow it or refactor to centralized DB API).

## File References
- `src/enki/gates/layer0.py`
- `src/enki/gates/uru.py`
- `src/enki/orch/orchestrator.py`
- `src/enki/orch/agents.py`
- `src/enki/orch/validation.py`
- `src/enki/orch/mail.py`
- `src/enki/memory/abzu.py`
- `src/enki/memory/beads.py`
- `src/enki/memory/schemas.py`
- `src/enki/memory/staging.py`
- `src/enki/memory/sessions.py`
- `src/enki/memory/extraction.py`
- `src/enki/memory/gemini.py`
- `src/enki/orch/bridge.py`
- `src/enki/mcp/orch_tools.py`
- `src/enki/mcp/memory_tools.py`
- `src/enki/mcp_server.py`
- `src/enki/scripts/migrate_v1.py`

