# Enki v3 Implementation Spec

> **Version**: 1.2
> **Date**: 2025-02-13
> **Status**: Final
> **Scope**: Complete implementation blueprint for Enki v3. CC codes from this document.
> **Audience**: Dev agent reads this and knows exactly what files to create, in what order, with what interfaces.
> **Source**: Derived from Product Specs (Abzu v1.2, Uru v1.1, EM v1.4, Bridge v1.1, Ship & Quality v1.2). If there's a conflict, this spec wins for implementation details; Product Specs win for behavior.
> **Critical lesson from v1/v2**: Enforcement that blocks legitimate work gets disabled. The exempt path system is the most important thing to get right. See Section 4.
> **v1.1 Changes**: Updated EM references to v1.3. Added CLAUDE.md to build order. Phase 3 build order includes new modules (claude_md.py, devops.py). agents.py expanded for 14-agent roster. Test strategy scope clarification (tests FOR Enki vs tests Enki writes). Updated line estimates.
> **v1.2 Changes**: Updated EM references to v1.4. 14→13 agents (Docs removed). Added prompts/ directory (Layer 0 protected). agents.py assembles prompts from files. Added onboarding.py, researcher.py to Phase 3. Added user_profile table to wisdom.db schema. Added migration script. Added Gemini review report generator. Prism integration in qualify phase.

---

## Table of Contents

