# Enki v3 Bridge Spec — Cross-Pillar Interfaces

> **Version**: 1.1
> **Date**: 2025-02-13
> **Status**: Final
> **Scope**: Defines all interfaces between Pillar 1 (Abzu/Memory), Pillar 2 (Uru/Gates), and Pillar 3 (EM/Orchestration), plus external systems (Gemini, Yggdrasil, JSONL).
> **Audience**: Architect reads this to understand how the pillars connect. Implementation Spec uses this to define module boundaries and API contracts.
> **v1.1 Changes**: Added WAL mode requirement, DB protection cross-reference to Uru Layer 0.5.

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Map](#2-system-map)
3. [Database Ownership](#3-database-ownership)
4. [Pillar-to-Pillar Interfaces](#4-pillar-to-pillar-interfaces)
5. [Hook Orchestration](#5-hook-orchestration)
6. [Gemini Review Cycle](#6-gemini-review-cycle)
7. [Spawn Authority](#7-spawn-authority)
8. [MCP Tool Surface](#8-mcp-tool-surface)
9. [Yggdrasil Interface](#9-yggdrasil-interface)
10. [JSONL Interface](#10-jsonl-interface)
11. [Failure Modes](#11-failure-modes)

---

## 1. Overview

Enki v3 has three pillars:

| Pillar | Name | What | Nature |
|---|---|---|---|
| 1 | **Abzu** | Memory — beads, session summaries, candidate staging | Infrastructure (library) |
| 2 | **Uru** | Gates — workflow enforcement, hooks, feedback loop | Infrastructure (hooks + library) |
| 3 | **EM** | Orchestration — agent spawning, mail, DAG, sprints | Agent (spawned by Enki) |

Plus two peer agents spawned by Enki:

| Agent | What |
|---|---|
| **PM** | Project owner — intake, specs, debate, status, closure |
| **Enki (persona)** | The orchestrating identity. Spawns PM and EM. Human's interface. |

And external systems:

| System | What |
|---|---|
| **Gemini** | External LLM for quarterly review (beads + rules) |
| **Yggdrasil** | Project management tool (Enki's Jira + Confluence) — TBD |
| **JSONL** | CC's transcript files — raw archive, read-only for Abzu |

---

## 2. System Map

```
                              ┌──────────┐
                              │  Human   │
                              └────┬─────┘
                                   │
                              ┌────▼─────┐
                              │   Enki   │ ← persona + orchestrating identity
                              │ (spawns) │
                              └──┬───┬───┘
                     ┌───────────┘   └───────────┐
                     ▼                           ▼
               ┌───────────┐              ┌───────────┐
               │    PM     │◄── mail ───►│    EM     │
               │  (peer)   │              │  (peer)   │
               └─────┬─────┘              └─────┬─────┘
                     │                          │ spawns
                     │                    ┌─────┼─────────┐
                     │                    ▼     ▼         ▼
                     │                  Dev    QA    Validator...
                     │
    ┌────────────────┼────────────────────────────────────────┐
    │                │          INFRASTRUCTURE                │
    │                ▼                                        │
    │  ┌──────────────────┐  reads   ┌──────────────────┐    │
    │  │   Abzu (Pillar 1)│◄────────│   Uru (Pillar 2) │    │
    │  │                  │          │                  │    │
    │  │  wisdom.db       │          │  uru.db          │    │
    │  │  abzu.db         │          │  hooks/*.sh      │    │
    │  └────────┬─────────┘          └──────────────────┘    │
    │           │                                            │
    │           │ reads                                      │
    │           ▼                                            │
    │  ┌──────────────────┐                                  │
    │  │  em.db (per proj)│ ← EM + PM read/write             │
    │  └──────────────────┘                                  │
    │                                                        │
    └────────────────────────────────────────────────────────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Gemini    Yggdrasil    JSONL
     (quarterly)   (TBD)    (raw archive)
```

---

## 3. Database Ownership

Four databases. Each has one owner. Cross-reads are defined. No cross-writes.

### SQLite Configuration (v1.1)

All databases use WAL (Write-Ahead Logging) mode with a 5000ms busy timeout. This handles concurrent reads and the limited concurrent writes from parallel subagents (EM supports MAX_PARALLEL_TASKS = 2).

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

Set on every connection open. Non-negotiable.

### Database Protection

All `.db` files under `~/.enki/` are protected from direct CC manipulation by Uru Layer 0.5 (see Uru Spec Section 7). CC must use Enki tools/functions to modify DB state — direct `sqlite3` commands via bash are blocked.

| Database | Owner | Location | Lifespan | Writes | Reads |
|---|---|---|---|---|---|
| **wisdom.db** | Abzu | `~/.enki/wisdom.db` | Permanent | Abzu (preferences direct, Gemini-promoted beads) | PM, EM, CC, Gemini |
| **abzu.db** | Abzu | `~/.enki/abzu.db` | Rolling | Abzu (session summaries, staged candidates) | Uru (workflow state), Gemini (candidates) |
| **em.db** | EM | `~/.enki/projects/{name}/em.db` | Per-project + 30 days | EM (mail, tasks, sprints), PM (mail, decisions) | Abzu (project completion distillation), Uru (goal/phase/spec checks) |
| **uru.db** | Uru | `~/.enki/uru.db` | Permanent | Uru (enforcement logs, proposals, nudge state) | Gemini (quarterly audit) |

### Cross-Read Rules

| Reader | Reads From | What | Why |
|---|---|---|---|
| Uru | em.db | goal, phase, spec_approved, tier | Gate checks — binary state only |
| Uru | abzu.db | session state (active session exists) | Session context for nudges |
| Abzu | em.db | mail threads, bugs, pm_decisions | Project completion distillation |
| Gemini | wisdom.db | beads | Context for promotion decisions |
| Gemini | abzu.db | staged candidates | Review candidates for promotion |
| Gemini | uru.db | enforcement logs, proposals | Rule evolution review |
| PM | wisdom.db | beads, staged candidates | Historical context for debate |
| EM | wisdom.db | beads, staged candidates | Context injection for agents |

### No Cross-Writes

No component writes to another component's database. If data needs to move:

| Data Movement | How |
|---|---|
| Bead candidate → wisdom.db | Gemini promotes via Abzu functions |
| EM state → enforcement check | Uru reads em.db (read-only) |
| em.db knowledge → beads | Abzu reads em.db at project completion, writes candidates to abzu.db |
| Enforcement log → proposal | Uru creates proposal in its own uru.db |

---

## 4. Pillar-to-Pillar Interfaces

### Abzu ↔ EM (Pillar 1 ↔ Pillar 3)

| Direction | Interface | When |
|---|---|---|
| **EM → Abzu** | `abzu.remember(content, category, project)` | EM records decision/learning during orchestration |
| **EM → Abzu** | `abzu.recall(query, scope, project)` | EM fetching context to inject into agent prompts |
| **Abzu → em.db** | `abzu.read_em_db(project)` | Project completion — reads mail threads for distillation |
| **Abzu → EM** | Session summary injection at session start | Hook loads last session summary, EM uses it for continuity |

### Abzu ↔ Uru (Pillar 1 ↔ Pillar 2)

| Direction | Interface | When |
|---|---|---|
| **Uru → abzu.db** | Read session state | Nudge checks (long session without summary) |
| **Uru → em.db** | Read goal, phase, spec_approved | Gate checks (hard blocks) |
| **Abzu → Uru** | Nothing | Abzu never calls Uru |

Uru reads from databases Abzu and EM own. Abzu never needs to know about Uru.

### EM ↔ Uru (Pillar 3 ↔ Pillar 2)

| Direction | Interface | When |
|---|---|---|
| **Uru → em.db** | Read workflow state | Every pre-tool-use gate check |
| **EM → Uru** | Nothing | EM never calls Uru. Uru reads EM's state. |

EM drives the workflow. Uru verifies the workflow is being followed. EM doesn't know Uru exists. Belt and suspenders.

### PM ↔ Abzu

| Direction | Interface | When |
|---|---|---|
| **PM → Abzu** | `abzu.recall(query, scope, project)` | Historical context for debate, cross-project awareness |
| **PM → Abzu** | Nothing else | PM doesn't write to Abzu — PM's outputs live in em.db and Yggdrasil |

---

## 5. Hook Orchestration

Hooks are the integration point where all three pillars meet. Each hook calls functions from multiple pillars.

### Hook → Pillar Mapping

| Hook | Uru (Pillar 2) | Abzu (Pillar 1) | EM (Pillar 3) |
|---|---|---|---|
| **session-start** | Init enforcement state, inject rules | Load persona, last summary, beads (tier-dependent) | Load project context, tier, goal, phase |
| **pre-tool-use** | Layer 0 check, Gate 1/2/3 | — | — |
| **post-tool-use** | Nudge checks, log enforcement | — | — |
| **pre-compact** | Log enforcement state | CC captures conversational state, heuristic captures operational state | — |
| **post-compact** | Re-inject enforcement context | Re-inject persona, accumulated summaries, phase/tier/goal | — |
| **session-end** | Write enforcement summary, generate proposals | Finalize session summary, extract bead candidates, run decay | — |

### Hook Execution Order Within Each Hook

**session-start:**
1. Uru: initialize uru.db session entry
2. Abzu: load persona + last session summary + beads (tier-dependent)
3. EM: load project state (goal, phase, tier)
4. Uru: inject enforcement context ("You are in phase X. Next step: Y.")

**pre-tool-use:**
1. Layer 0: file blocklist check (pure bash, <1ms)
2. Uru Gate 1: goal exists? (DB read, <10ms)
3. Uru Gate 2: spec approved? (DB read, <10ms, Standard/Full only)
4. Uru Gate 3: phase >= implement? (DB read, <10ms)
5. If all pass → tool executes

**post-tool-use:**
1. Uru: log tool call to enforcement_log
2. Uru Nudge 1: check for unrecorded decisions
3. Uru Nudge 2: check for long session without summary

**pre-compact:**
1. Uru: log current enforcement state snapshot
2. Abzu: heuristic extraction of operational state from JSONL
3. Abzu: CC writes conversational state (ideas, direction, reasoning)
4. Both stored as pre-compact summary in abzu.db

**post-compact:**
1. Abzu: re-inject persona + accumulated pre-compact summaries + phase/tier/goal
2. Uru: re-inject enforcement context

**session-end:**
1. Abzu: read accumulated pre-compact summaries
2. Abzu: heuristic extraction on remaining JSONL since last pre-compact
3. Abzu: CC distillation → final session summary + bead candidates
4. Abzu: run decay pass on wisdom.db
5. Uru: write session enforcement summary
6. Uru: generate feedback proposals if warranted (overrides, ignored nudges)

---

## 6. Gemini Review Cycle

One quarterly review covers both Abzu and Uru. Single Gemini session, two review passes.

### Input Package

```
To Gemini:
  FROM ABZU:
    - All staged bead candidates in abzu.db
    - Existing wisdom.db beads (for context, dedup, contradiction checking)
    - Session summaries from the period

  FROM URU:
    - enforcement_log entries since last review
    - Pending feedback proposals
    - Current gate rules and thresholds
    - Block/override/nudge statistics
```

### Gemini Actions

**On Abzu (beads):**

| Action | What |
|---|---|
| Promote | Candidate → wisdom.db as permanent bead |
| Consolidate | Merge related candidates/beads into richer bead |
| Discard | Delete candidate from staging (noise, duplicate) |
| Flag | Mark existing wisdom.db bead for deletion (stale, contradicted) |

**On Uru (rules):**

| Action | What |
|---|---|
| Approve proposal | Rule change accepted, human applies it |
| Reject proposal | Proposal closed, rule unchanged |
| Suggest modification | Different change than proposed |
| Identify gap | New rule needed (proposal created) |

### Output

```
From Gemini:
  ABZU:
    - List of promoted beads (with consolidated content)
    - List of discarded candidates (with reasons)
    - List of flagged existing beads (with reasons)

  URU:
    - Approved/rejected proposals with reasoning
    - Suggested rule parameter changes
    - Identified gaps
    - Overall health assessment

Human reviews Gemini's output before changes are applied.
```

---

## 7. Spawn Authority

Definitive spawn model. Referenced by all three pillar specs.

### Who Spawns Whom

| Agent | Spawned By | When |
|---|---|---|
| **PM** | Enki | Intake, debate, sprint status, blockers, change requests, closure |
| **EM** | Enki | After PM kickoff, for execution |
| **Architect** | Enki (at PM's request via mail) | Planning phase — writes Implementation Spec |
| **DBA** | Enki (at PM's request via mail) | Planning phase — contributes data model |
| **Dev** | EM | Task execution |
| **QA** | EM | Task execution (parallel with Dev) |
| **Validator** | EM | Post-execution validation |
| **Reviewer** | EM | Post-validation code review |
| **InfoSec** | EM | Conditional — auth/data/network changes |

### What Is NOT Spawned

| Component | Nature | Why |
|---|---|---|
| **Abzu** | Library/infrastructure | Always available, called by functions |
| **Uru** | Hooks + library | Always running (hooks fire automatically) |
| **Gemini** | External LLM | Invoked on schedule, not spawned |

### Peer Relationship

PM and EM are peer departments. Neither spawns nor controls the other. Communication is through mail in em.db.

```
Enki spawns PM → PM does intake → PM writes spec → PM sends kickoff mail
Enki spawns EM → EM reads kickoff mail → EM builds DAG → EM spawns agents
```

---

## 8. MCP Tool Surface

Total across all pillars:

### Abzu Tools (4)

| Tool | What | Writes to |
|---|---|---|
| `enki_remember` | Store bead — preference to wisdom.db, else to staging | wisdom.db or abzu.db |
| `enki_recall` | Search wisdom.db + staging | Updates `last_accessed` |
| `enki_star` | Mark bead permanent | wisdom.db |
| `enki_status` | Health check | Read-only |

### Uru Tools (0)

Uru has no MCP tools. It operates entirely through hooks. CC cannot invoke Uru — Uru invokes itself on every tool call via the pre/post-tool-use hooks.

### EM Tools (defined in EM spec)

| Tool | What |
|---|---|
| `enki_goal` | Set project goal |
| `enki_phase` | Set/check current phase |
| `enki_triage` | Classify work, determine tier |
| `enki_debate` | Trigger debate round |
| `enki_plan` | Create sprint plan |
| `enki_approve` | Request human approval |
| `enki_decompose` | Break spec into tasks |
| `enki_orchestrate` | Build DAG, begin execution |
| `enki_task` | Execute a task (spawn agent) |
| `enki_bug` | File/manage bugs |

### Total Tool Count

| Pillar | Tools |
|---|---|
| Abzu | 4 |
| Uru | 0 |
| EM | ~10 |
| **Total** | **~14** |

Down from 35 in current Enki. Under the 30-tool ceiling from PLTM analysis.

---

## 9. Yggdrasil Interface

**Parked for separate design.** Placeholder interfaces defined here.

### What Yggdrasil Is

Enki's project management tool — Jira + Confluence equivalent. Living document for projects from inception to closure.

### Who Writes

| Agent | Writes |
|---|---|
| **PM** | Project creation, full specs, sprint milestones, status updates, change request outcomes, closure summary |
| **EM** | Bug entries, bug status updates, task progress, blocker/dependency comments |

### Who Reads

| Agent | Reads |
|---|---|
| **PM** | Existing projects for context, dependency flags |
| **EM** | Current project state for operational context |

### Relationship to Abzu

Yggdrasil is the structured project record. Abzu is distilled knowledge. They don't duplicate:

| Data | Lives in |
|---|---|
| Project specs, status, bugs | Yggdrasil |
| Distilled decisions, learnings, patterns | wisdom.db (via Abzu) |
| Operational mail, task state | em.db |

Yggdrasil interface will be defined in its own design pass.

---

## 10. JSONL Interface

### What JSONL Is

Claude Code's complete conversation transcript: `~/.claude/projects/{project-slug}/{session-id}.jsonl`. Append-only event log. Never truncated by compaction. CC manages these files.

### Who Reads JSONL

| Consumer | When | What |
|---|---|---|
| Abzu (heuristic extraction) | Pre-compact, session-end | Regex patterns for decisions, errors, files |
| Abzu (CC distillation) | Session-end | CC reads excerpt for structured extraction |
| Gemini | Quarterly review (optional) | Raw sessions for gap-finding |

### Who Writes JSONL

Nobody in Enki. CC writes JSONL as part of its normal operation. JSONL is read-only for all Enki components.

### Retention

JSONL files are on disk, managed by CC. Enki doesn't delete them. They serve as the ultimate fallback — if all else fails, JSONL has every word of every session.

---

## 11. Failure Modes

What happens when things break.

### Abzu Failure

| Failure | Impact | Recovery |
|---|---|---|
| wisdom.db corrupted | No beads available | Session continues without bead context. JSONL has raw data for reconstruction. |
| abzu.db corrupted | No session summaries | Next session starts fresh. Pre-compact summaries lost for current session. |
| Session-end extraction fails | No candidates staged, summary incomplete | JSONL still has data. Next Gemini review can extract from JSONL directly. |
| FTS5 index corrupted | Search returns nothing | Rebuild index from beads table. No data loss. |

### Uru Failure

| Failure | Impact | Recovery |
|---|---|---|
| Hook script error | Gate check fails → defaults to ALLOW (fail open) or BLOCK (fail closed)? | **Fail closed.** If Uru can't check, tool call is blocked. Better to halt than to allow unverified actions. |
| uru.db corrupted | No enforcement logs | Hooks still function (gate checks read em.db/abzu.db). Logs lost but enforcement continues. |
| em.db unreadable | Gate checks can't verify goal/phase/spec | Fail closed — block code mutations until em.db is accessible. |

### EM Failure

| Failure | Impact | Recovery |
|---|---|---|
| em.db corrupted | Project state lost | Reconstruct from JSONL + Yggdrasil. Abzu session summaries provide recent context. |
| Agent spawn fails | Task not executed | EM retries (max 3), then escalates to human. |
| Mail delivery fails | Agent output not routed | EM retries, then logs and escalates. |

### Cross-Pillar Failure

| Failure | Impact | Recovery |
|---|---|---|
| Gemini unavailable | No quarterly review | Candidates accumulate in staging. Rules don't evolve. No data loss. Resume on next review. |
| Hooks disabled by CC environment | All Uru enforcement gone | Layer 0 blocklist still protects files (if hooks are the mechanism, this is a total enforcement failure). **This is the single biggest risk.** |
| All DBs lost | Complete memory/state loss | JSONL is the ultimate fallback. Full reconstruction possible but expensive. |

### The Hooks-Disabled Risk

If CC's environment disables hooks (e.g., configuration change, CC update that changes hook behavior), all Uru enforcement disappears. This is the single point of failure for Pillar 2.

**Mitigation:** Session-start hook writes a canary file. If canary is absent and code mutations are attempted, something is wrong. But this only works if the session-start hook itself fired. If hooks are globally disabled, there is no mitigation within Enki — it's an environmental dependency.

**Monitoring:** Gemini quarterly review checks enforcement_log. If log entries stop appearing, Gemini flags it.

---

*End of Enki v3 Bridge Spec v1.0*
