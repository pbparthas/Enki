#!/bin/bash
# HOOK_VERSION=v4.1.0
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
fi

# Enki SessionStart Hook
# Minimal — only handles session ID and Uru initialization.
# Context injection moved to UserPromptSubmit (SessionStart context injection
# is broken for new sessions in CC 2.1.84 — see github.com/anthropics/claude-code/issues/10373)

set -euo pipefail
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Write session ID for MCP tools to read
SESSION_FILE="$HOME/.enki/current_session_id"
if [[ -n "$SESSION_ID" ]]; then
    echo "$SESSION_ID" > "$SESSION_FILE" 2>/dev/null || true
fi

# Generate session ID if not provided
if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID=$(/home/partha/.enki-venv/bin/python -c "import uuid; print(uuid.uuid4())")
fi

# Migrate legacy PROJECT marker
/home/partha/.enki-venv/bin/python -c "
import sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    from enki.project_state import deprecate_global_project_marker
    deprecate_global_project_marker()
except Exception:
    pass
" 2>/dev/null || true

# Initialize Uru enforcement state
echo "$(date -Iseconds) [enki-session-start] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru \
    --hook session-start 2>>"$LOG" 1>&2 || true

# Output allow decision — no context here, UserPromptSubmit handles injection
echo '{"decision": "allow"}'
