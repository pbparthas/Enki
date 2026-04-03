# Enki PLAYBOOK — Exact Operational Sequences

This is your step-by-step guide for every phase. Follow it exactly.
When in doubt: `enki_phase(action='status')` to orient, then return here.

---

## HOW TO START ANY SESSION

```
1. enki_recall(query="project context recent decisions")
2. enki_phase(action='status') → read current phase
3. Go to the section for that phase below
```

If no project is active:
```
→ New project: enki_goal(description="...", project="name")
→ Existing project not registered: enki_register(path=".")
```

---

## PHASE: planning

### What this phase is
Requirements gathering. No code, no spec yet. You are understanding what to build.

### Exact sequence

**Greenfield (new codebase):**
```
1. enki_goal(description="...", project="name")
2. enki_recall(query="similar projects past decisions")
3. Q&A with human — validate: outcome, audience, constraints, success criteria, scope, risks
4. enki_spawn('pm', 'spec-draft') → READ prompt_path verbatim → READ context_artifact
   → Task tool FOREGROUND → wait for completion
5. enki_report(role='pm', task_id='spec-draft', summary=..., status='completed')
   → PM writes docs/spec-draft.md
→ Go to PHASE: spec
```

**Brownfield (existing codebase):**
```
1. enki_goal(description="...", project="name")
2. enki_recall(query="codebase patterns decisions")
3. enki_spawn('researcher', 'codebase-profile') → Task tool FOREGROUND → wait
4. enki_report(role='researcher', task_id='codebase-profile', summary=..., status='completed')
5. Present codebase profile to human — confirm tech stack
6. Q&A with human → same intake checklist
7. enki_spawn('pm', 'spec-draft') → Task tool FOREGROUND → wait
8. enki_report(role='pm', task_id='spec-draft', summary=..., status='completed')
→ Go to PHASE: spec
```

**External spec (spec already exists):**
```
1. enki_goal(spec_path="/path/to/spec.md", project="name")
2. enki_spawn('pm', 'spec-review') → Task tool FOREGROUND → wait
   (PM reviews and endorses existing spec — does NOT rewrite)
3. enki_report(role='pm', task_id='spec-review', summary=..., status='completed')
→ Go to PHASE: spec (skip debate if spec is already final)
```

### NEVER in this phase
- Do not call enki_wave
- Do not call enki_decompose
- Do not write any code

---

## PHASE: spec

### What this phase is
Spec debate, adversarial review, approval. The spec gets stress-tested before
any implementation planning begins.

### Exact sequence

**Step 1 — Run debate (always for Standard/Full tier):**
```
enki_debate()
→ Returns Round 1 spawn instructions

Round 1 agents (Standard/Full): cto, devils_advocate, tech_feasibility, security-architect
Round 1 agents (brownfield only): + historical_context

For each agent in Round 1 (sequential, foreground):
  enki_spawn(role, 'debate-r1-{role}') → Task tool FOREGROUND → wait
  enki_report(role=role, task_id='debate-r1-{role}', summary=..., status='completed')
  enki_debate_update(role=role, round='1', output={...from agent JSON output...})

enki_debate() → Returns Round 2 spawn instructions

For each agent in Round 2 (sequential, foreground):
  enki_spawn(role, 'debate-r2-{role}') → Task tool FOREGROUND → wait
  enki_report(role=role, task_id='debate-r2-{role}', summary=..., status='completed')
  enki_debate_update(role=role, round='2', output={...from agent JSON output...})

enki_debate() → Returns reconciliation spawn instructions

enki_spawn('pm', 'debate-reconcile') → Task tool FOREGROUND → wait
enki_report(role='pm', task_id='debate-reconcile', summary=..., status='completed')
enki_debate_update(role='pm', round='reconciliation', output={...from PM JSON output...})

enki_debate() → Returns complete with spec-final.md and debate-summary.md paths
```

**Step 2 — HITL review:**
```
Present docs/debate-summary.md to human
Present docs/spec-final.md to human
Wait for verbal approval
enki_approve(stage='spec')
```

