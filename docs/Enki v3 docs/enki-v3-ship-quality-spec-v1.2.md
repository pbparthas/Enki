# Enki v3 Ship & Quality Spec

> **Version**: 1.2
> **Date**: 2025-02-13
> **Status**: Draft
> **Scope**: Defines everything from "code review passes" to "deployed and verified" for PRODUCTS BUILT THROUGH ENKI. This spec does NOT cover tests for Enki itself — those are in the Implementation Spec Section 11. This spec covers what Enki does for the projects it builds.
> **Audience**: Architect reads this for the full lifecycle. EM reads this for phase definitions. QA reads this for test scope beyond task-level. DevOps reads this for deployment configuration. PM reads this for closure criteria.
> **Dependencies**: EM Orchestrator Spec v1.4 (phases, agents, tiers), Uru Gates Spec v1.1 (new phase gates), Abzu Memory Spec v1.2 (distillation triggers).
> **Problem statement**: EM defines development from intake through code review. But "ship" is a hand-wave. Task-level QA writes tests per task — no one owns regression, integration testing, E2E, or release qualification. No CI pipeline. No deployment definition. No rollback. The cycle has a beginning and middle, but no structured end.
> **v1.1 Changes**: Corrected regression to ISTQB definition (practice, not test level). Replaced Release Engineer with DevOps agent (user-configurable deploy). Added explicit scope: tests for products, not for Enki. Updated test pyramid to three levels + regression practice. Deployment is git + pipelines by default, user configures specifics.
> **v1.2 Changes**: Sprint-level Reviewer for cross-task consistency (review phase). Prism as qualify-phase tool for full codebase review + security scan. Docs agent removed (13 agents). Updated CI pipeline with Prism stage. Review coverage table (task/sprint/project levels).

---

## Table of Contents

