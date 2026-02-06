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
# DIGEST INJECTION (primary path)
# =============================================================================

DIGEST_FILE="${ENKI_DIR}/.compact-digest"

if [[ -f "${DIGEST_FILE}" ]] && [[ -s "${DIGEST_FILE}" ]]; then
    echo "## Context Restored (Post-Compaction)"
    echo ""

    # Output the digest as-is — it was built mechanically by transcript.py
    cat "${DIGEST_FILE}"
    echo ""

else
    # =============================================================================
    # FALLBACK: .enki/ file state only (no digest available)
    # =============================================================================

    echo "## Context Restored (Post-Compaction) — Limited"
    echo ""
    echo "*No transcript digest available. Showing .enki/ state only.*"
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

    # Recent activity
    if [[ -f "${ENKI_DIR}/RUNNING.md" ]]; then
        echo "### Recent Activity"
        echo '```'
        tail -10 "${ENKI_DIR}/RUNNING.md" 2>/dev/null | grep -v "^$" | head -8
        echo '```'
        echo ""
    fi
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
