#!/bin/bash
# HOOK_VERSION=v4.0.1
LOG="$HOME/.enki/hook-errors.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
if ! (echo "" >> "$LOG") 2>/dev/null; then
    LOG="/tmp/enki-hook-errors.log"
    (echo "" >> "$LOG") 2>/dev/null || true
fi
# hooks/post-tool-use.sh — Nudges + logging (non-blocking)
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

echo "$(date -Iseconds) [enki-post-tool-use] tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -m enki.gates.uru --hook post-tool-use 2>>"$LOG" || true)

# ── Sentrux drift scoring ───────────────────────────────────────────────
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
PROJECT=$(echo "$INPUT" | jq -r '.project // empty')
if [[ -n "$SESSION_ID" ]]; then
    DRIFT_RESULT=$(echo "$INPUT" | /home/partha/.enki-venv/bin/python -c "
import json, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
try:
    data = json.load(sys.stdin)
    from enki.gates.sentrux import score_tool_call
    result = score_tool_call(
        session_id=data.get('session_id', ''),
        tool_name=data.get('tool_name', ''),
        tool_input=data.get('tool_input', {}),
        tool_output=data.get('tool_response', {}),
        project=data.get('project'),
    )
    print(json.dumps(result))
except Exception:
    print(json.dumps({'action': 'none', 'message': None}))
" 2>>"$LOG" || echo '{"action":"none","message":null}')

    DRIFT_ACTION=$(echo "$DRIFT_RESULT" | jq -r '.action // "none"' 2>>"$LOG" || echo "none")
    DRIFT_MESSAGE=$(echo "$DRIFT_RESULT" | jq -r '.message // ""' 2>>"$LOG" || echo "")
    if [[ "$DRIFT_ACTION" == "escalate" && -n "$DRIFT_MESSAGE" ]]; then
        ENKI_DRIFT_MESSAGE="$DRIFT_MESSAGE" /home/partha/.enki-venv/bin/python -c "
import os, sys
sys.path.insert(0, '/home/partha/Desktop/Enki/src')
from enki.gates.sentrux import send_telegram_escalation
send_telegram_escalation(os.environ.get('ENKI_DRIFT_MESSAGE', ''))
" 2>>"$LOG" &
        echo "$(date -Iseconds) [sentrux] ESCALATION session=$SESSION_ID project=$PROJECT result=$DRIFT_RESULT" >> "$LOG" 2>/dev/null || true
    fi
fi

if [[ -n "$RESULT" ]]; then
    NUDGE_COUNT=$(echo "$RESULT" | jq -r '.nudges | length // 0' 2>>"$LOG" || true)
    if [[ "$NUDGE_COUNT" != "0" ]]; then
        NUDGE_TEXT=$(echo "$RESULT" | jq -r '.nudges | join("\n")' 2>>"$LOG" || true)
        jq -n --arg ctx "$NUDGE_TEXT" '{
          hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: $ctx
          }
        }'
    fi
else
    echo "$(date -Iseconds) [enki-post-tool-use] EMPTY RESULT tool=$TOOL_NAME" >> "$LOG" 2>/dev/null || true
fi
