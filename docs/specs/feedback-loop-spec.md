# Spec: Close Enki's Feedback Loops

## Problem

Enki has two disconnected feedback loops:

1. **Knowledge loop** (session → learning): Sessions generate violations, interceptions, and outcomes, but learnings aren't extracted and stored automatically.
2. **Enforcement loop** (outcomes → adjustment): Gate violations, FP rates, and evasions are tracked but never feed back into enforcement changes.

## Solution: Two Layers

### Layer 1: Reflector (already written, needs integration)

**Source files**: `/home/partha/Downloads/files(4)/reflector.py`, `test_reflector.py`, `REFLECTOR_INTEGRATION.md`

Pipeline: `Session → Gather Trace → Reflect (heuristics) → Distill Skills → Store Beads`

Key design decision: No LLM for reflection — pure heuristic analysis prevents self-rationalization.

### Layer 2: Feedback Loop (new module)

Pipeline: `Accumulated Data → Analyze FP/Evasions → Propose Adjustments → Present to Human → Human Decides → Track Outcome`

Bridges:
- `ereshkigal.py` (FP rates, evasion data) → `patterns.json` (pattern changes)
- `evolution.py` (gate adjustments) → `enforcement.py` (gate behavior)

---

## Implementation Steps

### Step 0: Two-Tier Evolution (Local → Promote → Global)

**Problem with flat global:** A single `~/.enki/EVOLUTION.md` written by all projects bloats fast and gives CC a single point to influence cross-project enforcement.

**Design:** Local evolution per-project, mechanical promotion to global.

```
Project A session → ProjectA/.enki/EVOLUTION.md (local)
Project B session → ProjectB/.enki/EVOLUTION.md (local)
                              ↓
                    promote_to_global() — mechanical, no AI
                              ↓
                    ~/.enki/EVOLUTION.md (global, distilled)
```

#### What lives where

| File | Scope | Written by | Contains |
|------|-------|-----------|----------|
| `<project>/.enki/EVOLUTION.md` | Per-project | Session-end feedback cycle | All corrections, adjustments, proposals for this project |
| `~/.enki/EVOLUTION.md` | Global | Promotion script only | Only `applied` or `acknowledged` proposals from any project |
| `~/.enki/EVOLUTION_ARCHIVE.md` | Global | Pruning during `enki_maintain` | Archived old global entries (append-only) |