1. [Build Order](#1-build-order)
2. [Directory Structure](#2-directory-structure)
3. [Database Schemas (Complete)](#3-database-schemas)
4. [Exempt Path System (CRITICAL)](#4-exempt-path-system)
5. [Phase 0: Bootstrap](#5-phase-0-bootstrap)
6. [Phase 1: Uru (Gates)](#6-phase-1-uru-gates)
7. [Phase 2: Abzu (Memory)](#7-phase-2-abzu-memory)
8. [Phase 3: EM (Orchestration)](#8-phase-3-em-orchestration)
9. [Phase 4: Integration](#9-phase-4-integration)
10. [Migration from Current Enki](#10-migration)
11. [Test Strategy](#11-test-strategy)
12. [Module Interface Contracts](#12-module-interface-contracts)

---

## 1. Build Order

Gates first. Gates protect everything else. But gates need databases, and databases need schemas. So:

```
Phase 0: Bootstrap
    schemas.py (all 4 DBs + user_profile in wisdom.db) → DB initialization → CLAUDE.md (for Enki itself)

Phase 1: Uru (Gates) — ~910 lines
    Layer 0 → Layer 0.5 → Layer 1 gates → Nudges → Hooks (all 6)
    Layer 0 must protect: hooks, uru.py, uru.db, PERSONA.md, abzu.py core, prompts/ directory
    TEST: Verify gates block correctly AND exempt paths pass correctly

Phase 2: Abzu (Memory) — ~2,300 lines
    schemas already exist → beads.py → sessions.py → extraction.py
    → retention.py → staging.py → gemini.py → abzu.py (facade)
    MCP tools: enki_remember, enki_recall, enki_star, enki_status

Phase 3: EM (Orchestration) — ~6,750 lines
    schemas already exist → mail.py → task_graph.py → agents.py (13 agents, loads prompts/)
    → pm.py → validation.py → tiers.py → bugs.py → orchestrator.py
    → parsing.py → bridge.py → status.py → yggdrasil.py
    → claude_md.py → devops.py → onboarding.py → researcher.py

Phase 4: Integration
    Hook wiring (hooks call Abzu + Uru together)
    Session lifecycle end-to-end test
    Gemini review report generator (scripts/gemini_review.py)
    Migration script (scripts/migrate_v1.py)

Prompts: (NOT built by CC — written by Gemini, Layer 0 protected)
    prompts/_base.md → prompts/_coding_standards.md → 13 agent prompt files
    Delivered separately via Agent Prompt Specification
```

**Why this order:**
- Phase 0 creates all DB tables upfront. No schema changes mid-build.
- Phase 1 builds enforcement. Once hooks are live, ALL subsequent work is protected.
- Phase 2 builds memory. Hooks can now capture session state.
- Phase 3 builds orchestration. Memory provides context, gates enforce workflow.
- Phase 4 wires everything together.

**The chicken-and-egg problem:** Uru's gates can't protect the Uru build itself. Phase 0 and Phase 1 are built WITHOUT enforcement. Human reviews all Phase 0/1 code before committing. Once hooks are live, Phase 2+ is protected.

---

## 2. Directory Structure

```
~/.enki/
├── hooks/                          # CC lifecycle hooks (shell scripts)
│   ├── session-start.sh
│   ├── pre-tool-use.sh            # Layer 0 + 0.5 + 1
│   ├── post-tool-use.sh           # Nudges + logging
│   ├── pre-compact.sh
│   ├── post-compact.sh
│   └── session-end.sh
├── prompts/                        # Agent prompt files (Layer 0 PROTECTED — CC cannot edit)
│   ├── _base.md                   # Shared: identity, output format, mail protocol
│   ├── _coding_standards.md       # Shared: SOLID/DRY/Clean Code
│   ├── pm.md                      # Per-agent prompts (13 files)
│   ├── architect.md
│   ├── dba.md
│   ├── dev.md
│   ├── qa.md
│   ├── ui_ux.md
│   ├── validator.md
│   ├── reviewer.md
│   ├── infosec.md
│   ├── devops.md
│   ├── performance.md
│   ├── researcher.md
│   └── em.md
├── wisdom.db                       # Permanent beads + user_profile (Abzu)
├── abzu.db                         # Session summaries + staging (Abzu)
├── uru.db                          # Enforcement logs (Uru)
├── projects/
│   └── {project-name}/
│       └── em.db                   # Per-project orchestration (EM)
├── reviews/                        # Gemini review packages (generated by cron)
│   └── review-YYYY-QN.md
├── persona/
│   └── PERSONA.md                  # Identity file
├── config/
│   └── enki.toml                   # Global config (tier defaults, paths, thresholds)
└── SESSION_ID                      # Current session marker

src/enki/                           # Python source
├── __init__.py
├── gates/                          # Pillar 2: Uru
│   ├── __init__.py
│   ├── uru.py                      # Gate check logic
│   ├── layer0.py                   # Blocklist + DB protection patterns
│   ├── feedback.py                 # Feedback proposals
│   └── schemas.py                  # uru.db tables
├── memory/                         # Pillar 1: Abzu
│   ├── __init__.py
│   ├── abzu.py                     # Facade — public API
│   ├── beads.py                    # Bead CRUD + FTS5
│   ├── sessions.py                 # Session summary lifecycle
│   ├── staging.py                  # Candidate staging + promotion
│   ├── extraction.py               # Heuristic JSONL extraction
│   ├── retention.py                # Decay + maintenance
│   ├── gemini.py                   # Gemini review report generator (no API — exports package)
│   └── schemas.py                  # wisdom.db + abzu.db tables
├── orch/                           # Pillar 3: EM
│   ├── __init__.py
│   ├── orchestrator.py             # Core EM
│   ├── mail.py                     # Mail system
│   ├── task_graph.py               # DAG + waves
│   ├── agents.py                   # Agent definitions + prompt assembly from prompts/
│   ├── pm.py                       # PM workflow
│   ├── validation.py               # Blind validation
│   ├── tiers.py                    # Tier system
│   ├── bugs.py                     # Bug lifecycle
│   ├── parsing.py                  # Agent output parsing
│   ├── bridge.py                   # Memory bridge
│   ├── status.py                   # Status updates
│   ├── yggdrasil.py                # Project tracking
│   ├── claude_md.py                # CLAUDE.md generation
│   ├── devops.py                   # DevOps agent: CI, deploy, verify
│   ├── onboarding.py               # Entry point detection, user profile, first-time flow
│   ├── researcher.py               # Codebase Profile, scoped investigation
│   └── schemas.py                  # em.db tables
├── mcp/                            # MCP tool definitions
│   ├── __init__.py
│   ├── memory_tools.py             # enki_remember, enki_recall, enki_star, enki_status
│   └── orch_tools.py               # enki_goal, enki_phase, enki_triage, enki_quick, etc.
├── scripts/                        # Maintenance scripts (not part of core — run manually)
│   ├── gemini_review.py            # Generate review package for external LLM
│   └── migrate_v1.py               # One-time migration from v1/v2 beads
├── cli.py                          # CLI entrypoint
├── db.py                           # Shared DB connection management (WAL, busy_timeout)
└── config.py                       # Config loading from enki.toml
```

---

## 3. Database Schemas

### Shared Connection Management — db.py

Every database connection in Enki goes through `db.py`. No module opens its own connection.

```python
"""db.py — Shared database connection management.

Every connection uses WAL mode and busy_timeout.
Every connection is scoped to a specific database.
No module bypasses this.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

ENKI_ROOT = Path.home() / ".enki"

def _configure(conn: sqlite3.Connection) -> None:
    """Apply mandatory SQLite configuration."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

@contextmanager
def connect(db_path: str | Path):
    """Context manager for database connections.
    
    Usage:
        with connect(ENKI_ROOT / "wisdom.db") as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(str(db_path))
    _configure(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def wisdom_db():
    """Connection to wisdom.db (permanent beads)."""
    return connect(ENKI_ROOT / "wisdom.db")

def abzu_db():
    """Connection to abzu.db (session summaries + staging)."""
    return connect(ENKI_ROOT / "abzu.db")

def uru_db():
    """Connection to uru.db (enforcement logs)."""
    return connect(ENKI_ROOT / "uru.db")

def em_db(project: str):
    """Connection to per-project em.db."""
    path = ENKI_ROOT / "projects" / project / "em.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return connect(path)
```

### Schema Initialization

All schemas are created at bootstrap time. `schemas.py` in each pillar defines its tables. `db.py` provides the `init_all()` function.

```python
def init_all():
    """Create all databases and tables. Idempotent."""
    from enki.gates.schemas import create_tables as create_uru
    from enki.memory.schemas import create_tables as create_memory
    
    with wisdom_db() as conn:
        create_memory(conn, "wisdom")
    with abzu_db() as conn:
        create_memory(conn, "abzu")
    with uru_db() as conn:
        create_uru(conn)
```

### Complete Schema DDL

All table DDL is defined in the Product Specs:
- **wisdom.db**: Abzu Spec Section 16 (beads, beads_fts, projects)
- **abzu.db**: Abzu Spec Section 16 (session_summaries, bead_candidates, candidates_fts, extraction_log)
- **uru.db**: Uru Spec Section 11 (enforcement_log, feedback_proposals, nudge_state)
- **em.db**: EM Spec Section 18 (mail_messages, mail_threads, task_state, sprint_state, bugs, pm_decisions, mail_archive)

Implementation copies DDL verbatim from Product Specs. No schema invention at implementation time.

---

## 4. Exempt Path System (CRITICAL)

**This is the #1 reason v1/v2 enforcement was disabled.** Gates blocked CC from writing memory files, docs, session summaries, and config. CC couldn't do its job, so enforcement was turned off.

### The Three Failures to Prevent

**Failure 1: Gate blocks infrastructure writes.**
CC writing a session summary to `~/.enki/abzu.db` is not a code mutation. It must never be blocked by workflow gates.

**Failure 2: Layer 0 matches filename in content, not target.**
`echo "Fixed bug in enforcement.py" > notes.md` — the write target is `notes.md`, but Layer 0 saw "enforcement.py" in the command string and blocked it.

**Failure 3: Multiple layers stack-block.**
Layer 0 passes (file isn't protected). Layer 1 blocks (no goal set). But the file is a .md doc that should be exempt from Layer 1. Each layer must independently respect exemptions.

### Exempt Path Categories

| Category | Patterns | Why Exempt |
|---|---|---|
| **Enki infrastructure** | `~/.enki/*` (except hooks/, uru.py, uru.db, PERSONA.md, abzu.py, layer0.py) | Memory writes, session summaries, DB operations |
| **Documentation** | `*.md` anywhere except `src/` | Docs, notes, specs, README |
| **Configuration** | `CLAUDE.md`, `.claude/*`, `*.toml`, `*.yaml`, `*.yml` (in project root) | Project config |
| **Memory tools** | Any write performed by `enki_remember`, `enki_star` MCP tools | These ARE the workflow |
| **Git operations** | `.git/*` | Version control |

### The Exempt Check Function

```python
"""gates/uru.py — exempt_path()

This function is called by EVERY enforcement layer.
If it returns True, the tool call passes with NO further checks.
This is the single source of truth for what's exempt.
"""

import os
from pathlib import Path

ENKI_ROOT = Path.home() / ".enki"

# Layer 0 PROTECTED files — these are NEVER exempt, even from exempt_path()
# Listed explicitly. If a file is here, it cannot be written by CC. Period.
LAYER0_PROTECTED = {
    "session-start.sh", "pre-tool-use.sh", "post-tool-use.sh",
    "pre-compact.sh", "post-compact.sh", "session-end.sh",
    "uru.py", "layer0.py",
    "PERSONA.md",
    "_base.md", "_coding_standards.md",  # Shared prompt templates
    "pm.md", "architect.md", "dba.md", "dev.md", "qa.md",
    "ui_ux.md", "validator.md", "reviewer.md", "infosec.md",
    "devops.md", "performance.md", "researcher.md", "em.md",  # Agent prompts
}

# Files under ~/.enki/ that are protected by Layer 0
LAYER0_PROTECTED_PATHS = {
    ENKI_ROOT / "hooks",           # entire hooks directory
    ENKI_ROOT / "prompts",         # entire prompts directory (written by Gemini, not CC)
    ENKI_ROOT / "uru.db",          # enforcement DB (Layer 0.5 also protects)
}

def is_layer0_protected(filepath: str) -> bool:
    """Check if file is Layer 0 protected. These CANNOT be written by CC."""
    path = Path(filepath).resolve()
    basename = path.name
    
    # Check basename against protected names
    if basename in LAYER0_PROTECTED:
        return True
    
    # Check if path is under a protected directory
    for protected in LAYER0_PROTECTED_PATHS:
        try:
            path.relative_to(protected)
            return True
        except ValueError:
            continue
    
    return False


def is_exempt(filepath: str, tool_name: str = None) -> bool:
    """Check if a file path is exempt from workflow gate checks.
    
    CRITICAL: This function must be fast (<1ms) and must NEVER
    produce false negatives (blocking legitimate infrastructure writes).
    False positives (allowing a code file through) are caught by
    the next gate check, so they're less dangerous.
    
    Returns True if the file should bypass Layer 1 gate checks.
    Returns False if the file needs full gate verification.
    
    Layer 0 protected files are handled BEFORE this function is called.
    If is_layer0_protected() returns True, the call is blocked
    regardless of what this function returns.
    """
    path = Path(filepath)
    
    # Category 1: Enki infrastructure (except Layer 0 protected)
    try:
        path.resolve().relative_to(ENKI_ROOT.resolve())
        # It's under ~/.enki/ — exempt unless Layer 0 protected
        # (Layer 0 check happens before this function is called)
        return True
    except ValueError:
        pass
    
    # Category 2: Documentation (*.md outside src/)
    if path.suffix == '.md':
        # Block .md files inside src/ — those might be code-adjacent docs
        # that should follow the workflow
        parts = path.parts
        if 'src' not in parts:
            return True
    
    # Category 3: Configuration files in project root
    if path.name in ('CLAUDE.md',):
        return True
    if path.suffix in ('.toml', '.yaml', '.yml'):
        # Only exempt if in project root (not nested in src/)
        parts = path.parts
        if 'src' not in parts:
            return True
    
    # Category 4: .claude directory
    if '.claude' in path.parts:
        return True
    
    # Category 5: Git
    if '.git' in path.parts:
        return True
    
    # Not exempt — needs full gate check
    return False
```

### Target Extraction for Bash Commands

**Layer 0 and 0.5 must extract the WRITE TARGET from bash commands, not match against the full command string.**

```python
"""gates/layer0.py — extract_bash_target()

Extracts the file being WRITTEN TO from a bash command.
Does NOT match against the entire command string.
This prevents false positives like blocking:
    echo "Fixed bug in enforcement.py" > notes.md
where the target is notes.md, not enforcement.py.
"""

import re
import shlex

def extract_write_targets(command: str) -> list[str]:
    """Extract file paths being written to from a bash command.
    
    Returns list of file paths that are write targets.
    Returns empty list if no write targets detected (read-only command).
    
    IMPORTANT: Only extracts TARGETS, not mentions.
    'echo "enforcement.py" > notes.md' returns ['notes.md']
    'sed -i s/x/y/ enforcement.py' returns ['enforcement.py']
    'cat enforcement.py' returns [] (read-only)
    """
    targets = []
    
    # Split compound commands
    segments = re.split(r'[;|]', command)
    
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        
        # Redirect operators: > >> 
        # Target is the token AFTER the redirect
        redirect_match = re.findall(r'>{1,2}\s*(\S+)', segment)
        targets.extend(redirect_match)
        
        # tee: target is the argument
        tee_match = re.search(r'\btee\s+(?:-a\s+)?(\S+)', segment)
        if tee_match:
            targets.append(tee_match.group(1))
        
        # sed -i: target is the LAST argument
        if re.search(r'\bsed\s+.*-i', segment):
            parts = shlex.split(segment)
            if parts:
                targets.append(parts[-1])
        
        # cp, mv: target is the LAST argument
        cp_mv_match = re.search(r'\b(cp|mv)\s+', segment)
        if cp_mv_match:
            parts = shlex.split(segment)
            if len(parts) >= 3:
                targets.append(parts[-1])
        
        # rm: target is all arguments after flags
        rm_match = re.search(r'\brm\s+', segment)
        if rm_match:
            parts = shlex.split(segment)
            for part in parts[1:]:
                if not part.startswith('-'):
                    targets.append(part)
        
        # python -c with open(..., 'w'): too complex to parse reliably
        # Block if sqlite3 or open() with write mode detected
        if re.search(r'python.*-c', segment):
            if re.search(r"open\(.*['\"]w", segment):
                # Can't reliably extract target, treat whole segment as suspicious
                targets.append("__PYTHON_WRITE__")
    
    return targets


def extract_db_targets(command: str) -> list[str]:
    """Extract database files being targeted by bash commands.
    
    For Layer 0.5 — catches sqlite3 binary and Python sqlite3 module.
    Returns list of .db file paths being targeted.
    """
    targets = []
    
    # sqlite3 binary: sqlite3 path/to/file.db "..."
    sqlite_match = re.findall(r'\bsqlite3\s+(\S+\.db\S*)', command)
    targets.extend(sqlite_match)
    
    # Python sqlite3.connect
    connect_match = re.findall(r'sqlite3\.connect\(["\']([^"\']+)["\']', command)
    targets.extend(connect_match)
    
    # File operations targeting .db files
    for pattern in [r'>{1,2}\s*(\S+\.db)', r'\b(cp|mv|rm)\s+.*?(\S+\.db)']:
        matches = re.findall(pattern, command)
        for m in matches:
            if isinstance(m, tuple):
                targets.extend(m)
            else:
                targets.append(m)
    
    return [t for t in targets if t.endswith('.db')]
```

### How Layers Use Exempt Paths

```
pre-tool-use.sh receives tool call
    │
    ├─ Tool is Write/Edit/MultiEdit/NotebookEdit?
    │   │
    │   ├─ Extract file path from tool input
    │   ├─ is_layer0_protected(path)? → BLOCK
    │   ├─ is_exempt(path)? → ALLOW (skip all further checks)
    │   └─ Layer 1 gate checks (goal, phase, spec)
    │
    └─ Tool is Bash?
        │
        ├─ extract_write_targets(command)
        │   └─ For each target:
        │       ├─ is_layer0_protected(target)? → BLOCK
        │       ├─ is_exempt(target)? → skip this target
        │       └─ Layer 1 gate checks on non-exempt targets
        │
        ├─ extract_db_targets(command)  [Layer 0.5]
        │   └─ Any .db under ~/.enki/? → BLOCK
        │
        └─ No write targets extracted? → ALLOW (read-only command)
```

### The Golden Rule

**Exempt files are checked ONCE, at the TOP of the enforcement stack. If a file is exempt, it bypasses ALL subsequent layers. No layer can re-block an exempt file.**

This is the single most important implementation detail. If this is wrong, enforcement blocks legitimate work and gets disabled.

---

## 5. Phase 0: Bootstrap

### What Gets Built

| File | Lines | What |
|---|---|---|
| `src/enki/db.py` | ~80 | Connection management (WAL, busy_timeout) |
| `src/enki/config.py` | ~60 | Config loading from enki.toml |
| `src/enki/gates/schemas.py` | ~60 | uru.db table DDL |
| `src/enki/memory/schemas.py` | ~150 | wisdom.db + abzu.db table DDL |
| `src/enki/orch/schemas.py` | ~200 | em.db table DDL |
| `~/.enki/config/enki.toml` | ~40 | Default configuration |
| `CLAUDE.md` | ~100 | Updated project instructions |

### Bootstrap Script

```bash
#!/bin/bash
# bootstrap.sh — Run once to initialize Enki v3
set -euo pipefail

ENKI_ROOT="$HOME/.enki"

# Create directory structure
mkdir -p "$ENKI_ROOT"/{hooks,projects,persona,config}

# Initialize databases
python -c "from enki.db import init_all; init_all()"

# Verify
echo "Databases created:"
ls -la "$ENKI_ROOT"/*.db

echo "Bootstrap complete."
```

### enki.toml Defaults

```toml
[general]
version = "3.0"

[memory]
fts5_min_score = 0.3
session_summary_max_tokens = { minimal = 1500, standard = 4000, full = 8000 }
decay_thresholds = { d90 = 0.5, d180 = 0.2, d365 = 0.1 }

[gates]
max_parallel_tasks = 2
nudge_tool_call_threshold = 30

[gemini]
review_cadence = "quarterly"
```

---

## 6. Phase 1: Uru (Gates)

### Build Order Within Phase 1

```
1. gates/layer0.py          — blocklist + target extraction (pure Python, no DB)
2. gates/uru.py              — gate checks + nudge logic (reads DB)
3. gates/feedback.py         — proposal CRUD (writes uru.db)
4. hooks/pre-tool-use.sh     — Layer 0 → 0.5 → 1 (calls layer0.py + uru.py)
5. hooks/post-tool-use.sh    — Nudges + logging (calls uru.py)
6. hooks/session-start.sh    — Init Uru state
7. hooks/session-end.sh      — Enforcement summary
8. hooks/pre-compact.sh      — Log state
9. hooks/post-compact.sh     — Re-inject context
```

### Hook Shell Script Template

All hooks follow the same pattern:

```bash
#!/bin/bash
# hooks/{hook-name}.sh
set -euo pipefail

# Read hook input from stdin
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty')

# Call Python for logic (hooks are thin shells, logic in Python)
RESULT=$(python -m enki.gates.uru \
    --hook "{hook-name}" \
    --tool "$TOOL_NAME" \
    --input "$TOOL_INPUT" \
    2>/dev/null)

# Output decision
if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    echo '{"decision":"allow"}'
fi
```

**Hooks are thin shells.** All logic lives in Python. Hooks just pipe stdin to Python and return the result. This makes testing easier (test Python directly) and maintenance simpler (edit Python, not bash).

**Exception: Layer 0 and Layer 0.5 have bash fast-paths** for the most critical checks (protected file names, sqlite3 detection) that run BEFORE Python is invoked, for speed and resilience.

### pre-tool-use.sh (Most Critical Hook)

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# ── Layer 0: Bash fast-path for protected files ──
# These checks run in pure bash, before Python, for maximum speed
# and resilience (works even if Python/Enki is broken)

if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
      "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then
    
    TARGET=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')
    BASENAME=$(basename "$TARGET" 2>/dev/null || echo "")
    
    # Hard-coded protected basenames (Layer 0)
    case "$BASENAME" in
        session-start.sh|pre-tool-use.sh|post-tool-use.sh|\
        pre-compact.sh|post-compact.sh|session-end.sh|\
        uru.py|layer0.py|PERSONA.md)
            echo '{"decision":"block","reason":"Layer 0: Protected file '"$BASENAME"'"}'
            exit 0
            ;;
    esac
fi

if [[ "$TOOL_NAME" == "Bash" ]]; then
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    
    # Layer 0.5: sqlite3 targeting .db files under ~/.enki
    if echo "$CMD" | grep -qP 'sqlite3\s+\S*\.db'; then
        echo '{"decision":"block","reason":"Layer 0.5: Direct DB manipulation. Use Enki tools."}'
        exit 0
    fi
    if echo "$CMD" | grep -qP 'sqlite3\.connect'; then
        echo '{"decision":"block","reason":"Layer 0.5: Direct DB manipulation. Use Enki tools."}'
        exit 0
    fi
fi

# ── Layer 1+: Python handles the rest ──
# Python does: exempt path check, gate checks, context-aware decisions
RESULT=$(echo "$INPUT" | python -m enki.gates.uru --hook pre-tool-use 2>/dev/null)

if [[ -n "$RESULT" ]]; then
    echo "$RESULT"
else
    # If Python fails, fail CLOSED (block) for mutation tools, OPEN for reads
    if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" || \
          "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" || \
          "$TOOL_NAME" == "Bash" ]]; then
        echo '{"decision":"block","reason":"Uru unavailable. Blocking mutation for safety."}'
    else
        echo '{"decision":"allow"}'
    fi
fi
```

### uru.py Gate Check Flow

```python
"""gates/uru.py — Core gate logic.

Called by hooks. Reads DB state. Returns allow/block decisions.
"""

def check_pre_tool_use(tool_name: str, tool_input: dict) -> dict:
    """Main gate check for pre-tool-use hook.
    
    Returns: {"decision": "allow"} or {"decision": "block", "reason": "..."}
    """
    # Step 1: Determine if this is a mutation tool
    MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    
    if tool_name not in MUTATION_TOOLS and tool_name != "Bash":
        return {"decision": "allow"}  # Read-only tools always pass
    
    # Step 2: Extract target file(s)
    if tool_name in MUTATION_TOOLS:
        filepath = tool_input.get("file_path") or tool_input.get("path", "")
        targets = [filepath] if filepath else []
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        targets = extract_write_targets(command)
        
        # Layer 0.5 DB check (Python layer — bash fast-path already caught obvious ones)
        db_targets = extract_db_targets(command)
        enki_root = str(Path.home() / ".enki")
        for db in db_targets:
            if enki_root in str(Path(db).resolve()):
                return {"decision": "block", 
                        "reason": "Layer 0.5: Direct DB manipulation. Use Enki tools."}
    else:
        targets = []
    
    if not targets:
        return {"decision": "allow"}  # No write targets detected
    
    # Step 3: Check each target
    for target in targets:
        if target == "__PYTHON_WRITE__":
            # Suspicious Python write — block
            return {"decision": "block",
                    "reason": "Unverifiable Python file write in bash command."}
        
        # Layer 0: protected files
        if is_layer0_protected(target):
            return {"decision": "block",
                    "reason": f"Layer 0: Protected file {Path(target).name}"}
        
        # Exempt check: if exempt, skip ALL gate checks for this target
        if is_exempt(target, tool_name):
            continue  # This target is fine, check next
        
        # Layer 1: Gate checks (only for non-exempt files)
        gate_result = _check_gates(target)
        if gate_result["decision"] == "block":
            return gate_result
    
    return {"decision": "allow"}


def _check_gates(filepath: str) -> dict:
    """Layer 1 gate checks. Only called for non-exempt files."""
    
    # Read current project context
    project = _get_current_project()
    
    if not project:
        return {"decision": "block",
                "reason": "Gate 1: No active project. Set one with enki_goal."}
    
    # Gate 1: Goal exists?
    goal = _get_active_goal(project)
    if not goal:
        return {"decision": "block",
                "reason": "Gate 1: No active goal. Set one with enki_goal."}
    
    # Gate 3: Phase >= implement?
    phase = _get_current_phase(project)
    IMPLEMENT_PHASES = {"implement", "review", "ship"}
    if phase not in IMPLEMENT_PHASES:
        return {"decision": "block",
                "reason": f"Gate 3: Phase is '{phase}'. Code changes need phase >= implement."}
    
    # Gate 2: Spec approved? (Standard/Full only)
    tier = _get_tier(project)
    if tier in ("standard", "full"):
        if not _is_spec_approved(project):
            return {"decision": "block",
                    "reason": "Gate 2: No approved spec. Needs human approval before implementation."}
    
    return {"decision": "allow"}
```

### Nudge Implementation

```python
def check_post_tool_use(tool_name: str, tool_input: dict, 
                        assistant_response: str = "") -> dict:
    """Post-tool-use checks. Non-blocking. Returns nudge messages."""
    
    nudges = []
    session_id = _get_session_id()
    
    # Nudge 1: Unrecorded decision
    if _contains_decision_language(assistant_response):
        if not _recent_enki_remember(session_id, within_turns=2):
            if _should_fire_nudge("unrecorded_decision", session_id):
                nudges.append(
                    "Good decision. Worth recording — consider enki_remember."
                )
                _record_nudge_fired("unrecorded_decision", session_id)
    
    # Nudge 2: Long session without summary
    tool_count = _get_tool_count_since_summary(session_id)
    if tool_count > 30:
        if _should_fire_nudge("long_session", session_id):
            nudges.append(
                f"Productive session — {tool_count} actions since last checkpoint. "
                "Good time to capture state."
            )
            _record_nudge_fired("long_session", session_id)
    
    # Nudge 3: Unread kickoff mail
    if tool_name in ("Write", "Edit", "Bash"):
        unread_kickoffs = _get_unread_kickoff_mails()
        if unread_kickoffs:
            project = unread_kickoffs[0]["project"]
            if _should_fire_nudge("unread_kickoff", session_id):
                nudges.append(
                    f"Kickoff mail pending for {project}. "
                    "Spawn EM to begin execution."
                )
                _record_nudge_fired("unread_kickoff", session_id)
    
    # Log tool call
    _log_enforcement(session_id, "post-tool-use", tool_name, 
                     tool_input, "allow", nudges)
    
    if nudges:
        return {"decision": "allow", "nudges": nudges}
    return {"decision": "allow"}
```

---

## 7. Phase 2: Abzu (Memory)

### Build Order Within Phase 2

```
1. memory/schemas.py         — Already created in Phase 0
2. memory/beads.py           — CRUD + FTS5 + dedup + ranking
3. memory/sessions.py        — Summary lifecycle + injection budget
4. memory/extraction.py      — Heuristic JSONL parsing + versioning
5. memory/retention.py       — Decay scoring + maintenance
6. memory/staging.py         — Candidate management + promotion
7. memory/gemini.py          — Review interface (prep + process)
8. memory/abzu.py            — Facade — public API
9. mcp/memory_tools.py       — MCP tool definitions
```

### Key Function Signatures

```python
# ── memory/abzu.py (facade) ──

def inject_session_start(project: str, goal: str, tier: str) -> str:
    """Load and format context for session start injection.
    Returns formatted string for CC's context window.
    Tier-dependent: Minimal gets less, Full gets more."""

def update_pre_compact_summary(
    session_id: str, project: str,
    operational_state: str,    # from heuristic
    conversational_state: str  # from CC
) -> None:
    """Store pre-compact summary. Accumulates across compactions."""

def inject_post_compact(session_id: str, tier: str) -> str:
    """Load accumulated summaries for post-compact injection.
    Applies injection budget — collapses old summaries if over limit."""

def finalize_session(session_id: str, project: str) -> None:
    """Session end: reconcile summaries, extract candidates, run decay."""

def remember(content: str, category: str, project: str = None,
             summary: str = None, tags: str = None) -> dict:
    """Store a bead. Preference → wisdom.db direct. Others → staging."""

def recall(query: str, scope: str = "project", 
           project: str = None, limit: int = 5) -> list[dict]:
    """Search beads. Updates last_accessed. Applies ranking + min score."""

def star(bead_id: str) -> None:
    """Mark bead as permanent (never decays)."""

def status() -> dict:
    """Health check: DB sizes, bead counts, staging depth, decay stats."""
```

### Injection Budget Implementation

```python
# ── memory/sessions.py ──

from enki.config import get_config

def get_post_compact_injection(session_id: str, tier: str) -> str:
    """Build post-compact injection within token budget.
    
    Budget from config:
        Minimal: ~1,500 tokens for summaries
        Standard: ~4,000 tokens
        Full: ~8,000 tokens
    
    If accumulated summaries exceed budget:
    1. Keep most recent pre-compact summary in full
    2. Collapse older summaries into condensed narrative
    3. If still over, keep only most recent + key decisions list
    """
    config = get_config()
    budget = config["memory"]["session_summary_max_tokens"][tier]
    
    summaries = _load_accumulated_summaries(session_id)
    
    if not summaries:
        return ""
    
    total_tokens = sum(_estimate_tokens(s) for s in summaries)
    
    if total_tokens <= budget:
        # Under budget — return all summaries
        return _format_summaries(summaries)
    
    # Over budget — collapse
    most_recent = summaries[-1]
    older = summaries[:-1]
    
    # Condensed narrative from older summaries
    condensed = _condense_summaries(older)
    
    combined_tokens = _estimate_tokens(most_recent) + _estimate_tokens(condensed)
    
    if combined_tokens <= budget:
        return _format_condensed(condensed, most_recent)
    
    # Still over — extreme compression
    decisions_only = _extract_decisions_only(summaries)
    return _format_minimal(decisions_only, most_recent)
```

### JSONL Parser Versioning

```python
# ── memory/extraction.py ──

JSONL_FORMAT_VERSION = "1.0"

# Expected JSONL structure (Claude Code format as of 2025-02)
EXPECTED_KEYS = {"type", "message", "timestamp"}
EXPECTED_MESSAGE_TYPES = {"human", "assistant", "tool_use", "tool_result"}

def validate_jsonl_format(jsonl_path: str) -> bool:
    """Check if JSONL matches expected format before extraction.
    
    Returns True if format matches, False if unrecognized.
    On False: log warning, skip heuristic extraction, 
    fall back to CC distillation only.
    """
    try:
        with open(jsonl_path) as f:
            first_line = f.readline()
            if not first_line:
                return False
            entry = json.loads(first_line)
            
            # Check for expected top-level keys
            if not EXPECTED_KEYS.issubset(entry.keys()):
                _log_format_mismatch(jsonl_path, entry.keys())
                return False
            
            return True
    except (json.JSONDecodeError, IOError):
        return False


def extract_from_jsonl(jsonl_path: str, session_id: str) -> list[dict]:
    """Heuristic extraction from JSONL transcript.
    
    Returns list of bead candidate dicts.
    If format is unrecognized, returns empty list (falls back to CC distillation).
    """
    if not validate_jsonl_format(jsonl_path):
        return []  # Graceful fallback
    
    candidates = []
    # ... regex extraction logic ...
    return candidates
```

### FTS5 Minimum Score

```python
# ── memory/beads.py ──

def search(query: str, project: str = None, 
           scope: str = "project", limit: int = 10,
           min_score: float = None) -> list[dict]:
    """FTS5 search with ranking and minimum score filtering.
    
    Score = fts5_relevance × project_boost × weight × source_boost
    
    min_score is applied BEFORE project boosts, preventing weak matches
    from surfacing just because they have a project boost.
    """
    config = get_config()
    min_score = min_score or config["memory"]["fts5_min_score"]  # default 0.3
    
    # Raw FTS5 search (no boosts yet)
    with wisdom_db() as conn:
        raw_results = conn.execute("""
            SELECT b.*, rank AS fts_score
            FROM beads_fts
            JOIN beads b ON beads_fts.rowid = b.rowid
            WHERE beads_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit * 3)).fetchall()  # Over-fetch, then filter
    
    # Apply minimum score threshold (on raw FTS5 score)
    filtered = [r for r in raw_results if abs(r["fts_score"]) >= min_score]
    
    # Apply boosts
    scored = []
    for r in filtered:
        boost = 1.0
        if r["project"] == project:
            boost *= 1.5
        elif r["project"] is None:
            boost *= 1.2
        boost *= r["weight"]
        
        scored.append({**dict(r), "final_score": abs(r["fts_score"]) * boost})
    
    # Sort by final score, return top N
    scored.sort(key=lambda x: x["final_score"], reverse=True)
    return scored[:limit]
```

---

## 8. Phase 3: EM (Orchestration)

### Build Order Within Phase 3

```
1. orch/schemas.py            — Already created in Phase 0
2. orch/mail.py               — Message CRUD, threads, routing
3. orch/task_graph.py          — DAG, waves, cyclic recovery (from Odin)
4. orch/agents.py              — 13 agent definitions, prompt assembly from prompts/ files, output templates
5. orch/tiers.py               — Tier system + auto-detection + enki_quick
6. orch/pm.py                  — PM workflow + customer presentation + entry point validation
7. orch/validation.py          — Blind validation + failure-mode checklist
8. orch/bugs.py                — Bug lifecycle
9. orch/parsing.py             — Agent output JSON parsing
10. orch/bridge.py             — Memory bridge (beads from em.db)
11. orch/status.py             — Status updates
12. orch/yggdrasil.py          — Project tracking (stub for Phase 2)
13. orch/claude_md.py           — CLAUDE.md generation from Codebase Profile or project type registry
14. orch/devops.py              — DevOps agent: CI execution, deploy per user config, verify, rollback
15. orch/onboarding.py          — Entry point detection, user profile, first-time user flow
16. orch/researcher.py          — Codebase Profile protocol, scoped investigation, time-bounded
17. orch/orchestrator.py       — Core EM (wires everything, conditional spawning, sprint-level review)
18. mcp/orch_tools.py          — MCP tool definitions
```

### enki_quick Fast-Path

```python
# ── orch/tiers.py ──

def quick(description: str, project: str) -> dict:
    """Fast-path for Minimal tier. Combines goal + triage + phase in one command.
    
    Sets goal, auto-triages as Minimal, jumps to implement phase.
    Uru Gate 1 (goal) and Gate 3 (phase) are satisfied immediately.
    Gate 2 (spec) doesn't apply to Minimal tier.
    
    Returns: {"goal": ..., "tier": "minimal", "phase": "implement"}
    """
    # Verify this is actually Minimal-tier work
    signals = _analyze_scope(description)
    detected_tier = detect_tier(signals)
    
    if detected_tier != "minimal":
        return {
            "error": f"Auto-detected tier is '{detected_tier}', not minimal. "
                     "Use full workflow: enki_goal → enki_triage → enki_phase.",
            "detected_tier": detected_tier
        }
    
    # Set goal
    _set_goal(project, description, tier="minimal")
    
    # Jump to implement
    _set_phase(project, "implement")
    
    return {
        "goal": description,
        "tier": "minimal",
        "phase": "implement",
        "message": "Quick mode active. Edit files, then enki_phase('ship') when done."
    }
```

### Human Approval CLI

**This is the answer to Gemini's Q3: "What prevents CC from hallucinating approval?"**

```python
# ── cli.py ──

def approve(project: str, spec_type: str = "implementation"):
    """Human approval command. Writes directly to em.db.
    
    Run from terminal, NOT from CC:
        enki approve --project myproject --spec implementation
    
    This sets human_approved=1 in em.db. CC cannot call this.
    The CLI writes directly to the DB using the human's process,
    not CC's process. Combined with Layer 0.5 (CC can't sqlite3),
    this means CC cannot forge approval.
    """
    from enki.db import em_db
    
    with em_db(project) as conn:
        conn.execute("""
            UPDATE task_state 
            SET human_approved = 1, 
                approved_at = datetime('now'),
                approved_by = 'human_cli'
            WHERE project = ? AND spec_type = ?
        """, (project, spec_type))
    
    print(f"✓ Approved {spec_type} spec for {project}")
```

---

## 9. Phase 4: Integration

### Hook Wiring

Phase 4 connects hooks to both Uru and Abzu. Before Phase 4, hooks only call Uru.

**session-start.sh** additions:
1. Call `abzu.inject_session_start()` to load persona + beads + last summary
2. Combine with Uru enforcement context
3. Output as system prompt injection

**pre-compact.sh** additions:
1. Call `abzu.update_pre_compact_summary()` with heuristic + CC state
2. Uru logs enforcement state (already wired)

**post-compact.sh** additions:
1. Call `abzu.inject_post_compact()` with injection budget
2. Re-inject Uru enforcement context (already wired)

**session-end.sh** additions:
1. Call `abzu.finalize_session()` — reconcile, extract, decay
2. Uru writes enforcement summary + proposals (already wired)

### End-to-End Test Scenarios

| Scenario | Expected |
|---|---|
| CC tries to edit code with no goal | Gate 1 blocks |
| CC tries to edit code in plan phase | Gate 3 blocks |
| CC writes to `~/.enki/abzu.db` via enki_remember | Exempt — allowed |
| CC writes a .md doc outside src/ | Exempt — allowed |
| CC runs `sqlite3 ~/.enki/em.db "UPDATE..."` | Layer 0.5 blocks |
| CC edits pre-tool-use.sh | Layer 0 blocks |
| CC writes to notes.md mentioning "enforcement.py" in content | Allowed (target extraction) |
| CC uses `enki_quick "fix typo"` then edits a file | Allowed (goal + implement set) |
| CC ignores nudge 3 times | Tone escalates but never blocks |
| Pre-compact fires, summaries exceed budget | Condensed injection, no overflow |
| Session crashes mid-work | Next start falls back to last pre-compact |

---

## 10. Migration from Current Enki

### Current State

Current Enki v1/v2 is disabled. 28 modules, ~17.2K lines. Most infrastructure dead.

### What Carries Over

| From | To | What |
|---|---|---|
| beads.py (current) | memory/beads.py | FTS5 logic, content hashing |
| task_graph.py (Odin) | orch/task_graph.py | DAG, waves, cyclic recovery |
| validation.py (Odin) | orch/validation.py | Blind validation patterns |
| hooks/*.sh (current) | hooks/*.sh | Shell structure (rewritten) |

### Bead Migration

378 existing beads need migration:
1. Map 8 types → 5 categories (decision/learning/pattern/fix/preference)
2. Strip `kind` field
3. Move `last_accessed` from access_log to bead row
4. All beads go to **staging** in abzu.db, NOT directly to wisdom.db
5. First Gemini review promotes the worthy ones
6. Current wisdom.db backed up, new one starts clean

### Cutover

1. Back up `~/.enki/` entirely
2. Run bootstrap.sh (creates new DBs)
3. Run migration script (beads → staging)
4. Deploy hooks (Phase 1 complete)
5. Verify gates work (test scenarios above)
6. Begin Phase 2-4 development UNDER enforcement

---

## 11. Test Strategy

**Scope**: This section covers tests FOR ENKI ITSELF — verifying that gates, memory, and orchestration work correctly. Tests that Enki writes for the products it builds (QualityPilot, SongKeeper, Cortex, etc.) are covered in the Ship & Quality Spec.

### Per-Phase Testing

| Phase | Tests | What |
|---|---|---|
| 0 | Schema validation | All tables create, WAL mode on, busy_timeout set |
| 1 | Gate unit tests | Each gate blocks/allows correctly, exempt paths work, target extraction accurate |
| 1 | Integration test | Full hook pipeline with mock tool calls |
| 2 | Memory unit tests | Bead CRUD, FTS5 search, dedup, ranking, injection budget |
| 2 | Session lifecycle | Pre-compact → accumulate → post-compact → session-end |
| 3 | Mail unit tests | Message CRUD, thread creation, routing |
| 3 | DAG tests | Wave computation, cyclic recovery, parallel limits |
| 4 | End-to-end | Full session lifecycle with enforcement + memory + orchestration |

### Gate Testing Priority (Phase 1)

This is the most critical test suite. If gates are wrong, everything is wrong.

```python
# tests/test_gates.py

def test_gate1_no_goal_blocks_code():
    """No active goal → block code edits."""

def test_gate1_no_goal_allows_docs():
    """No active goal → allow .md files (exempt)."""

def test_gate1_no_goal_allows_enki_infra():
    """No active goal → allow writes under ~/.enki/ (exempt)."""

def test_gate3_wrong_phase_blocks():
    """Phase is 'plan' → block code edits."""

def test_gate3_implement_phase_allows():
    """Phase is 'implement' → allow code edits."""

def test_layer0_blocks_hook_edit():
    """Editing hooks/pre-tool-use.sh → Layer 0 block."""

def test_layer05_blocks_sqlite3():
    """Bash with sqlite3 em.db → Layer 0.5 block."""

def test_layer05_allows_normal_bash():
    """Bash with ls, cat, grep → allow."""

def test_exempt_enki_dir():
    """Write to ~/.enki/abzu.db → exempt, allow."""

def test_exempt_md_outside_src():
    """Write to docs/README.md → exempt, allow."""

def test_not_exempt_md_in_src():
    """Write to src/enki/README.md → NOT exempt, gate checks apply."""

def test_target_extraction_redirect():
    """'echo "enforcement.py" > notes.md' → target is notes.md."""

def test_target_extraction_sed():
    """'sed -i s/x/y/ enforcement.py' → target is enforcement.py."""

def test_bash_content_not_target():
    """'echo "Fixed bug in uru.py" > log.txt' → allowed (target is log.txt)."""

def test_multiple_targets_one_blocked():
    """'cp good.py enforcement.py' → blocked (target is enforcement.py)."""

def test_enki_quick_sets_goal_and_phase():
    """enki_quick creates goal + implement phase for Minimal tier."""

def test_enki_quick_rejects_non_minimal():
    """enki_quick with Standard-tier work returns error."""
```

---

## 12. Module Interface Contracts

### Abzu ↔ Hooks

```python
# Hook calls these. Abzu implements them.
abzu.inject_session_start(project, goal, tier) -> str
abzu.update_pre_compact_summary(session_id, project, operational, conversational) -> None
abzu.inject_post_compact(session_id, tier) -> str
abzu.finalize_session(session_id, project) -> None
```

### Abzu ↔ MCP

```python
# MCP tools call these. Abzu implements them.
abzu.remember(content, category, project, summary, tags) -> dict
abzu.recall(query, scope, project, limit) -> list[dict]
abzu.star(bead_id) -> None
abzu.status() -> dict
```

### Uru ↔ Hooks

```python
# Hook calls these. Uru implements them.
uru.check_pre_tool_use(tool_name, tool_input) -> dict  # {"decision": "allow|block"}
uru.check_post_tool_use(tool_name, tool_input, response) -> dict  # with nudges
uru.init_session(session_id) -> None
uru.end_session(session_id) -> None
uru.log_enforcement_state() -> None
uru.inject_enforcement_context() -> str
```

### Uru → em.db (read-only)

```python
# Uru reads these from em.db. EM writes them.
_get_active_goal(project) -> str | None
_get_current_phase(project) -> str | None
_get_tier(project) -> str | None
_is_spec_approved(project) -> bool
```

### EM ↔ Abzu

```python
# EM calls these for context injection
abzu.recall(query, scope, project) -> list[dict]
abzu.remember(content, category, project) -> dict
```

### EM ↔ MCP

```python
# MCP tools call these. EM implements them.
em.set_goal(project, description, tier) -> dict
em.set_phase(project, phase) -> dict
em.triage(description) -> dict
em.quick(description, project) -> dict
em.decompose(spec, project) -> dict
em.orchestrate(project) -> dict
em.spawn_task(task_id, project) -> dict
```

---

## Summary

| Phase | Lines | Files | Depends On |
|---|---|---|---|
| 0: Bootstrap | ~530 | 6 | Nothing |
| 1: Uru | ~910 | 10 | Phase 0 |
| 2: Abzu | ~2,300 | 9 | Phase 0, Phase 1 (hooks) |
| 3: EM | ~6,750 | 18 | Phase 0, Phase 1, Phase 2 |
| 4: Integration | ~350 | Scripts + hook updates + tests | All |
| **Total** | **~10,840** | **~43** | — |

Plus: 15 prompt files in prompts/ (written by Gemini, Layer 0 protected, not counted above). 2 maintenance scripts (gemini_review.py, migrate_v1.py).

Down from ~17.2K lines / 28 modules in current Enki. 37% smaller. Every line earns its place.

---

*End of Enki v3 Implementation Spec v1.2*
