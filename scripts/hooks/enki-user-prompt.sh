#!/bin/bash
# HOOK_VERSION=v4.1.0
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
fi

# Enki UserPromptSubmit Hook
# Injects Enki session context on first prompt of each session.
# Subsequent prompts: error-pattern knowledge search.

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Skip if no prompt
if [[ -z "$PROMPT" ]]; then
    exit 0
fi

# ── Session-once context injection ─────────────────────────────────────────
# SessionStart hook context injection is broken for new sessions in CC 2.1.84.
# UserPromptSubmit is the reliable injection point.
# We inject once per session using a marker file keyed to session_id.

CACHE_DIR="$HOME/.enki/cache"
mkdir -p "$CACHE_DIR" 2>/dev/null || true

if [[ -n "$SESSION_ID" ]]; then
    INJECTED_MARKER="$CACHE_DIR/injected-${SESSION_ID}"

    if [[ ! -f "$INJECTED_MARKER" ]]; then
        # First prompt of this session — inject full context
        touch "$INJECTED_MARKER" 2>/dev/null || true

        echo "$(date -Iseconds) [enki-user-prompt] first-prompt context injection session=$SESSION_ID" >> "$LOG" 2>/dev/null || true

        # Resolve project from CWD
        PROJECT=$(ENKI_CWD="$CWD" /home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.project_state import resolve_project_from_cwd
    result = resolve_project_from_cwd(os.environ.get('ENKI_CWD', ''))
    print(result or '')
except Exception:
    print('')
" 2>/dev/null || echo "")

        if [[ -n "$PROJECT" && "$PROJECT" != "." ]]; then
            # Read project state
            STATE_JSON=$(ENKI_PROJECT="$PROJECT" /home/partha/.enki-venv/bin/python -c "
import json, os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.project_state import normalize_project_name, read_all_project_state, project_db_path
    project = normalize_project_name(os.environ.get('ENKI_PROJECT'))
    if project_db_path(project).exists():
        state = read_all_project_state(project)
    else:
        state = {}
    print(json.dumps({
        'project': project,
        'phase': state.get('phase') or 'none',
        'tier': state.get('tier') or 'standard',
        'goal': state.get('goal') or 'none',
    }))
except Exception as e:
    print(json.dumps({'project': '', 'phase': 'none', 'tier': 'standard', 'goal': 'none'}))
" 2>>"$LOG" || echo '{"project":"","phase":"none","tier":"standard","goal":"none"}')

            PROJECT=$(echo "$STATE_JSON" | jq -r '.project // ""')
            PHASE=$(echo "$STATE_JSON" | jq -r '.phase // "none"')
            TIER=$(echo "$STATE_JSON" | jq -r '.tier // "standard"')
            GOAL=$(echo "$STATE_JSON" | jq -r '.goal // "none"')

            # Build session context
            CONTEXT=$(ENKI_PROJECT="$PROJECT" ENKI_GOAL="$GOAL" ENKI_TIER="$TIER" ENKI_PHASE="$PHASE" \
            /home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.session_context import build_session_start_context
    project = os.environ.get('ENKI_PROJECT') or 'default'
    goal = os.environ.get('ENKI_GOAL') or 'none'
    tier = os.environ.get('ENKI_TIER') or 'standard'
    phase = os.environ.get('ENKI_PHASE') or 'none'
    print(build_session_start_context(project, goal, tier, phase))
except Exception as e:
    print(f'Context unavailable: {e}')
" 2>>"$LOG" || echo "Enki context unavailable.")

            # Add SKILL essentials
            SKILL_CONTENT=$(/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.session_context import get_skill_essentials
    print(get_skill_essentials())
except Exception:
    pass
" 2>/dev/null || true)

            if [[ -n "$SKILL_CONTENT" ]]; then
                CONTEXT="${CONTEXT}

---ENKI-SKILL---
${SKILL_CONTENT}"
            fi

            # Output as additionalContext — this is the correct UserPromptSubmit format
            /home/partha/.enki-venv/bin/python3 -c "
import json, sys
content = sys.stdin.read()
output = {
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': content
    }
}
print(json.dumps(output))
" <<< "$CONTEXT" 2>/dev/null || exit 0

            exit 0
        fi
    fi
fi

# ── Error pattern knowledge search (subsequent prompts) ────────────────────
ERROR_PATTERNS="error|Error|ERROR|exception|Exception|traceback|Traceback|failed|Failed|TypeError|SyntaxError|NameError|AttributeError|ImportError|KeyError|ValueError|RuntimeError"

if echo "$PROMPT" | grep -qiE "$ERROR_PATTERNS"; then
    QUERY=$(echo "$PROMPT" | head -c 200)
    echo "$(date -Iseconds) [enki-user-prompt] error-pattern search tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true

    SEARCH_RESULT=$(/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.mcp.orch_tools import enki_recall
    result = enki_recall(query='''$QUERY''', limit=3)
    if result and isinstance(result, list):
        for r in result[:3]:
            print(r.get('content', ''))
except Exception:
    pass
" 2>/dev/null || true)

    if [[ -n "$SEARCH_RESULT" ]]; then
        /home/partha/.enki-venv/bin/python3 -c "
import json, sys
content = sys.stdin.read()
output = {
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': '## Enki: Relevant Solutions\n' + content
    }
}
print(json.dumps(output))
" <<< "$SEARCH_RESULT" 2>/dev/null || exit 0
        exit 0
    fi
fi

# No context to inject
exit 0
