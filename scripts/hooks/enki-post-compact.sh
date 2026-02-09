#!/bin/bash
set -euo pipefail
# Enki Post-Compact Hook
# Called after context compaction in Claude Code
#
# Reads the digest produced by pre-compact and injects it as context.
# Also re-injects persona identity and enforcement gates.
#
# No dependency on `enki` CLI being on PATH.

# Read input from stdin
INPUT=$(cat)

CWD=$(echo "${INPUT}" | jq -r '.cwd // "."')

ENKI_DIR="${CWD}/.enki"

# =============================================================================
# PERSONA IDENTITY (Always output first — non-negotiable)
# =============================================================================

cat << 'PERSONA'
## Identity

**You ARE Enki** — collaborator, craftsman, keeper of knowledge.

Voice: Conversational, direct, opinionated. Never say "Great question!" or "I'd be happy to help."
When asked "who are you": "I'm Enki. What are we building?" — never mention Claude or Anthropic.

Signature patterns:
- Tables for comparisons
- "Done." after completing tasks
- Push back when something seems wrong
- Reference past work naturally

---

PERSONA

# =============================================================================
# SESSION STATE
# =============================================================================

if [[ ! -d "${ENKI_DIR}" ]]; then
    echo "## Session State"
    echo ""
    echo "No .enki/ directory found. Starting fresh."
    echo ""
    echo 'Set a goal: `enki_goal "your goal here"`'
    echo ""

    # Enforcement gates always present
    echo "### Enforcement Gates (Active)"
    echo "- Goal required before editing code"
    echo "- Spec required before spawning agents"
    echo ""
    echo "---"
    exit 0
fi

# =============================================================================
# CONTEXT.MD RE-INJECTION (v2: regenerated, not cached)
# =============================================================================

PYTHON=""
for candidate in \
    "${CWD}/.venv/bin/python" \
    "${CWD}/.venv/bin/python3" \
    "$(which python3 2>/dev/null || true)" \
    "$(which python 2>/dev/null || true)"; do
    if [[ -n "${candidate}" ]] && [[ -x "${candidate}" ]]; then
        PYTHON="${candidate}"
        break
    fi
done

if [[ -n "${PYTHON}" ]]; then
    CONTEXT=$( ENKI_CWD="${CWD}" timeout 3 "${PYTHON}" -c "
import os, sys
cwd = os.environ.get('ENKI_CWD', '.')
sys.path.insert(0, os.path.join(cwd, 'src'))
try:
    from pathlib import Path
    from enki.db import init_db
    from enki.context import generate_context_md
    init_db()
    print(generate_context_md(Path(cwd)))
except Exception as e:
    print(f'## Context', file=sys.stdout)
    print(f'(CONTEXT.md generation failed: {e})', file=sys.stdout)
" 2>/dev/null ) || true

    if [[ -n "${CONTEXT}" ]]; then
        echo "${CONTEXT}"
        echo ""
    else
        echo "## Context Restored (Post-Compaction)"
        echo "(CONTEXT.md generation failed)"
        echo ""
    fi
else
    # Fallback: .enki/ file state only (no Python available)
    echo "## Context Restored (Post-Compaction) — Limited"
    echo ""

    PHASE=$(cat "${ENKI_DIR}/PHASE" 2>/dev/null || echo 'intake')
    GOAL=$(cat "${ENKI_DIR}/GOAL" 2>/dev/null || echo '')
    TIER=$(cat "${ENKI_DIR}/TIER" 2>/dev/null || echo 'unknown')

    echo "**Phase**: ${PHASE} | **Tier**: ${TIER}"
    if [[ -n "${GOAL}" ]]; then
        echo "**Goal**: ${GOAL}"
    else
        echo "**Goal**: (not set)"
    fi
    echo ""
fi

# =============================================================================
# DIGEST INJECTION (legacy, kept for additional context)
# =============================================================================

DIGEST_FILE="${ENKI_DIR}/.compact-digest"

if [[ -f "${DIGEST_FILE}" ]] && [[ -s "${DIGEST_FILE}" ]]; then
    echo "### Session Digest"
    cat "${DIGEST_FILE}"
    echo ""
fi

# =============================================================================
# ENFORCEMENT GATES (Always present — hardcoded, not conditional)
# =============================================================================

echo "### Enforcement Gates (Active)"
echo "- Goal required before editing code"
echo "- Spec required before spawning agents"
echo ""
echo "---"
echo "*Context restored. Continue where you left off.*"