**Step 3 — Igi adversarial review:**
```
enki_spawn('igi', 'igi-review') → Task tool FOREGROUND → wait
enki_report(role='igi', task_id='igi-review', summary=..., status='completed')
Present Igi findings to human
Wait for verbal approval
enki_approve(stage='igi')
→ Phase advances to 'approved' automatically
```

### NEVER in this phase
- Do not skip debate for Standard or Full tier
- Do not call enki_approve(stage='spec') before debate is complete
- Do not call enki_approve(stage='igi') before presenting Igi findings to human

---

## PHASE: approved

### What this phase is
Pre-implementation kickoff and Architect impl spec. Feasibility confirmed,
task DAG created.

### Exact sequence

**Step 1 — Kickoff:**
```
enki_kickoff()
→ Returns PM + Architect spawn instructions

enki_spawn('pm', 'kickoff-pm') → Task tool FOREGROUND → wait
enki_report(role='pm', task_id='kickoff-pm', summary=..., status='completed')
enki_kickoff_update(role='pm', output={...from PM JSON output...})

enki_spawn('architect', 'kickoff-architect') → Task tool FOREGROUND → wait
enki_report(role='architect', task_id='kickoff-architect', summary=..., status='completed')
enki_kickoff_update(role='architect', output={...from Architect JSON output...})

[If PM output signals dba_needed=true:]
  enki_spawn('dba', 'kickoff-dba') → Task tool FOREGROUND → wait
  enki_report(role='dba', task_id='kickoff-dba', summary=..., status='completed')
  enki_kickoff_update(role='dba', output={...})

[If PM output signals ui_needed=true:]
  enki_spawn('ui_ux', 'kickoff-ui_ux') → Task tool FOREGROUND → wait
  enki_report(role='ui_ux', task_id='kickoff-ui_ux', summary=..., status='completed')
  enki_kickoff_update(role='ui_ux', output={...})

enki_kickoff_complete()
```

If blockers found:
```
→ Present blockers to human
→ Wait for resolution
→ enki_approve(stage='spec-revision', note='resolution details')
→ enki_kickoff() again → repeat from Step 1
```

If no blockers:
```
→ Present kickoff summary to human (verbal ok)
→ Proceed to Step 2
```

**Step 2 — Architect impl spec:**
```
enki_spawn('architect', 'impl-spec') → Task tool FOREGROUND → wait
enki_report(role='architect', task_id='impl-spec', summary=..., status='completed')
```

Architect output MUST contain a JSON block with tasks array:
```json
{
  "tasks": [
    {
      "name": "Task name",
      "description": "Exact description of what to implement",
      "files": ["path/to/file.ts"],
      "dependencies": ["Other task name"],
      "acceptance_criteria": ["criterion 1", "criterion 2"]
    }
  ]
}
```

**Step 3 — HITL approval:**
```
Present impl spec to human
Wait for verbal approval
enki_approve(stage='architect')
→ Phase advances to 'implement' automatically
```

**Step 4 — Decompose:**
```
enki_decompose(tasks=[
  {
    "name": "...",
    "description": "...",    ← REQUIRED — copy from Architect JSON output exactly
    "files": [...],
    "dependencies": [...]
  },
  ...
])
→ Creates sprint and task records in em.db
```

### NEVER in this phase
- Do not call enki_wave before enki_decompose
- Do not call enki_decompose without description for each task
- Do not skip kickoff — always run it before Architect impl spec

---

## PHASE: implement

### What this phase is
Wave execution. You are the orchestrator. You NEVER implement code yourself.
You spawn agents and report results.

### Exact sequence per wave

```
enki_wave()
→ Returns list of tasks and agents for this wave
→ ALWAYS note the sprint_branch in the response
```

**For EACH task returned (one at a time):**

