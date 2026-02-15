# Abzu Memory Spec — Enki v3 Pillar 1

> **Version**: 1.2
> **Date**: 2025-02-13
> **Status**: Final — All design decisions locked
> **Scope**: This is the Memory pillar (Pillar 1) of Enki v3. Orchestration (Pillar 3) and Gates (Pillar 2) have separate specs.
> **Audience**: Architect reads this and knows what to build. Dev reads the Implementation Spec derived from this.
> **Correction**: This spec supersedes the EM Orchestrator Spec on spawn authority — EM does not spawn PM. Enki spawns both as peer departments.
> **v1.1 Changes**: Corrected Uru/Gates bridge — Uru reads em.db/abzu.db for workflow state, not wisdom.db for patterns.
> **v1.2 Changes**: Added injection token budget (compaction death spiral prevention), on-demand Gemini mini-review, FTS5 minimum score threshold, JSONL parser versioning.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture Principles](#2-architecture-principles)
3. [Three-Database Model](#3-three-database-model)
4. [Abzu as Infrastructure](#4-abzu-as-infrastructure)
5. [Bead Design](#5-bead-design)
6. [Ingestion Paths](#6-ingestion-paths)
7. [Session Lifecycle](#7-session-lifecycle)
8. [Retrieval](#8-retrieval)
9. [Retention and Decay](#9-retention-and-decay)
10. [Deduplication](#10-deduplication)
11. [Gemini Review](#11-gemini-review)
12. [Tier-Dependent Context Loading](#12-tier-dependent-context-loading)
13. [Bridge Interfaces](#13-bridge-interfaces)
14. [Spawn Authority Correction](#14-spawn-authority-correction)
15. [MCP Tool Surface](#15-mcp-tool-surface)
16. [Data Schemas](#16-data-schemas)
17. [Bill of Materials](#17-bill-of-materials)
18. [Anti-Patterns](#18-anti-patterns)
19. [Glossary](#19-glossary)

---

## 1. Overview

Abzu is Enki v3's memory system — named for the primordial freshwater ocean beneath the earth in Sumerian mythology, the domain from which Enki draws knowledge.

Abzu is **infrastructure, not an agent**. It is a library/service layer that hooks, agents, and Enki itself call into. Always available, never spawned. It manages three databases, controls what knowledge enters and exits, and provides the memory foundation that all other Enki components depend on.

Without Abzu, EM can't resume projects, PM can't access historical context, Uru can't verify workflow state, and sessions lose continuity on compaction.

### What Abzu Solves

| Problem | How |
|---|---|
| Knowledge lost on compaction | Pre-compact captures conversational + operational state, post-compact re-injects it |
| Session continuity | Session summaries accumulate across compactions, final summary carries to next session |
| Cross-project knowledge | Single wisdom.db searched across all projects with project-aware ranking |
| Fix/error recall | Error → fix pairs stored as beads, searchable when same issue recurs |
| Work style memory | Preferences stored directly, survive indefinitely |
| Bead quality control | All non-preference knowledge staged, Gemini reviews before permanent storage |
| Dead infrastructure | 4 MCP tools replace 35. Hooks handle automation. No dead tables. |

---

## 2. Architecture Principles

**Abzu is a library, not an agent.** No spawn, no prompt, no LLM reasoning. Functions called by hooks, MCP tools, and other Enki components. Deterministic where possible.

**JSONL is the raw archive.** Claude Code's JSONL transcript files are the source of truth for everything that happened in a conversation. Abzu reads from them, never writes to them. They survive compaction and session boundaries.

**Two-gate quality control.** Non-preference beads go to staging (abzu.db). Only Gemini-approved beads reach permanent storage (wisdom.db). This prevents noise accumulation and removes CC self-assessment as the final judge of knowledge quality.

**Hooks drive automation.** Session start, pre-compact, post-compact, session end — each hook calls Abzu functions. No manual tool calls needed for core memory operations.

**Minimal tool surface.** Four MCP tools. Everything else is internal. The previous 35-tool surface proved that manual-only tools don't get used.

**Fail safe, not fail open.** If pre-compact extraction fails, session summary is incomplete but session continues. If bead staging fails, JSONL still has the data. If Gemini review is late, candidates accumulate but nothing is lost.

---

## 3. Three-Database Model

Abzu manages three databases with clear ownership boundaries:

| Database | Owner | Location | Lifespan | Purpose |
|---|---|---|---|---|
| **wisdom.db** | Abzu | `~/.enki/wisdom.db` | Permanent | Gemini-approved beads, FTS5 index, project registry |
| **abzu.db** | Abzu | `~/.enki/abzu.db` | Rolling | Session summaries, bead candidates (staging), extraction log |
| **em.db** | EM | `~/.enki/projects/{name}/em.db` | Per-project | Mail, threads, task_state, sprint_state, bugs, pm_decisions |

### Ownership Rules

- **wisdom.db** — Only two write paths: preference beads (direct) and Gemini-promoted beads. No other component writes here.
- **abzu.db** — Abzu writes session summaries and staged candidates. Gemini reads candidates for review. EM/PM do not write here.
- **em.db** — EM and PM read/write mail and project state. Abzu reads em.db at project completion for distillation. Per-project, disposable.

### Why Three, Not One

One database mixes operational churn (mail, task state) with permanent knowledge (beads). em.db churns heavily during active projects and gets deleted after 30 days. wisdom.db is append-mostly and permanent. Mixing them means either the permanent database gets polluted with disposable data, or the operational database carries weight it doesn't need.

---

## 4. Abzu as Infrastructure

Abzu is a Python library exposing functions. Not an agent, not a service, not an MCP server (though some functions are exposed as MCP tools).

### Interface Model

```python
# Hooks call Abzu
abzu.inject_session_start(project, goal, tier)
abzu.update_pre_compact_summary(session_id, operational_state, conversational_state)
abzu.inject_post_compact(session_id, tier)
abzu.finalize_session(session_id, project)

# MCP tools call Abzu
abzu.remember(content, category, project)
abzu.recall(query, scope, project)
abzu.star(bead_id)
abzu.status()

# EM/PM call Abzu
abzu.recall(query, scope, project)  # read-only for PM
abzu.remember(content, category, project)  # EM writes candidates

# Gemini review calls Abzu
abzu.get_staged_candidates(project=None)
abzu.promote_candidate(candidate_id, consolidated_content=None)
abzu.discard_candidate(candidate_id, reason)
abzu.consolidate_beads(bead_ids, merged_content)
abzu.flag_for_deletion(bead_id, reason)
```

### What Abzu Does NOT Do

- Does not make decisions about what's worth remembering (that's CC distillation or Gemini review)
- Does not spawn agents or route mail (that's EM)
- Does not enforce gates or intercept tool calls (that's Pillar 2)
- Does not manage JSONL files (that's Claude Code)
- Does not run LLM inference (extraction prompts are CC's or Gemini's job)

---

## 5. Bead Design

### Categories

Five categories. Each earns its place based on actual usage and Abzu's requirements:

| Category | What | Example | Decay |
|---|---|---|---|
| `decision` | Architectural/design choice with reasoning | "Used JWT over sessions because stateless" | Standard |
| `learning` | Something discovered through experience | "Refresh token race condition under concurrent requests" | Standard |
| `pattern` | Reusable approach that worked (or didn't) | "Auth middleware: validate → refresh → proceed" | Slower |
| `fix` | Error → solution pair | "FTS5 not in CLI sqlite3, use Python sqlite3 module" | Standard |
| `preference` | Work style, tool choice, process preference | "Always use strict TypeScript" | Never |

### What Changed from Current Enki

| Current | Abzu |
|---|---|
| 8 `type` values, 4 never used | 5 `category` values, all used |
| 4 `kind` values, 2 never used | `kind` field eliminated |
| `type` and `kind` redundant overlap | Single `category` field |
| Embeddings (1.3% coverage) | Cut. FTS5 only. Add back only if proven insufficient. |
| `access_log` table (bulk-stamped garbage) | `last_accessed` column on bead row itself |
| Content hash for dedup | Kept — works, 100% coverage |

### Bead Lifecycle

```
Knowledge created in conversation
    ↓
CC calls enki_remember OR session-end extraction
    ↓
Is it a preference?
    YES → wisdom.db direct (permanent, no decay)
    NO  → abzu.db staging (candidate)
    ↓
Gemini monthly/quarterly review
    ↓
Promote → wisdom.db (permanent, subject to decay)
Discard → deleted from staging
Consolidate → merged with existing beads in wisdom.db
```

### Scoping

Beads are scoped by project:

| Scope | `project` field | Search behavior |
|---|---|---|
| Global | `NULL` | Always included in search results |
| Project-specific | Project name | Included when searching that project |

Default search returns current project + global. Cross-project search is explicit.

### Search Ranking

```
Score = fts5_relevance
    × (1.5 if current_project)
    × (1.2 if global)
    × (1.0 if other_project)
    × weight
    × (0.8 if staged_candidate)  -- candidates ranked below approved beads
```

---

## 6. Ingestion Paths

### Path Overview

| Path | What | When | Destination |
|---|---|---|---|
| **enki_remember (preference)** | CC detects user preference | During session | wisdom.db direct |
| **enki_remember (non-preference)** | CC recognizes decision/learning/pattern/fix | During session | abzu.db staging |
| **Session-end extraction** | Heuristic first, then CC distillation | Session end hook | abzu.db staging |
| **Project completion** | Heuristic + CC distill from em.db | Project closes | abzu.db staging |
| **Gemini review** | External LLM reviews staged candidates | Monthly/quarterly | Promotes to wisdom.db |

### Session-End Extraction Detail

**Step 1: Heuristic extraction** (deterministic, no LLM)
- Reads JSONL for current session
- Regex patterns extract: decisions ("I'll...", "Changed...", "Decided..."), errors/exceptions, files modified, task completions
- Produces structured list of candidate beads

**JSONL Parser Versioning (v1.1):** JSONL files are Claude Code's internal format. If Anthropic updates the schema (e.g., nesting tool outputs differently, changing event types), heuristic extraction breaks silently. `extraction.py` must:
- Declare a `JSONL_FORMAT_VERSION` constant
- Check JSONL structure on first read (expected keys, nesting patterns)
- If structure doesn't match expected version, log warning and skip heuristic extraction (fall back to CC distillation only)
- Never silently extract garbage from an unrecognized format

**Step 2: CC distillation** (LLM-assisted)
- CC receives: heuristic output + accumulated session summaries + existing beads for project
- Structured prompt: "What decisions were made? What was learned? What patterns emerged? What errors were fixed? Don't extract what's already in existing beads."
- CC enriches heuristic output, fills gaps, catches nuance heuristics miss
- CC may also auto-call `enki_remember` during conversation when it recognizes significant moments (this is part of distillation behavior)

**Step 3: Store**
- All candidates → abzu.db staging
- Preferences → wisdom.db direct
- Dedup check on each before storing

### Project Completion Extraction

When a project completes:
1. Abzu reads em.db mail threads for the project
2. Heuristic extraction on mail content (decisions, bug patterns, architectural choices)
3. CC distillation with project context
4. Candidates → abzu.db staging
5. em.db kept 30 days → deleted

### Gemini Periodic Review

Quarterly full review + on-demand mini-review (see Section 11). During periodic review, Gemini:
1. Reads all staged candidates from abzu.db
2. Reads existing wisdom.db beads for context
3. Filters: worth keeping permanently?
4. Extracts: patterns, learnings, decisions with reasoning for Enki evolution
5. Consolidates: merges related candidates into richer beads
6. Promotes: approved beads → wisdom.db
7. Discards: noise, duplicates, low-value items deleted from staging
8. Flags: existing wisdom.db beads for deletion if stale/contradicted

---

## 7. Session Lifecycle

### Session Start

Hook calls `abzu.inject_session_start(project, goal, tier)`:

1. Load persona identity
2. Load phase / tier / goal from `.enki/` files
3. Load last session's final summary from abzu.db (`is_final = 1`, current project)
4. Load relevant beads from wisdom.db (FTS5 on goal/project)
5. Load relevant candidates from abzu.db staging
6. Load enforcement gates
7. Write session ID to `.enki/SESSION_ID`

Amount loaded is tier-dependent (see Section 12).

### Pre-Compact

Hook calls `abzu.update_pre_compact_summary(session_id, ...)`:

1. **Heuristic**: Extract operational state from JSONL (files modified, tasks, errors)
2. **CC**: Write conversational state — what are we discussing, ideas in progress, direction, decisions made vs open, user's last ask
3. Both stored as pre-compact summary row in abzu.db
4. Summaries **accumulate** across compactions (append, not overwrite)

```
Pre-compact #1 → summary with sequence=1
Pre-compact #2 → summary with sequence=2 (builds on #1)
Pre-compact #3 → summary with sequence=3 (builds on #1 + #2)
```

### Post-Compact

Hook calls `abzu.inject_post_compact(session_id, tier)`:

1. Load persona identity
2. Load phase / tier / goal
3. Load accumulated pre-compact summaries for current session (subject to injection budget)
4. Load enforcement gates

Post-compact carries the intellectual thread — not just "you were editing auth.py" but "you were editing auth.py because we decided JWT needs refresh token rotation, and we were debating DB vs Redis for storage, leaning DB."

**Post-compact does NOT re-inject beads.** The conversational state carries what was relevant. If CC needs more, it calls `enki_recall` explicitly.

### Injection Budget (v1.1 — Compaction Death Spiral Prevention)

**Added based on external review.** In long, complex sessions (Full tier), accumulated pre-compact summaries could exceed the compaction threshold itself, creating a death spiral: compact → re-inject → overflow → compact again.

**Token budgets for post-compact injection:**

| Component | Minimal | Standard | Full |
|---|---|---|---|
| Persona | ~200 tokens | ~500 tokens | ~500 tokens |
| Phase/tier/goal | ~100 | ~100 | ~100 |
| Accumulated summaries | ~1,500 | ~4,000 | ~8,000 |
| Enforcement context | ~200 | ~400 | ~400 |
| **Total budget** | **~2,000** | **~5,000** | **~9,000** |

**When summaries exceed budget:**

1. Keep the most recent pre-compact summary in full
2. Collapse all earlier summaries into a single condensed narrative ("Summary of summaries")
3. If condensed still exceeds budget, keep only the most recent summary + key decisions list
4. Log the collapse event to abzu.db (for debugging continuity loss)

The condensation is heuristic (extract decisions, current direction, active files), not CC distillation — it runs in the hook before CC has context.

### Session End

Hook calls `abzu.finalize_session(session_id, project)`:

1. Read all accumulated pre-compact summaries (v1, v2, v3...)
2. Heuristic extraction on any JSONL content since last pre-compact
3. CC distillation: receives accumulated summaries + final session state
4. CC reconciles: dedupes across summaries, produces final clean summary
5. Final session summary written to abzu.db (`is_final = 1`)
6. Bead candidates extracted → abzu.db staging
7. Pre-compact snapshot rows for this session can be cleaned up
8. Decay pass runs on wisdom.db

### Crash Recovery

If session crashes (no clean end):
- Next session start finds no final summary for previous session
- Falls back to most recent pre-compact summary if available
- Falls back to heuristic JSONL extraction if no pre-compact summary exists
- Degraded but not broken — JSONL always has the data

---

## 8. Retrieval

### enki_recall

Single search tool with scope parameter:

```
enki_recall("auth pattern")                     → current project + global
enki_recall("auth pattern", scope="all")        → all projects
enki_recall("auth pattern", project="Odin")     → Odin specifically
```

**Search path:**
1. FTS5 on wisdom.db beads (permanent, approved)
2. FTS5 on abzu.db staging (candidates, read-only)
3. Results merged, wisdom.db ranked higher than staging
4. Ranked by: relevance × project_boost × weight × source_boost

**Ranking formula:**
```
Score = fts5_relevance × project_boost × weight × source_boost
```

Where:
- `project_boost`: 1.5 (current project), 1.2 (global), 1.0 (other project)
- `source_boost`: 1.0 (wisdom.db), 0.8 (staging candidate)
- `weight`: decay weight from retention system (0.1 to 1.0)

**Minimum score threshold (v1.1):** Results below a relevance floor are filtered out before project boosts are applied. This prevents weak FTS5 matches from being surfaced just because they have a 1.5× project boost. Threshold is configurable (default: 0.3 normalized FTS5 score). This avoids the failure mode where a poor match from the current project outranks a strong global match.

**On recall**, `last_accessed` is updated on the bead row. This drives decay scoring — recalled beads stay alive.

### Who Calls Recall

| Consumer | Access | Purpose |
|---|---|---|
| CC (during session) | Read wisdom.db + staging | Context for current work |
| EM | Read wisdom.db + staging | Context injection for execution agents |
| PM | Read wisdom.db + staging | Historical context for debate, cross-project awareness |
| Uru | Read em.db + abzu.db (workflow state only) | Binary gate checks: goal exists, spec approved, phase correct |
| Gemini | Read both + write wisdom.db | Review authority |

### Execution Agents and Abzu

Dev, QA, Validator, Reviewer, InfoSec do NOT access Abzu directly. EM injects relevant beads into their prompts when spawning them. They are pure subagents — receive context, do work, return output.

---

## 9. Retention and Decay

### wisdom.db Beads

Decay is driven by **actual recall usage**, not just age. A 2-year-old bead that gets recalled regularly stays hot. A 1-month-old bead nobody searches for fades.

| Condition | Weight |
|---|---|
| Recalled in last 30 days | 1.0 |
| Not recalled in 90 days | 0.5 |
| Not recalled in 180 days | 0.2 |
| Not recalled in 365 days | 0.1 |
| Starred | Always 1.0 |
| Category = `preference` | Always 1.0 |

**Decay reduces search ranking but never deletes.** Only Gemini can flag a bead for deletion. This prevents silent knowledge loss.

**Deletion flow:**
```
Gemini reviews bead → flags for deletion (with reason)
    → Flag stored on bead
    → Next maintenance pass: deletes flagged beads
```

No `enki_forget` tool. No automatic purge. No decay-based deletion.

**Decay trigger:** Session-end hook runs decay pass. Automatic, not a manual maintenance command.

### abzu.db Session Summaries

| Rule | Action |
|---|---|
| Last 5 final summaries per project | Kept |
| Older than 5 | Deleted |
| Pre-compact snapshots | Deleted after final summary is reconciled at session end |

### abzu.db Staged Candidates

| Rule | Action |
|---|---|
| Awaiting Gemini review | Kept indefinitely |
| Gemini promotes | Moved to wisdom.db, deleted from staging |
| Gemini discards | Deleted from staging |

### em.db

| Rule | Action |
|---|---|
| Project active | Full retention |
| Project complete | Abzu distills candidates, em.db kept 30 days |
| 30 days post-completion | em.db deleted |

---

## 10. Deduplication

One rule: content hash + FTS keyword check at write time.

```python
def store_candidate(content, category, project):
    # Exact duplicate?
    if hash_exists(content_hash(content)):
        return SKIP

    # Similar enough to existing?
    matches = fts5_search(extract_keywords(content), limit=3)
    if any(match.score > 0.85 for match in matches):
        return SUPERSEDE_OLD

    return STORE
```

Three outcomes:
- **Hash match**: Skip — exact duplicate
- **High FTS similarity (>0.85)**: Supersede — new replaces old (richer version)
- **No match**: Store normally

Anything that slips through, Gemini catches during periodic review. No separate dedup system, no semantic similarity scoring, no consolidation pass.

---

## 11. Gemini Review

### Purpose

Gemini is the external quality gate. CC extracts candidates, Gemini decides what's permanent. This prevents noise accumulation and removes CC self-assessment as the final authority on knowledge quality.

### Process

**Full review** — quarterly (configurable):

1. **Input**: All staged candidates from abzu.db + existing wisdom.db beads for context
2. **Gemini evaluates each candidate**:
   - Worth keeping permanently?
   - Related to existing beads? (consolidation opportunity)
   - Contradicts existing beads? (flag old one)
   - Is this a pattern, decision, learning, or fix?
3. **Actions**:
   - **Promote**: Candidate → wisdom.db as permanent bead
   - **Consolidate**: Merge candidate with existing bead(s) into richer version
   - **Discard**: Delete from staging (noise, too granular, irrelevant)
   - **Flag existing**: Mark wisdom.db bead for deletion if stale/contradicted
4. **Output**: Review report — what was promoted, discarded, consolidated, flagged

**On-demand mini-review** — user-triggered (v1.1):

The user can trigger a focused Gemini review at any time for a specific project. Mini-review scope:
- Only staged candidates for the specified project
- Only wisdom.db beads tagged to that project (for context/dedup)
- Same promote/discard/consolidate/flag actions as full review
- Does NOT review Uru enforcement logs or rule proposals (that's full review only)

**Why:** Quarterly cadence is too slow for a single developer shipping weekly. Knowledge gained Monday shouldn't wait 90 days to reach permanent status. Mini-review lets the user graduate candidates after a sprint ships or a project completes.

**Trigger:** User runs `enki_review --project {name}` or similar CLI command. Not triggered by CC — human decides when knowledge is ready for promotion.

### What Gemini Looks For

- Patterns across projects ("this auth approach worked in Odin AND TestForge")
- Evolving knowledge ("this decision was revised three times — keep only the latest")
- Gaps ("lots of candidates about deployment but no permanent beads — promote")
- Contradictions ("bead says use sessions, newer candidate says use JWT — flag old")
- Enki evolution material ("learnings about CC behavior, workflow improvements")

### Deletion Authority

Only Gemini can delete from wisdom.db. The flow:

```
Gemini flags bead for deletion
    → gemini_flagged = 1, flag_reason = "..."
    → Next session-end maintenance pass
    → Flagged beads deleted
```

No manual delete. No automatic purge. No decay-based deletion.

---

## 12. Tier-Dependent Context Loading

### Session Start

| Injection | Minimal | Standard | Full |
|---|---|---|---|
| Persona | Short (2-3 lines) | Full | Full |
| Phase / Tier / Goal | Yes | Yes | Yes |
| Last session summary | Skip | Yes | Yes |
| Relevant beads | Skip | 3 beads | 5 beads + 3 candidates |
| Enforcement gates | Minimal (1 line) | Full | Full |

### Post-Compact

| Injection | Minimal | Standard | Full |
|---|---|---|---|
| Persona | Short | Full | Full |
| Phase / Tier / Goal | Yes | Yes | Yes |
| Conversational state | Last pre-compact only | All accumulated | All accumulated |
| Enforcement gates | Minimal | Full | Full |

Minimal tier keeps tokens cheap. Full tier loads everything. Standard is the middle ground.

---

## 13. Bridge Interfaces

### Direct Abzu Consumers

| Consumer | Reads | Writes |
|---|---|---|
| **PM** | wisdom.db beads, abzu.db staging | Nothing — PM's outputs live in em.db and Yggdrasil |
| **EM** | wisdom.db beads, abzu.db staging, session summaries | Bead candidates (via `enki_remember`), session summaries |
| **Uru** | em.db (goal, phase, spec approval), abzu.db (session state) | Nothing — Uru reads workflow state for gate checks only |
| **Gemini** | wisdom.db beads, abzu.db staged candidates, uru.db logs | Promotes/discards/consolidates/flags beads; approves/rejects rule proposals |
| **Hooks** | wisdom.db beads, abzu.db summaries | Session summaries, pre-compact snapshots |

### Indirect Consumers (Through EM)

Dev, QA, Validator, Reviewer, InfoSec, Architect, DBA — all receive Abzu context through EM's prompt injection. They never call Abzu functions directly.

### Abzu → em.db

At project completion, Abzu reads em.db:
- Mail threads (decisions, discussions, escalations)
- Bug lifecycle (error → fix patterns)
- PM decisions (what was proposed, what human decided)
- Distills candidates → abzu.db staging

### Data Flow Diagram

```
                    ┌─────────────┐
                    │   Gemini    │
                    │  (review)   │
                    └──────┬──────┘
                    promote│discard
                           │
┌──────────┐     ┌─────────▼─────────┐     ┌──────────────┐
│  em.db   │────▶│     abzu.db       │     │  wisdom.db   │
│ (project)│     │  (staging +       │────▶│  (permanent) │
└──────────┘     │   summaries)      │     └──────┬───────┘
  distill        └───────────────────┘            │
                         ▲                        │
                         │                        │
              ┌──────────┴──────────┐             │
              │      Hooks         │              │
              │  (session start,   │◀─────────────┘
              │   pre/post compact,│    inject beads
              │   session end)     │
              └────────────────────┘
                         ▲
                         │
              ┌──────────┴──────────┐
              │    CC / Agents     │
              │  enki_remember     │
              │  enki_recall       │
              └────────────────────┘

Preferences bypass staging:
  CC enki_remember(preference) ──────────▶ wisdom.db direct
```

---

## 14. Spawn Authority Correction

**This section corrects the EM Orchestrator Spec.**

The EM spec states "EM spawns PM." This is wrong. PM and EM are peer departments within Enki. Neither spawns or controls the other.

### Correct Model

| Agent | Spawned by | Role |
|---|---|---|
| **PM** | Enki | Project owner — intake, specs, debate, status, closure |
| **EM** | Enki | Execution manager — DAG, agent spawning, mail routing |
| **Dev, QA, Validator, Reviewer, InfoSec** | EM | Execution agents |
| **Architect, DBA** | Enki (at PM's request via mail) | Planning agents |

### Relationship

PM and EM are separate departments in the same organization (Enki). They communicate through mail in em.db. PM hands off to EM via kickoff mail. EM sends status to PM via mail. Neither reports to the other.

```
User has idea
    → Enki spawns PM (intake)
    → PM does Q&A, writes Product Spec, runs debate
    → PM sends kickoff mail to EM
    → Enki spawns EM (execution)
    → EM builds DAG, spawns execution agents
    → EM sends status mail to PM
    → PM sends status to User
```

On blockers, change requests, sprint completions — Enki spawns PM independently. PM is not waiting inside EM's process.

---

## 15. MCP Tool Surface

Four tools. Down from 35.

| Tool | What | Writes to |
|---|---|---|
| `enki_remember` | Store a bead — preference goes to wisdom.db, all else to abzu.db staging | wisdom.db or abzu.db |
| `enki_recall` | Search wisdom.db + abzu.db staging with scope parameter | Updates `last_accessed` on returned beads |
| `enki_star` | Mark bead as permanent — weight always 1.0, never decays | wisdom.db |
| `enki_status` | Health check — bead count, staging count, last Gemini review, decay stats | Read-only |

### What Happened to the Other 31 Tools

| Old Tool | Disposition |
|---|---|
| `enki_forget` | Eliminated — only Gemini can delete |
| `enki_goal`, `enki_phase` | Remain as .enki/ file operations, not Abzu tools |
| `enki_debate`, `enki_plan`, `enki_approve` | PM workflow — EM spec, not Abzu |
| `enki_decompose`, `enki_orchestrate`, `enki_task` | EM orchestration — EM spec |
| `enki_bug` | EM bug lifecycle — em.db |
| `enki_log` | Replaced by session summaries |
| `enki_maintain` | Absorbed into session-end hook (automatic) |
| `enki_submit/spawn/record_validation` | EM validation workflow |
| `enki_retry_rejected_task`, `enki_validation_status` | EM task management |
| `enki_worktree_*` (4 tools) | Separate worktree concern, not memory |
| `enki_send/get_message`, `enki_claim/release_file` | EM mail system in em.db |
| `enki_triage`, `enki_handover`, `enki_escalate` | EM/PM workflow |
| `enki_reflect` | Replaced by session-end distillation |
| `enki_feedback_loop` | Moved to Gates (Pillar 2) |
| `enki_simplify` | Standalone utility, not memory |

---

## 16. Data Schemas

### wisdom.db

**Beads:**

```sql
CREATE TABLE beads (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,                     -- short description for search results
    category TEXT NOT NULL CHECK (category IN (
        'decision', 'learning', 'pattern', 'fix', 'preference'
    )),
    project TEXT,                     -- NULL for global scope
    weight REAL DEFAULT 1.0,
    starred INTEGER DEFAULT 0,
    content_hash TEXT NOT NULL,
    tags TEXT,                        -- JSON array (optional enrichment)
    context TEXT,                     -- why this bead exists / source
    superseded_by TEXT,
    gemini_flagged INTEGER DEFAULT 0, -- 1 = Gemini marked for deletion
    flag_reason TEXT,                 -- why Gemini flagged it
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    promoted_at TIMESTAMP,           -- when Gemini promoted from staging
    FOREIGN KEY (superseded_by) REFERENCES beads(id),
    FOREIGN KEY (project) REFERENCES projects(name)
);

CREATE INDEX idx_beads_project ON beads(project);
CREATE INDEX idx_beads_category ON beads(category);
CREATE INDEX idx_beads_weight ON beads(weight);
CREATE INDEX idx_beads_hash ON beads(content_hash);
CREATE INDEX idx_beads_flagged ON beads(gemini_flagged);
```

**FTS5 Index:**

```sql
CREATE VIRTUAL TABLE beads_fts USING fts5(
    content,
    summary,
    tags,
    content='beads',
    content_rowid='rowid'
);

CREATE TRIGGER beads_ai AFTER INSERT ON beads BEGIN
    INSERT INTO beads_fts(rowid, content, summary, tags)
    VALUES (new.rowid, new.content, new.summary, new.tags);
END;

CREATE TRIGGER beads_ad AFTER DELETE ON beads BEGIN
    INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
    VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
END;

CREATE TRIGGER beads_au AFTER UPDATE ON beads BEGIN
    INSERT INTO beads_fts(beads_fts, rowid, content, summary, tags)
    VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
    INSERT INTO beads_fts(rowid, content, summary, tags)
    VALUES (new.rowid, new.content, new.summary, new.tags);
END;
```

**Projects:**

```sql
CREATE TABLE projects (
    name TEXT PRIMARY KEY,
    path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP              -- updated on bead write or recall
);
```

**User Profile** (added v1.4 — persistent user preferences across all projects):

```sql
CREATE TABLE user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('explicit', 'inferred', 'codebase')),
    confidence REAL DEFAULT 1.0,
    project_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Write paths: PM writes during intake (explicit), PM learning loop writes (inferred), Researcher writes after Codebase Profile (codebase). Read paths: PM at intake, Architect at CLAUDE.md generation, DevOps at deploy.

### abzu.db

**Session Summaries:**

```sql
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT,
    sequence INTEGER DEFAULT 0,        -- 0=session start load, 1+=pre-compact snapshots
    goal TEXT,
    phase TEXT,
    operational_state TEXT,            -- files, tasks, errors (heuristic)
    conversational_state TEXT,         -- ideas, direction, reasoning (CC)
    is_final INTEGER DEFAULT 0,       -- 1 = session-end reconciled version
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_summaries_session ON session_summaries(session_id);
CREATE INDEX idx_summaries_project ON session_summaries(project, is_final);
```

**Bead Candidates (Staging):**

```sql
CREATE TABLE bead_candidates (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,
    category TEXT NOT NULL CHECK (category IN (
        'decision', 'learning', 'pattern', 'fix'
    )),                                -- no 'preference' — those go direct to wisdom.db
    project TEXT,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,              -- 'session_end', 'project_completion', 'manual', 'em_distill'
    session_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_candidates_project ON bead_candidates(project);
CREATE INDEX idx_candidates_hash ON bead_candidates(content_hash);
```

**FTS5 on Candidates:**

```sql
CREATE VIRTUAL TABLE candidates_fts USING fts5(
    content,
    summary,
    content='bead_candidates',
    content_rowid='rowid'
);

CREATE TRIGGER candidates_ai AFTER INSERT ON bead_candidates BEGIN
    INSERT INTO candidates_fts(rowid, content, summary)
    VALUES (new.rowid, new.content, new.summary);
END;

CREATE TRIGGER candidates_ad AFTER DELETE ON bead_candidates BEGIN
    INSERT INTO candidates_fts(candidates_fts, rowid, content, summary)
    VALUES ('delete', old.rowid, old.content, old.summary);
END;
```

**Extraction Log:**

```sql
CREATE TABLE extraction_log (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    jsonl_path TEXT,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    candidates_created INTEGER DEFAULT 0,
    method TEXT NOT NULL               -- 'heuristic', 'cc_distill', 'project_completion'
);

CREATE INDEX idx_extraction_session ON extraction_log(session_id);
```

### em.db (per project — defined in EM spec, referenced here)

```sql
-- Owned by EM, read by Abzu at project completion
-- Tables: mail_messages, mail_threads, task_state, sprint_state, bugs, pm_decisions
-- See EM Orchestrator Spec Section 18 for full schemas
```

### Table Count

| Database | Tables | FTS | Total |
|---|---|---|---|
| wisdom.db | 2 (beads, projects) | 1 (beads_fts) | 3 |
| abzu.db | 3 (session_summaries, bead_candidates, extraction_log) | 1 (candidates_fts) | 4 |
| **Total Abzu-owned** | **5** | **2** | **7** |

Down from 17 tables in current Enki, 8 of which were permanently empty.

---

## 17. Bill of Materials

### Estimated Module Breakdown

| File | ~Lines | What | Source |
|---|---|---|---|
| `memory/abzu.py` | ~500 | Core Abzu library — public API, orchestrates all operations | New |
| `memory/beads.py` | ~300 | Bead CRUD, dedup, FTS5 search, ranking | Rewritten from current |
| `memory/staging.py` | ~200 | Candidate staging, promotion, discard | New |
| `memory/sessions.py` | ~300 | Session summary lifecycle — create, accumulate, finalize, inject | New |
| `memory/extraction.py` | ~400 | Heuristic extraction from JSONL, pattern matching | Evolved from transcript.py |
| `memory/retention.py` | ~150 | Decay scoring, maintenance pass, Gemini flag processing | Simplified from current |
| `memory/schemas.py` | ~150 | SQLite table definitions, migrations for wisdom.db + abzu.db | New |
| `memory/gemini.py` | ~300 | Gemini review interface — prepare candidates, process results | New |

**Total: ~2,300 lines** (estimated)

### Compared to Current

| Current Enki Memory | Lines | Abzu | Lines |
|---|---|---|---|
| beads.py | ~400 | beads.py | ~300 |
| search.py | ~200 | Absorbed into beads.py (FTS5 only, no hybrid) | — |
| embeddings.py | ~300 | Cut | — |
| retention.py | ~250 | retention.py | ~150 |
| summarization.py | ~200 | Replaced by sessions.py | — |
| context.py | ~200 | Absorbed into abzu.py | — |
| offline.py | ~300 | Cut (remote/local split is deployment) | — |
| client.py | ~400 | Cut | — |
| **Total** | **~2,250** | **Total** | **~2,300** |

Similar line count but every line earns its place. No dead infrastructure.

---

## 18. Anti-Patterns

Lessons from the diagnostic and from PLTM-Claude analysis. What Abzu explicitly avoids:

### From Current Enki

| Anti-Pattern | What Went Wrong | Abzu's Answer |
|---|---|---|
| 17 tables, 8 empty | Built infrastructure nobody used | 7 tables, all active |
| 35 MCP tools, 31 manual-only | Manual tools don't get called | 4 tools + hooks |
| Embeddings at 1.3% coverage | Pipeline nobody maintains | FTS5 only |
| Self-analysis table | CC analyzing itself, writing to its own table | Gemini does external review |
| Session table not populated | Two systems (SQLite + files), neither complete | One path: abzu.db session_summaries |
| Access log bulk-stamped | Tracking system never organically used | `last_accessed` on bead row, updated on actual recall |
| Retention did nothing | 371/378 beads at weight 1.0 | Recall-based decay, not just age |
| Post-compact loses beads | Only session-start injects beads | Post-compact injects conversational state |

### From PLTM-Claude

| Anti-Pattern | What's Wrong | Abzu's Answer |
|---|---|---|
| 136 MCP tools | Decision paralysis for the agent | 4 tools |
| 40 tables | Fragmented data, impossible to reason about | 7 tables |
| 3-judge memory jury | Over-engineering storage decisions | Content hash + FTS dedup |
| 4 memory types with separate schemas | Unnecessary categorization | 5 categories, one table |
| Embedding pipeline + knowledge graph | Complexity without proven value | FTS5 first |
| Self-modeling, personality tracking | Solving imaginary problems | Persona is a file |
| Dashboard before core works | Visualization before the thing works | Get recall working first |
| External LLM for ingestion | Dependency on external API availability | Heuristic + CC, Gemini only for review |
| 11 tests for 136 tools | Untested infrastructure | Test what ships |

### From Claudest (What They Got Right)

| Principle | Claudest's Approach | Abzu's Adoption |
|---|---|---|
| Simple beats complex | SQLite + FTS5, no embeddings, scored 74% on LoCoMo | FTS5 only, add embeddings only if proven insufficient |
| Store raw, let LLM search | Raw conversations indexed, LLM constructs queries | JSONL is raw archive, Abzu stores structured extraction |
| Async, never block | Hook-based sync on session stop | Session-end extraction, never blocks working session |
| No knowledge graphs | They add complexity without proportional value | No knowledge graph |

---

## 19. Glossary

| Term | Definition |
|---|---|
| **Abzu** | Enki v3's memory system. Named for the Sumerian primordial freshwater ocean — Enki's domain. Infrastructure layer, not an agent. |
| **Bead** | A unit of distilled knowledge in wisdom.db. Decisions, learnings, patterns, fixes, or preferences. |
| **Candidate** | A bead that hasn't been Gemini-approved yet. Lives in abzu.db staging. Searchable but ranked lower than approved beads. |
| **wisdom.db** | Permanent knowledge store. Only preferences and Gemini-approved beads live here. |
| **abzu.db** | Operational memory. Session summaries, bead candidates, extraction tracking. |
| **em.db** | Per-project operational database owned by EM. Mail, tasks, bugs. Read by Abzu at project completion. |
| **Staging** | The `bead_candidates` table in abzu.db where non-preference beads wait for Gemini review. |
| **Gemini Review** | Monthly/quarterly external LLM review that promotes, discards, or consolidates staged candidates. The only path to permanent storage in wisdom.db (other than preferences). |
| **Heuristic Extraction** | Regex/pattern-based extraction from JSONL. Deterministic, no LLM. Catches decisions, errors, files. |
| **CC Distillation** | LLM-assisted extraction where CC enriches heuristic output with nuance, fills gaps, identifies patterns heuristics miss. |
| **Session Summary** | Operational + conversational state captured at pre-compact and session end. Carries context across compactions and sessions. |
| **Surface Water** | Metaphor for operational state (session summaries) — fast access, ephemeral. |
| **Deep Water** | Metaphor for permanent knowledge (beads in wisdom.db) — accumulated, curated, persistent. |

---

## Appendix A: Migration from Current Enki

### What Gets Migrated

| Current | Action | Target |
|---|---|---|
| 378 beads in wisdom.db | Migrate to new schema, map types to categories | wisdom.db (new) as candidates pending Gemini review |
| FTS5 triggers | Keep pattern, rebuild for new schema | wisdom.db (new) |
| 25 projects | Migrate to simplified projects table | wisdom.db (new) |
| Session archives (`.enki/sessions/*.md`) | No migration — JSONL is the source | N/A |

### What Gets Dropped

| Current | Why |
|---|---|
| `embeddings`, `embedding_cache` | Cut — FTS5 only |
| `access_log` | Replaced by `last_accessed` on bead |
| `enki_self_analysis` | Dead table |
| `agents`, `messages`, `file_claims` | Moved to em.db (EM spec) |
| `interceptions`, `violations`, `tier_escalations` | Moved to Gates (Pillar 2) |
| `feedback_proposals` | Moved to Gates (Pillar 2) |
| `sync_queue`, `auth_tokens`, `bead_cache` | Deployment concerns, not memory |

### Migration Strategy

1. Export current beads with category mapping (`decision`→`decision`, `learning`→`learning`, `solution`→`fix`, `violation`→`learning`)
2. Import into new wisdom.db as staged candidates in abzu.db
3. Run Gemini review on migrated candidates — promotes valuable ones, discards noise/empties/redacted beads
4. Clean slate for wisdom.db — only Gemini-approved beads survive migration

---

## Appendix B: Open Items for Other Specs

### For Pillar 2 (Uru Spec) — Resolved
- ✅ Feedback proposals table — owned by uru.db
- ✅ Interceptions, violations — replaced by uru.db enforcement_log
- ✅ Uru reads em.db/abzu.db for workflow state (binary checks), not wisdom.db for patterns

### For Pillar 3 (EM Spec — Corrections Applied in v1.1)
- ✅ Spawn authority: EM does not spawn PM. Enki spawns both as peers.
- ✅ em.db ownership: EM owns em.db, Abzu reads it at project completion
- ✅ PM's Abzu access: PM reads wisdom.db + staging directly, not through EM

### For Yggdrasil (Separate Design)
- PM writes to Yggdrasil — interface TBD
- EM writes to Yggdrasil — interface TBD
- Yggdrasil's relationship to Abzu's project registry — avoid duplication

### For Bridge Spec
- Abzu → EM: session summary injection, bead context for agents
- EM → Abzu: candidate staging, em.db distillation trigger
- Uru → em.db/abzu.db: workflow state reads for gate checks
- Gemini → Abzu + Uru: combined quarterly review cycle

---

*End of Abzu Memory Spec v1.1*
