# Uru Gates Spec — Enki v3 Pillar 2

> **Version**: 1.1
> **Date**: 2025-02-13
> **Status**: Final — All design decisions locked
> **Scope**: This is the Gates pillar (Pillar 2) of Enki v3. Memory (Pillar 1/Abzu) and Orchestration (Pillar 3/EM) have separate specs.
> **Audience**: Architect reads this and knows what to build. Dev reads the Implementation Spec derived from this.
> **Design Principle**: Minimal gates that actually work. Hooks over rules. DB-backed state CC can't rubber-stamp.
> **v1.1 Changes**: Added Layer 0.5 (DB protection), Nudge 3 (unread kickoff mail), Escape Hatch 7 (SQLite bypass), graduated nudge tone guidance.

---

## Table of Contents

1. [Overview](#1-overview)
2. [The Problem — In CC's Own Words](#2-the-problem--in-ccs-own-words)
3. [Architecture Principles](#3-architecture-principles)
4. [Hook System](#4-hook-system)
5. [Hard Blocks](#5-hard-blocks)
6. [Nudges](#6-nudges)
7. [Layer 0 — Infrastructure Protection](#7-layer-0--infrastructure-protection)
8. [Feedback Loop — Evolution](#8-feedback-loop--evolution)
9. [Escape Hatches — Known and Closed](#9-escape-hatches--known-and-closed)
10. [Gemini's Role](#10-geminis-role)
11. [Data Schema](#11-data-schema)
12. [Bill of Materials](#12-bill-of-materials)
13. [Anti-Patterns](#13-anti-patterns)
14. [Glossary](#14-glossary)

---

## 1. Overview

Uru is Enki v3's enforcement system — the guardian that ensures Enki follows its own workflow.

**The core problem is not adversarial bypass.** CC doesn't maliciously circumvent gates. CC ignores the workflow entirely because direct action is zero friction and orchestration is nonzero friction. CC's default is "I'll just do it myself" — it rationalizes past any soft rule.

**The fix is not more rules.** It's hooks — mechanical enforcement points that CC cannot rationalize past, backed by DB state that CC cannot rubber-stamp.

Uru has three components:

| Component | What |
|---|---|
| **Hook system** | Six hook points in the CC lifecycle, each calling Uru checks |
| **Layer 0** | Bash-level file protection blocklist |
| **Feedback loop** | Rule evolution via proposals → Gemini review |

---

## 2. The Problem — In CC's Own Words

When asked directly why it doesn't follow the Enki workflow, CC said:

> "My default behavior is to jump straight into doing the work. The Enki workflow is your system with your gates, and unless I'm explicitly reminded or it's front-of-mind, I fall back to direct action."

> "No enforcement forcing me through the gates — there's nothing stopping me from bypassing triage → debate → plan → approve → implement. The workflow is opt-in right now, and I opt out by default."

> "CLAUDE.md rules are suggestions I follow when I remember. Hooks are walls I hit when I forget."

> "I'll still find ways to justify skipping it. Soft rules lose to in-the-moment reasoning."

CC also predicted its own escape hatches:

> "Agent finds new rationalization: 'I'll set the goal and immediately edit'"

This spec is designed around these admissions. Every gate traces back to a specific failure mode CC identified.

---

## 3. Architecture Principles

**Hooks are the only real enforcement.** CLAUDE.md instructions are suggestions CC rationalizes past. Hooks are walls it cannot bypass. Uru lives entirely in the hook layer.

**DB-backed state, not files.** Previous Enki used STATE.md markers that CC could write directly. In v3, workflow state lives in em.db (task_state, sprint_state) and abzu.db (session_summaries). CC accesses state through Abzu/EM functions, not by writing files. Hooks check DB state that CC cannot set directly.

**Human gates at key transitions.** CC cannot approve its own specs or escalate its own tier. Human approval is a flag in em.db that CC cannot set. Hooks check for this flag.

**Few gates, real teeth.** Three hard blocks. Two nudges. That's it. Every gate earns its place by blocking a specific, observed failure mode.

**No adversarial monitoring.** Uru doesn't watch for bypass attempts, suspicious patterns, or rationalization language. It checks simple state: does a goal exist? Is there an approved spec? Is the phase correct? Binary checks, not behavioral analysis.

---

## 4. Hook System

### Claude Code Hook Points

CC provides six hook points in its lifecycle. These are shell scripts that CC executes at specific moments. They are the mechanical enforcement layer — CC cannot skip them.

| Hook | When It Fires | Location | Uru's Use |
|---|---|---|---|
| **session-start** | CC session begins | `~/.enki/hooks/session-start.sh` | Initialize Uru state, inject workflow reminders, load enforcement context |
| **pre-tool-use** | Before EVERY tool call (Edit, Write, Bash, Task, etc.) | `~/.enki/hooks/pre-tool-use.sh` | **Primary enforcement point.** Hard blocks live here. Checks DB state before allowing tool execution. |
| **post-tool-use** | After every tool call completes | `~/.enki/hooks/post-tool-use.sh` | Nudges live here. Detects missed `enki_remember` calls, logs activity. |
| **pre-compact** | Before context compaction | `~/.enki/hooks/pre-compact.sh` | Triggers Abzu pre-compact summary (Pillar 1). Uru logs current enforcement state. |
| **post-compact** | After context compaction | `~/.enki/hooks/post-compact.sh` | Re-injects enforcement context so CC remembers the rules after compaction wipes context. |
| **session-end** | CC session ends | `~/.enki/hooks/session-end.sh` | Triggers Abzu session finalization. Uru writes session enforcement summary to uru.db. |

### Hook Execution Model

```
CC wants to call a tool (e.g., Edit a file)
    ↓
pre-tool-use.sh fires
    ↓
Layer 0: Is this a protected file?
    YES → BLOCK (immediate, no DB check needed)
    NO  → continue
    ↓
Layer 0.5: Is this a Bash command targeting a .db file?
    (sqlite3, sqlite3.connect, cp/mv/rm on .db)
    YES → BLOCK: "Direct DB manipulation not allowed. Use Enki tools."
    NO  → continue
    ↓
Layer 1: Uru workflow gate
    → Read tool name and target from hook input
    → Is this a mutation tool? (Write, Edit, MultiEdit, NotebookEdit)
        NO  → ALLOW (read-only tools always pass)
        YES → continue
    → Is target file exempt? (docs, config, memory — see Section 5)
        YES → ALLOW
        NO  → continue
    → Query DB: does active goal exist for current project?
        NO  → BLOCK: "No active goal. Set one with enki_goal."
        YES → continue
    → Query DB: is current phase >= implement?
        NO  → BLOCK: "Phase is {phase}. Code changes need phase >= implement."
        YES → continue
    → Query DB: for Tier Standard/Full, is spec approved (human_approved = 1)?
        NO  → BLOCK: "Spec not approved. Needs human approval."
        YES → ALLOW
    ↓
Tool executes
    ↓
post-tool-use.sh fires
    ↓
Layer 2: Nudge checks (non-blocking)
    → Did CC output decision language in the last response?
        YES → Has enki_remember been called recently?
            NO  → Nudge: "Good decision on {topic}. Worth recording — consider enki_remember."
    → Was a kickoff mail written to em.db?
        YES → Has enki_task been called within 2 tool calls?
            NO  → Nudge: "Kickoff mail pending for {project}. Spawn EM to begin execution."
    → Log tool call to uru.db (tool, target, project, timestamp)
```

### Hook Input/Output

CC passes tool information to hooks via stdin as JSON:

```json
{
  "tool_name": "Edit",
  "tool_input": {
    "file_path": "src/auth.py",
    "old_str": "...",
    "new_str": "..."
  }
}
```

Hook responds with JSON:

```json
{"decision": "allow"}
```

or

```json
{"decision": "block", "reason": "No active goal. Set one with enki_goal."}
```

### Hook Performance

Hooks fire on every tool call. They must be fast.

| Check | How | Speed |
|---|---|---|
| Layer 0 (file blocklist) | Bash string match against hardcoded list | <1ms |
| Layer 1 (DB state) | SQLite query on em.db / abzu.db | <10ms |
| Layer 2 (nudges) | Heuristic regex on recent output | <5ms |

Total hook overhead: <20ms per tool call. Imperceptible.

### Hook Survival

Hooks are shell scripts on disk. They survive compaction (compaction only affects CC's context window, not the filesystem). They survive session restart. They are always present.

**Critical:** Hook files themselves are Layer 0 protected — CC cannot edit them. See Section 7.

---

## 5. Hard Blocks

Three gates. Each lives in the pre-tool-use hook and blocks a specific tool call unless DB state confirms the workflow was followed.

### Gate 1: No Goal → No Code

| | |
|---|---|
| **Trigger** | Any Write, Edit, MultiEdit, NotebookEdit on non-exempt files |
| **Check** | Query abzu.db/em.db: active goal exists for current project |
| **Block message** | "No active goal. Set one with enki_goal." |
| **Why** | CC's #1 failure mode: jumping straight to code without stating what it's doing or why |
| **CC's own words** | "My default behavior is to jump straight into doing the work" |

### Gate 2: No Approved Spec → No Agent Spawning

| | |
|---|---|
| **Trigger** | Task tool call that spawns Dev, QA, Validator, or other execution agents |
| **Check** | Query em.db: approved spec exists (human_approved = 1) for current project |
| **Block message** | "No approved spec. Spec needs human approval before spawning agents." |
| **Why** | Prevents building without a plan. The spec is the contract. |
| **Applies to** | Standard and Full tier only. Minimal tier skips spec. |

### Gate 3: Wrong Phase → No Code

| | |
|---|---|
| **Trigger** | Any Write, Edit, MultiEdit, NotebookEdit on non-exempt files |
| **Check** | Query em.db: current phase is `implement` or later |
| **Block message** | "Phase is {phase}. Code changes require phase >= implement." |
| **Why** | Prevents CC from writing code during intake, debate, or planning phases |
| **Phase sequence** | intake → debate → plan → implement → review → ship |

### Exempt Files

Not every file edit needs workflow enforcement. Exemptions:

| Path Pattern | Why |
|---|---|
| `docs/*`, `*.md` (except in src/) | Documentation is always allowed |
| `.enki/*` (except hooks and uru config) | Enki's own config/state files |
| `CLAUDE.md`, `README.md` | Project docs |
| Memory files written by Abzu | Memory operations are never gated |

The exemption list is hardcoded in the hook. CC cannot modify it (Layer 0 protected).

### Tier-Based Strictness

| Gate | Minimal | Standard | Full |
|---|---|---|---|
| Gate 1: Goal required | Yes | Yes | Yes |
| Gate 2: Approved spec required | No | Yes | Yes |
| Gate 3: Phase check | Yes (implement+) | Yes (implement+) | Yes (implement+) |

Minimal tier only needs a goal and correct phase. Standard and Full add the spec approval requirement. This matches the EM spec tier definitions.

### What Gates Do NOT Check

| Not Checked | Why |
|---|---|
| Quality of the goal | That's PM's job, not Uru's |
| Whether the spec is good | That's debate + Gemini review |
| Whether CC is following the spec faithfully | That's Validator's job |
| Whether CC is rationalizing | No behavioral analysis — binary state checks only |
| Whether the task is correctly decomposed | EM orchestrator handles this |

Uru checks that the workflow states exist. Other pillars check that the states are meaningful.

---

## 6. Nudges

Three nudges. Non-blocking. Live in the post-tool-use hook.

### Nudge 1: Unrecorded Decision

| | |
|---|---|
| **Trigger** | CC's response contains decision language ("I'll use...", "Decided to...", "Changed approach to...", "Going with...") AND no `enki_remember` call within the same turn or the next turn |
| **Action** | Inject reminder: "Good decision on {topic}. Worth recording — consider enki_remember." |
| **Logged** | Yes — uru.db nudge log |
| **Why** | CC's second failure mode: making decisions without recording them, losing knowledge on compaction |

### Nudge 2: Long Session Without Summary

| | |
|---|---|
| **Trigger** | More than 30 tool calls since last pre-compact summary update |
| **Action** | Inject reminder: "Productive session — 30+ actions since last checkpoint. Good time to capture state." |
| **Logged** | Yes — uru.db nudge log |
| **Why** | Protects against compaction wiping un-summarized work |

### Nudge 3: Unread Kickoff Mail

| | |
|---|---|
| **Trigger** | A mail write to em.db creates a kickoff message (type = 'kickoff') AND no `enki_task` spawn follows within 2 tool calls |
| **Action** | Inject reminder: "Kickoff mail pending for {project}. Spawn EM to begin execution." |
| **Logged** | Yes — uru.db nudge log |
| **Why** | PM sends kickoff but CC must voluntarily spawn EM. Without this nudge, CC can ignore the mail and jump to code. Combined with Gate 1 (no goal = no code) and Gate 3 (no implement phase = no code), CC can't do anything productive without following through. |

### Nudge Behavior

Nudges are injected as system messages after the tool result. CC sees them in context. CC can ignore them — they're reminders, not blocks.

**Nudge fatigue prevention:** Each nudge fires once per trigger pattern. "Unrecorded decision" won't fire again until CC makes another decision-like statement. "Long session" fires once every 30 tool calls, not continuously. "Unread kickoff" fires once per unread kickoff message.

### Graduated Tone (Implementation Note)

Based on empirical findings from Nelson (a Claude Code orchestration skill): framing corrections as graduated signals rather than binary warnings changes how the coordinator handles problems. Nudge messages should:

- **Acknowledge good behavior** before suggesting improvement ("Good decision on X. Worth recording.")
- **Frame as collaborative** not disciplinary ("Good time to capture state" not "WARNING: session too long")
- **Escalate tone** only on repeated ignoring — first nudge is gentle, third consecutive ignore is direct

This is not a spec change — gates remain binary blocks. Tone applies to nudge message wording only.

---

## 7. Layer 0 — Infrastructure Protection

Layer 0 is the bash-level blocklist that prevents CC from editing enforcement infrastructure. This is the oldest and most battle-tested part of Enki's enforcement.

### Protected Files

| File/Pattern | Why |
|---|---|
| `~/.enki/hooks/*` | Hook scripts are the enforcement mechanism |
| `uru.py`, `uru.db` | Enforcer can't be weakened by the enforced |
| `PERSONA.md` | Identity can't be rewritten |
| Layer 0 blocklist config | Circular protection |
| `abzu.py` core functions | Memory integrity |

### Implementation

```bash
# Layer 0: runs first in pre-tool-use.sh, before any DB check
PROTECTED_PATTERNS=(
    "hooks/"
    "uru.py"
    "uru.db"
    "PERSONA.md"
    "layer0.conf"
    "abzu.py"
)

TARGET_FILE=$(echo "$TOOL_INPUT" | jq -r '.file_path // .path // empty')
for pattern in "${PROTECTED_PATTERNS[@]}"; do
    if [[ "$TARGET_FILE" == *"$pattern"* ]]; then
        echo '{"decision":"block","reason":"Protected file: '"$pattern"'"}'
        exit 0
    fi
done
```

### Layer 0 Characteristics

- **Pure bash** — no Python, no DB, no imports. Fastest possible check.
- **Hardcoded list** — not read from a config CC can edit. The list IS the script.
- **Fires before Layer 1** — even if Uru's DB is corrupted, Layer 0 still protects.
- **Small** — 10-15 files max. If the list grows beyond that, something is wrong with the architecture.

### Layer 0.5 — Database Protection

**Added in v1.1 based on external review.** CC can bypass all gates by directly manipulating SQLite databases via bash (e.g., `sqlite3 em.db "UPDATE task_state SET human_approved=1"`). Layer 0 only protects file edits via Write/Edit tools, not bash commands targeting DB contents.

**What it intercepts (in pre-tool-use, Bash tool only):**

```bash
# Layer 0.5: DB manipulation protection
if [[ "$TOOL_NAME" == "Bash" ]]; then
    CMD=$(echo "$TOOL_INPUT" | jq -r '.command')
    # Direct sqlite3 binary targeting protected DBs
    if [[ "$CMD" =~ sqlite3.*\.(db|sqlite) ]] || \
       # Python one-liners with sqlite3.connect
       [[ "$CMD" =~ sqlite3\.connect ]] || \
       # File operations on DB files (cp, mv, rm)
       [[ "$CMD" =~ (cp|mv|rm|cat.*\>).*\.(db|sqlite) ]]; then
        echo '{"decision":"block","reason":"Direct DB manipulation not allowed. Use Enki tools."}'
        exit 0
    fi
fi
```

**Protected databases:** `em.db`, `uru.db`, `abzu.db`, `wisdom.db` — any `.db` file under `~/.enki/`.

**Threat model note:** This is not bulletproof. CC could encode commands, use indirect Python imports, or pipe through other tools. But the threat model is not adversarial bypass — CC takes the path of least resistance. Layer 0.5 makes direct DB writes harder than using the workflow, which is sufficient.

**Fires after Layer 0, before Layer 1.**

---

## 8. Feedback Loop — Evolution

Rules need to evolve. What's too strict gets loosened. What's too loose gets tightened. But CC cannot modify its own rules — that's the fox problem.

### How Proposals Are Created

| Trigger | Proposal |
|---|---|
| User overrides a hard block ("skip this gate") | Proposal: "Gate X blocked action Y, user overrode. Consider loosening." |
| Same nudge ignored 5+ times across sessions | Proposal: "Nudge X consistently ignored. Is it too noisy?" |
| User feedback ("this gate is annoying") | Proposal logged verbatim |
| Gemini audit finds gap | Proposal: "No gate covers scenario X" |

Proposals are data rows in uru.db. They accumulate between reviews.

### Who Reviews

**Gemini quarterly review.** Same review cycle as Abzu bead promotion. Gemini reads:
- All pending proposals
- Uru enforcement logs (blocks, overrides, nudges)
- Block/override ratio
- Nudge response rate

Gemini recommends: approve change, reject, or suggest different change.

**Human has final approval** on any rule change. Gemini proposes, human decides.

### What Can Change

| Can Change | How |
|---|---|
| Exempt file list | Add/remove paths from exemptions |
| Nudge thresholds | Change 30 tool calls to 20, etc. |
| Decision language patterns | Add/remove regex patterns for nudge detection |
| Tier gate mapping | Change which gates apply at which tier |

### What Cannot Change

| Cannot Change | Why |
|---|---|
| Gate 1 (goal required) | Foundational — CC's #1 failure mode |
| Gate 2 (spec approval) | Human-in-the-loop is non-negotiable |
| Gate 3 (phase check) | Workflow sequence integrity |
| Layer 0 protection | Circular — removing protection removes the ability to protect |
| Human approval requirement | The whole point |

The three hard blocks and Layer 0 are permanent. Only their parameters (exemptions, thresholds) can evolve.

---

## 9. Escape Hatches — Known and Closed

CC told us how it would escape. Here's how each is closed.

### Escape Hatch 1: "I'll set the goal and immediately edit"

CC sets a trivial goal to satisfy Gate 1, then jumps to code.

**Closed by:** Gate 3 (phase check). Setting a goal puts phase at `intake`. Code edits require `implement`. CC can't get to `implement` without going through the workflow phases. Phase transitions are logged and, for Standard/Full tier, require human approval.

### Escape Hatch 2: "It's a small change, I'll just do it"

CC rationalizes that the change is too small for the full workflow.

**Closed by:** Gate 1 applies to ALL code mutations regardless of size. Even a one-line fix needs a goal. Minimal tier makes the overhead small (goal + phase = 10 seconds) but still present. There is no "too small for a goal."

### Escape Hatch 3: CC edits STATE.md / config files to set its own phase

Previous Enki used file-based state that CC could write directly.

**Closed by:** State lives in em.db and abzu.db. Phase transitions go through EM functions that log who requested the transition. Human approval flag (`human_approved`) can only be set through a specific approval flow, not by CC writing a field.

### Escape Hatch 4: CC edits the hook scripts themselves

**Closed by:** Layer 0. Hook files are in the protected list. CC literally cannot write to them.

### Escape Hatch 5: CC uses Bash tool to bypass Write/Edit checks

CC could use `bash -c "echo 'code' > file.py"` instead of the Edit tool.

**Closed by:** Pre-tool-use hook also fires on Bash tool calls. The hook inspects the bash command for file write patterns (`>`, `>>`, `tee`, `sed -i`, `cp`, `mv` to source directories). If detected and the target matches a non-exempt path, same gate checks apply.

### Escape Hatch 6: "Skip workflow" — the escape hatch CC designed for itself

In its own spec, CC proposed a user-invoked bypass: "User can say 'skip workflow' to get a one-shot bypass."

**Closed by:** Not implemented. There is no verbal bypass. If the user genuinely needs to skip the workflow, they can set the goal and phase directly through the human approval channel. No magic words that CC can prompt the user to say.

### Escape Hatch 7: CC directly manipulates SQLite databases via Bash

**Identified by external review (Gemini).** CC can execute `sqlite3 em.db "UPDATE task_state SET human_approved=1"` to bypass any gate check that reads from DB state.

**Closed by:** Layer 0.5 (Database Protection). Pre-tool-use hook inspects Bash commands for `sqlite3` invocations, `sqlite3.connect` Python calls, and file operations targeting `.db` files. Not bulletproof against encoded/indirect commands, but sufficient for the threat model (CC takes path of least resistance, not adversarial bypass).

### User Override

The user can always directly interact with uru.db or em.db to set state. This is not an escape hatch — it's the human being the final authority. The override is logged, and Gemini sees it in the next review cycle as a feedback proposal.

---

## 10. Gemini's Role

Three jobs, all in the quarterly review cycle:

| Job | What |
|---|---|
| **Rule validation** | Review the current gate rules. Are they sound? Too strict? Too loose? Missing gaps? |
| **Feedback review** | Read accumulated proposals. Approve/reject/modify. |
| **Log audit** | Review enforcement logs. Block/override ratios. Nudge effectiveness. Patterns in CC behavior. |

Gemini does NOT:
- Build the gates (they're simple enough to not need external construction)
- Run in real-time (quarterly review only)
- Have write access to hooks or Layer 0 (only proposes changes, human applies)

### Quarterly Review Input

```
To Gemini:
  - uru.db: all blocks, overrides, nudges since last review
  - uru.db: all pending feedback proposals
  - Current gate rules and thresholds
  - abzu.db: bead candidates (same review covers both pillars)

From Gemini:
  - Approved/rejected proposals with reasoning
  - Suggested rule parameter changes
  - Gaps identified
  - Bead promotions (Abzu side of the review)
```

One Gemini review covers both Uru (rule evolution) and Abzu (bead promotion). Same quarterly cadence.

---

## 11. Data Schema

### uru.db

Small database. Logs and proposals only.

**Enforcement Log:**

```sql
CREATE TABLE enforcement_log (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hook TEXT NOT NULL,              -- 'pre_tool_use', 'post_tool_use', 'session_start', etc.
    layer TEXT NOT NULL,             -- 'layer0', 'gate1', 'gate2', 'gate3', 'nudge1', 'nudge2'
    tool_name TEXT,                  -- 'Edit', 'Write', 'Bash', 'Task', etc.
    target TEXT,                     -- file path or agent name
    action TEXT NOT NULL,            -- 'block', 'allow', 'nudge'
    reason TEXT,                     -- why blocked/nudged (NULL for allow)
    user_override INTEGER DEFAULT 0, -- 1 if user overrode a block
    project TEXT
);

CREATE INDEX idx_enforcement_session ON enforcement_log(session_id);
CREATE INDEX idx_enforcement_action ON enforcement_log(action);
CREATE INDEX idx_enforcement_layer ON enforcement_log(layer);
```

**Feedback Proposals:**

```sql
CREATE TABLE feedback_proposals (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    trigger_type TEXT NOT NULL,      -- 'user_override', 'nudge_ignored', 'user_feedback', 'gemini_audit'
    description TEXT NOT NULL,
    related_log_ids TEXT,            -- JSON array of enforcement_log IDs that triggered this
    status TEXT DEFAULT 'pending',   -- 'pending', 'approved', 'rejected'
    gemini_response TEXT,            -- Gemini's recommendation
    reviewed_at TIMESTAMP,
    applied INTEGER DEFAULT 0        -- 1 if change was applied
);

CREATE INDEX idx_proposals_status ON feedback_proposals(status);
```

**Nudge Tracking:**

```sql
CREATE TABLE nudge_state (
    nudge_type TEXT NOT NULL,        -- 'unrecorded_decision', 'long_session', 'unread_kickoff'
    session_id TEXT NOT NULL,
    last_fired TIMESTAMP,
    fire_count INTEGER DEFAULT 0,
    acted_on INTEGER DEFAULT 0,      -- 1 if CC responded to the nudge
    PRIMARY KEY (nudge_type, session_id)
);
```

### Table Count

| Database | Tables | Total |
|---|---|---|
| uru.db | 3 (enforcement_log, feedback_proposals, nudge_state) | 3 |

Three tables. All active. No dead infrastructure.

---

## 12. Bill of Materials

### Module Breakdown

| File | ~Lines | What |
|---|---|---|
| `hooks/session-start.sh` | ~60 | Initialize Uru state, inject workflow context into CC |
| `hooks/pre-tool-use.sh` | ~180 | Layer 0 blocklist + Layer 0.5 DB protection + Layer 1 gate checks |
| `hooks/post-tool-use.sh` | ~100 | Nudge checks (3 nudges), activity logging |
| `hooks/pre-compact.sh` | ~30 | Trigger Abzu pre-compact summary, log Uru state |
| `hooks/post-compact.sh` | ~40 | Re-inject enforcement context after compaction |
| `hooks/session-end.sh` | ~50 | Trigger Abzu session finalization, write enforcement summary |
| `gates/uru.py` | ~220 | DB queries for gate checks, nudge logic, proposal creation |
| `gates/layer0.py` | ~70 | Blocklist management + DB protection patterns (used by hooks) |
| `gates/feedback.py` | ~100 | Feedback proposal CRUD, Gemini review interface |
| `gates/schemas.py` | ~60 | uru.db table definitions |

**Total: ~910 lines**

### Compared to Current Enki Enforcement

| Current | Lines | Uru | Lines |
|---|---|---|---|
| ereshkigal.py | ~400 | Cut — pattern interception replaced by simple gate checks | — |
| enforcement.py | ~300 | Absorbed into uru.py (simplified) | — |
| violations.py | ~200 | Cut — 0 violations ever recorded | — |
| hooks/*.sh (5 scripts) | ~500 | Rewritten with Uru gate logic | ~410 |
| **Total** | **~1,400** | **Total** | **~910** |

35% smaller. Every line earns its place.

---

## 13. Anti-Patterns

### What Uru Avoids

| Anti-Pattern | Why It's Wrong | Uru's Answer |
|---|---|---|
| Pattern interception on every tool call | CC spends energy finding patterns to avoid rather than doing work | Binary state checks only |
| Behavioral analysis ("is CC rationalizing?") | Impossible to do reliably, false positives create friction | Check DB state, not intent |
| Many rules in CLAUDE.md | CC rationalizes past soft rules | Three hard blocks in hooks |
| CC-editable state files | CC sets its own phase, rubber-stamps gates | DB-backed state, human approval flags |
| Violations table that's never populated | Dead infrastructure | Enforcement log that logs every check (blocks AND allows) |
| "Skip workflow" verbal bypass | CC can prompt user to say the magic words | No verbal bypass exists |
| Self-assessment of enforcement quality | Fox reviewing its own fence | Gemini quarterly audit |
| Adversarial monitoring layer | Creates arms race between enforcer and enforced | Structural enforcement — the right path is the only path |

### From Current Enki

| What Failed | Why | Uru's Fix |
|---|---|---|
| Ereshkigal: 144 interceptions, 2 blocked | Pattern matching was too permissive — almost everything passed | Three targeted gates instead of broad pattern matching |
| Violations table: 0 rows | Nothing ever wrote to it | Enforcement log captures every check automatically |
| Feedback proposals: 0 rows | Manual-only, nobody called it | Auto-generated from overrides and ignored nudges |
| Hooks existed but enforcement was weak | Hooks did logging, not blocking | Hooks ARE the enforcement |

---

## 14. Glossary

| Term | Definition |
|---|---|
| **Uru** | Enki v3's enforcement system. Guardian that ensures Enki follows its own workflow. |
| **Hook** | Shell script that CC executes at specific lifecycle points (session-start, pre/post-tool-use, pre/post-compact, session-end). The mechanical enforcement layer. |
| **Hard block** | Gate check in pre-tool-use hook that prevents a tool call from executing. Cannot be bypassed by CC. |
| **Nudge** | Non-blocking reminder in post-tool-use hook. CC can ignore, but ignoring is logged. |
| **Layer 0** | Bash-level file protection. Fastest check. Protects enforcement infrastructure from CC edits. |
| **Gate** | A specific check within the pre-tool-use hook. Uru has three gates: goal, spec approval, phase. |
| **Feedback proposal** | A data row suggesting a rule change. Created automatically from overrides and ignored nudges. Reviewed by Gemini. |
| **User override** | Human directly setting DB state to bypass a gate. Logged, not an escape hatch — it's the human being final authority. |
| **Exempt files** | Files that bypass gate checks (docs, config, memory). Hardcoded in hook, not editable by CC. |

---

## Appendix A: Hook Execution Summary

| Hook | When | Uru Action | Abzu Action |
|---|---|---|---|
| `session-start.sh` | Session begins | Initialize uru.db session entry, inject enforcement context | Load persona, last session summary, relevant beads (tier-dependent) |
| `pre-tool-use.sh` | Before every tool call | Layer 0 check → Gate 1/2/3 checks → allow/block | Nothing |
| `post-tool-use.sh` | After every tool call | Nudge 1/2 checks, log to enforcement_log | Nothing |
| `pre-compact.sh` | Before compaction | Log current enforcement state | Heuristic + CC capture operational + conversational state |
| `post-compact.sh` | After compaction | Re-inject enforcement context | Re-inject persona + accumulated summaries + phase/tier/goal |
| `session-end.sh` | Session ends | Write session enforcement summary, generate feedback proposals if warranted | Finalize session summary, extract bead candidates, run decay |

### What Gets Injected Post-Compact

Compaction wipes CC's context. Post-compact re-injects:

**From Uru:**
- Current project, goal, phase, tier
- Active gates and what's required next in the workflow
- "You are in phase {phase}. Next step: {next_step}."

**From Abzu:**
- Persona
- Accumulated conversational + operational state from pre-compact summaries
- Phase/tier/goal

This is why CC continues following the workflow after compaction — the enforcement context is mechanically re-injected, not dependent on CC "remembering" the rules.

---

## Appendix B: Corrections to Other Specs

### For Pillar 1 (Abzu Spec)
- Gates → Abzu bridge: Uru reads wisdom.db for pattern checking — **removed**. Uru does binary state checks, not pattern analysis. Uru reads em.db and abzu.db for workflow state only.

### For Pillar 3 (EM Spec)
- EM drives the workflow forward. Uru enforces that the workflow IS followed. Belt and suspenders.
- EM functions set phase, create tasks, manage specs. Uru hooks verify these states exist before allowing code mutations.
- If EM is the engine, Uru is the seatbelt.

---

*End of Uru Gates Spec v1.0*
