# Enki v3 Design Decisions — Living Document

> **Purpose**: This document captures every architectural decision made during the Enki from-scratch redesign. It is the continuity artifact — when a new chat starts, reading this document should bring the reviewer up to speed on where we are, what was decided, and what's still open.
>
> **Last updated**: 2026-02-24
>
> **Context**: Current Enki (v1/v2) is disabled. We are redesigning from scratch using a three-pillar model. This project (Claude.ai) is the external review channel — Claude Code never sees these conversations. Partha brings specs and decisions here for independent analysis, then takes refined outputs back to implementation.

---

## Three-Pillar Architecture

Enki v3 is structured as three independent pillars with defined interfaces:

| Pillar | Name | What | Spec | Status |
|---|---|---|---|---|
| **1. Memory** | Abzu | Beads, session summaries, candidate staging, Gemini review | abzu-memory-spec.md v1.2 | ✅ Complete |
| **2. Gates** | Uru | Workflow enforcement, hooks, feedback loop | uru-gates-spec.md v1.1 | ✅ Complete |
| **3. Orchestration** | EM | Agents (13), DAG, specs, mail, CLAUDE.md, onboarding, validation | em-orchestrator-spec.md v1.4 | ✅ Complete |
| **Bridge** | — | Cross-pillar interfaces, hook orchestration, Gemini review cycle | enki-v3-bridge-spec.md v1.0 | ✅ Complete |
| **Ship & Quality** | — | Test pyramid, regression practice, CI, Prism, deploy, closure | enki-v3-ship-quality-spec.md v1.2 | ✅ Complete |
| **Implementation** | — | Build blueprint: phases, directory, code samples, test strategy | enki-v3-implementation-spec.md v1.2 | ✅ Complete |
| **Agent Prompts** | — | 13 agent prompt specifications, shared templates, Layer 0 protected | agent-prompt-spec.md v1.0 | ⏳ Gemini writing |

### Naming Convention

All names from Sumerian mythology, maintaining consistency with Enki:

| Name | Mythology | Role in Enki v3 |
|---|---|---|
| **Enki** | God of wisdom, water, creation | The orchestrating identity — persona + framework |
| **Abzu** | Primordial freshwater ocean, Enki's domain | Memory system — knowledge rises from the deep |
| **Uru** | Guardian | Enforcement — ensures Enki follows its own rules |
| **Ereshkigal** | ~~Queen of the underworld~~ | Retired. Replaced by Uru. |

---

## Spawn Authority (Corrected)

**This is the definitive spawn model. EM does NOT spawn PM.**

PM and EM are peer departments within Enki — like PM and Engineering in a real SE org. Neither spawns nor controls the other. They communicate through mail.

