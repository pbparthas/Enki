#!/bin/bash
set -euo pipefail
# Enki Session Start Hook
# Called when Claude Code session starts
#
# Injects persona identity and session state.
# Does NOT depend on `enki` CLI being on PATH — reads .enki/ files directly.
# Python calls are optional, with 2-second timeouts.

# Read input from stdin
INPUT=$(cat)

# Extract fields (P1-06: all vars quoted)
PROMPT=$(echo "${INPUT}" | jq -r '.prompt // ""')
CWD=$(echo "${INPUT}" | jq -r '.cwd // "."')
SESSION_ID=$(echo "${INPUT}" | jq -r '.session_id // ""')

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
# FIND PYTHON (used by optional sections below)
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

# =============================================================================
# SESSION STATE (from .enki/ files — no CLI needed)
# =============================================================================

if [[ -d "${ENKI_DIR}" ]]; then
    PHASE=$(cat "${ENKI_DIR}/PHASE" 2>/dev/null || echo 'intake')
    GOAL=$(cat "${ENKI_DIR}/GOAL" 2>/dev/null || echo '')
    TIER=$(cat "${ENKI_DIR}/TIER" 2>/dev/null || echo 'unknown')

    echo "**Phase:** ${PHASE} | **Tier:** ${TIER}"

    if [[ -n "${GOAL}" ]]; then
        echo "**Goal:** ${GOAL}"
        echo ""

        # Relevant beads via Python (optional, non-blocking)
        # Pass CWD and GOAL via env vars — never interpolate into Python code
        if [[ -n "${PYTHON}" ]]; then
            RELEVANT=$( ENKI_CWD="${CWD}" ENKI_GOAL="${GOAL}" timeout 2 "${PYTHON}" -c "
import os, sys
cwd = os.environ.get('ENKI_CWD', '.')
goal = os.environ.get('ENKI_GOAL', '')
sys.path.insert(0, os.path.join(cwd, 'src'))
try:
    from enki.db import init_db
    from enki.search import search
    init_db()
    results = search(goal, limit=3, log_accesses=False)
    for r in results[:3]:
        print(f'- [{r.bead.type}] {(r.bead.summary or r.bead.content[:100])}')
except Exception:
    pass
" 2>/dev/null ) || true

            if [[ -n "${RELEVANT}" ]]; then
                echo "### Relevant Knowledge"
                echo "${RELEVANT}"
                echo ""
            fi
        fi

        PROJECT_NAME=$(basename "${CWD}")
        echo "*Back to ${PROJECT_NAME}. What's next?*"
    else
        echo ""
        echo "*What shall we build?*"
    fi
else
    echo "*No project state found. What shall we build?*"
fi

# =============================================================================
# LAST SESSION CONTEXT (from .enki/sessions/ — automatic continuity)
# =============================================================================

if [[ -d "${ENKI_DIR}/sessions" ]]; then
    # Find the most recent archive with actual content (skip empty archives)
    LAST_ARCHIVE=""
    for candidate in $(ls -t "${ENKI_DIR}/sessions/"*.md 2>/dev/null | head -5); do
        ENTRY_COUNT=$(grep -c "^\[" "${candidate}" 2>/dev/null || true)
        ENTRY_COUNT=${ENTRY_COUNT:-0}
        if [[ "${ENTRY_COUNT}" -gt 0 ]]; then
            LAST_ARCHIVE="${candidate}"
            break
        fi
    done

    if [[ -n "${LAST_ARCHIVE}" && -f "${LAST_ARCHIVE}" ]]; then
        # Extract header lines (lines starting with #)
        LAST_GOAL=$(grep "^# Goal:" "${LAST_ARCHIVE}" 2>/dev/null | head -1 | sed 's/^# Goal: //')
        LAST_PHASE=$(grep "^# Phase:" "${LAST_ARCHIVE}" 2>/dev/null | head -1 | sed 's/^# Phase: //')
        LAST_FILES=$(grep "^# Files:" "${LAST_ARCHIVE}" 2>/dev/null | head -1 | sed 's/^# Files: //')
        LAST_ARCHIVED=$(grep "^# Archived:" "${LAST_ARCHIVE}" 2>/dev/null | head -1 | sed 's/^# Archived: //' | cut -c1-16)

        # Get last 10 non-empty, non-header entries
        LAST_ENTRIES=$(grep -v "^#" "${LAST_ARCHIVE}" 2>/dev/null | grep -v "^$" | tail -10)

        if [[ -n "${LAST_GOAL}" ]]; then
            echo ""
            echo "### Last Session"
            echo "- **Goal:** ${LAST_GOAL}"
            [[ -n "${LAST_PHASE}" ]] && echo "- **State:** ${LAST_PHASE}"
            [[ -n "${LAST_FILES}" ]] && echo "- **Scope:** ${LAST_FILES}"
            [[ -n "${LAST_ARCHIVED}" ]] && echo "- **When:** ${LAST_ARCHIVED}"

            if [[ -n "${LAST_ENTRIES}" ]]; then
                echo ""
                echo "**Recent activity:**"
                echo '```'
                echo "${LAST_ENTRIES}"
                echo '```'
            fi
            echo ""
        fi
    fi
fi

# =============================================================================
# ERESHKIGAL REVIEW CHECK (via Python, optional)
# =============================================================================

if [[ -n "${PYTHON}" ]]; then
    REVIEW_ALERT=$( ENKI_CWD="${CWD}" timeout 2 "${PYTHON}" -c "
import os, sys
cwd = os.environ.get('ENKI_CWD', '.')
sys.path.insert(0, os.path.join(cwd, 'src'))
try:
    from enki.db import init_db
    from enki.ereshkigal import is_review_overdue, get_review_reminder
    init_db()
    if is_review_overdue():
        print(get_review_reminder())
except Exception:
    pass
" 2>/dev/null ) || true

    if [[ -n "${REVIEW_ALERT}" ]]; then
        echo ""
        echo "---"
        echo "${REVIEW_ALERT}"
    fi
fi

# =============================================================================
# FEEDBACK PROPOSALS CHECK (via Python, optional)
# =============================================================================

if [[ -n "${PYTHON}" ]]; then
    FEEDBACK_ALERT=$( ENKI_CWD="${CWD}" timeout 2 "${PYTHON}" -c "
import os, sys, json
cwd = os.environ.get('ENKI_CWD', '.')
sys.path.insert(0, os.path.join(cwd, 'src'))
try:
    from enki.db import init_db, get_db
    init_db()
    db = get_db()
    pending = db.execute(
        'SELECT COUNT(*) FROM feedback_proposals WHERE status = ?', ('pending',)
    ).fetchone()[0]
    regressed = db.execute(
        'SELECT COUNT(*) FROM feedback_proposals WHERE status = ?', ('regressed',)
    ).fetchone()[0]
    if pending or regressed:
        parts = []
        if pending: parts.append(str(pending) + ' pending')
        if regressed: parts.append(str(regressed) + ' regressed')
        print('Feedback proposals: ' + ', '.join(parts) + '. Run enki_feedback_loop status to review.')
except Exception:
    pass
" 2>/dev/null ) || true

    if [[ -n "${FEEDBACK_ALERT}" ]]; then
        echo "${FEEDBACK_ALERT}"
    fi
fi

# =============================================================================
# ENFORCEMENT GATES (Always present — hardcoded, not conditional)
# =============================================================================

echo ""
echo "### Enforcement Gates (Active)"
echo "- Goal required before editing code"
echo "- Spec required before spawning agents"
echo ""

# =============================================================================
# SESSION TRACKING
# =============================================================================

if [[ -n "${SESSION_ID}" ]]; then
    mkdir -p "${ENKI_DIR}"
    echo "${SESSION_ID}" > "${ENKI_DIR}/SESSION_ID"
fi

exit 0