```
Step 1: QA-write (spawn immediately)
  enki_spawn(role='qa', task_id='{task_id}', mode='write')
  → Read prompt_path verbatim
  → Read context_artifact completely
  → Task tool FOREGROUND — wait for completion
  enki_report(role='qa', task_id='{task_id}', summary='...', status='completed', mode='write')

Step 2: Dev (spawn IMMEDIATELY after QA-write — do NOT wait for Validator)
  enki_spawn(role='dev', task_id='{task_id}')
  → Read prompt_path verbatim
  → Read context_artifact completely
  → Task tool FOREGROUND — wait for completion
  enki_report(role='dev', task_id='{task_id}', summary='...', status='completed')

Step 3: Validator review-tests (NOW validate QA tests)
  enki_spawn(role='validator', task_id='{task_id}', mode='review-tests')
  → Task tool FOREGROUND — wait for completion
  enki_report(role='validator', task_id='{task_id}', summary='...', status='completed')
  [If Validator finds issues → QA fixes → re-run Validator before continuing]

Step 4: QA execute
  enki_spawn(role='qa', task_id='{task_id}', mode='execute')
  → Task tool FOREGROUND — wait for completion
  enki_report(role='qa', task_id='{task_id}', summary='...', status='completed')

Step 5: Validator compliance
  enki_spawn(role='validator', task_id='{task_id}', mode='compliance')
  → Task tool FOREGROUND — wait for completion
  enki_report(role='validator', task_id='{task_id}', summary='...', status='completed')

Step 6: Complete task ← MANDATORY, NEVER SKIP
  enki_complete(task_id='{task_id}')
  → This marks the task done, queues merge, releases session claim
  → Without this, the wave will return the same task again forever
```

**After ALL tasks in the wave are complete:**
```
enki_mail_inbox()  → read agent messages

If enki_wave returned checkpoint_reviewer_required=True:
  enki_spawn('reviewer', '{sprint_id}-checkpoint-{n}', mode='batch-review')
  → Task tool FOREGROUND — wait for completion
  enki_report(role='reviewer', task_id=..., summary=..., status='completed')
  [P2 bugs filed — does NOT block next wave]

enki_wave()        → get next wave OR sprint_complete signal
```

**When enki_wave returns sprint_complete=True:**
```
enki_phase(action='status')  → confirm all tasks done
enki_phase(action='advance', to='validating')
→ Go to PHASE: validating
```

**When enki_wave returns no tasks but sprint not complete:**
```
→ Some tasks are in_progress by other sessions or blocked
→ enki_phase(action='status') to see which
→ Wait or escalate if blocked
```

### NEVER in this phase
- NEVER use Agent tool — ALWAYS use Task tool (Task tool sets required permissions)
- NEVER wait for Validator before spawning Dev after QA-write — spawn Dev immediately
- NEVER spawn Reviewer per-task — Reviewer runs at wave checkpoints and sprint end only
- NEVER call enki_wave again before enki_complete for each task in the current wave
- NEVER implement code yourself — spawn agents for all implementation work
- NEVER call enki_report without having run the Task tool first

### Conditional agent spawning (after Dev+QA complete)
```
If task files include .tsx/.jsx/.vue/.css → also spawn ui_ux
If task involves auth/token/session/encrypt → also spawn infosec
If task modifies hot path identified in codebase profile → also spawn performance

For each conditional agent:
  enki_spawn(role='{role}', task_id='{task_id}')
  → Task tool FOREGROUND → wait
  enki_report(role='{role}', task_id='{task_id}', summary=..., status='completed')
  (then proceed to enki_complete as normal)
```

---

## PHASE: validating

### What this phase is
Sprint-level review and final validation before completion.

### Exact sequence
```
1. enki_validate(scope='sprint')
   → Handles: DevOps sprint tests, InfoSec sprint-audit, Reviewer sprint-review
   → Architect prioritizes all bugs found
   → Fix loop: Dev → QA execute → Validator → reporter revalidation
   → HITL validation: file bugs via enki_bug(), then call enki_validate() again
   → Returns clear when all P0/P1 resolved

2. enki_sprint_close()
   → Auto-generates sprint summary (tasks, bugs, coverage)
   → P2/P3 bugs seeded to next sprint
   → Merges sprint branch
   → HITL: "another sprint or close project?"
     - Another sprint: call enki_goal() to start next sprint
     - Final sprint: enki_validate(scope='project') → enki_project_close()
```

