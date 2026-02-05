#!/bin/bash
# Enki Session Start Hook
# Called when Claude Code session starts
#
# Injects persona identity and session state.
# Does NOT depend on `enki` CLI being on PATH — reads .enki/ files directly.
# Python calls are optional, with 2-second timeouts.

# Read input from stdin
INPUT=$(cat)

# Extract fields
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

ENKI_DIR="$CWD/.enki"

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
    "$CWD/.venv/bin/python" \
    "$CWD/.venv/bin/python3" \
    "$(which python3 2>/dev/null)" \
    "$(which python 2>/dev/null)"; do
    if [[ -n "$candidate" ]] && [[ -x "$candidate" ]]; then
        PYTHON="$candidate"
        break
    fi
done

# =============================================================================
# SESSION STATE (from .enki/ files — no CLI needed)
# =============================================================================

if [[ -d "$ENKI_DIR" ]]; then
    PHASE=$(cat "$ENKI_DIR/PHASE" 2>/dev/null || echo 'intake')
    GOAL=$(cat "$ENKI_DIR/GOAL" 2>/dev/null || echo '')
    TIER=$(cat "$ENKI_DIR/TIER" 2>/dev/null || echo 'unknown')

    echo "**Phase:** $PHASE | **Tier:** $TIER"

    if [[ -n "$GOAL" ]]; then
        echo "**Goal:** $GOAL"
        echo ""

        # Relevant beads via Python (optional, non-blocking)
        if [[ -n "$PYTHON" ]]; then
            RELEVANT=$( timeout 2 "$PYTHON" -c "
import sys
sys.path.insert(0, '$CWD/src')
try:
    from enki.db import init_db
    from enki.search import search
    init_db()
    results = search('$GOAL', limit=3, log_accesses=False)
    for r in results[:3]:
        print(f'- [{r.bead.type}] {(r.bead.summary or r.bead.content[:100])}')
except Exception:
    pass
" 2>/dev/null )

            if [[ -n "$RELEVANT" ]]; then
                echo "### Relevant Knowledge"
                echo "$RELEVANT"
                echo ""
            fi
        fi

        PROJECT_NAME=$(basename "$CWD")
        echo "*Back to $PROJECT_NAME. What's next?*"
    else
        echo ""
        echo "*What shall we build?*"
    fi
else
    echo "*No project state found. What shall we build?*"
fi

# =============================================================================
# ERESHKIGAL REVIEW CHECK (via Python, optional)
# =============================================================================

if [[ -n "$PYTHON" ]]; then
    REVIEW_ALERT=$( timeout 2 "$PYTHON" -c "
import sys
sys.path.insert(0, '$CWD/src')
try:
    from enki.db import init_db
    from enki.ereshkigal import is_review_overdue, get_review_reminder
    init_db()
    if is_review_overdue():
        print(get_review_reminder())
except Exception:
    pass
" 2>/dev/null )

    if [[ -n "$REVIEW_ALERT" ]]; then
        echo ""
        echo "---"
        echo "⚠️ $REVIEW_ALERT"
    fi
fi

# =============================================================================
# FEEDBACK PROPOSALS CHECK (via Python, optional)
# =============================================================================

if [[ -n "$PYTHON" ]]; then
    FEEDBACK_ALERT=$( timeout 2 "$PYTHON" -c "
import sys, json
sys.path.insert(0, '$CWD/src')
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
" 2>/dev/null )

    if [[ -n "$FEEDBACK_ALERT" ]]; then
        echo "📋 $FEEDBACK_ALERT"
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

if [[ -n "$SESSION_ID" ]]; then
    mkdir -p "$ENKI_DIR"
    echo "$SESSION_ID" > "$ENKI_DIR/SESSION_ID"
fi

exit 0
