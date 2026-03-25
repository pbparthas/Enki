#!/bin/bash
# HOOK_VERSION=v4.1.0
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
fi
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Write session ID for MCP tools
SESSION_FILE="$HOME/.enki/current_session_id"
if [[ -n "$SESSION_ID" ]]; then
    echo "$SESSION_ID" > "$SESSION_FILE" 2>/dev/null || true
fi

PROJECT=$(echo "$INPUT" | jq -r '.project // empty')

if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(/home/partha/.enki-venv/bin/python -c "import uuid; print(uuid.uuid4())")
fi

# Migrate legacy marker
/home/partha/.enki-venv/bin/python -c "
from enki.project_state import deprecate_global_project_marker
deprecate_global_project_marker()
" 2>/dev/null || true

# CWD resolution
if [[ -z "$PROJECT" ]]; then
    CWD=$(pwd)
    PROJECT=$(ENKI_CWD="$CWD" /home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
from enki.project_state import resolve_project_from_cwd
result = resolve_project_from_cwd(os.environ.get('ENKI_CWD', ''))
print(result or '')
" 2>/dev/null || echo "")
fi

if [[ -z "$PROJECT" || "$PROJECT" == "." ]]; then
    echo '{"decision": "allow", "context": "No active Enki project for this directory. Call enki_goal to initialise."}'
    exit 0
fi

# Initialize Uru
echo "$(date -Iseconds) [enki-session-start] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook session-start 2>>"$LOG" 1>&2 || true

# Read project state
STATE_JSON=$(ENKI_PROJECT="$PROJECT" /home/partha/.enki-venv/bin/python -c "
import json, os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
from enki.project_state import normalize_project_name, read_all_project_state, project_db_path
project = normalize_project_name(os.environ.get('ENKI_PROJECT'))
if project_db_path(project).exists():
    state = read_all_project_state(project)
else:
    state = {'phase': None, 'tier': None, 'goal': None}
print(json.dumps({
    'project': project,
    'phase': state.get('phase') or 'none',
    'tier': state.get('tier') or 'minimal',
    'goal': state.get('goal') or 'none',
}))
" 2>>"$LOG" || echo '{"project":"","phase":"none","tier":"minimal","goal":"none"}')

PROJECT=$(echo "$STATE_JSON" | jq -r '.project // ""')
PHASE=$(echo "$STATE_JSON" | jq -r '.phase // "none"')
TIER=$(echo "$STATE_JSON" | jq -r '.tier // "minimal"')
GOAL=$(echo "$STATE_JSON" | jq -r '.goal // "none"')

# Build context — phase-aware, compact
CONTEXT=$(ENKI_PROJECT="$PROJECT" ENKI_GOAL="$GOAL" ENKI_TIER="$TIER" ENKI_PHASE="$PHASE" \
/home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
from enki.session_context import build_session_start_context
project = os.environ.get('ENKI_PROJECT') or 'default'
goal = os.environ.get('ENKI_GOAL') or 'none'
tier = os.environ.get('ENKI_TIER') or 'minimal'
phase = os.environ.get('ENKI_PHASE') or 'none'
print(build_session_start_context(project, goal, tier, phase))
" 2>>"$LOG" || echo "Context unavailable.")

# Add SKILL.md essentials (pipeline section stripped)
SKILL_CONTENT=$(ENKI_PROJECT="$PROJECT" /home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
from enki.session_context import get_skill_essentials
print(get_skill_essentials())
" 2>/dev/null || true)

if [ -n "$SKILL_CONTENT" ]; then
    CONTEXT="${CONTEXT}

---ENKI-SKILL---
${SKILL_CONTENT}"
fi

# Output as JSON with context field — this is the ONLY way CC injects context
# The context field value gets injected into CC's system prompt
CONTEXT_JSON=$(echo "$CONTEXT" | /home/partha/.enki-venv/bin/python -c "
import json, sys
content = sys.stdin.read()
print(json.dumps(content))
" 2>/dev/null || echo '""')

echo "{\"decision\": \"allow\", \"context\": ${CONTEXT_JSON}}"