### HITL bug filing during validation
```
If you find an issue during review:
  enki_bug(action='file', title='...', description='...', severity='P1',
           filed_by='hitl', affected_files=['...'])
  Do NOT call enki_sprint_close yet
  Call enki_validate() again — fix loop will pick up the new bug
```

### NEVER in this phase
- NEVER spawn InfoSec or Reviewer directly — use enki_validate()
- NEVER call enki_sprint_close before enki_validate returns clear
- NEVER fix bugs directly — route through enki_validate fix loop

---

---
## PHASE: closing

### What this phase is
Project wrap-up after all sprints complete and project-level validation cleared.
Merge, push, memory pipeline, optional documentation, HITL acceptance.

### Exact sequence
```
1. enki_project_close()
   → Merges all worktrees to main
   → git push origin main
   → enki_wrap() final memory pipeline
   → Returns HITL question for project acceptance

2. [Optional] enki_document()
   → Auto-detects required docs (README, ARCHITECTURE, SECURITY, FEATURES, etc.)
   → PM project summary → Architect architecture summary → Technical Writer writes all docs
   → Call after enki_project_close() if documentation is needed

3. HITL reviews final state, then:
   enki_phase(action='advance', to='complete')
```

### NEVER in this phase
- NEVER modify code — all changes must have gone through the pipeline
- NEVER skip enki_wrap() — it runs inside enki_project_close() automatically

## PHASE: complete

### What this phase is
Session wrap-up and memory persistence.

### Exact sequence
```
1. enki_wrap()  → runs transcript → memory pipeline
2. Present final summary to human
```

---

## COMMON MISTAKES AND FIXES

| Mistake | Why it happens | Fix |
|---------|---------------|-----|
| Agent tool instead of Task tool | Forgetting the rule | ALWAYS use Task tool for agent spawning. Agent tool bypasses permission grants. |
| enki_wave returns same tasks | enki_complete not called | After dev+qa report for each task, call enki_complete(task_id) before calling enki_wave again |
| Gate blocks with "architect not completed" | enki_report not called after architect | Always call enki_report after every agent Task completion |
| enki_debate returns error about spec-draft | PM wrote spec to wrong path | PM must write to docs/spec-draft.md exactly |
| Dev explores codebase instead of building | description missing from task | Check context_artifact — description should be there. If empty, impl spec had no description. |
| Wave returns no tasks but sprint not done | Tasks in_progress from dead session | enki_wave auto-recovers on next call via tmux liveness check |
| enki_complete fails validator gate | Validator was never spawned | Validator gate only fires if validator was actually spawned. Check task context. |

---

## QUICK ORIENTATION COMMANDS

```bash
# What phase am I in and what's next?
enki_phase(action='status')

# What's the sprint progress?
enki_sprint_summary(sprint_id='...')

# What tasks are ready now?
enki_next_actions()

# What's in my inbox?
enki_mail_inbox()

# Generate sprint DAG diagram
enki_diagram(type='dag')

# Generate pipeline status diagram  
enki_diagram(type='pipeline')
```

---

## TOOL QUICK REFERENCE

| Tool | When | Never |
|------|------|-------|
| enki_wave | Start of each wave, after all enki_complete calls | Before enki_decompose, before enki_complete for current wave |
| enki_spawn | When pipeline requires an agent | Use Agent tool — always use Task tool to run the agent |
| enki_report | After EVERY agent Task completion | Call without running Task tool first |
| enki_complete | After dev+qa both reported for a task | Skip it — wave will loop forever without it |
| enki_decompose | Once, after architect approved, before first wave | Multiple times for same sprint |
| enki_approve | After every human verbal approval | Auto-advance without human seeing the output |
| enki_escalate | When blocked and human input needed | Improvise around blockers |
| enki_diagram | On demand for visualization | — |

---

## SESSION START — MANDATORY FIRST ACTION

**Every session, before any other action, print this banner:**

```
𒀭 Enki — {project} | Phase: {phase} | Tier: {tier}
Goal: {goal}
{sprint_status if implement/validating}
→ {next_action}
```

Values are in the injected context above. Print them verbatim.
Then proceed with next_action immediately without waiting for human input.
Do not ask "what would you like to work on?" — you already know.