1. [The Gap](#1-the-gap)
2. [Phase Extension](#2-phase-extension)
3. [Test Pyramid](#3-test-pyramid)
4. [Regression Practice](#4-regression-practice)
5. [CI Pipeline](#5-ci-pipeline)
6. [Release Qualification](#6-release-qualification)
7. [Deployment Pipeline](#7-deployment-pipeline)
8. [Rollback](#8-rollback)
9. [PM Closure](#9-pm-closure)
10. [Abzu Distillation Trigger](#10-abzu-distillation-trigger)
11. [Project Type Registry](#11-project-type-registry)
12. [DevOps Agent in Ship Phase](#12-devops-agent-in-ship-phase)
13. [Uru Gate Extensions](#13-uru-gate-extensions)
14. [Data Schemas](#14-data-schemas)
15. [Bill of Materials](#15-bill-of-materials)
16. [Anti-Patterns](#16-anti-patterns)

---

## 1. The Gap

Current state in EM spec:

```
implement → review → ship → ???
```

What "ship" means today: nothing. EM marks project done. PM writes a summary. Abzu distills from em.db. That's it.

What's missing:

| Level | What's Missing | Impact |
|---|---|---|
| **Task QA** | Tests exist but are throwaway | No accumulation, no regression suite |
| **Sprint** | No integration testing across tasks | Tasks work alone, break together |
| **Project** | No E2E testing, no release qualification | Ship without knowing if product works |
| **Deployment** | No pipeline, no artifact, no target | "Ship" means nothing operationally |
| **Post-ship** | No smoke test, no rollback plan | Broken deploy stays broken |
| **Closure** | PM summary exists but no acceptance verification | Project "done" without user sign-off |

---

## 2. Phase Extension

### Current EM Phases

```
intake → debate → plan → implement → review → ship
```

### Extended Phases

```
intake → debate → plan → implement → review → qualify → deploy → verify → close
```

| New Phase | What Happens | Gate |
|---|---|---|
| **qualify** | CI passes, regression passes, E2E passes, release candidate built | Gate: CI green + regression green |
| **deploy** | Artifact deployed to target environment | Gate: qualify complete |
| **verify** | Smoke tests in target environment, PM acceptance check | Gate: deploy successful |
| **close** | PM closure, Abzu distillation, em.db archived | Gate: verify passed + PM sign-off |

### Tier Applicability

| Phase | Minimal | Standard | Full |
|---|---|---|---|
| qualify | Lint + existing tests pass | CI + regression | CI + regression + E2E |
| deploy | Manual / copy | Scripted deploy | Full pipeline with staging |
| verify | Manual smoke | Automated smoke | Automated smoke + PM acceptance |
| close | Auto (session-end) | PM summary | PM summary + retrospective |

Minimal tier projects (typo fixes, config changes) still run through qualify/deploy/verify/close — the overhead is just smaller. A typo fix still needs to pass lint and not break existing tests.

---

## 3. Test Pyramid

### Scope Clarification

**This section covers tests that Enki writes for the PRODUCTS it builds** (QualityPilot, SongKeeper, Cortex, Orion, etc.). Tests for Enki itself (verifying gates, memory, orchestration) are a separate concern defined in the Implementation Spec.

### The Three Test Levels

```
                    ┌─────────┐
                    │  E2E    │  ← Project-level: does the product meet acceptance criteria?
                   ┌┴─────────┴┐
                   │ Integration │  ← Sprint-level: do tasks work together?
                 ┌─┴────────────┴─┐
                 │  Unit / Task    │  ← Task-level: does this function work?
                 └────────────────┘
```

**Regression is NOT a test level.** It's a practice — see Section 4.

### Level 1: Unit / Task Tests (Existing — QA Agent)

**Owner**: QA agent (per task)
**When**: During implement phase, per task
**What**: Tests derived from spec for the specific task
**Lifespan**: Lives with the implementation. Refactor the code, tests may need updating.
**Storage**: `tests/unit/{module}/`
**Already defined in**: EM Spec Section 10 (TDD Flow)

No changes needed here. This works.

### Level 2: Integration Tests (NEW)

**Owner**: QA agent (sprint-level spawn)
**When**: After sprint tasks complete, during review phase
**What**: Tests that verify cross-task interactions within a sprint
**Lifespan**: Sprint-scoped. May not survive architectural changes across sprints.

**How integration tests differ from unit tests:**

| Unit (task) | Integration (sprint) |
|---|---|
| Tests auth login function in isolation | Tests auth login → get token → access protected route |
| Tests rate limiter counts correctly | Tests rate limiter + auth + endpoint together |
| Mocks external dependencies | Uses real (local) dependencies where possible |

**EM's role**: After sprint tasks complete, EM spawns QA at sprint-level with:
- All task specs from the sprint
- List of files modified across all tasks
- Cross-task dependency map (from task_graph.py)

QA (sprint-level) writes integration tests targeting the interaction points between tasks.

### Level 3: E2E Tests (NEW — Full Tier Only)

**Owner**: QA agent (project-level spawn)
**When**: After final sprint, during qualify phase
**What**: Tests that verify the product meets acceptance criteria from the Product Spec
**Lifespan**: Project-scoped.

**E2E tests are derived from the Product Spec, not the Implementation Spec.** They test WHAT the user asked for, not HOW it was built.

```
Product Spec says: "User can log in with email and password"
    → E2E test: POST /login with valid credentials → 200 + JWT token

Product Spec says: "Rate limiting prevents brute force"
    → E2E test: 10 rapid login attempts → 429 after 5th
```

**PM provides acceptance criteria.** QA (project-level) writes E2E tests from those criteria. This is the final validation before deploy.

---

## 4. Regression Practice

### What Regression Testing Actually Is (ISTQB)

Regression testing is **not a test type**. It's a **practice** — the act of re-running existing tests after changes to verify nothing broke.

Per ISTQB: "Testing of a previously tested program following modification to ensure that defects have not been introduced or uncovered in unchanged areas of the software."

This means:
- You don't "write regression tests" as a separate artifact category
- You **select from your existing tests** (unit, integration, E2E) and re-run them after changes
- The regression suite is a **curated subset** of tests from all levels that cover critical functionality

### The Regression Suite

The regression suite is a **curated, permanent collection** drawn from unit, integration, and E2E tests. It's the "safety net" — the set of tests that MUST pass before any release.

**What goes into the regression suite:**

| Source Level | What Gets Selected | Why |
|---|---|---|
| Unit | Stable contract tests (e.g., `test_hash_password_returns_bcrypt`) | Tests a stable API contract, not an implementation detail |
| Integration | Stable cross-module tests (e.g., `test_auth_login_returns_valid_token`) | Tests a critical interaction path |
| E2E | Most E2E tests | User journeys are prime regression candidates |

**What does NOT go into the regression suite:**
- Unit tests tightly coupled to implementation (break on refactor — that's fine, but not regression-worthy)
- One-off exploratory tests
- Tests for deprecated features

### How the Regression Suite Grows

```
Sprint 1 complete:
    → QA (sprint-level) reviews all tests from Sprint 1
    → Selects tests that cover critical user-facing behavior
    → Those tests → regression suite manifest (tests/regression/suite.json)

Sprint 2 complete:
    → QA runs existing regression suite first (do Sprint 2 changes break Sprint 1?)
    → QA reviews Sprint 2 tests, selects new regression candidates
    → New selections → added to regression suite
    → Suite grows monotonically
```

**Key rule: Regression suite ONLY GROWS.** Tests are never removed unless:
- The feature they test is deliberately removed (PM authorizes)
- Removal is logged in em.db mail thread with documented reason

### When Regression Runs

Regression suite runs at two points:
1. **After each sprint** (during review phase): Verify new sprint didn't break old behavior
2. **During qualify phase**: Full regression run as part of CI pipeline

### Storage

```
tests/
├── unit/                    # Task-level tests (QA per task)
│   ├── auth/
│   │   ├── test_login.py
│   │   └── test_refresh.py
│   └── routes/
│       └── test_protected.py
├── integration/             # Sprint-level cross-task tests
│   ├── sprint1/
│   │   └── test_auth_flow.py
│   └── sprint2/
│       └── test_rate_limit_auth.py
├── e2e/                     # Project-level acceptance tests (Full tier)
│   ├── test_user_journey.py
│   └── test_security_baseline.py
├── regression/              # Manifest only — tests live in unit/integration/e2e
│   ├── suite.json           # Registry: which tests are in the regression suite
│   └── history.json         # Pass/fail history per run
├── fixtures/                # Shared test fixtures
│   └── ...
└── conftest.py              # Shared config (pytest) or equivalent
```

**Note**: `tests/regression/` contains only the manifest and history. The actual test files live in `tests/unit/`, `tests/integration/`, and `tests/e2e/`. The manifest is a list of paths.

### suite.json Format

```json
{
  "version": 1,
  "last_updated": "2025-02-13T10:00:00Z",
  "tests": [
    {
      "path": "tests/unit/auth/test_login.py::test_valid_credentials_returns_token",
      "added_in_sprint": 1,
      "added_at": "2025-02-05T14:00:00Z",
      "source": "unit",
      "reason": "Critical auth contract — must never break"
    },
    {
      "path": "tests/integration/sprint1/test_auth_flow.py::test_login_token_access",
      "added_in_sprint": 1,
      "added_at": "2025-02-05T14:00:00Z",
      "source": "integration",
      "reason": "Full auth flow across modules"
    },
    {
      "path": "tests/e2e/test_user_journey.py::test_complete_registration_flow",
      "added_in_sprint": 3,
      "added_at": "2025-02-13T09:00:00Z",
      "source": "e2e",
      "reason": "End-to-end user registration — acceptance criterion"
    }
  ]
}
```

### history.json — Run History

```json
{
  "runs": [
    {
      "id": "run-001",
      "timestamp": "2025-02-13T14:00:00Z",
      "trigger": "sprint_complete",
      "sprint": 2,
      "total": 12,
      "passed": 12,
      "failed": 0,
      "skipped": 0,
      "duration_seconds": 45,
      "result": "PASS"
    }
  ]
}
```

### Regression Run Triggers

| Trigger | When | Scope |
|---|---|---|
| Sprint complete | After all sprint tasks pass review | Full regression suite |
| Pre-qualify | Before entering qualify phase | Full suite + integration + E2E |
| Pre-deploy | Before deployment (if separate from qualify) | Smoke subset + critical path |
| On-demand | Human requests | Configurable (full, smoke, specific tags) |

---

## 5. CI Pipeline

### What CI Means in Enki

CI is not GitHub Actions (though it can be). CI is the set of automated checks that run before code is considered shippable. Enki's CI is defined per project type and runs during the **qualify** phase.

### CI Check Categories

| Category | Checks | Blocking? | Notes |
|---|---|---|---|
| **Static Analysis** | Lint, type check, formatting | Blocking | |
| **Security (task)** | Dependency audit, secret scan | Blocking | Per-task InfoSec already caught issues; this is CI verification |
| **Unit Tests** | Task-level test suite | Blocking | Some may be implementation-coupled. Must pass at qualify. |
| **Integration** | Sprint-level cross-task tests | Blocking | Current sprint's integration tests |
| **Regression** | Re-run curated suite from all test levels | Blocking | **Critical gate.** Re-runs selected unit/integration/E2E tests. |
| **E2E** | Acceptance criteria tests (Full tier only) | Blocking | |
| **Prism Code Review** | Full codebase quality scan | Blocking (P0/P1), Advisory (P2/P3) | Prism is external tool, not agent. Tree-sitter + static analysis + LLM. |
| **Prism Security** | Full codebase security scan | Blocking (P0/P1), Advisory (P2/P3) | OWASP, secrets, input validation — full codebase scope |
| **Performance** | Benchmark regression (if baseline exists) | Advisory | |
| **Coverage** | Test coverage report | Advisory (no hard threshold) | |

### CI Pipeline Definition

```yaml
# Project-level CI definition: .enki/ci.yaml
# EM reads this during qualify phase and executes checks in order

pipeline:
  name: "project-ci"
  
  stages:
    - name: "static"
      parallel: true
      checks:
        - type: "lint"
          command: "{project_type.lint_command}"
          blocking: true
        - type: "typecheck" 
          command: "{project_type.typecheck_command}"
          blocking: true
        - type: "format"
          command: "{project_type.format_command}"
          blocking: true
    
    - name: "security"
      parallel: true
      checks:
        - type: "prism"
          command: "prism review --severity P0,P1"
          blocking: true
        - type: "secrets"
          command: "{project_type.secret_scan_command}"
          blocking: true
        - type: "dependencies"
          command: "{project_type.dep_audit_command}"
          blocking: true
    
    - name: "tests"
      parallel: false
      checks:
        - type: "unit"
          command: "{project_type.test_command} tests/unit/"
          blocking: true
        - type: "integration"
          command: "{project_type.test_command} tests/integration/"
          blocking: true
    
    - name: "regression"
      parallel: false
      checks:
        - type: "regression"
          command: "enki regression run"
          blocking: true
          note: "CRITICAL — runs curated suite from tests/regression/suite.json manifest. Tests live in unit/integration/e2e dirs."
    
    - name: "e2e"
      condition: "tier == 'full'"
      checks:
        - type: "e2e"
          command: "{project_type.test_command} tests/e2e/"
          blocking: true
    
    - name: "advisory"
      parallel: true
      checks:
        - type: "coverage"
          command: "{project_type.coverage_command}"
          blocking: false
    
    - name: "prism"
      parallel: true
      checks:
        - type: "prism-quality"
          command: "prism review --full"
          blocking: true
          note: "Full codebase code quality scan. P0/P1 block, P2/P3 logged as tech debt."
        - type: "prism-security"
          command: "prism security --full"
          blocking: true
          note: "Full codebase security scan. P0/P1 block, P2/P3 logged as tech debt."
```

### CI Execution Model

EM runs CI checks during qualify phase. Each check is executed as a bash command. EM captures stdout/stderr and exit code.

```
Qualify phase starts
    → EM reads .enki/ci.yaml
    → Stage 1 (static): lint ∥ typecheck ∥ format
        All pass? → continue
        Any blocking fail? → STOP. Report to human.
    → Stage 2 (security): prism ∥ secrets ∥ deps
        All pass? → continue
        Any blocking fail? → STOP. Report to human.
    → Stage 3 (tests): unit → integration (sequential)
        All pass? → continue
        Any fail? → STOP. Report failures. EM files bugs.
    → Stage 4 (regression): behavioral contract tests
        Pass? → continue
        Fail? → CRITICAL. This means user-facing behavior broke.
                EM files P0 bug. Cannot proceed to deploy.
    → Stage 5 (e2e): if Full tier
        Pass? → continue
        Fail? → STOP. Report. EM files bugs.
    → Stage 6 (advisory): coverage ∥ prism-quality
        Results reported but don't block.
    → All blocking checks pass → qualify COMPLETE
```

### CI Results Storage

CI results are stored in em.db and reported via mail:

```sql
-- New table in em.db
CREATE TABLE ci_runs (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    sprint INTEGER,
    trigger TEXT NOT NULL,          -- 'sprint_complete', 'pre_qualify', 'pre_deploy', 'manual'
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running',  -- 'running', 'passed', 'failed', 'cancelled'
    stages_json TEXT,               -- JSON: per-stage results with timing
    blocking_failures TEXT,         -- JSON: list of blocking check failures
    advisory_results TEXT           -- JSON: coverage, quality scan results
);
```

### First-Sprint CI Bootstrap

For the first sprint of a new project, there's no regression suite yet. CI still runs:
- Static analysis (lint, types, format)
- Security (Prism, secrets, deps)
- Unit tests (from Sprint 1 tasks)
- Integration tests (from Sprint 1)

Regression suite starts accumulating after Sprint 1 completes.

---

## 6. Release Qualification

### What Qualifies a Release

A release candidate is qualified when:

1. **All blocking CI checks pass** — no lint errors, no P0/P1 Prism findings, no failing tests
2. **Regression suite is green** — accumulated tests from all sprints pass
3. **E2E tests pass** (Full tier) — acceptance criteria verified
4. **No open P0/P1 bugs** — all critical bugs from review are resolved
5. **Human sign-off** — for Standard/Full tier, human confirms release readiness

### Qualification Report

EM generates a qualification report during the qualify phase:

```markdown
# Release Qualification Report
Project: my-project
Sprint: 3 (final)
Date: 2025-02-13

## CI Results
- Static analysis: ✅ PASS (lint, typecheck, format)
- Security (task-level): ✅ PASS (secrets, deps)
- Unit tests: ✅ PASS (47/47)
- Integration tests: ✅ PASS (12/12)
- Regression: ✅ PASS (59/59)
- E2E: ✅ PASS (8/8)
- Prism code review: ✅ PASS (0 P0/P1, 3 P2 logged as tech debt)
- Prism security scan: ✅ PASS (0 P0/P1, 1 P3 logged)

## Review Coverage
- Task-level: Reviewer passed all 12 tasks
- Sprint-level: Reviewer cross-task consistency — 2 P2 issues fixed
- Project-level: Prism full scan — clean

## Coverage
- Line coverage: 78%
- Branch coverage: 64%

## Open Bugs
- P0: 0
- P1: 0
- P2: 3 (deferred to next release)

## Advisory
- Prism P2/P3: 7 findings (reviewed, accepted)

## Verdict: QUALIFIED FOR DEPLOY
```

### Qualification Gate

```python
def is_qualified(project: str) -> bool:
    """Check if project passes release qualification."""
    latest_ci = get_latest_ci_run(project)
    
    if not latest_ci or latest_ci.status != "passed":
        return False
    
    open_bugs = get_open_bugs(project, severity=["P0", "P1"])
    if open_bugs:
        return False
    
    tier = get_tier(project)
    if tier in ("standard", "full"):
        if not get_human_signoff(project, "release"):
            return False
    
    return True
```

---

## 7. Deployment Pipeline

### Deployment Is Project-Type-Specific

Different projects deploy differently. The deployment pipeline is configured per project type in `.enki/deploy.yaml`.

### Deployment Targets

| Project Type | Artifact | Target | Deploy Method |
|---|---|---|---|
| Web app (Python) | Docker image | Orion (orionforge.dev) | Docker push + restart |
| Web app (Node/TS) | Docker image or build dir | Orion | Docker push or rsync |
| CLI tool (Python) | pip package | PyPI or local | `pip install` / `twine upload` |
| CLI tool (Node) | npm package | npm registry or local | `npm publish` |
| Library (Python) | pip package | PyPI | `twine upload` |
| Library (TS) | npm package | npm | `npm publish` |
| Enki itself | Python package + hooks | `~/.enki/` | Custom installer |
| Static site | Build dir | Orion / CDN | rsync / deploy |
| Mobile (Flutter) | APK / IPA | Store / sideload | Build + sign |

### deploy.yaml

```yaml
# .enki/deploy.yaml — project deployment configuration

target:
  type: "docker"                    # docker, package, binary, static, custom
  
  # Docker-specific
  registry: "orionforge.dev:5000"   # or docker.io, ghcr.io
  image_name: "my-project"
  dockerfile: "Dockerfile"
  
  # Environment targets
  environments:
    staging:
      host: "staging.orionforge.dev"
      port: 22
      deploy_command: "docker compose pull && docker compose up -d"
    production:
      host: "orionforge.dev"
      port: 22
      deploy_command: "docker compose pull && docker compose up -d"

  # Pre-deploy checks
  pre_deploy:
    - "docker build -t {image_name}:{version} ."
    - "docker push {registry}/{image_name}:{version}"
  
  # Post-deploy verification
  post_deploy:
    - "curl -sf https://{host}/health || exit 1"
    - "curl -sf https://{host}/api/version | grep {version}"

rollback:
  strategy: "previous_version"      # previous_version, specific_tag, manual
  command: "docker compose pull && docker compose up -d"
```

### Deployment Flow

```
Qualify phase complete (all CI green, bugs resolved)
    → Human approves deploy (Standard/Full) or auto-proceed (Minimal)
    → EM enters deploy phase
    → Read .enki/deploy.yaml
    → Build artifact
        Docker: docker build + push
        Package: build + publish
        Static: build
    → Deploy to target
        Docker: SSH + docker compose
        Package: twine/npm publish
        Static: rsync
    → Enter verify phase
    → Run post-deploy checks (smoke tests)
        Health endpoint responds?
        Version matches?
        Core functionality works?
    → Verify passes → close phase
    → Verify fails → rollback triggered
```

### Staging (Full Tier)

Full tier projects deploy to staging first:

```
qualify → deploy (staging) → verify (staging) → human approval → deploy (production) → verify (production) → close
```

Standard and Minimal skip staging — deploy directly to production (or final target).

---

## 8. Rollback

### When Rollback Triggers

| Trigger | Automatic? | Action |
|---|---|---|
| Post-deploy health check fails | Yes | Roll back immediately |
| Post-deploy smoke test fails | Yes | Roll back immediately |
| Human reports broken after deploy | No (manual) | Roll back on request |
| Performance degradation detected | No (advisory) | Human decides |

### Rollback Strategies

| Strategy | How | When |
|---|---|---|
| **Previous version** | Re-deploy the last known good version (image tag, package version) | Default for Docker/package deploys |
| **Git revert** | Revert the merge commit, re-deploy | When version tagging isn't available |
| **Manual** | Human handles it | Custom deploy targets |

### Rollback Data

```sql
-- New table in em.db
CREATE TABLE deploy_log (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    environment TEXT NOT NULL,       -- 'staging', 'production'
    version TEXT NOT NULL,
    artifact_type TEXT NOT NULL,     -- 'docker', 'package', 'binary', 'static'
    artifact_ref TEXT,               -- image tag, package version, commit SHA
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deployed_by TEXT,                -- 'em', 'human_cli'
    verify_status TEXT,              -- 'pending', 'passed', 'failed'
    verify_completed_at TIMESTAMP,
    rollback_of TEXT,                -- deploy_id of the deploy this rolled back
    rolled_back_at TIMESTAMP,
    rolled_back_by TEXT
);
```

Every deploy is logged. Rollback creates a new deploy entry pointing to the previous version, with `rollback_of` linking to the failed deploy.

---

## 9. PM Closure

### Closure Is Not Just "Mark Done"

PM closure verifies the project delivered what was promised. This is the acceptance test at the human level.

### PM Closure Checklist

```
PM spawned at close phase:
    1. Read Product Spec acceptance criteria
    2. Read qualification report (CI results, test results, coverage)
    3. Read deploy log (what was deployed, where)
    4. Verify acceptance criteria against E2E test results
    5. Customer presentation (Standard/Full tiers):
        - Present delivered features vs planned scope
        - Present descoped items with rationale
        - Present known tech debt
        - Present deployment status and how to use
    6. Customer sign-off (human approves or requests changes)
    7. Write project summary:
        - What was delivered vs what was planned
        - What was descoped and why
        - Open items / tech debt logged
        - Lessons learned (→ bead candidates)
    8. Update Yggdrasil with final status
    9. Send closure mail to human
    10. Trigger Abzu distillation (Section 10)
    11. Archive em.db (30-day retention)
```

**Gate**: Customer sign-off (step 6) must succeed before PM writes closure report. If customer requests changes, those become change requests routed through PM (EM Spec Section 4).

### Closure Report

```markdown
# Project Closure Report
Project: my-project
Started: 2025-02-01
Closed: 2025-02-13

## Delivery Summary
- 3 sprints, 12 tasks completed
- 2 tasks descoped (moved to backlog with documented reasons)
- 59 unit tests, 12 integration tests, 8 E2E tests

## Acceptance Criteria
| Criterion | Status | Evidence |
|---|---|---|
| User can log in | ✅ Met | E2E test_user_login |
| Rate limiting works | ✅ Met | E2E test_rate_limit |
| Admin dashboard | ⚠️ Partial | Dashboard exists, 2 features descoped |

## Deployment
- Deployed to orionforge.dev on 2025-02-13
- Version: 1.0.0
- Smoke tests: passed

## Tech Debt
- P2 bug #7: timezone handling in reports (deferred)
- P3: test coverage for edge cases in auth refresh

## Lessons Learned
- JWT refresh token rotation added complexity; decision was sound but estimate was off
- Integration tests caught 3 bugs that unit tests missed — integration-first next time
```

---

## 10. Abzu Distillation Trigger

### When Distillation Fires

Currently defined in Abzu spec: "When a project completes, Abzu reads em.db mail threads."

This spec makes that precise:

```
PM closure complete
    → PM sends "project_closed" mail to EM thread
    → EM marks project state as "closed" in em.db
    → Session-end hook detects closed project
    → Triggers Abzu project completion extraction:
        1. Read em.db mail threads (all conversations)
        2. Read em.db bugs (patterns, recurrences)
        3. Read em.db pm_decisions (what was learned)
        4. Read qualification report (what worked, what didn't)
        5. Heuristic extraction: decisions, patterns, fixes
        6. CC distillation: richer extraction with project context
        7. Candidates → abzu.db staging
    → em.db retained for 30 days
    → After 30 days: em.db archived to cold storage, then deleted
```

### What Becomes Bead Candidates from Closure

| Source | Example Candidates |
|---|---|
| PM closure report | "JWT refresh rotation: estimate 2 days, took 4. Factor 2x for auth-related work." |
| Bug patterns | "Integration tests caught 3 bugs unit tests missed in auth module." |
| Architectural decisions | "WAL mode on SQLite resolved concurrency issues with parallel agents." |
| Descoped items | "Admin dashboard chart library: chose Chart.js over D3 for simpler API." |
| Deployment learnings | "Docker multi-stage build reduced image size from 800MB to 120MB." |

---

## 11. Project Type Registry

### Purpose

Different projects have different lint commands, test commands, build commands, and deployment targets. Rather than hard-coding these, Enki maintains a project type registry.

### Registry

```toml
# ~/.enki/config/project_types.toml

[python]
lint_command = "ruff check ."
typecheck_command = "mypy src/"
format_command = "ruff format --check ."
test_command = "pytest"
coverage_command = "pytest --cov=src --cov-report=term-missing"
secret_scan_command = "detect-secrets scan"
dep_audit_command = "pip-audit"
build_command = "python -m build"
package_type = "pip"

[typescript]
lint_command = "eslint src/"
typecheck_command = "tsc --noEmit"
format_command = "prettier --check src/"
test_command = "vitest run"
coverage_command = "vitest run --coverage"
secret_scan_command = "detect-secrets scan"
dep_audit_command = "npm audit"
build_command = "npm run build"
package_type = "npm"

[node]
lint_command = "eslint src/"
typecheck_command = "echo 'no typecheck for JS'"
format_command = "prettier --check src/"
test_command = "jest"
coverage_command = "jest --coverage"
secret_scan_command = "detect-secrets scan"
dep_audit_command = "npm audit"
build_command = "npm run build"
package_type = "npm"

[flutter]
lint_command = "flutter analyze"
typecheck_command = "echo 'Dart type system is built-in'"
format_command = "dart format --set-exit-if-changed ."
test_command = "flutter test"
coverage_command = "flutter test --coverage"
secret_scan_command = "detect-secrets scan"
dep_audit_command = "echo 'no standard audit for pub'"
build_command = "flutter build apk --release"
package_type = "apk"

[static]
lint_command = "echo 'no lint for static'"
typecheck_command = "echo 'no typecheck for static'"
format_command = "prettier --check ."
test_command = "echo 'no tests for static site'"
coverage_command = "echo 'n/a'"
secret_scan_command = "detect-secrets scan"
dep_audit_command = "echo 'n/a'"
build_command = "npm run build"
package_type = "static"
```

### Project Configuration

Each project declares its type in `.enki/project.toml`:

```toml
[project]
name = "my-project"
type = "typescript"             # references project_types.toml
deploy_target = "docker"        # docker, package, binary, static, custom

[overrides]
# Override any command from project_types.toml
test_command = "npm run test:ci"
```

---

## 12. DevOps Agent in Ship Phase

### Why

No existing agent owns the qualify → deploy → verify flow. Dev writes code. QA writes tests. Reviewer reviews. InfoSec scans. But nobody:
- Runs CI pipeline orchestration
- Builds artifacts
- Deploys to targets
- Runs post-deploy verification
- Handles rollback

DevOps (defined in EM Spec Section 7) handles this. DevOps is NOT a fixed deployment model — it reads the user's deploy configuration and executes accordingly. Git + pipelines is the common denominator.

### Agent Definition

| | |
|---|---|
| **Name** | DevOps |
| **Spawned by** | EM |
| **When** | qualify, deploy, and verify phases (also during implement if task modifies CI/CD config) |
| **Receives** | CI config, deploy config, qualification report, project type, **user's deploy preferences** |
| **Produces** | CI run results, built artifacts, deploy logs, verify results |
| **Writes to** | em.db (ci_runs, deploy_log), project repo (.github/workflows, Dockerfile, deploy scripts) |
| **Tier** | Standard + Full (Minimal uses simplified inline logic in EM) |

### User-Configurable Deployment

The user decides how to ship. DevOps reads this from `.enki/deploy.yaml`:

```yaml
# User configures their preferred deploy method
pipeline:
  provider: "github_actions"    # github_actions, gitlab_ci, manual, custom
  # OR
  provider: "manual"
  # OR
  provider: "custom"
  custom_script: "scripts/deploy.sh"

deploy:
  method: "docker"              # docker, package, rsync, git_push, custom
  target: "orionforge.dev"
```

**DevOps adapts to what the user wants.** Some users want GitHub Actions + Docker. Some want a simple git push. Some want npm publish. DevOps reads the config and executes.

### DevOps Responsibilities by Phase

**Qualify phase:**
1. Read `.enki/ci.yaml` and project type config
2. Execute CI stages in order
3. Collect results, report blocking failures
4. File bugs for failing checks (EM routes to Dev)
5. Generate qualification report

**Deploy phase:**
1. Read `.enki/deploy.yaml` (user's deploy preferences)
2. Build artifact per project type
3. Push to registry/target per user config
4. Execute deploy command (git push, docker compose, npm publish, etc.)
5. Log deployment to em.db

**Verify phase:**
1. Run post-deploy checks (health, version, smoke)
2. Report results
3. If verify fails → trigger rollback
4. If verify passes → signal ready for PM closure

### DevOps in the Mail System

DevOps communicates via em.db mail like all agents. Thread hierarchy:

```
Project thread
├── Sprint 1 thread
│   └── ...
├── Sprint 2 thread
│   └── ...
└── Release thread
    ├── DevOps: "CI pipeline started"
    ├── DevOps: "Static analysis: PASS"
    ├── DevOps: "Unit tests: PASS (47/47)"
    ├── DevOps: "Regression suite: PASS (59/59)"
    ├── DevOps: "Qualification report ready"
    ├── DevOps: "Deploying to staging..."
    ├── DevOps: "Deploy complete. Running verification."
    ├── DevOps: "Verification PASSED."
    └── EM: "Ready for PM closure."
```

---

## 13. Uru Gate Extensions

### New Phase Gates

The qualify → deploy → verify → close phases need gate enforcement:

| Gate | Phase Required | Check |
|---|---|---|
| Gate 4 (NEW) | qualify | Can only enter qualify if all sprint reviews are complete |
| Gate 5 (NEW) | deploy | Can only enter deploy if qualify is complete (CI green) |
| Gate 6 (NEW) | verify | Can only enter verify if deploy is complete |
| Gate 7 (NEW) | close | Can only close if verify passed AND PM signed off |

### Implementation Note

These gates are phase-sequence enforcement in em.db, checked by Uru's existing phase gate (Gate 3). Gate 3 already checks `phase >= implement` for code writes. The extension is:

```python
# No new gate logic needed — just extend the phase sequence
PHASE_ORDER = [
    "intake", "debate", "plan", "implement", "review",
    "qualify", "deploy", "verify", "close"
]

# Gate 3 already checks: current_phase >= required_phase
# Adding qualify/deploy/verify/close to the sequence is sufficient
```

The real enforcement is that EM can't advance phase without the preceding phase's conditions being met. Phase transitions are logged and, for Standard/Full, require human approval.

### Deploy Gate Special Case

Deploy phase has an additional structural gate: the human approval for deploy is SEPARATE from the spec approval (Gate 2). Even if the spec was approved earlier, deploying to production requires a fresh human sign-off.

```python
def can_deploy(project: str) -> bool:
    """Check deploy readiness. Separate from spec approval."""
    # Qualify must be complete
    if not is_phase_complete(project, "qualify"):
        return False
    
    tier = get_tier(project)
    
    # Minimal: auto-approve deploy
    if tier == "minimal":
        return True
    
    # Standard/Full: human must approve deploy separately
    return get_human_signoff(project, "deploy")
```

---

## 14. Data Schemas

### New Tables in em.db

```sql
-- CI run results
CREATE TABLE ci_runs (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    sprint INTEGER,
    trigger TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running',
    stages_json TEXT,
    blocking_failures TEXT,
    advisory_results TEXT
);

CREATE INDEX idx_ci_project ON ci_runs(project, status);

-- Deployment log
CREATE TABLE deploy_log (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    environment TEXT NOT NULL,
    version TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_ref TEXT,
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deployed_by TEXT,
    verify_status TEXT DEFAULT 'pending',
    verify_completed_at TIMESTAMP,
    rollback_of TEXT,
    rolled_back_at TIMESTAMP,
    rolled_back_by TEXT
);

CREATE INDEX idx_deploy_project ON deploy_log(project, environment);

-- Regression suite manifest (denormalized from suite.json for queries)
-- These are REFERENCES to existing tests, not a separate test category
CREATE TABLE regression_tests (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    path TEXT NOT NULL,
    category TEXT NOT NULL,            -- 'unit', 'integration', 'e2e'
    added_in_sprint INTEGER,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_task TEXT,
    tags TEXT,                          -- JSON array
    flaky INTEGER DEFAULT 0,
    removed INTEGER DEFAULT 0,
    removed_reason TEXT,
    removed_by TEXT
);

CREATE INDEX idx_regression_project ON regression_tests(project, removed);
```

---

## 15. Bill of Materials

### New Modules

| File | ~Lines | What |
|---|---|---|
| `orch/devops.py` | ~500 | DevOps agent: CI execution, deploy per user config, verify, rollback |
| `orch/ci.py` | ~400 | CI pipeline parser, stage execution, result collection |
| `orch/deploy.py` | ~350 | Deployment execution, artifact building, target management, user config reading |
| `orch/regression.py` | ~300 | Regression suite management: selection from existing tests, manifest, history |
| `orch/qualify.py` | ~200 | Qualification check, report generation |

**Total new: ~1,750 lines**

### Changes to Existing Modules

| File | Change |
|---|---|
| `orch/orchestrator.py` | Add qualify/deploy/verify/close phases, DevOps spawn |
| `orch/agents.py` | Add DevOps definition + prompts (replaces Release Engineer) |
| `orch/schemas.py` | Add ci_runs, deploy_log, regression_tests tables |
| `gates/uru.py` | Extend PHASE_ORDER with new phases |
| `memory/abzu.py` | Wire project closure distillation trigger |
| `mcp/orch_tools.py` | Add enki_qualify, enki_deploy, enki_rollback tools |

### Updated Total

| Component | Before Ship Spec | After Ship Spec |
|---|---|---|
| Uru (Gates) | ~910 | ~930 (phase list update) |
| Abzu (Memory) | ~2,300 | ~2,350 (closure trigger wiring) |
| EM (Orchestration) | ~5,600 | ~7,350 (+1,750 new modules) |
| **Total** | **~9,640** | **~11,460** |

---

## 16. Anti-Patterns

### What This Spec Avoids

| Anti-Pattern | Why It's Wrong | This Spec's Answer |
|---|---|---|
| Writing "regression tests" as a separate category | Regression testing is a practice (re-running existing tests), not a test type. Per ISTQB. | Regression suite is a curated selection FROM existing unit/integration/E2E tests. QA selects which tests enter the suite. |
| No regression suite at all | New code breaks old behavior silently. Nobody notices until production. | Curated regression suite grows monotonically. QA selects critical tests per sprint. |
| "It works on my machine" | No CI, no standard environment, works locally but not deployed. | CI pipeline with standard checks per project type. |
| Manual deployment | Copy files to server, hope for the best. | DevOps agent reads user's deploy config. Scripted, logged in em.db. |
| Ship without acceptance check | Product is "done" but nobody verified it meets requirements. | E2E tests from Product Spec + PM customer presentation + closure checklist. |
| No rollback plan | Deploy breaks prod, manual scramble to fix. | Automatic rollback on health check failure. Previous version always available. |
| Separate CI for each project | Every project reinvents its CI from scratch. | Project type registry. Declare type, inherit standard checks. |
| Test coverage as gate | "Must have 80% coverage" leads to garbage tests. | Coverage is advisory, not blocking. Quality over quantity. |
| QA only at task level | QA verifies each piece works alone. Nobody checks if pieces work together. | Integration tests (sprint), regression practice (curated subset), E2E (acceptance). |
| Fixed deployment model | Not every project ships the same way. Users have different CI/CD preferences. | DevOps reads `.enki/deploy.yaml`. Git + pipelines default, user configures specifics. |

---

*End of Enki v3 Ship & Quality Spec v1.2*