| Agent | Spawned By | Role |
|---|---|---|
| **PM** | Enki | Project owner — intake, specs, debate, status, customer presentation, closure |
| **EM** | Enki | Execution manager — DAG, agent spawning, mail routing |
| **Architect, DBA** | Enki (at PM's request) | Planning agents |
| **Dev, QA, UI/UX, Validator, Reviewer, InfoSec, DevOps, Docs, Performance, Researcher** | EM | Execution agents |

Infrastructure components (Abzu, Uru) are NOT spawned — they're libraries/hooks, always available.

**14 agents total** matching Odin's original vision. See EM Spec Section 7 for conditional spawning rules.

---

## Database Architecture

Four databases with clean ownership:

| Database | Owner | Location | Lifespan | Purpose |
|---|---|---|---|---|
| **wisdom.db** | Abzu | `~/.enki/wisdom.db` | Permanent | Gemini-approved beads, FTS5, project registry |
| **abzu.db** | Abzu | `~/.enki/abzu.db` | Rolling | Session summaries, bead candidates (staging), extraction log |
| **em.db** | EM | `~/.enki/projects/{name}/em.db` | Per-project + 30 days | Mail, threads, task_state, sprint_state, bugs, pm_decisions |
| **uru.db** | Uru | `~/.enki/uru.db` | Permanent | Enforcement logs, feedback proposals, nudge state |

### Key Rule: No Cross-Writes

No component writes to another component's database. Data moves through function calls, not direct DB access.

---

## Pillar 1: Abzu (Memory) — Decided

### Diagnostic Findings

Current Enki memory has 15 confirmed issues: 17 tables (8 permanently empty), 35 MCP tools (31 never automated), embeddings at 1.3% coverage, retention that never acted (371/378 beads at weight 1.0), session tracking broken, post-compaction losing beads. Pattern: strong schemas, weak wiring.

### Anti-Pattern Reference: PLTM-Claude

Analyzed PLTM-Claude (136 MCP tools, 40 tables, 3-judge memory jury, knowledge graphs). Established as "what not to build." Claudest (2 tools, SQLite + FTS5, 74% LoCoMo benchmark) established as "right direction."

### Bead Design

Five categories replacing 8 types + 4 kinds:

| Category | What | Decay |
|---|---|---|
| `decision` | Architectural choice with reasoning | Standard |
| `learning` | Discovered through experience | Standard |
| `pattern` | Reusable approach | Slower |
| `fix` | Error → solution pair | Standard |
| `preference` | Work style, tool choice | Never |

### Two-Gate Quality Control

**All non-preference beads go to staging (abzu.db), not directly to wisdom.db.** Only two paths write to wisdom.db:
1. Preference beads — direct (factual, no review needed)
2. Gemini-promoted beads — quarterly reviewed and approved

CC extracts candidates. Gemini decides what's permanent.

### Ingestion Paths

| Path | When | Destination |
|---|---|---|
| `enki_remember` (preference) | During session | wisdom.db direct |
| `enki_remember` (non-preference) | During session | abzu.db staging |
| Session-end extraction (heuristic first, then CC distillation) | Session end | abzu.db staging |
| Project completion (heuristic + CC from em.db) | Project closes | abzu.db staging |
| Gemini review | Quarterly | Promotes to wisdom.db |

### Session Summary Lifecycle

Pre-compact summaries accumulate across compactions (conversational + operational state). Session end reconciles all accumulated summaries into one final summary + bead candidates. Post-compact re-injects the full intellectual thread, not just operational state.

### Retention

Recall-based decay (recalled beads stay hot, unused beads fade). Decay reduces search ranking but **never deletes**. Only Gemini can flag for deletion. No `enki_forget` tool.

### MCP Tools: 4

`enki_remember`, `enki_recall`, `enki_star`, `enki_status`. Down from 35.

### FTS5 Only

Embeddings cut (1.3% coverage proved unmaintained). FTS5 first. Add embeddings only if FTS5 proves insufficient.

---

## Pillar 2: Uru (Gates) — Decided

### Core Insight: CC's Own Admission

CC was asked directly why it doesn't follow the workflow. Key quotes:
- "I fall back to direct action"
- "Hooks are walls I hit when I forget"
- "I'll still find ways to justify skipping it"
- "Agent finds new rationalization: 'I'll set the goal and immediately edit'"

### Design Philosophy

**The problem is not adversarial bypass — CC ignores the workflow because direct action is zero friction.** The fix is hooks (mechanical walls), not rules (suggestions CC rationalizes past). DB-backed state means CC can't rubber-stamp its own approvals.

### Hook System

Six hook points in CC's lifecycle:

| Hook | Uru's Use |
|---|---|
| **session-start** | Initialize enforcement state, inject workflow context |
| **pre-tool-use** | Primary enforcement — Layer 0 blocklist + 3 gate checks |
| **post-tool-use** | Nudges (non-blocking reminders) |
| **pre-compact** | Log enforcement state, trigger Abzu summary capture |
| **post-compact** | Re-inject enforcement context so CC remembers rules |
| **session-end** | Write enforcement summary, generate feedback proposals |

### Three Hard Blocks

| Gate | Check | Block If |
|---|---|---|
| Gate 1: No Goal → No Code | Active goal exists in em.db | No goal set |
| Gate 2: No Spec → No Agents | Approved spec exists (human_approved=1) | No approved spec (Standard/Full tier) |
| Gate 3: Wrong Phase → No Code | Phase >= implement | Phase is intake/debate/plan |

### Two Nudges

| Nudge | When |
|---|---|
| Unrecorded decision | CC outputs decision language but no `enki_remember` follows |
| Long session | 30+ tool calls without summary update |

### Layer 0: Infrastructure Protection

Protected files CC cannot edit: hooks, uru.py, uru.db, PERSONA.md, abzu.py core. Pure bash check, <1ms.

### Known Escape Hatches — Closed

| Escape Hatch | How Closed |
|---|---|
| "Set goal and immediately edit" | Gate 3: phase must be >= implement, can't skip |
| "It's a small change" | Gate 1 applies to ALL code mutations |
| Write STATE.md markers | State is DB-backed, no file markers |
| Edit hook scripts | Layer 0 protected |
| Bash file writes to bypass Edit | Hook inspects bash commands for write patterns |
| "Skip workflow" verbal bypass | Not implemented. No magic words. |

### Feedback Loop = Evolution

Proposals auto-created from overrides and ignored nudges. Gemini reviews quarterly. Human approves changes. CC never modifies its own rules.

---

## Pillar 3: EM (Orchestration) — Decided

### Odin Analysis

Strong data structures (TaskGraph, blind validation, cyclic recovery), weak wiring. ~6,500 lines cut. Keeping: TaskGraph with waves, blind validation, cyclic recovery (max 3 → HITL).

### Tier System

| Tier | Context | Scale | When |
|---|---|---|---|
| Minimal | Phase + goal | Single cycle: Dev → QA → done | Config, typos, bug fixes |
| Standard | + Spec, tasks, CLAUDE.md | Single sprint, parallel QA+Dev, Validators | Medium features |
| Full | + Beads, history, CLAUDE.md | Multi-sprint, two-spec, all agents, red-cell | New systems, large features |

### Two-Spec Model

PM writes Product Spec (WHAT), Architect writes Implementation Spec (HOW). Negotiation loop with max cycles before HITL. Blocker/risk/suggestion classification for pushback.

### CLAUDE.md as First-Class Artifact (v1.3)

**Decision**: CLAUDE.md is a planning-phase artifact, not an afterthought. Created by Architect after Implementation Spec is approved, before any code is written.

**WHY**: Industry consensus — CLAUDE.md is the highest leverage point for Claude Code effectiveness. Without it, CC starts blind every session.

**Framework**: WHY (project purpose) / WHAT (stack, structure) / HOW (commands, workflows). Under 300 lines. Progressive disclosure — point to docs, don't embed them.

**Who creates**: Architect (with PM input for WHY, customer input for custom instructions, DBA for data conventions).

**Lifecycle**: Version-controlled, updated by Architect per sprint, locked with specs at start.

**Tier**: Minimal skips (too small). Standard gets minimal version. Full gets full framework with customer instructions.

### 14-Agent Roster (v1.3)

**Decision**: Enki is a full SE firm, not just a dev tool. The original Odin 11-agent vision was correct. Five agents were missing from the v1.2 spec.

| Agent | Category | When Spawned |
|---|---|---|
| UI/UX | Conditional | When task touches frontend code |
| DevOps | Standard | Ship phases (qualify/deploy/verify) |
| Docs | Standard | Per sprint + project close |
| Performance | Conditional | When performance requirements in spec |
| Researcher | On-demand | EM spawns when any agent needs codebase investigation |

### Dev Coding Standards (v1.3)

**Decision**: Dev agent mandates SOLID, DRY, and Clean Code principles in its system prompt. These are universal — they go in the agent prompt, not in project CLAUDE.md. Reviewer enforces: violations are bugs, not suggestions.

### Customer Presentation Before Ship (v1.3)

**Decision**: PM presents to customer (human) before project closure. This is the acceptance gate. Customer must sign off before DevOps deploys to production. Missing this step means shipping without acceptance.

### Regression as Practice, Not Test Type (v1.3)

**Decision**: Per ISTQB, regression testing is a PRACTICE (re-running existing tests after changes), not a test level. The v1.0 spec incorrectly defined "regression tests" as purpose-written behavioral contracts separate from unit/integration/E2E.

**Correct model**: Regression suite = curated subset FROM existing unit/integration/E2E tests. QA selects which tests enter the regression suite at sprint completion. Suite only grows (removals require PM authorization).

### DevOps Over Release Engineer (v1.3)

**Decision**: Release Engineer was a fixed deployment model. Users have different CI/CD preferences. DevOps agent reads `.enki/deploy.yaml` for user's preferred pipeline, deploy method, and target. Git + pipelines is default, not only option.

### Docs Throughout Lifecycle (v1.3)

**Decision**: Docs agent writes throughout the project, not just at close. Inline comments during implement, API docs during review, sprint summaries, final user guides at close.

### TDD Flow (Corrected)

QA writes tests from spec. Dev implements from spec (NOT to pass tests). Dev never sees tests. Tests verify, don't drive. EM enforces blind wall by filtering agent context.

### Mail System

Agents communicate via mail in em.db. EM is relay/postman. Agents never talk directly. Thread IS the project memory — resume mid-flight by reading thread history.

### Work Types

Full orchestration for BIG projects only. Bug fixes, refactors, and enhancements have scaled-down workflows matching tier system.

---

## Cross-Pillar Decisions

### Gemini's Role

Single quarterly review covers both Abzu (bead promotion) and Uru (rule evolution). Gemini validates specs, not builds gates. Human approves all changes.

### Tier-Dependent Context Loading

| What | Minimal | Standard | Full |
|---|---|---|---|
| Persona | Short | Full | Full |
| Last session summary | Skip | Yes | Yes |
| Beads | Skip | 3 | 5 + 3 candidates |
| Enforcement gates | Minimal | Full | Full |

### Yggdrasil

Parked for separate design pass. Placeholder interfaces defined in Bridge Spec.

---

## Open Questions (Resolved)

All original open questions have been resolved:

| # | Question | Resolution | Spec |
|---|---|---|---|
| 1 | Agent output templates vs parsing | Strict templates per agent, EM parses structured sections | EM v1.1 |
| 2 | Concurrency within CC | Sequential — waves are dependency ordering | EM v1.1 |
| 3 | SDD path | Replaced by tier system (Minimal/Standard/Full) | EM v1.1 |
| 4 | Bug fix DAGs | Scaled by tier — Minimal has no DAG, Standard has single sprint | EM v1.1 |
| 5 | Mid-execution deviation | Dev notes in output → EM routes to Architect, execution pauses | EM v1.1 |
| 6 | Orchestration → Memory bridge | em.db distilled by Abzu at project completion → staging → Gemini promotes | Abzu v1.1 + Bridge |
| 7 | Session boundaries | Session summaries + mail persistence. Crash recovery via JSONL fallback | Abzu v1.1 |
| 8 | Mail schema | Messages, threads, agent inboxes in em.db. Per-project, ephemeral. | EM v1.1 |

---

## Agent Prompt Library (v1.4)

**Decision**: Separate prompt files with shared base template. agents.py assembles at spawn time.

**Structure**:
```
prompts/
├── _base.md              # Shared: output JSON format, mail protocol, agent identity boilerplate
├── _coding_standards.md  # Shared: SOLID/DRY/Clean Code (Dev + Reviewer reference)
├── pm.md                 # through to researcher.md — one file per agent
└── em.md
```

**Prompt structure** (every agent follows):
1. Identity — who you are
2. You Do — responsibilities and outputs
3. You Don't — boundaries
4. Input — what EM provides
5. Output Format — JSON template
6. Standards — agent-specific (SOLID for Dev, ISTQB for QA, OWASP for InfoSec)
7. Project Context — injected at runtime (CLAUDE.md, Codebase Profile, specs)

Static parts (1-6) in prompt file. Dynamic part (7) assembled by agents.py.

**Versioning**: Files in git. Version header in each prompt (`# v1.2 — added SOLID enforcement`). agents.py logs which prompt version was used per spawn in em.db mail.

**Testing**: Smoke tests for v3 launch (spawn each agent with minimal task, verify JSON output). Golden input/output regression pairs deferred to Phase 2.

## Migration from Current Enki (v1.4)

**Decision**: Option C — migrate all 378 beads to abzu.db staging, NOT directly to wisdom.db. Preferences go direct to wisdom.db. Everything else awaits Gemini review. Follows v3's "only Gemini promotes to wisdom" architecture.

**Category mapping**:

| v1 Type | v3 Category |
|---|---|
| architectural_decision | decision |
| lesson_learned | learning |
| code_pattern | pattern |
| bug_fix | fix |
| tool_preference, workflow_preference | preference |
| project_context | decision (if relevant) or drop |
| personal_note | preference |

**What gets dropped**: 8 empty tables, 31 unused MCP tools, embeddings (1.3% coverage), broken session tables, meaningless retention weights (all 1.0).

**Cutover**: Post-build, one-time script (`scripts/migrate_v1.py`). Run migration → first Gemini review on candidates → verify → retire v1 wisdom.db → enable v3 hooks → first real session.

## Project Onboarding (v1.4)

**Decision**: Three entry points (greenfield, mid-design, brownfield) with Codebase Profile protocol for brownfield.

**First-time user**: Two questions max. No tutorial. Preferences learned from behavior over time.

**User profile**: New `user_profile` table in wisdom.db. Key-value with source tracking (explicit/inferred/codebase). PM, Architect, DevOps all read it.

**Codebase Profile**: Structured JSON output from Researcher. Covers project metadata, structure, conventions, architecture, testing, CI/CD. All downstream agents consume relevant parts.

**Brownfield critical rule**: Researcher runs BEFORE Architect plans. Non-negotiable.

## Docs Agent Removed (v1.4)

**Decision**: Cut Docs as standalone agent. 14 → 13 agents. Documentation responsibilities distributed:

| Need | Now Handled By |
|---|---|
| Inline docs / docstrings | Dev (mandated in coding standards) |
| API documentation | Dev (generated from code annotations) |
| README | Architect (simplified CLAUDE.md) |
| Architecture docs | Architect (Implementation Spec) |
| User guide | PM (derived from Product Spec at close) |
| Changelog | PM (from sprint summaries) |
| Doc quality enforcement | Reviewer (added to review checklist) |

### Decision: Project Onboarding — Three Entry Points (v1.4)

**Why**: Specs assumed greenfield. Real usage includes mid-design (specs exist, no code) and brownfield (existing codebase + feature request). Without this, PM has no protocol for handling existing codebases.

**Decision**: Three entry points detected at PM intake:
- **Greenfield**: Full flow as-is
- **Mid-Design**: PM validates existing artifacts against checklist, shortened intake
- **Brownfield**: Researcher maps codebase FIRST (Codebase Profile), then Architect plans constrained by existing architecture

**Key rule**: Researcher runs BEFORE Architect for brownfield. Non-negotiable.

### Decision: Codebase Profile Protocol (v1.4)

**Why**: Architect cannot plan against an unmapped codebase. Ad-hoc file reading produces inconsistent analysis.

**Decision**: Researcher outputs structured JSON: project metadata, structure, conventions, architecture, testing, CI/CD, relevance scoping. Every downstream agent reads the parts it needs. Read-only, time-bounded (5 min default), stored in em.db mail.

### Decision: User Profile in wisdom.db (v1.4)

**Why**: Returning customers shouldn't re-explain preferences. Currently everything per-project in em.db (ephemeral).

**Decision**: New `user_profile` table in wisdom.db. Key-value with source tracking (explicit/inferred/codebase-derived). Explicit beats inferred. Codebase values promote to global after 3+ consistent projects. PM, Architect, DevOps all read it. First-time user: two questions max, everything else learned from behavior.

### Decision: Sprint-Level Review + Prism (v1.4)

**Why**: Reviewer only ran per-task. Nobody checked cross-task consistency at sprint level or did full-codebase review at project level.

**Decision**: Three review levels:
- **Task**: Reviewer subagent (existing)
- **Sprint**: Reviewer spawned with all sprint files for cross-task consistency (naming, patterns, error handling, API contracts, DRY)
- **Project**: Prism external tool in qualify phase (tree-sitter + static analysis + LLM agents). P0/P1 blocking, P2/P3 tech debt.

### Decision: Agent Prompts as Layer 0 Protected (v1.4)

**Why**: Prompts define agent behavior. CC executing under those prompts should not modify its own instructions. Same principle as Gemini building enforcement layer.

**Decision**: prompts/ directory added to Layer 0 blocklist. 15 files (2 shared templates + 13 agent prompts). Written by Gemini from Agent Prompt Specification. CC reads at runtime, cannot edit. Changes require HITL approval + merge. agents.py assembles: _base.md + agent prompt + project context.

### Decision: Gemini Review as Cron Report (v1.4)

**Why**: No API keys stored in Enki, no external calls from codebase, no attack surface.

**Decision**: Cron generates review package (markdown + JSON) → drops in ~/.enki/reviews/. User takes package to any external LLM (AI Studio, ChatGPT, local model). User brings back structured response → `enki review apply`. Enki generates the question, user gets the answer however they want.

### Decision: Migration from v1/v2 (v1.4)

**Why**: 378 beads in existing wisdom.db. Need to carry over knowledge without polluting v3.

**Decision**: Option C — all beads to abzu.db staging, preferences go directly to wisdom.db (skip review). External LLM reviews staging candidates, promotes valuable ones. One-time script (scripts/migrate_v1.py). Run after v3 fully built, before first real use. v1/v2 and v3 don't conflict (different schemas).

---

## Resolved Questions

| # | Original Question | Resolution |
|---|---|---|
| 2 | Hooks-disabled risk | Installation requirement, not design. `enki doctor` checks hooks. No hooks = no Enki. |
| 3 | Gemini review tooling | Cron report generator. No API calls from Enki. Script exports package → user pastes into external LLM → user runs `enki review apply`. |
| 4 | em.db schema finalization | Schemas are in EM spec Section 20 + Ship spec Section 14. Done. |
| 5 | Migration from current Enki | Option C: all 378 beads to abzu.db staging, preferences direct to wisdom.db, external LLM reviews rest. One-time script: scripts/migrate_v1.py. Cutover after v3 fully built. |
| 6 | CLAUDE.md generation template | Architect generates from Codebase Profile (brownfield) or Implementation Spec (greenfield). |
| 7 | Agent prompt library | Separate files in prompts/ dir + shared base template. Layer 0 protected. Written by Gemini, not CC. Smoke tests for v3, golden regression for Phase 2. agents.py assembles at runtime. |
| 8 | Conditional spawning heuristics | Codebase Profile detects frameworks/file extensions. EM asks human if unsure. |
| 9 | Docs agent scope | Cut. Distributed across Dev (docstrings), Reviewer (doc quality), Architect (README/CLAUDE.md), PM (user guide/changelog). |

## Remaining Open Questions

| # | Question | Context |
|---|---|---|
| 1 | **Yggdrasil design** | PM/EM write interface, relationship to Abzu project registry, Jira+Confluence replacement. Parked — separate discussion. |

---

## Repos Referenced

| Repo | What | Verdict |
|---|---|---|
| Zeroshot | Blind validation, progressive context | **Keep** — patterns for EM |
| Odin | TaskGraph, waves, cyclic recovery | **Keep heavily** — bones of EM |
| iloom | Environment isolation, worktrees | **Park** — Phase 2 |
| mcp_agent_mail (Yegge) | Mail pattern inspiration | **Absorbed** — built into em.db |
| PLTM-Claude | Anti-pattern reference | **Studied** — what not to build |
| Claudest | Simple memory reference | **Studied** — FTS5 approach adopted |
| Nelson (harrymunro) | CC orchestration skill, graduated discipline | **Studied** — nudge tone guidance adopted |

---

## External Review (Gemini) — Changes Applied

Full architectural review by Gemini 2.5 Pro identified 3 critical, 3 significant, and 3 minor issues. All addressed:

| Finding | Severity | Resolution | Spec |
|---|---|---|---|
| SQLite integrity gap (CC can `sqlite3` directly) | Critical | Layer 0.5 DB protection added | Uru v1.1 |
| Compaction death spiral (injection overflow) | Critical | Token budget with recursive distillation | Abzu v1.2 |
| Ghost spawn trigger (PM→EM handoff undefined) | Critical | Nudge 3: unread kickoff mail | Uru v1.1 |
| TDD blind wall is prompt-level only | Significant | Documented as known limitation, Phase 2 for OS isolation | EM v1.2 |
| SQLite concurrency with parallel agents | Significant | WAL mode + busy_timeout mandatory | Bridge v1.1 |
| Gemini review cadence too slow | Significant | On-demand mini-review added | Abzu v1.2 |
| FTS5 ranking bias from project boost | Minor | Minimum score threshold before boosts | Abzu v1.2 |
| JSONL format fragility | Minor | Parser versioning with graceful fallback | Abzu v1.2 |
| Tier auto-detection uses file count | Minor | Impact/complexity heuristics, escalation on low confidence | EM v1.2 |

Additional changes from Nelson analysis:
- Graduated nudge tone (acknowledge good behavior, escalate on repeated ignoring)
- `enki_quick` fast-path for Minimal tier (combines goal + phase in one command)

## Internal Review (v1.3) — Corrections Applied

Architectural review of spec package identified 7 fundamental corrections:

| Finding | Impact | Resolution | Spec |
|---|---|---|---|
| CLAUDE.md missing from orchestration flow | Critical | New Section 6 — CLAUDE.md as first-class artifact | EM v1.3 |
| 5 agents missing from Odin roster | Critical | Added UI/UX, DevOps, Performance, Researcher. Docs cut (distributed). 13 agents total. | EM v1.4 |
| Regression defined as test type (ISTQB wrong) | Significant | Regression is a practice — curated suite from existing tests | Ship v1.1 |
| Release Engineer assumes fixed deploy model | Significant | DevOps agent with user-configurable deployment | EM v1.3 + Ship v1.1 |
| Dev has no mandated coding standards | Significant | SOLID/DRY/Clean Code in agent prompt, Reviewer enforces | EM v1.3 |
| Customer presentation step missing | Moderate | PM presents to customer before ship | EM v1.3 + Ship v1.1 |
| Two test contexts conflated (Enki vs products) | Moderate | Explicit scope statements in both specs | Ship v1.1 + Impl v1.1 |
| No project onboarding for existing codebases | Critical | Three entry points + Codebase Profile protocol | EM v1.4 |
| No user profile persistence | Significant | user_profile table in wisdom.db | EM v1.4 |
| No agent prompt management | Significant | Separate prompt files + shared base template | EM v1.4 |

---

## Spec Inventory

| Spec | Version | Location |
|---|---|---|
| Abzu Memory Spec | v1.2 | abzu-memory-spec.md |
| Uru Gates Spec | v1.1 | uru-gates-spec.md |
| EM Orchestrator Spec | v1.4 | em-orchestrator-spec-v1.4.md |
| Bridge Spec | v1.1 | enki-v3-bridge-spec.md |
| Ship & Quality Spec | v1.2 | enki-v3-ship-quality-spec-v1.2.md |
| Implementation Spec | v1.2 | enki-v3-implementation-spec-v1.2.md |
| Agent Prompt Spec | v1.0 | agent-prompt-spec.md |
| Design Decisions (this doc) | — | enki-v3-design-decisions.md |

---

## Maintenance Updates

### 2026-02-24: Uru hook stdin JSON parsing hardening

- Scope: `src/enki/gates/uru.py` CLI hook entrypoint input parsing only.
- Change: replaced direct `json.loads(sys.stdin.read())` with empty-safe parsing:
  `raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""` and `hook_input = json.loads(raw) if raw else {}`.
- Reason: avoid `JSONDecodeError` when hook stdin is empty or whitespace-only.
- Non-change guarantee: no gate logic, blocking behavior, or decision outputs were modified.
- Validation: `echo '' | PYTHONPATH=src python3 -m enki.gates.uru --hook pre-tool-use` returns `{"decision": "allow"}` with exit `0`.

---

## How to Use This Document

**For new chats**: Read this first. It tells you every decision made and why. Don't re-explain or re-debate decided items unless Partha explicitly reopens them.

**For continuing work**: Yggdrasil is the only remaining open question. Agent prompts are with Gemini. Everything else is locked.

**For Partha**: When you start a new chat, reference this document. The reviewer will have memory + this document and should be able to continue without re-explanation.

**For implementation**: Each pillar spec is the source of truth for its domain. This document is the summary. If there's a conflict, the pillar spec wins.