**Modify `src/enki/evolution.py`**:
- Add `get_local_evolution_path(project_path)` → `project_path / ".enki" / "EVOLUTION.md"`
- Add `get_global_evolution_path()` → `Path.home() / ".enki" / "EVOLUTION.md"`
- Refactor `load_evolution_state()` and `save_evolution_state()` to take explicit path
- Add `promote_to_global(project_path)`: mechanical promotion of applied/acknowledged proposals. NOT promoted: `reason` field (CC's rationale). Dedup by (proposal_type, target).
- Add `migrate_per_project_evolution(project_path)`: idempotent migration marker
- Add `get_evolution_context_for_session(project_path)`: merge local + global, local precedence
- Add `prune_local_evolution(project_path)`: archive old local corrections (>90 days), trim to 30 corrections + 15 adjustments
- Add `prune_global_evolution()`: archive old global reverted entries (>180 days)

**Modify `src/enki/hooks.py`** session-start:
- Call `migrate_per_project_evolution(project_path)` on first run (idempotent)
- Use `get_evolution_context_for_session(project_path)` for injection context

**Pruning:**
- Local: 30 corrections, 15 adjustments max; older completed/reverted → `<project>/.enki/EVOLUTION_ARCHIVE.md`
- Global: only contains promoted entries; reverted >180 days → `~/.enki/EVOLUTION_ARCHIVE.md`
- `applied` and `acknowledged` global entries stay indefinitely (proven learnings)

**Fox Problem Protection:**
- Promotion is mechanical — status check only, no AI judgment
- `reason` field (CC's self-reported rationale) is NOT promoted
- ALL applied/acknowledged proposals promote regardless of type
- Local file is auditable — `diff` local vs global anytime

### Step 1: Integrate Reflector

**Copy file**: `reflector.py` → `src/enki/reflector.py`
**Copy file**: `test_reflector.py` → `tests/test_reflector.py`

**Modify `src/enki/hooks.py`** (~line 188):
- Replace `handle_session_end()` to call `close_feedback_loop()` from reflector
- Per integration guide in REFLECTOR_INTEGRATION.md

**Modify `scripts/hooks/enki-pre-compact.sh`**:
- Add `enki reflect --project "$CWD"` before exit

**Modify `src/enki/mcp_server.py`**:
- Add `enki_reflect` tool that calls `close_feedback_loop()`

**Modify `src/enki/__init__.py`**:
- Add reflector exports

### Step 2: Add `feedback_proposals` DB Table

**Modify `src/enki/db.py`** (add to SCHEMA):

```sql
CREATE TABLE IF NOT EXISTS feedback_proposals (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    proposal_type TEXT NOT NULL CHECK (proposal_type IN (
        'gate_loosen', 'gate_tighten',
        'pattern_add', 'pattern_remove', 'pattern_refine'
    )),
    target TEXT NOT NULL,
    description TEXT NOT NULL,
    reason TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    evidence_json TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN (
        'pending', 'applied', 'regressed', 'reverted', 'rejected', 'acknowledged'
    )),
    applied_at TIMESTAMP,
    reverted_at TIMESTAMP,
    pre_apply_snapshot TEXT,
    post_apply_snapshot TEXT,
    sessions_since_apply INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON feedback_proposals(status);
```

### Step 3: Create `src/enki/feedback_loop.py`

Core functions:

| Function | Purpose |
|----------|---------|
| `analyze_pattern_fp_rates(days=14)` | Per-pattern FP rates from `interceptions` table |
| `analyze_evasion_patterns(days=30)` | Group evasion reasoning from correlated evasions (see constraints below) |
| `generate_proposals(project_path)` | Main analysis → max 1 proposal per cycle |
| `store_proposal(proposal)` | Insert into `feedback_proposals` table |
| `apply_proposal(proposal_id)` | HITL: human approves, execute change |
| `reject_proposal(proposal_id)` | HITL: human rejects proposal |
| `acknowledge_regression(proposal_id)` | HITL: human accepts regression as expected |
| `revert_proposal(proposal_id)` | HITL: human decides to undo change |
| `check_for_regressions()` | Compare pre/post metrics, flag (don't auto-revert) — see constraints below |
| `run_feedback_cycle(project_path)` | Full cycle: analyze → propose → store (never apply) |
| `get_feedback_summary()` | Human-readable status with pending + regressed |
| `prune_evolution(project_path)` | Archive old corrections, trim EVOLUTION.md |
| `get_session_start_alerts()` | Pending proposals + regressions for session-start injection |

Constants:
```python
FEEDBACK_THRESHOLDS = {
    "fp_rate_to_loosen": 0.40,
    "min_evaluations_to_loosen": 5,
    "evasion_bug_count_to_tighten": 2,
    "violation_count_to_tighten": 5,
    "regression_sessions_to_check": 5,        # was 3 — too few for signal
    "regression_violation_increase": 2.0,      # was 1.5 — 50% on small samples is noise
    "regression_min_violations_post": 5,       # don't flag regression unless at least 5 violations in post window
}

NEVER_LOOSEN = {
    "gates": {"phase", "spec"},
    "pattern_categories": {"certainty_patterns"},
}

MAX_PROPOSALS_PER_CYCLE = 1
```

#### CONSTRAINT: `analyze_evasion_patterns` Data Source

The evasion data comes from `find_evasions_with_bugs()` in `ereshkigal.py` (line 742). This correlates allowed interceptions with later violations in the same session. Two issues to handle:

1. **Weak correlation**: An allowed interception followed by a violation in the same session doesn't mean the interception *caused* the violation. **Tighten the correlation**: only count it as an evasion if the allowed interception and subsequent violation share the same `tool` OR the same file path. Different gate, different file = coincidence, not evasion.

2. **The reasoning field is Claude's self-report**: We're not trusting Claude's *judgment* about whether it evaded — we're using the raw text of its reasoning as material for new regex patterns. That's valid. But `analyze_evasion_patterns` must extract regex candidates from the reasoning text (common phrases, minimizing language), NOT ask Claude to interpret what it was doing.

**Implementation**: `analyze_evasion_patterns` should:
```python
def analyze_evasion_patterns(days: int = 30) -> list[dict]:
    """Find common phrases in evasion reasoning that could become patterns.

    Uses find_evasions_with_bugs() with tightened correlation (same tool OR file).
    Extracts common n-grams from reasoning text — pure text analysis, no LLM.

    Returns list of {phrase, count, example_reasonings} for human review.
    """
    evasions = find_evasions_with_bugs_tight(days)  # tightened version
    if len(evasions) < 2:
        return []  # not enough signal

    # Extract 2-4 word ngrams from reasoning
    # Count frequency across evasions
    # Return phrases appearing in 2+ distinct evasions
    # Human decides if they become patterns
```

#### CONSTRAINT: `check_for_regressions` Must Short-Circuit

This runs every session-end. Don't waste cycles when there's nothing to check.

```python
def check_for_regressions() -> list[dict]:
    """Check applied proposals for regression. Short-circuits if nothing to check."""
    db = get_db()

    # Fast path: any applied proposals at all?
    applied_count = db.execute(
        "SELECT COUNT(*) FROM feedback_proposals WHERE status = 'applied'"
    ).fetchone()[0]

    if applied_count == 0:
        return []  # nothing to check, skip all the metric queries

    # Only check proposals with enough sessions elapsed
    # ... rest of regression detection
```

Also: regression detection requires BOTH conditions:
1. Violation rate increased by `regression_violation_increase` (2.0x) compared to pre-apply baseline
2. At least `regression_min_violations_post` (5) violations in the post-apply window

If you have 1 violation pre-apply and 2 post-apply, that's 2x increase but only 2 absolute violations. Not a regression — that's noise.

### Step 4: Wire Session End → Both Loops

**Modify `src/enki/hooks.py`** `handle_session_end()`:

```python
def handle_session_end(project_path=None):
    from .reflector import close_feedback_loop as reflect
    from .feedback_loop import run_feedback_cycle, check_for_regressions

    # Loop 1: Reflect → store learnings as beads
    reflection_report = reflect(project_path)

    # Loop 2: Analyze → propose enforcement changes (never auto-apply)
    feedback_report = run_feedback_cycle(project_path)

    # Loop 3: Check regressions on previously applied proposals → flag for human
    regression_report = check_for_regressions()

    return {**reflection_report, "feedback": feedback_report, "regressions": regression_report}
```

### Step 4b: Wire Session Start → Surface Alerts

**Modify `scripts/hooks/enki-session-start.sh`** or `hooks.py` session-start:

```python
# After persona injection, surface feedback alerts
from .feedback_loop import get_session_start_alerts
alerts = get_session_start_alerts()
if alerts:
    # Inject into session context:
    # "Pending feedback proposals: N. Regressions flagged: M."
    # "Run enki_feedback_loop status to review."
```

### Step 5: Add MCP Tool `enki_feedback_loop`

**Modify `src/enki/mcp_server.py`**:

Actions: `run`, `status`, `apply`, `reject`, `revert`, `acknowledge`

- `run` — generate proposals from accumulated data (session end)
- `status` — show pending proposals + applied + regressed
- `apply` — human approves, execute change
- `reject` — human rejects proposal
- `revert` — human decides to undo applied change showing regression
- `acknowledge` — human accepts regression as expected (keep the change)

### Step 6: Tests

**New file**: `tests/test_feedback_loop.py`

Test classes:
- `TestAnalyzePatternFPRates` — FP rate computation from interceptions
- `TestAnalyzeEvasionPatterns` — ngram extraction from evasion reasoning, must test tightened correlation (same tool/file)
- `TestGenerateProposals` — proposal generation from data
- `TestApplyProposal` — pattern add/remove, gate adjust
- `TestRegressionDetection` — violation/FP increase detection, must test short-circuit on zero applied proposals, must test minimum absolute violation threshold
- `TestRevertProposal` — undo with self-correction logging
- `TestRunFeedbackCycle` — full cycle integration
- `TestNeverLoosen` — hard floor protection

### Step 7: Run existing + new tests

```bash
pytest tests/test_reflector.py tests/test_feedback_loop.py -v
```

---

## Files Modified

| File | Change |
|------|--------|
| `src/enki/reflector.py` | **NEW** — copy from Downloads |
| `src/enki/feedback_loop.py` | **NEW** — enforcement feedback |
| `tests/test_reflector.py` | **NEW** — copy from Downloads |
| `tests/test_feedback_loop.py` | **NEW** — enforcement feedback tests |
| `src/enki/evolution.py` | Make global (`~/.enki/`), add migration + pruning |
| `src/enki/ereshkigal.py` | Tighten `find_evasions_with_bugs()` — require same tool OR same file_path; remove dead `get_evasions_with_bugs()` stub at line 566 |
| `src/enki/db.py` | Add `feedback_proposals` table |
| `src/enki/hooks.py` | Wire session start (alerts) + session end (both loops) |
| `src/enki/mcp_server.py` | Add `enki_reflect` + `enki_feedback_loop` tools |
| `src/enki/__init__.py` | Add exports |
| `scripts/hooks/enki-session-start.sh` | Surface pending proposals + regressions |
| `scripts/hooks/enki-pre-compact.sh` | Add reflection trigger |

## Design Decisions

### 1. Two-Tier Evolution — Local Autonomy, Global Learning

**Problem with flat global:** A single `~/.enki/EVOLUTION.md` bloats fast and gives CC a single point to influence cross-project enforcement. Problem with per-project only: learnings fragment — a gate adjustment discovered in Project A never helps Project B.

**Fix:** Two tiers. Local per-project + mechanical promotion to global.

- `<project>/.enki/EVOLUTION.md` — per-project, written by session-end feedback cycle
- `~/.enki/EVOLUTION.md` — global, written ONLY by `promote_to_global()` (mechanical, no AI)
- `~/.enki/wisdom.db` — already global (violations, interceptions, beads, feedback_proposals)
- `~/.enki/patterns.json` — already global

**What stays per-project** (session-specific):
- `<project>/.enki/RUNNING.md` — session activity log
- `<project>/.enki/{PHASE, TIER, GOAL, SESSION_ID}` — session state
- `<project>/.enki/SCOPE` — orchestration scope

**Promotion rules (fox problem protection):**
- Only `applied` or `acknowledged` proposals qualify
- Dedup by (proposal_type, target) — don't re-promote existing entries
- `reason` field (CC's rationale) is NOT promoted — only factual fields
- Tag each promoted entry with `source_project` for traceability
- ALL qualifying proposals promote regardless of type (no selective filtering)

**Session-start injection:**
- CC reads BOTH local and global at session start
- Local takes precedence on conflicts (a gate might be correctly tight for project A but loose for project B)

**Staleness/size management:**
- Local: 30 corrections, 15 adjustments max; completed/reverted >90 days → `<project>/.enki/EVOLUTION_ARCHIVE.md`
- Global: reverted >180 days → `~/.enki/EVOLUTION_ARCHIVE.md`; applied/acknowledged stay indefinitely
- `feedback_proposals` older than 180 days with status `rejected`/`reverted` → cleaned during `enki_maintain`
- Reflector beads tagged `auto-reflected` — subject to normal bead retention/decay

### 2. HITL Only — No Auto-Apply

All proposals require human decision. No `auto_apply` parameter.

**Flow:**
1. Session end → `run_feedback_cycle()` generates proposals
2. Proposals stored with status `pending`
3. Next session start → surface pending proposals: "You have N pending feedback proposals. Review with `enki_feedback_loop status`"
4. Human reviews via MCP tool → `apply` or `reject`
5. Applied proposals tracked for regression

### 3. Regression = Analysis, Not Auto-Revert

When regression is detected on an applied proposal:
- **Don't auto-revert**
- At session end, `check_for_regressions()` flags proposals showing regression
- Surface to human: "Proposal X shows regression: violations up 60%. Review?"
- Human decides: `revert` (undo the change) or `acknowledge` (keep it, the increase is expected/acceptable)
- Add `acknowledged` status to proposal lifecycle: `pending → applied → regressed → reverted|acknowledged`

**Regression thresholds are deliberately conservative:**
- 2.0x violation increase required (not 1.5x — small sample noise kills you at 1.5x)
- Minimum 5 violations in post-apply window required (prevents flagging 1→2 as "doubled")
- 5 sessions observed before checking (not 3 — need enough data for the minimum threshold to be meaningful)

### 4. Session Start Surfacing

Pending proposals and regression warnings surface at session start via the session-start hook, not just passively sitting in DB.

### 5. Evasion Correlation Must Be Tight

`find_evasions_with_bugs()` in ereshkigal.py currently correlates allowed interceptions with ANY later violation in the same session. That's too loose — different gate, different file, different action. Coincidence, not causation.

**Fix:** Tighten to require the allowed interception and subsequent violation share at least one of:
- Same `tool` (e.g., both are `Edit` actions)
- Same file path (e.g., both touch `auth.py`)

This is a change to `src/enki/ereshkigal.py`, function `find_evasions_with_bugs()` (line 742). Update the SQL JOIN to include the tool/file constraint. The existing stub `get_evasions_with_bugs()` (line 566) that returns empty should be removed — it's dead code, replaced by `find_evasions_with_bugs()`.

### 6. No LLM Anywhere in These Loops

Both layers are heuristic-only:
- **Reflector**: counts, ratios, thresholds → skill beads
- **Feedback loop**: FP rates, evasion ngrams, violation counts → proposals

If you want LLM analysis of patterns, that goes through the external review channel — you bring the weekly report to a fresh Claude session (or here to this project). Enki never interprets its own behavior.

## Guardrails

- Max 1 proposal per cycle (prevents cascading changes)
- `NEVER_LOOSEN`: phase gate, spec gate, certainty_patterns
- HITL required for all proposals — no auto-apply, no auto-revert
- All changes logged to global `~/.enki/EVOLUTION.md`
- EVOLUTION.md pruned: 30 corrections, 15 adjustments max; older → archive
- `feedback_proposals` table cleaned during `enki_maintain`
- Regression requires both 2.0x increase AND minimum 5 absolute violations
- Evasion correlation tightened to same tool or same file
- `check_for_regressions()` short-circuits when zero applied proposals exist

## Verification

1. Run `pytest tests/test_reflector.py -v` — reflector tests pass
2. Run `pytest tests/test_feedback_loop.py -v` — feedback tests pass
3. Run `pytest tests/ -v` — no regressions
4. Manual: call `enki_reflect` MCP tool → see reflection report
5. Manual: call `enki_feedback_loop` with action=run → see proposals (or "stable")
6. Manual: call `enki_feedback_loop` with action=status → see pending/applied counts
7. Verify `check_for_regressions()` returns `[]` immediately when no applied proposals exist (no wasted DB queries)
8. Verify `analyze_evasion_patterns()` returns `[]` when fewer than 2 evasions found (not enough signal)
