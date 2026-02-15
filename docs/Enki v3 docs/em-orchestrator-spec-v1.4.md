# EM Orchestrator Spec — Enki v3 Pillar 3

> **Version**: 1.4
> **Date**: 2025-02-13
> **Status**: Final — All design decisions locked
> **Scope**: This is the Orchestration pillar (Pillar 3) of Enki v3. Memory (Pillar 1) and Gates (Pillar 2) have separate specs.
> **Audience**: Architect reads this and knows what to build. Dev reads the Implementation Spec derived from this.
> **v1.1 Changes**: Corrected spawn authority (Enki spawns PM and EM as peers, EM does not spawn PM). Updated memory bridge to reference Abzu spec. Updated agent role tables with spawn ownership.
> **v1.2 Changes**: Added `enki_quick` fast-path for Minimal tier, blind wall known limitation documentation, improved tier auto-detection heuristics (impact over volume).
> **v1.3 Changes**: CLAUDE.md as first-class project artifact. Full agent roster. Dev mandates SOLID/DRY/Clean Code. Customer presentation before ship. Docs agent throughout lifecycle.
> **v1.4 Changes**: Project Onboarding (new §3) — three entry points (greenfield, mid-design, brownfield), Codebase Profile protocol, user profile in wisdom.db, first-time user experience. Docs agent removed (14→13 agents) — responsibilities distributed to Dev, Reviewer, Architect, PM. Sprint-level Reviewer spawn. Prism as qualify-phase tool. Agent prompts Layer 0 protected (separate spec). Gemini review → cron report generator.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Principles](#architecture-principles)
3. [Project Onboarding](#project-onboarding)
4. [Tier System](#tier-system)
5. [PM Workflow](#pm-workflow)
6. [Two-Spec Model](#two-spec-model)
7. [CLAUDE.md Lifecycle](#claudemd-lifecycle)
8. [Agent Roles](#agent-roles)
9. [Mail System](#mail-system)
10. [Sprint/Task DAG](#sprinttask-dag)
11. [TDD Flow](#tdd-flow)
12. [Bug Lifecycle](#bug-lifecycle)
13. [Work Types](#work-types)
14. [Session Boundaries](#session-boundaries)
15. [Orchestration → Memory Bridge](#orchestration--memory-bridge)
16. [Yggdrasil Integration](#yggdrasil-integration)
17. [Status Updates](#status-updates)
18. [Enforcement Strategy](#enforcement-strategy)
19. [Nelson Additions](#nelson-additions)
20. [Data Schemas](#data-schemas)
21. [Bill of Materials](#bill-of-materials)
22. [Repos Referenced](#repos-referenced)
23. [Glossary](#glossary)

---

## 1. Overview

The EM (Engineering Manager) Orchestrator is Enki v3's multi-agent coordination system. It manages the full lifecycle of software engineering work — from user idea through PM intake, spec creation, debate, planning, CLAUDE.md creation, parallel development and testing, validation, review, deployment, customer presentation, and project closure.

EM is the central relay for execution. Agents never talk directly to each other. All communication flows through EM via a mail system built into Enki's SQLite. EM spawns execution agents (Dev, QA, Validator, Reviewer, InfoSec, UI/UX, DevOps, Performance, Researcher), parses their output, routes messages, tracks progress, and escalates to the human when needed. 13 agents total — PM, EM, Architect, DBA (planning), Dev, QA, UI/UX, Validator, Reviewer, InfoSec, DevOps, Performance, Researcher (execution).

**Spawn authority correction**: EM does not spawn PM. Enki spawns both PM and EM as peer departments — like a real SE org where PM and Engineering are separate departments that work together but don't report to each other. PM hands off to EM via kickoff mail. See Section 8 for full spawn authority.

The orchestrator operates at three tiers (Minimal, Standard, Full) that control both how much context agents receive and how many agents participate in the workflow. The tier system replaces the previously proposed SDD (Spec-Driven Development) path.

---

## 2. Architecture Principles

**EM has no opinions.** EM brokers, routes, spawns execution agents, tracks. It does not modify specs, make technical decisions, or override agent output. If something needs a decision, EM routes it to the right agent or escalates to the human.

**Mail is the single source of truth.** Every agent interaction, decision, blocker, and status update is a mail message in SQLite. The mail thread IS project memory. Mid-flight resume reads the thread. Audit reads the thread. Status reports read the thread.

**Structure enforces behavior.** The orchestrator makes the right path the easiest path. CC doesn't "decide" to follow the workflow — orchestrator functions only return the next valid action. No instructions to follow, just functions to call.

**Agents are stateless.** Each agent is spawned fresh via CC's Task tool, given context from the mail thread, does its work, returns output, exits. No long-running agents. Mail provides continuity.

**Fail closed.** If EM can't parse agent output, it retries (max 3 attempts) then escalates to human. If tier can't be determined, it defaults to Full. If debate cycles exhaust, it escalates. Never silently proceed with incomplete data.

**Human is final authority.** Every major checkpoint requires human approval. PM learning loop may reduce approval frequency over time, but human always has veto.

---

## 3. Project Onboarding

Enki is a full SE firm that takes on clients. Clients arrive in different states — some with nothing but an idea, some with design artifacts, some with a live codebase that needs work. Enki must handle all three without forcing the client to understand Enki's internal workflow.

### First-Time User

When Enki detects no user profile in wisdom.db (first session ever, or first session on a new machine):

```
No user profile detected
    → Enki introduces itself:
        "I'm Enki, your software engineering team.
         Tell me what you want to build or what you need changed,
         and I'll handle the rest — planning, coding, testing, deployment."
    → Enki asks:
        1. "Are we starting something new, or working on an existing codebase?"
        2. "How do you want updates? Every sprint, daily, or only when I need you?"
    → Stores answers in wisdom.db user_profile
    → Proceeds to appropriate entry point
```

**What Enki does NOT do on first contact:**
- Tutorial. No "here's how tiers work" or "here's how I use agents."
- Long questionnaire. Two questions max. Everything else is learned from behavior.
- Demand deploy config upfront. That comes when it's relevant (ship phase).

**What gets captured over time (PM learning loop + beads):**
- Approval patterns: rubber-stamp or scrutinize?
- Communication preference: terse or detailed?
- Deploy targets: discovered at first ship, remembered for next project
- Coding conventions: extracted from existing codebases, applied to new ones
- Tech stack preferences: inferred from project history

### User Profile

Persistent across all projects. Stored in wisdom.db.

```sql
CREATE TABLE user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'explicit' (user told us), 'inferred' (derived from behavior), 'codebase' (extracted from project)
    confidence REAL DEFAULT 1.0,   -- 1.0 = user stated, 0.5 = inferred, decays if contradicted
    project_id TEXT,               -- Which project this was learned from (NULL if global)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Rules:**
- Explicit beats inferred. If user says "I want daily updates" and behavior suggests they ignore them, explicit wins.
- Codebase-derived values are per-project but promote to global if consistent across 3+ projects.
- PM reads user_profile at intake. Architect reads at CLAUDE.md generation. DevOps reads at deploy.
- User can see and edit their profile: `enki profile` CLI command.

### Three Entry Points

Every project starts with one of three entry points. PM detects the entry point at intake. Human confirms.

#### Entry Point 1: Greenfield (New Project)

**Signal**: No existing repo, or empty repo, or "I want to build X from scratch."

**Flow**: This is the full flow already defined in the spec. No changes.

```
PM intake (full) → Product Spec → debate → Architect + DBA → 
Implementation Spec → CLAUDE.md → EM builds DAG → execute → ship
```

#### Entry Point 2: Mid-Design (Specs/Artifacts Exist, No Code)

**Signal**: Customer provides design artifacts — PRD, wireframes, architecture doc, API spec, etc. But no codebase yet.

**Flow**:

```
Customer provides design artifacts
    ↓
PM reviews artifacts against intake checklist
    ↓
Gaps found?
    YES → PM asks targeted follow-ups (not full intake — just gaps)
    NO  → PM adopts artifacts as Product Spec (or maps to format)
    ↓
Customer approves Product Spec (may be fast — "yes, that's what I gave you")
    ↓
Architect + DBA review → Implementation Spec (normal)
    ↓
Normal flow from here
```

**What changes from greenfield:**
- PM intake is validation, not extraction. PM reads existing artifacts and checks its intake checklist against them.
- Debate round may be shorter if design artifacts already address feasibility and edge cases.
- Architect has richer input — existing architecture docs constrain the Implementation Spec.

#### Entry Point 3: Brownfield (Existing Codebase)

**Signal**: Customer provides a repo path, or "I have a project and I need X added/changed/fixed."

**Flow**:

```
Customer provides existing codebase + request
    ↓
PM intake (scoped — "what do you want added/changed?")
    ↓
Researcher runs Codebase Profile (see below)
    ↓
PM reads profile → completes Product Spec with codebase context
    ↓
Architect reads profile → writes Implementation Spec 
    (constrained by existing architecture)
    ↓
Architect reads existing CLAUDE.md (if any) 
    OR generates CLAUDE.md from Codebase Profile
    ↓
Normal flow from here (with brownfield adaptations)
```

**Brownfield adaptations:**

| Concern | Greenfield | Brownfield |
|---|---|---|
| First step after intake | Architect plans freely | Researcher maps codebase FIRST |
| CLAUDE.md | Created from scratch | Read existing OR generate from Codebase Profile |
| Implementation Spec | Free architecture choice | Constrained by existing patterns, deps, conventions |
| Dev context | Just the spec + CLAUDE.md | Spec + CLAUDE.md + existing code to integrate with |
| QA context | Just the spec | Spec + existing test patterns to match |
| Regression suite | Empty, built from scratch | Existing test suite IS the regression baseline from day 1 |
| DevOps | Configure from scratch | Read existing CI/CD config, extend don't replace |
| Reviewer | Review against spec + CLAUDE.md | Review against spec + CLAUDE.md + existing conventions |

**Critical rule: Researcher runs BEFORE Architect plans.** Non-negotiable for brownfield.

### Codebase Profile

When Researcher analyzes an existing codebase, it produces a structured **Codebase Profile**. This is a protocol with defined output, not ad-hoc file reading.

**Researcher receives**: Repo path + customer's request (for relevance scoping)

**Researcher produces**:

```json
{
  "profile_version": 1,
  "project": {
    "name": "derived-from-package-json-or-dir-name",
    "primary_language": "typescript",
    "languages": ["typescript", "python", "sql"],
    "frameworks": ["express", "react", "prisma"],
    "package_managers": ["npm", "pip"],
    "monorepo": false
  },
  "structure": {
    "source_dirs": ["src/", "api/"],
    "test_dirs": ["tests/", "__tests__/"],
    "config_dir": ".config/",
    "ci_config": ".github/workflows/ci.yml",
    "docker": true
  },
  "conventions": {
    "naming": "camelCase for functions, PascalCase for classes",
    "import_style": "absolute imports from src/",
    "error_handling": "custom AppError class hierarchy",
    "linter": "eslint with airbnb config",
    "formatter": "prettier",
    "test_framework": "jest",
    "test_pattern": "*.test.ts co-located with source"
  },
  "architecture": {
    "pattern": "layered (routes → controllers → services → repositories)",
    "entry_point": "src/index.ts",
    "key_modules": [
      {"path": "src/auth/", "purpose": "Authentication + authorization"},
      {"path": "src/api/", "purpose": "REST API routes and controllers"}
    ],
    "data_flow": "HTTP → Express middleware → controller → service → Prisma → PostgreSQL",
    "external_deps": ["PostgreSQL", "Redis", "SendGrid"]
  },
  "testing": {
    "framework": "jest",
    "total_tests": 147,
    "test_dirs": ["tests/unit/", "tests/integration/"],
    "e2e_exists": false
  },
  "ci_cd": {
    "provider": "github_actions",
    "pipelines": ["ci.yml", "deploy.yml"],
    "deploy_method": "docker push to ECR + ECS deploy",
    "environments": ["staging", "production"]
  },
  "claude_md_exists": false,
  "relevant_to_request": {
    "files_likely_touched": ["src/auth/", "src/api/routes/user.ts"],
    "existing_patterns_to_follow": "Auth uses passport.js with JWT strategy",
    "risks": "Auth module has no integration tests"
  }
}
```

**Consumers:**

| Consumer | Reads | Uses For |
|---|---|---|
| **PM** | project, relevant_to_request | Scoping the Product Spec |
| **Architect** | Everything | Implementation Spec constrained by existing architecture |
| **Architect** | conventions, structure | Generating CLAUDE.md if none exists |
| **Dev** | conventions, architecture | Following existing patterns |
| **QA** | testing | Matching existing test framework and patterns |
| **DevOps** | ci_cd | Extending existing pipeline |
| **Reviewer** | conventions | Reviewing against codebase standards |
| **User Profile** | conventions, ci_cd | Populating codebase-derived preferences |

**Researcher constraints:**
- Read-only. Never modifies files.
- Scoped by customer's request. Don't map irrelevant modules.
- Time-bounded (configurable, default 5 minutes). Partial profile with noted gaps is fine.
- Output stored in em.db as mail message (from: Researcher, to: Architect).

### Entry Point Detection

```python
def detect_entry_point(signals: dict) -> str:
    if signals.get("existing_repo") and has_source_files(signals["repo_path"]):
        return "brownfield"
    if signals.get("design_artifacts"):
        return "mid_design"
    return "greenfield"
```

Human confirms. If PM gets it wrong, human corrects.

### Entry Point × Tier Matrix

Entry points and tiers are independent axes:

| | Minimal | Standard | Full |
|---|---|---|---|
| **Greenfield** | Rare | Small new tool/script | Full product |
| **Mid-Design** | Never | Feature with existing spec | Full product with PRD |
| **Brownfield** | Bug fix in existing codebase | Feature in existing product | Major refactor/new system |

---

## 4. Tier System

The tier system controls both context loading (what agents know) and orchestration scale (how many agents participate). One system, dual purpose. Replaces the SDD concept entirely.

### Tier Definitions

| Tier | Context Loaded | Orchestration Scale | When |
|---|---|---|---|
| **Minimal** | Phase + goal | No DAG, no sprints. Single cycle: Dev → QA → done. No Architect, no Validator. | Config changes, typos, bug fixes, small refactors |
| **Standard** | + Spec, tasks | Single sprint, task-level DAG, parallel QA+Dev, Validators. No PM/Architect planning phase. Red-cell skipped. | Medium features, larger refactors, enhancements |
| **Full** | + Beads, history | Multi-sprint, two-spec (PM → Architect), full planning + execution, all agents. Red-cell review included. | Substantial features, large refactors, new systems |
| **Auto** | Detect | EM classifies based on scope, file count, task complexity | Default mode |

### Tier Selection Flow

1. **Auto** — EM makes initial tier guess from scope signals (file count, task complexity, phase)
2. **Architect proposes** — After reviewing spec, Architect recommends a tier (may see complexity EM missed)
3. **EM reconciles** — If Architect proposes higher tier than Auto, escalate. Tier can only go UP automatically, never down without human approval. Prevents gaming.
4. **Human overrides** — Final say at approval time. Can force any tier.

### Tier-Specific Flows

**Minimal:**
```
User describes change
    → EM assigns to Dev
    → Dev implements
    → QA writes tests from description, runs against code
    → Done
```

**Minimal Fast-Path (v1.1):** For trivial changes (typo fixes, config edits, 1-character fixes), the full Minimal flow (set goal → set phase → assign Dev → assign QA) is too much overhead. `enki_quick` combines goal + triage + phase=implement in one command:

```
enki_quick "fix typo in auth.py line 42"
    → Creates goal (Minimal tier, auto-detected)
    → Sets phase to implement immediately
    → Uru Gate 1 and Gate 3 satisfied
    → CC can edit the file
    → On completion, session-end extraction captures the change
```

`enki_quick` is Minimal tier ONLY. Standard and Full always require the full workflow. Gate 2 (spec approval) is not bypassed because Minimal tier doesn't require spec approval.

**Standard:**
```
User provides spec (or PM writes one)
    → EM builds single-sprint DAG
    → Per task: QA writes tests ∥ Dev implements (blind wall)
    → Validator checks both
    → Reviewer reviews
    → Done
```

**Full:**
```
User has idea
    → PM intake Q&A
    → PM writes Product Spec
    → Debate round (Technical Feasibility, Devil's Advocate, Historical Context)
    → Human approves Product Spec
    → Architect + DBA write Implementation Spec
    → Red-cell review (adversarial)
    → Human approves Implementation Spec
    → EM builds multi-sprint DAG
    → Per sprint, per task: full TDD cycle
    → Project closure via PM
```

### Auto-Detection Logic

```python
def detect_tier(scope_signals):
    # Impact-based heuristics, not just volume (v1.1)
    # A 50-file typo fix is Minimal; a 2-file auth rewrite is Standard
    if scope_signals.is_config_only or scope_signals.work_type in ('typo', 'config'):
        return Tier.MINIMAL
    if scope_signals.file_count <= 2 and scope_signals.estimated_tasks <= 1:
        return Tier.MINIMAL
    elif scope_signals.file_count <= 10 and scope_signals.estimated_tasks <= 5:
        return Tier.STANDARD
    else:
        return Tier.FULL
```

**v1.1 note:** File count alone is a weak heuristic. A repo-wide rename touching 50 files is Minimal; a 2-file auth change is Standard. Auto-detection should weight impact/complexity (new patterns, security-sensitive, cross-module) over volume. Implementation should refine these heuristics based on observed misclassifications. When confidence is low, escalate to human for tier selection rather than defaulting to Full.

EM can also escalate tier mid-execution if scope creep is detected (new files touched, tasks multiplying beyond original estimate).

---

## 5. PM Workflow

PM owns the project from intake to closure. PM is spawned by Enki at checkpoints, not long-running. PM and EM are peer departments — neither spawns nor controls the other. They communicate through mail. Mail thread + Yggdrasil provide continuity between PM spawns.

### PM Intake (Full Tier Only)

**Hybrid approach**: Freeform conversation with the user, validated against a checklist.

**PM Intake Checklist:**
- What is the goal/outcome?
- Who is it for (user persona, audience)?
- What are the constraints (tech, time, budget)?
- What are the success criteria?
- What's in scope / out of scope?
- Any dependencies on existing systems?
- Any known risks?

PM converses naturally, asks follow-ups based on answers. Before declaring intake complete, PM validates all checklist items are covered. If gaps remain, PM asks targeted follow-ups.

**Completion**: PM gives a summary of what it understood. User confirms "that's everything." PM begins writing the Product Spec.

### Debate Round

After PM writes Product Spec, it goes through sequential review by three perspectives:

| Perspective | Focus |
|---|---|
| **Technical Feasibility** | Can this be built? Architecture concerns, dependencies, effort |
| **Devil's Advocate** | What's wrong? Missing edge cases, flawed assumptions, scope risks |
| **Historical Context (Enki)** | Have we tried this before? Conflicts with existing systems? Past lessons? |

**Process:**
- All three are CC subagents with different prompts
- Sequential review (not parallel)
- Each reviewer returns structured feedback
- PM reconciles all feedback, revises spec
- Revised spec goes back for re-review
- **Max 2 debate cycles** before HITL escalation
- The first perspective (Technical Feasibility) can be Architect, CTO, or senior Dev — PM or EM assigns based on project scope

### PM Decision Learning

PM can make decisions autonomously over time:

1. **Start**: PM proposes change → sends to human → waits for approval
2. **Human responds**: approve / reject / modify
3. **PM logs**: decision pattern (what was proposed, what human decided, context)
4. **Over time**: PM recognizes patterns → makes similar decisions autonomously
5. **PM notifies human**: "I've made this decision based on past approvals"
6. **Human can override**: right (confidence grows) or wrong (PM reverts, asks for correct decision, logs correction)

Human always has veto. PM gets smarter about what the human would approve.

### PM Spawn Triggers

Enki spawns PM when:
- Project intake (user has new idea)
- Spec debate needed
- Kickoff with Architect/EM/DBA
- Sprint completion (status update)
- Bug filed (notification)
- Blocker/HITL escalation
- Change request from user
- Scheduled cadence (configured interval)
- Project closure

PM and EM are independent — Enki decides when each is needed. Between spawns, PM is "off" — like a real PM managing multiple projects.

### PM at Project Close

- **Customer Presentation** (Full tier): PM presents the completed product to the customer, comparing deliverables against the original Product Spec acceptance criteria. This is the formal acceptance gate — customer confirms the product meets requirements, or raises issues that become bugs/change requests.
- Writes project summary (delivered vs planned, descoped items, tech debt, lessons learned)
- Updates Yggdrasil with final status
- Triggers memory bridge (bead extraction)
- Sends final status to human

### PM and Change Requests

PM owns ALL change requests, regardless of size:
- User wants to add/change scope mid-project
- Dev discovers spec needs revision
- Reviewer finds architectural concern requiring spec update

Even small changes go through PM. PM may approve without full debate (based on learning loop) but always logs the change.

---

## 6. Two-Spec Model

Two separate specifications with a negotiation loop between them. This applies at **Full tier only**. Standard tier uses a single spec. Minimal tier uses a description only.

### Product Spec (PM Owns)

- What the feature is
- User stories / acceptance criteria
- Design constraints, business rules, UX requirements
- Does NOT contain: tech stack, file names, APIs, databases

### Implementation Spec (Architect Owns)

- Tech stack, approach, files to modify
- Data model (with DBA input)
- API contracts, dependencies
- Risks and mitigation
- Sprint breakdown with task list
- Does NOT contain: business justification, user stories, design rationale

### Negotiation Flow

```
PM writes Product Spec → human approves
    → Architect + DBA review Product Spec
    → Feasible?
        YES → Architect writes Implementation Spec
            → Red-cell review (Full tier)
            → HITL approves
            → Both specs locked
        NO  → Architect raises concerns (blocker/risk/suggestion)
            → EM brokers → PM updates Product Spec
            → Architect re-reviews (max cycles before HITL escalation)
```

- **Blockers** must be resolved before proceeding
- **Risks** are noted in Implementation Spec with mitigation plans
- **Suggestions** are PM's call to accept or reject

Once both specs are locked (human approved), they become immutable. Any changes require a formal change request through PM.

---

## 7. CLAUDE.md — Project Constitution

CLAUDE.md is the most important file in any project. It is the first file CC reads on every session and governs how all agents interact with the codebase. Without it, every agent starts blind — guessing at structure, conventions, and constraints.

### Why CLAUDE.md Is First-Class

CLAUDE.md is not optional documentation. It is a **project artifact** created during planning, before any code is written. It is the project's constitution — the onboarding document that turns a general-purpose LLM into a project-aware teammate.

### Who Creates It

**Architect creates CLAUDE.md** after the Implementation Spec is approved, before EM builds the DAG.

Inputs:
- **From PM / Product Spec**: WHY — project purpose, goals, what problem it solves
- **From Architect / Implementation Spec**: WHAT and HOW — tech stack, project structure, architecture decisions, coding conventions, build/test/deploy commands
- **From Customer (human)**: Any custom instructions the customer wants enforced — style preferences, forbidden patterns, specific tooling, naming conventions, domain terminology
- **From DBA**: Data model conventions, migration patterns

### CLAUDE.md Structure (WHY / WHAT / HOW Framework)

```markdown
# Project: [Name]

[One-line description of what this project is and does]

## WHY
- Purpose and goals of the project
- Who it's for (user persona / audience)
- What problem it solves

## WHAT
- Tech stack with specific versions
- Project structure (directory map with purpose of each)
- Key architectural decisions and the reasoning behind them
- Domain terminology that maps to code entities

## HOW
- Build command: `...`
- Test command: `...`
- Lint command: `...`
- Run command: `...`
- Deploy command: `...`
- Git workflow (branch naming, commit message format)
- Code style conventions (beyond what linters enforce)

## CONVENTIONS
- Patterns used AND patterns explicitly avoided (with alternatives)
- Error handling approach
- Logging conventions
- API design patterns

## CONSTRAINTS
- Files/directories that must never be modified directly
- Protected paths and why
- Customer-specific instructions
- Enki governance hooks (reference only — do not detail enforcement)

## PROGRESSIVE DISCLOSURE
- For [topic], see `docs/[topic].md`
- For test patterns, see `tests/README.md`
- For API documentation, see `docs/api.md`
```

### Best Practices (Non-Negotiable)

1. **Concise.** Under 300 lines. Shorter is better. If Claude already does something correctly without the instruction, delete it.
2. **Progressive disclosure.** Don't put everything in CLAUDE.md. Tell CC *where to find* detailed information, not *what* the information is. Link to docs, don't inline them.
3. **No linter rules.** Use actual linters and formatters (.editorconfig, eslint, ruff). CLAUDE.md is not a linter.
4. **Actionable commands.** Every build/test/lint/deploy command must be copy-pasteable. No "run the test suite" — specify the exact command.
5. **Version-controlled.** CLAUDE.md is committed to git. It's a shared team artifact, not a personal note.
6. **Living document.** Architect updates it as the project evolves. New patterns discovered → update CLAUDE.md. Wrong assumption → fix it. But don't let it bloat — prune regularly.
7. **No secrets.** No API keys, connection strings, or credentials. Ever.

### CLAUDE.md in the Orchestration Flow

```
Implementation Spec approved
    → Architect creates CLAUDE.md (using inputs from PM, customer, DBA)
    → Customer reviews CLAUDE.md (can add custom instructions)
    → CLAUDE.md committed to project repo root
    → EM reads CLAUDE.md before building DAG
    → All agents receive CLAUDE.md as part of their context
    → Architect updates CLAUDE.md per sprint as project evolves
```

### Tier Applicability

| Tier | CLAUDE.md |
|---|---|
| **Minimal** | Auto-generated from project type registry (bare minimum: stack, commands, structure). Customer can augment. |
| **Standard** | Architect creates from spec. Customer can augment. |
| **Full** | Architect creates from spec + PM input + customer input. Full WHY/WHAT/HOW. Customer review required. |

### Enki-Specific CLAUDE.md Additions

For projects managed by Enki, CLAUDE.md includes a governance section:

```markdown
## ENKI GOVERNANCE
- This project is managed by Enki v3
- All code changes go through the orchestration pipeline
- See `.enki/project.toml` for project type and tier configuration
- Hooks are active — do not bypass or modify hook scripts
```

This section is minimal and referential. It tells CC that Enki is in charge without detailing enforcement mechanisms (fox problem — CC doesn't need to know how the cage works, just that it exists).

---

## 8. Agent Roles

### Planning-Phase Agents

| Role | Spawned By | Does | Doesn't |
|---|---|---|---|
| **PM** | Enki | Writes product spec, debates, reconciles feedback, tracks project, manages changes, presents to customer at project close | Choose tech stack, make technical decisions |
| **Architect** | Enki (at PM's request via mail) | Reviews product spec, raises concerns, writes implementation spec, creates CLAUDE.md, proposes tier | Approve own spec (HITL does), make business decisions |
| **DBA** | Enki (at PM's request via mail) | Contributes data model to implementation spec, reviews CLAUDE.md data conventions | Exist as separate execution agent |

### Execution-Phase Agents

| Role | Spawned By | Does | Doesn't |
|---|---|---|---|
| **Dev** | EM | Implements from implementation spec following SOLID, DRY, and Clean Code principles (see Dev Coding Mandate below) | Deviate from spec, see tests, make spec decisions, write "clever" code that trades readability for brevity |
| **QA** | EM | Writes tests from spec, runs tests against implementation | See implementation when writing tests |
| **Validator** | EM | Checks output against specs (blind), adversarial review at Full tier | See dev's reasoning, modify code |
| **Reviewer** | EM | Code review for quality, patterns, maintainability, SOLID/DRY adherence | Block on style alone |
| **InfoSec** | EM | Security review when auth/data/network changes detected | Review non-security code |
| **UI/UX** | EM | Frontend design, component structure, accessibility (WCAG), responsive layout | Backend logic, API design, database work |
| **DevOps** | EM | CI/CD pipeline setup, deployment config per user preferences, infrastructure-as-code | Application code, business logic |
| **Performance** | EM | Profiling, benchmark establishment, optimization recommendations, regression detection | Implementation of fixes (Dev does that) |
| **Researcher** | EM | Read-only codebase investigation, exploratory analysis, "how does X work" answers | Writing code, modifying files, making decisions |

### Coordination

| Role | Spawned By | Does | Doesn't |
|---|---|---|---|
| **EM** | Enki | Brokers negotiation, builds DAG, spawns execution agents, routes mail, tracks progress | Have opinions, modify specs, make decisions, spawn PM |

### Dev Coding Mandate

Dev agent's system prompt includes these non-negotiable principles:

**SOLID Principles:**
- **Single Responsibility**: Each class/module has one reason to change
- **Open/Closed**: Open for extension, closed for modification
- **Liskov Substitution**: Subtypes must be substitutable for their base types
- **Interface Segregation**: No client should depend on methods it doesn't use
- **Dependency Inversion**: Depend on abstractions, not concretions

**DRY (Don't Repeat Yourself):**
- Extract common logic into shared utilities
- No copy-paste code — if it appears twice, abstract it
- Configuration over hardcoding

**Clean Code:**
- Meaningful, descriptive names (no single-letter variables except loop counters)
- Small functions — each does one thing
- Single level of abstraction per function
- Proper error handling — no swallowed exceptions, no bare except
- Comments explain WHY, not WHAT (code should be self-documenting for WHAT)
- Consistent formatting per project's linter/formatter config

**Project-Specific:** Dev also receives CLAUDE.md conventions and follows them. Project conventions override general principles where they conflict (e.g., if CLAUDE.md says "use functional style, no classes" that overrides SOLID class-based patterns).

### Conditional Agents

Not every project needs every agent. EM spawns conditionally:

| Agent | Spawned When |
|---|---|
| **UI/UX** | Project has frontend components (detected from project type, Codebase Profile frameworks, or spec mentions UI/frontend/CSS/components) |
| **DevOps** | Ship phase — always spawned for qualify/deploy/verify. Also spawned if CI/CD setup needed during plan phase |
| **Performance** | Project type includes benchmarks, or spec mentions performance requirements, or Architect flags it |
| **Researcher** | On-demand — EM spawns when any agent raises "I need to understand how X works" in their output. Always for brownfield entry point (Codebase Profile). |
| **InfoSec** | Auth, data handling, network, encryption, or user input processing detected in task scope |

### Documentation Responsibilities (Distributed)

No dedicated Docs agent. Documentation is distributed across existing agents:

| Documentation Type | Owner | When |
|---|---|---|
| Inline docstrings (public functions, modules) | Dev | During implementation — mandated by coding standards |
| Documentation quality check | Reviewer | During code review — violations are P2 bugs |
| README, CLAUDE.md | Architect | Planning phase, updated per sprint |
| User guide, changelog | PM | Project closure |
| API documentation | Dev | Generated from code annotations (OpenAPI, JSDoc) |
| Sprint summaries | PM | Sprint completion |

### Spawn Authority Summary

| Agent | Spawned By |
|---|---|
| PM | Enki |
| EM | Enki |
| Architect, DBA | Enki (at PM's request) |
| Dev, QA, Validator, Reviewer | EM (always, per task) |
| InfoSec, UI/UX, DevOps, Performance, Researcher | EM (conditional, per above) |

### Agent as Subagent

All agents are CC subagents spawned via the Task tool. They:
- Receive context from EM (filtered mail thread + relevant specs + CLAUDE.md)
- Execute their task
- Return structured JSON output
- Exit

They cannot call MCP tools, cannot write to mail directly, cannot interact with other agents. EM handles all routing.

**Context filtering per agent:**

| Agent | Receives | Does NOT Receive |
|---|---|---|
| Dev | Implementation Spec, CLAUDE.md, Architect plan, relevant mail (no QA messages) | QA tests, QA output, Validator feedback on tests |
| QA | Product Spec + Implementation Spec (acceptance criteria), CLAUDE.md | Dev code, Dev output, Dev decisions |
| UI/UX | Product Spec (UX requirements), CLAUDE.md, design constraints | Backend implementation details |
| Reviewer (task) | Dev's output files, Implementation Spec, CLAUDE.md | QA output, Validator feedback |
| Reviewer (sprint) | All files modified across sprint, Implementation Spec, CLAUDE.md | Individual agent reasoning |
| Researcher | Relevant specs, CLAUDE.md, the specific question asked, Codebase Profile (brownfield) | Agent outputs unrelated to the question |
| All others | As appropriate per blind wall rules | As appropriate per blind wall rules |

---

## 9. Mail System

### Design Principles

- Built into Enki's SQLite — no external dependencies
- Single `mail_messages` table, query by `to_agent` for inboxes
- Attachments inline in message body
- Threads represent project structure (project → sprint → task → HITL)
- Mail is the single source of truth for project state

### Message Status Flow

```
unread → read → acknowledged → assigned(agent) → resolved
```

- **unread**: Message delivered, not yet processed
- **read**: EM or agent has seen it
- **acknowledged**: EM has processed and determined next action
- **assigned(agent)**: EM has routed to a specific agent for action
- **resolved**: Action completed, outcome recorded

### Thread Hierarchy

```
Project Thread (top-level)
├── Planning Thread (PM ↔ Architect negotiation)
├── CLAUDE.md Thread (Architect ↔ PM ↔ Customer)
├── Sprint-1 Thread
│   ├── Task-A Thread (EM ↔ QA ↔ Dev ↔ Validator ↔ Reviewer)
│   ├── Task-B Thread
│   └── EM: "Sprint 1 complete"
├── Sprint-2 Thread
│   └── ...
├── Release Thread (DevOps updates)
├── Release Thread (DevOps ↔ EM for qualify/deploy/verify)
├── HITL Thread (EM ↔ User escalations)
└── Change Request Thread (PM ↔ User)
```

### Retention Policy

- **Active projects**: All messages retained
- **Completed projects**: Archived after 30 days
- **Old threads**: Threads older than 10 days considered old (eligible for archival)
- **Archived messages**: Summarized into beads (via memory bridge), then moved to archive table
- **Archived data**: Still searchable if needed

### User Interaction with Mail

**Visibility**: User sees everything. Access via:
- CLI: `enki mail inbox`, `enki mail thread Sprint-1/Task-A`, `enki mail status`
- API/web viewer (future)

**User is NOT cc'd on every routine message.** EM only mails user for:
- HITL escalations (blockers, max cycles hit)
- Sprint completion summaries
- Approval requests (spec sign-off, task sign-off)
- Status updates (per configured triggers)

**User can send mail (Human Overseer pattern):**

| User wants to... | How |
|---|---|
| Answer escalation | Reply to EM's HITL message |
| Change direction mid-sprint | Send mail to project thread — EM picks it up |
| Give feedback on completed work | Send mail to task thread — EM routes to Dev |
| Pause execution | Send mail — EM pauses all spawns |

User messages have highest priority. EM processes them before any agent output.

### Mail Routing

EM parses agent output and routes based on `TO:` markers in structured JSON:

```json
{
  "status": "DONE",
  "completed_work": "Implemented auth middleware...",
  "files_modified": ["src/auth.py", "src/middleware.py"],
  "decisions": [
    {"decision": "Used JWT over session tokens", "reasoning": "Stateless, scalable"}
  ],
  "messages": [
    {"to": "QA", "content": "Auth approach uses JWT, not sessions. Test accordingly."},
    {"to": "Architect", "content": "Data model needed one extra table for refresh tokens."}
  ],
  "blockers": []
}
```

EM extracts the `messages` array and routes each to the appropriate agent's inbox. Role-based routing (`TO:QA`, `TO:Dev`, `TO:Validator`) — EM resolves role to the correct agent instance based on context.

### Output Parsing Failure

If agent output doesn't follow JSON convention:
1. First attempt: EM asks agent to retry with proper structure
2. Second attempt: EM asks again with explicit template
3. Third attempt: HITL escalation — human reviews raw output

Max 3 total attempts (2 retries) before escalation.

---

## 10. Sprint/Task DAG

### Two-Level DAG

| Level | What | Who Creates |
|---|---|---|
| **Sprint level** | Which sprints, dependencies between them | EM from Implementation Spec |
| **Task level** | QA/Dev/Validator/Reviewer cycle per task | EM generates per task |

### Architect Output Format

Architect uses **strict table format** for sprint/task breakdown in the Implementation Spec:

```
## Sprint 1: Core Auth
| Task | Files | Dependencies | Estimated Complexity |
|---|---|---|---|
| A: JWT middleware | src/auth.py, src/middleware.py | None | Medium |
| B: User model | src/models/user.py, migrations/ | None | Low |
| C: Login endpoint | src/routes/auth.py | A, B | Medium |

## Sprint 2: Protected Routes
| Task | Files | Dependencies | Estimated Complexity |
|---|---|---|---|
| D: Route guards | src/middleware/guards.py | Sprint 1 | Low |
| E: Role-based access | src/models/roles.py, src/middleware/rbac.py | D | High |
```

### EM Validation

After parsing the DAG from Architect's output, EM validates:
- **Circular dependencies**: No task depends on itself or creates cycles
- **File existence**: Referenced files exist or are marked as new
- **Sprint ordering**: Sprint dependencies are acyclic
- **File overlap**: No two tasks in the same sprint touch the same file without explicit dependency
- **Task granularity**: EM pushes back on Architect if tasks are too big or too small

### File Overlap Rules

1. **Preferred**: Architect splits work so no two tasks touch the same file
2. **Fallback**: If overlap is unavoidable, EM auto-creates a dependency between overlapping tasks (later task waits for earlier task to complete)
3. File overlap between sprints is expected (Sprint 2 builds on Sprint 1's files)

### Parallelism

- **Max 2 tasks in parallel** (configurable via `MAX_PARALLEL_TASKS`)
- Tasks within a sprint can run simultaneously if independent (no shared files, no dependency)
- Within a single task, QA and Dev are always parallel (blind wall)
- Max 4 concurrent subagents during QA+Dev phase (2 tasks × 2 agents each)

### Task Scheduling

```python
MAX_PARALLEL_TASKS = 2  # configurable

def get_next_tasks_to_spawn(sprint):
    wave = get_current_wave(sprint)  # tasks with all deps met
    running = get_running_tasks(sprint)
    available_slots = MAX_PARALLEL_TASKS - len(running)
    if available_slots <= 0:
        return []  # wait for something to complete
    ready = [t for t in wave if t.status == "pending"]
    return ready[:available_slots]
```

CC calls `get_next_actions()`, gets back a list, spawns those. If list is empty, waits. Structure enforces execution order — no instructions needed.

---

## 11. TDD Flow

### Corrected Understanding

**WRONG (classical Kent Beck TDD):** QA writes tests → Dev writes minimum code to pass tests.

**CORRECT (Enki TDD):**
1. QA writes tests from spec (defines expected behavior)
2. Dev implements actual feature from spec (NOT "to make tests pass")
3. QA runs tests against implementation to verify

Dev's input is spec + architect plan, NOT test files. Dev builds real implementation. Tests serve as verification, not driving force.

### Blind Wall

EM enforces the blind wall through context filtering:
- **Dev receives**: Implementation Spec, Architect's plan, relevant mail thread (filtered — no QA messages)
- **QA receives**: Product Spec, Implementation Spec (acceptance criteria), relevant mail thread (filtered — no Dev messages)
- Neither sees the other's work until QA runs tests against Dev's code

**Known limitation (v1.1):** The blind wall is prompt-level isolation, not structural isolation. CC subagents run in the same local directory and can technically `ls` or `cat` files. OS-level isolation via worktrees/sandboxes (iloom approach) is deferred to Phase 2. The current blind wall is "strong enough" for v3 — subagents spawned via Task tool have limited tool access and scoped prompts. Document and revisit if empirically violated.

### Per-Task TDD Cycle

```
EM spawns for Task A:
    ┌─────────────────────┬─────────────────────┐
    │   QA writes tests   │   Dev implements    │  PARALLEL (blind wall)
    │   (from spec)       │   (from spec)       │
    └────────┬────────────┴──────────┬──────────┘
             ↓                       ↓
      Validator checks tests    Validator checks code
      (cover spec?)             (covers scope?)
             ↓                       ↓
        QA runs tests against Dev's code
             ↓
        Pass? ──→ YES ──→ Reviewer + InfoSec (parallel)
          │                    ↓
          NO               Issues found?
          ↓                 ↓ YES        ↓ NO
        Bug filed        Bug filed    Sign off → DONE
          ↓                ↓
        Dev fixes        Dev fixes
          ↓                ↓
        Code changed? ──→ YES → EM decides if QA needs notification
          ↓                       → If yes: QA updates tests, re-runs
        QA re-runs                → If no: QA re-runs existing tests
          ↓
        Pass? (cycle back, max 3 → HITL)
```

### QA Notification Decision

When code changes after review, EM decides if the change is significant enough to notify QA to update tests. Not automatic — EM parses Dev's output for scope of change. Minor fixes (formatting, variable naming) don't require QA notification. Structural changes (new code paths, changed APIs) do.

### Sprint-Level Review

After all tasks in a sprint complete, EM spawns Reviewer at sprint-level (separate from per-task review):

```
All sprint tasks complete (per-task review passed)
    → EM spawns Reviewer (sprint-level) with:
        - All files modified across all sprint tasks
        - Implementation Spec
        - CLAUDE.md
    → Reviewer checks cross-task consistency:
        - Naming consistency across tasks
        - Import patterns consistent
        - Error handling consistent
        - API contracts match between modules
        - No duplicate code across tasks (DRY at sprint level)
        - Architecture alignment with Implementation Spec
    → Issues filed as P2 bugs (Dev fixes before qualify)
```

### Project-Level Review: Prism

Prism is NOT an agent. It's an external tool invoked by DevOps during qualify phase for full-codebase review.

```
Qualify phase:
    → DevOps runs Prism:
        prism review --full      → Code quality scan (all files)
        prism security --full    → Security scan (all files)
    → Prism output: structured findings with severity (P0/P1/P2/P3)
    → DevOps files results in em.db mail
    → EM routes: P0/P1 → blocking bugs (Dev fixes before deploy)
                  P2/P3 → logged as tech debt
```

**Why Prism at project level, not Reviewer subagent:** Reviewer is a CC subagent with context window limits. Prism uses tree-sitter + static analysis + LLM agents — built for whole-codebase review. Different tool for different scale.

**Review coverage summary:**

| Level | Who | What | When |
|---|---|---|---|
| Task | Reviewer (subagent) | Code quality, SOLID/DRY, docstrings | Per task during implement |
| Task | InfoSec (conditional) | Security review | Per task when security-relevant |
| Sprint | Reviewer (sprint-level) | Cross-task consistency | After all sprint tasks complete |
| Project | Prism (external tool) | Full codebase quality + security | Qualify phase |

---

## 12. Bug Lifecycle

### Storage

Bugs are stored in a separate `bugs` table in SQLite (not just mail messages). This enables querying, metrics, and tracking across projects.

### Bug Flow

1. **Filing**: QA, Reviewer, or InfoSec files a bug via their output
2. **Priority**: Filer assigns priority. EM can override if needed.
3. **Batching**: Multiple bugs from one test run are filed as separate entries. Dev receives all at once.
4. **Assignment**: EM assigns bug to Dev (or appropriate agent)
5. **Fix**: Dev implements fix, notes what changed
6. **QA notification**: EM decides if code change is significant enough to notify QA
7. **Verification**: QA re-runs tests (updated if notified)
8. **Closure**: Bug marked resolved. Notification goes to original filer (whoever filed it — Reviewer, InfoSec, QA)

### Bug in Mail

Bug filing creates a mail message in the task thread with bug metadata. The `bugs` table stores structured data. Both are linked by bug ID.

---

## 13. Work Types

Work types are **metadata tags only** — they don't define separate orchestration flows. Tiers control all orchestration.

### Available Tags

| Tag | Typical Tier | Notes |
|---|---|---|
| Feature | Full | New functionality, full planning cycle |
| Enhancement | Standard | Extending existing functionality |
| Bug Fix | Minimal | Fix and verify |
| Refactor (small) | Minimal | Rename, extract method, cleanup |
| Refactor (large) | Full | Module rewrite, architecture change |
| Performance | Standard | Optimization with benchmarking |
| Research/Spike | Minimal | Produces report, not code. **QA skipped.** |

### Classification

- EM auto-classifies work type and proposes tier
- Human approves classification and tier at project start
- Work type tag is stored in Yggdrasil for reporting and metrics

### Research/Spike Exception

Research/Spike is the only work type that changes the flow: QA is skipped because the output is a report/analysis, not code. All other work types follow their tier's standard flow.

---

## 14. Session Boundaries

### State Storage

**Dual approach**: SQLite for speed, mail for truth.

- **TaskGraph state** (task statuses, active sprint, current wave) stored in `task_state` table in SQLite
- EM writes task status to SQLite as it goes (fast reads during execution)
- **On session restart**: EM reconciles SQLite state against mail thread
- **If they disagree**: Mail wins. SQLite is rebuilt from mail.

### Interrupted Subagents

If a subagent (e.g., Dev) was mid-execution when session died:
1. Treat as **failed**
2. EM checks if spec changed since agent was spawned
3. If spec unchanged: re-spawn agent with same context
4. If spec changed: re-spawn with updated context
5. No attempt to recover partial output — clean re-spawn

### EM Restart

On session restart, a **hook** injects state into EM's context:

```
Active project: X
Tier: Full
Sprint 1: Task A complete, Task B in progress (Dev submitted, QA pending), Task C pending
Sprint 2: Not started
Blockers: None
Last PM status: [timestamp]
```

EM then reads mail thread to reconcile and verify the injected state. If discrepancies found, mail wins.

---

## 15. Orchestration → Abzu Memory Bridge

When a project completes, Abzu reads em.db and distills bead candidates. EM does not write to Abzu directly — Abzu pulls from em.db.

**See Abzu Memory Spec (Pillar 1) for full details on ingestion, staging, and Gemini review.**

### What Becomes Bead Candidates

| Extracted from em.db | Category |
|---|---|
| Both specs (Product + Implementation) | `decision` |
| Key decisions from planning thread | `decision` |
| Bug patterns and fixes | `fix` |
| Architectural approaches that worked | `pattern` |
| Final review feedback (insights) | `learning` |

### What Does NOT Become Candidates

| Excluded | Why |
|---|---|
| Routine "task complete" messages | No lasting value |
| Intermediate validator output | Ephemeral verification |
| Raw test results | Too granular, tests themselves are in codebase |
| Status update messages | Operational, not knowledge |

### Lifecycle

1. Project completes → Abzu reads em.db mail threads
2. Heuristic + CC distillation produces candidates
3. Candidates → abzu.db staging (NOT wisdom.db)
4. em.db kept 30 days → deleted
5. Gemini monthly/quarterly review promotes candidates → wisdom.db

Candidates are NOT permanent until Gemini approves them.

---

## 16. Yggdrasil Integration

Yggdrasil is the project management tool — Enki's Jira + Confluence. It serves as the living document for a project from inception to closure.

### Who Writes What

| Agent | Writes to Yggdrasil |
|---|---|
| **PM** | Project creation (name, goal, scope, tier), full specs (Product + Implementation — content not just links), sprint milestones, status updates, change request outcomes, project closure summary |
| **EM** | Bug entries from QA/Reviewer/InfoSec, bug status updates, task progress, comments on blockers/dependencies |

### What PM Reads

- Existing projects for context before creating new ones
- Dependency/conflict flags raised by EM as comments
- PM resolves conflicts via spec change or comment response

### Conflict Handling

1. EM detects dependency or conflict with existing project
2. EM raises it as a comment on the Yggdrasil project entry
3. PM is spawned to review
4. PM resolves: either revises spec or adds comment explaining why it's not a conflict
5. Resolution logged in Yggdrasil

### Yggdrasil as Living Record

- Project entry created at intake
- Specs stored in full (not links — the actual content)
- Every sprint milestone tracked
- Every bug tracked with full lifecycle
- Every change request tracked
- Every status update recorded
- Project closure with final summary
- Persists after project completion for historical reference

---

## 17. Status Updates

### Triggers

PM sends status updates to human on ALL of the following:
- Sprint completion
- Blocker/HITL escalation
- Bug filed
- Scheduled cadence (configurable interval — daily, weekly, etc.)
- Project milestones (spec approved, first sprint done, project complete)

### Channel

- **Primary**: Mail thread within Enki
- **Secondary**: Email to human (explore cost-effective delivery options — future implementation)

### Content

High-level summary including:
- Key decisions made since last update
- Sprint progress (tasks complete/in-progress/pending)
- Bugs filed and their status
- Review comments and concerns
- Spec changes or change requests
- Blockers and escalations

---

## 18. Enforcement Strategy

### Philosophy

Make the right path the easiest path. CC should WANT to use the orchestrator because it's the most efficient way to work. Don't build an adversarial system — build a well-paved road.

### Structural Enforcement (Minimal)

- **Layer 0 blocklist** on enforcement files (stays — Pillar 2)
- **Gate 2**: Spec approval required before agent spawning
- **Gate 3**: TDD enforcement — QA and Dev must both exist in task DAG
- **Ereshkigal**: In original form — ensuring work quality, not watching for bypass

### No Orchestration-Specific Enforcement

No "validate every Task tool call against DAG." No adversarial pattern watching. The orchestrator's function-based design means CC doesn't "decide" to follow the pattern — it calls `get_next_actions()` and spawns what the function returns. Structure IS enforcement.

### Why This Works (Odin Lesson)

Odin's orchestrator worked without gates because the DAG was code, not instructions. CC didn't "decide" to follow the wave pattern — the orchestrator code only gave it the next task when dependencies were met. We replicate this: execution path enforces behavior.

### Gemini-Built Enforcement Layer (Proposed)

The fox doesn't build its own cage:

| Who Builds | What | Why |
|---|---|---|
| **Gemini** | Gates, hooks, Ereshkigal, Layer 0/0b blocklists | Builder never runs under constraints — no incentive to weaken |
| **CC (Enki)** | Orchestrator, agents, mail, memory, beads | CC builds what it uses, constrained by what Gemini built |
| **Human** | Reviews both, approves merges | Final authority |

---

## 19. Nelson Additions

Based on analysis of the Nelson orchestration framework (Royal Navy themed Claude Code skill), two enhancements are incorporated:

### Red-Cell Review Step

At **Full tier only**, after Architect completes Implementation Spec but before QA/Dev spawn:

- Validator runs in **adversarial mode** with a different prompt
- Challenges assumptions in the Implementation Spec
- Identifies potential failure points
- Checks rollback readiness
- Validates that the plan handles edge cases

Not a new agent — Validator with a red-cell prompt. If red-cell finds issues, they go back to Architect for revision (max 2 cycles before HITL).

Skipped at Minimal and Standard tiers.

### Failure-Mode Checklist

At **Full and Standard tiers**, Validator includes pre-mortem analysis for each task:

1. What could fail?
2. How would we detect failure?
3. What's the fastest rollback?
4. What are the dependency risks?
5. What's the least certain assumption?

At Minimal tier, this is skipped.

---

## 20. Data Schemas

### Mail Messages

```sql
CREATE TABLE mail_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    parent_thread_id TEXT,
    project_id TEXT NOT NULL,
    from_agent TEXT NOT NULL,        -- "EM", "QA", "Dev", "User", "PM", etc.
    to_agent TEXT NOT NULL,          -- Role-based: "QA", "Dev", "Validator", "User"
    subject TEXT,
    body TEXT NOT NULL,              -- Full content including inline attachments
    importance TEXT DEFAULT 'normal', -- normal, high, critical
    status TEXT DEFAULT 'unread',    -- unread, read, acknowledged, assigned, resolved
    assigned_to TEXT,                -- Agent assigned when status = 'assigned'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_id TEXT,                    -- Links to task DAG
    sprint_id TEXT,
    FOREIGN KEY (thread_id) REFERENCES mail_threads(thread_id)
);

CREATE INDEX idx_mail_to_agent ON mail_messages(to_agent, status);
CREATE INDEX idx_mail_thread ON mail_messages(thread_id);
CREATE INDEX idx_mail_project ON mail_messages(project_id);
```

### Mail Threads

```sql
CREATE TABLE mail_threads (
    thread_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_thread_id TEXT,
    type TEXT NOT NULL,              -- planning, sprint, task, hitl, change_request
    status TEXT DEFAULT 'active',    -- active, complete, blocked, archived
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,
    FOREIGN KEY (parent_thread_id) REFERENCES mail_threads(thread_id)
);
```

### Bugs

```sql
CREATE TABLE bugs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    sprint_id TEXT,
    filed_by TEXT NOT NULL,          -- Agent role that filed: "QA", "Reviewer", "InfoSec"
    assigned_to TEXT,                -- Agent role assigned to fix: usually "Dev"
    priority TEXT NOT NULL,          -- critical, high, medium, low (filer assigns, EM can override)
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'open',      -- open, in_progress, fixed, verified, closed
    mail_message_id TEXT,            -- Links to the mail message that filed this bug
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    FOREIGN KEY (mail_message_id) REFERENCES mail_messages(id)
);

CREATE INDEX idx_bugs_project ON bugs(project_id, status);
CREATE INDEX idx_bugs_task ON bugs(task_id);
```

### Task State

```sql
CREATE TABLE task_state (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    sprint_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending, running, complete, failed, blocked
    assigned_files TEXT,             -- JSON array of file paths
    dependencies TEXT,               -- JSON array of task_ids
    tier TEXT NOT NULL,              -- minimal, standard, full
    work_type TEXT,                  -- feature, bug_fix, refactor, enhancement, performance, research
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    agent_outputs TEXT,              -- JSON: last output from each agent phase
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3
);

CREATE INDEX idx_task_sprint ON task_state(sprint_id, status);
CREATE INDEX idx_task_project ON task_state(project_id);
```

### Sprint State

```sql
CREATE TABLE sprint_state (
    sprint_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    sprint_number INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending, active, complete, blocked
    dependencies TEXT,               -- JSON array of sprint_ids
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

### PM Decision Log (Learning Loop)

```sql
CREATE TABLE pm_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    decision_type TEXT NOT NULL,     -- scope_change, priority, resource, technical
    proposed_action TEXT NOT NULL,
    context TEXT,                    -- What led to this decision
    human_response TEXT,             -- approve, reject, modify
    human_modification TEXT,         -- If modified, what the human changed
    pm_was_autonomous INTEGER DEFAULT 0, -- Was this an autonomous PM decision?
    human_override TEXT,             -- If autonomous: right, wrong, null
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pm_decisions_type ON pm_decisions(decision_type, human_response);
```

### Mail Archive

```sql
CREATE TABLE mail_archive (
    id TEXT PRIMARY KEY,
    original_id TEXT NOT NULL,       -- Original mail_messages.id
    thread_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    created_at TIMESTAMP,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_archive_project ON mail_archive(project_id);
CREATE INDEX idx_archive_thread ON mail_archive(thread_id);
```

---

## 21. Bill of Materials

### Estimated Module Breakdown

| File | ~Lines | What | Source |
|---|---|---|---|
| `orch/orchestrator.py` | ~1300 | Core EM: spawn, process, auto-advance, mail routing, tier management, conditional agent logic, entry point detection, sprint-level review spawn | Odin (rewritten) |
| `orch/mail.py` | ~600 | Message storage, threads, inbox/outbox, routing, archival | New (inspired by agent_mail) |
| `orch/task_graph.py` | ~600 | DAG, waves, cyclic recovery, sprint management | Odin (nearly verbatim) |
| `orch/agents.py` | ~500 | Agent definitions (13), prompt assembly from prompts/ files, output templates | Odin (trimmed + new) |
| `orch/pm.py` | ~600 | PM workflow: intake, debate, learning loop, change requests, customer presentation, entry point validation | New |
| `orch/validation.py` | ~400 | Blind validation, progressive context, failure-mode checklist | Odin (wired in) |
| `orch/bugs.py` | ~300 | Bug lifecycle: filing, assignment, tracking, closure | New |
| `orch/tiers.py` | ~250 | Tier definitions, auto-detection, escalation logic | Odin (extended) |
| `orch/yggdrasil.py` | ~400 | Project tracking integration: create, update, query | New |
| `orch/status.py` | ~200 | Status update generation, trigger management | New |
| `orch/bridge.py` | ~300 | Memory bridge: bead extraction, archival, summarization | New |
| `orch/parsing.py` | ~250 | Agent output JSON parsing, retry logic | New |
| `orch/schemas.py` | ~200 | SQLite table definitions, migrations | New |
| `orch/claude_md.py` | ~300 | CLAUDE.md generation from Codebase Profile or project type registry, template engine | New |
| `orch/onboarding.py` | ~250 | Entry point detection, user profile management, first-time user flow | New |
| `orch/researcher.py` | ~300 | Codebase Profile protocol, scoped investigation, time-bounded analysis | New |

**Total**: ~6,750 lines (estimated)

**External**: Agent prompts live in `prompts/` directory (Layer 0 protected, written by Gemini, not counted in orch lines). Prism invoked as external tool during qualify phase (not part of orch codebase).

### Dependencies

- **Internal**: Enki SQLite (wisdom.db), Enki CLI, Pillar 1 (Memory — for bead extraction), Pillar 2 (Gates — for enforcement)
- **External**: None. Everything built into Enki.

---

## 22. Repos Referenced

| Repo | What | How Used |
|---|---|---|
| **Odin** (own) | TaskGraph, waves, cyclic recovery, agent definitions | Bones of EM — task_graph.py nearly verbatim, orchestrator.py rewritten |
| **mcp_agent_mail** (steveyegge) | Mail pattern, Human Overseer concept | Inspiration for mail system — built into Enki, no dependency |
| **Nelson** (harrymunro) | Red-cell review, failure-mode checklist, execution modes | Two enhancements incorporated (red-cell, failure-mode) |
| **Zeroshot** (covibes) | Blind validation, progressive context | Patterns for Validator agent |
| **Claudest** (gupsammy) | FTS5 recall memory, session context injection | Parked for Pillar 1 memory redesign |
| **PLTM-Claude** (Alby2007) | Memory approach | Parked for Pillar 1 — reference for what to avoid |
| **iloom** (iloom-ai) | Environment isolation, worktrees | Parked — Phase 2 |

---

## 23. Glossary

| Term | Definition |
|---|---|
| **EM** | Engineering Manager — the central orchestrator. Routes mail, spawns agents, tracks progress. Has no opinions. |
| **Blind Wall** | Context filtering that prevents QA and Dev from seeing each other's work during parallel execution. |
| **Bead** | A unit of knowledge in Enki's memory system (Pillar 1). Extracted from orchestration at project completion. |
| **CLAUDE.md** | Project constitution file. Created by Architect during planning. First file CC reads on every session. Defines WHY/WHAT/HOW for the project. |
| **Customer Presentation** | PM presents completed product to customer against acceptance criteria. Formal acceptance gate before closure. Full tier only. |
| **DAG** | Directed Acyclic Graph — the dependency structure of tasks within sprints. |
| **HITL** | Human-In-The-Loop — escalation to the human user for decisions that agents can't resolve. |
| **Red-Cell** | Adversarial review step where Validator challenges the Implementation Spec before execution begins. Full tier only. |
| **SOLID** | Five design principles (Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion) mandated for Dev agent. |
| **DRY** | Don't Repeat Yourself — no duplicated logic. Mandated for Dev agent. |
| **Yggdrasil** | Enki's project management tool — serves as living project documentation from inception to closure. |
| **Wave** | A set of tasks whose dependencies are all met and can be executed in the current cycle. |
| **Two-Spec Model** | Product Spec (what) + Implementation Spec (how) with negotiation loop between PM and Architect. |
| **Tier** | Orchestration scale level (Minimal, Standard, Full) controlling both context and agent participation. |
| **Fox Problem** | The fundamental constraint that CC both implements Enki and is constrained by it — requiring structural enforcement. |
| **Progressive Disclosure** | CLAUDE.md principle: tell CC where to find info, not all the info itself. Prevents context bloat. |

---

## Appendix A: Full Tier Flow Comparison

### Minimal Tier

```
User describes change
    ↓
EM auto-detects tier (Minimal)
    ↓
Human approves tier
    ↓
CLAUDE.md auto-generated from project type registry (if not exists)
    ↓
Dev implements from description (SOLID/DRY/Clean Code)
    ↓
QA writes tests from description
    ↓
QA runs tests
    ↓
Pass → Done
Fail → Bug → Dev fixes → QA re-runs (max 3 → HITL)
```

**Agents involved**: Dev, QA
**No**: PM, Architect, DBA, Validator, Reviewer, InfoSec, UI/UX, DevOps, Performance, Researcher, sprints, DAG

### Standard Tier

```
User provides spec (or PM writes brief one)
    ↓
EM auto-detects tier (Standard), Architect proposes
    ↓
Human approves tier + spec
    ↓
Architect creates CLAUDE.md (customer can augment)
    ↓
EM builds single-sprint DAG
    ↓
Per task:
    QA writes tests from spec  ∥  Dev implements from spec (blind wall, SOLID/DRY)
        ↓                              ↓
    Validator checks tests      Validator checks code
    (+ failure-mode checklist)  (+ failure-mode checklist)
        ↓                              ↓
    QA runs tests against Dev's code
        ↓
    Reviewer reviews (checks SOLID/DRY adherence)
        ↓
    (InfoSec, UI/UX, Performance — if triggered)
        ↓
    Done (or bug cycle, max 3 → HITL)
    ↓
Done
```

**Agents involved**: Dev, QA, Validator, Reviewer, DevOps, (InfoSec, UI/UX, Performance if triggered)
**No**: PM intake/debate, DBA, red-cell, multi-sprint, customer presentation

### Full Tier

```
User has idea
    ↓
Enki spawns PM → PM intake Q&A (hybrid: freeform + checklist)
    ↓
PM writes Product Spec
    ↓
Debate: Technical Feasibility → Devil's Advocate → Historical Context (sequential)
    ↓ (max 2 cycles)
PM reconciles, human approves Product Spec
    ↓
Enki spawns Architect + DBA → write Implementation Spec
    ↓
Red-cell review (Validator in adversarial mode)
    ↓
Human approves Implementation Spec
    ↓
Architect creates CLAUDE.md (inputs from PM, customer, DBA)
    ↓
Customer reviews CLAUDE.md (adds custom instructions if any)
    ↓
CLAUDE.md committed to repo
    ↓
PM sends kickoff mail to EM
    ↓
Enki spawns EM → EM builds multi-sprint DAG
    ↓
Per sprint:
    Per task:
        QA writes tests from spec  ∥  Dev implements from spec (blind wall, SOLID/DRY)
            ↓                              ↓
        Validator checks tests      Validator checks code
        (+ failure-mode checklist)  (+ failure-mode checklist)
            ↓                              ↓
        QA runs tests against Dev's code
            ↓
        Reviewer + InfoSec review (parallel)
            ↓
        (UI/UX, Performance — if triggered)
            ↓
        Done (or bug cycle, max 3 → HITL)
    ↓
    Sprint complete → PM status update → Yggdrasil updated
    ↓
Next sprint
    ↓
All sprints complete
    ↓
DevOps: qualify → deploy → verify (per user's configured pipeline)
    ↓
PM: Customer presentation (deliverables vs acceptance criteria)
    ↓
Customer accepts? 
    YES → PM closure → Memory bridge → Beads extracted → Archived
    NO  → Issues become bugs/change requests → cycle back
```

**Agents involved**: PM, Architect, DBA, Dev, QA, Validator, Reviewer, InfoSec, UI/UX (if frontend), DevOps, Performance (if flagged), Researcher (on-demand)
**All features**: Two-spec, CLAUDE.md, debate, red-cell, multi-sprint, blind wall, failure-mode, SOLID/DRY, customer presentation, sprint-level review, Prism qualify, Yggdrasil, memory bridge

---

## Appendix B: Agent Output JSON Template

All agents return structured JSON to EM:

```json
{
  "agent": "Dev",
  "task_id": "task-a-jwt-middleware",
  "status": "DONE",
  "completed_work": "Description of what was implemented...",
  "files_modified": [
    "src/auth.py",
    "src/middleware.py"
  ],
  "files_created": [
    "src/utils/jwt_helper.py"
  ],
  "decisions": [
    {
      "decision": "Used RS256 over HS256 for JWT signing",
      "reasoning": "Asymmetric keys allow public verification without sharing secrets"
    }
  ],
  "messages": [
    {
      "to": "QA",
      "content": "Auth uses RS256 JWT. Public key at config/jwt_public.pem"
    },
    {
      "to": "Architect",
      "content": "Added jwt_helper.py not in original spec — utility extraction"
    }
  ],
  "concerns": [
    {
      "to": "Architect",
      "content": "Token refresh flow may need a dedicated endpoint not in spec"
    }
  ],
  "blockers": [],
  "tests_run": null,
  "tests_passed": null,
  "tests_failed": null
}
```

---

## Appendix C: Open Items for Other Pillars

These items surfaced during orchestration design but belong to other specs:

### For Pillar 1 (Memory Spec)
- Recall memory layer (inspired by Claudest) — raw conversation search via FTS5
- Bead extraction interface — what EM's bridge module calls
- Session context injection hook — what gets loaded on session start
- PLTM-Claude analysis — what not to do

### For Pillar 2 (Gates Spec)
- Gemini-built enforcement layer — gates, hooks, Ereshkigal built by non-CC system
- Layer 0 blocklist on enforcement files
- Gate 2 (spec approval) and Gate 3 (TDD) integration points with EM
- Ereshkigal pattern interception for orchestration commands
- **New agents scope restrictions**: UI/UX writes_to must exclude `src/enki/`, DevOps cannot modify hook scripts or enforcement files
- **CLAUDE.md governance section**: minimal reference to Enki, no enforcement details exposed

### For Bridge Spec
- Orchestration → Memory: bead extraction triggers, content selection, archival
- Orchestration → Gates: enforcement checkpoints in EM flow
- Memory → Orchestration: historical context injection for agents (especially Enki reviewer in debate)
- Gates → Orchestration: what gets blocked, what gets logged

### For Ship & Quality Spec
- DevOps agent handles qualify/deploy/verify — user-configurable deployment
- Customer presentation as acceptance gate before ship
- Sprint-level Reviewer for cross-task consistency
- Prism as project-level review tool in qualify phase
- Regression as practice (ISTQB) — curated suite from existing tests

---

*End of EM Orchestrator Spec v1.4*
