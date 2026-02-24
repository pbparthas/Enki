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
