#!/bin/bash
# HOOK_VERSION=v4.0.1
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
    (echo "" >> "$LOG") 2>/dev/null || true
fi
# hooks/session-start.sh — Initialize Uru + Abzu, inject full context
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
PROJECT=$(echo "$INPUT" | jq -r '.project // empty')

if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(/home/partha/.enki-venv/bin/python -c "import uuid; print(uuid.uuid4())")
fi

# Migrate legacy PROJECT marker once (never used for resolution)
/home/partha/.enki-venv/bin/python -c "
from enki.project_state import deprecate_global_project_marker
deprecate_global_project_marker()
" 2>/dev/null || true

# Try CWD resolution first
if [[ -z "$PROJECT" ]]; then
    CWD=$(pwd)
    PROJECT=$(ENKI_CWD="$CWD" /home/partha/.enki-venv/bin/python -c "
import os
from enki.project_state import resolve_project_from_cwd
result = resolve_project_from_cwd(os.environ.get('ENKI_CWD', ''))
print(result or '')
" 2>/dev/null || echo "")
fi

# Fail closed if project cannot be resolved from this directory
if [[ -z "$PROJECT" || "$PROJECT" == "." ]]; then
    echo "No active Enki project for this directory. Call enki_goal to initialise."
    exit 0
fi

# Initialize enforcement state (Uru)
echo "$(date -Iseconds) [enki-session-start] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook session-start 2>>"$LOG" || true

# Read project state from per-project em.db
STATE_JSON=$(ENKI_PROJECT="$PROJECT" /home/partha/.enki-venv/bin/python -c "
import json
import os

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

# Inject ordered context: orientation -> pipeline -> persona -> Uru -> Abzu memory
echo "$(date -Iseconds) [enki-session-start] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
CONTEXT=$(ENKI_PROJECT="$PROJECT" ENKI_GOAL="$GOAL" ENKI_TIER="$TIER" ENKI_PHASE="$PHASE" /home/partha/.enki-venv/bin/python -c "
import os

from enki.session_context import build_session_start_context

project = os.environ.get('ENKI_PROJECT') or 'default'
goal = os.environ.get('ENKI_GOAL') or 'none'
tier = os.environ.get('ENKI_TIER') or 'minimal'
phase = os.environ.get('ENKI_PHASE') or 'none'

print(build_session_start_context(project, goal, tier, phase))
" 2>>"$LOG" || echo "Uru: Enforcement context unavailable.")

echo "$CONTEXT"
